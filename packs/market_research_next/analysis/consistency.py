"""Cross-section number check: two sections giving different figures for one topic.

The numeric rule is adapted from RivalSearchMCP core/conflict/detect.py (MIT,
damionrashford): numbers of the same unit class that differ by 5% or more and share at
least two topic words within 50 characters are a conflict, with confidence
min(1, 0.5 + 0.1 x shared words). Standard library only, so the review step can run it
without the pack's model or HTTP dependencies.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from itertools import combinations
from typing import Any

_SCALES = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9,
           "t": 1e12, "trillion": 1e12}
_UNIT_CLASSES = {
    "percent": ("%", "percent", "pc"),
    "mass": ("kg", "g", "lb", "lbs", "oz"),
    "storage": ("gb", "mb", "kb"),
    "frequency": ("ghz", "mhz"),
    "duration": ("year", "years", "yr", "yrs", "month", "months", "mo", "mos", "day", "days",
                 "hour", "hours", "hr", "hrs"),
    "headcount": ("people", "users", "employees", "customers"),
}
_CLASS_OF = {unit: name for name, units in _UNIT_CLASSES.items() for unit in units}
_UNIT_WORDS = "|".join(sorted((u for u in _CLASS_OF if u != "%"), key=len, reverse=True))
#: The lookbehind keeps digits inside words (B2B, Q3, COVID-19) and decimals out.
_NUMBER = re.compile(
    r"(?:(?P<cur>[$£€])\s?)?(?<![\w.,])"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s?(?P<scale>thousand|million|billion|trillion|[kmbt])\b)?"
    rf"(?:\s?(?P<unit>%|(?:{_UNIT_WORDS})\b))?",
    re.IGNORECASE)
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,}")
_STOP = frozenset("""
about above after again against also among approximately around been before being below
between both could does doing down during each estimate estimated from further have having
here into just less like many more most much only other over same some such than that their
them then there these they this those through under until very were what when where which
while will with within would your thousand million billion trillion percent roughly nearly
""".split())
_WINDOW, _CONTEXT, _MIN_GAP, _MIN_SHARED = 50, 60, 0.05, 2
#: The units a repair is worth a correction round for: market sizes, prices, shares.
_REVIEWED = frozenset({"money", "percent"})


def _numbers(text: str) -> list[dict]:
    found = []
    for match in _NUMBER.finditer(text):
        raw, cur, scale, unit = (match.group("num"), match.group("cur"),
                                 (match.group("scale") or "").lower(),
                                 (match.group("unit") or "").lower())
        plain = not (cur or scale or unit)
        if plain and "," not in raw and "." not in raw and 1900 <= int(raw) <= 2100:
            continue  # a year, not a figure
        if plain and _is_list_index(text, match.start("num"), match.end("num")):
            continue
        if plain and re.match(r"(?:st|nd|rd|th)\b", text[match.end("num"):], re.I):
            continue  # an ordinal or a street ("23rd"), not a figure
        value = float(raw.replace(",", "")) * _SCALES.get(scale, 1.0)
        kind = ("money" if cur else _CLASS_OF.get(unit) if unit
                else "count" if scale else "bare")
        window = text[max(0, match.start() - _WINDOW):match.end() + _WINDOW]
        found.append({"value": value, "kind": kind,
                      "tokens": {t.lower() for t in _TOKEN.findall(window)
                                 if len(t) > 3 and t.lower() not in _STOP},
                      "context": text[max(0, match.start() - _CONTEXT):
                                      match.end() + _CONTEXT].strip()})
    return found


def _is_list_index(text: str, start: int, end: int) -> bool:
    """"1." or "2)" opening a line numbers a list item; it is not a figure."""
    line_start = text.rfind("\n", 0, start) + 1
    after = text[end:end + 2]
    return (not text[line_start:start].strip() and after[:1] in (".", ")")
            and (len(after) < 2 or after[1].isspace()))


def find_number_conflicts(texts: Mapping[str, str],
                          kinds: frozenset[str] | None = None) -> list[dict]:
    """Every pair of sections stating different same-unit figures on one topic.

    Two sections describing one venture share most of their words, so the upstream rule
    (any two figures near two shared words) pairs unrelated numbers: "a 10-minute walk or
    2-mile drive" in both sections reads as 10 against 2. A pair is no conflict when each
    section also states the other's figure, and `kinds` limits the check to the units
    whose disagreement matters (the review uses money and percent).
    """
    numbers = {key: [n for n in _numbers(text) if kinds is None or n["kind"] in kinds]
               for key, text in texts.items() if isinstance(text, str)}
    best: dict[tuple[str, str, str], dict] = {}
    for key_a, key_b in combinations(numbers, 2):
        stated_a = {(n["kind"], round(n["value"], 2)) for n in numbers[key_a]}
        stated_b = {(n["kind"], round(n["value"], 2)) for n in numbers[key_b]}
        for a in numbers[key_a]:
            for b in numbers[key_b]:
                if a["kind"] != b["kind"]:
                    continue
                if ((b["kind"], round(b["value"], 2)) in stated_a
                        or (a["kind"], round(a["value"], 2)) in stated_b):
                    continue  # each also states the other's figure: they agree
                largest = max(abs(a["value"]), abs(b["value"]))
                if largest == 0 or abs(a["value"] - b["value"]) / largest < _MIN_GAP:
                    continue
                shared = a["tokens"] & b["tokens"]
                if len(shared) < _MIN_SHARED:
                    continue
                topic = " ".join(sorted(shared)[:5])
                item = {"type": "number", "topic": topic, "section_a": key_a,
                        "section_b": key_b, "value_a": a["value"], "value_b": b["value"],
                        "context_a": a["context"], "context_b": b["context"],
                        "confidence": round(min(1.0, 0.5 + 0.1 * len(shared)), 2)}
                slot = (key_a, key_b, topic)
                if slot not in best or item["confidence"] > best[slot]["confidence"]:
                    best[slot] = item
    return list(best.values())


def section_texts(result: Mapping[str, Any], keys: Iterable[str]) -> dict[str, str]:
    """Narrative, findings and assumptions of each envelope, as one text per section."""
    texts = {}
    for key in keys:
        section = result.get(key)
        if not isinstance(section, dict):
            continue
        parts = [section.get("narrative")]
        for field in ("findings", "assumptions"):
            items = section.get(field)
            parts += items if isinstance(items, list) else []
        text = "\n".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
        if text:
            texts[key] = text
    return texts


def _figure(value: float) -> str:
    return f"{value:,.0f}" if value.is_integer() else f"{value:,.2f}"


def conflict_issues(result: Mapping[str, Any], *, model_written: set[str],
                    min_confidence: float = 0.7, limit: int = 3) -> list[dict]:
    """Repair issues for the strongest conflicts between model-written sections."""
    keys = [key for key in result if key in model_written]
    conflicts = [c for c in find_number_conflicts(section_texts(result, keys), _REVIEWED)
                 if c["confidence"] >= min_confidence]
    conflicts.sort(key=lambda c: -c["confidence"])  # stable: section order breaks ties
    return [{"code": "number_conflict",
             "message": (f'{c["section_a"]} says "{c["context_a"]}" and {c["section_b"]} says '
                         f'"{c["context_b"]}". The figures {_figure(c["value_a"])} and '
                         f'{_figure(c["value_b"])} differ on the same topic ({c["topic"]}). '
                         "Use one figure, or state why they differ."),
             "repair": [c["section_a"], c["section_b"]]}
            for c in conflicts[:max(0, limit)]]
