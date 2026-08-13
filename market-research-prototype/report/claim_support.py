"""report/claim_support.py — does a footnoted number come from anything we measured?

report/citation.py already answers the STRUCTURAL questions: does a checkable sentence
carry a marker, and does that marker resolve to an emitted citation. Both can be true
while the sentence is still false — the marker resolves, and the source it resolves to
says nothing about the number in front of it.

MEASURED across runs 12-15, 190 footnoted sentences in the 4Ps sections:

    119 (63%)  carried a number the claim text their own marker points at never states
     91        of those are real pipeline values cited to the wrong source
     28 (15%)  appear NOWHERE in the deterministic inputs the section was handed

The 28 are almost all quantified operational targets: "150 drinks/day", "500 local
workers", "150 monthly high-intent searches", "$0.45 per click", "0.5 miles", "4 blocks".

WHAT THE MEASUREMENT CORRECTED. The intuitive fix is "stop inventing volume targets", and
the volume_ladder reminder already forbids that — a target must sit between break-even and
the obtainable ceiling. On run15 that band is 47.7 to 324/day, so "150 drinks/day"
COMPLIES. The model is obeying its instructions.

The defect is that a PROPOSAL is dressed as a MEASUREMENT. "Target 150 drinks per day,
sitting above the 47.7 break-even threshold at a $5.50 price anchor ³" places one invented
number and two computed ones under a single marker, and the invented one inherits the
others' authority. A reader cannot tell them apart — which is the whole value of a footnote.

So the invariant is about MARKERS, not magnitudes: a citation marker may only sit on a
sentence whose numbers came from the facts the section was handed (or from the cited claim
itself). An operator target is legitimate prose; it just has to appear uncited and labelled
as a recommendation.

ADVISORY BY CONSTRUCTION. This reads prose with a regex, which is unsound in both
directions: it cannot see a fabricated ATTRIBUTE ("3-minute pour-over" survives if the 3 is
handed elsewhere), and it will occasionally flag honest derived arithmetic. A false
positive must cost a line of noise, never a paid report. The complementary half of the fix
is prompt-side (four_ps._r_citation_discipline) — this catches what slips through.
"""
from __future__ import annotations

import re
from typing import Iterable

from .citation import _marker_ids, sentences

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_PCT_IN_SENTENCE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s?%")

_SECTIONS = ("product", "price", "place", "promotion")

# Numbers this small are ordinals, list counts and calendar noise ("the 3 pillars",
# "within 6 months") far more often than they are asserted measurements, and flagging
# them buried the real findings. The measured class — targets, volumes, spends — sits
# comfortably above it.
_MIN_INTERESTING = 10.0

_MAX_DEPTH = 8


def _numbers_in(text) -> set[float]:
    out: set[float] = set()
    for m in _NUM.finditer(str(text or "")):
        try:
            out.add(float(m.group().replace(",", "")))
        except ValueError:
            continue
    return out


def _walk_numbers(obj, depth: int = 0) -> set[float]:
    """Every number reachable in a payload — values, and numerals inside strings."""
    if depth > _MAX_DEPTH or isinstance(obj, bool):
        return set()
    if isinstance(obj, (int, float)):
        return {float(obj)}
    if isinstance(obj, str):
        return _numbers_in(obj)
    out: set[float] = set()
    if isinstance(obj, dict):
        for v in obj.values():
            out |= _walk_numbers(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            out |= _walk_numbers(v, depth + 1)
    return out


def given_numbers(result: dict) -> set[float]:
    """The deterministic values the 4Ps sections were actually handed.

    Deliberately EXCLUDES four_ps and viability: if the narrative counted as its own
    evidence, every invented number would justify itself, and the check would pass by
    construction on exactly the reports it exists to catch.
    """
    r = result or {}
    given: set[float] = set()
    for key in ("max_diff", "market_sizing", "economics", "differentiators",
                "competitor_pricing", "financials"):
        given |= _walk_numbers(r.get(key))
    pricing = r.get("pricing") or {}
    for key in ("psm", "benchmark", "break_even", "price_of_record"):
        given |= _walk_numbers(pricing.get(key))
    disc = r.get("discover") or {}
    for key in ("competitor_density", "active_signal_density", "avg_opportunity_score"):
        given |= _walk_numbers(disc.get(key))

    # The volume ladder's rungs are computed in Python and injected into every section
    # prompt, so they are legitimately citable even though they appear in no stored field.
    econ = r.get("economics") or {}
    som = ((r.get("market_sizing") or {}).get("som") or {}).get("mid")
    price = econ.get("price_per_unit")
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
           for v in (som, price)):
        per_day = som / price / 365
        given |= {round(per_day, 1), float(int(per_day)), float(round(per_day))}
    return given


