"""The customers and spend of a local venture's trade area, worked out in code.

Left to a planner, one cafe got its county in one run, a 1.5 km circle in the next and a
single census tract in the one after, so its market size moved with the planner's choice.
The area is a fixed circle around the venture, and the census counts every tract it
touches, because a single tract swings with how the address happens to be written.
"""
from __future__ import annotations

import re
from typing import Any

from treadle.execution_limits import charge_tool_call

from . import competitor_count

#: About a 20-minute walk, the distance competitor_count already uses for local rivals.
RADIUS_M = 1500
_POSSESSIVE = re.compile(r"^([A-Z][\w .-]*?)'s\s+(.+)$")
_WITHIN = re.compile(r"^(?:the\s+)?(.+?)\s+(?:neighbou?rhoods?|district|area)\s+(?:of|in)\s+(.+)$", re.I)
_AREA_WORD = re.compile(r"\s+(?:neighbou?rhoods?|district|area)\b", re.I)
_ARTICLE = re.compile(r"^the\s+", re.I)
CENSUS_URL, CENSUS_TITLE = ("https://data.census.gov/",
                            "US Census Bureau, American Community Survey")
BLS_URL, BLS_TITLE = ("https://www.bls.gov/cex/",
                      "US Bureau of Labor Statistics, Consumer Expenditure Survey")


def address(location: str) -> str:
    """A founder's way of naming a place, as the geocoder needs it: "Lincoln Park, Chicago"."""
    place = location.strip()
    possessive = _POSSESSIVE.match(place)
    if possessive:
        return _plain_place(f"{possessive.group(2)}, {possessive.group(1)}")
    within = _WITHIN.match(place)
    return _plain_place(f"{within.group(1)}, {within.group(2)}") if within else place


def _plain_place(text: str) -> str:
    return _AREA_WORD.sub("", _ARTICLE.sub("", text)).strip()


def figures(context: dict, registry: Any = None) -> list[dict]:
    """Households in the area and their spend; empty when the venture has no US trade area."""
    if competitor_count.inapplicable(context):
        return []
    if registry is None:
        from ..open_tools import research_tools
        registry = research_tools()

    def call(tool: str, **args: Any) -> dict:
        charge_tool_call()
        evidence = registry[tool].fn(**args)
        return evidence.payload if not evidence.error and isinstance(evidence.payload, dict) else {}

    location = competitor_count.location_phrase(context)
    spot = call("geocode_address", address=address(location))
    if not spot.get("state_fips") or not spot.get("county_fips"):
        return []  # Census data is US-only; the planner looks elsewhere.
    tracts = call("tracts_in_catchment", lat=spot["lat"], lng=spot["lng"], radius_m=RADIUS_M)
    geoids = sorted(tracts.get("geoids") or [])
    if not geoids:
        return []
    area = call("acs_income_distribution", state_fips=spot["state_fips"],
                county_fips=spot["county_fips"], geoids=geoids)
    if not area.get("households"):
        return []
    place = f"{RADIUS_M / 1000:g} km around {location}"
    found = [{"name": f"Households within {RADIUS_M / 1000:g} km", "kind": "customer_count",
              "value": area["households"], "unit": "households", "geography": place,
              "year": None, "source_url": CENSUS_URL, "source_tool": "acs_income_distribution",
              "source_title": CENSUS_TITLE, "retrieved_at": None, "trade_area": True}]
    spend = call("bls_cex_spend", category=competitor_count.category_phrase(context))
    if spend.get("annual_usd") and spend.get("covers"):
        found.append({"name": f"Household spend on {spend['covers']}", "kind": "broader_spend",
                      "value": spend["annual_usd"], "unit": "USD per household per year",
                      "geography": "United States", "year": None, "source_url": BLS_URL,
                      "source_tool": "bls_cex_spend", "source_title": BLS_TITLE,
                      "retrieved_at": None, "trade_area": True})
    return found
