"""
Phase 0: Opportunity Discovery via trend momentum + review velocity.

Low-cost, scraper-free (except Trustpilot which has a stable JSON endpoint).

Pipeline:
  1. Google Trends category scan → slope + rising related queries
  2. LLM (Haiku) extracts brand candidates from the rising queries
  3. For each candidate: validate via brand-level trend slope, Trustpilot
     momentum (review count + velocity + avg stars), Reddit mention count,
     and domain age.
  4. Composite momentum score (pure math)
  5. LLM (Haiku) synthesis writes thesis for each opportunity

All HTTP-bound calls are cached 7 days (see cache.py). Re-running the same
category within a week costs only the LLM calls.
"""
from __future__ import annotations
import json
import os
import re
import time

from sources import (
    google_trends_rising,
    brand_trend_slope,
    trustpilot_momentum,
    reddit_mentions,
    estimate_domain_age_days,
    resolve_brand_domain,
    probe_domain_patterns,
    validate_domain,
    wayback_activity,
    instagram_signal,
    meta_ad_library,
    rank_meta_advertisers,
)
from llm import call_json
from logger import get

log = get("discover")


LLM_BRAND_GENERATION_PROMPT_B2B = """You are researching the "{category}" product category in {geo} for a B2B SaaS founder.

Your job: list 12+ COMPETING B2B PRODUCTS in this space. NOT consumer brands.

⛔ CRITICAL: do NOT include B2C brands or megabrand SaaS incumbents the founder already knows:
  Salesforce, HubSpot, Microsoft 365, Google Workspace, Slack, Notion, Asana, Monday,
  Atlassian (Jira/Confluence), Zoom, Adobe, Oracle, SAP, AWS, Azure, GCP,
  any consumer DTC brand, any retail / e-commerce / CPG brand.

✅ PRIORITIZE these instead — what a B2B founder needs to learn from:
  • Newer SaaS startups (founded 2018+) sold via product-led growth or sales
  • YC / Techstars batch grads in this space
  • Indie SaaS / bootstrapped tools (think Indie Hackers, MicroConf)
  • Niche category leaders (smaller vertical or specific buyer-persona focus)
  • Open-source-with-paid-tier alternatives
  • Recently-funded Series A/B startups in this space (Crunchbase signal)
  • Acquisitions in the last 3 years (signal of hot market)

For each: name, likely_domain (their primary B2B website — NOT app store listing or Amazon),
category_fit (one sentence — what they sell + WHO buys + why a B2B founder should study them).

Return JSON {{"brands": [{{"name": ..., "likely_domain": ..., "query_evidence": "LLM-generated", "category_fit": ...}}, ...]}}.

Quality > quantity. 4 real B2B challengers > 12 with Salesforce padding."""


LLM_BRAND_GENERATION_PROMPT = """You are researching the "{category}" product category in {geo} for a DTC founder.

⛔ CRITICAL: do NOT include legacy mass-market megabrands. The founder ALREADY KNOWS those exist. They learn nothing from a list that includes:
  Mentos, Wrigley, Hershey, Kellogg's, Coca-Cola, Pepsi, Nike, Apple, Samsung,
  Procter & Gamble brands, Unilever brands, Mars brands, Kraft brands,
  any brand owned by Nestle, J&J, Colgate-Palmolive, etc.
  Established household names predating 2010 generally don't count unless they recently pivoted.

✅ PRIORITIZE these instead — these are who founders actually need to learn from:
  • Shopify-store DTC challengers (sell direct, often launched 2018-2024)
  • TikTok-native brands with viral growth
  • Indie / bootstrapped brands featured on Product Hunt, BeautyTok, /r/DTC
  • Crowdfunded brands (Kickstarter / Indiegogo origin)
  • Subscription-box natives
  • "Better-for-you" / health-conscious challengers
  • Sustainable / B-corp / mission-driven brands
  • Celebrity-founded / creator-founded brands
  • Niche premium / specialty positioning
  • International DTC brands now expanding to {geo}

For "{category}" specifically, think: who would a savvy DTC founder follow on Twitter? Who's on the latest Forbes "30 under 30" CPG list? Who's been featured in Modern Retail or Retail Brew newsletters? Who's been acquired by big CPG in the last 3 years (signal of a hot space)?

List AT LEAST 12 such challenger brands. For each:
- name: the brand name
- likely_domain: their primary DTC website (NOT amazon.com listings)
- category_fit: ONE sentence — what makes them a CHALLENGER worth studying (not just "they sell X")

Return JSON:
{{
  "brands": [
    {{"name": "Brand Name", "likely_domain": "brand.com", "query_evidence": "LLM-generated", "category_fit": "what makes them a challenger worth studying"}},
    ...
  ]
}}

If you can only think of legacy brands, return fewer brands rather than padding with incumbents. Quality > quantity. A list of 4 real DTC challengers is more useful than 12 with Wrigley/Hershey at the top."""


