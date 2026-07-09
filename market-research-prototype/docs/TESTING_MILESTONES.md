# Testing Guidelines & Completion Milestones — Harness v2 (P0–P6)

Every phase of [CC_HARNESS_PLAN.md](CC_HARNESS_PLAN.md) completes ONLY when its milestone gate
passes. A gate is a **runnable command with a numeric threshold** — never a judgment call.
Tests are written FIRST (red → green), per the repo's TDD rule.

---

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
