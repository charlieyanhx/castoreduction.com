# Castor Harness v2 — Adopting Claude Code's 6-Layer Architecture
## Clean-Room Plan & Testing Program (unified)

> **This document is the single source of truth for Harness v2.** It merges the architecture
> plan and the testing milestones (the former `TESTING_MILESTONES.md`, now a pointer here) into
> one program: **§1** what happens to every existing file · **§2** the target structure and the
> idea it serves · **§3** the debugging/unit-test program that verifies every §2 file and goal ·
> **§4** the execution order · **§5** the timeline and the implement→test→confirm loop every
> item runs through. A milestone is claimed by a **program's exit code**, never an opinion.

**Goal:** rebuild Castor's execution core on the architecture that makes Claude Code the most
reliable long-running agent harness in production — adapted to what Castor actually does:
generate paid-grade, numbers-must-be-right market-research documents at premium-report
(BCC-class) parity.

**IP/licensing ground rules (clean-room):** we adopt *concepts and patterns* as documented in
public reverse-engineering literature (minusx.ai "Decoding Claude Code", kirshatrov
claude-code-internals, shareAI-lab/analysis_claude_code, Piebald-AI system-prompt collections,
Anthropic's own published engineering posts — all verified reads, see
[HARNESS_LITERATURE.md](HARNESS_LITERATURE.md)). **No decompiled source is copied.** All code is
written fresh against our own registry/Evidence abstractions; dependencies stay MIT/BSD/Apache.

**The reference architecture (from the leak literature):**

| Layer | Claude Code (leaked names) | What it does |
|---|---|---|
| **L1 — Entry** | CLI REPL, slash commands, hooks | user interaction, command routing, lifecycle hooks around every tool call |
| **L2 — Master loop** | `nO` loop | ONE single-threaded loop over a flat, append-only history; "agent = loop + LLM + tools" |
| **L3 — Context & memory** | `wU2`/`AU2`, CLAUDE.md, system-reminders, TodoWrite | layered always-loaded memory; headroom-based compaction into a fixed 8-part schema; microcompaction to disk pointers; mid-stream steering via injected reminders; full-list recitation |
| **L4 — Tool system** | `UH1` scheduler, `MH1` executor, permission gateway | discovery → arg validation → permission gate → concurrency-classified execution (read-parallel ≤10 / write-serial) → normalized result → state record |
| **L5 — Sub-agents** | Task → `I2A` | context quarantine: fresh context + tool mask, same loop, ONE compact return; **at most one branch deep** |
| **L6 — Persistence** | messages.jsonl, todo files | append-only transcript; state on disk; resume = replay, not re-run |

Key verified numbers that shape this plan: >50% of CC's LLM calls run on Haiku-class models;
~9.4k tokens of tool descriptions vs ~2.8k of system prompt (descriptions ARE the routing
layer); compaction fires on headroom accounting; CLAUDE.md is a 4-layer concatenation delivered
as a post-system user message and re-injected after compaction.

---

# §1 — Existing code: keep / move / rewrite / delete

Triage of every file in the repo (line counts and reference counts measured 2026-07-10).
**Method: incremental moves with import shims per wave — never a big-bang rename.** A module
moves only in the wave that touches it (§4), and old import paths keep working via a
one-line re-export until the wave after.

### 1a. Orchestration & entry (→ L1/L2)

| File (lines) | Role today | Direction | Destination / note |
|---|---|---|---|
| `plan.py` (1915) | the 18-step orchestrator + ~30 module-level helpers | **SPLIT** | steps → `orchestrator/steps/` (join `skills/pipeline_steps.py`); loop/ledger → `orchestrator/run.py`; pure helpers (routers, extractors, integrity/provenance builders) → `orchestrator/derive.py`. The single biggest de-risking split in the repo |
| `api.py` (1303) | FastAPI: jobs, render context, tunnels, workspace routes | **KEEP, slim** | `entry/api.py`; extract the giant report render-context into `report/context.py`; add `entry/hooks.py` events (plan item 1.1) |
| `cli.py` (127) | legacy CLI | **KEEP, slim** | `entry/cli.py`; drop dead subcommands (report.py legacy paths) |
| `intake.py` (304) | venture intake/validation (422 on vague prompts) | **KEEP** | `entry/intake.py` — the fail-safe behavior the audit praised |
| `jobs.py` (179) | SQLite job store + checkpoint | **KEEP, extend** | `persistence/jobs.py`; grows `resume(job_id)` (6.2) |
| `provenance.py` (94) | per-run tool/LLM trace (debug panel) | **KEEP, extend** | `persistence/ledger.py` absorbs it — provenance becomes a *view* over the RunLedger (2.1) |
| `harness/agent.py` | bounded agent loop (recitation, masking, budget) | **KEEP, extend** | `orchestrator/agent_loop.py`; gains microcompaction + reminders (3.2/3.3) |
| `harness/refine.py` | generator-evaluator-refine | **KEEP** | `orchestrator/refine.py` |

### 1b. Domain skills — the report's substance (→ L4 skills)

All KEEP; they move under `capabilities/skills/<domain>/` in the wave that touches them.

