"""
Iter 36: Customer universe generation (spec step 5).

Builds a list of REAL B2B company names that plausibly match the ICP. Before
this module, the pipeline decoded competitor-audience vibe; a B2B report
buyer wants "you can sell to Warby Parker, Chubbies, Glossier, Hims, ..." —
concrete names with domain + employee band + buyer role to target.

Two discovery methods combined (spec: parallel + merged):

METHOD A — Competitor customer cross-reference:
  - Scrape each top competitor's /customers and /case-studies pages
  - Extract company names from the HTML
  - These are existing buyers in the category (high-intent ICP signal)

METHOD B — LLM-generated ICP + searched candidates:
  - Ask LLM to describe the ideal customer (industry, size, role, pain)
  - Use DDG to find real companies matching the ICP description
  - Cross-reference with Wikidata for firmographic enrichment

Output shape:
  {
    "count": int,
    "icp_summary": str,
    "companies": [
      {name, domain, industry, employee_band, source, evidence_url}
    ],
    "segments": [list of LLM-labeled segments],
    "sources": ["competitor-customers", "ddg+llm"],
    "methods_used": [...]
  }

This is scope-limited (targets ~30 companies, not spec's 200-500, to keep
runtime + API cost bounded; can be raised later via config).
"""
from __future__ import annotations
import json
import re
from typing import Any
from urllib.parse import urlparse

import requests

from cache import get as cache_get, put as cache_put
from llm import call_json
from logger import get

log = get("customer_universe")


ICP_PROMPT = """Given this product, describe the ideal B2B customer (ICP) in detail.

PRODUCT:
{product_summary}

CORE FEATURES:
{features}

DIFFERENTIATORS:
{differentiators}

COMPETITOR TOP CUSTOMERS (already using similar products — pattern-match):
{competitor_customers}

Return a detailed ICP profile. Be concrete and specific — not "small businesses" but
"Shopify DTC brands doing $500K-$5M ARR with an in-house performance marketer".

JSON — short categorical fields FIRST (so they survive any truncation), narrative LAST.
buyer_role is REQUIRED — the SPECIFIC HUMAN who signs the PO. It is NOT the industry,
NOT the company type. It is one job title (e.g. "VP Engineering", "Head of People",
"Director of Benefits", "Founder/CEO", "Chief Information Security Officer").

{{
  "industry": "e.g. 'Direct-to-consumer e-commerce' or 'B2B SaaS mid-market'",
  "company_size_employees": "e.g. '11-50'",
  "company_size_revenue": "e.g. '$500K-$5M ARR'",
  "buyer_role": "REQUIRED — specific job title of the economic buyer. Examples: 'VP Engineering', 'Head of People Operations', 'Director of Benefits', 'CISO', 'Founder/CEO'. Do NOT leave null. Do NOT put industry here.",
  "tech_maturity": "low | medium | high",
  "geographic_markets": ["US", "UK", ...],
  "key_pains_that_map": ["pain 1", "pain 2"],
  "search_queries_to_find_them": [
    "5-8 search queries that would surface real companies matching this ICP"
  ],
  "icp_summary": "EXACTLY 1 sentence, hard cap 30 words. Must mention the buyer_role explicitly. Place LAST so truncation only kills it."
}}

CRITICAL: every B2B venture HAS an economic buyer with a job title. If the venture is
sleep-coaching for employers, buyer_role is "Head of Benefits" or "VP People Ops".
If it's APM for engineers, it's "VP Engineering" or "Director of Platform". NEVER leave null."""


SEGMENT_LABEL_PROMPT = """You are labeling B2B customer segments for THIS venture.

VENTURE BEING ANALYZED: {venture_summary}

Here are real companies we've identified as potential customers:
{companies}

Group them into 2-4 meaningful segments. CRITICAL:
- Segment labels must reflect WHY THESE COMPANIES would buy OUR venture
  (e.g. for a sleep-coaching app: "High-stress finance firms with overworked staff",
  not "Global Financial Institutions" which says nothing about why they'd buy sleep coaching).
- Use industry × size × buying-motivation, not just industry.
- ALL fields below are REQUIRED — never leave description, size_pct, or buying_motivation empty.

JSON:
{{
  "segments": [
    {{
      "label": "5-8 words tying industry × size × motivation, e.g. 'High-stress mid-market finance firms (overworked exec teams)'",
      "description": "2-3 sentences: who they are, what they're suffering from, why they'd buy OUR product",
      "size_pct": "<integer percentage 0-100, e.g. 40>",
      "member_examples": ["3-5 example company names from the list above"],
      "buying_motivation": "primary reason they'd buy OUR product specifically — not generic",
      "decision_maker_role": "e.g. 'Head of Benefits' or 'Chief People Officer'"
    }}
  ]
}}

Be opinionated — if one segment is clearly the wedge, note that explicitly in description."""


# ---------------------------------------------------------------------------
# Method A: scrape competitor customer / case-study pages
# ---------------------------------------------------------------------------

# Regex patterns for extracting company names from /customers pages.
# These are heuristics — HTML varies wildly — so we expect partial success.
COMPANY_PATTERNS = [
    # <img alt="CompanyName logo"> — very common on customer-logo grids.
    # Post-process to strip trailing " logo"/" Logo" word since alt text often embeds it.
    re.compile(r'<img[^>]+alt="([A-Z][A-Za-z0-9&.\- ]{2,30})"', re.I),
    # <h3>Acme Inc</h3> <p>Read case study</p>
    re.compile(r'<h[2-4][^>]*>([A-Z][A-Za-z0-9&.\- ]{2,30})</h[2-4]>\s*<[^>]+>(?:Read|View|See)\s+(?:their\s+)?(?:case\s+study|story)', re.I),
]