EXTRACT_BRANDS_PROMPT = """From these Google Trends "rising" queries in the "{category}" category, extract distinct EMERGING or DTC brand names and guess their likely .com domain.

Rising queries (search term + relative lift):
{queries}

Return JSON:
{{
  "brands": [
    {{"name": "Brand Name", "query_evidence": "the query that mentions this brand", "likely_domain": "brandname.com"}},
    ...
  ]
}}

Rules:
- SKIP well-known megabrands (Apple, Samsung, Sony, Nike, Amazon, etc.) — we want EMERGING or DTC challengers only
- Only include queries that clearly reference a distinct brand/product name
- Skip generic category queries like "best {category} 2024"
- If multiple queries reference the same brand, combine them
- Maximum 10 brands
- Prefer brands that are small, new, or direct-to-consumer
- If uncertain about the domain, still include the brand and set likely_domain to your best guess"""


SYNTHESIS_PROMPT = """You are ranking market opportunities from momentum signals.

Category: {category}
Category-level trend slope (12mo): {slope}
Competitors discovered: {density} total; {active_density} with meaningful web-momentum signal (low = open opportunity, high = crowded)
Average opportunity score across candidates: {avg_score}

Candidate brands with evidence:
{candidates}

CRITICAL: produce a `ranked_opportunities` entry for **EVERY candidate listed** —
do NOT drop any. If a candidate looks weak, still include it but mark
`suggested_next_step: "ignore"` and explain why in the thesis. Founders need
the full landscape, not a curated subset.

For each candidate write a one-sentence thesis explaining what makes it an opportunity (or why it's a reference to learn from), and suggest the next move.

Return JSON:
{{
  "category_read": "2-3 sentences on the category as a whole",
  "ranked_opportunities": [
    {{
      "rank": int,
      "brand": "name",
      "domain": "domain.com",
      "opportunity_score": 0-100,
      "signals": {{
        "trend_slope": float or null,
        "trustpilot_reviews": int or null,
        "trustpilot_avg_stars": float or null,
        "trustpilot_velocity_slope": float or null,
        "reddit_mentions": int or null,
        "domain_age_days": int or null
      }},
      "description": "1 sentence — what this business sells or does",
      "relevance": "direct | adjacent | reference — 'direct' = same product category; 'adjacent' = same buyer/business model but different product; 'reference' = different category but instructive case study",
      "thesis": "why this is an opportunity or reference — 1-2 sentences, specific",
      "suggested_next_step": "decode_taste | monitor | ignore"
    }}
  ]
}}

Scoring guidance:
- Rising trend slope + young domain + growing Trustpilot velocity = hottest (80-100)
- Rising trend + established domain = mature reference (50-75)
- Flat or declining signals = low opportunity (0-40)
- No Trustpilot + no Reddit = insufficient data (mark thesis accordingly)"""


# Megabrands to flag — if discovered, score them down hard. They add noise.
MEGABRAND_NAMES = {
    # Candy / mints
    "mentos", "wrigley", "altoids", "tic tac", "tictac", "ice breakers", "icebreakers",
    "trident", "extra", "dentyne", "orbit", "stride", "5 gum", "eclipse",
    # Mega CPG
    "hershey", "nestle", "mars", "kraft", "kellogg", "kelloggs", "general mills",
    "coca-cola", "coca cola", "pepsi", "pepsico", "kraft heinz",
    "procter & gamble", "p&g", "unilever", "j&j", "johnson & johnson",
    "colgate", "palmolive", "crest", "oral-b", "oral b", "philips sonicare",
    # Tech mega
    "apple", "samsung", "google", "amazon", "microsoft", "meta", "facebook",
    "nike", "adidas", "under armour",
    # Fashion / beauty mega
    "loreal", "l'oreal", "estee lauder", "estée lauder", "shiseido",
}


def _density_counts(enriched: list[dict]) -> tuple[int, int]:
    """B1/D16: competitor_density must count the actual competitor SET discovered
    (len(enriched)), not web-momentum hits (_score>20). A hyperlocal cafe with 30
    real OSM-sourced venues and zero web presence used to score density=1 — the
    viability prompt then faithfully rendered '1 meaningful competitor' against a
    market with 30 real rivals (R4 criticals: e8baf9dd, 955a4b3b, 94008e7c).
    Returns (competitor_density, active_signal_density) — the latter preserves the
    old web-momentum-only count as a distinct, honestly-labeled signal."""
    active = sum(1 for e in enriched if (e.get("_score") or 0) > 20)
    return len(enriched), active


def _is_megabrand(brand_name: str) -> bool:
    """True if the brand is a legacy megabrand the founder doesn't need to learn from."""
    if not brand_name:
        return False
    n = brand_name.lower().strip()
    if n in MEGABRAND_NAMES:
        return True
    # Also check substring (e.g., "Ice Breakers Candy" → "ice breakers")
    for mega in MEGABRAND_NAMES:
        if mega in n and len(mega) > 4:  # avoid false positives on short names
            return True
    return False


