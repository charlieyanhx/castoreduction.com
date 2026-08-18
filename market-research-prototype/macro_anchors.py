"""
Iter 36: Macro economic anchors for market sizing credibility.

FRED (Federal Reserve Economic Data, https://fred.stlouisfed.org/) publishes
free economic time-series. No API key required for CSV downloads. Useful for
anchoring TAM/SAM/SOM claims to real macro indicators instead of LLM vibes.

Available anchors (curated set — add more later):
  GDP                   — nominal US GDP (current dollars)
  E-commerce sales      — quarterly US e-commerce retail sales
  Services PPI          — producer price index for services (proxies B2B pricing drift)
  Small business count  — firms with <500 employees

Returned as a small dict for the report's Methodology + Market Sizing sections
to cite. Hit rate is 100% for known series; cached 24h.
"""
from __future__ import annotations
import csv
import io
import time
from typing import Any

import requests

from cache import get as cache_get, put as cache_put
from logger import get

log = get("macro_anchors")

FRED_CSV_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv"
WORLDBANK_BASE = "https://api.worldbank.org/v2/country/{country}/indicator/{ind}"
IMF_BASE = "https://www.imf.org/external/datamapper/api/v1/{indicator}/{country}"
CACHE_TTL = 24 * 3600  # 1 day

# Iter 40 (#4b): hardcoded per-vertical anchors when no API has it.
# These are well-cited industry benchmarks updated yearly. Each entry must
# include a verifiable source URL so the report doesn't look like vibes.
VERTICAL_ANCHORS = {
    # B2B SaaS — KeyBanc Capital Markets / OpenView SaaS Benchmarks 2024
    "b2b_saas_median_nrr": {
        "label": "B2B SaaS Median Net Revenue Retention",
        "value": 102.0, "unit": "%",
        "description": "Median NRR for $1-10M ARR B2B SaaS (KeyBanc 2024 SaaS Survey).",
        "source": "https://www.keybanc.com/-/media/keybank/business-insights/2024-saas-survey-key-findings.pdf",
        "applies_to": ["saas"],  # R4 rank 24: SaaS metric, not all-b2b
    },
    "b2b_saas_median_cac_payback": {
        "label": "B2B SaaS Median CAC Payback",
        "value": 18.0, "unit": "months",
        "description": "Median CAC payback period for $1-25M ARR B2B SaaS (OpenView 2024 SaaS benchmarks).",
        "source": "https://openviewpartners.com/saas-benchmarks/",
        "applies_to": ["saas"],  # R4 rank 24: SaaS metric, not all-b2b
    },
    "b2b_saas_magic_number": {
        "label": "B2B SaaS Magic Number Benchmark",
        "value": 0.7, "unit": "ratio",
        "description": "Healthy magic number benchmark — >1.0 efficient, 0.5-1.0 acceptable, <0.5 inefficient.",
        "source": "https://www.bvp.com/atlas/the-saas-magic-number",
        "applies_to": ["saas"],  # R4 rank 24: SaaS metric, not all-b2b
    },
    # DTC e-commerce — Shopify Plus / Klaviyo / 2024 industry reports
    "dtc_median_repeat_rate": {
        "label": "DTC Median Repeat Purchase Rate (12mo)",
        "value": 28.0, "unit": "%",
        "description": "Median 12-month repeat-purchase rate for DTC brands $1-5M revenue (Klaviyo 2024 benchmarks).",
        "source": "https://www.klaviyo.com/marketing-resources/industry-benchmarks",
        "applies_to": ["dtc", "ecommerce", "consumer"],
    },
    "dtc_median_aov": {
        "label": "DTC Median Order Value",
        "value": 80.0, "unit": "USD",
        "description": "Median AOV across DTC verticals (Shopify 2024 commerce report). Wide variance by category.",
        "source": "https://www.shopify.com/research/state-of-commerce",
        "applies_to": ["dtc", "ecommerce"],
    },
    "us_ecommerce_share": {
        "label": "US E-commerce Share of Retail",
        "value": 16.2, "unit": "%",
        "description": "US e-commerce share of total retail sales Q4 2024 (US Census Bureau Quarterly Retail Report).",
        "source": "https://www.census.gov/retail/ecommerce.html",
        "applies_to": ["dtc", "ecommerce", "consumer"],
    },
    # Healthcare / digital therapeutics
    "digital_health_employer_spend": {
        "label": "Employer Digital Health Spend per Employee",
        "value": 187.0, "unit": "USD/employee/year",
        "description": "Average employer spend on digital health benefits per employee (Mercer 2024 National Survey).",
        "source": "https://www.mercer.com/insights/total-rewards/employee-benefits/national-survey-employer-sponsored-health-plans/",
        "applies_to": ["healthcare", "digital_health", "wellness", "employer"],  # R4 rank 24: health metric, not all-b2b
    },
    "wellbeing_app_completion_rate": {
        "label": "Wellbeing App Median Program Completion Rate",
        "value": 23.0, "unit": "%",
        "description": "Median enrollee completion rate for digital wellness coaching programs (PwC Health Research Institute 2023).",
        "source": "https://www.pwc.com/us/en/industries/health-industries/library.html",
        "applies_to": ["healthcare", "digital_health", "wellness"],
    },
    # Marketplaces
    "marketplace_take_rate_median": {
        "label": "Marketplace Median Take Rate",
        "value": 15.0, "unit": "%",
        "description": "Median platform take rate across consumer + B2B marketplaces (a16z marketplaces benchmarks).",
        "source": "https://a16z.com/marketplace-100/",
        "applies_to": ["marketplace"],
    },
}

