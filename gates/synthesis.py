"""gates/synthesis.py: the citation gate on the delegated analyst report.

THE FACT LAYER IS THE PRODUCT AND THE WRITING IS DELEGATED UNDER A GATE. One frontier pass
over the result's non-underscore keys writes the analyst report as markdown, and the prompt
makes it put the JSON key path of every number it uses in square brackets the first time,
like [market_sizing.som.mid]. MEASURED 2026-09-12 on the diag01 run: Opus wrote 1,820 words
with 60 citations, all resolving, every number in the evidence, and surfaced four pipeline
defects nobody had found. The same prompt on Sonnet 4.5 invented a BEA 21% price-level
figure and a $12,000 / $4,500 / $2,000 / $23,000 cost sketch. A whole-blob number match
missed both, because 141KB of evidence contains almost every small number somewhere. This
module narrows the evidence to what the report cites: it resolves each [path], pools the
leaf numbers of the cited values, and holds every number in the prose to that pool. The
pool is document-wide, not sentence-wide: a figure over 100 passes if ANY citation in the
report vouches for it, so a number that happens to sit in some cited list passes. Two
rules keep that from being a loophole. A whole number of 100 or less must also carry a
citation in its own paragraph, and a number is matched only at the scale its own suffix
names.

A NUMBER IS MATCHED AT THE SCALE ITS OWN SUFFIX NAMES, and nothing else. The first cut of
this gate let every number try every scale in both directions, and the measurement was
damning: 69 of the 99 round-thousand rents from $1,000 to $99,000, appended to the Opus
report as an invented sentence, were accepted, because "$12,000" could borrow the count 12
through a x1000 rung and "$4,500" could borrow the $451,702 SOM low through a x100 rung.
The whole Sonnet cost sketch passed. Under the suffix rule ("$12,000" and "12,000" are
12,000; "16.5k" is 16,500; "52.6M" is 52.6 million; "35%" is 35 or 0.35) the same probe
accepts 13 of 99, every one of them within 0.6% of a value in a cited list, and the sketch
fails on three of its four figures. The fourth, 12,000, is a follower count inside a list
the report cites, and the 21% fails on the paragraph rule.

TWO DETECTORS, because the two failures are worth different things. D62 fails the report
when a number in the prose is in no value the prose cites: that is an invented figure and a
buyer must not read it. D63 only warns when a citation path does not resolve: the model may
cite a dropped section by its name, and the numbers beside it are still judged by D62
against everything else the report cites.

2 of the 63 deterministic detectors. Each takes (result, html) and returns a Finding;
neither shares state with the other or with any other module.
"""
from __future__ import annotations

import re
from bisect import bisect_right
from typing import Optional

from gates.common import Finding, not_applicable

# A citation is [path] or [path, path]. A path may carry [n] indexes of its own, so the body
# admits one level of nested digit brackets: the reference prototype's [^\[\]]+ matched the
# inner "[0]" of [competitor_pricing.per_domain[0].median], resolved the empty path "0" to
# the ROOT of the fact layer, and so pooled every number in the run. One citation vouched
# for everything.
_CITE_RE = re.compile(r"\[((?:[^\[\]]|\[\d+\])+?)\]")
_PART_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*|\[\d+\]")
# The destination of a markdown link, "](https://...)". An address, not a claim.
_LINK_TARGET_RE = re.compile(r"\]\([^)\n]*\)")

# A number in prose: an optional $, digits with thousands commas and a decimal part (or a
# bare ".5"), then the suffix that fixes its scale (a percent sign, k, M, B, the words for
# them, or x for a multiple), and no letter or digit on either side so that "7am", "4Ps",
# "5km" and a path segment such as ".year_3" or "[0]" are not numbers. The bracket in the
# lookbehind is the whole reason a citation path contributes no numeric token of its own:
# every digit in one is glued to a letter, a dot or an index bracket.
_NUM_RE = re.compile(
    r"(?<![\w.\[])(\$?)(-?(?:\d[\d,]*\.?\d*|\.\d+))"
    r"(%|\s?percent\b|[kK]\b|\s?thousand\b|M\b|\s?million\b|B\b|\s?billion\b|x\b)?"
    r"(?!\w)")
