# Testing Guidelines & Completion Milestones — Harness v2 (P0–P6)

> **Deterministic gate programs — milestones are claimed by a PROGRAM, not an opinion:**
> - **Report quality:** `gates.py` (D01–D14, one detector per historical audit critical; proven
>   by the 21-test seeded-bug suite in `test_gates.py`). `python gates.py --corpus <dir> --gate core`.
> - **Harness build (this plan):** `harness_gates.py` (H01–H20, one check per phase invariant;
>   runner soundness in `test_harness_gates.py`). Each check is **n/a until its phase is built,
>   then pass/FAIL forever** — so the program identifies today's problems now and pinpoints
>   what's missing as each phase lands. Claim a phase with `python harness_gates.py --gate M<k>`
>   (exit 0/1); the `now` gate (H18–H20: depth-1 masking, Evidence envelope, hard-rules-in-gate)
>   is the floor that must never regress.
>
> The LLM audit panel (R4) is reserved for what cannot be deterministic (prose quality).
> Same code/corpus in → same verdict out.
>
> **Harness baseline (2026-07-09, `docs/baselines/harness_baseline.json`):** 6 pass · 3 FAIL ·
> 11 not-built. The three FAILs are the empirical P0 work-list: **H01** 5 tools with thin
> routing docstrings (named in the scorecard), **H02** 59/60 components lack negative scope
> ("Do NOT use when…"), **H13** compaction mention without an anti-thrash guard.

## 0.1 Current deterministic baseline (2026-07-09, latest 16 reports, `--gate all`)
**80% cells pass · 10 blocking failures** → the empirical near-term goals, in fix order:

| Goal | Detector | Failing today | Definition of done |
|---|---|---|---|
| **G1** WTP unit bleed (last `/mo` leak) | D05 | 4 reports (bottle/bowl/drop-in economics but `wtp: /mo` — consumer_research still uses the old unit inference) | D05 = 100% pass on a fresh 16-venture corpus |
| **G2** SAM ≤ TAM on the national path | D04 | 3 reports (SAM 315M > TAM 180M etc. — ordering clamp not applied on one path) | D04 = 100% pass |
| **G3** profitable-at-SOM coherence | D08 | 2 reports (claim true, every scenario negative) | D08 = 100% pass (the SOM-consistency work) |
| **G4** scale-router misroute (agency→hyperlocal) | D07 | 1 report | D07 = 100% pass |
| G5 non-US sources (M6) | D11 | 1 report (Lisbon → US Census/BLS) | D11 = 100% pass |
| G6 provenance everywhere | D12 | pre-feature reports only | D12 = 100% on fresh corpus (already true for new runs) |

Baseline scorecard: `docs/baselines/deterministic_baseline.json`. Re-run after each fix; the
gate output goes in the fix's commit message (Rules of Evidence #1).

Every phase of [CC_HARNESS_PLAN.md](CC_HARNESS_PLAN.md) completes ONLY when its milestone gate
passes. A gate is a **runnable command with a numeric threshold** — never a judgment call.
Tests are written FIRST (red → green), per the repo's TDD rule.

---

## 0.2 Implementation order (dependency-driven; each wave exits through a gate)

