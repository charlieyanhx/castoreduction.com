"""
config/loader.py — load + cache pipeline config from YAML profiles.

Profile selection (in priority order):
  1. PIPELINE_PROFILE env var ('default' | 'quick' | 'deep')
  2. Falls back to 'default'

Profile files live in config/profiles/{name}.yaml.

Lookups use dotted-path syntax:
    get("max_diff.panel_size", default=30)
    get("differentiators.per_dimension_max_tokens", default=1500)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml

from logger import get as get_logger

log = get_logger("config")

PROFILES_DIR = Path(__file__).parent / "profiles"
_CACHE: dict[str, Any] = {}
_CACHED_PROFILE: Optional[str] = None


def profile_name() -> str:
    """The currently-active profile name."""
    return os.environ.get("PIPELINE_PROFILE", "default").strip() or "default"


def available_profiles() -> list[str]:
    """List of all profile YAML files present."""
    if not PROFILES_DIR.exists():
        return []
    return sorted(p.stem for p in PROFILES_DIR.glob("*.yaml"))


def _load() -> dict[str, Any]:
    """Load the active profile's YAML once and cache it."""
    global _CACHED_PROFILE
    name = profile_name()
    if _CACHED_PROFILE == name and _CACHE:
        return _CACHE
    path = PROFILES_DIR / f"{name}.yaml"
    if not path.exists():
        log.warning("[config] profile '%s' not found at %s, using empty config", name, path)
        _CACHE.clear()
        _CACHED_PROFILE = name
        return _CACHE
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        log.warning("[config] failed to load %s: %s", path, e)
        data = {}
    _CACHE.clear()
    _CACHE.update(data)
    _CACHED_PROFILE = name
    log.info("[config] loaded profile '%s' from %s", name, path.name)
    return _CACHE


def reload_config() -> None:
    """Clear cache; next get() call will re-read disk. For tests + dev."""
    _CACHE.clear()
    global _CACHED_PROFILE
    _CACHED_PROFILE = None


def get(path: str, default: Any = None) -> Any:
    """Fetch a config value by dotted path. Returns `default` if missing."""
    data = _load()
    cur: Any = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def get_all() -> dict[str, Any]:
    """Return a copy of the full active profile (for debugging / introspection)."""
    return dict(_load())
