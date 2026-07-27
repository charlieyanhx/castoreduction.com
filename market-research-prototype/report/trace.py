"""report/trace.py — sentence-level provenance: which script produced THIS sentence.

`section_provenance.py` answers "which module owns this block". For debugging that is one
step too coarse: seeing a sentence that reads wrong, you want the exact result path behind
it so you can go read the code that wrote that field.

DERIVED, NOT ANNOTATED. The report's prose IS the result values, so walking the result dict
gives {text -> JSON path} for free, and the first path segment is the result key that
SECTION_SOURCES already maps to a producing module. No template markup, so the mapping
cannot drift away from the template the way hand-placed tags do.

Three honest outcomes per block, because not all prose is model output:

  result    matched a result value — carries the exact path (`four_ps.price.narrative`,
            `differentiators.differentiators[4].why_unique`) and its producing module.
  template  static prose written in templates/report.html: the report's own framing, a
            caveat, a legend. "This sentence is not from a model" is a real debugging
            answer, and the section-level overlay could not give it.
  unmatched the template transformed the value before printing it (truncation like
            `[:120]`, currency formatting, interpolation into a sentence), so no exact run
            survives. Counted and reported — never quietly relabelled as template prose.

Annotation is tag-safe by construction: the HTML is split into tag and text runs and only
text runs are rewritten, so a value that also appears inside an attribute cannot corrupt the
markup. Debug-only — this changes the bytes of the page, never what it says.
"""
from __future__ import annotations

import html as _html
import re
from typing import Any, Optional

# Below this length a value is a label or an enum ("high", "llm", "direct", "seat") and
# would match all over the page, attributing prose to whatever field happened to share a
# word with it.
_MIN_VALUE_CHARS = 25
# A block shorter than this is a heading, a number or a table cell — not a sentence anyone
# needs to trace.
_MIN_BLOCK_CHARS = 40

# Run-bookkeeping keys, not report content. `_trace` in particular holds the run's own event
# log, so indexing it attributes report prose to the provenance panel's own text.
_SKIP_KEYS = ("_trace", "_cogs", "_steps_completed", "_elapsed_seconds", "_verification")


def index_values(result: dict) -> list[tuple[str, str]]:
    """Every substantial string in `result` paired with its JSON path, LONGEST FIRST.

    Longest-first matters: a short value that is a substring of a longer one must not claim
    the longer one's text. `differentiators.items[0].why` and a summary quoting it would
    otherwise race, and the shorter (less specific) path could win.
    """
    found: dict[str, str] = {}

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in _SKIP_KEYS:
                    continue
                walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")
        elif isinstance(node, str):
            text = node.strip()
            if len(text) >= _MIN_VALUE_CHARS and text not in found:
                found[text] = path

    walk(result or {}, "")
    return sorted(found.items(), key=lambda kv: -len(kv[0]))


def _root_key(path: str) -> str:
    """The result key a path hangs off — `personas[2].name` -> `personas`."""
    return re.split(r"[.\[]", path, maxsplit=1)[0]


def producer_for_path(path: str) -> Optional[dict]:
    """The producing script for a result path, via its root key.

    Falls back through a sibling key before giving up: side-channel results hang off a
    variant of a mapped key (`audiences_undecodable` beside `audiences`,
    `pricing_benchmark` beside `pricing`), and answering "?" for those would send a
    debugger looking for a module that does not exist. Returns None only when even the
    stem is unmapped, and the caller then reports the root key itself — which is still a
    better lead than a question mark."""
    from report.section_provenance import producer_for
    root = _root_key(path)
    hit = producer_for(root)
    if hit:
        return hit
    # `audiences_undecodable` -> `audiences`; `segment_ranking_debug` -> `segment_ranking`.
    parts = root.split("_")
    while len(parts) > 1:
        parts.pop()
        hit = producer_for("_".join(parts))
        if hit:
            return {**hit, "via": root}
    return None


_TAG_SPLIT = re.compile(r"(<[^>]+>)")
_BLOCK = re.compile(r"<(p|li|td|h3|blockquote)\b[^>]*>(.*?)</\1>", re.S | re.I)


