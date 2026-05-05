"""
config/ — declarative pipeline configuration (Phase 4 of cycle32 migration).

Configuration values that previously lived as magic numbers scattered across
pipeline modules (max_tokens, panel sizes, retry counts, etc.) now live as
JSON/YAML so they can be tuned without touching code.

Public API:
    from config import get, get_all, profile_name

    # Read a single setting:
    n = get("max_diff.panel_size", default=30)

    # Read a whole namespace:
    psm_settings = get("psm")  # → {"panel_size": 40, ...}

    # Switch active profile:
    PIPELINE_PROFILE=quick python -m benchmarks.run_all
"""
from .loader import (
    get,
    get_all,
    profile_name,
    available_profiles,
    reload_config,
)

__all__ = ["get", "get_all", "profile_name", "available_profiles", "reload_config"]