# Curated series — key + human-readable label + description.
# Iter 39: each series has a FRED id AND a World Bank fallback (different code,
# same concept). When FRED times out, we hit World Bank's no-key JSON API.
SERIES = {
    "us_gdp_nominal": {
        "fred_id": "GDP",
        "wb_country": "USA",
        "wb_indicator": "NY.GDP.MKTP.CN",  # GDP, current LCU
        "wb_unit_scale": 1e-9,              # convert to Billions
        "label": "US Nominal GDP",
        "unit": "Billions USD",
        "description": "Nominal US Gross Domestic Product (quarterly from FRED, annual from World Bank).",
    },
    "ecommerce_sales_quarterly": {
        "fred_id": "ECOMSA",
        "wb_country": None,  # no equivalent
        "label": "US E-commerce Retail Sales",
        "unit": "Millions USD",
        "description": "Quarterly US e-commerce retail sales (seasonally adjusted). Anchors DTC TAM.",
    },
    "services_ppi": {
        "fred_id": "PCU52",
        "wb_country": None,
        "label": "Services PPI (Finance & Insurance)",
        "unit": "Index 2019=100",
        "description": "Producer Price Index for services — proxies B2B service pricing drift.",
    },
    # Iter 40 (#4a): IMF-backed series — GDP growth, no FRED equivalent at this granularity
    "us_real_gdp_growth": {
        "fred_id": "A191RL1Q225SBEA",  # FRED: Real GDP, % change at annual rate
        "imf_indicator": "NGDP_RPCH",   # Real GDP growth — IMF World Economic Outlook
        "imf_country": "USA",
        "label": "US Real GDP Growth",
        "unit": "% YoY",
        "description": "Real GDP growth — useful as the secular tailwind/headwind anchor.",
    },
    "world_real_gdp_growth": {
        "fred_id": None,
        "imf_indicator": "NGDP_RPCH",
        "imf_country": "WEOWORLD",
        "label": "Global Real GDP Growth",
        "unit": "% YoY",
        "description": "Global real GDP growth (IMF WEO). Anchor for international expansion claims.",
    },
}



_YEAR_AGO_TOLERANCE_DAYS = 200   # nearest observation must plausibly BE a year back


