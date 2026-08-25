"""
B2B firmographic enrichment — for B2B SaaS reports, attach founded year,
funding, employee band, HQ, GitHub footprint to each competitor record.

Sources (in priority order, all free + zero-key):
  1. Wikidata SPARQL — structured infobox data, ~20% hit rate but extremely clean
  2. GitHub Orgs REST — public engineering posture (org repos, primary language)
  3. DDG search snippets → LLM extraction — broadest coverage as a fallback

Each enrichment returns a partial dict; missing fields are None. Caller merges.
Cached via the standard `cache` module (7-day TTL).

Why no Crunchbase/Apollo/LinkedIn:
- Crunchbase is Cloudflare-gated; scraping is brittle and ToS-questionable
- Apollo/Hunter free tiers require credit-card signup
- LinkedIn scraping gets banned + violates ToS
"""
from __future__ import annotations
import json
import re
import time
import urllib.parse
from typing import Any

import requests

from cache import get as cache_get, put as cache_put
from logger import get as get_logger

log = get_logger("firmographics")

WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
GITHUB_API = "https://api.github.com"
USER_AGENT = "MarketResearchPrototype/0.1 (open-source research tool)"


# ---------------------------------------------------------------------------
# Wikidata
# ---------------------------------------------------------------------------
def _wikidata_query(domain: str) -> dict | None:
    """
    Find a Wikidata entity whose official website (P856) matches the domain,
    return founded year, employee count, HQ, total funding (if present).

    Uses a simple LIKE filter — Wikidata stores P856 as full URLs.
    """
    cache_key = f"wikidata:{domain.lower()}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    domain_clean = domain.lower().lstrip("www.").rstrip("/")
    sparql = f"""
    SELECT ?company ?companyLabel ?inception ?employees ?hqLabel ?countryLabel WHERE {{
      ?company wdt:P856 ?website.
      FILTER(CONTAINS(LCASE(STR(?website)), "{domain_clean}"))
      OPTIONAL {{ ?company wdt:P571 ?inception. }}
      OPTIONAL {{ ?company wdt:P1128 ?employees. }}
      OPTIONAL {{ ?company wdt:P159 ?hq. }}
      OPTIONAL {{ ?company wdt:P17 ?country. }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
    }}
    LIMIT 1
    """
    # Iter 38: cached request via scrape.http (24h SQLite cache → near-instant
    # on re-runs). On timeout/4xx/5xx, fall through to a Wayback snapshot.
    from scrape.http import request as _http_request
    try:
        r = _http_request(
            "GET",
            WIKIDATA_SPARQL,
            params={"query": sparql, "format": "json"},
            headers={"Accept": "application/sparql-results+json"},
            timeout=15,
        )
        if r is None or not r.ok:
            # Try Wayback snapshot of the SPARQL endpoint URL (rare hit but free)
            try:
                from scrape.wayback import fetch_via_wayback
                snap = fetch_via_wayback(
                    f"{WIKIDATA_SPARQL}?query={sparql}&format=json",
                    timeout=10,
                )
                if snap:
                    data = json.loads(snap)
                else:
                    raise RuntimeError("wayback also empty")
            except Exception:
                cache_put(cache_key, {})
                return None
        else:
            data = r.json()
        bindings = data.get("results", {}).get("bindings", [])
        if not bindings:
            cache_put(cache_key, {})
            return {}
        b = bindings[0]
        out = {}
        if "inception" in b:
            year_match = re.search(r"\d{4}", b["inception"]["value"])
            if year_match:
                out["founded_year"] = int(year_match.group())
        if "employees" in b:
            try:
                out["employee_count_exact"] = int(float(b["employees"]["value"]))
            except (ValueError, TypeError):
                pass
        if "hqLabel" in b and b["hqLabel"]["value"]:
            hq = b["hqLabel"]["value"]
            country = (b.get("countryLabel") or {}).get("value", "")
            out["hq"] = f"{hq}, {country}".strip(", ") if country else hq
        if out:
            out["_source"] = "wikidata"
        cache_put(cache_key, out)
        return out
    except Exception as e:
        log.warning("wikidata lookup failed for %s: %s", domain, e)
        return None


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------
def _github_org(domain: str, brand: str) -> dict | None:
    """
    Heuristic: try the brand slug as an org name. If it 200s, pull repos and
    aggregate primary language. Many B2B SaaS startups have public GitHub orgs.
    """
    # Slugify brand (TripleWhale → triplewhale, lower, strip non-alnum).
    # R1 (88b416f6): the domain-derived candidate is dropped when the domain is a
    # shared platform host — Dify.ai rostered with github.com turned domain.split
    # into the org guess "github", and the report printed GitHub Inc's languages
    # as Dify's stack.
    from scrape.search import is_shared_platform_host
    candidates = [
        re.sub(r"[^a-z0-9]", "", brand.lower()),
        re.sub(r"[^a-z0-9-]", "", brand.lower().replace(" ", "-")),
    ]
    if domain and not is_shared_platform_host(domain):
        candidates.append(domain.split(".")[0].lower())
    candidates = [c for c in dict.fromkeys(candidates) if c]  # dedupe, drop empty

    cache_key = f"github:{brand.lower()}|{domain.lower()}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    for org in candidates:
        try:
            r = requests.get(f"{GITHUB_API}/orgs/{org}", headers=headers, timeout=8)
            if r.status_code != 200:
                continue
            org_data = r.json()
            # Pull top repos (sorted by stars)
            repos_r = requests.get(
                f"{GITHUB_API}/orgs/{org}/repos",
                params={"per_page": 30, "sort": "updated"},
                headers=headers,
                timeout=8,
            )
            repos = repos_r.json() if repos_r.status_code == 200 else []
            if not isinstance(repos, list):
                repos = []
            languages: dict[str, int] = {}
            total_stars = 0
            for repo in repos:
                if not isinstance(repo, dict):
                    continue
                lang = repo.get("language")
                if lang:
                    languages[lang] = languages.get(lang, 0) + 1
                total_stars += repo.get("stargazers_count", 0) or 0
            primary_language = max(languages, key=languages.get) if languages else None

            # R1 (88b416f6): a name-shaped org is not identity. The org "glean"
            # (147 stars, Ruby) is not Glean the company ($770M raised, real handle
            # gleanwork). Accept only an org whose own blog/site links back to the
            # brand's domain — null-beats-wrong, this module's stated contract.
            blog = str(org_data.get("blog") or "").lower()
            dom_root = domain.lower().lstrip("www.").split("/")[0]
            if not (dom_root and not is_shared_platform_host(dom_root)
                    and dom_root in blog):
                log.debug("github org %s rejected: no link-back to %s (blog=%r)",
                          org, dom_root, blog)
                continue
            out = {
                "github_org": org,
                "github_url": org_data.get("html_url"),
                "public_repos": org_data.get("public_repos"),
                "total_stars": total_stars,
                "primary_language": primary_language,
                "_source": "github",
            }
            cache_put(cache_key, out)
            return out
        except Exception as e:
            log.debug("github lookup for org=%s failed: %s", org, e)
            continue
    cache_put(cache_key, {})
    return {}


