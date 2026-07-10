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


# Deterministic physical-local detector — an obvious bricks-and-mortar venue WITH a
# location and NO digital framing. Used to override the classifier when the LLM
# degrades (rate-limited → unsafe national/online defaults), which otherwise mis-routes
# a "pizzeria in Echo Park" to national_digital and skips the trade-area path.
_VENUE_RE = re.compile(
    r"\b(restaurant|pizzeria|pizza|cafe|café|coffee\s?shop|bar|pub|brewery|gym|"
    r"fitness|yoga|pilates|salon|barber|spa|clinic|dental|dentist|store|shop|"
    r"boutique|bakery|deli|diner|grocery|food\s?truck|studio|gallery|nail\s?salon)s?\b",
    re.I)
# Location: a street address, OR "in <Capitalized place>" (excluding country-level).
_LOC_RE = re.compile(
    r"\b\d{1,6}\s+[A-Z][\w.]*\s+(st|street|ave|avenue|blvd|boulevard|rd|road|dr|"
    r"drive|way|ln|lane|pl|place)\b|\bin\s+[A-Z][a-zA-Z]+", re.I)
_COUNTRY_RE = re.compile(r"\bin\s+(the\s+)?(us|u\.s\.|usa|america|canada|uk|europe)\b", re.I)
_DIGITAL_RE = re.compile(
    r"\b(saas|software|app|platform|online|api|web\s?app|mobile\s?app|marketplace|"
    r"b2b|b2c|e-?commerce|website|cloud)\b", re.I)


def _is_physical_local(description: str) -> bool:
    """True for an obvious single physical local venue with a location (no digital framing)."""
    d = description or ""
    if _DIGITAL_RE.search(d):
        return False                      # a SaaS *for* restaurants is not a restaurant
    if not _VENUE_RE.search(d):
        return False
    # Needs a real location signal that isn't merely country-level ("in the US").
    loc = _LOC_RE.search(d)
    if not loc:
        return False
    return not _COUNTRY_RE.search(d) or "," in d  # "in US" alone doesn't count

# Deterministic client-services detector (G4/D07) — client-serving professional
# services (agencies, consultancies, firms, design/dev studios paid per project or
# retainer) are NOT footfall businesses: revenue arrives from client engagements, not
# walk-in trade, so the household trade-area path NEVER applies — even when a city is
# named ("design studio in Portland" would otherwise satisfy _VENUE_RE + _LOC_RE).
# The audit critical: a $20k/project brand-design studio was routed hyperlocal, where
# D07 (geo competitors) is unsatisfiable for an agency. Requires BOTH a provider noun
# and a client/engagement signal, so "yoga studio for local clients" stays hyperlocal.
_SERVICES_PROVIDER_RE = re.compile(
    r"\b(agenc(?:y|ies)|consultanc(?:y|ies)|consulting|"
    r"(?:design|creative|branding|brand|dev(?:elopment)?|software|product)\s+(?:studio|firm|shop|house)|"
    r"(?:marketing|law|accounting|architecture|pr|staffing|recruiting)\s+(?:firm|agency|practice)|"
    r"professional\s+services?)\b", re.I)
_CLIENT_ENGAGEMENT_RE = re.compile(
    r"\b(clients?|businesses|companies|startups|brands|b2b|engagements?|"
    r"project-?based|per[- ]project|retainers?)\b", re.I)


def _is_client_services(description: str) -> bool:
    """True for a client-serving professional-services venture (provider noun AND
    client/engagement context) — a business sized on engagements, not footfall."""
    d = description or ""
    return bool(_SERVICES_PROVIDER_RE.search(d) and _CLIENT_ENGAGEMENT_RE.search(d))


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

    Do NOT use when you actually want the figures — call size_market, which
    runs this classifier and then executes the routed engine; picking a
    per-scale engine yourself skips the deterministic override rules applied
    here (client-services, physical-local, multi-location).
    """
    signals = _extract_signals(description, geo)

    # Deterministic override #0 (G4/D07): client-serving professional services never
    # route to the household trade-area path — regardless of the LLM's signals, and
    # taking precedence over the physical-local override ("design studio in Portland"
    # is an engagement business, not a walk-in venue).
    client_services = _is_client_services(description)
    if client_services:
        signals = {**signals, "is_physical": False,
                   "geo_scope": signals["geo_scope"]
                   if signals["geo_scope"] in ("national", "global") else "national",
                   "delivery": "online"
                   if signals["delivery"] in ("in_person", "local_delivery")
                   else signals["delivery"]}

    # Deterministic override #1: an obvious physical local venue (with a location, no
    # digital framing) is physical + local — even if the LLM degraded to national/online
    # defaults. This is the fix for "pizzeria in Echo Park → national_digital → 0 competitors".
    elif _is_physical_local(description):
        scope = "regional" if _is_multi_location(description) else "local_metro"
        signals = {**signals, "is_physical": True,
                   "delivery": signals["delivery"]
                   if signals["delivery"] in ("in_person", "local_delivery") else "in_person",
                   "geo_scope": scope}

    # Deterministic correction #2: explicit multi-location language upgrades a
    # physical venture from a single trade area to a regional rollout.
    physical = signals["is_physical"] or signals["delivery"] in ("in_person", "local_delivery")
    if physical and signals["geo_scope"] in ("single_site", "local_metro") \
            and _is_multi_location(description):
        signals = {**signals, "geo_scope": "regional"}  # immutable update

    scale, method, sizing_skill, rationale = _route(signals)
    if client_services:
        rationale = ("client-serving professional services: project/retainer revenue "
                     "from business clients, not walk-in trade — household trade-area "
                     "sizing never applies")
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