def _pick_year_ago(rows, latest):
    """The observation nearest to exactly one year before `latest`, or None.

    The previous rule scanned `reversed(rows)` for the first row in the prior CALENDAR YEAR,
    which is that year's LAST observation. On a quarterly series with the latest point at
    2026-04-01 (Q2), that selects 2025-10-01 (Q4) — two quarters back, labelled "YoY". Choose
    by date distance instead: correct for quarterly, monthly and annual series alike, and it
    returns None rather than a mislabelled comparison when nothing sits near the one-year mark.
    """
    import datetime as _dt

    def _d(row):
        try:
            return _dt.date.fromisoformat(str(row.get("date"))[:10])
        except (TypeError, ValueError):
            return None

    anchor = _d(latest or {})
    if not anchor or not rows:
        return None
    try:
        target = anchor.replace(year=anchor.year - 1)
    except ValueError:                      # 29 Feb
        target = anchor - _dt.timedelta(days=365)
    best, best_gap = None, None
    for r_ in rows:
        d = _d(r_)
        if d is None or d >= anchor:
            continue
        gap = abs((d - target).days)
        if best_gap is None or gap < best_gap:
            best, best_gap = r_, gap
    if best is None or best_gap > _YEAR_AGO_TOLERANCE_DAYS:
        return None
    return best


def _fetch_fred_series(fred_id: str) -> dict | None:
    """Pull the most recent observations for a FRED series. Returns {latest_value, latest_date, prev_year_value, yoy_pct}."""
    cache_key = f"fred:{fred_id}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    # Iter 38: scrape.http (cached) + Wayback fallback when FRED times out
    from scrape.http import request as _http_request
    try:
        r = _http_request(
            "GET",
            FRED_CSV_BASE,
            params={"id": fred_id, "cosd": _recent_cutoff()},
            timeout=15,
        )
        text = None
        if r is not None and r.status_code == 200 and len(r.text) >= 50:
            text = r.text
        else:
            # Wayback fallback — last good snapshot of the FRED CSV
            try:
                from scrape.wayback import fetch_via_wayback
                snap = fetch_via_wayback(
                    f"{FRED_CSV_BASE}?id={fred_id}&cosd={_recent_cutoff()}",
                    timeout=10,
                )
                if snap and len(snap) > 50:
                    text = snap
                    log.info("FRED %s: served from Wayback snapshot (live timed out)", fred_id)
            except Exception:
                pass
            if not text:
                log.warning("FRED series %s unavailable (live + Wayback both failed)", fred_id)
                cache_put(cache_key, {})
                return {}

        reader = csv.DictReader(io.StringIO(text))
        rows = []
        for row in reader:
            # Headers vary: "DATE" or "observation_date"
            date = row.get("DATE") or row.get("observation_date")
            val_key = next((k for k in row.keys() if k not in ("DATE", "observation_date")), None)
            if not date or not val_key:
                continue
            val = row.get(val_key)
            try:
                if val and val != ".":
                    rows.append({"date": date, "value": float(val)})
            except (ValueError, TypeError):
                continue

        if not rows:
            cache_put(cache_key, {})
            return {}

        latest = rows[-1]
        prev_year = _pick_year_ago(rows[:-1], latest)

        out = {
            "latest_value": latest["value"],
            "latest_date": latest["date"],
            "n_observations": len(rows),
            "source": f"https://fred.stlouisfed.org/series/{fred_id}",
        }
        if prev_year:
            out["prev_year_value"] = prev_year["value"]
            out["prev_year_date"] = prev_year["date"]
            if prev_year["value"] > 0:
                yoy = (latest["value"] - prev_year["value"]) / prev_year["value"] * 100
                out["yoy_pct"] = round(yoy, 2)

        cache_put(cache_key, out)
        return out
    except Exception as e:
        log.warning("FRED fetch failed for %s: %s", fred_id, e)
        return None


def _recent_cutoff() -> str:
    """Roughly 3 years ago in YYYY-MM-DD — enough for YoY + trend."""
    import datetime
    d = datetime.date.today() - datetime.timedelta(days=3 * 365)
    return d.isoformat()


