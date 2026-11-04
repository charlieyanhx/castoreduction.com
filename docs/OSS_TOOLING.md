# Castor — Best-in-class OSS for each subsystem

Scouted + verified (GitHub push dates, PyPI versions, licenses live as of 2026-06). Ranked by
impact × effort. "Already installed" verified against this venv.

## The 4 near-free wins (do first — deps already in the tree or trivial)

| # | Subsystem → Castor file | Tool | Why | Effort |
|---|---|---|---|---|
| 1 | LLM JSON reliability → `llm.py:call_json` | **instructor** ★13.2k MIT — *already pinned in requirements, never imported* | Pydantic-validated JSON + auto re-ask; replaces `json_repair`/`_parse_error` salvage | low |
| 2 | web search → `scrape/search.py` | **Tavily** ★1.3k MIT (free 1k/mo, no card) | ends the dead-search roulette; returns pre-cleaned, ranked results. Add as first backend, keep cascade as fallback | low |
| 3 | content extraction/validation → `scrape/structured.py`, `scrape/crawl.py` | **trafilatura** ★6.2k Apache-2.0 — *already installed (2.0.0), bump to 2.1.0* | returns empty on parked/thin pages → a cheap deterministic content-validity gate before price extraction | low |
| 4 | dedup + brand match → `discover.py`, `customer_universe.py` | **RapidFuzz** ★4k MIT | collapses "Calm / Calm.com / Calm Business" (exact-lowercase dedup inflates competitor counts today) | low |

## The semantic gate (zero new dep — reuses the model `clustering.py` already loads)

| 5 | category relevance → `sources.py:validate_domain`, `competitor_pricing.py` | **fastembed** bge-small ★3k Apache-2.0 | cosine category-relevance check before scraped prices feed the PSM median — kills wrong-category/parked pollution that substring matching misses | medium |

## Second wave (higher impact, real dependency weight)

| 6 | deep research → `harness/agent.py` | **GPT-Researcher** ★27.9k Apache-2.0 | 18 pluggable retrievers (fixes dead search) + LiteLLM core; adopt as a per-worker "research limb", keep Castor's crew/Evidence/validation | medium |
| 7 | **local reviews** (Google Maps / Yelp / Trustpilot) → `tools/geo.py`, retire Trustpilot Playwright | **outscraper-python** ★MIT (paid) + **yelpapi** (free baseline) | ratings + review_count + review text for nearby competitors — *this is the "more sources per industry" ask*; upgrades names-only OSM competitors | low–med |
| 8 | clustering + axis labels → `clustering.py` | **BERTopic** ★7.7k MIT | deletes bespoke cluster+UMAP+LLM-label code, removes a flaky LLM call (tune for small 5–15 competitor sets) | medium |
| 9 | sentiment → reddit/customer-voice | **transformers + optimum/ONNX** (cardiffnlp twitter-roberta) | VADER misreads sarcasm/slang; transformer sentiment is far better (model download cost) | medium |
| 10 | PDF render → `api.py` | **WeasyPrint** ★9.3k BSD | honors the `@page` page-number footer Chromium's `page.pdf()` silently drops (needs native pango libs → medium here) | medium |
| 11 | Census ACS/FIPS → `tools/geo.py` | **datamade/census** ★681 BSD + **python-us** | replaces hand-rolled Census URLs (pairs with the FCC-FIPS bypass already wired) | low |

## Also flagged
- **Exa** (semantic search, find-similar for competitor expansion) — strong P7-alt to Tavily.
- **SearXNG self-hosted** — root-cause fix for search rate-limits (AGPL, run as a service, ops burden).
- **LiteLLM canonical `BerriAI/litellm`** — verify we're not pinned to a non-canonical fork.
- **Camoufox** — stealth fetcher for the few bot-protected hosts (Trustpilot etc.).
- Port Van Westendorp curve math (don't import) — removes LLM-asserted price points.

## Honest caveat from the scout
"Already installed" holds for **instructor / trafilatura / fastembed**, but NOT weasyprint / rapidfuzz
/ tldextract in this venv — so those need a real `pip install` (WeasyPrint also needs native pango).
