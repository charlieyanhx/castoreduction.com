# Castor ⇄ Manus Improvement Loop

> The operating process: treat **Manus as the adversarial benchmark** and **Castor
> as the generator we keep improving**. Every round: run both, find where Manus
> wins, make Castor match-or-beat, then **prove the fix on an unseen case** while
> re-benchmarking Manus. No overfitting to a single prompt.

This is a GAN-style loop. Manus is the discriminator we must beat; the holdout
pool is the test set we never train against.

---

## The loop (one round)

```
1. RUN BOTH      same prompt → Castor report + Manus report
2. DIFF          line up numbers, sections, claims side by side
3. JUDGE         score 10-dim rubric; per dim decide: Castor / Manus / tie
4. FIX           for every dim where Manus wins by ≥1: root-cause →
                 make Castor MATCH or BEAT it → add a regression test
5. OOS VALIDATE  run both on an UNSEEN holdout case; confirm the fix
                 generalizes (Castor didn't just overfit) AND re-score Manus
6. LOG           append the round to the ledger (scores + fixes + OOS verdict)
```

**Superiority rule (step 3):** a side "wins" a dimension only by **≥1 point** on the
5-point rubric. Ties don't trigger fixes. Manus winning a dimension on ≥2 of 3 runs
(LLM variance) makes it a real gap, not noise.

**Match-or-beat rule (step 4):** "match" = Castor reaches Manus's level on that dim;
"beat" = Castor adds rigor Manus lacks (provenance, validation, reconciliation).
Prefer beat. Every fix ships with a test so the gap can't silently reopen.

**Overfitting guard (step 5):** the fix is only accepted if it improves the
**holdout** case too. If it helps the diagnosis case but not holdout, it's
overfit — revert or generalize.

---

## The rubric (from `manus_comparison.md`)

provenance · method-fit · triangulation · validation · numeric-specificity ·
competitor-coverage · consumer-insight · defensibility · web-recency · reproducibility.
Weighted; Castor's thesis is to win the rigor dims and stay within 1 pt on recency.

---

## Case pools — diagnosis vs. holdout (NEVER cross them)

**Diagnosis pool** — we run, diff, and *fix against* these. Spans the scale taxonomy.

| ID | Venture | Scale | Used in |
|----|---------|-------|---------|
| D1 | B2B SaaS, restaurant inventory mgmt, US, $99/mo | national_digital | Round 1 ✅ |
| D2 | A specialty coffee shop at a real Austin address | hyperlocal | — |
| D3 | A regional chain of 8 boutique fitness studios, Austin | regional | — |

**Holdout pool** — validation ONLY. Never tune to these. Different verticals so a
fix can't be vertical-specific.

| ID | Venture | Scale | Status |
|----|---------|-------|--------|
| H1 | B2B SaaS for dental-practice scheduling, US, $149/mo | national_digital | unseen |
| H2 | A food truck in Portland, OR | hyperlocal | unseen |
| H3 | A DTC supplement brand, US, $39/mo | national_digital (ecom) | unseen |
| H4 | A global developer-observability SaaS | global_digital | unseen |

Rotate one holdout in per round; once a holdout has been used to *diagnose*, it
graduates to the diagnosis pool and a fresh holdout is added.

---

## Ledger

### Round 1 — D1 (restaurant inventory SaaS) · 2026-06-05

**Diff (key numbers):**
| | Castor | Manus | Winner |
|---|---|---|---|
| Restaurant count | 166k ❌ | 412k / 1.1M (live, cited) | **Manus** |
| TAM | $1.8B (broken bottom-up) | $1.31B (math checks) | **Manus** (reconciles) |
| SAM | $600M | $490M | tie (close) |
| Kept $99 input | dropped → $25 ❌ | kept + validated | **Manus** |
| Math reconciles | no (166k×$50≠$845M) | yes | **Manus** |
| Depth (CLV/EVC/4Ps/funding) | rich | thin | **Castor** |
| Triangulation approach | 3-method | 1-method | **Castor** |
| Validation gate | yes (missed bugs) | none | **Castor** (now fixed) |
| Web recency | stale | current 2025 | **Manus** |
| Reproducibility | deterministic | varies | **Castor** |