def _fetch_worldbank(country: str, indicator: str, scale: float = 1.0) -> dict | None:
    """
    Iter 39: World Bank fallback — no key, JSON API, well rate-limited.
    Returns the same shape as _fetch_fred_series (latest_value/date + YoY).
    """
    cache_key = f"wb:{country}:{indicator}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    from scrape.http import request as _http_request
    try:
        url = WORLDBANK_BASE.format(country=country, ind=indicator)
        r = _http_request(
            "GET", url,
            params={"format": "json", "per_page": 5, "date": f"{__import__('datetime').date.today().year - 4}:{__import__('datetime').date.today().year}"},
            timeout=10,
        )
        if r is None or not r.ok:
            cache_put(cache_key, {})
            return {}
        data = r.json()
        # WB returns [meta, [observations]]
        if not isinstance(data, list) or len(data) < 2:
            cache_put(cache_key, {})
            return {}
        rows = [o for o in (data[1] or []) if o.get("value") is not None]
        if not rows:
            cache_put(cache_key, {})
            return {}
        # WB rows are date-descending; latest first
        latest = rows[0]
        out = {
            "latest_value": float(latest["value"]) * scale,
            "latest_date": latest["date"],
            "n_observations": len(rows),
            "source": f"https://data.worldbank.org/indicator/{indicator}?locations={country}",
        }
        if len(rows) > 1:
            prev = rows[1]
            try:
                pv = float(prev["value"]) * scale
                if pv > 0:
                    out["prev_year_value"] = pv
                    out["prev_year_date"] = prev["date"]
                    out["yoy_pct"] = round((out["latest_value"] - pv) / pv * 100, 2)
            except (TypeError, ValueError):
                pass
        cache_put(cache_key, out)
        return out
    except Exception as e:
        log.warning("World Bank fetch failed for %s/%s: %s", country, indicator, e)
        return None


def _fetch_imf(indicator: str, country: str = "USA") -> dict | None:
    """
    Iter 40 (#4a): IMF DataMapper API — no key, JSON, well-documented.
    Indicator codes: NGDP_RPCH (real GDP growth), NGDPD (nominal GDP USD bn), etc.
    Returns same shape as _fetch_fred_series.
    """
    cache_key = f"imf:{country}:{indicator}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    from scrape.http import request as _http_request
    try:
        r = _http_request("GET", IMF_BASE.format(indicator=indicator, country=country), timeout=10)
        if r is None or not r.ok:
            cache_put(cache_key, {})
            return {}
        data = r.json()
        # IMF returns {values: {INDICATOR: {COUNTRY: {YEAR: VALUE}}}}
        vals = (data.get("values") or {}).get(indicator, {}).get(country, {})
        if not vals:
            cache_put(cache_key, {})
            return {}
        # Years are string keys; pick latest with a numeric value
        years = sorted([(int(y), v) for y, v in vals.items() if v is not None])
        if not years:
            cache_put(cache_key, {})
            return {}
        latest_year, latest_value = years[-1]
        out = {
            "latest_value": float(latest_value),
            "latest_date": str(latest_year),
            "n_observations": len(years),
            "source": f"https://www.imf.org/external/datamapper/{indicator}@WEO/{country}",
        }
        if len(years) > 1:
            prev_year, prev_value = years[-2]
            try:
                pv = float(prev_value)
                if pv > 0:
                    out["prev_year_value"] = pv
                    out["prev_year_date"] = str(prev_year)
                    out["yoy_pct"] = round((out["latest_value"] - pv) / pv * 100, 2)
            except (TypeError, ValueError):
                pass
        cache_put(cache_key, out)
        return out
    except Exception as e:
        log.warning("IMF fetch failed for %s/%s: %s", country, indicator, e)
        return None


def fetch_vertical_anchors(business_model: str = "", category: str = "") -> dict:
    """
    Iter 40 (#4b): return curated industry benchmarks that match this venture's
    business model + category. Hardcoded values + verifiable source URLs.
    """
    tags = set()
    bm = (business_model or "").lower()
    cat = (category or "").lower()
    full = bm + " " + cat
    if "b2b" in full:
        tags.add("b2b")
    if "saas" in full or "software" in full:
        tags.update(["saas", "b2b"])  # R4 rank 24: saas is b2b-flavored, but b2b hardware is not saas
    if "dtc" in full or "ecommerce" in full or "e-commerce" in full or "consumer" in full or "direct-to-consumer" in full:
        tags.update(["dtc", "ecommerce", "consumer"])
    if "marketplace" in full:
        tags.add("marketplace")
    if "health" in full or "wellness" in full or "medical" in full or "therapeutic" in full or "wellbeing" in full or "employer" in full:
        tags.update(["healthcare", "digital_health", "wellness", "employer"])

    out = {}
    for k, anchor in VERTICAL_ANCHORS.items():
        applies = set(anchor.get("applies_to", []))
        if applies & tags:
            out[k] = anchor
    return out


