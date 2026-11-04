"""The fixed costs a founder stated, worked out in code.

The survey asks twice and the answers overlap: one cafe's monthly estimate held beans and
milk, already in its cost per drink, and its fixed costs held a lease the estimate left out.
Left to the writer, each run kept different lines and the break-even moved with them.
Lines count only when the estimate is broken down: a total with nothing behind it may hide
costs, so adding up what happens to be priced would understate them.
"""
from __future__ import annotations

import re
from typing import Any

from . import base

#: Costs that grow with each sale: in fixed_cost as well as the unit cost, they count twice.
PER_UNIT_COST = re.compile(r"ingredient|\bbeans?\b|\bmilk\b|\bcogs\b|cost of goods|materials?\b|"
                           r"inventory|packaging", re.I)
_LINE = re.compile(r"([^,:;.$]*)\$\s?(\d[\d,]*(?:\.\d+)?)(k\b)?", re.I)
_LEAD = re.compile(r"^\s*(?:(?:a|per|each)\s+(?:month|year|week)\b\s*)?(?:and\s+)?", re.I)
_TRAIL = re.compile(r"(?:\s+(?:is|are|of|about|around|roughly|approximately|at|costs?))*\s*$", re.I)
_NOT_MONTHLY = re.compile(r"\s*(?:(?:a|per|each|/)\s*(?:year|yr|annum|week|day)\b|annually|yearly|"
                         r"weekly|daily|one[\s-]?(?:time|off)|once|up\s?front)", re.I)
_TOTAL = re.compile(r"^(?:total|overall|all in)\b", re.I)


def _lines(text: str) -> list[dict[str, Any]]:
    found = []
    for match in _LINE.finditer(text):
        name = _TRAIL.sub("", _LEAD.sub("", match.group(1)))
        if name and not _TOTAL.match(name) and not _NOT_MONTHLY.match(text, match.end()):
            amount = float(match.group(2).replace(",", ""))
            found.append({"name": name, "amount": amount * 1000 if match.group(3) else amount})
    return found


def fixed_lines(context: dict) -> list[dict[str, Any]]:
    """Each stated monthly fixed cost once; empty when the founder gave no breakdown."""
    facts = base.intake_facts(context)
    estimate = _lines(facts.get("monthly_cost_estimate", "").partition(":")[2])
    if len(estimate) < 2:
        return []
    # The two answers repeat each other; within one answer an equal amount is another cost.
    stated = {line["amount"] for line in estimate}
    lines = estimate + [line for line in _lines(facts.get("fixed_costs", ""))
                        if line["amount"] not in stated]
    in_unit_cost = "$" in facts.get("unit_economics", "")
    return [line for line in lines
            if not (in_unit_cost and PER_UNIT_COST.search(line["name"]))]