_TRAILING_LOGO_RE = re.compile(r'\s+logo\s*$', re.I)

# Iter 39: tighten the noise filter — review widgets / badges / generic UI alt-text
# look like company names to a naive regex. Reject any extracted name containing
# these tokens. Also reject names that start with these words.
_BLACKLIST_TOKENS = {
    "review", "reviews", "rating", "ratings", "star", "stars",
    "icon", "iconset", "badge", "trust", "trusted", "certified", "certificate",
    "shield", "secure", "ssl", "verified", "award", "winner",
    "arrow", "button", "banner", "cta", "hero", "cover",
    "image", "photo", "picture", "img", "screenshot", "thumbnail",
    "play", "pause", "loading", "spinner", "checkmark", "close",
    "search", "menu", "hamburger", "facebook", "twitter", "linkedin",
    "instagram", "youtube", "tiktok", "github",
    "open", "available", "visit", "report", "guide", "ebook",
    "download", "free", "demo", "sample", "trial", "pricing",
    "blog", "news", "press", "media", "podcast", "webinar",
    "five", "four", "three",  # "Five star" patterns
    # Generic site nav / single-word page labels:
    "home", "about", "contact", "products", "product", "services",
    "service", "solutions", "company", "team", "careers", "jobs",
    "login", "signup", "support", "help", "faq", "docs", "documentation",
    # Listicle / aggregator titles that DDG sometimes returns as "company names":
    "growing", "startups", "batch", "best", "top", "list", "fastest",
    "leading", "popular", "new", "featured", "meet", "introducing",
    "directory", "marketplace", "platform", "ranking",
}

_NUM_PREFIX_RE = re.compile(r'^[0-9]')  # "100+ companies" etc

CUSTOMER_PATHS = (
    "/customers", "/case-studies", "/clients", "/who-uses",
    # Iter 42 (issue 6): wider net
    "/case-study", "/customer-stories", "/success-stories", "/our-customers",
    "/used-by", "/trusted-by", "/companies", "/our-clients",
    "/about/customers", "/why-us/customers",
)

# Common selectors for actual customer-logo containers — when present, scope
# the search to JUST these rather than scanning the whole page.
CUSTOMER_LOGO_SELECTORS = (
    '[class*="customer" i]',
    '[class*="client" i]',
    '[class*="logo-grid" i]',
    '[class*="logo-cloud" i]',
    '[class*="logos" i]',
    '[class*="trusted" i]',
    '[class*="brands" i]',
    '[class*="press" i]',
    '[id*="customer" i]',
    '[id*="client" i]',
    '[id*="trusted" i]',
)


_NEWS_HEADLINE_RE = re.compile(
    r"\b(raises?|raised|funded|acquires?|acquired|launches?|launched|"
    r"announces?|announced|unveils?|debuts?|hires?|appoints?|"
    r"closes?|closed|secures?|secured|valuation|seed round|series\s+[a-e])\b",
    re.I,
)
_DOLLAR_AMOUNT_RE = re.compile(r"\$\s*\d+(\.\d+)?\s*(M|B|K|million|billion)?", re.I)


def _merge_prospects(method_lists: list[list[dict]], target_count: int) -> list[dict]:
    """Merge the discovery methods' outputs, collapsing near-duplicate company names
    (W2-4: 'Acme'/'Acme Corp'/'acme.com' are ONE prospect — exact-lowercase dedup
    couldn't see it). First occurrence wins, so method priority (A before B before
    C/D/E) is preserved. Caps at 2x target_count like the old inline loop."""
    from sources import collapse_near_dupes
    flat = [c for lst in method_lists for c in lst]
    return collapse_near_dupes(flat, max_out=target_count * 2)



# Domains that are never a prospect, however good the page title looks. A federal statistics
# agency, an NGO data project, a think tank, a wiki and a trade publication are not companies
# an operator can sell to — and the shipped Customer Universe listed all five as "real
# companies matching the ICP". `discover` already applies this judgement in the other
# direction (StellarAlbedo was correctly demoted for resolving to newsweek.com); this is the
# same rule on the customer side.
_NON_PROSPECT_TLDS = (".gov", ".gov.uk", ".edu", ".ac.uk", ".mil", ".int")
_NON_PROSPECT_HOSTS = frozenset((
    "ourworldindata.org", "thebreakthrough.org", "wikipedia.org", "wikimedia.org",
    "newsweek.com", "forbes.com", "reuters.com", "bloomberg.com", "techcrunch.com",
    "medium.com", "substack.com", "linkedin.com", "indeed.com", "glassdoor.com",
    "ziprecruiter.com", "crunchbase.com", "statista.com", "researchgate.net",
    "sciencedirect.com", "iea.org", "irena.org", "worldbank.org", "oecd.org",
))
# Trade press and job boards give themselves away in the host itself.
_NON_PROSPECT_RE = re.compile(
    r"(?:^|\.)[a-z0-9-]*(?:news|magazine|journal|gazette|herald|wiki)\.|"
    r"(?:^|\.)(?:press|blog|jobs|careers|times)\.", re.I)


def _is_prospect_domain(domain: str | None) -> bool:
    """Could a company at this domain plausibly BUY something? Institutions cannot."""
    d = (domain or "").strip().lower()
    if not d:
        return False
    d = re.sub(r"^https?://", "", d).split("/")[0]
    d = d[4:] if d.startswith("www.") else d
    if any(d == t.lstrip(".") or d.endswith(t) for t in _NON_PROSPECT_TLDS):
        return False
    try:
        from sources import root_domain
        root = root_domain(d) or d
    except Exception:
        root = d
    if root in _NON_PROSPECT_HOSTS or d in _NON_PROSPECT_HOSTS:
        return False
    return not _NON_PROSPECT_RE.search(d)