# ---------------------------------------------------------------------------
# DDG-snippet → LLM extraction (fallback for funding/employee data)
# ---------------------------------------------------------------------------
LLM_FIRMOGRAPHIC_PROMPT = """Extract firmographic data for this B2B SaaS company from the provided search snippets.

COMPANY: {brand}
DOMAIN: {domain}

SEARCH SNIPPETS:
{snippets}

Return ONLY a JSON object with these fields (use null when the snippets don't support a confident value — DO NOT GUESS):
{{
  "founded_year": 2019 or null,
  "employee_band": "1-10" | "11-50" | "51-200" | "201-500" | "501-1000" | "1000+" or null,
  "total_raised_usd_m": 25 (millions) or null,
  "last_round_stage": "Pre-seed" | "Seed" | "Series A" | "Series B" | "Series C" | "Series D+" | "Acquired" | "Public" or null,
  "last_round_year": 2023 or null,
  "hq": "City, Country" or null,
  "evidence_phrases": ["1-3 short snippet quotes that support the above"]
}}

If the snippets clearly describe a DIFFERENT company (false-positive search hit), return all-null with a "wrong_company" evidence phrase. Be conservative — null beats wrong."""


def _llm_extract_from_snippets(brand: str, domain: str) -> dict | None:
    """
    Use ddgs to grab a few search snippets, then ask the LLM to extract
    firmographic fields from them. Conservative: null beats wrong.
    """
    cache_key = f"firm-llm:{brand.lower()}|{domain.lower()}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    # Iter 38: search cascade (Brave → SearXNG → ddgs) instead of ddgs-only
    try:
        from scrape import search as _search
        queries = [
            f'"{brand}" raised series funding crunchbase',
            f'"{brand}" employees headquartered founded',
        ]
        snippets = []
        for q in queries:
            for hit in _search.search(q, max_results=4):
                title = hit.get("title") or ""
                body = hit.get("snippet") or ""
                if title or body:
                    snippets.append(f"- {title}: {body}")

        # Also pull structured data from the company's own homepage (free firmographics)
        try:
            from scrape.http import request as _http_request
            from scrape.structured import extract as _structured_extract
            r = _http_request("GET", f"https://{domain}", timeout=8, allow_redirects=True)
            if r is not None and r.ok and len(r.text) > 200:
                blob = _structured_extract(r.text, base_url=f"https://{domain}")
                facts = []
                if blob.get("founded_year"):
                    facts.append(f"Founded {blob['founded_year']} (per JSON-LD on {domain})")
                if blob.get("employee_count"):
                    facts.append(f"{blob['employee_count']} employees (per JSON-LD on {domain})")
                if blob.get("description"):
                    facts.append(f"Self-description: {blob['description'][:200]}")
                if facts:
                    snippets.append("- " + " | ".join(facts))
        except Exception:
            pass

        if not snippets:
            cache_put(cache_key, {})
            return {}
    except Exception as e:
        log.warning("search cascade failed for %s: %s", brand, e)
        return None

    snippets_blob = "\n".join(snippets)[:3000]

    try:
        from llm import call_json
        result = call_json(
            system="You extract structured firmographics from search snippets. Be conservative — return null when the data isn't clearly stated. Never guess.",
            user=LLM_FIRMOGRAPHIC_PROMPT.format(brand=brand, domain=domain, snippets=snippets_blob),
            max_tokens=900,  # iter 39: bumped from 600 to avoid mid-number JSON truncation
        )
        if "_parse_error" in result:
            cache_put(cache_key, {})
            return {}
        # Strip None values for cleaner merging
        out = {k: v for k, v in result.items() if v is not None and v != []}
        if out:
            out["_source"] = "ddg+llm"
        cache_put(cache_key, out)
        return out
    except Exception as e:
        log.warning("LLM firmographic extraction failed for %s: %s", brand, e)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def enrich_one(brand: str, domain: str) -> dict:
    """
    Enrich a single competitor with firmographics. Returns a dict with whatever
    fields could be discovered. Sources merge in priority order:
      Wikidata > GitHub > LLM-from-snippets

    Empty dict if nothing was found.
    """
    if not brand or not domain:
        return {}
    out: dict[str, Any] = {"brand": brand, "domain": domain, "sources": []}

    wd = _wikidata_query(domain) or {}
    if wd:
        for k, v in wd.items():
            if k == "_source":
                if v not in out["sources"]:
                    out["sources"].append(v)
            elif k not in out or not out.get(k):
                out[k] = v

    gh = _github_org(domain, brand) or {}
    if gh:
        for k, v in gh.items():
            if k == "_source":
                if v not in out["sources"]:
                    out["sources"].append(v)
            elif k not in out or not out.get(k):
                out[k] = v

    # Only invoke the LLM if we're missing the high-value funding/employee fields
    needs_llm = not (
        out.get("founded_year") and (out.get("employee_count_exact") or out.get("employee_band"))
    )
    if needs_llm:
        ll = _llm_extract_from_snippets(brand, domain) or {}
        if ll:
            for k, v in ll.items():
                if k == "_source":
                    if v not in out["sources"]:
                        out["sources"].append(v)
                elif k not in out or not out.get(k):
                    out[k] = v

    # Iter 39: range-validate numeric fields. json_repair sometimes salvages
    # truncated LLM JSON like `{"founded_year": 2}` from a `2025` cut off
    # mid-number. Sanity-check before letting the value reach the report.
    yr = out.get("founded_year")
    if yr is not None:
        try:
            yr_i = int(yr)
            if not (1900 <= yr_i <= 2100):
                log.warning("[firmographics] %s: rejecting bad founded_year=%r", brand, yr)
                out["founded_year"] = None
            else:
                out["founded_year"] = yr_i
        except (TypeError, ValueError):
            out["founded_year"] = None

    raised = out.get("total_raised_usd_m")
    if raised is not None:
        try:
            r_f = float(raised)
            # Cap at $100B to reject obvious garbage (truncated billions etc)
            if not (0 < r_f < 100_000):
                log.warning("[firmographics] %s: rejecting bad total_raised=%r", brand, raised)
                out["total_raised_usd_m"] = None
            else:
                out["total_raised_usd_m"] = r_f
        except (TypeError, ValueError):
            out["total_raised_usd_m"] = None

    last_yr = out.get("last_round_year")
    if last_yr is not None:
        try:
            ly = int(last_yr)
            if not (1990 <= ly <= 2100):
                out["last_round_year"] = None
            else:
                out["last_round_year"] = ly
        except (TypeError, ValueError):
            out["last_round_year"] = None

    # Derive employee_band from exact count if we have one and no band yet
    if not out.get("employee_band") and out.get("employee_count_exact"):
        n = out["employee_count_exact"]
        try:
            n = int(n)
            if not (1 <= n <= 5_000_000):
                out["employee_count_exact"] = None
                n = None
        except (TypeError, ValueError):
            out["employee_count_exact"] = None
            n = None
        if n:
            for low, high, label in [
                (0, 10, "1-10"), (11, 50, "11-50"), (51, 200, "51-200"),
                (201, 500, "201-500"), (501, 1000, "501-1000"), (1001, 10**9, "1000+"),
            ]:
                if low <= n <= high:
                    out["employee_band"] = label
                    break

    return out


