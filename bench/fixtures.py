"""bench/fixtures.py -- one plausible argument for every parameter name in the repo.

A bench that can only run the capabilities somebody remembered to write a case for is a
bench that goes stale silently. So arguments are resolved by PARAMETER NAME, not by
capability name: `domain` is a domain wherever it appears, and the nine tools taking one
are covered by a single line. A capability added tomorrow whose signature reuses the
existing vocabulary is exercised the day it lands, with no edit here.

The 63 registered capabilities currently require 26 distinct parameter names between
them, and all 26 are below. A name that is NOT below is not silently skipped: `args_for`
returns it in `missing`, `smoke` marks that capability `no-fixture`, and `bench doctor`
lists them. An uncallable capability is a finding, not an absence.

TWO KINDS OF ENTRY, and they are separate on purpose:

  REQUIRED (`BY_PARAM`)  what a parameter with no default must be given.
  BUDGET   (`BUDGET`)    optional parameters bench deliberately shrinks. `limit=5`
                         instead of 25, `max_steps=2` instead of 6. A smoke run is
                         asking "does this still work", not "how good is the answer",
                         and the difference is minutes and tokens. Every run prints
                         the arguments it used, so a shrunk budget is never invisible.

The values are real and stable. Real, because a tool given "foo" tests the error path
rather than the tool. Stable, because a FIXED prompt is what makes the llm cache answer
a second bench run for nothing -- see bench/llm_gate.py.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable

# A venture the whole repo is already shaped around: a physical, local, single-site
# food business, which is the case most of the sizing and geo surface is written for.
VENTURE = "a specialty coffee shop in Austin, Texas"
BRAND = "Blue Bottle Coffee"
DOMAIN = "bluebottlecoffee.com"
ADDRESS = "1200 W 6th St, Austin, TX 78703"
LAT, LNG = 30.2711, -97.7437          # Austin, TX
STATE_FIPS, COUNTY_FIPS = "48", "453"  # Travis County, Texas
NAICS = "722515"                       # snack and nonalcoholic beverage bars

# A page small enough to read in a failure message and rich enough to exercise both
# parsers: extract_structured wants the JSON-LD block, extract_prices wants the money.
HTML = (
    '<html><head><title>Pricing</title>'
    '<script type="application/ld+json">'
    '{"@context":"https://schema.org","@type":"Organization","name":"Acme Roasters",'
    '"url":"https://example.com"}'
    '</script></head><body>'
    '<h1>Plans</h1>'
    '<div class="tier"><span itemprop="price">$29</span> per month</div>'
    '<div class="tier"><span itemprop="price">$79</span> per month</div>'
    '<div class="tier">$249/mo</div>'
    '</body></html>'
)

SEARCH_HITS = [
    {"url": "https://bluebottlecoffee.com/", "title": "Blue Bottle Coffee",
     "snippet": "Specialty coffee roaster and cafes."},
    {"url": "https://www.reddit.com/r/Coffee/comments/abc/",
     "title": "Best pour over?", "snippet": "A thread about coffee."},
    {"url": "https://www.g2.com/products/coffee", "title": "Coffee on G2",
     "snippet": "An aggregator page."},
]

COMPETITORS = [
    {"brand": "Blue Bottle Coffee", "domain": "bluebottlecoffee.com", "relevance": "high"},
    {"brand": "Stumptown Coffee", "domain": "stumptowncoffee.com", "relevance": "high"},
]

# The four keys estimate_market_size actually reads off a profile.
PROFILE = {
    "name": "Cardinal Coffee",
    "summary": "A single-site specialty coffee shop and micro-roastery in Austin.",
    "business_model": "retail food and beverage, walk-in",
    "apparent_target_customer": "downtown office workers and weekend regulars",
}

# Ordered SOM < SAM < TAM, every figure sourced: a payload validate_numbers should PASS.
# A fixture that fails its own gate would make every run look broken.
SIZING = {
    "tam_usd": 1_000_000_000,
    "sam_usd": 120_000_000,
    "som_usd": 2_400_000,
    "figures": [
        {"label": "TAM", "value_usd": 1_000_000_000, "source": "Census CBP 2022",
         "formula": "2,000,000 households x $500/yr"},
        {"label": "SAM", "value_usd": 120_000_000, "source": "ACS 5-year 2022",
         "formula": "$1,000,000,000 x 12%"},
        {"label": "SOM", "value_usd": 2_400_000, "source": "trade-area capacity",
         "formula": "$120,000,000 x 2%"},
    ],
}

# Two INDEPENDENT origins, because same-origin estimates collapse to single_source and
# the interesting path through triangulate is the cross-origin one.
ESTIMATES = [
    {"value": 2_400_000.0, "source": "Census CBP establishment count x ARPU",
     "method": "bottom_up", "origin": "census"},
    {"value": 2_900_000.0, "source": "scraped competitor pricing x catchment",
     "method": "top_down", "origin": "scrape"},
]


def _worker_evidence() -> dict:
    """What run_research_crew hands synthesis_agent: worker name -> Evidence.

    Built here rather than imported from a run, so the synthesis path can be exercised
    with no workers dispatched -- which is the whole point of testing one agent alone.
    """
    from core import Evidence
    return {
        "market_scan_agent": Evidence(
            source="market_scan_agent", category="market_scan", count=2,
            payload={"answer": "Two established specialty roasters operate downtown; "
                                "neither runs a subscription.",
                     "competitors": COMPETITORS}),
        "demand_signal_agent": Evidence(
            source="demand_signal_agent", category="demand_signal", count=1,
            payload={"answer": "Search interest for 'pour over Austin' is flat "
                                "year over year."}),
    }


# --------------------------------------------------------------------------- required
# Parameter name -> the value bench passes for it. Callables are evaluated per run so a
# fixture can build objects (Evidence) without importing the domain at module import.
BY_PARAM: dict[str, Any] = {
    "address": ADDRESS,
    "annual_arpu": 1188.0,               # $99/mo, the docstring's own example
    "brand": BRAND,
    "category": "coffee shop",
    "competitors": COMPETITORS,
    "county_fips": COUNTY_FIPS,
    "description": VENTURE,
    "domain": DOMAIN,
    "estimates": ESTIMATES,
    "handle": "bluebottlecoffee",
    "hits": SEARCH_HITS,
    "html": HTML,
    "label": "SOM",
    "lat": LAT,
    "lng": LNG,
    "naics": NAICS,
    # A city, not a street: size_citywide is the engine for a founder who has picked a
    # city but not a site, so a full address would defeat what it is for.
    "place": "Austin, Texas",
    "payload": SIZING,
    "profile": PROFILE,
    "query": "specialty coffee shop Austin",
    "section_name": "market_sizing",
    "sizing": SIZING,
    "state_fips": STATE_FIPS,
    "url": f"https://{DOMAIN}/",
    "worker_evidence": _worker_evidence,
}

# ----------------------------------------------------------------------------- budget
# Optional parameters bench shrinks when the signature has them. These are ceilings on
# work, never on correctness: a tool that returns 5 rows proves the same plumbing as one
# that returns 25, in a fifth of the requests.
BUDGET: dict[str, Any] = {
    "limit": 5,
    "max_candidates": 5,
    "max_pages": 1,
    "max_steps": 2,
    "max_strategies": 2,
    "max_to_enrich": 2,
    "max_workers": 2,
    "n_perspectives": 2,
}

# ------------------------------------------------------------------------- by surface
# Parameter name, narrowed by the capability's category. A `query` means something
# different to a developer community than it does to a maps API: asking Hacker News
# about "specialty coffee shop Austin" returns nothing, and an `empty` verdict that only
# means "wrong site for that question" cannot tell a working parser from a broken one.
# So the customer-voice surface is asked something its sources actually discuss, while
# staying on the venture's own subject.
BY_LABEL: dict[str, dict[str, Any]] = {
    "customer_voice": {"query": "coffee"},
}

# ---------------------------------------------------------------------- per-capability
# The narrow cases where the parameter NAME is not enough. Each one says why.
OVERRIDES: dict[str, dict] = {
    # "coffee shop" is a place, not a subscription. scrape_market_price only keeps
    # recurring $5-$5,000/month values, so a category with no monthly pricing pages
    # would skeleton for a reason that has nothing to do with the scraper working.
    "scrape_market_price": {"category": "crm software"},
    # The category here selects which trade publications to search, not a business type.
    "vertical_publication_mentions": {"category": "coffee"},
    # naics is Optional in the signature but the skill requires category OR naics, and
    # skeletons without one. Passing it exercises the live Census count instead.
    "grounded_bottom_up": {"naics": NAICS},
    # The default 'llm' mode is the real path and the one worth testing; named here so
    # it is a decision on the record rather than a default nobody looked at.
    "narrate_section": {"mode": "llm"},
    # Both parameters are Optional in the signature, but the body needs ONE of them and
    # returns a named skeleton without either. Passing the NAICS code rather than the
    # category also keeps the live Census path under test instead of the LLM resolver.
    "census_business_counts": {"naics": NAICS},
    # Same shape: category or series_id, neither declared required. The category goes
    # through the LLM series resolver, which is the path worth exercising here.
    "bls_cex_spend": {"category": "food away from home"},
    # And again: addresses or representative_address, both Optional, one required. Two
    # sites rather than one, because the rollout composition is what this skill does
    # that size_hyperlocal does not, and one address would not exercise it.
    "size_regional": {"addresses": [ADDRESS, "500 Congress Ave, Austin, TX 78701"],
                      "planned_locations": 2},
}


def _unwrap(fn: Callable) -> Callable:
    """The undecorated function, whose signature is the real one."""
    return getattr(fn, "__wrapped_fn__", None) or fn


def _signature(fn: Callable) -> inspect.Signature:
    """The signature with annotations resolved. eval_str matters: every module here uses
    `from __future__ import annotations`, so without it every annotation is a string."""
    raw = _unwrap(fn)
    try:
        return inspect.signature(raw, eval_str=True)
    except (TypeError, ValueError, NameError):
        return inspect.signature(raw)


def _resolve(value: Any) -> Any:
    """Evaluate a callable fixture; pass anything else through."""
    return value() if callable(value) else value


def args_for(name: str, fn: Callable, extra: dict | None = None,
             label: str = "") -> tuple[dict, list[str]]:
    """Arguments bench would call `fn` with, and the required names it has none for.

    Returns (kwargs, missing). `missing` non-empty means the capability cannot be
    exercised -- the caller reports that as `no-fixture` rather than calling anyway and
    reading the gateway's refusal as a failure of the capability.

    Precedence, lowest first: budget defaults, parameter-name fixtures, the capability's
    surface (BY_LABEL), per-capability OVERRIDES, then `extra` from the command line.
    The command line always wins.
    """
    overrides = dict(OVERRIDES.get(name) or {})
    by_label = dict(BY_LABEL.get(label) or {})
    supplied = dict(extra or {})
    params = _signature(fn).parameters

    kwargs: dict[str, Any] = {}
    missing: list[str] = []

    for pname, p in params.items():
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if pname in supplied:
            kwargs[pname] = supplied[pname]
            continue
        if pname in overrides:
            kwargs[pname] = overrides[pname]
            continue
        required = p.default is inspect.Parameter.empty
        if required:
            if pname in by_label:
                kwargs[pname] = _resolve(by_label[pname])
            elif pname in BY_PARAM:
                kwargs[pname] = _resolve(BY_PARAM[pname])
            else:
                missing.append(pname)
            continue
        if pname in BUDGET:
            kwargs[pname] = BUDGET[pname]

    # An argument the signature does not accept is a caller error the gateway would
    # refuse; surfacing it here names it as a bench problem instead.
    unknown = [k for k in supplied if k not in params]
    if unknown and not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        missing.extend(f"(not a parameter: {k})" for k in unknown)

    return kwargs, missing
