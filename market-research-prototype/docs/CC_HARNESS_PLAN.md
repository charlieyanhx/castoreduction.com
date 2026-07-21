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

### 5d. Deviation log (§3 rule 3 / stall rule: deviations get a written note here)

- **W1 item 1 — "instructor" implemented as a hand-rolled Pydantic corrective loop**
  (commit 5b17657). instructor's client-wrapping composes per provider SDK, not with our
  cross-provider chain + whole-chain backoff + schema-fingerprinted cache; the loop
  (~40 lines in `llm.call_json`) has identical semantics — schema shown to the model,
  validated with coercion, error-embedded re-ask, `_parse_error` only as the exhausted
  last resort. The dep stays installed; revisit at `model/client.py` (Wave 3+) whether
  to adopt instructor there. Confirm held: live parse-error smoke 0/10.
- **W1 close-out ran the §4 exit gate only** (`harness_gates --gate M1` + full R1 sweep;
  commit f5d415a). The §5a wave-close ritual (R3 corpus regen + reproducibility pair +
  R5 smoke) was NOT re-run for W1: the wave's only pipeline-behavior delta is the
  schemaless repair re-ask in `call_json`, which activates only on previously-FAILING
  parse paths; `response_model=` has no production call sites yet. The R3 regen +
  repro pair + R5 smoke run next at the Wave-2 close-out (D6) as scheduled, which
  therefore doubles as the corpus-level confirmation of the W1 llm changes.
- **D1 item 5 widened**: `daily_cron.sh` deleted alongside `daily_check.py` (542b3f7) —
  the wrapper's sole purpose was invoking the deleted file. User-side: a crontab entry
  pointing at it must be removed manually (flagged in the commit + to the operator).
- **D1 item 7 narrowed**: the planned `tools/domain.py` stale-import removal was moot —
  the only "probe" there is `probe_domain_patterns`, a healthy wrapper of
  `sources.probe_domain_patterns`, unrelated to the deleted `probe.py` (71ad655).
- **W1 gap-closure after plan-check** (5806e9a, 95cc34c): agents' negative-scope
  descriptions (D2-3 items 3-6 listed the agents files; the fan-out covered only
  tools+skills) landed late with H02 tightened to inspect AGENT_REGISTRY; the
  taste-dedup R1 failure was root-caused as a deterministic dedup param collision
  (not flakiness) and fixed with per-connection `JOBS_DB_PATH` + session temp DBs —
  R1 at literal 100% (733/733) from here on.
- **W2 item 1 — Tavily via house REST, not the SDK** (120eccd): the plan's file list
  included requirements.txt assuming tavily-python; the backend follows the _brave
  pattern (scrape.http.request), so no new dep. LIVE-SMOKE CONFIRM STILL PENDING:
  TAVILY_API_KEY not yet in .env at the W2 close-out — the cascade degrades to the
  old order until it lands; run the 5-query smoke when it does.