def _signal_score(signals: dict) -> float:
    """
    Composite 0-100 score from free signals. Pure math, no LLM.

    Weighting philosophy: any single signal failing (pytrends rate-limit,
    Trustpilot 403, Reddit 403) shouldn't collapse the score. We now rely
    more on the signals that reliably work, and give credit for a brand
    simply being real+active (validated domain, Wayback, Instagram).

    Weights (max 100):
      - Validated domain existence:    10 pts (base — brand is real)
      - Brand Google Trends slope:     0–25 pts (demand signal, when available)
      - Trustpilot velocity:           0–15 pts (when Trustpilot accessible)
      - Trustpilot quality penalty:    −10 pts if stars <3
      - Reddit context mentions:       0–10 pts (when Reddit accessible)
      - Domain youth (0–5 years):      0–15 pts (opportunity freshness)
      - Wayback activity:              0–15 pts (site liveness)
      - Wayback positive velocity:     0–10 pts (growing)
      - Instagram presence + scale:    0–15 pts (social validation)
    """
    score = 0.0

    # Base credit: the brand has a validated, reachable, non-parked domain
    # This handles the degenerate case where pytrends/Trustpilot/Reddit are all down
    if signals.get("domain") and signals.get("domain_confidence") in ("high", "medium"):
        score += 10

    slope = signals.get("trend_slope")
    if slope is not None:
        # slope of +1.0 (doubled) → 25 pts; +0.3 → 7 pts; negative → 0
        score += max(0, min(25, slope * 25))

    tv = signals.get("trustpilot_velocity_slope")
    if tv is not None:
        score += max(0, min(15, tv * 15))
    elif signals.get("trustpilot_reviews") is not None:
        # on Trustpilot but insufficient history → partial credit
        score += 5

    stars = signals.get("trustpilot_avg_stars")
    if stars is not None and stars < 3.0:
        score -= 10

    rm = signals.get("reddit_mentions") or 0
    score += min(10, rm * 1.0)

    age = signals.get("domain_age_days")
    if age is not None:
        if age < 730:
            score += 15
        elif age < 1825:
            score += 15 * (1 - (age - 730) / (1825 - 730))

    wb_avg = signals.get("wayback_avg_per_month")
    wb_vel = signals.get("wayback_velocity")
    if wb_avg is not None:
        score += min(15, wb_avg * 1.5)
        if wb_vel is not None and wb_vel > 0:
            score += min(10, wb_vel * 10)

    # Instagram — now up to 15 pts (was 8), rewards scale more
    ig_followers = signals.get("ig_followers")
    if ig_followers is not None:
        score += 3  # has IG at all
        if ig_followers >= 10_000:
            score += 3
        if ig_followers >= 100_000:
            score += 4
        if ig_followers >= 500_000:
            score += 5

    # Megabrand penalty: if this is a legacy giant, slash the score in half
    # so genuine challengers rank above them in synthesis output
    if _is_megabrand(signals.get("brand", "")):
        score = score * 0.4
        signals["_is_megabrand"] = True

    return round(max(0.0, min(100.0, score)), 1)


def _union_named_competitors(candidates: list[dict], named_competitors) -> list[dict]:
    """cycle29 + W2-4: operator-named competitors are GUARANTEED to flow through —
    but a near-duplicate of an existing candidate ('Calm Business' when 'Calm.com'
    was already surfaced) TAGS the existing entry as operator-seeded instead of
    appending a second copy of the same company. Returns a new list."""
    if not named_competitors:
        return candidates
    from sources import brand_names_match
    out = [dict(c) for c in candidates]
    for nc in named_competitors:
        nc_clean = (nc or "").strip()
        if not nc_clean:
            continue
        hit = next((c for c in out
                    if brand_names_match(c.get("name") or "", nc_clean)), None)
        if hit is not None:
            hit["_seed"] = "operator"
            continue
        out.append({
            "name": nc_clean,
            "query_evidence": "named_in_description",
            "_seed": "operator",
        })
    return out


def _msd(*args, **kwargs):
    """Lazy indirection to skills.discovery_multi.multi_strategy_discovery — kept as a
    module-level function so it's a stable patch seam in tests AND avoids a top-level
    import cycle (discovery_multi imports from skills.discovery, which pulls tools)."""
    from skills.discovery_multi import multi_strategy_discovery
    return multi_strategy_discovery(*args, **kwargs)


def _union_web_discovered_competitors(candidates: list[dict], category: str, geo: str,
                                      max_candidates: int, ceiling: int = 18) -> list[dict]:
    """Item 2 (scraper audit): ground the competitor SET in LIVE WEB SEARCH, not only
    LLM recall / Google-Trends extraction. Runs the multi-strategy fan-out
    (skills.discovery_multi): parallel diverse web searches (category / 'alternatives
    to X' / 'best <category>' / review-site angles) → dedupe + rank by cross-strategy
    agreement → direct/indirect classification. Its real, web-sourced competitors are
    unioned into the candidate list BEFORE signal gathering, so they flow through the
    same enrichment / ranking / hygiene gates as every other candidate.

    Additive + best-effort: if the search backends are down or return nothing (e.g. no
    TAVILY_API_KEY/BRAVE_SEARCH_KEY and public SearXNG unreachable), the LLM/Trends
    candidates are left untouched — the pipeline degrades to the prior behavior rather
    than failing. Physical-local ventures are additionally covered by the OSM geo path
    in plan.py, which overrides this set when it fires. Mutates+returns the list."""
    try:
        ev = _msd(description=category, geo=geo, max_candidates=max_candidates)
        web = (getattr(ev, "payload", None) or {}).get("competitors") or []
    except Exception as e:
        log.warning("[discover] web fan-out discovery failed (non-fatal): %s", e)
        return candidates
    if not web:
        log.info("[discover] web fan-out returned no competitors (backends down / no keys?)")
        return candidates
    existing = {(c.get("name") or "").strip().lower() for c in candidates}
    added = 0
    for c in web:
        if len(candidates) >= ceiling:
            break
        name = (c.get("name") or "").strip()
        if not name or name.lower() in existing:
            continue
        candidates.append({
            "name": name,
            "domain": c.get("domain"),
            "query_evidence": "web_search",
            "_seed": "web_fanout",
            "relationship": c.get("relationship"),
        })
        existing.add(name.lower())
        added += 1
    log.info("[discover] web fan-out grounded the set with %d live competitors "
             "(%d total candidates)", added, len(candidates))
    return candidates