# Page furniture, job titles and category phrases that a title-split mints as a "company".
_PAGE_FURNITURE = frozenset((
    "homepage", "home page", "home", "about", "about us", "contact", "contact us",
    "index", "products", "our products", "solutions", "services", "our services",
    "overview", "login", "sign in", "search", "blog", "news", "careers", "jobs",
))
_JOB_TITLE_RE = re.compile(
    r"^(?:senior |junior |lead |chief |head of |vp of |vice president |director|manager|"
    r"engineer|analyst|president|officer)\b|"
    r"\b(?:director|manager|engineer|analyst|specialist|coordinator|officer),\s",
    re.I)
# A company name is a noun phrase. A sentence has a verb and function words in the middle.
_SENTENCE_RE = re.compile(
    r"\b(?:is|are|was|were|to|when|why|how|what|that|which|from|with|about|okay|get|gets)\b",
    re.I)


def _is_plausible_company_name(name: str) -> bool:
    """Iter 39: stricter junk filter — kills review widget alt-text masquerading as customer names.
    cycle22: also reject DDG news-headline titles like 'Nexxa.ai Raises $4.4M in Pre' that
    leaked through after title-splitting on common separators."""
    n = (name or "").strip()
    if len(n) < 4 or len(n) > 40:
        return False
    if n.upper() == n or n.lower() == n:
        # All-caps usually means filename/abbreviation; all-lowercase means generic word
        return False
    if _NUM_PREFIX_RE.match(n):
        return False
    lowered = n.lower()
    tokens = set(re.findall(r"[a-z]+", lowered))
    if tokens & _BLACKLIST_TOKENS:
        return False
    # Need at least one vowel
    if not re.search(r"[aeiou]", lowered):
        return False
    # cycle22: news-headline rejection
    if _NEWS_HEADLINE_RE.search(n) or _DOLLAR_AMOUNT_RE.search(n):
        return False
    # cycle22: trailing word must be a real word, not a truncation like "in Pre"
    if re.search(r"\b(in|of|to|for|at|by|the|a|an)\s*$", n, re.I):
        return False
    # A search-result title is not a company name. Measured on job d62bc04f: "Homepage"
    # (eia.gov), "Director, Solar Development" (a job posting), "Installed solar energy
    # capacity" (a chart) and an essay headline were all shipped as "real companies matching
    # the ICP". The checks above test the SHAPE of the string; these test what it SAYS.
    if lowered in _PAGE_FURNITURE:
        return False
    if _JOB_TITLE_RE.search(n):
        return False
    if _SENTENCE_RE.search(n):
        return False
    # "Installed solar energy capacity" is a chart label, not a company. A company name
    # capitalises its words; a category phrase capitalises only the first.
    words = n.split()
    if len(words) >= 3 and sum(1 for w in words if w[:1].isupper()) * 2 < len(words):
        return False
    # A release note is not a company. "Atualização do Produto 2.32" (modoenergy.com) shipped
    # as a company name; no company name carries a version number.
    if re.search(r"\b\d+\.\d+\b|\bv\d+\b", n, re.I):
        return False
    return True


def _scrape_competitor_customers(domain: str, max_companies: int = 20) -> list[dict]:
    """
    Iter 38: now uses scrape.http (cached + throttled) and falls through to
    extruct's JSON-LD walk for higher-recall extraction (catches schema.org
    `Organization.subOrganization`/`subjectOf` patterns that regex misses).
    """
    cache_key = f"cust_univ:customers:{domain}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached[:max_companies]

    from scrape.http import request as _http_request
    from scrape import structured as _structured

    domain = domain.lower().replace("https://", "").replace("http://", "").rstrip("/")
    results: list[dict] = []
    seen = set()

    def _add(name: str, src: str, url: str):
        n = _TRAILING_LOGO_RE.sub("", (name or "")).strip()
        if not _is_plausible_company_name(n):
            return
        if not _is_prospect_domain(url) and not _is_prospect_domain(domain):
            return          # a .gov / NGO / trade-press page is not a prospect
        key = n.lower()
        if key in seen:
            return
        seen.add(key)
        results.append({"name": n, "source": src, "evidence_url": url, "evidence_competitor": domain})

    for path in CUSTOMER_PATHS:
        url = f"https://{domain}{path}"
        r = _http_request("GET", url, timeout=8, allow_redirects=True)
        if r is None or r.status_code != 200 or len(r.text) < 500:
            continue

        # 1. Iter 39: scope extraction to actual customer-logo containers via
        #    selectolax CSS selectors. Cuts false-positives drastically.
        scoped_html = _extract_customer_logo_sections(r.text)

        # 2. Regex over scoped HTML (falls back to full HTML if no section matched)
        target_html = scoped_html if scoped_html else r.text
        for pat in COMPANY_PATTERNS:
            for m in pat.finditer(target_html):
                _add(m.group(1), "competitor-customers", url)
                if len(results) >= max_companies:
                    break
            if len(results) >= max_companies:
                break

        # 3. JSON-LD walk — Organization.subOrganization, ItemList items, etc
        try:
            blob = _structured.extract(r.text, base_url=url)
            for item in blob.get("json_ld", []):
                _walk_json_ld_for_orgs(item, _add, url)
        except Exception:
            pass
        if len(results) >= max_companies:
            break

    cache_put(cache_key, results)
    return results[:max_companies]


