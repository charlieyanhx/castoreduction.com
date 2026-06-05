"""
skills/sizing/classify.py — market-scale classifier (Layer 3 keystone).

The single most important routing decision in the numbers-right engine: WHICH
sizing method applies. Get this wrong and a neighborhood cafe gets sized like a
SaaS startup (or vice versa) — the recurring "scope drift on TAM" bug.

Design: the LLM does only EXTRACTION (pull structured signals from free text);
the routing is DETERMINISTIC rules over those signals. This keeps the decision
auditable and testable — no number, and no method choice, is left to LLM whim.

Signals extracted:
  is_physical : does serving a customer require a physical premise / footfall?
  geo_scope   : single_site | local_metro | regional | national | global
  delivery    : in_person | local_delivery | online | hybrid

Scales (→ sizing method, → which sizing skill handles it):
  hyperlocal        → trade_area_catchment      → size_hyperlocal
  regional          → per_location_rollout      → size_regional
  national_physical → per_location_rollout      → size_regional
  national_digital  → topdown_bottomup_digital  → size_national_digital
  global_digital    → topdown_bottomup_digital  → size_national_digital
"""
from __future__ import annotations

import re

from llm import call_json
from skills.registry import skill
from tools import Evidence

# Deterministic multi-location detection — an explicit count or chain/franchise
# language means a physical venture is at least regional, regardless of what the
# LLM extracted (caught by the Manus benchmark: "8 studios across Austin" was
# mis-routed to hyperlocal).
_COUNT_RE = re.compile(
    r"\b(\d+)\s+(locations?|studios?|stores?|outlets?|branches|sites?|"
    r"restaurants?|gyms?|shops?|clinics?|cafes?|salons?)\b", re.I)
_CHAIN_RE = re.compile(r"\b(chain|franchise|multi-?location|multi-?site|nationwide)\b", re.I)


def _is_multi_location(description: str) -> bool:
    m = _COUNT_RE.search(description or "")
    if m:
        try:
            if int(m.group(1)) >= 2:
                return True
        except ValueError:
            pass
    return bool(_CHAIN_RE.search(description or ""))

# Scale constants.
HYPERLOCAL = "hyperlocal"
REGIONAL = "regional"
NATIONAL_PHYSICAL = "national_physical"
NATIONAL_DIGITAL = "national_digital"
GLOBAL_DIGITAL = "global_digital"

# Method → the sizing skill that will execute it.
METHOD_FOR_SCALE = {
    HYPERLOCAL: ("trade_area_catchment", "size_hyperlocal"),
    REGIONAL: ("per_location_rollout", "size_regional"),
    NATIONAL_PHYSICAL: ("per_location_rollout", "size_regional"),
    NATIONAL_DIGITAL: ("topdown_bottomup_digital", "size_national_digital"),
    GLOBAL_DIGITAL: ("topdown_bottomup_digital", "size_national_digital"),
}

_VALID_GEO = {"single_site", "local_metro", "regional", "national", "global"}
_VALID_DELIVERY = {"in_person", "local_delivery", "online", "hybrid"}

_EXTRACT_SYSTEM = (
    "You classify a business by how its market should be sized. Return ONLY JSON "
    "with exactly these keys: is_physical (boolean — does serving a customer "
    "require a physical premise or in-person footfall?), geo_scope (one of: "
    "single_site, local_metro, regional, national, global), delivery (one of: "
    "in_person, local_delivery, online, hybrid). No prose."
)


def _extract_signals(description: str, geo: str) -> dict:
    """LLM extraction of sizing signals. Defensive defaults if the call degrades."""
    raw = call_json(
        system=_EXTRACT_SYSTEM,
        user=f"Market geography hint: {geo}\n\nBUSINESS:\n{description}",
        max_tokens=300,
    ) or {}
    geo_scope = str(raw.get("geo_scope", "")).strip().lower()
    delivery = str(raw.get("delivery", "")).strip().lower()
    return {
        "is_physical": bool(raw.get("is_physical", False)),
        "geo_scope": geo_scope if geo_scope in _VALID_GEO else "national",
        "delivery": delivery if delivery in _VALID_DELIVERY else "online",
    }


def _route(signals: dict) -> tuple[str, str, str, str]:
    """Deterministic signals → (scale, method, sizing_skill, rationale). Pure."""
    physical = signals["is_physical"] or signals["delivery"] in ("in_person", "local_delivery")
    scope = signals["geo_scope"]

    if physical:
        if scope in ("single_site", "local_metro"):
            scale, why = HYPERLOCAL, "physical premise serving a local trade area"
        elif scope == "regional":
            scale, why = REGIONAL, "physical footprint rolled out across a region"
        else:  # national / global physical
            scale, why = NATIONAL_PHYSICAL, "physical footprint at national scale"
    else:
        scale = GLOBAL_DIGITAL if scope == "global" else NATIONAL_DIGITAL
        why = "digital/online delivery, geography-agnostic within its market"

    method, sizing_skill = METHOD_FOR_SCALE[scale]
    return scale, method, sizing_skill, why


@skill(produces="market_scale", consumes=["company_profile"])
def classify_market_scale(description: str, geo: str = "US") -> Evidence:
    """Classify a venture's market scale and route it to the right sizing method.

    Returns Evidence(produces="market_scale") with payload:
      {scale, sizing_method, sizing_skill, signals, rationale}
    No market figures are produced here — this only decides HOW the downstream
    sizing skill will compute them.
    """
    signals = _extract_signals(description, geo)

    # Deterministic correction: explicit multi-location language upgrades a
    # physical venture from a single trade area to a regional rollout.
    physical = signals["is_physical"] or signals["delivery"] in ("in_person", "local_delivery")
    if physical and signals["geo_scope"] in ("single_site", "local_metro") \
            and _is_multi_location(description):
        signals = {**signals, "geo_scope": "regional"}  # immutable update

    scale, method, sizing_skill, rationale = _route(signals)
    return Evidence(
        source="classify_market_scale",
        category="skill_output",
        count=1,
        payload={
            "scale": scale,
            "sizing_method": method,
            "sizing_skill": sizing_skill,
            "signals": signals,
            "rationale": rationale,
        },
        cost_meta={"scale": scale, "sizing_method": method},
    )