_COMPLETENESS_SYSTEM = (
    "You audit a competitor list for COMPLETENESS. Given a venture, the competitors "
    "already found, and fresh web-search results, name REAL competitor companies that "
    "are clearly MISSING from the list — actual companies/products drawn from the "
    "search results that genuinely compete, NOT ones already found, NOT review sites / "
    "publishers (Forbes, G2, Capterra, PCMag…), NOT generic phrases ('Best CRM'). If "
    "the list already looks complete, return an empty list. Return ONLY JSON: "
    "{\"missing\": [{\"name\": str, \"domain\": str|null}]}."
)


def _verify_competitor_completeness(candidates: list[dict], category: str, geo: str,
                                    max_add: int = 6, ceiling: int = 20) -> list[dict]:
    """Item 4 (scraper audit — the verification loop): a SECOND, adversarial pass that
    attacks the LLM-recall blind spot the fan-out can still share. It seeds live searches
    off the CURRENT set ('alternatives to <known competitor>') plus category queries,
    then asks an LLM — grounded in the fresh search results, not its own memory — which
    real competitors are MISSING, and unions them in. This is the 'what did we miss?'
    critic: 'alternatives to X' surfaces rivals that no category query returns.

    Best-effort and additive: any failure (backends down, no LLM) leaves the set
    untouched. Mutates + returns the list."""
    try:
        from tools.scrape import web_search, filter_aggregator_domains
        existing = [(c.get("name") or "").strip() for c in candidates if c.get("name")]
        queries = [f"top {category} companies", f"{category} competitors"]
        for seed in existing[:2]:
            queries.append(f"alternatives to {seed}")
        hits: list[dict] = []
        for q in queries[:4]:
            ev = web_search(q, max_results=6)
            rows = (filter_aggregator_domains(ev.payload or []).payload) or []
            hits.extend(r for r in rows if isinstance(r, dict))
        if not hits:
            return candidates
        lines = [f"- {h.get('title','')} | {(h.get('snippet') or '')[:140]}" for h in hits[:30]]
        raw = call_json(
            system=_COMPLETENESS_SYSTEM,
            user=(f"VENTURE: {category}\n\nALREADY FOUND:\n"
                  + "\n".join(f"- {n}" for n in existing)
                  + "\n\nFRESH SEARCH RESULTS:\n" + "\n".join(lines)),
            max_tokens=500,
        ) or {}
    except Exception as e:
        log.warning("[discover] completeness check failed (non-fatal): %s", e)
        return candidates
    existing_lower = {n.lower() for n in existing}
    added = 0
    for m in (raw.get("missing") or []):
        if added >= max_add or len(candidates) >= ceiling:
            break
        if not isinstance(m, dict):
            continue
        name = (m.get("name") or "").strip()
        if not name or name.lower() in existing_lower:
            continue
        candidates.append({
            "name": name,
            "domain": m.get("domain"),
            "query_evidence": "completeness_check",
            "_seed": "completeness",
        })
        existing_lower.add(name.lower())
        added += 1
    if added:
        log.info("[discover] completeness critic surfaced %d missing competitor(s)", added)
    return candidates


def _merge_enrichment_provenance(ranked_ops: list, enriched: list) -> None:
    """Copy per-record provenance from the enriched candidates into the LLM-ranked
    records, matched by domain then brand (R4 rank 4).

    The synthesis LLM rebuilds each record, so everything the enrichment learned —
    HOW the domain was adopted (domain_source/domain_confidence) and the relevance
    verdict — was dropped on the floor: 251/251 stored ranked records carried none of
    it, which meant no gate could ever police identity-blind adoption. Mutates
    ranked_ops in place; records with no enriched counterpart are left alone.
    """
    by_domain = {e.get("domain"): e for e in enriched if e.get("domain")}
    by_brand = {e.get("brand"): e for e in enriched if e.get("brand")}
    for op in ranked_ops or []:
        src = by_domain.get(op.get("domain")) or by_brand.get(op.get("brand"))
        if not src:
            continue
        for key in ("off_category", "relevance_score", "domain_source",
                    "domain_confidence"):
            if key in src:
                op[key] = src[key]