def _extract_customer_logo_sections(html: str) -> str:
    """
    Iter 39: use selectolax CSS selectors to extract just the bits of HTML
    that are actually customer-logo grids. Returns the concatenated HTML of
    matching sections, or empty string if no good selector matched.
    """
    if not html:
        return ""
    try:
        from selectolax.parser import HTMLParser
        tree = HTMLParser(html)
        chunks = []
        for sel in CUSTOMER_LOGO_SELECTORS:
            for node in tree.css(sel):
                # Skip if section is huge — likely matched something too generic
                outer = node.html or ""
                if len(outer) < 80000:
                    chunks.append(outer)
        return "\n".join(chunks)[:120000]  # cap total
    except Exception:
        return ""


def _walk_json_ld_for_orgs(node, add_fn, url: str):
    """Recursively pull org names from a JSON-LD subtree."""
    if isinstance(node, dict):
        t = node.get("@type") or ""
        ts = [t] if isinstance(t, str) else (t if isinstance(t, list) else [])
        if any("Organization" in str(x) or "Corporation" in str(x) for x in ts):
            name = node.get("name")
            if isinstance(name, str):
                add_fn(name, "competitor-customers-jsonld", url)
        for v in node.values():
            if isinstance(v, (dict, list)):
                _walk_json_ld_for_orgs(v, add_fn, url)
    elif isinstance(node, list):
        for item in node:
            _walk_json_ld_for_orgs(item, add_fn, url)


# ---------------------------------------------------------------------------
# Method B: LLM-generated ICP → DDG search → candidate extraction
# ---------------------------------------------------------------------------
def _build_icp(
    product_summary: str,
    features: list[str],
    differentiators: list[dict],
    competitor_customers: list[str],
) -> dict:
    """Ask LLM for a structured ICP + search queries to find real companies."""
    diffs_text = "\n".join(f"  - {d.get('feature', '')}" for d in (differentiators or [])[:5]) or "  (none identified)"
    cust_text = ", ".join(competitor_customers[:15]) if competitor_customers else "(none scraped)"
    result = call_json(
        system="You profile ideal customer segments concretely. Return only JSON.",
        user=ICP_PROMPT.format(
            product_summary=product_summary[:600],
            features="\n".join(f"  - {f}" for f in (features or [])[:8]) or "  (none listed)",
            differentiators=diffs_text,
            competitor_customers=cust_text,
        ),
        max_tokens=1500,  # iter 43 (issue K): bumped 900→1500 — ICP summary was truncating mid-sentence
    )
    # cycle31-r2 (BUG C): parse_error used to return {} → ICP entirely empty.
    # Now return a minimal heuristic skeleton so downstream segment-labeling and
    # report rendering still have something to work with.
    if "_parse_error" in result:
        log.warning("[customer_universe] ICP LLM parse_error — returning heuristic skeleton")
        return _build_icp_skeleton(product_summary)

    # cycle30: backstop buyer_role — LLM consistently returned null. Derive a
    # heuristic role from the product description if missing.
    if not (result.get("buyer_role") or "").strip():
        result["buyer_role"] = _derive_buyer_role_heuristic(product_summary, result.get("industry") or "")
    # cycle31-r2 (BUG C): if the LLM returned a malformed dict that PARSED as a
    # non-empty dict but with all fields blank/null, treat it like parse_error
    if not any((result.get(k) or "").strip() if isinstance(result.get(k), str) else result.get(k)
               for k in ("industry", "company_size_employees", "icp_summary")):
        log.warning("[customer_universe] ICP LLM returned all-empty fields — falling back to skeleton")
        skeleton = _build_icp_skeleton(product_summary)
        # preserve buyer_role we just derived above
        skeleton["buyer_role"] = result.get("buyer_role") or skeleton["buyer_role"]
        return skeleton
    return result


def _build_icp_skeleton(product_summary: str) -> dict:
    """cycle31-r2: emergency fallback when LLM ICP call fails. Produces a minimal
    skeleton so downstream pipeline doesn't see an empty dict."""
    role = _derive_buyer_role_heuristic(product_summary, "")
    return {
        "industry": "Industry not extracted by LLM — see venture description",
        "company_size_employees": "see venture description for stated band",
        "company_size_revenue": "",
        "buyer_role": role,
        "tech_maturity": "medium",
        "geographic_markets": ["US"],
        "key_pains_that_map": [],
        "search_queries_to_find_them": [],
        "icp_summary": f"Heuristic ICP placeholder — buyer: {role}. ICP LLM step failed; operator should validate venture description against this skeleton.",
        "_skeleton": True,
    }


