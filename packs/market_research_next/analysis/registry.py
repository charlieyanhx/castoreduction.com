"""Which analysis sections exist, what each waits for, and where each is shown.

Modules are imported by name on first use, so importing the pack does not pull in every
section's dependencies. Every section also needs `prep`; "optional" inputs are waited
for when they run and read when present. See docs/treadle/AGENT_REVAMP.md.
"""
from __future__ import annotations

import importlib
from types import ModuleType

ANALYSIS = (
    "competitor_count", "community_discovery", "pricing", "sizing", "five_forces",
    "generic_strategy", "analog_markets", "positioning", "go_to_market",
)

#: Required upstream sections (besides prep). Missing one, the section is skipped.
REQUIRES = {
    "competitor_count": (),
    "community_discovery": (),
    "pricing": ("competitive_landscape",),
    "sizing": ("market_size",),
    "five_forces": ("competitive_landscape",),
    "generic_strategy": ("five_forces",),
    "analog_markets": ("competitive_landscape",),
    "positioning": ("target_personas", "competitive_landscape", "customer_voice",
                    "differentiators", "generic_strategy"),
    "go_to_market": ("positioning", "target_personas"),
}

#: Optional upstream sections, for analysis and research sections alike. A research
#: section's required inputs stay in the pack's DEPENDENCIES.
OPTIONAL = {
    "competitive_landscape": ("competitor_count",),
    "customer_voice": ("community_discovery",),
    "market_size": ("competitor_count",),
    "revenue": ("sizing", "pricing"),
    "pricing": ("competitor_count",),
    "sizing": ("pricing", "competitor_count", "competitive_landscape"),
    "five_forces": ("pricing", "sizing"),
    "generic_strategy": ("pricing", "sizing"),
    "analog_markets": ("sizing",),
    "positioning": ("sizing", "pricing"),
    "go_to_market": ("pricing",),
}

#: Computed in code from fetched data; everything else in ANALYSIS is model analysis.
COMPUTED = frozenset({"competitor_count", "community_discovery", "sizing"})

#: Where each analysis section is shown: after the research section named here.
SHOWN_AFTER = {
    "positioning": "summary",
    "community_discovery": "customer_voice",
    "competitor_count": "competitive_landscape",
    "pricing": "competitor_count",
    "sizing": "market_size",
    "five_forces": "differentiators",
    "generic_strategy": "five_forces",
    "analog_markets": "generic_strategy",
    "go_to_market": "analog_markets",
}


def module(key: str) -> ModuleType:
    if key not in ANALYSIS:
        raise KeyError(f"no analysis section {key!r}")
    return importlib.import_module(f"{__package__}.{key}")


def label(key: str) -> str:
    return module(key).LABEL


def display_order(research_order: tuple[str, ...]) -> tuple[str, ...]:
    """The research display order with each analysis section placed after its anchor."""
    order = list(research_order)
    placed = True
    pending = list(ANALYSIS)
    while pending and placed:
        placed = False
        for key in list(pending):
            anchor = SHOWN_AFTER[key]
            if anchor in order:
                order.insert(order.index(anchor) + 1 + _run_after(order, anchor), key)
                pending.remove(key)
                placed = True
    return tuple(order + pending)


def _run_after(order: list[str], anchor: str) -> int:
    """How many analysis sections already sit directly after `anchor`, so later ones
    anchored to it land behind them rather than in front."""
    count, index = 0, order.index(anchor) + 1
    while index < len(order) and order[index] in ANALYSIS and SHOWN_AFTER[order[index]] == anchor:
        count, index = count + 1, index + 1
    return count