| File (lines) | Direction → destination |
|---|---|
| `market_sizing.py` (669) | MOVE → `skills/sizing/national.py` (joins the existing `skills/sizing/` package: hyperlocal, classify, validate) |
| `four_ps.py` (912) | MOVE → `skills/narrative/four_ps.py` |
| `pricing.py` (376), `economics.py` (375), `financials.py` (158) | MOVE → `skills/economics/` — **`financials.py` is REWRITTEN by the forecast engine** (M8): one deterministic model emits all tables |
| `business_model.py` (230) | MOVE → `skills/economics/model_router.py` (the transactional/subscription/marketplace/… router) |
| `discover.py` (687) | MOVE → `skills/discovery/brands.py` (merge naming with `skills/discovery.py`, `skills/discovery_multi.py`) |
| `clustering.py` (404), `differentiators.py` (371), `whitespace` logic | MOVE → `skills/competitive/` |
| `taste.py` (402), `personas.py` (148), `segment_scoring.py` (241) | MOVE → `skills/audience/` |
| `company_profile.py` (87), `competitor_pricing.py` (127), `place.py` (150) | MOVE → respective skills dirs |
| `customer_universe.py` (956) | KEEP (B2B mode) → `skills/audience/universe.py`; quality depends on Wave-2 data layer |
| `reddit_signal.py` (440), `firmographics.py` (434), `macro_anchors.py` (432) | MOVE → `skills/signals/`; macro_anchors gets vertical-match guard (audit: wrong-vertical anchors) |
| `skills/` package (registry, perspective, triangulate, price_intel, refine_report, narration, pipeline, pipeline_steps, sizing/) | KEEP in place — this IS the L4 skill layer; `pipeline*.py` absorb plan.py steps; 2 pilots become SKILL.md folders (3.4) |
| `match.py` (84) | KEEP (used by cli + integration tests) → `skills/discovery/match.py` |

### 1c. Tools & data infra (→ L4 tools)

| File | Direction |
|---|---|
| `tools/registry.py` (+ Evidence envelope) | **KEEP — already at SOTA.** Gains concurrency classes (4.1), permission tiers (4.2), pydantic arg models (4.3) |
| `tools/geo.py`, `econ.py`, `scrape.py`, `domain.py`, `trend.py`, `social.py`, `ads.py`, `customer_voice.py`, `firmographic.py` | KEEP; each gets routing-grade docstring + negative scope (4.5, gate H01/H02) |
| `scrape/http.py` | KEEP — cache/throttle/UA layer is solid |
| `scrape/search.py` | **EXTEND** — Tavily becomes first backend (Wave 2); keep cascade as fallback |
| `scrape/crawl.py`, `structured.py` | **EXTEND** — trafilatura content-validity gate in front of price extraction (Wave 2) |
| `scrape/wayback.py` | KEEP |
| `sources.py` (1107) | **SPLIT** → `tools/sources/` (trustpilot, articles, forums, vertical_pubs); gains fastembed relevance gate + tldextract root-domain fix (Wave 2) |
| `llm.py` (394) | **KEEP, extend** → `model/client.py`: instructor structured output (Wave 1), `tier=` param (P3), KV-stable prefixes (3.5) |
| `cache.py` (65) | KEEP → `model/cache.py` |
| `agents/` (registry, planner, crew, research_agents, synthesis) | KEEP — L5; gains depth-1 enforcement (5.1), spawn contracts (5.2), compact returns (5.3); crew wired into the pipeline at P6 |

### 1d. Report & render

| File | Direction |
|---|---|
| `templates/report.html`, `onepager.html`, `compare.html` | KEEP → `report/templates/`; SafeUndefined stays; M8 adds print-PDF template |
| `charts.py` (256) | KEEP → `report/charts.py`; degenerate-clustering suppression (audit M8 finding) |
| `report.py` (197) | **MERGE** — keep only the functions `api.py` imports → `report/render.py`; delete the legacy CLI renderers |
| NEW files | see §2: `report/citation.py`, `report/forecast.py`, `report/pdf.py`, `report/context.py` |

### 1e. Core utils, gates, benchmarks

| File | Direction |
|---|---|
| `schema.py`, `errors.py`, `logger.py`, `net.py` | KEEP → `core/` |
| `gates.py` (311, D01–D14) + `harness_gates.py` (329, H01–H20) | **KEEP — the deterministic gate programs.** → `gates/`; every new v2 file lands with its gate check (§3) |
| `benchmarks/` (judge, prose_judge, score, run_all, cases) | KEEP — R4/R5 rings + the M8 parity judge |
| `history.py` (111), `feedback.py` (134) | KEEP (workspace endpoints) → `persistence/` |

### 1f. DELETE (verified-dead or superseded)

| File | Evidence | Action |
|---|---|---|
| `daily_check.py` (246) | 0 prod / 0 test refs | delete |
| `smoke.py` (145) | 0 refs; superseded by `gates.py` | delete |
| `probe.py` (57) | dev scratch; 1 stale ref | remove ref, delete |
| `web/legacy-console.html` | 0 refs in api.py | delete |
| legacy paths in `report.py`/`cli.py` | superseded by templates + api | delete during 1d merge |
| `web/index.html`, `dashboard.html`, `progress.html` | still routed | KEEP for now; **CONSOLIDATE** into workspace in the UI wave — mark, don't break |

Deletion protocol: `git grep` proves 0 references → delete in its own commit → full R1 suite +
`gates.py --gate core` green before push. Nothing is deleted on memory or vibes.

### 1g. Tests (all KEEP — reorganize)

All ~45 `test_*.py` files stay green throughout; they relocate to `tests/` mirroring the v2 tree
**in the same commit as the module they test**. `test_infra.py` (2224 lines) is SPLIT along the
same boundaries as plan.py. The seeded-bug suites (`test_gates.py`, `test_harness_gates.py`)
are the model for every new detector (§3).

### 1h. Docs

