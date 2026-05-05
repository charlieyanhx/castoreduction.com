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

# Trigger registration of all tool modules
from . import customer_voice  # noqa: F401  — side-effect: registers tools
from . import firmographic    # noqa: F401  — side-effect: registers tools

__all__ = [
    "Evidence", "ToolMeta", "TOOL_REGISTRY", "tool",
    "list_tools", "categories", "get_tool", "describe_tool", "describe_all",
]