| Wave | Work | Why this position | Exit gate |
|---|---|---|---|
| **0** (~½ day) | Fix G1–G4: WTP unit in consumer_research, SAM≤TAM clamp on the national path, profitable-at-SOM reconciliation, agency scale-misroute | Cheapest known bugs; detectors already written; instant measurable win | `gates.py --gate core` ≥95%, D04/D05/D07/D08 = 100% on fresh corpus |
| **1** (1–2 d) | Wire **instructor** into llm.py (already installed) + write the 59 negative-scope docstrings + 5 thin ones + H13 anti-thrash guard | JSON reliability underpins every later integration; descriptions are P0 of the harness plan and improve routing immediately | `harness_gates.py --gate M1` PASS; parse_error rate ~0 in smoke |
| **2** (2–3 d) | Data layer: **Tavily** first search backend, **trafilatura** content gate, **RapidFuzz** dedup, **tldextract**, **fastembed** relevance gate on pricing/competitors. *(User: free TAVILY / CENSUS / BLS keys.)* | The binding constraint on report quality (dead search → LLM-guessed brands) and on M8 fact density; needs Wave 1's reliable JSON | search smoke >0 results on 5 canned queries; D13 still green; relevance-gate unit tests |
| **3** (2–4 d) | Harness P1+P2: RunLedger, transcript, **resume** | Biggest operational pain (every corpus regen this month re-ran finished steps); must exist before expensive premium runs | `harness_gates.py --gate M2` + `--gate M3` PASS; resume re-runs 0 completed steps |
| **4** (3–5 d) | M8 core: deterministic **forecast engine** (also the deep G3 fix), **claim→source store + CitationAgent** (consumes Wave 2 facts), **WeasyPrint** PDF | Premium-parity substance; citation store needs real retrieval; forecast engine needs nothing — could start anytime | `test_forecast_model.py` reconciliation; fact-density counter; ≥20pp PDF renders |
| **5** | P3 (tiering + read-parallel scheduler) → P4 (CASTOR.md, reminders, compaction) → P5 (SKILL.md, spawn contracts) | Cost/speed then context-scale; none block earlier waves | M4 → M5 → M6 gates |
| **6** | P6 premium multi-agent: research crew stage + verifier panel + effort knob | Needs Opus-class budget + Wave 3's resume protecting expensive runs; verifier design matures from R4 experience | M7 gate; then the R4 panel confirms ≥90%/0-critical |

Re-run the R4 LLM panel once after Wave 2 (cheap checkpoint) and once at M7 (the final claim).

## 0. Test taxonomy (the five rings)

| Ring | What | Runs | Command |
|---|---|---|---|
| **R1 Unit** | pure functions: routers, resolvers, gates, ledger ops | every commit (<30s) | `.venv/bin/python -m pytest -q` |
| **R2 Contract** | tool/skill/agent registry invariants: Evidence shape, arg models, descriptions, depth limits | every commit | `pytest test_contracts.py -q` (new) |
| **R3 Corpus regression** | regenerate the 16-venture corpus; structural checks (completeness, geo, SOM-match, no blanks, no /mo bleed) | per phase + nightly | `python /tmp/audit/gen.py && python /tmp/audit/stage1_check.py` |
| **R4 Audit panel** | independent multi-agent auditors score the rubric; adversarial verify | per phase end | `Workflow(audit_workflow.js)` |
| **R5 Live E2E** | browser drives the workspace: submit → watch stream → read report | per phase end + before any release | Chrome extension / Playwright script |

**Standing thresholds (apply to every milestone, no exceptions):**
- R1+R2 green: **100%** (no skips, no xfails without a linked issue)
- R3 corpus: **≥15/16 clean** (only the intake-rejected vague prompt may fail), **0 blank reports**, **0 SOM mismatches**, **0 `/mo` labels on per-unit spines**
- Reproducibility: same input twice → **ΔTAM = ΔSOM = 0%** on cache hit; **Δ ≤ 15%** on cache miss (bypass mode)
- R4 gate trend: **pass % must not decrease** and **criticals must not increase** vs the previous phase's run (baseline today: 26% pass / 6 criticals)

---

## 1. Per-phase milestone gates

### M0 — Baseline freeze (before P0 starts)
*Purpose: lock the numbers we must not regress.*
- [ ] Record current: R1 count (≈70), R3 result (15/16), R4 gate (26% / 6 crit), tokens + wall-clock per report, LLM calls per report (~35).
- **Gate:** baseline document committed as `docs/baselines/M0.json`. Everything after is measured against it.

### M1 — P0 complete (descriptions, prompt discipline, KV-stable prefixes)
Write first: `test_descriptions.py` — every `@tool/@skill/@agent` description contains WHAT + WHEN
+ ≥1 negative-scope phrase ("do not use"); byte-stability test — two consecutive prompt builds
for the same run are byte-identical (no timestamps/randomness in prefixes).
- **Gate:**
  - [ ] R1/R2 100%; description lint passes for **100% of registered components**
  - [ ] Agent-loop misroute test: scripted 10-goal suite, wrong-tool selections **≤ 1/10** (baseline measured at M0)
  - [ ] Prefix byte-stability: **100%** across 3 consecutive builds

