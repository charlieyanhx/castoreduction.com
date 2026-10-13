"""packs/travel_guide — the second report type, and the proof the engine is general.

A city guide: neighbourhoods, a walking route that depends on them, seasonal advice,
restaurants only when the trip is a food trip, and a budget that reads two of the others.
The content is deliberately canned, and declared so (origin="simulated", which the
FactStore records as inferred). What this pack exercises is the ENGINE (ordering
derived from declarations, bounded reads, per-section checks, not-applicable as an
answer) on a domain that shares nothing with market research.
"""
from __future__ import annotations

from treadle.core.section import Section
from treadle import ReportType, register

#: A day of walking. A route longer than this is not a walking route.
MAX_WALK_KM = 20


def _route_is_walkable(payload) -> str | None:
    legs = (payload or {}).get("legs") or []
    total = sum(float(leg.get("km") or 0) for leg in legs)
    if total > MAX_WALK_KM:
        return f"the route totals {total:.1f} km across {len(legs)} legs, which is not a walk"
    return None


def _stops_are_described(payload) -> str | None:
    named = {leg.get("area") for leg in (payload or {}).get("legs") or []}
    invented = sorted(n for n in named if n and n not in set(payload.get("known_areas") or []))
    if invented:
        return f"the route walks through {', '.join(invented)}, which the guide never described"
    return None


def _neighbourhoods(ctx: dict) -> dict:
    return {"areas": ["Alfama", "Chiado"]}


def _walking_route(ctx: dict) -> dict:
    areas = ctx["neighbourhoods"].get("areas") or []
    return {"legs": [{"area": a, "km": 2.5} for a in areas], "known_areas": areas}


def _seasonal(ctx: dict) -> dict:
    return {"note": "Aim for shoulder season."}


def _restaurants(ctx: dict) -> dict:
    return {"picks": ["Cervejaria Ramiro"], "count": 1}


def _budget(ctx: dict) -> dict:
    picks = (ctx.get("restaurants") or {}).get("picks") or []
    return {"per_day": 60 + 20 * len(picks), "currency": "EUR"}


def sections(brief: dict) -> list[Section]:
    interests = list(brief.get("interests") or [])

    def not_a_food_trip() -> str | None:
        if "food" in interests:
            return None
        return (f"this guide is for a {brief.get('days', '?')}-day "
                f"{'/'.join(interests) or 'general'} trip, which did not ask for restaurants")

    return [
        Section(key="neighbourhoods", produce=_neighbourhoods, label="Where to stay",
                origin="simulated"),
        Section(key="walking_route", produce=_walking_route, consumes=("neighbourhoods",),
                label="A day on foot", origin="computed",
                invariants=(("walkable", _route_is_walkable),
                            ("stops_are_described", _stops_are_described))),
        Section(key="seasonal", produce=_seasonal, label="When to go", origin="simulated"),
        Section(key="restaurants", produce=_restaurants, label="Where to eat", origin="simulated",
                inapplicable=not_a_food_trip),
        Section(key="budget", produce=_budget, consumes=("neighbourhoods",),
                optional=("restaurants",), label="What it costs", origin="computed"),
    ]


REPORT_TYPE = register(ReportType(
    key="travel_guide",
    label="City travel guide",
    description="A short guide to one city, shaped by the trip's length and interests.",
    sections=sections,
))
