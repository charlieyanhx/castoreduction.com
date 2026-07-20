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

from pydantic import BaseModel, model_validator
from .registry import tool, Evidence


class BlsCexSpendArgs(BaseModel):
    category: Optional[str] = None
    series_id: Optional[str] = None

    @model_validator(mode="after")
    def at_least_one(self) -> "BlsCexSpendArgs":
        if not self.category and not self.series_id:
            raise ValueError("at least one of 'category' or 'series_id' must be provided")
        return self

_BLS_API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# Optional config cache (category → CEX series id). Empty by default; resolution is
# generic via the LLM. NOT a hardcoded spend table — only a series-id memo.
_CEX_SERIES_CACHE: dict[str, str] = {}

# Curated category → REAL, API-VERIFIED BLS CEX mean-annual-expenditure series id (all consumer
# units). This is config (which real series to read), NOT a hardcoded value — the dollar amount is
# always fetched live from BLS. The LLM cannot reliably produce valid CXU ids, so for cleanly-
# mappable categories we use the verified id directly; only unmapped categories fall to the LLM.
# Longest substring match wins. (Verified live: each id returns a 2023 value from the BLS API.)
_CEX_SERIES_CURATED = {
    "restaurant": "CXUFOODAWAYLB0101M", "eatery": "CXUFOODAWAYLB0101M",
    "diner": "CXUFOODAWAYLB0101M", "food truck": "CXUFOODAWAYLB0101M",
    "fast food": "CXUFOODAWAYLB0101M", "fast-casual": "CXUFOODAWAYLB0101M",
    "food away": "CXUFOODAWAYLB0101M", "dining": "CXUFOODAWAYLB0101M",
    "grocery": "CXUFOODHOMELB0101M", "supermarket": "CXUFOODHOMELB0101M",
    "bar": "CXUALCBEVGLB0101M", "pub": "CXUALCBEVGLB0101M", "brewery": "CXUALCBEVGLB0101M",
    "wine": "CXUALCBEVGLB0101M", "alcohol": "CXUALCBEVGLB0101M",
    "salon": "CXUPERSCARELB0101M", "barber": "CXUPERSCARELB0101M", "spa": "CXUPERSCARELB0101M",
    "nail": "CXUPERSCARELB0101M", "beauty": "CXUPERSCARELB0101M", "personal care": "CXUPERSCARELB0101M",
    "apparel": "CXUAPPARELLB0101M", "clothing": "CXUAPPARELLB0101M", "boutique": "CXUAPPARELLB0101M",
    "clinic": "CXUHEALTHLB0101M", "dental": "CXUHEALTHLB0101M", "health": "CXUHEALTHLB0101M",
    "pet": "CXUPETSLB0101M", "veterinary": "CXUPETSLB0101M",
    "gym": "CXUENTRTAINLB0101M", "fitness": "CXUENTRTAINLB0101M", "yoga": "CXUENTRTAINLB0101M",
    "cinema": "CXUENTRTAINLB0101M", "entertainment": "CXUENTRTAINLB0101M", "recreation": "CXUENTRTAINLB0101M",
}


def _resolve_cex_series(category: str) -> Optional[str]:
    """Map a category to a BLS CEX 'mean annual expenditure' series id (CXU…). Tries the curated,
    API-verified map first (longest substring match — deterministic, real); only falls back to the
    LLM for unmapped categories. The dollar value is NEVER asked of the LLM — always fetched live."""
    if not category:
        return None
    key = category.lower().strip()
    if key in _CEX_SERIES_CACHE:
        return _CEX_SERIES_CACHE[key]
    # Curated, verified series — longest matching keyword wins (so "wine bar" → alcohol).
    best, best_len = None, 0
    for kw, sid in _CEX_SERIES_CURATED.items():
        if kw in key and len(kw) > best_len:
            best, best_len = sid, len(kw)
    if best:
        _CEX_SERIES_CACHE[key] = best
        return best
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


@tool(category="econ", returns="{annual_usd, series_id, source}",
      args_model=BlsCexSpendArgs)
def bls_cex_spend(category: Optional[str] = None,
                  series_id: Optional[str] = None) -> Evidence:
    """Annual household spend ($/yr) for a category from BLS CEX — a real source.

    Pass `series_id` directly, or a `category` (LLM resolves the CEX series id; the
    value still comes from BLS). Optional BLS_API_KEY env raises rate limits.
    Do NOT use for establishment counts (census_business_counts) or area
    demographics (acs_demographics) — this is per-household $/yr for one category.
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
