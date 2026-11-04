# Castor Functionality Audit — "Can it vet *any* business?"

**Goal of this audit:** prove (or disprove) that Castor produces a *defensible, coherent,
correctly-modeled* research report across the full range of businesses a user could submit —
not just the one venture (a Silver Lake cafe) we hand-tuned. "Comprehensive vetting ability"
means: for an arbitrary venture, every published number is (a) the right *kind* of number for
that business, (b) plausible in magnitude, (c) internally consistent with the rest of the
report, and (d) either sourced or honestly labeled as an estimate.

This is an **output audit** (does the report make sense?), not a unit-test audit (those exist
and pass). It is run by an **independent agent**, never the one that wrote the feature — the
pattern that has repeatedly caught what self-review missed.

---

## 1. The bar (pass criteria for the whole tool)

Castor is "comprehensive" when, across the test corpus below:

- **No fabricated headline numbers.** Every TAM/SAM/SOM/price/CLV is sourced, computed, or
  labeled UNSOURCED. (Confirmed once for the cafe; must hold across models.)
- **Right model, right unit.** A per-visit business is never priced as a monthly subscription;
  a marketplace's economics are take-rate-based, not seat-based; etc.
- **Magnitudes survive a human read.** No "$5K/yr SOM for a cafe", no "$505M national TAM for a
  hyperlocal venture", no "$38/drink".
- **Internal coherence.** A figure (CAC, CLV, competitor count, SOM) has *one* value across all
  sections. No section contradicts another.
- **Honest degradation.** When data is thin or a step fails, the report says so (banner / labels)
  rather than presenting $0 / blank / noise as a finding.
- **Reproducibility.** Same input twice → same headline numbers (temp=0/seed), or the variance is
  disclosed.

Quantified gate: **≥ 90% of rubric cells PASS, zero CRITICAL findings open**, across the corpus.

---

## 2. What actually changes Castor's behavior (the audit axes)

The report is produced by a deterministic DAG whose **routers** adapt the analysis. The audit must
exercise every router branch — that is where "works for the cafe" stops generalizing.

| Axis | Router / code | Branches today | Known blind spot |
|---|---|---|---|
| **Business model** | `business_model.classify_business_model` | `transactional`, `subscription` only; **ambiguous → subscription** | marketplace, ecommerce/DTC product, B2B services/agency, ad-supported/free, franchise, hardware, hybrid — all collapse to 2 buckets |
| **Market scale** | `skills/sizing/classify.classify_market_scale` | hyperlocal, regional, national_digital, (global) | regional multi-unit & global not stress-tested |
| **Physical vs digital** | `market_scale.signals.is_physical` → `size_hyperlocal` vs national | physical (Census/OSM grounded) vs digital | digital pure-play sizing relies entirely on LLM |
| **Data availability** | discover / competitor_pricing / taste decode | rich vs thin | thin-data path must degrade honestly, not fabricate |
| **Geography** | Census/ACS/BLS (US-only) + Nominatim/OSM (global) | US grounded vs intl fallback | non-US ventures have no Census/BLS grounding |

---

## 3. Test corpus (the ventures to run)

~16 ventures chosen so **every router branch and every blind spot is hit at least once**. Each is a
one-line prompt with a stated price/scale so the routers have something to bite on.

### Transactional / physical (grounding + retail spine)
1. **Cafe — Silver Lake LA** (baseline; regression anchor) — hyperlocal, transactional, US.
2. **Restaurant — Austin TX**, $35/cover — hyperlocal, capacity-side SOM should bind.
3. **Boutique gym** — Denver, $30/drop-in + membership — tests transactional-vs-membership edge.
4. **Hair salon** — Brooklyn, $80/cut — hyperlocal services.
5. **Food truck** — Portland, $12/plate — mobile (no fixed trade-area address).

### Subscription / SaaS (original spine — must not regress)
6. **B2B SaaS** — team analytics, $40/seat/mo — national_digital, subscription.
7. **Consumer membership app** — fitness, $15/mo — subscription, digital.