`docs/` keeps: this file (canonical), HARNESS_LITERATURE, AGENT_DOC_LITERATURE, OSS_TOOLING,
AUDIT_PLAN/RESULTS, REPORT_SPEC, REPORT_METHODOLOGY, ARCHITECTURE (update at each wave),
SIZING, TRIANGULATION. Historical (NUMBER_FIX_PLAN, METHOD_AUDIT, CODE_AUDIT, MANUS_TEARDOWN,
FORESIGHT, IMPROVEMENT_LOOP, AGENT_PARITY_PLAN) → `docs/archive/` — kept, out of the way.
`TESTING_MILESTONES.md` → pointer stub to this file.

---

# §2 — Target repository structure (the goal, and the idea behind it)

### 2a. The abstract idea

Castor v2 is **a deterministic document factory wrapped around a disciplined agent core**:

1. **Mechanisms belong to the harness; judgment belongs to the model.** Ordering, budgets,
   permissions, persistence, and gates are deterministic code. The LLM exercises judgment only
   *inside* a step, against curated context, and its output is validated before it becomes state.
2. **One flat history per run.** Every step, tool call, decision, and failure appends to one
   RunLedger — the way CC's message history is the single truth. Provenance, streaming UI,
   debugging, and resume are all *views over the ledger*, never separate bookkeeping.
3. **Context is engineered, not accumulated.** Byte-stable prefixes (KV-cache), layered
   always-loaded memory (operator → industry → venture brief, specific-last), reminders injected
   at trigger points, recitation of the full remaining plan, and reversible compaction
   (pointers, never deletion).
4. **Fan out to read, serialize to write, gate everything.** Read-only tools parallelize (≤10);
   anything that authors a number is single-writer and passes `validate_numbers`-class gates.
   Sub-agents are context quarantine — one branch deep, schema'd compact returns.
5. **Every number has lineage.** Claim→source records, calculation strings, UNSOURCED labels,
   and a provenance appendix — the anti-"Source: BCC Research" differentiator that is Castor's
   commercial moat (M8).
6. **Resume, don't re-run.** Any interrupted run completes from its ledger with ≤1 duplicated
   LLM call. Reliability on flaky infrastructure is a harness property, not a model property.

