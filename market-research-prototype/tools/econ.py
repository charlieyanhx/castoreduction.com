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

from pydantic import BaseModel, Field, model_validator

from logger import get
from .registry import tool, Evidence

log = get("tools.econ")


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
    # Task A: the commonest hyperlocal food venture types were all absent, so
    # bls_cex_spend failed on every live run ("could not resolve a BLS CEX series for
    # 'independent specialty coffee shop'") and the trade-area TAM lost its spend half.
    # All food-away-from-home, the same series `restaurant`/`diner`/`dining` already use.
    #
    # EVERY KEY HERE IS >= 4 CHARS AND MOSTLY MULTI-WORD, ON PURPOSE. Matching is bare
    # substring, so `tea` would match "TEAm analytics saas" and size a B2B SaaS venture on
    # household restaurant spending -- hence `tea house`/`teahouse`/`tea room`, never `tea`.
    # `juice bar`/`smoothie bar` also FIX a live bug: `bar` maps to alcohol, so a juice bar
    # was being sized on household alcohol spend until a longer key could outrank it.
    "coffee": "CXUFOODAWAYLB0101M", "cafe": "CXUFOODAWAYLB0101M",
    "café": "CXUFOODAWAYLB0101M", "espresso": "CXUFOODAWAYLB0101M",
    "roastery": "CXUFOODAWAYLB0101M", "bakery": "CXUFOODAWAYLB0101M",
    "patisserie": "CXUFOODAWAYLB0101M", "pastry": "CXUFOODAWAYLB0101M",
    "tea house": "CXUFOODAWAYLB0101M", "teahouse": "CXUFOODAWAYLB0101M",
    "tea room": "CXUFOODAWAYLB0101M", "bubble tea": "CXUFOODAWAYLB0101M",
    "juice bar": "CXUFOODAWAYLB0101M", "smoothie": "CXUFOODAWAYLB0101M",
    "ice cream": "CXUFOODAWAYLB0101M", "gelato": "CXUFOODAWAYLB0101M",
    "dessert": "CXUFOODAWAYLB0101M", "bistro": "CXUFOODAWAYLB0101M",
    "brasserie": "CXUFOODAWAYLB0101M", "deli": "CXUFOODAWAYLB0101M",
    "sandwich": "CXUFOODAWAYLB0101M", "pizzeria": "CXUFOODAWAYLB0101M",
    "pizza": "CXUFOODAWAYLB0101M", "taqueria": "CXUFOODAWAYLB0101M",
    "noodle": "CXUFOODAWAYLB0101M", "ramen": "CXUFOODAWAYLB0101M",
    "sushi": "CXUFOODAWAYLB0101M", "steakhouse": "CXUFOODAWAYLB0101M",
    "brunch": "CXUFOODAWAYLB0101M", "cafeteria": "CXUFOODAWAYLB0101M",
    "food hall": "CXUFOODAWAYLB0101M", "catering": "CXUFOODAWAYLB0101M",
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
    # Normalise separators before matching. The curated keys are space-separated, but callers
    # pass machine-style names -- size_hyperlocal's own default is "food_away_from_home", and
    # measured, `"food away" in "food_away_from_home"` is False, so the pipeline's default
    # category resolved to nothing while a human typing "food away from home" resolved fine.
    key = category.lower().strip().replace("_", " ").replace("-", " ")
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


