# R4 fix-order — session handoff prompt

Paste the block below into a fresh session to continue the work.

---

```
Continue the Castor R4 fix-order work in /Users/charlieyan/Downloads/castor-advisories/market-research-prototype (git repo, branch master, all pushed). Castor is a market-research report generator; this program hardens it against the R4 qualitative audit (baseline: 13.5% pass / 53 criticals).

SOURCE OF TRUTH: docs/R4_FIX_ORDER.md — a severity-ranked list of 24 root-cause fixes. Read it first. Ranks 1–22 are fully FIXED (each has a "FIXED" note there). Ranks 23–24 are partially done; the doc records exactly what's left with file locations.

METHODOLOGY (follow it exactly, one fix per commit):
1. MEASURE the defect on the real corpus first (out/wave4_corpus/*.json + matching *.html) — print real before/after numbers, never assert.
2. TDD: write the test in a new test_<name>.py, confirm it FAILS (RED), then implement.
3. Add a deterministic gate to gates.py (invariants are D01…D43; register in the INVARIANTS list; each `check(result_json, html) -> Finding(ok: bool|None, detail)`, None=N/A). Match the gate number to the next free D-id.
4. Run the FULL suite in the background (`python -m pytest -q` — ~4.5 min, must end all-passing; it was 1611 passed, 5 skipped) and only commit when green.
5. Commit with the corpus evidence in the message (conventional-commits, e.g. `fix(rank 23): ...`); attribution is disabled globally so NO Co-Authored-By trailer. Push each commit.
   Watch for the recurring trap: legacy tests/fixtures often encode the OLD behaviour you're repairing — update them to assert the invariant, don't weaken the fix.
   Env: `source .venv/bin/activate` first. No LLM keys here, so tests mock call_json; prompt-anchoring fixes are verified by prompt-capture tests, not corpus gates.

REMAINING WORK (in priority order):
A. Rank 23 residuals (skills/perspective.py:60-68 one WTP band can pool demand/supply/advertiser sides on multi-sided ventures; a few hardcoded "$"/"/mo"; bare-"unit" noun fallback in plan.py _UNIT_DEFAULT_BY_KIND). NOTE: the core cross-surface unit divergence is already 0/16 (D05/D21 gate it) — only these narrower pieces remain, and a wrong unit-scale change can CREATE a report bug, so measure carefully.
B. Rank 24 tail: self-refuting cannot-decode notice (taste.py:271-291); churn 5.0 default (financials.py:202); "b2b"→SaaS anchor substring (macro_anchors.py:361-368); seat/account scale mixing; formula-tokenizer phantom-suffix section block (skills/sizing/validate.py:36-38).
C. THE FINAL MEASUREMENT (highest value, BLOCKED here): regenerate the 16-venture corpus by running the pipeline over each report's profile.summary input (~2h live LLM), then `python gates.py --corpus out/wave4_corpus --gate all` (the D25–D43 gates should flip green), then run benchmarks/r4_panel.js for the qualitative rubric audit vs the 13.5%/53 baseline. This needs ANTHROPIC_API_KEY set — confirm it's present before starting, or tell me it's missing.

Start by reading docs/R4_FIX_ORDER.md, then pick up at item A. Ask me nothing you can determine from the code or corpus; measure, fix, gate, prove, commit.
```

---

## Session ledger (what shipped)

- **Ranks 7–22 fully fixed** — gates D31–D42 added, plus D04/D18 rewrites. Each TDD'd,
  corpus-measured, full-suite-green, committed and pushed.
- **Rank 24 mostly landed** — break-even `ceil()`, dead in-page nav anchors (gate D43,
  16/16 stale corpus), `0.0/day`→`<1/day`, un-numbered "weakest assumptions" heading.
- **Rank 23** — the cross-surface unit divergence is already 0/16 (D05/D21); narrower
  residuals documented.
- **43 gates registered** (was ~22 at session start). Full suite: 1611 passed, 5 skipped.

## The measurement blocker

The definitive R4 pass-rate re-measurement (corpus regen + r4_panel.js) cannot run
without live LLM keys. On the stale pre-fix corpus, the new gates flag the defect surface
across all 16/16 reports (D27/D30/D38 16/16; D25 10/10 national; D32/D33 10/16; D37 12/16;
D43 16/16); each rank's re-render test confirms these flip to PASS on fresh artifacts.