_LEAF_NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")
# A GFM header separator carries a pipe. A horizontal rule ("---") directly under a table
# row does not, and must not turn that row into a header.
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*\|")
_LIST_MARKER_RE = re.compile(r"^\s*\d+\.\s")
# "an 11-hour window": a count of hours or minutes bound by a hyphen is clock arithmetic on
# the founder's opening times, which the model cannot cite by path. Nothing else is: a
# "45-seat room", a "60-day test" or a "$9,400-a-month" rent are claims on the evidence.
_DESCRIPTOR_RE = re.compile(r"-(?:hour|minute)s?\b", re.IGNORECASE)

# Integers that carry no claim: counts of one to ten, a dozen, a fortnight, a month, a
# quarter, and the years a report can be about. Everything else has to be in the evidence.
_TRIVIAL_INTEGERS = frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 30, 90}
                              | set(range(2020, 2031)))
# The scale a suffix names. A bare or dollar figure is what it says; a percentage may be
# stored as a fraction; a magnitude word multiplies. Never the other way round.
_SUFFIX_SCALES: dict[str, tuple[float, ...]] = {
    "": (1,), "%": (1, 0.01), "percent": (1, 0.01), "k": (1e3,), "thousand": (1e3,),
    "m": (1e6,), "million": (1e6,), "b": (1e9,), "billion": (1e9,), "x": (1,),
}
_TOLERANCE = 0.006          # rounding to one decimal place, measured on the diag01 output
_YEAR_RANGE = (1900, 2100)  # a comma-less whole number in here is a year, and years are exact
_SMALL_INTEGER = 100        # at or below this a whole number also needs a citation nearby
_LEAF_DEPTH = 4
_LEAF_LIST_CAP = 50
_MAX_OFFENDERS = 5
_CONTEXT_CHARS = 60


#: The keys that hold writing rather than evidence: the report under test, and the
#: earlier drafts a workshop rewrite moves aside.
_WRITING_KEYS = ("synthesis", "synthesis_history")


def _facts(r: dict) -> dict:
    """The fact layer: every non-underscore key of the result, minus the writing.

    A citation to [synthesis.markdown] would resolve to the report itself and pool every
    number in it, so the report would vouch for its own inventions. A citation to an
    earlier draft under synthesis_history would do the same one rewrite later.
    """
    return {k: v for k, v in (r or {}).items()
            if isinstance(k, str) and not k.startswith("_") and k not in _WRITING_KEYS}


def _synthesis_markdown(r: dict) -> Optional[str]:
    syn = (r or {}).get("synthesis")
    md = syn.get("markdown") if isinstance(syn, dict) else None
    return md if isinstance(md, str) and md.strip() else None


def resolve_path(path: str, facts: dict):
    """'market_sizing.figures[0].source' -> its value; None when the path does not exist.

    A ': suffix' is dropped (the model writes [market_sizing.som: "low"] for a sub-key it
    is quoting). An empty path resolves to nothing, never to the root.
    """
    parts = _PART_RE.findall(str(path).split(":", 1)[0].strip())
    if not parts:
        return None
    node = facts
    for part in parts:
        if part.startswith("["):
            i = int(part[1:-1])
            if not isinstance(node, list) or i >= len(node):
                return None
            node = node[i]
        else:
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
    return node


def leaf_numbers(value, depth: int = 0, out: Optional[set] = None) -> set:
    """Every number a resolved value contains, to a bounded depth.

    Strings contribute the numbers written in them, so a citation to a formula sentence
    ("28,871 households within 1.5 km") vouches for the figures the sentence states.
    """
    if out is None:
        out = set()
    if depth > _LEAF_DEPTH or isinstance(value, bool):
        return out
    if isinstance(value, (int, float)):
        out.add(float(value))
    elif isinstance(value, str):
        for m in _LEAF_NUM_RE.findall(value):
            try:
                out.add(float(m.replace(",", "")))
            except ValueError:
                pass
    elif isinstance(value, dict):
        for v in value.values():
            leaf_numbers(v, depth + 1, out)
    elif isinstance(value, list):
        for v in value[:_LEAF_LIST_CAP]:
            leaf_numbers(v, depth + 1, out)
    return out


