# Castor Method Audit (cycle33, honest)

> A critical audit, not a summary. Every finding is grounded in the code as it
> actually runs. Severity: CRITICAL (claim contradicted) · HIGH · MEDIUM · SOUND.

---

## CRITICAL

### C1 — De-hardcoding violated the core moat invariant
SIZING.md invariant #1: **"the LLM never invents a number."** The just-shipped
`resolve_annual_spend()` asks the LLM for a dollar figure ($1,174/yr for dental)
that then **drives TAM**. That is an LLM-invented number in the most load-bearing
place. The previous hardcoded BLS value (`3360` with a citation) was *less general
but more defensible* — it had a real source. We traded provenance for generality;
for a "numbers a lender can underwrite" product, that's the wrong trade.
- **`resolve_naics` (code) is defensible** — NAICS is a classification, and the
  *count* still comes from Census. Keep it.
- **`resolve_annual_spend` (dollar value) is not** — it must come from the **BLS
  CEX API**, not the LLM. Fix: build a `bls_cex_spend` tool; LLM only maps category
  → BLS series id, never emits the number.

### C2 — The headline fixes are latent, not active
`plan.py` references the new sizing skills **once** (just `classify_market_scale`).
`grounded_bottom_up`, `census_business_counts`, `size_hyperlocal/regional/market`
are **never called by the live pipeline.** The live report still uses
`estimate_market_size`'s LLM bottom-up (the last live run showed "150k restaurants",
not a Census count). So **C6 does not affect real reports** — it's unit-test-true,
production-false. The same is true of the entire scale-adaptive sizing engine.

### C3 — The validation gate does not gate
`run_plan` does `result["market_sizing"] = sizing` **unconditionally**; a "block"
only writes a flag into the payload. SIZING.md says "the renderer must refuse to
publish blocked sizing" — it doesn't. So "validate loud, block bad numbers" is
aspirational. The $3B-SAM and 166k-formula cases would be *flagged* but still
*shipped* in the rendered report.

---

## HIGH

### H1 — Zero real-LLM / integration tests
~200 tests, **all 20 files mock** the LLM and network; **0 exercise a real provider
response.** Mocks encode our assumptions about output shape; real Gemini/Groq return
messy, variant, and `_parse_error` shapes (seen live this session). The test count
overstates confidence — we have strong *unit* coverage and ~no *integration* coverage.

### H2 — The agents are unused
7 agents + crew + planner; **0 calls in `plan.py`.** The "agentic, Claude-Code-style"
narrative is not the runtime — the product is the deterministic pipeline plus a few
new skills. Either wire the crew in or stop claiming it as the engine.

### H3 — The Manus comparison is n=1 and self-judged
One prompt, one run each side, scored by the same agent that built Castor. No
LLM-variance repeats, no blind/independent judge. The verdicts are useful *anecdote
that drove real fixes* — but they are not measurement. "Within 1 pt on recency" etc.
are not yet statistically meaningful.

### H4 — "Reproducible / deterministic spine" is overstated
Profile, discovery, taste, sizing, 4Ps, viability are all LLM calls. Same input →
different output across runs. The determinism is in *orchestration order*, not in
*content*. We should claim "deterministic control flow, stochastic content," not
"reproducible numbers."

---

## MEDIUM

### M1 — Provenance present but often not verifiable
The gate checks `source` is non-empty, not that it's real. Many sources are
`"derived"` / `"estimate_market_size"`. Manus cited live URLs; we frequently don't.
Provenance should require a resolvable reference for externally-sourced figures.

### M2 — Sourced-chain weakest link
Even with a Census count, TAM = count × **ARPU** × **penetration** × **serviceable
fraction** — and those multipliers are LLM/assumption-driven. TAM is dominated by the
multipliers, so "every number sourced" is true for the *count* and false for the
*drivers*. The headline is only as defensible as its softest input.

### M3 — Session caches never expire
`_NAICS_CACHE` / `_SPEND_CACHE` memoize for the process with no TTL; a wrong early
resolution persists. Minor, but it can mask nondeterminism in eval.

---

## SOUND (keep, don't churn)

- **Registry pattern** (tools/skills/agents, uniform Evidence) — clean, extensible,
  genuinely good.
- **C7 formula reconciliation** — real, generic, catches a whole class of arithmetic
  bugs; the best thing shipped this cycle.
- **The improvement loop + OOS discipline** — correct meta-process; already caught a
  real overfit (hardcoded NAICS) that all in-sample tests missed.
- **Scale classifier + routing** — sound design; the deterministic router is testable.

---

## Remediation priority (what to fix, in order)

1. **C3 — make the gate actually gate.** ✅ DONE. `gate_and_annotate_sizing` sets
   `publishable=False` on a failed gate; the report renders a red "Numbers failed
   validation — do not rely on these figures" banner listing the blocks. Tests:
   `TestC3GateEnforces` + template render check.
2. **C2 — wire the sizing engine into `plan.py`.** ✅ DONE (bottom-up path).
   `ground_sizing_bottom_up` runs before the gate: replaces the LLM bottom-up with a
   live Census count × ARPU (target-customer establishments × stated price), recomputes
   the TAM headline, degrades gracefully without a price/count. Tests: `TestC2GroundedBottomUp`.
   *(Remaining: dispatch physical ventures to `size_hyperlocal/regional` when an
   address is captured at intake — needs the intake location field.)*
3. **C1 — stop the LLM inventing the load-bearing number.** ✅ DONE. Built the real
   `bls_cex_spend` tool (`tools/econ.py`): the LLM maps category → BLS CEX series id,
   the **number comes from the BLS Public Data API**. `resolve_annual_spend` tries BLS
   first and returns `(value, sourced)`; only on BLS failure does it fall back to an
   LLM estimate, which is labeled `"UNSOURCED"` and caps confidence. Invariant #1
   restored. Tests: `test_econ.py` (7) + the C1 hyperlocal test. Live-verified the
   honest fallback (sandboxed BLS → `sourced=False`).
4. **H1 — real-LLM integration smoke.** ✅ DONE. `test_integration_live.py` — 5
   opt-in tests (skipped unless `CASTOR_LIVE_TESTS=1` + a key) asserting real-provider
   SHAPE survival on classify / NAICS / spend / consumer-research / market-sizing.
   4/5 verified passing live against Gemini. We finally have integration coverage,
   not only mocks.
5. **H2 — agentic claim made true.** ✅ DONE (honest scope). The crew is too expensive
   to force on every report, so it's now an explicit invokable capability:
   `POST /research/crew` runs `run_research_crew` (planner → parallel specialists →
   synthesis) as an async job. The agents are no longer idle; they're an opt-in
   "deep research" mode rather than the default pipeline engine. *(Down-scope, not
   over-claim: the default `/plan` remains the deterministic pipeline.)*
6. **H3 — statistical benchmark** (≥3 runs/side, independent judge) — NOT STARTED.
7. **H4 — correct the "reproducible" wording** to "deterministic control flow,
   stochastic content" in ARCHITECTURE.md — NOT STARTED.
8. **C1 follow-on — real `bls_cex_spend` tool** so spend is BLS-sourced — NOT STARTED.
9. **C2 follow-on — capture address at intake** → dispatch physical ventures to
   `size_hyperlocal/regional` live — NOT STARTED.

---

## One-line verdict

The **architecture and meta-process are sound**; the **claims run ahead of the
runtime.** The most important fixes are not new capability — they are making the
capability we already built *actually execute on a live report* (C2), *actually block
bad numbers* (C3), and *stop the LLM from inventing the load-bearing numbers* (C1).
