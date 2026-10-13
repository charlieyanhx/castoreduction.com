"""treadle/registry.py — which report types exist, and how a pack joins.

Packs are imported BY NAME, on request, never discovered by the engine at import time.
`import treadle` must stay cheap and must not load a domain: the moment the engine
imports a pack eagerly, it can no longer be used without that pack, which is the defect
core/ was extracted to end.
"""
from __future__ import annotations

import importlib

from core.registry import DuplicateRegistration, Registry

from .spec import ReportType

REPORT_TYPES: Registry[ReportType] = Registry("report type")

#: Where packs live unless a dotted path says otherwise.
PACKS_PACKAGE = "packs"


def register(report_type: ReportType) -> ReportType:
    """Add a report type. A key registered twice raises DuplicateRegistration (a
    ValueError): two definitions of one report means the winner depends on import order."""
    return REPORT_TYPES.register(report_type.key, report_type)


def get(key: str) -> ReportType:
    try:
        return REPORT_TYPES[key]
    except KeyError:
        known = ", ".join(sorted(REPORT_TYPES)) or "none loaded"
        raise KeyError(f"no report type {key!r} (known: {known})") from None


def load_pack(name: str) -> None:
    """Import a pack so it registers itself. `name` is a pack under packs/ or a dotted
    module path. Loading the same pack twice is a no-op, not a duplicate: Python imports a
    module once, and the registration lives at its module scope."""
    importlib.import_module(name if "." in name else f"{PACKS_PACKAGE}.{name}")


__all__ = ["REPORT_TYPES", "DuplicateRegistration", "register", "get", "load_pack"]