# cycle31 (round 2): rules ordered MOST-SPECIFIC FIRST. Generic terms like
# "growth" must come AFTER specific terms like "corporate card / spend management"
# so a fintech venture mentioning "high-growth companies" doesn't get tagged
# as a marketing tool. Earlier ordering put marketing rule before finance,
# causing fintech_b2b to mis-classify as VP Marketing.
_BUYER_ROLE_HEURISTIC_RULES = [
    # (keyword regex, default buyer role)
    # SPECIFIC FIRST: industry-defining nouns
    (re.compile(r"\b(cyber.insurance|cybersecurity insurance|risk.monitoring|attack.surface|underwriting)", re.I), "COO / CFO / Director of IT-Security (SMB)"),
    (re.compile(r"\b(corporate card|spend management|ap automation|accounts payable)", re.I), "CFO / Controller / Head of Finance"),
    (re.compile(r"\b(observability|apm|distributed tracing|sre|devops)", re.I), "VP Engineering / Head of Platform"),
    (re.compile(r"\b(siem|soar|soc platform|threat.intel|ciso)", re.I), "Chief Information Security Officer"),
    (re.compile(r"\b(emr|ehr|practice management|clinical workflow|patient portal)", re.I), "Chief Medical Officer / Practice Owner"),
    (re.compile(r"\b(restaurant|pos|kitchen|qsr|food service)", re.I), "Owner-Operator / Director of Operations (restaurant)"),
    (re.compile(r"\b(payroll|hris|onboarding|hr|people ops)", re.I), "Founder/CEO or Head of People (SMB)"),
    (re.compile(r"\b(sales engagement|cadence|outbound automation|prospecting)", re.I), "VP Sales / Head of Revenue Operations"),
    (re.compile(r"\b(corporate learning|l&d|upskilling|talent development|lms)", re.I), "Chief Learning Officer / Director of L&D"),
    # cycle31-r3 (round 4): freight/logistics MUST come before legal/contract,
    # because freight venture descriptions often mention "contract management"
    # for freight contracts → was misclassifying as General Counsel.
    (re.compile(r"\b(freight|3pl|trucking|carrier|broker|load.board|tms)", re.I), "Director of Logistics / VP Operations (freight/3PL)"),
    (re.compile(r"\b(supply chain|logistics|warehouse|fleet|fulfillment)", re.I), "VP Operations / Head of Supply Chain"),
    (re.compile(r"\b(construction|aec|gc firm|project management.*construction)", re.I), "VP Operations / Project Executive"),
    (re.compile(r"\b(legal|in[- ]house counsel|paralegal|general counsel)", re.I), "General Counsel / Head of Legal"),
    (re.compile(r"\b(real estate|property|landlord|tenant|leasing)", re.I), "Director of Real Estate / Asset Manager"),
    (re.compile(r"\b(sleep|wellness|mental health|wellbeing|cbt)", re.I), "Head of Benefits / VP People Operations"),
    # GENERIC LAST: catch-all category words
    (re.compile(r"\b(insurance|policy|premium|broker|claims)", re.I), "VP Insurance Operations / Head of Underwriting"),
    (re.compile(r"\b(security|compliance|grc|infosec|cybersecurity)", re.I), "Chief Information Security Officer"),
    (re.compile(r"\b(finance|accounting|ledger|invoicing|controller|cfo)", re.I), "Controller / VP Finance"),
    (re.compile(r"\b(healthcare|clinical|patient|provider)", re.I), "Chief Medical Officer / VP Clinical Operations"),
    (re.compile(r"\b(api|developer tools|sdk|database|infrastructure|platform engineer)", re.I), "VP Engineering / Director of Platform"),
    (re.compile(r"\b(customer support|help desk|ticketing)", re.I), "VP Customer Success / Head of Support"),
    (re.compile(r"\b(sales|crm|pipeline)", re.I), "VP Sales / Head of Revenue Operations"),
    (re.compile(r"\b(marketing automation|email automation|funnel)", re.I), "VP Marketing / Head of Growth"),
]


def _derive_buyer_role_heuristic(product_summary: str, industry: str) -> str:
    """Last-resort: scan venture text for category cues, return a reasonable buyer role."""
    haystack = f"{product_summary} {industry}".lower()
    for pat, role in _BUYER_ROLE_HEURISTIC_RULES:
        if pat.search(haystack):
            return role
    return "Head of [function — needs validation interview]"


VERTICAL_SEEDS = {
    # Iter 40 (#2b): vertical-aware seed lists for narrow B2B verticals where
    # DDG returns 0 candidates. Each seed is a known directory page that
    # lists buyers in that vertical. The fetcher pulls the page (live → Wayback
    # fallback) and lets extruct/regex pull company names.
    "employer_wellness": {
        "tags": ["wellness", "wellbeing", "mental health", "sleep", "employer benefit", "hrtech"],
        "directories": [
            "https://www.shrm.org/topics-tools/news/benefits-compensation",
            "https://www.welltok.com/customers/",
        ],
        "icp_query_seeds": [
            "list of fortune 500 companies offering mental health benefits",
            "companies offering sleep coaching as employee benefit",
            "us employers with wellness program 200-2000 employees",
        ],
    },
    "shopify_dtc_brands": {
        "tags": ["shopify", "dtc", "ecommerce", "direct-to-consumer", "ecommerce analytics"],
        "directories": [
            "https://apps.shopify.com/popular",
        ],
        "icp_query_seeds": [
            "fastest growing shopify dtc brands under 5M revenue",
            "indie shopify brands list 2024",
        ],
    },
    "saas_b2b_general": {
        "tags": ["b2b saas", "saas", "enterprise software"],
        "directories": [],
        "icp_query_seeds": [
            "list of seed funded b2b saas startups 2024",
            "y combinator b2b saas batch winter 2024",
        ],
    },
}


def _vertical_for(profile: dict) -> dict | None:
    """Match the venture's profile to a vertical-seeds bucket. Heuristic by keyword."""
    text = " ".join([
        (profile.get("category") or ""),
        (profile.get("summary") or ""),
        (profile.get("business_model") or ""),
        (profile.get("apparent_target_customer") or ""),
    ]).lower()
    best = None
    best_hits = 0
    for key, seeds in VERTICAL_SEEDS.items():
        hits = sum(1 for tag in seeds["tags"] if tag in text)
        if hits > best_hits:
            best_hits = hits
            best = (key, seeds)
    return best


