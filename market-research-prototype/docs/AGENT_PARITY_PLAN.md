# Castor ⇄ Manus — Capability Parity & Agent Test Plan

> Goal: match every Manus capability that matters for market research, and **prove
> it with specific, runnable tests** — not vibes. This is the test spec the agents
> must pass before we claim parity.

Derived from a live head-to-head (restaurant-inventory-SaaS prompt, identical input).
Where Manus beat us, the gap becomes a test that must go green.

---

## Part 1 — Capability parity matrix

Status: ✅ have & tested · 🟡 partial / not wired · ❌ missing

| # | Manus capability (observed) | Castor component | Status | Gap |
|---|---|---|---|---|
| C1 | **Task decomposition** — splits request into N visible steps | `plan_research` + crew | 🟡 | not driving the live pipeline |
| C2 | **Live web search** — finds current sources | `tools/scrape.web_search` | 🟡 | works but discovery underuses it |
| C3 | **Page fetch + extraction** — read MarketMan pricing page | `scrape.fetch_page` / `extract_prices` / `extract_structured` | 🟡 | not consistently invoked |
| C4 | **Multi-source synthesis** | `synthesis_agent` | ✅ | wire into report |
| C5 | **Constraint adherence** — keep "$99/mo" | — | ❌ | Castor silently re-priced to $25 |
| C6 | **Live-grounded numbers** — 1M restaurants, real | `sizing/*` bottom-up | ❌ | used stale 166k + broken formula |
| C7 | **Number reconciliation** — formula = result | `validate_numbers` | ❌ | gate misses formula/segment mismatches |
| C8 | **Citation/provenance per claim** | Evidence `source` | ✅ | keep |
| C9 | **Long-horizon autonomy** (5–15 min unattended) | harness `run_agent` | ✅ | keep |
| C10 | **Self-correction / iteration** | harness loop | 🟡 | no retry-on-low-confidence |
| C11 | **Artifact generation** (report/slides/PDF) | report.html + PDF | ✅ | add slides later |
| C12 | **Recency** (2025/2026 funding, news) | `trend` + search | 🟡 | no funding-data source |

**The four ❌/weak items (C5, C6, C7, C10) are the priority — they're correctness, not polish.**

---

## Part 2 — The agent test suite (golden tasks)

Each test: a fixed input, the agent/skill under test, and a binary pass criterion.
Lives in `test_agent_parity.py` (new). Run: `pytest test_agent_parity.py -v`.

### Group A — Tool-level capabilities (atomic)

| ID | Test | Input | Pass criterion |
|---|---|---|---|
| A1 | Live web search returns results | `web_search("restaurant inventory software")` | ≥5 results, each with url + title |
| A2 | Page fetch works | `fetch_page("https://www.marketman.com/pricing")` | non-empty text, status 200 |
| A3 | Price extraction | `extract_prices(marketman_html)` | ≥1 numeric price, each with currency |
| A4 | Structured extraction | `extract_structured(html)` | returns JSON-LD or og: fields when present |
| A5 | Trend signal | `google_trends_rising("restaurant POS")` | numeric slope returned or graceful skeleton |

### Group B — Agent behaviors (autonomous)

| ID | Test | Input | Pass criterion |
|---|---|---|---|
| B1 | Planner decomposes | venture desc | `plan_research` returns ≥3 ordered, roster-valid steps |
| B2 | Dynamic crew dispatch | digital SaaS | local_market_agent NOT selected; market_scan IS |
| B3 | Competitor discovery breadth | "AI customer support" | `multi_strategy_discovery` ≥10 competitors via ≥3 strategies |
| B4 | Direct/indirect classification | same | each competitor tagged direct/indirect/adjacent |
| B5 | Worker isolation | kill one worker | crew still returns a brief (no crash) |
| B6 | Synthesis cites agents | crew run | brief names which agent surfaced each point |

### Group C — Correctness gates (the ❌ items — must go green)

| ID | Test | Input | Pass criterion |
|---|---|---|---|
| **C5-T** | **Constraint adherence** | venture with "$99/month" | report pricing references $99; if it recommends another price it must **explicitly reconcile** (a `price_reconciliation` field), never silently drop |
| **C6-T** | **Live-grounded bottom-up** | restaurant SaaS, US | bottom-up unit count is **live-sourced + cited**, within 2× of ground truth (~700k independent); no hardcoded constant |
| **C7-T** | **Formula reconciliation** | any sizing payload | for every figure, `eval(formula_inputs) ≈ value_usd` (±5%); segments sum to their labeled parent (±2%) |
| **C7-T2** | **Flag–data agreement** | sizing with 3 TAM methods filled | no "0/3 methods filled" flag fires |
| **C10-T** | **Self-correction** | force a low-confidence sub-result | agent retries once before accepting |

### Group D — Head-to-head (vs Manus, scored)

Run `benchmarks/run_manus_bench.py` on 4 queries; paste Manus output; score the
10-dim rubric in `manus_comparison.md`. Parity target: **Castor ≥ Manus on
provenance, method-fit, triangulation, validation, defensibility; within 1 pt on
web-recency.**

---

## Part 3 — Build plan to close the gaps (ordered)

Each item ships with its test from Part 2.

1. **Fix C5 (constraint adherence) — ½ day.** Thread the intake's `pricing` field
   into `market_sizing` + `four_ps`; add `price_reconciliation` when the model
   recommends a different price. Test: **C5-T**.
2. **Fix C7 (number reconciliation) — 1 day.** Extend `validate_numbers` with:
   formula-evaluator (parse `formula`, recompute, compare), segmentation-sum check,
   flag-vs-data check. Tests: **C7-T, C7-T2**.
3. **Fix C6 (live-grounded bottom-up) — 1–2 days.** Add a `census_business_counts`
   tool (Census CBP / County Business Patterns by NAICS) so unit counts are
   live-sourced, not hardcoded; route bottom-up through it. Test: **C6-T**.
4. **Wire the crew into the pipeline (C1–C4) — 1 day.** `run_plan` calls
   `run_research_crew(dynamic=True)` for the discover/demand/pricing phases; feed
   Evidence into the deterministic sizing/narration. Tests: **B1–B6**.
5. **Add C10 self-correction — ½ day.** In the harness loop, retry a sub-result
   once when its Evidence is skeleton/low-confidence. Test: **C10-T**.
6. **Add funding-data source (C12) — 1 day.** Wrap an OSS/free funding signal
   (e.g. OpenVC / Wikidata `P2769` budget / news search) into `firmographic`.

Total: ~5–6 days to close the correctness gaps + wire the agents in.

---

## Part 4 — How we'll know we've reached parity

- **All of Group C green** (the correctness gates) — non-negotiable; these are the
  bugs the live comparison exposed.
- **Groups A & B green** — capability coverage.
- **Group D**: Castor wins the rigor dimensions and is within 1 pt on recency across
  all 4 benchmark queries, on ≥3 independent runs (LLM variance).
- **Regression**: the existing ~190-test suite stays green throughout.

Parity is not "feels as good as Manus" — it's **this suite passing**, re-run every
cycle, with the Manus head-to-head as the external check.