What we deliberately do NOT build (verified skips): a free-form master loop for the whole
product (report generation is a known workflow — determinism is the moat); parallel authorship
of the same numbers (the audit's dual-SOM/3-CAC criticals ARE that failure class); terminal
REPL; async mid-run steering; LLM injection checks (tools are parameterized); vector/RAG stores
("LLM search >>> RAG", verified). Under a premium (Opus-class) budget, multi-agent turns ON for
research breadth and adversarial verification — never for writing (Part 4b economics: ~15×
tokens ≈ $5–20 COGS vs a $99–499 report).

### 2b. The tree (recycled ← / NEW)

```
castor/
├── entry/                    (L1)
│   ├── api.py                ← api.py (slimmed)
│   ├── cli.py                ← cli.py
│   ├── intake.py             ← intake.py
│   └── hooks.py              NEW  — on_step_start/end, on_tool_call, on_failure, on_complete (1.1)
├── orchestrator/             (L2)
│   ├── run.py                ← plan.py run_plan (the spine; emits ledger events)
│   ├── steps/                ← plan.py steps + skills/pipeline_steps.py (one file per step)
│   ├── derive.py             ← plan.py pure helpers (routers, extractors, integrity)
│   ├── plan_artifact.py      NEW  — emit + validate plan.json before execution (2.2)
│   ├── agent_loop.py         ← harness/agent.py (+ compaction, reminders)
│   └── refine.py             ← harness/refine.py
├── context/                  (L3)
│   ├── memory.py             NEW  — CASTOR.md hierarchy: operator → industry → venture (3.1)
│   ├── compaction.py         NEW  — microcompaction → fixed-schema summary; anti-thrash (3.2)
│   └── reminders.py          NEW  — inject_reminder(step, text); gates steer downstream calls (3.3)
├── capabilities/             (L4/L5)
│   ├── tools/                ← tools/ (+ concurrency classes, arg models, tiers)
│   │   └── sources/          ← sources.py split (+ relevance gate)
│   ├── skills/               ← skills/ + root domain modules per §1b
│   │   └── <name>/SKILL.md   NEW  — progressive disclosure, 2 pilots first (3.4)
│   ├── agents/               ← agents/ (+ depth-1, spawn contracts, compact returns)
│   ├── scheduler.py          NEW  — read-parallel ≤10 / write-serial executor (4.1)
│   └── gateway.py            NEW  — permission tiers free|metered|paid; per-run budgets (4.2)
├── model/                    (L5-model)
│   ├── client.py             ← llm.py (+ instructor, KV-stable prefixes)
│   ├── tiering.py            NEW  — tier="utility"|"main"|"judge" routing (P3)
│   └── cache.py              ← cache.py
├── persistence/              (L6)
│   ├── jobs.py               ← jobs.py
│   ├── ledger.py             NEW  — append-only RunLedger; provenance.py becomes a view (2.1)
│   ├── transcript.py         NEW  — per-run JSONL, messages.jsonl analog (6.1)
│   ├── resume.py             NEW  — resume(job_id): replay ledger, re-run only the tail (6.2)
│   └── history.py feedback.py ← history.py, feedback.py
├── report/
│   ├── context.py            ← api.py render-context extraction
│   ├── render.py             ← report.py (merged, legacy deleted)
│   ├── charts.py             ← charts.py
│   ├── templates/            ← templates/
│   ├── forecast.py           NEW  — ONE deterministic model → all segment tables reconcile (M8)
│   ├── citation.py           NEW  — claim→source store + post-draft CitationAgent (M8)
│   ├── verifier.py           NEW  — pre-publish adversarial panel, productized R4 (6b, P6)
│   └── pdf.py                NEW  — WeasyPrint print-grade PDF: cover/TOC/numbered figures (M8)
├── core/                     ← schema.py errors.py logger.py net.py
gates/                        ← gates.py harness_gates.py (+ new checks per §3)
tests/                        ← all test_*.py, mirrored per-package
web/                          ← workspace.html/js (+ consolidated surfaces)
docs/                         ← per §1h
```

### 2c. New files needed (complete list, each tied to a plan item and a §3 test)

| New file | Implements | Wave |
|---|---|---|
| `entry/hooks.py` | lifecycle pub-sub → streaming UI, banner, ledger | 3 |
| `orchestrator/plan_artifact.py` | plan.json emit + validate + gate | 5 |
| `context/memory.py` | layered CASTOR.md, byte-stable, re-injected post-compaction | 5 |
| `context/compaction.py` | microcompaction, fixed schema, anti-thrash cap | 5 |
| `context/reminders.py` | triggered system-reminder channel | 5 |
| `capabilities/scheduler.py` | concurrency-classified tool executor | 5 |
| `capabilities/gateway.py` | permission tiers + per-run budget | 5 |
| `model/tiering.py` | utility/main/judge model routing | 5 |
| `persistence/ledger.py` | append-only RunLedger | 3 |
| `persistence/transcript.py` | per-run JSONL | 3 |
| `persistence/resume.py` | replay + tail re-run | 3 |
| `report/forecast.py` | deterministic forecast engine (deep G3 fix) | 4 |
| `report/citation.py` | claim→source store + citation pass | 4 |
| `report/pdf.py` | WeasyPrint premium PDF | 4 |
| `report/verifier.py` | pre-publish skeptic panel | 6 |
| `skills/*/SKILL.md` ×2 pilots | progressive disclosure | 5 |

---

# §3 — Debugging & unit-testing program (verifies every §2 file and goal)

**Doctrine:** milestones are claimed by programs. Two deterministic gate runners already exist
and extend to cover v2: `gates.py` (D01–D14 — one detector per historical audit critical,
proven by the 21-test seeded-bug suite) and `harness_gates.py` (H01–H20 — one check per phase
invariant; n/a until built, then pass/FAIL forever). The LLM audit panel (R4) is reserved for
what cannot be deterministic (prose quality). Same code + corpus in → same verdict out.

### 3a. The five rings (when what runs)

| Ring | What | Runs | Command |
|---|---|---|---|
| **R1 Unit** | pure functions | every commit (<30s) | `pytest -q` |
| **R2 Contract** | registry invariants: Evidence shape, descriptions, arg models, depth | every commit | `pytest tests/test_contracts.py -q` |
| **R3 Corpus** | regenerate 16 ventures; structural detectors | per wave + nightly | `python /tmp/audit/gen.py && python gates.py --corpus /tmp/audit/run1 --gate all` |
| **R4 Panel** | independent multi-agent rubric + adversarial verify | per wave end | `Workflow(audit_workflow.js)` |
| **R5 Live E2E** | browser drives workspace → stream → report | per wave end | Chrome/Playwright script |

**Standing thresholds (never loosen; tightening only):** R1+R2 = 100%, no skips. R3 ≥15/16
clean, 0 blank reports, 0 SOM mismatches, 0 `/mo` on per-unit spines. Reproducibility ΔTAM=ΔSOM=0%
on cache hit, ≤15% on bypass. R4 pass% never decreases, criticals never increase
(baseline 2026-07: 26% / 6 criticals; deterministic baseline: 80% cells / 10 blocking).

### 3b. Per-file verification map — every §2 file → its test → its gate

*(tests marked NEW are written FIRST, red → green, per the repo TDD rule)*

| §2 file | Unit/contract test | Deterministic gate check |
|---|---|---|
| `entry/hooks.py` | NEW `test_hooks.py`: every event fires exactly once per step; a raising subscriber never breaks the run | H-M2: ledger step-events == steps_completed |
| `entry/api.py` | `test_api.py` (exists) + render-context split keeps `test_report_render.py` green | D01 (no blank body), M2 streaming check |
| `entry/intake.py` | `test_intake.py` (exists) | D-intake: vague prompt → 422 |
| `orchestrator/run.py` + `steps/` | `test_plan_sizing_gate.py`, `test_hyperlocal_routing.py`, `test_sizing_dispatch.py` (exist; imports updated in-commit) | D02–D09 core detectors |
| `orchestrator/derive.py` | `test_report_data_fixes.py` (exists — routers/extractors/units) | D05 unit bleed, D07 scale route |
| `orchestrator/plan_artifact.py` | NEW `test_plan_artifact.py`: plan.json referencing a nonexistent result key → blocked pre-LLM; valid plan → executes in order | H-M6 seeded bad plan blocked |
| `orchestrator/agent_loop.py` | `test_harness.py` (exists) + NEW recitation test: full remaining plan recited each cycle | H03 budget, H04 recitation |
| `context/memory.py` | NEW `test_memory.py`: layering order (operator→industry→venture, specific-last), byte-stable injection, re-injection after compaction | H-M5 A/B operator-pref honored |
| `context/compaction.py` | NEW `test_compaction.py`: 200-step synthetic run — pointers resolve 100%, hot tail inline, fixed schema validates, anti-thrash aborts at cap | H13 anti-thrash; M5 3× budget survival |
| `context/reminders.py` | NEW `test_reminders.py`: gate-triggered reminder reaches the next LLM call's context exactly once | H-M5 |
| `capabilities/tools/*` | `test_tools.py`, `test_tools_round2.py`, `test_econ.py`, `test_overpass_retry.py` (exist) | H01/H02 descriptions+negative scope (FAIL today: 5 thin, 59 without scope — the P0 work-list) |
| `capabilities/sources/` | `test_ground_scrape_price.py` (exists) + NEW `test_relevance_gate.py`: off-category page → price rejected; parked domain → rejected (fastembed + tldextract) | D13 fabricated-benchmark detector |
| `capabilities/scheduler.py` | NEW `test_scheduler.py`: parallel_safe tools overlap (ledger timestamps), mutating serialize, cap ≤10 respected | M4 wall-clock ≥25% faster |
| `capabilities/gateway.py` | NEW `test_gateway.py`: metered tool without budget → clean refusal Evidence, never an exception; spend recorded | M4 quota degradation honest |
| `capabilities/agents/` | `test_agents.py`, `test_agents_planner.py` (exist) + NEW `test_spawn_contracts.py`: spawn without objective/schema/mask → validation error; depth-2 spawn → registry refusal | H18 depth-1 (passes today — keep) |
| `model/client.py` | `test_llm_determinism.py` (exists) + NEW `test_structured.py`: instructor path returns validated model or retries; `_parse_error` path dead | M1 parse_error ≈ 0 smoke |
| `model/tiering.py` | NEW `test_tiering.py`: utility calls route to flash-lite (ledger model field) | M4 ≥40% utility-tier share, R3 quality unchanged |
| `persistence/ledger.py` | NEW `test_ledger.py`: append-only (no update/delete API), survives restart, step+tool events complete | M2 exact-match counts |
| `persistence/transcript.py` | NEW: transcript replays to identical state | M2 |
| `persistence/resume.py` | NEW `test_resume.py`: SIGKILL after step N → resume completes, ≤1 duplicated LLM call; kill-sweep at steps 2/5/9/14 → 4/4 identical TAM/SOM | M3 batch-with-kills ≤1.3× clean wall-clock |
| `report/forecast.py` | NEW `test_forecast_model.py`: one model emits ALL segment tables; every table sums to the same headline to the decimal; CAGR recomputes from endpoints | M8; also closes G3/D08 |
| `report/citation.py` | NEW `test_citation.py`: fact record = {claim ≤256c, source, date}; unsourced dated claim → flagged; renderer refuses unbacked sentences in premium mode | M8 ≥40 cited facts, 0 uncited dates |
| `report/pdf.py` | NEW `test_report_pdf.py`: cover, TOC w/ real page numbers, numbered Tables/Figures, footer | M8 ≥20pp PDF |
| `report/verifier.py` | NEW `test_verifier_gate.py`: seeded dual-SOM → CONFIRMED + publish blocked | M7 ≥90% of 10-bug seeded suite caught, ≤1/16 false-block |
| `report/charts.py` | `test_report_render.py` (exists) + NEW: silhouette ≤0 → whitespace callout suppressed | audit M8 finding |
| `skills/*/SKILL.md` | NEW `test_skill_disclosure.py`: metadata ≤150 tokens; body loads only on trigger (ledger proves no body tokens on untriggered runs) | M6 |
| `gates/` runners themselves | `test_gates.py` (21 seeded bugs), `test_harness_gates.py` (exist) — **every new detector lands with a seeded-bug test proving it catches its target** | self-verifying |

### 3c. Debugging instrumentation (how we see inside)

- **RunLedger + transcript** — the primary debug artifact; every step/tool/LLM call/decision,
  queryable; the workspace Computer panel and the provenance appendix are views of it.
- **Provenance panel** (shipped) — per-source live/fallback status per report; extends to show
  ledger events at M2.
- **Run-health banner** (shipped) — degraded runs say so; never $0/blank as findings.
- **Seeded-bug drills** — every historical audit critical exists as a mutation fixture; gates
  must catch them forever (regression = the detector suite itself fails).
- **`--verbose` gate mode** — `gates.py`/`harness_gates.py` print the offending JSON path +
  rendered-HTML line for every FAIL, so a red gate IS the bug report.

### 3d. Milestone gates (claim = command exit 0 + output pasted in the commit)

- **M0 baseline freeze** — record R1 count, R3 result, R4 (26%/6), tokens+wall-clock+LLM calls
  per report → `docs/baselines/M0.json`. *(Done 2026-07-09: harness 6 pass/3 FAIL/11 not-built;
  deterministic 80% cells/10 blocking.)*
- **M1 (P0)** — descriptions 100% (H01/H02 green), misroute ≤1/10 on the 10-goal suite, prefix
  byte-stability 3× builds. `harness_gates.py --gate M1`
- **M2 (P1)** — ledger counts exact-match, provenance renders from ledger (old path deleted),
  R5 shows a live step event mid-run, R3 unchanged. `--gate M2`
- **M3 (P2)** — resume ≤1 dup LLM call; kill-sweep 4/4; 0 blank/stuck on injected-kill batch;
  ≤1.3× wall-clock. `--gate M3`
- **M4 (P3)** — fetch stage ≥25% faster; bad-arg suite 100% boundary failures; honest quota
  degradation; ≥40% utility-tier calls with R3 quality intact. `--gate M4`
- **M5 (P4)** — 3× step-budget survival within 5% judge quality; 100% pointer reversibility;
  thrash cap; operator.md A/B provable. `--gate M5`
- **M6 (P5)** — 2 SKILL.md pilots ≤150-token metadata; 100% spawn contracts; seeded bad plan
  blocked; R4 ≥ M0+20pts, criticals ≤3. `--gate M6`
- **M7 (P6, premium)** — deep mode ≥3 independent origins per headline number; verifier ≥90% of
  seeded suite, ≤1/16 false-blocks; COGS ≤$25 logged. **Final harness gate: R4 ≥90% cells,
  0 CRITICAL, full corpus.** `--gate M7`
- **M8 (premium-report parity — the commercial bar)** — reference: BCC FCB049D (224pp = 12×~18pp
  templated chapters, $2,750–$5,500 class; all its numbers self-referential — we must match
  structure/fact-density/consistency and beat transparency). Gates: ≥20pp print PDF (cover, TOC
  w/ page numbers, numbered Tables/Figures); ≥40 dated externally-cited fact-events, zero
  uncited dated claims; forecast engine reconciles ≥3 segmentations to one headline to the
  decimal; every figure carries lineage; zero UNSOURCED headlines when data keys present; blind
  judge scores Castor vs a BCC chapter ≥parity on structure/evidence/consistency/transparency;
  COGS ≤$40. Reference PDFs stay in local `parity_corpus/` — **never committed** (copyright).

### 3e. Regression protocol & rules of evidence

| Trigger | Suite |
|---|---|
| every commit | R1+R2 green to push |
| every wave merge | R3 corpus + reproducibility pair + R5 smoke (1 venture) |
| milestone claim | full gate above → `docs/baselines/M<N>.json` |
| nightly (when wired) | R3 + 3-venture R4 mini-panel; alert on breach |

1. A milestone is claimed **only** with its gate output in the commit message.
2. Any R3/R4 regression = stop feature work, fix, re-run. A wave cannot complete on a red ring.
3. Thresholds only tighten. Loosening requires a written rationale in this file via PR.
4. Every bug found by R4/R5 or a human read becomes an R1 test before the fix (the WTP-band,
   OSM-key, and SafeUndefined fixes all followed this pattern).
5. Every DELETE from §1f lands in its own commit with the grep proof and a green gate run.

---

# §4 — Execution order (waves; each exits through a gate)

Current empirical work-list from the deterministic baseline (80% cells / 10 blocking):
**G1** WTP `/mo` leak in consumer_research (D05, 4 reports) · **G2** SAM≤TAM clamp on national
path (D04, 3) · **G3** profitable-at-SOM coherence (D08, 2 — deep fix is `report/forecast.py`) ·
**G4** agency scale-misroute (D07, 1) · G5 non-US sources (D11) · G6 provenance (D12).

| Wave | Work (§1/§2 items) | Exit gate |
|---|---|---|
| **0** (~½ d) | Fix G1–G4; §1f deletions (daily_check, smoke, probe, legacy-console) | `gates.py --gate core` ≥95%; D04/05/07/08 = 100%; deletion commits green |
| **1** (1–2 d) | instructor into `model/client.py`; the 59 negative-scope + 5 thin docstrings; H13 anti-thrash | `harness_gates.py --gate M1` |
| **2** (2–3 d) | Data layer: Tavily first backend, trafilatura gate, RapidFuzz dedup, tldextract, fastembed relevance gate; `sources.py` split *(user: free TAVILY/CENSUS/BLS keys)* | search smoke >0 on 5 queries; D13 green; `test_relevance_gate.py` |
| **3** (2–4 d) | `persistence/ledger.py` + `transcript.py` + `entry/hooks.py` + streaming; then `resume.py`; plan.py SPLIT begins (steps extracted as touched) | M2 then M3 |
| **4** (3–5 d) | `report/forecast.py` (deep G3) + `report/citation.py` + `report/pdf.py` | forecast reconciliation, fact-density counter, ≥20pp PDF (M8 partials) |
| **5** | `capabilities/scheduler.py` + `gateway.py` + `model/tiering.py` (M4) → `context/` memory/reminders/compaction (M5) → SKILL.md pilots + spawn contracts + `plan_artifact.py` (M6) | M4 → M5 → M6 |
| **6** (premium budget) | `report/verifier.py` + research-crew evidence stage + effort knob (quick/standard/deep) + tier map | M7; then M8 full gate |

Re-run the R4 panel after Wave 2 (cheap checkpoint) and at M7/M8 (the claims). User-side
prerequisites: free Tavily/Census/BLS keys before Wave 2; Opus-class budget decision before
Wave 6.

---

# §5 — Timeline & the execution loop (implement → test → confirm → repeat)

### 5a. The unit loop — how every single item in this plan is built

Every §2 file, §1 move, and G-fix goes through the SAME five-step cycle. No exceptions, no
batching of untested work:

```
┌─▶ 1. RED        write the test FIRST (named in the §3b map); run it; confirm it FAILS
│   2. IMPLEMENT  the minimal change that makes it pass (one item, not a batch)
│   3. TEST       full R1+R2 locally — 100% green, no skips (old tests move in-commit)
│   4. CONFIRM    run the item's deterministic gate check (D##/H##/M-gate) — exit 0;
│                 paste the gate output into the commit message (Rules of Evidence #1)
└── 5. REPEAT     commit + push, take the next item. NEVER start the next item on a red ring.
```

**Wave close-out** (after the last item of a wave): regenerate the R3 corpus → `gates.py --gate
all` → reproducibility pair (same input twice) → R5 smoke (1 venture through the workspace) →
write `docs/baselines/<wave>.json` → push. A wave is closed only by its §4 exit gate.

**Stall rule:** any item exceeding **2× its estimate** is split into smaller items or descoped
with a written note in this file (via PR) — never ground through silently.
**Daily cadence:** every working day ends with R1+R2 green and pushed; no overnight red.

### 5b. The calendar — file-exact (working days; ~6 weeks, P0–M8)

**Clean-up-first policy (explicit):** D1 is pure cleanup — the four known bug fixes and the
four verified-dead deletions, in the CURRENT flat layout. **No new file is created and no file
moves until the repo is green and lighter.** After D1, *moves* are deliberately NOT a separate
phase: a file moves only in the wave that already touches it (with a one-line import shim, tests
relocated in the same commit) — the §1 no-big-bang rule. So the shape is: **D1 clean → each wave
builds + relocates only what it touches → nothing moves untested.**

All paths below are the repo as it exists today (fix sites verified by grep 2026-07-10);
`NEW` paths are created in that wave.

**D1 — Wave 0: clean up existing (8 unit loops, one commit each)**

| # | Item | Files touched (exact) | Test first | Confirm |
|---|---|---|---|---|
| 1 | G1: consumer-research WTP unit uses `unit_for_model` | `plan.py:408` (swap `infer_wtp_unit`→`unit_for_model`), `skills/perspective.py` (`_unit_phrase` consumers) | `test_report_data_fixes.py` (extend TestUnitForModel) | D05 = 100% |
| 2 | G2: SAM≤TAM ordering re-applied after post-clamp TAM rewrites | `market_sizing.py:597` (`_enforce_sizing_ordering`), `plan.py` (`triangulate_sizing`/`ground_sizing_bottom_up` re-clamp after mid rewrite) | `test_market_sizing_ordering.py` (add post-triangulation case) | D04 = 100% |
| 3 | G3 (shallow): at-SOM profit claim reconciled with scenario ceiling | `business_model.py:223` (`at_som_volume`), `plan.py:1817` (enrich site) | `test_report_data_fixes.py` (new coherence case) | D08 = 100% |
| 4 | G4: national B2B services never routes hyperlocal | `skills/sizing/classify.py` | `test_sizing_classify.py` (agency fixture) | D07 = 100% |
| 5 | DELETE `daily_check.py` | `daily_check.py` (0 refs) | — | grep proof + R1 green |
| 6 | DELETE `smoke.py` | `smoke.py` (0 refs) | — | grep proof + R1 green |
| 7 | DELETE `probe.py` + its stale ref | `probe.py`, `tools/domain.py` (remove import) | `test_tools.py` still green | grep proof + R1 green |
| 8 | DELETE `web/legacy-console.html` | `web/legacy-console.html` (0 refs in api.py) | `test_api.py` green | grep proof |
| — | **Close-out:** fresh 16-venture corpus | — | — | `gates.py --gate core` ≥95% → `baselines/wave0.json` |

**D2–3 — Wave 1: LLM reliability + routing descriptions (existing files only)**

| # | Item | Files | Test first | Confirm |
|---|---|---|---|---|
| 1 | instructor wired into `call_json` (Pydantic-validated, auto re-ask; `_parse_error` path retired) | `llm.py` (already installed dep) | NEW `test_structured.py` | parse-error smoke ≈0 |
| 2 | H13: anti-thrash guard on the agent-limb budget | `harness/agent.py` | `test_harness.py` (extend) | H13 pass |
| 3–6 | Negative scope + WHAT/WHEN on every registered component, batched per file: `tools/geo.py econ.py scrape.py domain.py trend.py social.py ads.py customer_voice.py firmographic.py` → `skills/` registered fns → `agents/registry.py planner.py research_agents.py` | those files' docstrings only | `test_descriptions.py` (NEW, the H01/H02 lint) | H01+H02 = 100% |
| — | **Close-out** | — | — | `harness_gates.py --gate M1` exit 0 → `baselines/M1.json` |

**D4–6 — Wave 2: data layer (first shimmed split)** *(user: TAVILY/CENSUS/BLS keys by D4)*

| # | Item | Files | Test first | Confirm |
|---|---|---|---|---|
| 1 | Tavily as first search backend | `scrape/search.py` (`_tavily()` ahead of `_brave`), `requirements.txt` | NEW `test_search_backends.py` (mocked) | live smoke 5/5 queries >0 |
| 2 | trafilatura content-validity gate before price extraction | `scrape/structured.py`, `scrape/crawl.py` (bump trafilatura 2.0→2.1) | NEW `test_content_gate.py` (parked/thin page → rejected) | D13 green |
| 3 | tldextract root-domain (kills `.co.uk` parked slip) | `sources.py` (`is_parked_domain`), `discover.py:313` (dedup key) | extend `test_discovery.py` | R1 green |
| 4 | RapidFuzz near-dupe competitor collapse | `discover.py`, `customer_universe.py:874` | NEW `test_dedup.py` ("Calm/Calm.com/Calm Business"→1) | R1 green |
| 5 | fastembed relevance gate (venture category vs scraped page, cosine ≥ threshold) | `sources.py` (`validate_domain`), `competitor_pricing.py` (before `category_median`) | NEW `test_relevance_gate.py` (apparel page vs restaurant → rejected) | D13 + R3 spot |
| 6 | SPLIT `sources.py` → `tools/sources/{trustpilot,articles,forums,vertical}.py` + shim | `sources.py` becomes re-export shim | existing `test_ground_scrape_price.py` green unmoved | R1+R2 100% |
| — | **Close-out + R4 checkpoint** | — | — | `gates.py --gate all`; R4 panel run, trend vs 26%/6 recorded |

**D7–10 — Wave 3: ledger, streaming, resume (first NEW packages)**

| # | Item | Files | Test first | Confirm |
|---|---|---|---|---|
| 1 | `persistence/ledger.py` NEW (append-only RunLedger; `provenance.py` becomes a view/shim) | NEW + `provenance.py`, `plan.py` (emit events per step) | NEW `test_ledger.py` | event counts == steps/tools exact |
| 2 | `persistence/transcript.py` NEW (per-run JSONL) | NEW + `jobs.py` (path wiring) | NEW `test_transcript.py` (replay→identical state) | M2 partial |
| 3 | `entry/hooks.py` NEW + streaming to workspace | NEW + `api.py` (SSE/poll endpoint), `web/workspace.js` | NEW `test_hooks.py` | R5: live step visible mid-run |
| 4 | `persistence/resume.py` NEW (`resume(job_id)`) | NEW + `jobs.py`, `plan.py` (step-skip on intact Evidence) | NEW `test_resume.py` (SIGKILL @ step N) | **M3**: kill-sweep 4/4, ≤1 dup LLM call |
| 5 | plan.py SPLIT begins: extract the steps Wave 3 touched → `orchestrator/steps/` (+shim) | `plan.py` ↓, NEW `orchestrator/steps/*` | moved tests move in-commit | R1+R2 100% |
| — | **Close-out** | — | — | `--gate M2` (D8) then `--gate M3` (D10) → baselines |

**D11–15 — Wave 4: premium substance (report/ package created)**

| # | Item | Files | Test first | Confirm |
|---|---|---|---|---|
| 1 | `report/forecast.py` NEW — ONE model → all segment tables (retires ad-hoc scenario math = the deep G3) | NEW + `financials.py` (rewritten to consume it), `plan.py:1817` area | NEW `test_forecast_model.py` (tables reconcile to the decimal; CAGR recomputes) | D08 stays 100%; reconciliation gate |
| 2 | `report/citation.py` NEW — claim→source store + post-draft citation pass | NEW + `four_ps.py` (facts emitted), `templates/report.html` (cite render) | NEW `test_citation.py` (uncited dated claim → flagged) | fact-density counter runs |
| 3 | `report/pdf.py` NEW — WeasyPrint print PDF (cover/TOC/numbered figures) | NEW + `api.py` (endpoint swap from Playwright), premium template file | NEW `test_report_pdf.py` | ≥20pp PDF renders |
| 4 | `charts.py`: suppress whitespace callout when silhouette ≤0; drop bogus "0% variance" title for UMAP | `charts.py` | extend `test_report_render.py` | audit-M8 finding closed |
| — | **Close-out** | — | — | M8 partials → `baselines/wave4.json` |

**D16–22 — Wave 5: scheduler/gateway/tiering (M4, ~D18) → context (M5, ~D20) → skills/contracts (M6, D22)**

| # | Item | Files | Test first |
|---|---|---|---|
| 1 | `capabilities/scheduler.py` NEW (read-parallel ≤10/write-serial; replaces plan.py ThreadPoolExecutors) + `@tool(concurrency=)` | NEW + `tools/registry.py`, `plan.py` fan-outs | NEW `test_scheduler.py` |
| 2 | `capabilities/gateway.py` NEW (tiers + per-run budget) + pydantic arg models on `tools/geo.py econ.py scrape.py` | NEW + those tools | NEW `test_gateway.py`, bad-arg suite |
| 3 | `model/tiering.py` NEW + `tier=` in `llm.py` (utility→flash-lite) | NEW + `llm.py` call sites in `plan.py`/skills | NEW `test_tiering.py` |
| 4 | `context/memory.py` NEW (operator→industry→venture, byte-stable) | NEW + `llm.py` prefix assembly, `intake.py` (venture.md) | NEW `test_memory.py` |
| 5 | `context/reminders.py` NEW (generalizes `model_directive` in plan.py) | NEW + `plan.py`, `four_ps.py` | NEW `test_reminders.py` |
| 6 | `context/compaction.py` NEW (microcompaction→pointers; fixed schema) | NEW + `harness/agent.py` | NEW `test_compaction.py` |
| 7 | SKILL.md pilots ×2 (`skills/sizing/hyperlocal` + citation) + spawn contracts (`agents/registry.py` depth-1 hard, schema'd spawns) + `orchestrator/plan_artifact.py` NEW | those + NEW | NEW `test_skill_disclosure.py`, `test_spawn_contracts.py`, `test_plan_artifact.py` |

**D23–27 — Wave 6 (premium budget on): verifier + crew** → `report/verifier.py` NEW (+ 10-bug
seeded suite from the audit's criticals), `agents/crew.py` wired into `plan.py` as the evidence
stage, effort knob through `intake.py`→`api.py`→`plan.py`, COGS logging in `llm.py`/ledger.
Confirm: **M7** incl. final harness gate R4 ≥90%/0 critical (D27).

**D28–30 — M8 parity claim:** one deep-mode venture end-to-end → blind judge
(`benchmarks/judge.py`, new parity rubric) vs a BCC chapter → fix deltas → re-run → **M8 full
gate** → `baselines/M8.json`.

Slack: estimates are the §4 upper ends; the stall rule converts overruns into scope decisions.
External dependencies: **keys by D4** (Tavily/Census/BLS, free), **budget decision by D23**.

### 5c. Confirm artifacts (what "done" leaves behind)

Each unit loop leaves: the new/updated test file, the gate output in the commit message.
Each wave leaves: a `docs/baselines/<wave>.json` scorecard + updated ARCHITECTURE.md if the
tree changed. Each milestone leaves: its M-gate output + (M6/M7/M8) an R4 panel result. The
audit trail IS the project log — no separate status reports.