### The blind-spot models (expected to expose gaps)
8. **Marketplace** — local services two-sided, 15% take rate — *not* seat or per-visit; tests take-rate economics (likely mis-modeled today).
9. **Ecommerce / DTC product** — skincare brand, $45/unit, ships nationally — one-time purchase + repeat rate + AOV (currently maps to transactional per-unit — verify it's sane).
10. **B2B services / agency** — design studio, project-based $20k engagements — neither subscription nor per-unit retail.
11. **Ad-supported / free** — content app, $0 to user, monetizes via ads — no "price"; tests whether the pricing/economics spine degrades gracefully.
12. **Franchise / multi-unit** — 10-location regional chain — regional scale, unit + system economics.
13. **Hardware** — IoT device $199 + $5/mo — hybrid one-time + subscription.

### Stress / robustness
14. **Novel / thin-data** — niche deep-tech with ~no competitors — tests honest "data-thin" vs fabrication.
15. **Non-US** — cafe in Lisbon, Portugal — no Census/BLS; tests intl fallback labeling.
16. **Vague prompt** — "an app for dogs" (no price, no geo, no model) — tests safe defaults + how loudly it asks for / flags missing inputs.

---

## 4. Per-report rubric (scored for every venture)

Each report is scored PASS / WARN / FAIL on each row, with a one-line justification + the offending
quote. (WARN = plausible but weak; FAIL = a human would distrust the report.)

| # | Claim type | PASS criteria |
|---|---|---|
| R1 | **Market scale routing** | classified to the right scale; sizing method matches (trade-area vs national) |
| R2 | **TAM** | right method for scale; magnitude plausible; sourced or labeled; calc shown |
| R3 | **SAM/SOM** | funnel ordered (SOM≤SAM≤TAM); SOM plausible for *one* unit; capacity-anchored where physical |
| R4 | **Business model routing** | classified correctly; **not** defaulted to subscription wrongly |
| R5 | **Pricing** | right *unit* (per drink / per seat / per project / take-rate); coherent with stated price + WTP |
| R6 | **Unit economics** | right *framework* (retail margin/covers vs CLV:CAC vs take-rate); no SaaS framing on non-SaaS |
| R7 | **Financials** | revenue basis matches model (covers×check vs subscribers×ARPU vs GMV×take) |
| R8 | **Competitors** | real + relevant + geographically/categorically appropriate; count consistent everywhere |
| R9 | **Consumer / WTP** | right unit; coherent band (no fake single-point band); connects to price |
| R10 | **Differentiators** | grounded in actual competitor data, not generic |
| R11 | **Viability** | score's reasoning consistent with the numbers; cites the right model |
| R12 | **Integrity panel** | validation gate fired; provenance honest; "sourced N/N" accurate |

---

## 5. Cross-cutting invariants (scored once per report, across sections)

These are the failures that *only* appear when you read the whole report — the ones that bit us.

- **C1 — Single-value coherence.** Pick CAC, CLV, competitor count, SOM, price. Each must have ONE
  value across exec summary, body, takeaways, viability, citations. (Caught: CAC stated $15/$76/$110.)
- **C2 — No model bleed.** A transactional report contains no "subscription/per account/CLV:CAC/
  churn" in its *primary* spine (secondary lines allowed if labeled secondary).
- **C3 — No noise-as-analysis.** Sections with degenerate inputs (0% cluster variance, $0 EVC,
  "0 brands" personas) are hidden or explicitly flagged, not rendered as findings.
- **C4 — No impossible claims.** "100% of foot traffic", "capture 100% of market", negative
  break-even presented as success, etc.
- **C5 — Reproducibility.** Run each venture **twice**; headline TAM/SOM/price must match (or the
  delta is disclosed by the stability check).
- **C6 — Honest degradation.** Force a thin-data / failed-step case; confirm the "Incomplete —
  regenerate" banner fires and names what failed.
- **C7 — No fabrication.** No invented citations (e.g., crediting a PSM that errored), no sourced
  claim resting on an LLM guess.

---

## 6. Known-gap regression checks (from this session)

Explicitly re-verify the issues already found, on the relevant corpus members:

- **G1** WTP unit follows model (per-drink not /mo) — ventures 1–5, 9.
- **G2** TAM computes (no $0) under LLM flakiness — all; rely on thinking-off + retry.
- **G3** Hidden-constant disclosure (break-even costs, ±band, funnel clamp) — all.
- **G4** Competitor pricing **unit mismatch** (bean-bag subscription $ used for per-cup) — venture 1,2,9.
  *This is the open one — the per-cup competitor scraper isn't built yet.*
- **G5** Dropped 4Ps sections — all; confirm retry holds.

---

## 7. Methodology (how the audit is run)

1. **Generate** each corpus venture twice (reproducibility) → 32 reports. Capture `result.json` +
   rendered `report.html`.
2. **Independent auditors, fan-out by dimension.** A panel of agents (NOT the builder) each take a
   rubric group (sizing / pricing+economics / competitors+consumer / coherence+integrity) and read
   the real artifacts, emitting structured findings (quote + severity + suggested fix).
3. **Adversarial verification.** Every finding is checked by a skeptic agent that tries to *refute*
   it (default: not-a-bug unless a paying human would distrust the report) — kills plausible-but-wrong
   flags before they reach the fix list.
4. **Score + aggregate.** Per-venture scorecard (rubric × cross-cutting), rolled into a tool-level
   matrix: rows = ventures, columns = rubric/invariant cells.
5. **Triage.** CRITICAL (fabrication / data-loss / wrong-model headline) → blocks "comprehensive";
   HIGH → fix before claiming coverage; MED/LOW → backlog.

This is automatable as a single orchestrated workflow: `generate → (read ∥ by dimension) →
verify each finding → synthesize matrix`. Cost scales with corpus size; the LLM cache makes
re-runs cheap.

---

## 8. Deliverable

- `docs/AUDIT_RESULTS.md` — the scorecard matrix (16 ventures × 12 rubric + 7 invariant cells),
  every FAIL with the offending quote and a proposed fix, ranked by severity.
- A one-line verdict: **% cells PASS, # CRITICAL open** vs the §1 gate.
- A prioritized fix backlog (the next cycle of work).

---

## 9. Hypotheses going in (what I expect to fail)

Stating these up front so the audit is falsifiable, not a victory lap:

- **H1 (likely FAIL):** marketplace (8), B2B services (10), ad-supported (11) get mis-modeled — the
  classifier has no branch for them and defaults to subscription. Expect SaaS framing on non-SaaS.
- **H2 (likely WARN):** ecommerce/DTC (9) maps to "transactional per-unit" but misses repeat-purchase
  / AOV / shipping economics.
- **H3 (likely FAIL):** competitor pricing unit mismatch (G4) recurs anywhere scraped prices ≠ the
  venture's transaction unit.
- **H4 (likely WARN):** non-US (15) sizing is entirely LLM-estimated with weaker labeling than the
  US Census path.
- **H5 (likely WARN):** franchise/multi-unit (12) — SOM is single-unit; system-level economics absent.

If these hold, "comprehensive vetting" is **not yet true beyond transactional + subscription**, and the
fix backlog is: add classifier branches (marketplace/services/ecommerce/ad-supported) + their
economics, build the per-unit competitor scraper (G4), and strengthen non-US labeling.