def _apply_relevance_to_ranking(entries: list[dict]) -> list[dict]:
    """B4/D19: an off-category domain (content relevance below the W2-5 threshold)
    must never present as a "direct" competitor, and never outrank on-category
    entries by raw signal score alone (a stale/idle domain with a matching age still
    isn't a rival). Sorted with on-category entries first (stable by their existing
    order otherwise); any off_category entry has its relevance forced to "reference".
    Entries without an off_category field are treated as on-category (no verdict
    available — do not penalize what wasn't checked)."""
    out = []
    for e in entries:
        e = dict(e)
        if e.get("off_category"):
            e["relevance"] = "reference"
        out.append(e)
    out.sort(key=lambda e: bool(e.get("off_category")))
    return out


def _gather_signals(brand: dict, category: str, geo: str) -> dict:
    """
    Pull all free signals for one brand. Category is used as disambiguation
    context — 'David' alone is noise, 'David protein' is signal.
    """
    name = brand["name"]
    out: dict = {"brand": name, "query_evidence": brand.get("query_evidence")}

    # 1. Resolve domain — cascade: (a) validate LLM guess → (b) pattern probe → (c) DDG
    # Use the most distinctive word in the category ("protein" from "protein
    # bars"), not the filler tail word ("bars"). Longest word is a decent proxy.
    context_kw = max(category.split(), key=len) if " " in category else category
    domain = None
    domain_confidence = None
    llm_guess = brand.get("likely_domain")
    if llm_guess:
        v = validate_domain(llm_guess, context_keyword=context_kw, brand_name=name,
                            category=category)  # W2-5: content relevance gate
        # NEW: reject parked domains
        if v.get("parked"):
            log.info(f"  rejected parked domain for {name}: {llm_guess}")
        elif v.get("ok") and v.get("strong_match"):
            # W2-3: registrable root, multi-part-TLD aware — the naive last-two-labels
            # join stored 'co.uk' as a UK brand's domain (then fetched https://co.uk).
            from sources import root_domain
            domain = root_domain(v["final_url"])
            out["domain_source"] = "llm_validated"
            domain_confidence = "high"
            # Capture homepage description for the UI
            if v.get("meta_desc"):
                out["description"] = v["meta_desc"]
            elif v.get("title"):
                out["description"] = v["title"]
        # B4/D19: retain the W2-5 relevance verdict regardless of strong_match outcome
        # — validate_domain already computed it, and discarding it let an off-category
        # domain (e.g. a crypto-SaaS site for a superconductor venture) rank as a
        # "direct" competitor purely on domain age, with no relevance signal at all.
        if "relevance" in v or "off_category" in v:
            out["relevance_score"] = v.get("relevance")
            out["off_category"] = bool(v.get("off_category"))
    if not domain:
        try:
            p = probe_domain_patterns(name, context_keyword=context_kw)
            if p and p["confidence"] in ("high", "medium"):
                domain = p["domain"]
                out["domain_source"] = "pattern_probe"
                domain_confidence = p["confidence"]
                ev = p.get("evidence", {})
                if ev.get("meta_desc") and "description" not in out:
                    out["description"] = ev["meta_desc"]
                elif ev.get("title") and "description" not in out:
                    out["description"] = ev["title"]
        except Exception as e:
            out["pattern_error"] = str(e)
    if not domain:
        try:
            r = resolve_brand_domain(name, context=category)
            if r:
                domain = r
                out["domain_source"] = "ddg"
                domain_confidence = "medium"
        except Exception as e:
            out["ddg_error"] = str(e)
    # R8 / audit item 7: tiers (b) pattern-probe and (c) DDG resolve a domain WITHOUT ever
    # checking category relevance — validate_domain is the only tier that computes the
    # verdict. Measured on the Wave-4-entry corpus: only 168/312 competitor signals carried
    # an off_category verdict at all, so 46% reached the ranking with NO relevance signal.
    # _apply_relevance_to_ranking deliberately does not penalize what wasn't checked, so
    # those ranked as DIRECT competitors on signal score alone — and the pipeline then
    # scraped the wrong site, wrote a thesis about it, and priced it into the benchmark
    # (PurpleAir -> purpleair.shop age 7d; Clay -> Clay Labs GTM SaaS). Whichever tier wins,
    # the SAME verdict runs before the domain is trusted. The re-fetch is served from the
    # 24h requests-cache the probe just populated, so this costs ~nothing.
    if domain and "off_category" not in out:
        try:
            v2 = validate_domain(domain, context_keyword=context_kw, brand_name=name,
                                 category=category)
            if "relevance" in v2 or "off_category" in v2:
                out["relevance_score"] = v2.get("relevance")
                out["off_category"] = bool(v2.get("off_category"))
        except Exception as e:
            out["relevance_check_error"] = str(e)

    if domain:
        out["domain"] = domain
        out["domain_confidence"] = domain_confidence

    # 2. Brand trend slope — use the distinctive word for disambiguation,
    # but skip it if the brand name already contains it (avoids "david protein protein")
    if context_kw.lower() in name.lower():
        trend_query = name.lower()
    else:
        trend_query = f"{name} {context_kw}".lower()
    try:
        bt = brand_trend_slope(trend_query, geo=geo)
        out["trend_slope"] = bt.get("slope_12m")
        out["trend_current"] = bt.get("current")
        out["trend_query_used"] = trend_query
    except Exception as e:
        out["trend_error"] = str(e)

    time.sleep(2.5)  # pytrends rate limit courtesy (429s appear around 1/sec)

    # 3. Trustpilot momentum (only if we resolved a domain)
    if domain:
        try:
            tm = trustpilot_momentum(domain)
            if tm.get("on_trustpilot"):
                out["trustpilot_reviews"] = tm.get("review_count_sample")
                out["trustpilot_avg_stars"] = tm.get("avg_stars")
                out["trustpilot_velocity_slope"] = tm.get("velocity_slope")
                out["trustpilot_monthly"] = tm.get("monthly_review_counts")
        except Exception as e:
            out["trustpilot_error"] = str(e)

    # 4. Reddit mentions — search with context, filter by subreddit relevance
    try:
        posts = reddit_mentions(f'"{name}" {context_kw}', limit=25)
        # Filter posts whose title or body actually mentions the category word
        relevant = [
            p for p in posts
            if context_kw.lower() in (p.get("title") or "").lower()
            or context_kw.lower() in (p.get("selftext") or "").lower()
        ]
        out["reddit_mentions"] = len(relevant)
        out["reddit_raw_mentions"] = len(posts)
        out["reddit_top_subs"] = list({p.get("subreddit") for p in relevant if p.get("subreddit")})[:5]
    except Exception as e:
        out["reddit_error"] = str(e)

    # 5. Domain age
    if domain:
        try:
            age = estimate_domain_age_days(domain)
            out["domain_age_days"] = age
        except Exception as e:
            out["age_error"] = str(e)

    # 6. Wayback Machine activity — crawl frequency as liveness/traffic proxy
    if domain:
        try:
            wb = wayback_activity(domain, months_back=12)
            if "error" not in wb:
                out["wayback_snapshots_total"] = wb.get("snapshots_total")
                out["wayback_avg_per_month"] = wb.get("avg_per_month")
                out["wayback_velocity"] = wb.get("velocity")
                out["wayback_months_covered"] = wb.get("months_covered")
        except Exception as e:
            out["wayback_error"] = str(e)

    # 7. Instagram handle + follower count (free, no auth)
    if domain:
        try:
            ig = instagram_signal(domain)
            if ig.get("has_instagram"):
                out["ig_handle"] = ig.get("handle")
                out["ig_followers"] = ig.get("followers")
                out["ig_posts"] = ig.get("posts")
                out["ig_following"] = ig.get("following")
        except Exception as e:
            out["ig_error"] = str(e)

    out["_score"] = _signal_score(out)
    return out