def fetch_anchors(series_keys: list[str] | None = None,
                  business_model: str = "", category: str = "") -> dict:
    """
    Public API: fetch a curated set of macro anchors. Returns:
      {
        series: {key: {label, latest_value, unit, yoy_pct, source, ...}},
        fetched_at: timestamp,
      }

    Iter 39: each series tries FRED first, then World Bank fallback when configured.
    """
    keys = series_keys or list(SERIES.keys())
    out = {"series": {}, "vertical_anchors": {}, "fetched_at": int(time.time())}
    for k in keys:
        meta = SERIES.get(k)
        if not meta:
            continue
        # Try FRED → World Bank → IMF in order; first non-empty wins
        data = _fetch_fred_series(meta["fred_id"]) or {}
        provider = "fred"
        if not data.get("latest_value") and meta.get("wb_country") and meta.get("wb_indicator"):
            data = _fetch_worldbank(meta["wb_country"], meta["wb_indicator"], meta.get("wb_unit_scale", 1.0)) or {}
            provider = "worldbank"
        if not data.get("latest_value") and meta.get("imf_indicator"):
            data = _fetch_imf(meta["imf_indicator"], meta.get("imf_country", "USA")) or {}
            provider = "imf"
        if data.get("latest_value"):
            entry = {
                "label": meta["label"],
                "unit": meta["unit"],
                "description": meta["description"],
                "provider": provider,
                **data,
            }
            # Stamp the year-on-year wording HERE, where the unit is known. The fetchers
            # compute yoy_pct arithmetically for every series and cannot tell a level from a
            # rate; only this loop has the metadata that distinguishes them.
            entry["change_label"] = change_label(entry)
            out["series"][k] = entry
    # Iter 40 (#4b): always attach curated vertical anchors when business_model/category supplied
    if business_model or category:
        out["vertical_anchors"] = fetch_vertical_anchors(business_model, category)
    return out


def _is_rate_unit(unit: str | None) -> bool:
    """Is this series ALREADY expressed as a rate/percentage?

    A level (Billions USD, an index) changes by a PERCENT. A rate (% YoY) changes by
    PERCENTAGE POINTS. Conflating them shipped "US Real GDP Growth · 1.5 %YoY · +200.0%" to
    a customer, because the growth rate had moved 0.5 -> 1.5 and (1.5-0.5)/0.5 = +200%. Every
    reader parses that as GDP growing 200%.
    """
    u = (unit or "").strip().lower()
    return u.startswith("%") or u.startswith("percent") or u == "pp"


def change_label(anchor: dict) -> str:
    """How this series changed over a year, in the units that make it true.

    The ONE owner of that wording — the report table and the methodology citation both read
    it, so they cannot drift apart. Empty string when there is no prior-year observation:
    "+0.0%" would assert stability that was never measured.
    """
    if not anchor:
        return ""
    latest, prev = anchor.get("latest_value"), anchor.get("prev_year_value")
    if _is_rate_unit(anchor.get("unit")):
        if latest is None or prev is None:
            return ""
        return f"{float(latest) - float(prev):+.1f} pp"
    pct = anchor.get("yoy_pct")
    return f"{float(pct):+.1f}%" if pct is not None else ""


def format_anchor_for_citation(anchor: dict) -> str:
    """One-liner summary for methodology appendix."""
    if not anchor:
        return ""
    parts = [anchor.get("label", "?")]
    v = anchor.get("latest_value")
    u = anchor.get("unit", "")
    d = anchor.get("latest_date", "")
    if v:
        parts.append(f"{v:,.1f} {u}")
    if d:
        parts.append(f"as of {d}")
    chg = change_label(anchor)
    if chg:
        parts.append(f"YoY {chg}")
    return " · ".join(parts)
