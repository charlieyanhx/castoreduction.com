"""
tools/econ.py — authoritative economic inputs (C1 remediation).

`bls_cex_spend` returns annual spend per consumer unit (household) for a category
from the **BLS Consumer Expenditure Survey via the BLS Public Data API** — a real
source, not an LLM guess. The LLM only maps category → CEX series id; the *number*
comes from BLS. This restores SIZING.md invariant #1 ("the LLM never invents a
[sourced] number") for the spend input that drives hyperlocal TAM.

Degrades gracefully: if the series can't be resolved or BLS is unavailable, returns
Evidence(skeleton=True) — the caller then falls back to a clearly-labeled estimate
rather than presenting a guess as sourced.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from .registry import tool, Evidence

_BLS_API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# Optional config cache (category → CEX series id). Empty by default; resolution is
# generic via the LLM. NOT a hardcoded spend table — only a series-id memo.
_CEX_SERIES_CACHE: dict[str, str] = {}


def _resolve_cex_series(category: str) -> Optional[str]:
    """LLM maps a category to a BLS CEX 'mean annual expenditure' series id (CXU…).
    Returns a series id string or None. The dollar value is NOT asked of the LLM."""
    if not category:
        return None
    key = category.lower().strip()
    if key in _CEX_SERIES_CACHE:
        return _CEX_SERIES_CACHE[key]
    try:
        from llm import call_json
        raw = call_json(
            system=("Map the spending category to its BLS Consumer Expenditure Survey "
                    "mean-annual-expenditure series id (format CXU...LB0101M). Reply "
                    "ONLY JSON: {\"series_id\": \"...\"}. If unsure, null."),
            user=f"Category: {category}",
            max_tokens=60,
        ) or {}
        sid = str(raw.get("series_id") or "").strip()
        if sid.upper().startswith("CXU"):
            _CEX_SERIES_CACHE[key] = sid
            return sid
        return None
    except Exception:
        return None


@tool(category="econ", returns="{annual_usd, series_id, source}")
def bls_cex_spend(category: Optional[str] = None,
                  series_id: Optional[str] = None) -> Evidence:
    """Annual household spend ($/yr) for a category from BLS CEX — a real source.

    Pass `series_id` directly, or a `category` (LLM resolves the CEX series id; the
    value still comes from BLS). Optional BLS_API_KEY env raises rate limits.
    """
    sid = series_id or _resolve_cex_series(category or "")
    if not sid:
        return Evidence(source="bls_cex_spend", category="econ", count=0, skeleton=True,
                        error=f"could not resolve a BLS CEX series for {category!r}")
    from scrape.http import request
    payload = {"seriesid": [sid], "latest": "true"}
    key = os.getenv("BLS_API_KEY")
    if key:
        payload["registrationkey"] = key
    resp = request("POST", _BLS_API, json=payload, timeout=12)
    if resp is None or getattr(resp, "status_code", 500) >= 400:
        return Evidence(source="bls_cex_spend", category="econ", count=0, skeleton=True,
                        error=f"BLS API unavailable for {sid}")
    try:
        data = resp.json()
        series = (data.get("Results") or {}).get("series") or []
        rows = series[0].get("data") if series else []
        val = float(str(rows[0]["value"]).replace(",", "")) if rows else None
    except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError):
        val = None
    if val is None or val <= 0:
        return Evidence(source="bls_cex_spend", category="econ", count=0, skeleton=True,
                        error=f"BLS returned no usable value for {sid}")
    return Evidence(
        source="bls_cex_spend", category="econ", count=1,
        payload={"annual_usd": val, "series_id": sid,
                 "source": f"BLS Consumer Expenditure Survey (series {sid})"},
        cost_meta={"annual_usd": val, "series_id": sid, "source": "BLS CEX"},
    )