def _variants(text: str) -> list[str]:
    """The forms a result value can take once Jinja has rendered it."""
    escaped = _html.escape(text, quote=False)
    out = [text] if text == escaped else [escaped, text]
    # Jinja's autoescape turns ' into &#39; in some paths and leaves it in others.
    alt = escaped.replace("'", "&#39;")
    if alt not in out:
        out.append(alt)
    return out


def annotate(html: str, result: dict) -> tuple[str, dict]:
    """Wrap every result-derived run of text in a span naming its path and producer.

    Returns (annotated_html, stats). Only text runs are rewritten — never the inside of a
    tag — so a value that is also an attribute value cannot break the markup.
    """
    values = index_values(result)
    if not values:
        return html, {"matched": 0, "blocks": len(_BLOCK.findall(html)), "unmatched": 0}

    segments = _TAG_SPLIT.split(html)
    matched_paths: set[str] = set()
    for i, segment in enumerate(segments):
        if not segment or segment.startswith("<") or len(segment.strip()) < _MIN_VALUE_CHARS:
            continue
        # Collect NON-OVERLAPPING matches against the pristine text first, then splice once.
        # Inserting as we go would let a later (shorter) value match inside a span already
        # written — the attribute text becomes matchable content, and the output is
        # `data-src="…" data-by="…"` printed inside the report. Found live.
        claims: list[tuple[int, int, str, str]] = []   # (start, end, matched_form, path)
        for text, path in values:
            if path in matched_paths:
                continue          # one span per path — the first rendering owns it
            for form in _variants(text):
                if not form:
                    continue
                at = segment.find(form)
                while at != -1:
                    end = at + len(form)
                    if not any(at < c_end and c_start < end
                               for c_start, c_end, _f, _p in claims):
                        claims.append((at, end, form, path))
                        matched_paths.add(path)
                        break
                    at = segment.find(form, at + 1)
                if path in matched_paths:
                    break
        if not claims:
            continue
        out_parts, cursor = [], 0
        for start, end, form, path in sorted(claims):
            producer = producer_for_path(path) or {}
            out_parts.append(segment[cursor:start])
            out_parts.append(
                f'<span class="tr" data-src="{_html.escape(path, quote=True)}"'
                f' data-by="{_html.escape(str(producer.get("module") or _root_key(path)), quote=True)}"'
                f' data-origin="{_html.escape(str(producer.get("origin") or "?"), quote=True)}"'
                f'>{form}</span>')
            cursor = end
        out_parts.append(segment[cursor:])
        segments[i] = "".join(out_parts)
    out = "".join(segments)
    blocks = len(_BLOCK.findall(html))
    matched = out.count('<span class="tr"')
    return out, {"matched": matched, "blocks": blocks,
                 "unmatched": max(blocks - matched, 0)}


def trace_report(html: str, result: dict) -> list[dict]:
    """One row per substantial block: what produced it, and where to go look.

    Every block gets an answer. A block whose text matches no result value is the report's
    own prose — attributed to the template, which is exactly the fact a debugger needs when
    a sentence reads oddly but no model wrote it.
    """
    values = index_values(result)
    rows: list[dict] = []
    for _tag, inner in _BLOCK.findall(html):
        text = _html.unescape(re.sub(r"<[^>]+>", "", inner)).strip()
        if len(text) < _MIN_BLOCK_CHARS:
            continue
        hit = next(((v, p) for v, p in values
                    if v[:60] in text or (len(text) >= 60 and text[:60] in v)), None)
        if hit:
            producer = producer_for_path(hit[1]) or {}
            rows.append({"kind": "result", "path": hit[1],
                         "module": producer.get("module") or _root_key(hit[1]),
                         "produced_by": producer.get("produced_by") or "?",
                         "origin": producer.get("origin") or "?",
                         "text": text[:120]})
        else:
            rows.append({"kind": "template", "path": None,
                         "module": "templates/report.html",
                         "produced_by": "report template",
                         "origin": "authored", "text": text[:120]})
    return rows