**Manus won:** provenance, live-counts, constraint-adherence, reconciliation, recency.

**Fixes shipped (match-or-beat):**
- **C5** constraint adherence — `reconcile_pricing` keeps/​reconciles the stated price (was silently dropped). *beat* (Manus kept it; we keep + explain the gap). Test: `test_plan_sizing_gate.py::TestPricingReconciliation`.
- **C6** live-grounded counts — `census_business_counts` + `grounded_bottom_up` (412k live, cited). *match* Manus's live data. Test: `test_bottom_up.py`.
- **C7** reconciliation — `validate_numbers` formula-evaluator + segmentation-sum; *beat* (Manus reconciles implicitly; we enforce + block). Test: `test_validate_reconciliation.py`.

**Status:** C5/C7 wired into live pipeline + report; C6 tool built, routing into live
`market_sizing` still pending. 216 unit tests green.

**OOS validation (step 5) on H1 (dental SaaS, $149/mo) — code-level:**
- C5 ✅ generalizes (`extract_stated_price("$149/month") = 149`)
- C7 ✅ generalizes (formula reconciliation is category-agnostic)
- C6 ❌ **FAILED to generalize** — `census_business_counts(category="dental")` returned
  skeleton because "dental" wasn't in the 9 hardcoded NAICS codes. **Overfit to the
  restaurant case** — caught exactly by the OOS step.

**OOS-driven fix:** added `resolve_naics()` — exact-table → primary-token → **LLM
fallback** so any vertical resolves (dental → 621210). Now C6 generalizes. Test:
`test_bottom_up.py::TestNaicsResolver` (incl. dental end-to-end). 12 bottom-up tests green.

**De-hardcoding (operator directive "dont hardcode"):** removed the hardcoded
`NAICS_BY_CATEGORY` (9 categories) AND `BLS_CEX_ANNUAL_SPEND` (6 categories) tables.
Both are now **generic LLM resolvers** (`resolve_naics`, `resolve_annual_spend`) with
session caches — work for any vertical, need no code edits to add categories. This is
the "code generic, no hardcoded lists" principle.

**Live OOS run — H1 (dental SaaS, $149/mo), 2026-06-05:** ✅ PASSED
- C5: `$149` extracted · classify → `national_digital` (correct)
- C6: NAICS resolved **621210** (Offices of Dentists) generically — no hardcoding
- spend resolver: `$1,174/yr` dental (BLS-grounded)
All via live Gemini, on a vertical never seen in code.

**Round 1 verdict:** ✅ CLOSED. C5/C6/C7 generalize live OOS; overfit removed; no
hardcoded tables remain. (Full report-vs-Manus scoring on H1 is the next round's
in-sample diagnosis when desired.)

**Loop lesson:** OOS testing caught an overfit (hardcoded NAICS) that all in-sample
unit tests missed — and the fix (generic LLM resolution) removed the whole class of
hardcoding. Validates out-of-sample validation as standing process.

---

## How to run a round (commands)

```bash
# Castor side (all diagnosis + the chosen holdout)
python -m benchmarks.run_manus_bench --query Q3      # D1-style digital
# Manus side: paste the same prompt into Manus (browser), save output
# Score: fill the scorecard in manus_comparison.md, append a ledger entry here
```

Automation target: `benchmarks/run_manus_bench.py` already runs Castor's side on the
4 query archetypes; extend it to emit a per-dimension diff stub so scoring is faster.

---

## Definition of "parity reached"

Across **all holdout cases**, on ≥3 runs each: Castor wins or ties every rigor
dimension (provenance, method-fit, triangulation, validation, defensibility,
reconciliation) and is within 1 pt of Manus on web-recency. Until then, the loop runs.