### M2 — P1 complete (RunLedger + transcript + hooks + streaming)
Write first: `test_ledger.py` — append-only (no update/delete API), every plan.py step emits
start/end events, every tool call emits an Evidence ref, ledger survives process restart.
- **Gate:**
  - [ ] Ledger completeness: a full cafe run produces **step events = steps_completed count** and **tool events = provenance count** (exact match)
  - [ ] Provenance panel renders **from the ledger** (old path deleted — one source of truth)
  - [ ] R5: workspace shows ≥1 live step event **before** the run finishes (streaming actually streams)
  - [ ] R3 corpus unchanged (≥15/16, structural checks all pass)

### M3 — P2 complete (resume)
Write first: `test_resume.py` — kill a run after step N (SIGKILL the worker), call `resume(job_id)`.
- **Gate:**
  - [ ] Resumed run completes with **≤1 duplicated LLM call** (measured via ledger)
  - [ ] Kill-at-every-step sweep (steps 2, 5, 9, 14): **4/4 resumes** produce a complete report whose TAM/SOM equal an uninterrupted run's (cache-hit determinism)
  - [ ] Corpus batch with 3 injected kills: **0 blank reports, 0 stuck-`running` jobs**
  - [ ] The "regenerate everything after a blip" path is dead: batch wall-clock with kills ≤ **1.3×** clean batch

### M4 — P3 complete (scheduler + permission gateway + poka-yoke + tiering)
Write first: `test_scheduler.py` — read-only tools declared `parallel_safe` actually run
concurrently (assert overlap via ledger timestamps); mutating tools serialize; cap respected.
`test_gateway.py` — a metered tool without budget → clean refusal Evidence, not an exception.
`test_tiering.py` — utility calls route to flash-lite tier (assert via ledger model field).
- **Gate:**
  - [ ] Parallel fetch stage wall-clock **≥25% faster** than M0 baseline on the cafe run
  - [ ] Bad-arg suite (relative address, unmapped category, negative price): **100%** fail at the boundary with actionable messages, **0** deep-stack exceptions
  - [ ] Per-run budget test: set quota to N-1 of needed calls → run degrades honestly (banner + ledger record), never crashes
  - [ ] Tier split visible in ledger: **≥40%** of LLM calls on the utility tier for a standard run, with R3 quality thresholds still passing (tiering must not cost correctness)

### M5 — P4 complete (CASTOR.md memory + reminder channel + compaction)
Write first: `test_memory.py` — layering order (methodology → vertical → brief; specific-last),
byte-stable injection, re-injection after compaction. `test_compaction.py` — synthetic 200-step
agent run: microcompaction demotes old observations to Evidence-ID pointers, hot tail stays
inline, fixed-schema summary validates, anti-thrash cap aborts loudly at limit.
- **Gate:**
  - [ ] Agent limb survives a research goal **3× the M0 step budget** without context overflow, and final answer quality (judge score) within **5%** of a short-run answer
  - [ ] Compaction is reversible: any pointer in the compacted log resolves to full payload in provenance (**100%** of sampled pointers)
  - [ ] Thrash test: refill-loop scenario stops at the cap with a clear error (**never** >3 compactions/run)
  - [ ] Operator prefs in `operator.md` provably alter output (A/B: tone directive present/absent → judge detects the difference)

### M6 — P5 complete (SKILL.md pilots + spawn contracts + plan-as-artifact)
Write first: `test_spawn_contracts.py` — spawn without objective/output_schema/tool_mask →
validation error; depth-2 spawn attempt → registry refusal. `test_plan_artifact.py` — plan.json
referencing a nonexistent result key → gate blocks before any LLM call.
- **Gate:**
  - [ ] 2 skills migrated to SKILL.md folders; metadata-only context cost **≤150 tokens each** (measured); body loads only on trigger (ledger shows no body tokens on untriggered runs)
  - [ ] Spawn-contract enforcement: **100%** of crew spawns carry schemas; sub-agent returns validate or retry
  - [ ] plan.json gate: seeded bad plan (unknown section, missing figure source) → **blocked pre-render** with a named reason
  - [ ] R4 audit re-run: **pass % ≥ M0+20 points** cumulative, criticals **≤ 3**