def _run_signal_gathering_and_synthesis(result: dict, candidates: list, category: str, geo: str, max_candidates: int) -> dict:
    """Shared logic for steps 3-5: gather signals, optional Meta, LLM synthesis."""
    # Item 2 (scraper audit): ground the candidate set in live web search before
    # enrichment — both candidate paths (Trends-extraction and LLM-generation) funnel
    # through here, so this is the single point that makes the shipped competitor set
    # web-grounded instead of LLM-recall-only. Best-effort; leaves candidates unchanged
    # if search is unavailable.
    candidates = _union_web_discovered_competitors(candidates, category, geo, max_candidates)

    # Item 4 (scraper audit): a second, adversarial completeness pass — 'alternatives to
    # <known competitor>' searches + an LLM 'what did we miss?' critic grounded in the
    # fresh results — surfaces real rivals the fan-out / LLM recall both missed. Runs
    # before enrichment so any additions get the same signal gathering + hygiene.
    candidates = _verify_competitor_completeness(candidates, category, geo)

    # 3. Gather signals for each candidate.
    # Iter 39: parallelize — each candidate's signal gathering is fully
    # independent (different domains, different APIs). Sequential was
    # ~30s/candidate × 12 = 6 min; with 4 workers that drops to ~90 sec.
    # Per-host rate limiting in scrape.http protects against hammering one
    # host even when concurrency rises.
    log.info(f"[discover] step 3/5: gather signals for {len(candidates)} brands (parallel)")
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _enrich_one(brand: dict) -> dict:
        try:
            sig = _gather_signals(brand, category=category, geo=geo)
            if not sig.get("description") and brand.get("category_fit"):
                sig["description"] = brand["category_fit"]
            return sig
        except Exception as e:
            return {"brand": brand.get("name"), "error": str(e)}

    enriched = [None] * len(candidates)  # type: ignore
    with ThreadPoolExecutor(max_workers=4) as pool:
        future_to_idx = {pool.submit(_enrich_one, b): i for i, b in enumerate(candidates)}
        done_count = 0
        for fut in as_completed(future_to_idx):
            i = future_to_idx[fut]
            try:
                enriched[i] = fut.result(timeout=120)
            except Exception as e:
                enriched[i] = {"brand": candidates[i].get("name"), "error": f"timeout: {e}"}
            done_count += 1
            log.info(f"  [{done_count}/{len(candidates)}] {candidates[i].get('name')} done")
    # Replace any None (shouldn't happen but defensive)
    enriched = [e or {"brand": "?", "error": "no result"} for e in enriched]
    result["steps"]["signals"] = enriched

    # 4. (Optional) Meta Ad Library
    meta_token = os.environ.get("META_ACCESS_TOKEN", "")
    if meta_token:
        log.info("[discover] step 4/5: meta ad library enrichment")
        ads = []
        for brand in candidates[:5]:
            try:
                ads.extend(meta_ad_library(brand["name"], access_token=meta_token, limit=20))
            except Exception:
                pass
            time.sleep(0.5)
        result["steps"]["meta_advertisers"] = rank_meta_advertisers(ads)[:10] if ads else []
    else:
        log.info("[discover] step 4/5: meta ad library SKIPPED (no token)")

    # 5. LLM synthesis
    log.info("[discover] step 5/5: llm synthesis")
    enriched_sorted = sorted(enriched, key=lambda x: x.get("_score", 0), reverse=True)
    density, active_density = _density_counts(enriched_sorted)
    avg_score = (
        round(sum((e.get("_score") or 0) for e in enriched_sorted) / len(enriched_sorted), 1)
        if enriched_sorted else 0
    )
    result["competitor_density"] = density
    result["active_signal_density"] = active_density
    result["avg_opportunity_score"] = avg_score

    try:
        synthesis = call_json(
            system="You rank DTC market opportunities from momentum signals.",
            user=SYNTHESIS_PROMPT.format(
                category=category,
                slope=result.get("steps", {}).get("trends", {}).get("slope_12m"),
                density=density,
                active_density=active_density,
                avg_score=avg_score,
                candidates=json.dumps(enriched_sorted, default=str)[:5000],
            ),
            max_tokens=6000,  # iter 39: bumped — was dropping 7/8 candidates due to truncation
        )
        # call_json returns {"_parse_error": ...} on bad JSON — treat as failure
        if isinstance(synthesis, dict) and "_parse_error" in synthesis:
            raise RuntimeError(f"LLM JSON parse failed: {synthesis.get('_parse_error')}")
        # Also ensure it has the expected structure
        if not isinstance(synthesis, dict) or "ranked_opportunities" not in synthesis:
            raise RuntimeError(f"LLM returned malformed synthesis: missing ranked_opportunities")
        # B4/D19: the LLM ranks/labels relevance itself and doesn't see off_category —
        # merge the W2-5 verdict back in by domain (fallback: brand name) so an
        # off-category domain can never present as "direct" or outrank on-category
        # entries by raw score.
        _merge_enrichment_provenance(synthesis.get("ranked_opportunities") or [],
                                     enriched_sorted)
        synthesis["ranked_opportunities"] = _apply_relevance_to_ranking(
            synthesis["ranked_opportunities"])
        result["synthesis"] = synthesis
    except Exception as e:
        log.warning("LLM synthesis failed (%s), building raw ranking", e)
        raw_ranked = [
            {
                "rank": i,
                "brand": c.get("brand"),
                "domain": c.get("domain"),
                "description": c.get("description", ""),
                "opportunity_score": c.get("_score", 0),
                "relevance": "direct",
                "off_category": c.get("off_category", False),
                "relevance_score": c.get("relevance_score"),
                "signals": {
                    k: c.get(k) for k in
                    ["trend_slope", "trustpilot_reviews", "trustpilot_avg_stars",
                     "ig_followers", "wayback_avg_per_month", "domain_age_days"]
                    if c.get(k) is not None
                },
                "thesis": "(LLM synthesis unavailable — review raw signals)",
                "suggested_next_step": "decode_taste" if c.get("_score", 0) > 20 else "investigate_further",
            }
            for i, c in enumerate(enriched_sorted, 1)
        ]
        result["synthesis"] = {
            "category_read": f"Category '{category}' with {len(enriched_sorted)} candidates analyzed.",
            "ranked_opportunities": _apply_relevance_to_ranking(raw_ranked),  # B4/D19
            "_synthesis_error": str(e),
        }

    return result