def enrich_competitors(competitors: list[dict], max_to_enrich: int = 6) -> list[dict]:
    """
    Enrich the top N B2B competitors with firmographics. Mutates a copy
    of each dict; returns the new list. Slow operations cached.
    """
    out = []
    for i, c in enumerate(competitors[:max_to_enrich]):
        brand = c.get("brand")
        domain = c.get("domain")
        firm = enrich_one(brand, domain) if (brand and domain) else {}
        merged = dict(c)
        merged["firmographics"] = firm
        out.append(merged)
    # Pass through any beyond the enrichment cap unchanged
    for c in competitors[max_to_enrich:]:
        out.append(dict(c))
    return out


def format_firmographic_line(firm: dict) -> str:
    """One-line human summary for the report. Empty string if no data."""
    if not firm or not isinstance(firm, dict):
        return ""
    parts = []
    if firm.get("founded_year"):
        parts.append(f"Founded {firm['founded_year']}")
    if firm.get("hq"):
        parts.append(firm["hq"])
    band = firm.get("employee_band") or (
        f"{firm['employee_count_exact']} emp" if firm.get("employee_count_exact") else None
    )
    if band:
        parts.append(f"{band} employees")
    if firm.get("total_raised_usd_m"):
        parts.append(f"${firm['total_raised_usd_m']}M raised")
    elif firm.get("last_round_stage"):
        parts.append(firm["last_round_stage"])
    if firm.get("primary_language"):
        parts.append(f"primarily {firm['primary_language']}")
    return " · ".join(parts)


# Record DIRECT calls: these are the implementations behind the registered tools
# `enrich_competitor` and `enrich_competitors_batch`. plan.py:1739 calls enrich_competitors
# directly, so without this the firmographic enrichment of every competitor roster is invisible
# to the run ledger.
try:
    from persistence.ledger import instrument_source as _instrument

    enrich_one = _instrument(enrich_one, "enrich_competitor", "firmographic")
    enrich_competitors = _instrument(enrich_competitors, "enrich_competitors_batch",
                                     "firmographic")
except Exception:                              # instrumentation must never break the module
    pass
