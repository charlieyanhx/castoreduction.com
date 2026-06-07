# Number-Derivation Remediation Plan (cycle35)

> Driven by the **independent** methodology audit (a different agent than the one that
> built the pipeline). Each fix has explicit SUCCESS and FAILURE criteria and a
> verification method. We fix **one at a time**, verify, commit, then move on.
> Rule: a fix is not "done" until its success test is green AND its failure test
> would have caught the old behavior.

Core problem the audit found: **the tested system ≠ the executed system.** The good
machinery exists but `run_plan` routes around it. These fixes close that gap.

---

## F1 — The validation gate must actually withhold blocked numbers (CRITICAL)

**Problem.** `publishable` is set but never read; `result["market_sizing"]=sizing` is
unconditional (plan.py ~1016). A failed gate renders the TAM/SAM/SOM anyway, under a
banner. "Block" = annotation, not gating.

**Fix.** When `validate_numbers` fails (`publishable=False`), the rendered report must
**suppress the numeric TAM/SAM/SOM block** and show only the failure notice + the
blocks. The data may remain in the JSON (for debugging) but the *headline figures must
not render* as if trustworthy. Implement by passing `publishable` to the template and
gating the numeric block on it (report.html), and surfacing `publishable` in the API
render context.

**SUCCESS criteria**
- A report whose sizing fails the gate renders **no** 24pt TAM/SAM/SOM numbers — only
  the "numbers failed validation" notice listing the blocks.
- A passing report renders the numbers normally.
- New test `test_report_render.py::test_blocked_sizing_hides_numbers`: render the
  template with `market_sizing.validation.passed=False` → assert the formatted TAM
  currency string is **absent** and the failure notice is **present**.
- Companion: `test_..._passing_sizing_shows_numbers` → numbers present when passed.

**FAILURE criteria (must have caught the old behavior)**
- Running the new test against the *current* template must FAIL (numbers present despite
  `passed=False`). If it passes on the old template, the test is too weak — strengthen it.

**Verification.** `pytest test_report_render.py -q` green; manual: load a known
gate-failing job's report.html in the browser, confirm no headline numbers.

---

## F2 — Deterministic LLM calls: temperature=0 + seed (CRITICAL)

**Problem.** No backend sets temperature/seed (llm.py). Same input → different numbers;
only a 7-day cache hides it. Measured 47% TAM / 27× SOM swings.

**Fix.** Set `temperature=0` (and a fixed `seed` where the provider supports it) on all
backends in `llm.py` (Gemini, Groq, Anthropic). Keep it overridable via param for
creative tasks (narration) if needed, but default deterministic for numeric calls.

**SUCCESS criteria**
- `test_llm_determinism.py::test_payload_sets_temperature_zero` (mock the HTTP layer):
  assert each backend's request body includes `temperature == 0` (and `seed` when set).
- Live (opt-in, `CASTOR_LIVE_TESTS=1`): call `estimate_market_size`-style prompt twice
  with cache bypassed → identical TAM mid (±0%). Document the result.

**FAILURE criteria**
- The payload test must FAIL on current code (no temperature set today).
- If live double-run still diverges >5% with temperature=0, the fix is insufficient —
  note provider nondeterminism and add seed / document the residual.

**Verification.** `pytest test_llm_determinism.py -q`; one live double-run logged.

---

## F3 — Route to the grounded / scale-adaptive path as the PRIMARY derivation (CRITICAL)

**Problem.** `run_plan` always calls `estimate_market_size` (LLM-only). The scale router
+ `size_hyperlocal/regional/national_digital` + grounded bottom-up are dead code in
production; grounding only fires on a `$/mo` regex.

**Fix.** In `run_plan`, after `classify_market_scale`, **dispatch on
`scale_decision["sizing_skill"]`**:
- hyperlocal / regional → trade-area skills when a location is available; else fall back
  with an explicit low-confidence flag.