def _seed_companies_from_directories(directories: list[str], max_per_dir: int = 12) -> list[dict]:
    """
    Iter 40 (#2c hybrid): fetch each directory page (live → Wayback fallback),
    walk JSON-LD + regex for company names.
    """
    if not directories:
        return []
    from scrape.http import request as _http_request
    from scrape.wayback import fetch_via_wayback
    from scrape import structured as _structured

    out: list[dict] = []
    seen = set()
    for url in directories[:5]:
        html = None
        # Live first
        r = _http_request("GET", url, timeout=10, allow_redirects=True)
        if r is not None and r.ok and len(r.text) > 500:
            html = r.text
        else:
            # Wayback fallback
            try:
                html = fetch_via_wayback(url, timeout=15)
            except Exception:
                continue
        if not html:
            continue
        # Extract org names via JSON-LD walk
        try:
            blob = _structured.extract(html, base_url=url)
            for item in blob.get("json_ld", []):
                _walk_json_ld_for_orgs(item, lambda name, src, evidence: _seed_add(out, seen, name, "vertical-seed", url), url)
        except Exception:
            pass
        # Plus img-alt regex on scoped sections
        scoped = _extract_customer_logo_sections(html) or html
        for pat in COMPANY_PATTERNS:
            for m in pat.finditer(scoped[:80000]):
                _seed_add(out, seen, _TRAILING_LOGO_RE.sub("", m.group(1)).strip(), "vertical-seed", url)
                if len(out) >= max_per_dir * len(directories):
                    break
            if len(out) >= max_per_dir * len(directories):
                break
    return out


def _seed_add(out: list, seen: set, name: str, source: str, evidence_url: str):
    if not _is_plausible_company_name(name):
        return
    key = name.lower()
    if key in seen:
        return
    seen.add(key)
    out.append({
        "name": name,
        "source": source,
        "evidence_url": evidence_url,
    })


def _g2_reviewer_companies(competitor_brand: str, max_results: int = 10) -> list[dict]:
    """
    Iter 42 (issue 6): G2 review pages list reviewer companies in metadata.
    Search for the competitor's G2 page via search cascade, fetch (live or
    Wayback), pull `Reviewed by [CompanyName]` lines via JSON-LD or text.
    Returns list of {name, source, evidence_url}.
    """
    from scrape import search as _search
    from scrape.http import request as _http_request
    from scrape.wayback import fetch_via_wayback
    from scrape import structured as _structured

    out: list[dict] = []
    seen = set()
    hits = _search.search(f"{competitor_brand} reviews", max_results=4, prefer_domain="g2.com")
    for h in hits[:3]:
        url = h.get("url") or ""
        if "g2.com" not in url:
            continue
        # Try live first; G2 often blocks scrapers, so fall to Wayback
        r = _http_request("GET", url, timeout=8)
        html = ""
        if r is not None and r.ok and len(r.text) > 500:
            html = r.text
        else:
            try:
                html = fetch_via_wayback(url, timeout=10) or ""
            except Exception:
                continue
        if not html:
            continue
        # G2 review pages embed reviewer company in JSON-LD as Review.author.affiliation
        try:
            blob = _structured.extract(html, base_url=url)
            for item in blob.get("json_ld", []):
                _walk_for_company_affiliations(item, out, seen, url)
        except Exception:
            pass
        if len(out) >= max_results:
            break
    return out[:max_results]


def _walk_for_company_affiliations(node, out: list, seen: set, url: str):
    """Walk JSON-LD looking for Review.author.affiliation.name patterns."""
    if isinstance(node, dict):
        if node.get("@type") == "Review":
            author = node.get("author") or {}
            if isinstance(author, dict):
                aff = author.get("affiliation") or {}
                if isinstance(aff, dict):
                    name = aff.get("name")
                    if name and _is_plausible_company_name(name):
                        key = name.lower()
                        if key not in seen:
                            seen.add(key)
                            out.append({"name": name, "source": "g2-reviewer", "evidence_url": url})
        for v in node.values():
            if isinstance(v, (dict, list)):
                _walk_for_company_affiliations(v, out, seen, url)
    elif isinstance(node, list):
        for item in node:
            _walk_for_company_affiliations(item, out, seen, url)


def _crunchbase_wayback_search(query: str, max_results: int = 8) -> list[dict]:
    """
    Iter 40 (#2c): Crunchbase doesn't allow live scraping (403/Cloudflare),
    but Wayback has cached HTML pages. Search "site:crunchbase.com {query}"
    via search cascade, then fetch each result via Wayback.
    """
    from scrape import search as _search
    from scrape.wayback import fetch_via_wayback
    from scrape import structured as _structured
    from urllib.parse import urlparse

    out: list[dict] = []
    seen = set()
    hits = _search.search(query, max_results=max_results, prefer_domain="crunchbase.com")
    for h in hits[:max_results]:
        url = h.get("url") or ""
        if "crunchbase.com" not in url:
            continue
        # Crunchbase live blocks scrapers; go straight to Wayback
        try:
            html = fetch_via_wayback(url, timeout=15)
        except Exception:
            html = None
        if not html or len(html) < 500:
            # Use the title as a fallback name
            title = h.get("title") or ""
            name = title.split("-")[0].strip()
            if _is_plausible_company_name(name):
                _seed_add(out, seen, name, "crunchbase-search", url)
            continue
        try:
            blob = _structured.extract(html, base_url=url)
            cn = blob.get("company_name")
            if cn:
                _seed_add(out, seen, cn, "crunchbase-wayback", url)
        except Exception:
            pass
    return out


def _ddg_find_companies(queries: list[str], max_results_per_query: int = 5) -> list[dict]:
    """
    Iter 38: now uses scrape.search cascade (Brave → SearXNG → ddgs) so we no
    longer return 0 candidates when DDG is rate-limited.
    """
    from scrape import search as _search

    candidates = []
    seen_domains: set[str] = set()
    for q in queries[:6]:
        hits = _search.search(q, max_results=max_results_per_query)
        hits = _search.filter_aggregator_domains(hits)
        for h in hits:
            url = h.get("url") or ""
            if not url:
                continue
            try:
                netloc = urlparse(url).netloc.lower()
            except Exception:
                continue
            if netloc in seen_domains:
                continue
            seen_domains.add(netloc)
            title = h.get("title") or ""
            name = title.split("|")[0].split("—")[0].split("-")[0].strip()
            # Iter 40: apply same junk filter as competitor-customers extraction —
            # otherwise DDG hits like "36 Growing B2B SaaS Startups (2024)" or
            # "Meet the YC Winter 2024 Batch" pollute the universe with listicles.
            if not _is_plausible_company_name(name):
                continue
            candidates.append({
                "name": name,
                "domain": netloc,
                "source": f"search:{h.get('source','?')}",
                "evidence_url": url,
                "evidence_snippet": (h.get("snippet") or "")[:180],
            })
    return candidates