def _derivable(n: float, supported: Iterable[float], sentence: str) -> float | None:
    """Is `n` honest arithmetic on a supported number rather than a new assertion?

    Returns the percentage that explains it, or None. The percentage matters to the
    caller: a rate that successfully explains another number in its own sentence is the
    OPERATOR of a derivation, not an independent assertion, so flagging it would report
    the same honest arithmetic twice.

    MEASURED false positive this exists for: "buy 1 drink at $5.50, get the second at
    50% off, $8.25 total" flagged BOTH 8.25 (which is 5.50 x 1.5) and the 50. Scoped
    deliberately to percentages appearing IN THE SAME SENTENCE — a general two-term
    arithmetic search over a few hundred handed numbers matches almost anything by
    coincidence, which would quietly turn the whole check off.
    """
    pcts = {float(p.replace(",", "")) for p in _PCT_IN_SENTENCE.findall(sentence or "")}
    for base in supported:
        if not base:
            continue
        for p in pcts:
            f = p / 100.0
            for cand in (base * f, base * (1 + f), base * (1 - f), base * (2 - f),
                         base / f if f else 0):
                if cand and abs(cand - n) < 0.01:
                    return p
    return None


def unsupported_in_section(section: dict, given: Iterable[float]) -> list[dict]:
    """Footnoted numbers in one section that nothing we measured supports.

    A number is supported when the cited claim states it, when the pipeline handed it to
    the section, or when it is arithmetic on either. A sentence with no RESOLVING marker
    is skipped entirely: an uncited target is the honest form we are asking for, and a
    dangling marker is already audit_narrative's finding — reporting it here too would
    make one defect look like two.
    """
    sec = section or {}
    given = set(given or ())
    claims: dict[int, str] = {}
    for c in sec.get("citations") or []:
        if not isinstance(c, dict):
            continue
        try:
            claims[int(c.get("id"))] = f"{c.get('claim') or ''} {c.get('source') or ''}"
        except (TypeError, ValueError):
            continue

    rows: list[dict] = []
    texts = [sec.get("narrative") or ""] + [t for t in (sec.get("key_takeaways") or [])
                                            if isinstance(t, str)]
    for text in texts:
        for sent in sentences(text):
            ids = [i for i in _marker_ids(sent) if i in claims]
            if not ids:
                continue
            supported = set(given)
            for i in ids:
                supported |= _numbers_in(claims[i])
            # Derivability is resolved for EVERY number first, including ones too small
            # to report: "…50% off, $8.25 for two" derives 8.25 from a handed $5.50, and
            # that is what marks the 50 as an operator rather than a claim. Applying the
            # size floor first skipped 8.25, so the 50 was reported as invented — the
            # filter silently disabled the exemption it feeds.
            candidates, operator_pcts = [], set()
            for n in sorted(_numbers_in(sent)):
                if n in supported:
                    continue
                pct = _derivable(n, supported, sent)
                if pct is not None:
                    operator_pcts.add(pct)
                elif n >= _MIN_INTERESTING:
                    candidates.append(n)
            for n in candidates:
                if n in operator_pcts:
                    continue
                rows.append({"number": n, "citation_ids": ids, "sentence": sent.strip()})
    return rows


def unsupported_citations(result: dict) -> list[dict]:
    """Every footnoted number in the 4Ps that nothing in the run supports."""
    r = result or {}
    four_ps = r.get("four_ps")
    if not isinstance(four_ps, dict):
        return []
    given = given_numbers(r)
    out: list[dict] = []
    for name in _SECTIONS:
        sec = four_ps.get(name)
        if not isinstance(sec, dict):
            continue
        for row in unsupported_in_section(sec, given):
            out.append(dict(row, section=name))
    return out