def discover(category: str, geo: str = "US", max_candidates: int = 10, business_model: str = "", named_competitors: list[str] | None = None) -> dict:
    log.info(f"[discover] category={category!r} geo={geo}")
    result: dict = {"category": category, "geo": geo, "steps": {}}

    # 1. Category-level trends
    log.info("[discover] step 1/5: google trends category scan")
    try:
        trends = google_trends_rising(category, geo=geo)
        result["steps"]["trends"] = trends
    except Exception as e:
        result["steps"]["trends"] = {"error": str(e)}
        return result

    rising = trends.get("rising_queries", [])
    # Guard: rising_queries can be a dict {"error": ...} if the API failed
    if not isinstance(rising, list):
        rising = []

    # If Google Trends failed or returned nothing, use LLM to generate brand candidates directly
    if not rising:
        log.info("[discover] Google Trends returned no data — using LLM brand generation fallback")
        try:
            llm_brands = call_json(
                system="You are a DTC market research analyst with deep knowledge of emerging consumer brands.",
                # Switch prompt for B2B SaaS / B2B services to avoid surfacing consumer brands
                user=(LLM_BRAND_GENERATION_PROMPT_B2B if "b2b" in (business_model or "").lower() else LLM_BRAND_GENERATION_PROMPT).format(category=category, geo=geo),
                max_tokens=2000,
            )
            candidates = llm_brands.get("brands", [])[:max_candidates]
            if candidates:
                result["brand_extraction_method"] = "llm_generated"
                result["steps"]["trends"] = {
                    "category": category, "geo": geo,
                    "slope_12m": None,
                    "rising_queries": [],
                    "note": "Google Trends unavailable — brands generated by LLM"
                }
                # cycle29: union operator-named competitors here too
                if named_competitors:
                    existing_names = {(c.get("name") or "").lower() for c in candidates}
                    for nc in named_competitors:
                        nc_clean = (nc or "").strip()
                        if nc_clean and nc_clean.lower() not in existing_names:
                            candidates.append({"name": nc_clean, "query_evidence": "named_in_description", "_seed": "operator"})
                            existing_names.add(nc_clean.lower())
                    max_candidates = max(max_candidates, len(candidates))
                # Skip to signal gathering (step 3)
                log.info(f"  → {len(candidates)} LLM-generated candidates: {[c.get('name') for c in candidates]}")
                # Jump ahead — set rising to skip the normal extraction
                result["steps"]["keywords"] = [c.get("name") for c in candidates]
                # Go directly to signal gathering
                return _run_signal_gathering_and_synthesis(result, candidates, category, geo, max_candidates)
        except Exception as e:
            log.warning("LLM brand generation also failed: %s", e)

        result["error"] = "Could not find opportunities — Google Trends is rate-limited and LLM fallback failed. Try again in a few minutes."
        return result

    # 2. Extract brand candidates — prefer LLM, fall back to rule-based
    log.info(f"[discover] step 2/5: extract brand candidates from {len(rising)} rising queries")
    queries_str = "\n".join(
        f'- "{q.get("query")}" (+{q.get("value")}%)' for q in rising[:30]
    )
    candidates = []
    try:
        brands_resp = call_json(
            system="You extract brand names from search trend data.",
            user=EXTRACT_BRANDS_PROMPT.format(category=category, queries=queries_str),
            max_tokens=1500,
        )
        candidates = brands_resp.get("brands", [])[:max_candidates]
        result["brand_extraction_method"] = "llm"
    except Exception as e:
        log.warning("LLM brand extraction failed (%s), using rule-based fallback", e)
        result["brand_extraction_warning"] = str(e)

    # Fallback: rule-based extraction (strip category words, keep brand names)
    if not candidates:
        log.info("  → using rule-based brand extraction fallback")
        cat_words = set(category.lower().split()) | {
            # Common filler words
            "best", "review", "reviews", "healthy", "the", "are", "where",
            "to", "buy", "how", "why", "is", "new", "top", "for", "vs",
            "good", "bad", "cheap", "online", "near", "free", "does", "what",
            "can", "should", "which", "your", "our", "with", "without",
            "natural", "organic", "premium", "safe", "effective",
            # Generic product descriptors (not brands)
            "app", "apps", "tracking", "tracker", "device", "tool", "tools",
            "supplement", "supplements", "vitamin", "vitamins", "pill", "pills",
            "capsule", "capsules", "powder", "gummy", "gummies", "cream",
            "serum", "oil", "spray", "mask", "patch", "tape",
            # Common ingredients (not brands)
            "melatonin", "magnesium", "theanine", "ashwagandha", "valerian",
            "zinc", "iron", "calcium", "collagen", "retinol", "niacinamide",
            "caffeine", "creatine", "protein", "fiber", "probiotic",
        }
        seen: set[str] = set()
        for q in rising:
            words = [w for w in re.findall(r"[a-z]+", q.get("query", "").lower())
                     if w not in cat_words and len(w) > 2]
            if words:
                name = " ".join(words[:2]).title()
                if name not in seen:
                    seen.add(name)
                    candidates.append({"name": name, "query_evidence": q.get("query")})
            if len(candidates) >= max_candidates:
                break
        result["brand_extraction_method"] = "rule_based"

    if not candidates:
        result["error"] = "no brand candidates found from rising queries"
        return result

    # W2-4: collapse near-duplicate trends candidates ('Calm'/'Calm App') before the
    # operator union so enrichment never runs twice for one company.
    from sources import collapse_near_dupes
    candidates = collapse_near_dupes(candidates)

    # cycle29: union explicit named_competitors from the venture description
    # so they are GUARANTEED to flow through. Was losing Calm Business / Lyra /
    # Big Health / BetterUp because trends-based discovery surfaced different
    # brands than the operator named.
    if named_competitors:
        candidates = _union_named_competitors(candidates, named_competitors)
        # Bump max_candidates so we don't immediately truncate operator seeds
        max_candidates = max(max_candidates, len(candidates))
        log.info(f"  → seeded {len(named_competitors)} operator-named competitors; total candidates: {len(candidates)}")

    log.info(f"  → {len(candidates)} candidates: {[c.get('name') for c in candidates]}")

    # Steps 3-5: gather signals, meta enrichment, synthesis
    return _run_signal_gathering_and_synthesis(result, candidates, category, geo, max_candidates)


if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv

    load_dotenv()
    if len(sys.argv) < 2:
        log.info("usage: python discover.py <category> [geo]")
        sys.exit(1)
    cat = sys.argv[1]
    geo = sys.argv[2] if len(sys.argv) > 2 else "US"
    out = discover(cat, geo=geo)
    print(json.dumps(out, indent=2, default=str))
