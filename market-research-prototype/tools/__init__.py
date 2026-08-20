"""
tools/ — auto-discoverable capability primitives (Phase 1).

Each tool is a registered function with a uniform Evidence return shape.
Importing this package triggers all tool modules to register themselves.

Usage:
    from tools import Evidence, tool, list_tools, get_tool

    # As a regular function:
    e = hackernews_mentions("Stripe", limit=10)
    print(e.count, e.duration_s, e.payload[:3])

    # As a registry consumer:
    for t in list_tools(category="customer_voice"):
        print(t.name, t.signature)

    # Agent-style invocation:
    result = get_tool("hackernews_mentions").fn("Stripe", limit=10)
"""
from .registry import (
    Evidence,
    ToolMeta,
    TOOL_REGISTRY,
    tool,
    list_tools,
    categories,
    get_tool,
    describe_tool,
    describe_all,
)

# Trigger registration of all tool modules (cycle32 round 2: now consistent —
# every external-touching primitive is a registered tool)
from . import customer_voice  # noqa: F401  — voice scrapers
from . import firmographic    # noqa: F401  — Wikidata/GitHub enrich
from . import scrape          # noqa: F401  — low-level scrape utilities (search, structured, wayback)
from . import trend           # noqa: F401  — Google Trends, Wayback activity, Trustpilot momentum
from . import domain          # noqa: F401  — domain validation, parking detection, age
from . import social          # noqa: F401  — Instagram signals
from . import ads             # noqa: F401  — Meta Ad Library
from . import geo             # noqa: F401  — Census geocoder/ACS + OSM (trade-area sizing)
from . import econ            # noqa: F401  — BLS CEX spend (authoritative per-household spend)
from . import overture        # noqa: F401  — Overture Maps places (cuisine-grade venue census)
from . import gmaps_reviews   # noqa: F401  — Google Maps ratings via gosom subprocess

__all__ = [
    "Evidence", "ToolMeta", "TOOL_REGISTRY", "tool",
    "list_tools", "categories", "get_tool", "describe_tool", "describe_all",
]