def _label_segments(companies: list[dict], venture_summary: str = "") -> list[dict]:
    """Group the merged company list into LLM-labeled segments."""
    # Iter 43 (issue E): lowered from 3→2 (needs at least 2 to label distinct segments)
    if len(companies) < 2:
        return []
    sample = companies[:20]
    blob = "\n".join(
        f"  - {c.get('name')} ({c.get('domain', '')}): {c.get('evidence_snippet', '')[:80]}"
        for c in sample
    )[:2500]
    result = call_json(
        system="You label B2B customer segments. Return only JSON.",
        user=SEGMENT_LABEL_PROMPT.format(companies=blob, venture_summary=(venture_summary or "(not provided)")[:600]),
        max_tokens=2500,  # cycle22: 1500→2500 — segment description was truncating mid-word "environmen…"
    )
    if "_parse_error" in result:
        return []
    return result.get("segments", [])


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def build_customer_universe(
    profile: dict,
    competitors: list[dict],
    differentiators: list[dict] | None = None,
    target_count: int = 30,
) -> dict:
    """
    Spec step 5. Compose methods A + B to produce a real-company ICP universe.
    cycle31-r3 (Bug G): wrap whole function so that even if every method
    returns 0 (or an inner step throws), we return a useful skeleton with
    ICP fields + heuristic buyer_role, never `{}`.
    """
    try:
        return _build_customer_universe_inner(profile, competitors, differentiators, target_count)
    except Exception as e:
        log.warning("[cust_univ] inner build failed (%s) — returning skeleton with ICP only", e)
        return _customer_universe_skeleton(profile, error=str(e))


def _customer_universe_skeleton(profile: dict, error: str = "") -> dict:
    """cycle31-r3: emergency fallback when build_customer_universe fails entirely.
    Still produces a usable ICP block downstream consumers can render."""
    summary = profile.get("summary") or ""
    industry = profile.get("category") or "unknown"
    role = _derive_buyer_role_heuristic(summary, industry)
    band = profile.get("target_employee_band") or ""
    return {
        "count": 0,
        "icp_summary": (
            f"Heuristic ICP: {industry} firms"
            + (f" ({band})" if band else "")
            + f" — buyer: {role}. (Customer-universe scrapers found no matches; operator should validate.)"
        ),
        "icp_details": {
            "industry": industry,
            "company_size_employees": band,
            "buyer_role": role,
            "_skeleton": True,
        },
        "companies": [],
        "segments": [],
        "sources": [],
        "methods_used": [],
        "_skeleton_reason": error or "all 5 discovery methods returned 0 candidates",
    }