- national_digital / global → `estimate_market_size` BUT with the bottom-up method
  replaced by `grounded_bottom_up` (Census × ARPU) whenever a unit basis is resolvable,
  not only when the user typed a price.
- Always attach which path produced the headline + its `n_independent`.

**SUCCESS criteria**
- `test_plan_routing.py`: with `classify_market_scale` mocked to each scale, assert
  `run_plan`'s sizing step calls the matching skill (spy/patch), not unconditionally
  `estimate_market_size`.
- For a digital venture, the bottom-up figure's `origin` is `census` (not `llm`) when a
  NAICS resolves → triangulation reports `n_independent >= 2`.

**FAILURE criteria**
- The routing test must FAIL on current code (always calls estimate_market_size).
- If dispatch makes a previously-passing benchmark case error, that's a regression —
  the fallback must degrade gracefully (skeleton + flag), never crash.

**Verification.** `pytest test_plan_routing.py -q`; one live digital run shows
`n_independent>=2` when network (Census) is available.

---

## F4 — Kill the triangulation "converged" theatre in the report (HIGH)

**Problem.** Default path = 3 LLM draws → one origin (engine correctly says
"single_source"), but the LLM "3-method triangulation, converged" narrative still
renders, contradicting the engine.

**Fix.** Render **only** the engine's `triangulation.confidence` / `flag` /
`n_independent`. Delete/replace the LLM-authored "reconciliation" string in the TAM
section; the table title already says "3 Estimation Methods" (not "Independent").

**SUCCESS criteria**
- Template test: with `triangulation.confidence="single_source"`, the rendered TAM
  section contains "single source / not triangulated" and does **not** contain the
  word "converged" or "3 independent".

**FAILURE criteria**
- Test FAILS on current template (the LLM reconciliation string is present).

**Verification.** `pytest test_report_render.py -q`.

---

## F5 — Stop "self-heal" laundering incoherent numbers (HIGH)

**Problem.** `triangulate_sizing` overwrites an LLM value with its formula-derived value
so the gate passes (plan.py ~366-371). It manufactures coherence.

**Fix.** On a >10× value/formula mismatch, **do not rewrite** — flag the figure and let
the gate block it (F1 then withholds it). Only reconcile within a small tolerance
(rounding), never gross rewrites.

**SUCCESS criteria**
- `test_triangulate_sizing.py::test_gross_mismatch_not_silently_rewritten`: a figure
  whose formula computes 50× its value is left flagged (and the gate marks unpublishable),
  not overwritten to match.

**FAILURE criteria**
- Test FAILS on current code (value gets rewritten, gate passes).

**Verification.** `pytest` for that test + confirm F1 then hides it.

---

## F6 — Relabel circular checks as "internal consistency," add one external check (HIGH)

**Problem.** Formula-recon / segmentation-sum / SAM-waterfall compare LLM output to LLM
output; several pass by construction. They're labeled "validation."

**Fix.** Rename these to **"internal consistency checks"** in code + report. Add at least
one **external** cross-check that can actually fail — e.g. compare the LLM top-down TAM
against the Census/BLS grounded bottom-up; flag if they diverge > Nx (a real,
independent disagreement).

**SUCCESS criteria**
- `validate_numbers` output distinguishes `internal_consistency` from `external_checks`.
- New external check fires (warn/block) when grounded bottom-up and LLM top-down diverge
  beyond threshold; test with crafted inputs.

**FAILURE criteria**
- If no input can ever make the external check fail, it's not external — fix it.

**Verification.** `pytest test_validate_reconciliation.py -q`.

---

## Order & status
1. **F1** gate withholds — _next_
2. **F2** determinism
3. **F5** stop self-heal (pairs with F1)
4. **F4** kill convergence theatre
5. **F3** route to grounded primary (biggest)
6. **F6** external check

Each: write the FAILURE test first (must fail on current code), implement, make SUCCESS
test green, run full regression, commit. No fix is "done" until its test would have
caught the bug it fixes.