### M7 — P6 complete (premium multi-agent: crew stage + verifier panel + effort knob)
Write first: `test_effort_knob.py` — quick/standard/deep produce 0/3-5/10+ worker spawns
(ledger-verified). `test_verifier_gate.py` — seed a known critical (dual SOM) into a result →
panel CONFIRMS it and blocks publish.
- **Gate:**
  - [ ] Deep mode: **≥3 independent evidence origins** per headline number (TAM, SOM, price) on 3 test ventures — the triangulation moat, measured
  - [ ] Verifier panel catches **≥90%** of a 10-bug seeded suite (drawn from the audit's historical criticals: dual SOM, unit bleed, wrong-geo competitors, fabricated benchmark, currency mismatch…)
  - [ ] False-block rate: **≤1/16** clean corpus reports blocked incorrectly
  - [ ] COGS logged per report (tokens × tier price) and **≤ $25** in deep mode
  - [ ] **Final gate — the definition of done for Harness v2: R4 audit ≥90% cells pass, 0 CRITICAL, on the full 16-venture corpus.** (The threshold set in AUDIT_PLAN.md from day one.)

### M8 — Premium-report parity (the commercial bar)
**Reference artifact:** BCC Research FCB049D (224pp = 12 × ~18pp templated chapters; $2,750–$5,500
price class). Dissection (2026-06 read): structure is templated; the prose is dated-sourced
fact-events in formulaic connective tissue; ALL market numbers are self-referential ("Source: BCC
Research") with a one-page boilerplate methodology. Castor must match the **structure, fact
density, and internal consistency** — and beat the **transparency** (per-figure lineage vs
self-reference). Reference PDFs stay local (copyrighted — never commit them), in a local
`parity_corpus/` folder; only dissection notes enter the repo.

Write first: `test_forecast_model.py` — ONE deterministic model (base value → segment shares →
growth rates) emits all segmentation tables; every table reconciles to the same headline total
to the decimal; CAGRs recompute from endpoints. `test_report_pdf.py` — WeasyPrint output has
cover, TOC with real page numbers, numbered Tables/Figures, branded footer. `test_fact_density.py`
— counts dated+cited fact-events (date + claim + external source record) in the rendered report.

- **Gate:**
  - [ ] One deep-mode venture → **≥20-page print PDF**: cover, TOC w/ page numbers, numbered Tables/Figures, branded header/footer
  - [ ] **≥40 dated, externally-cited fact-events**, each backed by a claim→source record; **zero uncited dated claims** (beats BCC: their facts cite, their numbers don't)
  - [ ] Forecast engine: base year + 5-yr forecast + CAGR across **≥3 segmentations**, every table summing to the same headline total — enforced by `validate_numbers`, not prose
  - [ ] Every market figure carries lineage (formula or source); the provenance panel ships as the methodology appendix — the anti-"Source: BCC Research" differentiator
  - [ ] Zero UNSOURCED headline numbers in premium mode when data keys are present (Census/BLS/Tavily)
  - [ ] Blind judge (benchmarks/judge.py, new rubric) scores a Castor chapter against a BCC chapter on structure / evidence density / internal consistency / transparency: **parity or better on all four**, stored in `docs/baselines/M8.json`
  - [ ] COGS logged, **≤ $40**/premium report (supersedes M7's $25 for this deeper mode)

---

## 2. Regression protocol (what runs when)

| Trigger | Suite |
|---|---|
| every commit | R1 + R2 (fast rings) — must be green to push |
| every phase merge | R3 corpus + reproducibility pair + R5 smoke (1 venture) |
| phase end (milestone claim) | full milestone gate above, results appended to `docs/baselines/M<N>.json` |
| nightly (when wired) | R3 + a 3-venture R4 mini-panel; alert on any threshold breach |

**Rules of evidence:**
1. A milestone is claimed **only** with its gate output pasted into the phase commit message.
2. Any R3/R4 regression during a phase = stop feature work, fix, re-run — the phase cannot
   complete while a prior ring is red (matches the fix-and-retest loop we've run all along).
3. Thresholds move in one direction only (tighten). Loosening a threshold requires a written
   rationale in this file via PR.
4. Every bug found by R4/R5 or a human read becomes an R1 test before it's fixed (the WTP-band,
   OSM-key, and SafeUndefined fixes all followed this pattern — keep it).