- **W2 item 2 wiring site** (ead4a51): the content gate lives in scrape/structured.py
  per the plan, but the wiring is in competitor_pricing.scrape_brand_prices (not in
  the plan's file list) — that is where pages physically enter price extraction.
- **W2 item 3 widened** (0838d1c): root_domain wired at all FOUR naive-root sites
  (plan named two); same unit of work, one helper.
- **W2 close-out — D10 caught live by the R3 ring** (efa0723): the fresh-corpus gate
  flagged becc8783 shipping a degenerate WTP band (4 segments all $10 → 10/10/10
  "range"). Root-fixed as a consensus-point disclosure per §3 rule 4 (gate-found bug
  → R1 test → fix); venture re-run; core gate then 122/122 = 100%.
- **W2 close-out — R4 panel COMPLETE** (`docs/baselines/wave2_r4.json`): took three
  passes across two credit-ceiling resets (35→93→127 agents) to finish; final run
  127/127, 0 errors, every CRITICAL/HIGH finding adversarially verified. Result:
  **35.9% cells PASS (69/192), 31 CRITICAL, 24 HIGH**, full 16/16 coverage.
  BASELINE CAVEAT: this panel (4 dimension-groups over the 12 rubric rows R1-R12) is
  NOT identical methodology to the M0 audit (19 cells incl. 7 invariants; the actual
  AUDIT_RESULTS.md read 21%/47, and the plan's "26%/6" M0 note is a different scoping)
  — so 35.9%/31 is the Wave-2 baseline for THIS panel, which M7/M8 trend against;
  directionally pass-rate up and criticals well under the audit's 47. The confirmed
  CRITICALs cluster on cross-section numeric incoherence (R12=8, R3=5, R2=3, R11=3:
  one headline number rendered with different values across sections) and WTP-vs-price
  mismatch on deep-tech/B2B ventures (R9=5). Both are Wave-4 targets (report/forecast.py
  reconcile-to-the-decimal + report/citation.py), NOT the Wave 1-2 reliability/routing/
  data-layer scope — whose floor holds (deterministic gate D01-D14 = 100%). The Wave-2
  HARD exit gate was met independently of R4.
- **Wave 2.75 inserted (D7-8): R4 criticals burn-down** — the calendar's Wave 3
  (persistence) shifts +2 days. Rationale: the verified R4 panel showed the remaining
  trust gap is report substance, and 4 of its 5 clusters root-cause to surgical,
  deterministic fixes (wrong density input; hybrid /mo-price pick cascading into the
  subscription fallback; missing WTP↔price reconciliation; discarded relevance verdict
  in ranking) — each cheaper than a persistence day and each guarded by a new detector
  (D16-D19). Full spec: `docs/R4_BURNDOWN.md`, written as a self-contained executor
  brief (root-caused on Fable; executable by Sonnet/Haiku-class models per the tiering
  strategy — token-lean: corpus SIMULATION per item, ONE regen + a scoped R4 mini-panel
  at close-out only). D15 (2c94c13) already removed the largest cluster.
- **R4 harness defect found + fixed** (the credit-ceiling runs exposed it): the panel's
  aggregation counted a dead verifier the same as "verifier ran and upheld" (both →
  refuted:false), so a truncated run could silently pass off unverified findings as
  confirmed. This run survived on persistence (resumed until 0 agent errors), not
  correctness. Fixed: the aggregation now tracks `verifier_died` per finding, emits
  `verification_gaps` + a `valid` flag, and a run with any gap is INVALID. Re-confirmed
  the Wave-2 R4 at **0 gaps, valid=true** via cached replay (0 tokens). The M7/M8 R4
  claims run through this hardened panel.
- **W2 close-out doubles as the W1-deferred corpus ritual**: this regen is the R3 +
  reproducibility (identical to the cent) + R5 smoke (PASS 42s) confirmation of the
  W1 `call_json` changes that §5d parked at the W1 close.
- **D22 (post-Wave-2.8): "Python computes, LLM narrates" audit + burn-down** — a
  6-agent parallel code audit (user directive: stop letting the LLM do math; every
  number gets one Python-computed source of truth, the LLM only narrates it) surfaced
  7 findings; the user selected items 1-3 (the root-cause set) for immediate
  execution, deferring items 4-7 (forced-non-empty `ip_credentials` differentiator;
  differentiators.py's false docstring claim of Product/Promotion/EVC integration;
  hardcoded TAM range-caption string; tier-b/c domain-resolution relevance-gate gap)
  as audited-but-unscoped.
  - **item 1** (7aa18b7): `competitive_density_directive()` threads density +
    active_density into every 4Ps section prompt (Place/Product/Promotion previously
    got none at all — only Viability did), mirroring `model_directive`/
    `price_anchor_directive`. Documented known limitation: the F3 late geo-competitor
    override (`_surface_late_geo_competitors`) still runs AFTER 4Ps dispatch, so a
    hyperlocal venture's pre-override density can still leak into 4Ps prose — item 3
    is the safety net for that residual case.
  - **item 2** (8ab6d34): `VIABILITY_PROMPT`'s DIMENSION 3 was a single hardcoded
    CLV:CAC rubric for every business_model_kind, but the only real_metrics ever fed
    to it (economics_evc/economics_clv) are subscription-only keys — every other kind
    was scored against a rubric with zero data to satisfy it (the flagged R11 root
    cause). Added `unit_economics_rubric(kind)` (CLV:CAC for subscription,
    contribution-margin/break-even for per-unit, take-rate/two-sided-CAC for
    marketplace, ad-revenue-per-user/cost-to-serve for ad_supported) plus real_metrics
    enrichment reading the actual computed economics object per kind.
  - **item 3** (d228ccf): new gate `d22_viability_reasoning_density_coherent` mines
    "only/just N competitors" / "N competitors identified/found/in the market" claims
    (digit or spelled-out one-ten) from viability's own reasoning/summary/strengths/
    risks and fails on disagreement with the real competitor_density or
    active_signal_density — the safety net named in item 1's known-limitation note.
  - **Close-out verification (token-lean, scoped not full-corpus)**: unit suite 856
    passed/5 skipped (was 779 pre-D22, +77 net new tests across items 1-3); gates.py
    --gate all run against the stale 16-venture wave2 corpus showed D22 firing 0 false
    positives (2 ventures with an explicit claim both pass, rest correctly N/A). LIVE
    regen (real LLM calls, not simulation) of 2 ventures spanning the two audited
    business-model shapes — 174ae091 (marketplace) and 28d0ec61 (hybrid/per-unit) —
    both landed 0 gate failures (D01-D22) and, read directly from the fresh output,
    confirm the rubric fix: 174ae091's unit_economics_health reasoning now reads "The
    15% take rate yields a healthy $67.50 per booking, but two-sided CAC ... remain
    unmodeled" (zero CLV:CAC language); 28d0ec61's reads "a strong 65.5% contribution
    margin with a highly achievable break-even volume of 309 bottles per month" (exact
    match to `economics.contribution_margin_pct`). Deliberately did NOT re-run the
    full 14-16 venture corpus or the expensive R4 LLM-judge panel for this scoped fix
    — the two live regens directly exercise the two kinds items 1-3 targeted, and the
    unit tests already pin the wiring for subscription/ecommerce/services/ad_supported
    which weren't independently re-verified live.
  - **MEASURED accuracy delta (full 16-venture live regen + blinded A/B judge panel)** —
    ran on user request ("did the accuracy improve?"). Regenerated ALL 16 ventures with
    real LLM calls (809s, 0 failures), then a 32-agent Workflow (16 blind A/B judges +
    16 adversarial verifiers, 0 errors) scored NEW (D22-fixed) vs OLD (out/wave2_corpus
    baseline) viability reasoning, blinded per-venture, on the dimensions items 1-3
    targeted. VERDICT — improvement is REAL but modest and concentrated in density
    coherence, NOT the unit-econ rubric:
      * Deterministic cross-section coherence (all fixes combined): 83.9% (177/211,
        stale corpus) → **100% (218/218, 0 blocking failures)** on the fresh corpus.
        Conflates D15-D22 + regeneration, not D22-isolated.
      * Competitor-density coherence (item 1's target): viability claims that contradict
        the real density: OLD **4 → NEW 1**. The 1 residual (800c261b) is a borderline
        QUALIFIED claim ("zero direct *room-temperature* superconductor rivals" vs
        density 12 broad-category) — arguably legitimate, and correctly OUTSIDE D22's
        deliberately-narrow regex (which fired 0 on the fresh corpus). Clearest win.
      * Unit-economics model-appropriateness (item 2's target): absolute per-judge counts
        NEW **16/16** vs OLD **15/16** appropriate; CLV:CAC bleed on non-subscription
        OLD **1 → NEW 0**. BUT head-to-head, adversarially verified, NEW won only **3/16**
        — the skeptic REFUTED the "improvement" in 13/16 as cosmetic, because the OLD
        reports were ALREADY mostly model-appropriate (the pre-existing M4 model_directive
        does most of the subscription-bleed prevention; item 2's rubric is a marginal
        refinement, not a step-change). The 3 genuine wins (800c261b, 94008e7c, e8baf9dd)
        were as much factual-correctness (stating a strong 64-76% margin as strong, vs the
        old report calling it "marginal") as pure model-framing.
      * Honest scope: this A/B measures ONLY the viability dimensions D22 touched — it is
        a sharper instrument for "did THIS change help" than the headline R4 panel, but it
        does NOT reproduce or move the 33.9% full-rubric R4 number. Adversarial verify is
        conservative (biased toward "no real gain"), so 3/16 is a lower bound and the
        absolute counts (bleed 1→0, density 4→1) are the softer upper read; truth is
        between. Artifacts: scratchpad/{judge_results.json, aggregate_judgment.py},
        wave_d22_corpus/.
- **Scraper-stack audit + repair (SCR items 1-4)** — a 6-agent parallel audit of the
  web-scraping subsystem (acquisition/extraction/resilience/data-flow) found the load-
  bearing INPUT layer was the weakest, and — the key insight — weak for a fixable
  TOOL/wiring reason, not a model reason. Scores: discovery 3/10, fetch 4/10, content-
  extraction 4/10, structured-extraction 3/10, anti-hallucination 7/10 (the real moat).
  User directed: fix all four highest-leverage items, don't stop until done.
  - **item 1 — the two dead scrapers** (0efa57b): (a) tools/scrape.fetch_page called
    scrape.crawl.fetch_page(url, max_chars=) against a (url, timeout=) signature →
    TypeError on EVERY call, swallowed by @tool → the entire bottom-up ARPU scrape
    (price_intel) was dead in production; also the dict return was treated as a string.
    Fixed extraction + added a plain-HTTP fallback. (b) trustpilot used json.loads()
    with no `import json` → NameError swallowed → zero reviews parsed ever. Root env
    gap: the Playwright chromium binary was never installed (`playwright install
    chromium`); done. Verified LIVE: fetch_page('example.com') now returns real
    rendered HTML.
  - **item 2 — competitor discovery grounded in live web search** (847b154): the
    shipped set for non-local ventures was LLM-recall only (Trends-extraction / LLM
    generation); the tested multi-strategy fan-out existed but was NEVER called by
    run_plan. Wired it into discover._run_signal_gathering_and_synthesis (unions before
    enrichment, best-effort/graceful). CRUCIAL quality fix — the reason it was benched:
    it used raw search-result TITLES as competitor names + never filtered aggregators,
    so a live run returned "10 Best CRM | Forbes" / pcmag.com as "competitors". Added an
    LLM extraction pass mining REAL vendor names from titles+snippets (drops publishers).
    Verified LIVE: "CRM for small business" → Freshsales, HubSpot, Pipedrive, Bigin by
    Zoho, monday CRM, Creatio (all 'direct'), vs the prior listicle garbage. Documented
    TAVILY_API_KEY / BRAVE_SEARCH_KEY in .env.example (code already consumes them; real
    keys are the operator's to supply — without them the cascade degrades to flaky public
    SearXNG+ddgs).
  - **item 3 — pricing path + JS render + extraction precision** (af1b165): /pricing &
    /plans were never probed (PRICE_PATHS index 3+ vs MAX_PATHS=2) so SaaS prices were
    invisible → front-loaded them, widened to 4. Added _fetch_pricing_html JS-render
    fallback for SPA pricing pages. Precision: when a page ships structured price markup
    (schema.org/JSON-LD/microdata), TRUST it and skip the regex-over-all-text (the audit's
    one-$68 → [5,20,25,50,68,500] noise). First ground-truth extraction tests in the repo.
  - **item 4 — competitor-completeness verification loop** (the user's own idea): a
    second, adversarial pass (discover._verify_competitor_completeness) that seeds live
    'alternatives to <known competitor>' searches off the CURRENT set and asks an LLM,
    grounded in fresh results, 'what real competitors are missing?' — attacks the LLM-
    recall blind spot the fan-out can still share. Verified LIVE: thin {Asana, Trello}
    set → surfaced ProjectManager, Microsoft Project, ClickUp.
  - Discipline: every fix RED-tested first (new test_scraper_fixes.py — 24 tests across
    the 4 items — plus updates to test_discovery_multi / test_integration for the new
    call seams), each verified LIVE against the real web, committed per item. HONEST
    caveat: full-corpus regen + R4 re-measure of these scraper changes NOT yet run —
    the wins are proven at the unit + live-smoke level, not yet in a report-level
    accuracy delta.
- **Wave 3 (D7-10) — ledger / transcript / resume: items 1, 2, 4 landed; 3 and 5 open.**
  - **ORDER DEVIATION**: built item 4 (resume) before item 3 (hooks/streaming). Both
    hang off the same new ledger sink, and resume consumes transcript.replay directly,
    so doing it while that was fresh was cheaper and lower-risk. Item 3 is a different
    surface (api.py SSE + web/workspace.js) and is unblocked either way.
  - **item 1** (5fe9741): `persistence/ledger.py` RunLedger — append-only, thread-safe,
    3 event layers (step/tool/llm); `events()` returns copies so history can't be
    rewritten. provenance.py is now a thin VIEW over it, API + event shape unchanged
    (build_provenance_summary, the report panel and gate D12 all read through it).
    FOUND WHILE WIRING: plan.py never called set_step() at all, so every provenance
    event ever shipped with step=None — step labels are populated for the first time.
    New `_step_done(result, name)` writes to BOTH `_steps_completed` and the ledger so
    they cannot drift; all 29 raw append sites converted; later made idempotent (resume
    re-enters with steps already complete, and gate D01 counts that list's length —
    verified real runs carry 17-19 UNIQUE steps, no duplicates, so dedup is safe).
  - **item 2** (d47e8d2): `persistence/transcript.py` — per-run JSONL flushed per event
    (the flush is the point: a killed run keeps everything up to its last event).
    Guarantees replay→identical state, and tolerates a truncated final line (SIGKILL
    lands mid-write; a crash costs that one event, never the history). RunLedger gained
    an optional sink; jobs.run_async attaches/detaches a per-job writer.
  - **item 4**: `persistence/resume.py` — reconciles the two records a killed run leaves:
    the jobs row (step OUTPUTS, checkpointed) and the transcript (what COMPLETED, flushed
    per event). A kill between them leaves the transcript ahead, so completed-steps is the
    UNION with the transcript authoritative. `plan.run_plan(resume_from=...)` seeds from it
    and `_skip_step` skips ONLY on intact evidence — a step marked complete whose output is
    missing/empty/errored is recomputed (a hole in the report costs more than redoing a step).
    VERIFIED LIVE: a real pipeline SIGKILL'd at 45s mid-discover left a durable transcript
    (`completed: ['profile']`); resume returned an intact seed; the resumed run logged
    "profile RESUMED ... (skipped)" with **0 duplicate LLM calls**.
  - **item 3**: `entry/hooks.py` HookBus — the ledger has ONE sink and item 2 spent it on
    the transcript, so the bus sits between: ledger → BUS.emit → N subscribers, each
    delivery isolated (a raising subscriber can't break the run or starve the others;
    same reasoning as the sink guard — observability never fails the thing it watches).
    jobs.run_async now subscribes the transcript to the bus instead of owning the sink.
    NEW `GET /jobs/{id}/events?since=` serves R5 off the per-event-flushed transcript —
    strictly finer-grained than polling /jobs/{id}, whose partial result only advances at
    CHECKPOINTS and so can only ever show completed steps, never the tool running right
    now. workspace.js polls it (cursor-based) for a live activity label.
    VERIFIED: endpoint returns 200 with real mid-run activity off the REAL SIGKILL'd run's
    transcript (latest event = a live `web_search`, 8 items, mid-discover); the JS label
    function exercised in node (7/7 cases). NOTE: `preview_start` is broken in this env
    (ignores launch.json, falls back to a python3.9 http.server that hits a sandbox
    PermissionError), so the browser-level check was replaced by endpoint-over-real-HTTP
    + node execution of the pure JS logic.
  - **CORRECTION to the item-1 note above**: "step labels are populated for the first
    time" was overstated. Item 1 makes step EVENTS exist (name + status), which is what
    resume needs. The `step` FIELD on tool/llm events is STILL None — labelling those
    needs `set_step()` called at each step's START, which plan.py has never done and
    which 29 more edit sites would be the wrong way to add. It lands naturally with item
    5, where each step becomes a function that can label itself. The live-activity label
    degrades gracefully meanwhile (falls back to the bare tool name).
  - **item 5 — the split BEGINS** (scoped to "the steps Wave 3 touched"): profile + discover
    → `orchestrator/steps/{profile,competitors}.py`. The shared step machinery
    (`skip_step`/`step_done`/`step_scope`) moved to `orchestrator/steps/__init__.py` because
    an extracted step can't import plan (plan imports the steps — that's a cycle); plan.py
    re-exports the two under their old private names, which IS the wave's "+shim".
    RESOLVES THE ITEM-1 CORRECTION: `step_scope(name)` means each step now LABELS ITSELF, so
    tool/llm events finally carry the `step` field — provenance can attribute a fetch to the
    step that wanted it, which no amount of plan.py editing had ever done. Resume coverage
    goes 1 step → 2 (discover got its own guard).
  - **A REAL BUG THE SPLIT EXPOSED**: `test_resume.py` patched `plan.extract_company_profile`
    and `plan.discover`. After the move those names are no longer the call sites, so the
    patches silently intercepted NOTHING and the tests made REAL LLM + network calls — the
    suite ballooned 2min → **26min** and one test failed. Patch targets corrected to
    `orchestrator.steps.{profile,competitors}.*` (patch where a function is USED, not where
    it once lived); those tests are back to 2.1s. The now-dead `extract_company_profile` /
    `discover` imports were removed from plan.py (AST-verified unused). This is the exact
    failure mode the split is meant to surface, and it argues for finishing it.
  - **M3 SCOPE — HONEST**: the gate's "kill-sweep 4/4, ≤1 dup LLM call" is NOT yet claimable.
    The resume machinery is complete and proven, but `_skip_step` is currently applied to ONE
    step (profile). Broad skip coverage needs each step's local variables restorable from
    `result`, which is exactly what item 5 (extract steps → `orchestrator/steps/`) enables —
    run_plan is a ~900-line linear function whose locals feed forward, so adding 20+ skip
    guards before the split would be the kind of big-bang §1 forbids. M2/M3 gate runs are
    deferred to the item-3/item-5 close-out rather than claimed early.

### Wave 4–6 (this session) — deviations & findings

- **W4 item order.** Built 4 → 2 → 3 (charts, citation, PDF) rather than 1–4. Item 1
  (`report/forecast.py`) had landed earlier in the program; item 4 was the smallest
  self-contained piece and made a good warm-up against the render layer.

- **W4-2 measured, not asserted.** The fact-density counter is not a claim, it is a
  number: on the stored 16-venture corpus, **224/330 = 67.9%** of checkable claims
  (a year, a $ figure, or a %) carry a resolving citation — range 55%–85%, zero
  dangling markers. That is the baseline the number exists to move.

- **W4-3 deviates from "WeasyPrint print PDF" only in fallback.** WeasyPrint is
  preferred because it is the only engine that resolves `target-counter()`, i.e. that
  can print real page numbers next to TOC entries; Chromium is the fallback and its
  TOC omits them (a wrong page number is worse than none). On macOS WeasyPrint needs
  `brew install pango`, and `report/pdf.py` sets `DYLD_FALLBACK_LIBRARY_PATH` before
  the import — it loads those libs through ctypes at import time, so setting it after
  is too late.

- **W5-1 scheduler is NOT yet wired into plan.py.** The module, its policy, and its
  tests are complete, but the fan-out call sites still use their own
  ThreadPoolExecutors. A corpus regen was in flight against those fan-outs, and the
  Wave 4 baseline has to describe the code it was generated from. Migration is the
  Wave 5 close-out.

- **W5-3 tiering policy is deliberately conservative.** The mechanism routes three
  tiers; the POLICY downgrades exactly one call (discovery query planning, where the
  fan-out unions many strategies so no single query is load-bearing). Competitor
  extraction looks equally mechanical and is explicitly NOT downgraded — it decides
  who counts as a competitor, and that lands in the report. A test pins that, so a
  later tidy-up has to argue with it.

- **A ROUTE I BROKE, AND WHY THE SUITE MISSED IT.** Extracting `display_title()` in
  api.py, I placed it BETWEEN `@app.get("/jobs/{job_id}/report.html")` and
  `get_job_report_html`. FastAPI registered the helper as the handler; every request
  422'd asking for a `profile` body. 1189 tests stayed green because none exercised
  the route, and the corpus regen wrote 16 "reports" that were each an 82-byte
  validation error — caught only because the seeded verifier suite started reading
  those files. Fixed, plus: test_api now asserts each report route maps to its own
  endpoint, and the regen script raises on a non-200 render instead of writing it.

- **THE SEEDED SUITE'S FIRST DRAFT PROVED NOTHING.** test_verifier.py originally
  hand-built a "clean" report dict. It used `market_sizing.tam_usd` where the pipeline
  emits `market_sizing.tam.mid`, top-level `competitor_density` where it lives under
  `discover`, and so on — seven detectors found nothing to check, returned N/A, and
  the suite passed proving only that the fixture matched itself. Rewritten to seed ONE
  defect into a REAL corpus report and assert the DELTA (absent before, present
  after). Schema-faithful by construction, and unsatisfiable by a detector that fires
  on everything.

- **The scheduler's per-task timeout was doing nothing.** The solo executor sat in a
  `with` block, which joins on exit — so a timed-out task still blocked the batch for
  its full duration. `shutdown(wait=False)` fixed it; the test file went 5.4s → 0.45s,
  which is how it was noticed.

- **A GLOBAL CONCURRENCY CEILING WAS TRIED AND REVERTED.** Per-scheduler widths do
  not fix nesting (customer-voice fans out inside signal-gathering, so two 8-wide
  pools are 64 in flight). The obvious fix — every worker holding one shared
  `BoundedSemaphore` — deadlocks on the first run: an OUTER task holds a slot for its
  whole duration while its INNER tasks queue for slots that only free when the outer
  finishes. `test_nesting_does_not_deadlock`, written to catch exactly that, hung the
  suite. A slot must be held by work waiting on a HOST, not on other work, so the
  ceiling belongs at the tool boundary (`capabilities/gateway.py`, through which every
  external call already passes). Left UNDONE rather than left deadlocking, with the
  reasoning in the module and a test asserting no global pool is declared.

### Wave 4 close-out — measured, on a fresh 16-venture corpus

  | metric | value |
  |---|---|
  | deterministic gates (D01–D22) | **219/219 = 100.0%**, 0 blocking |
  | fact density (claims attributed) | **208/300 = 69.3%** |
  | pre-publication verifier | **0 blocking**, 92 advisory across 16 reports |
  | test suite | 1233 passed, 5 skipped |

  Scorecards: `docs/baselines/wave4.json` (gate table) and
  `docs/baselines/wave4_quality.json` (fact density + verifier, per venture).

  **Comparability caveat, stated plainly.** The corpus was generated by a process that
  imported plan.py at commit a52b225, so it reflects Wave 4–5 output and does NOT
  include the Wave 6 verifier/effort/COGS additions. Those are additive metadata on
  the result, not changes to any number the gates read — but the corpus is not
  evidence about them either way.

  **What this does NOT claim.** M7 (R4 ≥90% / 0 critical) is a QUALITATIVE panel
  result and is not established by any of the above. The 22 invariants read structure;
  the R4 panel reads judgement, and it has repeatedly found defects the gates
  structurally cannot see. 219/219 means the report is internally coherent, not that
  it is good.

### Waves 5–6 close-out

All Wave 5 (D16–22) and Wave 6 (D23–27) items are landed. Deviations and findings:

- **W5-1c was built TWICE, in parallel.** Another session pushed `3b96a1f` —
  its own `capabilities/scheduler.py` + `gateway.py` plus pydantic arg models on
  geo/econ/scrape — while this session built the same two modules. Reconciled by
  MERGING rather than picking a side (commit `4c4a3c9`): theirs is the base (it
  matches the plan row: `@tool` integration, arg models, dollar budget from config),
  with three things grafted from this side —
  signature-based validation covering all 36 registered tools rather than the 3 with
  arg models; validation running BEFORE the budget deduction so a refused call is
  never charged; and `call_named()`. Also fixed a latent bug in their failure guard,
  which indexed `parallel_jobs` by an index into `tools` (wrong tool named, IndexError
  once any job was mutating).

- **plan.py's step timeouts were COSMETIC — a real production bug.** Two joins wrapped
  `future.result(timeout=N)` inside `with ThreadPoolExecutor(...)`. The timeout fired
  and the degraded value was set, but exiting the `with` calls `shutdown(wait=True)`,
  so the block waited out the hung task anyway. Demonstrated: a 0.2s timeout on a 3s
  task exited after 3.01s. These were the run's two most expensive pairs (PSM+place at
  90s each, sizing+4Ps at 90s/120s) — one hung provider call was the difference between
  a report in minutes and one in tens of them. Migrated to
  `scheduler.run_labeled()`, whose timeout genuinely releases the batch. The degraded
  shape (`{"error": ...}`, `None -> {}`) is preserved so no call site changed.

- **THE CREW AND THE PLAN ARTIFACT WERE DEAD CODE.** `agents/crew.py` had existed since
  cycle33 and was reachable only at `POST /research/crew`; the pipeline that produces
  the deliverable never called it. `orchestrator/plan_artifact.py` was built and tested
  in Wave 5 and imported by nothing. Both are the same failure: a green suite
  certifying a module in isolation while the product never reaches it. Both now wired —
  the crew as a DEEP-effort evidence stage, the artifact as `result["_plan"]`.

- **The effort knob stopped short of intake.** The plan said intake→api→plan; only
  api→plan was built, so the one place an operator describes what a report is FOR
  could not set its depth. Closed with `POST /intake/{id}/effort`.

- **Deferred, with a reason.** `D28–30 / M8 parity` (one deep-mode venture judged blind
  against a BCC chapter) is NOT done: it needs a BCC chapter as the comparison artifact,
  which is not in the repo. Everything else in §5b through Wave 6 is landed.

  Suite at close: **1296 passed, 5 skipped.**

### M7 — MEASURED, AND NOT MET

Full R4 panel on the fresh 16-venture corpus. 174 agents, 14.3M tokens, 89 minutes,
`valid: true`, **0 verification gaps** — this is a sound reading, not a truncated one.

  | | entry | exit | target (M7) |
  |---|---|---|---|
  | pass | 13.0% | **13.5%** | ≥90% |
  | confirmed criticals | 61 | **53** | 0 |

**Flat.** 26/192 cells pass, 100 fail. And 99 of 107 flagged findings SURVIVED an
adversarial refutation pass whose default was `refuted=true` — these are not
scorer noise.

Per-row (pre-verification scoring), the shape is the finding:

  | row | PASS | FAIL | CRIT |
  |---|---|---|---|
  | R1 market-scale routing | 12 | 2 | 1 |
  | R4 business-model routing | 13 | 1 | 1 |
  | R2 TAM | 2 | 12 | 7 |
  | R6 unit economics | 0 | 14 | 9 |
  | R9 consumer/WTP | 1 | 11 | 8 |
  | R11 viability | 0 | 10 | 6 |
  | R12 integrity | **0** | **16** | **14** |

The only two rows that pass are R1 and R4 — exactly the two this program spent waves
fixing. Every row downstream of them fails. R12 fails on every single venture.

**THE GATES AND THE PANEL DISAGREE COMPLETELY: 219/219 vs 13.5%.** Worked example,
reproducible on `out/wave4_corpus/174ae091`:

  * `market_sizing.validation.passed = False`, `publishable = False`
  * the report renders `⚠ Failed validation — figures withheld`
  * …and, in the same document, "a massive **$1.22B** TAM", with a 65/100
    market-opportunity score built on it
  * `gates.d09_publishable_gated` returns **ok — "gated correctly"**

D09 checks that `publishable` is False and that a withhold banner EXISTS. It never
checks that the withheld number stays OUT of the prose. **The gate verifies a
disclosure was printed, not that the report obeys it.** That is the shape of most of
the 22: they read STRUCTURE (does a field exist, do two fields agree), while what
remains broken is SEMANTIC (does the narrative honour what the structure says).

So 219/219 is not evidence of quality, and adding more invariants of the same shape
should not be expected to move 13.5%. That is a conclusion about METHOD, and it needs
a decision before more building — recorded here rather than papered over.

Scorecard: `docs/baselines/wave6_exit_r4.json`.

### Post-M7 triage

M7 came back flat (13.0% → 13.5%), with the gates reading 219/219 against it. The
triage runs through the disagreement rather than around it.

**#1 — the D09 class: gates that check a disclosure EXISTS, not that the report OBEYS
it.** D09 verified `publishable=False` and that a withhold banner was rendered. Both
were true on 174ae091 while the same document printed "a massive $1.22B TAM" and
built a 65/100 score on it. All 4 corpus ventures that fail validation do this. D09
now also checks the withheld figures stay out of NARRATIVE prose (viability, the 4Ps,
the executive summary); the sizing table may still show the figure beside its warning,
because that is the disclosure. A test pins that a properly-withheld report still
passes — a gate, not a wall.

  Corpus went **219/219 (100.0%) → 215/219 (98.2%, 4 blocking)**. The corpus did not
  change; the gate got honest, and `baselines/wave4.json` was revised rather than left
  showing a 100% that was never true.

  Then an audit of all 22: **15 of 22 never read narrative prose at all.** That is the
  13.5%-vs-100% disagreement quantified — two thirds of the gate program cannot see
  the class of defect the panel is finding.

**#2 — R12's dominant cluster, and the biggest quality defect found so far.** Six of
the 14 R12 criticals named one cause. `economics.at_som_volume` was computed at
`som.high` and labelled `som_capture_pct: 100.0`, while the scenario table called the
identical row "130% of SOM, aggressive".

  | | |
  |---|---|
  | ventures affected | **12/16** — every one with the field |
  | implied capture | 120%–200%, all labelled 100% |
  | profit overstatement | 44% – 2.2× |
  | reports claiming profit on a LOSS | **2** (−$25,000/mo reported as +$81,667/mo) |

  Introduced in W4-1, deliberately, to make the claim bit-identical with the
  aggressive Y3 row. Agreeing was right; agreeing on the OPTIMISTIC row was not.

  The fix does not hardcode `som.mid` — it reads the BASE ceiling from `_y3_ceilings`,
  the same function financials uses to build the table. Two Python paths computing one
  quantity is how they drifted; now there is one, and the no-band ladder path stays
  coherent too. **D23** NEW gates the label against the implied capture. Re-running the
  fixed enrichment on real venture inputs: **0/12 → 12/12**.

**#3 — clustering the remaining 110 findings** (R2/R3/R5–R12) by root cause, one
analyst per row tracing to code, every claimed cause adversarially verified, then
deduped across rows. In flight.

**A standing caveat on all of the above.** Every number here is measured against a
corpus generated by the pre-fix code. The fixes are proven by re-running the affected
functions on real venture inputs, but the true post-fix R4 needs a regen (~2h) and a
fresh panel (~90min, ~14M tokens). Worth paying once, after the fix batch — not
between items.