def _build_customer_universe_inner(
    profile: dict,
    competitors: list[dict],
    differentiators: list[dict] | None = None,
    target_count: int = 30,
) -> dict:
    competitor_names: list[str] = []

    # --- Method A: scrape top 4 competitor customer pages ---
    method_a: list[dict] = []
    for c in (competitors or [])[:4]:
        domain = c.get("domain")
        if not domain:
            continue
        found = _scrape_competitor_customers(domain, max_companies=10)
        for f in found:
            method_a.append(f)
            if f.get("name"):
                competitor_names.append(f["name"])
    log.info("[cust_univ] method A scraped %d candidate companies from competitor pages", len(method_a))

    # --- Method B: LLM ICP + DDG search ---
    # cycle29: prepend the operator-stated target employee band to the summary so
    # the LLM doesn't drift to a wider/narrower band based on competitor patterns.
    summary_with_anchor = profile.get("summary", "") or ""
    target_band = (profile.get("target_employee_band") or "").strip()
    target_buyer = (profile.get("apparent_target_customer") or "").strip()
    if target_band:
        summary_with_anchor = (
            f"OPERATOR-STATED TARGET CUSTOMER BAND: {target_band} (HONOR THIS — do not widen or narrow). "
            + summary_with_anchor
        )
    if target_buyer:
        summary_with_anchor = f"OPERATOR-STATED BUYER: {target_buyer}. " + summary_with_anchor
    icp = _build_icp(
        product_summary=summary_with_anchor,
        features=profile.get("core_features", []),
        differentiators=differentiators or [],
        competitor_customers=competitor_names[:15],
    )
    # cycle29: post-LLM override — if operator stated a band, force it
    if isinstance(icp, dict) and target_band:
        icp["company_size_employees"] = target_band
    queries = icp.get("search_queries_to_find_them", []) if isinstance(icp, dict) else []
    method_b = _ddg_find_companies(queries) if queries else []
    log.info("[cust_univ] method B (ICP+DDG) found %d candidates", len(method_b))

    # --- Iter 40 Method C: vertical-aware seed list (#2b) ---
    method_c: list[dict] = []
    vert = _vertical_for(profile)
    if vert and (len(method_a) + len(method_b) < target_count):
        vkey, vseeds = vert
        log.info(f"[cust_univ] method C: vertical='{vkey}' — pulling seed directories + extra ICP queries")
        method_c.extend(_seed_companies_from_directories(vseeds.get("directories", [])))
        # Plus the vertical's curated ICP query seeds
        if vseeds.get("icp_query_seeds"):
            method_c.extend(_ddg_find_companies(vseeds["icp_query_seeds"], max_results_per_query=4))
        log.info(f"[cust_univ] method C found {len(method_c)} candidates")

    # --- Iter 40 Method D: Crunchbase via Wayback (#2c) ---
    # Iter 43: 30s strict timeout — Wayback often hangs minutes on Crunchbase URLs.
    method_d: list[dict] = []
    if isinstance(icp, dict) and icp.get("industry") and (len(method_a) + len(method_b) + len(method_c) < target_count):
        cb_query = (icp['industry'] or '')[:120] + " startups"  # cap query length
        log.info(f"[cust_univ] method D: Crunchbase via Wayback for '{cb_query[:80]}'")
        try:
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_crunchbase_wayback_search, cb_query, 8)
                try:
                    method_d = fut.result(timeout=30)
                    log.info(f"[cust_univ] method D found {len(method_d)} candidates")
                except FutureTimeoutError:
                    log.warning(f"[cust_univ] method D timed out (30s)")
                    fut.cancel()
        except Exception as e:
            log.warning(f"[cust_univ] method D failed: {e}")

    # --- Iter 42 Method E: G2 reviewer companies for top 2 competitors (issue 6) ---
    # Iter 42-cycle13: wrapped with 30s timeout per competitor — Wayback fetches
    # were hanging the pipeline (4+ min) when G2 was being scraped.
    method_e: list[dict] = []
    if (len(method_a) + len(method_b) + len(method_c) + len(method_d) < target_count):
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
        for comp in (competitors or [])[:2]:
            brand = comp.get("brand")
            if not brand:
                continue
            log.info(f"[cust_univ] method E (G2 reviewers) for '{brand}'")
            try:
                with ThreadPoolExecutor(max_workers=1) as ex:
                    fut = ex.submit(_g2_reviewer_companies, brand, 8)
                    try:
                        method_e.extend(fut.result(timeout=20))
                    except FutureTimeoutError:
                        log.warning(f"[cust_univ] method E timed out for {brand} (20s)")
                        fut.cancel()
            except Exception as e:
                log.warning(f"[cust_univ] method E failed for {brand}: {e}")
            if len(method_e) >= target_count:
                break

    # --- Merge + dedupe (W2-4: near-dupe collapse, not just exact-lowercase) ---
    merged = _merge_prospects([method_a, method_b, method_c, method_d, method_e],
                              target_count)

    # Keep top target_count by: method A (high-confidence) first, then B, then C/D
    def _bucket(c):
        s = c.get("source", "")
        return (
            0 if s == "competitor-customers" else
            1 if s.startswith("search:") or s == "ddg+icp" else
            2 if s == "vertical-seed" else
            3
        )
    merged.sort(key=_bucket)
    final = merged[:target_count]

    # cycle31-r3 (Bug G): if EVERY method returned 0 candidates, return a useful
    # skeleton with ICP + heuristic role rather than an empty companies list
    # that downstream segment-scoring will fail to use.
    if not final:
        log.warning("[cust_univ] all 5 methods returned 0 candidates — falling back to ICP-only skeleton")
        skeleton = _customer_universe_skeleton(profile, error="all 5 methods returned 0 candidates")
        # Preserve any ICP fields the LLM did produce
        if isinstance(icp, dict) and icp.get("industry"):
            skeleton["icp_details"].update({
                k: v for k, v in icp.items()
                if k in ("industry", "company_size_employees", "company_size_revenue",
                         "buyer_role", "tech_maturity", "key_pains_that_map")
            })
            if icp.get("icp_summary"):
                skeleton["icp_summary"] = icp["icp_summary"]
        return skeleton

    # --- Segments ---
    # Iter 41: lowered from 4 → 2 so thin verticals still get segment scoring downstream
    venture_brief = f"{profile.get('name', '?')}: {(profile.get('summary') or '')[:300]} | Business model: {profile.get('business_model', '?')}"
    segments = _label_segments(final, venture_summary=venture_brief) if len(final) >= 2 else []

    sources = sorted({c.get("source") for c in final if c.get("source")})

    methods_used = []
    if method_a: methods_used.append("competitor-scrape")
    if method_b: methods_used.append("ddg+icp")
    if method_c: methods_used.append(f"vertical-seed:{vert[0]}" if vert else "vertical-seed")
    if method_d: methods_used.append("crunchbase-wayback")
    if method_e: methods_used.append("g2-reviewers")

    return {
        "count": len(final),
        # iter 43-cycle20: derive icp_summary from short categorical fields if LLM
        # truncated before reaching it (the long narrative field is now placed LAST)
        # cycle21: only include sub-parts that are non-empty so we don't ship
        # strings like "(1,000-50,0, ) — buyer:" with stray punctuation
        "icp_summary": (
            (icp.get("icp_summary") or "").strip()
            or (lambda d: (
                (lambda industry, size_parts, buyer: (
                    " — ".join(filter(None, [
                        f"{industry} firms" + (f" ({', '.join(size_parts)})" if size_parts else "") if industry else None,
                        f"buyer: {buyer}" if buyer else None,
                    ])) or ""
                ))(
                    (d.get("industry") or "").strip(),
                    [s for s in [(d.get("company_size_employees") or "").strip(), (d.get("company_size_revenue") or "").strip()] if s],
                    (d.get("buyer_role") or "").strip(),
                )
            ))(icp)
            if isinstance(icp, dict) else ""
        ),
        "icp_details": {k: v for k, v in icp.items() if k != "search_queries_to_find_them"} if isinstance(icp, dict) else {},
        "companies": final,
        "segments": segments,
        "sources": sources,
        "methods_used": methods_used,
    }