def _founder_numbers(r: dict) -> set:
    """Numbers the founder stated, which the model was handed and cannot cite by path.

    The intake record (result["intake"], stamped by plan.py) and the profile are the
    pipeline's own record of the brief. `synthesis.venture` is the brief exactly as the
    synthesis producer handed it to the model. A price the founder typed is not a figure
    the model invented, so these join the pool of every citation.
    """
    out: set = set()
    for key in ("intake", "profile"):
        leaf_numbers((r or {}).get(key), 0, out)
    syn = (r or {}).get("synthesis")
    if isinstance(syn, dict) and isinstance(syn.get("venture"), str):
        leaf_numbers(syn["venture"], 0, out)
    return out


class _Token:
    """One number as written: its value, its scale, and whether it can be trivial."""

    def __init__(self, m: "re.Match"):
        self.pos = m.start(2)
        self.end = m.end()
        self.raw = m.group(2).rstrip(".,")
        self.value = float(self.raw.replace(",", ""))
        self.is_money = bool(m.group(1))
        suffix = (m.group(3) or "").strip().lower()
        self.is_percent = suffix in ("%", "percent")
        self.scales = _SUFFIX_SCALES[suffix]
        self.whole = self.value.is_integer()
        # "2019" is a year; "2,019" is a count. Only the year is held to an exact match.
        self.is_year = (self.whole and not self.is_money and not suffix and "," not in self.raw
                        and _YEAR_RANGE[0] <= self.value <= _YEAR_RANGE[1])
        self.is_trivial = (self.whole and not self.is_money and not self.is_percent
                           and self.scales == (1,) and int(self.value) in _TRIVIAL_INTEGERS)


def _matches(n: float, pool: set, scales: tuple = (1,), exact: bool = False) -> bool:
    """Is n, at one of the scales its suffix allows, within tolerance of a pool number?

    The absolute floor applies at scale 1 only: 0.5 lets "128 drinks" stand for 127.9 and
    0.05 lets "4.4" stand for 4.43. `exact` is for years, where 0.6% would be twelve of
    them either side.
    """
    whole = float(n).is_integer()
    for f in pool:
        for scale in scales:
            v = n * scale
            if exact:
                if v == f:
                    return True
                continue
            floor = (0.5 if whole else 0.05) if scale == 1 else 0.0
            if abs(v - f) <= max(floor, abs(f) * _TOLERANCE):
                return True
    return False


class _Layout:
    """Where each character of the markdown sits: its line, its block, its table.

    A BLOCK is the scope a small integer must share with a citation. For prose it is the
    paragraph. For a table it is the table plus the caption line on either side of it,
    because the source of a table is conventionally written under it once ("Source:
    [market_sizing.geo_competitors]") rather than in every row.
    """

    def __init__(self, text: str):
        self.text = text
        self.starts: list[int] = []
        self.lines: list[str] = []
        pos = 0
        for line in text.split("\n"):
            self.starts.append(pos)
            self.lines.append(line)
            pos += len(line) + 1
        self.kinds = ["table" if ln.lstrip().startswith("|") else
                      "blank" if not ln.strip() else "text" for ln in self.lines]

    def line_at(self, pos: int) -> int:
        return bisect_right(self.starts, pos) - 1

    def _line_span(self, i: int) -> tuple[int, int]:
        return self.starts[i], self.starts[i] + len(self.lines[i])

    def _run(self, i: int, kind: str) -> tuple[int, int]:
        lo = hi = i
        while lo > 0 and self.kinds[lo - 1] == kind:
            lo -= 1
        while hi + 1 < len(self.lines) and self.kinds[hi + 1] == kind:
            hi += 1
        return lo, hi

    def _caption(self, i: int, step: int) -> Optional[int]:
        """The nearest text line past the blanks on one side of a table, if any."""
        j = i + step
        while 0 <= j < len(self.lines) and self.kinds[j] == "blank":
            j += step
        return j if 0 <= j < len(self.lines) and self.kinds[j] == "text" else None

    def block_span(self, pos: int) -> tuple[int, int]:
        i = self.line_at(pos)
        kind = self.kinds[i]
        if kind == "blank":
            return self._line_span(i)
        lo, hi = self._run(i, kind)
        if kind == "table":
            above, below = self._caption(lo, -1), self._caption(hi, +1)
            lo = above if above is not None else lo
            hi = below if below is not None else hi
        return self._line_span(lo)[0], self._line_span(hi)[1]

    def is_table_header(self, pos: int) -> bool:
        i = self.line_at(pos)
        return (self.kinds[i] == "table" and i + 1 < len(self.lines)
                and bool(_TABLE_SEPARATOR_RE.match(self.lines[i + 1])))

    def is_list_marker(self, pos: int) -> bool:
        i = self.line_at(pos)
        m = _LIST_MARKER_RE.match(self.lines[i])
        return bool(m) and self.starts[i] + m.start() <= pos < self.starts[i] + m.end()


