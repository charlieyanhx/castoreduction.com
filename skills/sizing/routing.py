"""skills/sizing/routing.py — which sizing method a physical venture gets, and why.

WHY THIS IS NOT IN plan.py. The choice between a trade-area model, a city-scale scan and
a multi-site rollout is a METHODS decision: it turns on what the geocoder matched and how
many premises the venture runs, and getting it wrong is the recurring "scope drift on TAM"
bug. It lived inside a try/except in the orchestrator, which meant adding a sixth sizing
method required editing the orchestrator, and the person most qualified to add one is the
person who should never have to open that file.

It is a FUNCTION RETURNING A DECISION, not a dispatcher. The route says which registered
method to call, with what arguments, and what to tell the reader if the answer is a
downgrade. The orchestrator resolves the name through SKILL_REGISTRY and calls it. That
split is what makes the decision testable on its own: every case below can be asserted
without a geocoder, a network or a report.

TO ADD A SIZING METHOD: write it in this package with @skill(produces="market_sizing"),
register it in __init__.py, and add the branch here that routes to it. Nothing outside
skills/sizing/ changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

#: The geocoder levels that mean "not a single site". A neighbourhood name geocodes to
#: points kilometres apart, so it cannot stand in for a street address; a city is wider
#: still. Both get the city-scale scan rather than a 1.5 km ring around an arbitrary pin.
_NOT_A_SITE = ("city", "region", "zip")


@dataclass(frozen=True)
class Route:
    """One routing decision: the method, its arguments, and the reason if it is a downgrade.

    `downgrade` is not cosmetic. D52 exists to tell a REASONED reroute from a silent
    substitution, and it can only do that if the reroute says so in the result.
    """
    skill: str
    kwargs: dict = field(default_factory=dict)
    downgrade: Optional[str] = None


def route_physical_sizing(*, location: str, category: str, n_locations: Optional[int],
                          geo_level: Optional[str], osm_key: Optional[str],
                          osm_value: Optional[str], radius_m: int) -> Route:
    """Pick the sizing method for a venture that serves people at a premise.

    The order matters and is not arbitrary:

    1. A MULTI-SITE ROLLOUT keeps its own engine. size_regional composes per-site
       trade areas; sizing a chain as one site published a three-location chain's single
       trade area as its whole market.
    2. A LOCATION THAT IS NOT A SITE gets the city-scale scan. This is the geocoder's
       matched level, never the classifier's word and never a regex over the brief:
       no text predicate can tell "Silver Lake" from "Boise", and the geocoder can.
    3. OTHERWISE the trade-area model, which degrades honestly on its own when the
       address turns out to be thin.
    """
    if n_locations and n_locations > 1:
        return Route("size_regional", {
            "representative_address": location,
            "planned_locations": n_locations,
            "category": category,
            "osm_value": osm_value,
            "radius_m": radius_m,
        })

    if geo_level in _NOT_A_SITE:
        return Route("size_citywide", {
            "place": location,
            "category": category,
            "osm_value": osm_value,
            "osm_key": osm_key,
        }, downgrade=(f"the location {location!r} geocodes at {geo_level} level, not a "
                      f"site — city-scale scan ran instead of the trade-area model"))

    return Route("size_hyperlocal", {
        "address": location,
        "category": category,
        "osm_value": osm_value,
        "osm_key": osm_key,
        "radius_m": radius_m,
    })
