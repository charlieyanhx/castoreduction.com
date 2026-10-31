"""treadle/defaults.py — the one place the engine borrows from its host by default.

The agent loop and the gateway both take `registry=`. When a caller passes none, inside
Castor the tool set is Castor's market-research tools, resolved at CALL time so that
importing the engine never loads them. Outside Castor there is no default, and the caller
is told what to pass instead of meeting an unexplained ModuleNotFoundError.

One function rather than an import at each call site, so the engine's reach into its host
is a single line to find, and a single line to remove when Castor passes its registry in
explicitly.
"""
from __future__ import annotations

from treadle.core import Registry

#: The host package whose tool registry is the default inside Castor.
HOST_TOOLS = "tools"


def default_tool_registry() -> Registry:
    try:
        from tools import TOOL_REGISTRY
    except ModuleNotFoundError as e:
        # Only the host being ABSENT is answered here. A host whose own import is broken
        # (e.name is one of ITS dependencies) is a real failure and must surface as one.
        if e.name != HOST_TOOLS:
            raise
        raise LookupError(
            "no default tool registry outside Castor: pass registry= with the tools "
            "this run may use") from None
    return TOOL_REGISTRY