def _bls_why(data: dict | None) -> str:
    """The BLS reason for an empty result, appended to our own error text.

    MEASURED: an exhausted quota returns HTTP **200** with
    status=REQUEST_NOT_PROCESSED and "the daily threshold for total number of requests
    allocated to the user ... has been reached". Without this, that surfaced as
    "BLS returned no usable value for CXUFOODAWAYLB0101M" — which sends whoever is
    debugging to look for a bad series id when the series is fine and the QUOTA is the
    problem. The unkeyed limit is 25 requests/day per IP; a free BLS_API_KEY raises it
    to 500/day and is the actual fix.
    """
    if not isinstance(data, dict):
        return ""
    status = data.get("status")
    if not status or status == "REQUEST_SUCCEEDED":
        return ""
    msgs = data.get("message") or []
    if isinstance(msgs, str):
        msgs = [msgs]
    first = str(msgs[0])[:200] if msgs else ""
    hint = ""
    if "threshold" in first.lower() or "daily" in first.lower():
        hint = " — set a free BLS_API_KEY to raise the 25/day unkeyed limit to 500/day"
    return f" [BLS {status}{': ' + first if first else ''}]{hint}"


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
    # Retry once on a stall. Measured over four consecutive calls: 40.1s (hard stall), then
    # 0.5s, 0.4s, 0.4s. BLS is normally sub-second and occasionally hangs at the connection
    # level, and run4 lost its ENTIRE TAM to one such stall -- "BLS API unavailable for
    # CXUFOODAWAYLB0101M" after 12.07s against a 12s timeout, while the series had resolved
    # correctly. Trading a few seconds against publishing no market size is not a close call.
    resp = request("POST", _BLS_API, json=payload, timeout=20)
    if resp is None or getattr(resp, "status_code", 500) >= 400:
        log.warning("[econ] BLS stalled for %s — retrying once", sid)
        resp = request("POST", _BLS_API, json=payload, timeout=20)
    if resp is None or getattr(resp, "status_code", 500) >= 400:
        return Evidence(source="bls_cex_spend", category="econ", count=0, skeleton=True,
                        error=f"BLS API unavailable for {sid} (two attempts)")
    try:
        data = resp.json()
        series = (data.get("Results") or {}).get("series") or []
        rows = series[0].get("data") if series else []
        val = float(str(rows[0]["value"]).replace(",", "")) if rows else None
    except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError):
        data, val = {}, None
    if val is None or val <= 0:
        return Evidence(source="bls_cex_spend", category="econ", count=0, skeleton=True,
                        error=f"BLS returned no usable value for {sid}{_bls_why(data)}")
    return Evidence(
        source="bls_cex_spend", category="econ", count=1,
        payload={"annual_usd": val, "series_id": sid,
                 "source": f"BLS Consumer Expenditure Survey (series {sid})"},
        cost_meta={"annual_usd": val, "series_id": sid, "source": "BLS CEX"},
    )


class CexIncomeQuintileCurveArgs(BaseModel):
    item_code: str = Field(default="FOODAWAY", min_length=3,
                           description="CEX item code, e.g. FOODAWAY for food away from home")


# CEX series id = CXU + <item code> + <demographic code> + M. The LB01xx family is
# "quintiles of income before taxes": LB0101 = all consumer units, LB0102..LB0106 = lowest
# through highest 20%. VERIFIED by probe, not assumed -- the five quintile spends average
# $3,942.8, which reconciles with the published all-units $3,945.
_CEX_QUINTILE_CODES = ("LB0102", "LB0103", "LB0104", "LB0105", "LB0106")
_CEX_ALL_UNITS_CODE = "LB0101"
_CEX_INCOME_ITEM = "INCBEFTX"        # mean income before taxes, the curve's x-axis

# The curve is an ANNUAL NATIONAL CONSTANT, so refetching it per run is pure waste — and it is
# waste that breaks things. MEASURED: scrape.http's requests_cache is configured
# allowable_methods=("GET","HEAD") and the BLS API is POST, so BLS responses are never cached;
# every run hits it live against an unkeyed limit of 25 requests/day per IP. Exhausting that
# quota is not hypothetical — it happened while building this tool, and it took out the existing
# bls_cex_spend call too, costing the whole TAM.
#
# So: cache to disk on success, and fall back to the cache when BLS refuses. The cache stores
# ONLY data that was really fetched, and carries the BLS vintage plus the fetch date with it, so
# a stale curve announces its own age instead of masquerading as fresh. There is deliberately no
# hardcoded curve constant to fall back on.
_CEX_CURVE_CACHE_PATH = os.environ.get("CEX_CURVE_CACHE_PATH") or str(
    __import__("pathlib").Path(__file__).parent.parent / ".cex_curve_cache.json")


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _curve_cache_read(item_code: str) -> Optional[dict]:
    try:
        with open(_CEX_CURVE_CACHE_PATH, "r") as fh:
            return (json.load(fh) or {}).get(item_code)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _curve_cache_write(item_code: str, record: dict) -> None:
    """Best-effort. A cache write failure must never fail a run that already has its data."""
    try:
        try:
            with open(_CEX_CURVE_CACHE_PATH, "r") as fh:
                blob = json.load(fh) or {}
        except (OSError, ValueError, json.JSONDecodeError):
            blob = {}
        blob[item_code] = record
        with open(_CEX_CURVE_CACHE_PATH, "w") as fh:
            json.dump(blob, fh, indent=1, sort_keys=True)
    except (OSError, TypeError, ValueError) as exc:
        log.warning("[econ] could not cache the CEX curve: %s", exc)


@tool(category="econ", returns="{points, all_units_spend, all_units_income, vintage, source}",
      args_model=CexIncomeQuintileCurveArgs)
