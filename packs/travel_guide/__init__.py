"""packs/travel_guide: the second report type, and the proof the engine is general.

A city guide: neighbourhoods, a walking route that depends on them, seasonal advice,
restaurants only when the trip is a food trip, and a budget that reads two of the others.
The content is deliberately canned, and declared so (origin="simulated", which the
FactStore records as inferred). What this pack exercises is the ENGINE: ordering derived
from declarations, bounded reads, per-section checks, not-applicable as an answer, and a
survey derived from what the sections read.

ITS SURVEY IS WRITTEN NOWHERE. Each section names what it reads in consumes, optional and
decided_by, exactly as it names other sections; INPUTS says once how each outside value
arrives. treadle.Survey derives the questions:

  city        ask       read by Where to stay, When to go and Where to eat
  interests   ask       decides one thing: whether Where to eat exists at all
  diet        ask       read only by Where to eat, so asked only on a food trip. No default,
                        on purpose: assuming "no restrictions" for someone with an allergy
                        is the one assumption a food guide must not make
  days        ask       every budget figure is multiplied by it
  currency    find/ask  an agent may look it up; a person confirms it
  travellers  assume    one traveller unless someone says so; disclosed, never asked. The
                        per-person figure stands without it; only the total rests on it
  month       optional  sharpens When to go; never asked before the guide can run

The factory reads nothing from the brief: every outside value is a declared input, so
nothing is closed over and every read is traced.
"""
from __future__ import annotations

from treadle import Input, ReportType, register
from treadle.core.section import Section
from treadle.inputs import ASK, ASSUME, CHOICE, FIND, LOCATION, NUMBER

#: A day of walking. A route longer than this is not a walking route.
MAX_WALK_KM = 20
BASE_PER_DAY = 60
PER_RESTAURANT = 20
INTERESTS = ("food", "architecture", "museums", "nightlife", "outdoors", "meetings")
DIETS = ("none", "vegetarian", "vegan", "halal", "kosher", "gluten-free")
CURRENCIES = ("EUR", "GBP", "USD", "JPY")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")
#: Canned content per city; any other city gets the generic entry.
_AREAS = {"lisbon": ("Alfama", "Chiado"), "porto": ("Ribeira", "Baixa")}
_ANY_AREAS = ("Old town", "Centre")
_PICKS = {"lisbon": ("Cervejaria Ramiro",), "porto": ("Cafe Santiago",)}
_ANY_PICKS = ("the central market",)

INPUTS = (
    Input("city", "Which city is the guide for?", input_kind=LOCATION),
    Input("interests", "What is the trip for? Pick any that apply.",
          input_kind=CHOICE, options=INTERESTS, many=True),
    Input("diet", "Any dietary needs the restaurant picks must respect? 'none' is an answer.",
          input_kind=CHOICE, options=DIETS, write_in=True),
    Input("days", "How many days is the trip?", input_kind=NUMBER, unit_hint="days",
          low=1, high=60),
    Input("currency", "Which currency should the budget be in?", how=(FIND, ASK),
          input_kind=CHOICE, options=CURRENCIES, write_in=True,
          look_for="the currency used in the trip's city"),
    Input("travellers", "How many people are travelling?", how=(ASK, ASSUME),
          input_kind=NUMBER, unit_hint="people", low=1, high=20, load_bearing=False,
          default=1, assumption="one traveller"),
    Input("month", "Which month are you going?", input_kind=CHOICE, options=MONTHS),
)


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


def _not_a_food_trip(decided: dict) -> str | None:
    """Decided by `interests` alone, and declared so: the read is traced, it orders the
    survey, and it is never made on a context that lacks it."""
    interests = decided["interests"]
    if "food" in interests:
        return None
    return (f"this trip's interests ({', '.join(interests) or 'none given'}) do not "
            f"include food, so the guide has no restaurant section")


def _city(ctx: dict) -> str:
    return ctx["city"].strip().casefold()


def _neighbourhoods(ctx: dict) -> dict:
    return {"areas": list(_AREAS.get(_city(ctx), _ANY_AREAS))}


def _walking_route(ctx: dict) -> dict:
    areas = ctx["neighbourhoods"].get("areas") or []
    return {"legs": [{"area": a, "km": 2.5} for a in areas], "known_areas": areas}


def _seasonal(ctx: dict) -> dict:
    month = ctx.get("month")
    if not month:
        return {"note": "Aim for shoulder season."}
    return {"note": f"{ctx['city']} in {month}: book ahead if that is high season."}


def _restaurants(ctx: dict) -> dict:
    diet = ctx["diet"]
    picks = (list(_PICKS.get(_city(ctx), _ANY_PICKS)) if diet == "none"
             else [f"a {diet} place near the centre"])
    return {"picks": picks, "count": len(picks), "diet": diet}


def _budget(ctx: dict) -> dict:
    picks = (ctx.get("restaurants") or {}).get("picks") or []
    per_day = BASE_PER_DAY + PER_RESTAURANT * len(picks)
    per_person = per_day * ctx["days"]
    return {"per_day": per_day, "days": ctx["days"], "per_person": per_person,
            "travellers": ctx["travellers"], "total": per_person * ctx["travellers"],
            "currency": ctx["currency"]}


SECTIONS = (
    Section(key="neighbourhoods", produce=_neighbourhoods, consumes=("city",),
            label="Where to stay", origin="simulated"),
    Section(key="walking_route", produce=_walking_route, consumes=("neighbourhoods",),
            label="A day on foot", origin="computed",
            invariants=(("walkable", _route_is_walkable),
                        ("stops_are_described", _stops_are_described))),
    Section(key="seasonal", produce=_seasonal, consumes=("city",), optional=("month",),
            label="When to go", origin="simulated"),
    Section(key="restaurants", produce=_restaurants, consumes=("city", "diet"),
            decided_by=("interests",), inapplicable=_not_a_food_trip,
            label="Where to eat", origin="simulated"),
    Section(key="budget", produce=_budget,
            consumes=("neighbourhoods", "days", "currency", "travellers"),
            optional=("restaurants",), label="What it costs", origin="computed"),
)


def sections(_values: dict) -> list[Section]:
    """The guide's plan. It reads nothing from its argument: see the module docstring."""
    return list(SECTIONS)


REPORT_TYPE = register(ReportType(
    key="travel_guide",
    label="City travel guide",
    description="A short guide to one city, shaped by the trip's length and interests.",
    sections=sections,
    inputs=INPUTS,
))