def _resolve_citations(text: str, facts: dict, pool: set):
    """Every bracket, sorted into the spans that resolve and the bodies that do not.

    A bracket is a citation when at least one comma-separated path in it resolves; each
    resolving path adds its leaf numbers to the pool. A bracket no part of which resolves
    is prose: "[about $9,400 a month]" and the text of a markdown link are not citations,
    and the numbers in them are checked like any other.
    """
    resolved_spans: list[tuple[int, int]] = []
    unresolved: list[str] = []
    count = 0
    for m in _CITE_RE.finditer(text):
        count += 1
        hit = False
        for path in m.group(1).split(","):
            value = resolve_path(path, facts)
            if value is not None:
                hit = True
                leaf_numbers(value, 0, pool)
        if hit:
            resolved_spans.append((m.start(), m.end()))
        else:
            unresolved.append(m.group(1).strip())
    return count, resolved_spans, unresolved


def audit_synthesis(markdown: str, facts: dict, founder: Optional[set] = None,
                    handed: Optional[set] = None) -> dict:
    """Resolve every citation, pool their numbers, and check every number in the prose.

    Returns {"citations", "resolved", "unresolved", "checked", "offenders"} where
    `unresolved` lists citation bodies no part of which resolves and `offenders` lists
    (number as written, 60 characters of context) for every number the evidence cited
    does not contain. Pure: no result key is read here, so a test can hand it any prose.

    BRACKETS ARE NOT A HIDING PLACE. A citation path contributes no numeric token (see
    _NUM_RE), so every number on the page is checked wherever it sits, inside a bracket
    or outside one. The one span skipped is the destination of a markdown link.

    `founder` joins the pool: numbers the founder stated that the model cannot cite by
    path. `handed` joins it too and is the same kind of number with NO PATH AT ALL, so it
    is also exempt from the block rule. A whole number of 100 or less must normally
    share its paragraph with a citation, because the evidence is large and a small
    integer is in it by luck; a number the founder typed into a working session, or a
    credit balance the route asked the analyst to quote, is not in the evidence by luck
    and has nothing to cite, so holding it to the block rule would refuse "you have 23
    credits left" for want of a path that does not exist. The intake and the profile are
    NOT handed: they are in the fact layer and the model can cite them. A percentage is
    never handed: it is a claim about the evidence, whoever wrote the digits.
    """
    text = markdown or ""
    pool: set = set(founder or ()) | set(handed or ())
    citations, resolved_spans, unresolved = _resolve_citations(text, facts, pool)
    skipped = [(m.start(), m.end()) for m in _LINK_TARGET_RE.finditer(text)]

    layout = _Layout(text)
    checked = 0
    offenders: list[tuple[str, str]] = []
    for m in _NUM_RE.finditer(text):
        try:
            tok = _Token(m)
        except ValueError:
            continue
        if any(s <= tok.pos < e for s, e in skipped):
            continue
        if tok.is_trivial:
            continue
        if layout.is_table_header(tok.pos) or layout.is_list_marker(tok.pos):
            continue
        if (tok.whole and not tok.is_money and not tok.is_percent
                and _DESCRIPTOR_RE.match(text, tok.end)):
            continue
        checked += 1
        lo = max(0, tok.pos - _CONTEXT_CHARS * 2 // 3)
        context = text[lo:lo + _CONTEXT_CHARS].replace("\n", " ")
        if not _matches(tok.value, pool, tok.scales, exact=tok.is_year):
            offenders.append((tok.raw, context))
            continue
        if tok.whole and abs(tok.value) <= _SMALL_INTEGER:
            if handed and not tok.is_percent and tok.value in handed:
                continue
            b_lo, b_hi = layout.block_span(tok.pos)
            if not any(b_lo <= s < b_hi for s, _ in resolved_spans):
                offenders.append((tok.raw, context))
    return {"citations": citations, "resolved": len(resolved_spans),
            "unresolved": unresolved, "checked": checked, "offenders": offenders}


def audit_text(text: str, result: dict, founder_text: str = "") -> list[str]:
    """The D62 number audit on any prose written over this result: the offending numbers.

    THE SAME RULE THE REPORT IS HELD TO, FOR THE ANSWERS ABOUT IT. The workshop chat has
    the analyst answer the founder's questions from the evidence, and an answer is a
    fifty-token report: a number in it that is in no value it cites is an invented
    figure, and the founder must not read it. This is the gate's own resolver, pool and
    matcher over the same fact layer, with the founder's words pooled the way D62 pools
    them. `founder_text` is everything the founder handed the model that is not in the
    evidence (their messages, the passages they selected, their pinned notes, and the
    costs the route asked the analyst to quote): a price the founder stated or a balance
    the route stated is not a figure the model invented, and without this the analyst
    could not repeat the founder's own number back to them.

    Returns the numbers as written, in order, and nothing else: the caller decides what
    to do with an answer that has any, and the offenders are logged, never shown.
    """
    handed: set = set()
    if founder_text:
        leaf_numbers(str(founder_text), 0, handed)
    audit = audit_synthesis(text or "", _facts(result), _founder_numbers(result), handed)
    return [raw for raw, _ in audit["offenders"]]


def cited_paths(text: str, result: dict) -> list[str]:
    """The key paths a piece of prose cites that resolve in the fact layer, in order,
    once each. What the workshop stores beside an answer, so the page can link them."""
    facts = _facts(result)
    out: list[str] = []
    for m in _CITE_RE.finditer(text or ""):
        for path in m.group(1).split(","):
            path = path.strip()
            if path and path not in out and resolve_path(path, facts) is not None:
                out.append(path)
    return out


def d62_synthesis_numbers_are_in_the_evidence_it_cites(r: dict, html: Optional[str]) -> Finding:
    """Every number in the analyst report is in a value the report cites.

    The rule, in order: a citation's pool is the leaf numbers of what its path resolves to;
    every number in the prose, other than trivial integers, table header rows and list
    markers, must match some citation's pool at 0.6% at the scale its own suffix names; a
    whole number of 100 or less must also sit in a block (a paragraph, or a table with its
    caption) that carries a resolving citation, because small integers match somewhere by
    luck. Fails naming up to five offending numbers, each with 60 characters of context.

    Not applicable when the run produced no synthesis: that is a fact about the run, not
    about the report, and it must not thin D55's denominator on every report that
    predates the synthesis layer.
    """
    md = _synthesis_markdown(r)
    if md is None:
        return not_applicable("no synthesis on this run")
    audit = audit_synthesis(md, _facts(r), _founder_numbers(r))
    if audit["offenders"]:
        shown = "; ".join(f"{raw} in '...{ctx}...'"
                          for raw, ctx in audit["offenders"][:_MAX_OFFENDERS])
        return Finding(False, f"{len(audit['offenders'])} number(s) in the synthesis are in "
                              f"no value it cites ({audit['resolved']} of "
                              f"{audit['citations']} citations resolve): {shown}")
    return Finding(True, f"{audit['checked']} numeric token(s) all found in the "
                         f"{audit['resolved']} cited value(s)")


def d63_synthesis_citations_resolve(r: dict, html: Optional[str]) -> Finding:
    """Every [path] the analyst report cites exists in the fact layer.

    Advisory, not blocking. The prompt hands the model the names of the sections the run
    dropped, and citing one by name is the honest thing to do when saying what is
    missing. The number beside an unresolved citation is still held to D62 against the
    rest of the report's citations, so an unresolved path cannot launder an invented
    figure; it can only be a dead reference a reader should know about.
    """
    md = _synthesis_markdown(r)
    if md is None:
        return not_applicable("no synthesis on this run")
    audit = audit_synthesis(md, _facts(r))
    if audit["unresolved"]:
        shown = ", ".join(f"[{b[:80]}]" for b in audit["unresolved"][:_MAX_OFFENDERS])
        return Finding(False, f"{len(audit['unresolved'])} of {audit['citations']} "
                              f"citation(s) resolve to nothing in the fact layer: {shown}")
    return Finding(True, f"all {audit['citations']} citation(s) resolve")