def cex_income_quintile_curve(item_code: str = "FOODAWAY") -> Evidence:
    """The BLS CEX spend-vs-income curve: mean annual spend by income quintile.

    Returns `points` as [[mean_income_before_tax, mean_annual_spend], ...] ascending, plus the
    all-consumer-units figures. This is the CURVE used to convert a local income distribution
    into a local spend estimate; it is the reason a cafe in a $32k tract and one in a $250k
    tract no longer get the same per-household spend.

    Do NOT use for a single national spend figure -- that is bls_cex_spend. Do NOT use for
    household counts or local income (acs_demographics / acs_income_distribution).
    """
    def _from_cache(why: str) -> Evidence:
        """BLS refused. Serve the last real fetch, saying plainly that it is cached and when."""
        rec = _curve_cache_read(item_code)
        if not rec or len(rec.get("points") or []) < 2:
            return Evidence(source="cex_income_quintile_curve", category="econ", count=0,
                            skeleton=True, error=why)
        payload = dict(rec)
        payload["from_cache"] = True
        payload["source"] = (f"{rec.get('source', 'BLS Consumer Expenditure Survey')} "
                             f"[cached {rec.get('fetched_utc', 'earlier')}; BLS unavailable now]")
        log.warning("[econ] serving the CEX curve from cache — %s", why)
        return Evidence(source="cex_income_quintile_curve", category="econ",
                        count=len(payload["points"]), payload=payload,
                        cost_meta={"source": "BLS CEX (cached)",
                                   "vintage": rec.get("vintage")})

    ids = ([f"CXU{item_code}{c}M" for c in (_CEX_ALL_UNITS_CODE,) + _CEX_QUINTILE_CODES]
           + [f"CXU{_CEX_INCOME_ITEM}{c}M"
              for c in (_CEX_ALL_UNITS_CODE,) + _CEX_QUINTILE_CODES])
    from scrape.http import request
    # "latest" as the STRING "true", matching bls_cex_spend. The boolean form also works
    # (verified live before the quota ran out) but there is no reason for two spellings of the
    # same flag against the same API in one module.
    payload: dict = {"seriesid": ids, "latest": "true"}
    key = os.getenv("BLS_API_KEY")
    if key:
        payload["registrationkey"] = key
    # Same stall retry as bls_cex_spend: BLS is normally sub-second and occasionally hangs at
    # the connection level. A stall here costs the whole local adjustment.
    resp = request("POST", _BLS_API, json=payload, timeout=20)
    if resp is None or getattr(resp, "status_code", 500) >= 400:
        log.warning("[econ] BLS stalled on the CEX quintile curve — retrying once")
        resp = request("POST", _BLS_API, json=payload, timeout=20)
    if resp is None or getattr(resp, "status_code", 500) >= 400:
        return _from_cache("BLS API unavailable (two attempts)")
    try:
        data = resp.json()
        series = (data.get("Results") or {}).get("series") or []
    except (ValueError, AttributeError, json.JSONDecodeError):
        return _from_cache("BLS returned unparseable JSON")

    vals: dict[str, float] = {}
    vintage = None
    for s in series:
        rows = s.get("data") or []
        if not rows:
            continue
        try:
            vals[s["seriesID"]] = float(str(rows[0]["value"]).replace(",", ""))
            vintage = vintage or rows[0].get("year")
        except (ValueError, KeyError, TypeError):
            continue

    points = []
    for code in _CEX_QUINTILE_CODES:
        inc = vals.get(f"CXU{_CEX_INCOME_ITEM}{code}M")
        spend = vals.get(f"CXU{item_code}{code}M")
        # A quintile is usable only if BOTH coordinates arrived. Keeping a half-resolved point
        # would silently distort the curve rather than shorten it.
        if inc and spend and inc > 0 and spend > 0:
            points.append([inc, spend])
    points.sort(key=lambda p: p[0])
    if len(points) < 2:
        return _from_cache(f"only {len(points)} usable quintile point(s) for {item_code} — "
                           f"need at least 2 to interpolate{_bls_why(data)}")

    all_spend = vals.get(f"CXU{item_code}{_CEX_ALL_UNITS_CODE}M")
    all_income = vals.get(f"CXU{_CEX_INCOME_ITEM}{_CEX_ALL_UNITS_CODE}M")
    record = {"points": points, "all_units_spend": all_spend,
              "all_units_income": all_income, "item_code": item_code, "vintage": vintage,
              "source": (f"BLS Consumer Expenditure Survey {vintage or ''} — "
                         f"{item_code} by quintile of income before taxes").strip()}
    _curve_cache_write(item_code, dict(record, fetched_utc=_utc_now()))
    return Evidence(
        source="cex_income_quintile_curve", category="econ", count=len(points),
        payload=dict(record, from_cache=False),
        cost_meta={"source": "BLS CEX", "points": len(points), "vintage": vintage},
    )
