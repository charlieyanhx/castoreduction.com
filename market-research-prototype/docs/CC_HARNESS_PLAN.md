# Castor Harness v2 — Adopting Claude Code's 6-Layer Architecture (Clean-Room Plan)

**Goal:** rebuild Castor's execution core on the architecture that makes Claude Code the most
reliable long-running agent harness in production, adapted to what Castor actually does —
generate paid-grade, numbers-must-be-right market-research documents.

**IP/licensing ground rules (clean-room):** we adopt *concepts and patterns* as documented in
public reverse-engineering literature (minusx.ai "Decoding Claude Code", kirshatrov
claude-code-internals, shareAI-lab/analysis_claude_code, Piebald-AI system-prompt collections,
Anthropic's own published engineering posts). **No decompiled source is copied.** All code is
written fresh against our own registry/Evidence abstractions; dependencies stay MIT/BSD/Apache.

---

## Part 1 — Claude Code's architecture, as documented in the leak literature

The reverse-engineering analyses (esp. shareAI-lab's) describe a **6-layer design**:

| Layer | Claude Code (leaked names) | What it does |
|---|---|---|
| **L1 — Entry/UI** | CLI REPL, slash commands, hooks | user interaction, command routing, lifecycle hooks around every tool call |
| **L2 — Master loop** | `nO` main loop | ONE single-threaded conversation loop over a flat, append-only message history; streaming; no graph/state-machine — "agent = loop + LLM + tools" |
| **L3 — Context & memory** | `wU2` compressor, `AU2` 8-segment summary, CLAUDE.md, system-reminders, TodoWrite | keep the context window high-signal: layered always-loaded memory, auto-compaction at ~92% capacity into a structured 8-part summary, mid-stream steering via injected `<system-reminder>`s, TODO recitation as an attention anchor |
| **L4 — Tool system** | `UH1` scheduler, `MH1` 6-phase executor, permission gateway | tool discovery → arg validation → permission gate → concurrency-classified execution (read-only tools run parallel (≤10), mutating tools run serial) → result normalization → state record. Poka-yoke args (absolute paths), exact-string Edit discipline, descriptions-as-routing |
| **L5 — Sub-agents** | Task tool → `I2A` sub-agent | context isolation: a sub-agent gets its own fresh context + tool mask, runs the SAME loop, returns ONE compact tool_result; **at most one branch deep** (sub-agents cannot spawn sub-agents) — prevents context explosion and conflicting decisions |
| **L6 — Persistence** | messages.jsonl, todo files, session state | every session is an append-only JSONL transcript; todos and memory live on disk; resume = replay state, not re-run work |

**Cross-cutting prompt discipline** (from the leaked prompts): byte-stable system-prompt prefix
(KV-cache), "IMPORTANT"/"NEVER" emphasis for hard rules, negative scope in tool descriptions
("do NOT use when…"), worked examples over abstract rules, errors left in context as observations.

**Why this design wins** (corroborated by our verified harness research): flat single loop beats
multi-agent graphs for coherence (conflicting decisions live in one history); KV-cache-stable
prefixes are a 10× cost lever; compaction into a *structured* summary preserves decisions while
dropping bulk; one-branch sub-agents give parallelism without divergence; the tool layer being
boring and deterministic is what lets the model be creative safely.

**Verified sources for Part 1** (fetched and read — see HARNESS_LITERATURE.md §4 for the full
findings): minusx.ai *Decoding Claude Code* · kirshatrov *claude-code-internals* ·
Piebald-AI *claude-code-system-prompts* (500+ fragments, 231 tracked versions) ·
shareAI-lab *analysis_claude_code* / *learn-claude-code* · Yuyz0112 *claude-code-reverse* ·
promptlayer *master agent loop* · decodeclaude.com *compaction deep-dive* · outsightai +
georgesung traffic tracing · code.claude.com/docs/en/memory. Key verified numbers: >50% of CC's
LLM calls run on Haiku-class models; ~9.4k tokens of tool descriptions vs ~2.8k system prompt;
compaction historically at ~92% now headroom-based; TodoWrite forces FULL-list rewrites
(recitation is the mechanism); CLAUDE.md is a 4-layer concatenation delivered as a *user message
after* the system prompt and re-injected post-compaction; microcompaction demotes old tool
results to disk pointers keeping a hot tail inline.

---

## Part 2 — Castor today, mapped layer-by-layer

| Layer | Castor today | Grade |
|---|---|---|
| L1 | FastAPI (`api.py`) + workspace chat UI + jobs API. No lifecycle hooks, no operator commands | 🟡 partial |
| L2 | TWO loops: deterministic 18-step `plan.py` (the product) + bounded agent loop `harness/agent.py` (recitation, append-only observations, tool masking, budget) | 🟡 loop exists; no streaming events, fixed chain not plan-driven |
| L3 | LLM cache (content-addressed), temp0+seed; `model_directive` injections (a system-reminder analog); recitation in agent loop. **No compaction, no layered memory, no skill disclosure** | 🔴 biggest gap |
| L4 | `@tool` registry + uniform Evidence envelope + provenance trace + error-as-Evidence. **No scheduler/concurrency classes, no permission gateway, no arg poka-yoke, no response_format** | 🟡 half-built |
| L5 | `agents/` crew + `run_research_crew`. No one-branch rule, no output-schema contracts on spawn, sub-results not compacted | 🟡 |
| L6 | SQLite job store + `checkpoint()` after steps + provenance JSONL. **Resume re-runs from scratch; no event transcript; no lifecycle hooks** | 🟡 |

---

## Part 3 — The plan: Castor Harness v2, layer by layer

### L1 — Entry: operator commands + lifecycle hooks
*Adopt:* CC's hook model (Pre/Post per tool + per step) and slash-command routing.
- **1.1** `hooks.py`: `on_step_start/on_step_end/on_tool_call/on_failure/on_complete` — a tiny
  pub-sub the orchestrator fires. First consumers: live progress streaming to the workspace
  "Computer" panel (kills the last "dark capability"), degradation banner, provenance.
- **1.2** Operator directives in chat: `/regenerate <section>`, `/deepen <section>`, `/sources`,
  `/model-info` — routed like slash commands to pipeline entry points.
- *Skip:* a terminal REPL — Castor's surface is the web workspace.

### L2 — Master loop: keep the deterministic spine, make it plan-driven + streaming
*Key judgment call:* CC is a loop because coding is open-ended. Report generation is a
known workflow — and Anthropic's own guidance (verified) says **workflows beat agents for
well-understood tasks**. So we do NOT replace `plan.py` with a free loop. We adopt the loop's
*virtues* instead:
- **2.1** **Flat run ledger**: one append-only, per-run event history (`RunLedger`) that every
  step writes to (step start/end, Evidence refs, decisions, failures) — the single source of
  truth the way CC's message history is. Provenance becomes a view over it.
- **2.2** **Plan-as-artifact** (from REPORT_SPEC wish #4): the orchestrator emits `plan.json`
  (sections + figures referencing only existing result keys), a validator gates it, THEN
  execution follows it. plan.py's step order stops being implicit code and becomes checkable data.
- **2.3** **Streaming events** over the ledger → UI (via 1.1 hooks): the user watches steps,
  tool calls, and sources appear live, like CC's transcript.
- **2.4** The open-ended limb (`harness/agent.py`) stays the CC-style loop it already is;
  it gains L3/L4 upgrades below.

### L3 — Context & memory (the biggest gap → biggest wins)
- **3.1** **CASTOR.md memory hierarchy** (CLAUDE.md analog), layered and always-loaded in order:
  `operator.md` (user prefs: tone, depth, risk appetite) → `venture.md` (the brief; auto-written
  at intake) → `industry/<category>.md` (reusable industry knowledge packs). Injected as a
  byte-stable prefix block in every LLM call for that run.
- **3.2** **Structured compaction for the agent limb**: when the observation log nears the
  budget, compress into an 8-segment structured handoff (CC's AU2 pattern): objective / key
  decisions / evidence digest (with restorable pointers) / open questions / failures seen /
  next steps / constraints / sources. Reversible: bulky payloads drop to `evidence_id` pointers
  (we already store them) — never lossy on provenance. Verified refinements from the compaction
  deep-dive: (a) **microcompaction first** — demote old observations to Evidence-ID pointers
  while keeping a hot tail of recent ones fully inline (cheapest stage, nearly free since
  provenance already persists payloads); (b) **fixed checklist schema**, never freeform
  summarization; (c) after compaction, **re-materialize hot state from the provenance store**
  (current step, key Evidence), don't trust the summary; (d) **anti-thrash guard** — cap
  compactions per run and abort loudly rather than loop-compacting; (e) compact early enough
  that the compaction itself has headroom to run.
- **3.3** **System-reminder channel**: generalize `model_directive` into `inject_reminder(step,
  text)` — a uniform way any gate (validation, business-model router, run-health) steers any
  downstream LLM call. Same mechanism CC uses to steer mid-conversation.
- **3.4** **Skills as SKILL.md folders with progressive disclosure** (from the skills review):
  registry metadata always loaded (~100 tokens); procedural body loaded on trigger; bundled
  scripts *executed*, not read. Migrate 2 pilots first: `size_hyperlocal`, `citation`.
- **3.5** **KV-cache discipline** in `llm.py`: byte-stable system prefixes (no timestamps),
  stable tool-catalog ordering, append-only run context. (Gemini implicit caching → real
  latency/cost win; groundwork for Anthropic provider.)

### L4 — Tool system: finish the half-built layer
- **4.1** **Concurrency classes** on `@tool(concurrency="parallel_safe"|"serial"|"exclusive")`:
  read-only fetchers (OSM, Census, BLS, search) fan out ≤10 in parallel; LLM calls serial per
  provider; writes exclusive. A small `ToolScheduler` replaces ad-hoc ThreadPoolExecutors in plan.py.
- **4.2** **Permission gateway**: tiers `free | metered (needs key, costs quota) | paid | none`.
  Metered/paid tools declare cost; the gateway enforces per-run budgets and records spend to the
  ledger (CC's permission model, adapted from "can I touch this file" to "can I spend this quota").
- **4.3** **Poka-yoke args**: pydantic arg models on tools — reject relative/ambiguous inputs
  (e.g. `geocode_address` requires city+region; `bls_cex_spend` requires either a verified
  series id or a category that resolves). Bad args fail loud at the boundary, not deep inside.
- **4.4** **response_format enum** (`CONCISE|DETAILED`) on chatty tools (search, scrape,
  reviews): CONCISE for the agent loop (token diet), DETAILED for provenance.
- **4.5** **Descriptions-as-routing**: rewrite every `@tool/@skill/@agent` description as
  WHAT + WHEN + trigger keywords + **negative scope** ("Do NOT use for…"). The planner selects
  against these. (Verified: bad descriptions send agents "down completely wrong paths".)

### L5 — Sub-agents: CC's spawn discipline
- **5.1** **One-branch rule**: `run_agent(depth=1)` may spawn workers at depth 2; depth-2 CANNOT
  spawn. Enforced in the registry, not by convention.
- **5.2** **Spawn contracts**: every crew spawn carries `{objective, output_schema, tool_mask,
  boundaries}` — vague delegation is a validation error (Anthropic's multi-agent lesson, verified).
- **5.3** **Compact returns**: a sub-agent returns ONE Evidence (schema-validated), never its
  transcript; its full log goes to the ledger for debugging, not into the parent's context.
  (Context isolation — the whole point of CC's Task tool.)

### L6 — Persistence: resume, don't re-run
- **6.1** **Run transcript**: the RunLedger (2.1) persists as per-run JSONL next to the job row —
  CC's messages.jsonl analog. The debug panel and audits read it directly.
- **6.2** **True resume**: `checkpoint()` already snapshots results; add `resume(job_id)` that
  replays the ledger, skips completed steps with intact Evidence, and re-runs only the
  failed/missing tail. Kills the "regenerate everything after a Gemini blip" tax — the single
  biggest reliability lever we have.
- **6.3** **Session continuity for the workspace**: a venture's runs chain (`_previous_job_id`
  exists); expose diffs-between-runs (already have `_deltas_vs_previous`) as a first-class view.

### Cross-cutting — prompt discipline (free wins, do throughout)
- Hard rules phrased with IMPORTANT/NEVER in system prompts (validation gate directives,
  model_directive, judge rubrics); worked input→output examples in every skill body; negative
  scope everywhere; byte-stable prefixes (3.5).

---

## Part 4 — What we deliberately do NOT adopt (and why)
| CC feature | Skip because |
|---|---|
| Free-form master loop for the whole product | report generation is a known workflow; determinism + testability are Castor's moat (verified: "workflows beat agents for well-understood tasks") |
| Full multi-agent orchestration (3-5 parallel researchers everywhere) | ~15× token economics on a free Gemini tier; use only in the research limb where breadth pays |
| Terminal UI/REPL | our surface is the web workspace |
| h2A async mid-run steering queue | Castor is batch; replay-from-cache is the right substitute (verified skip) |
| LLM-based Bash injection checks | our tools are parameterized functions, not model-composed shell; a rule-based check in the tool wrapper suffices |
| Vector/RAG store for source documents | CC's verified "LLM search >>> RAG" — Castor is already tool-retrieval-first; keep grep/filter over cached sources |

**Correction from verified research — model tiering moves from skip → ADOPT:** >50% of CC's LLM
calls run on Haiku-class models (summarize/parse/classify/Explore). Tiering doesn't need a second
provider — Gemini has flash vs flash-lite. Add `tier="utility"|"main"` to `llm.py:call_json` and
route evidence summarization, classification, extraction, and label generation to flash-lite.
Slots into **P3**. This is the single biggest cost lever CC validates.

## Part 5 — Execution order (each phase independently shippable + testable)

| Phase | Items | Effort | Acceptance |
|---|---|---|---|
| **P0** | 4.5 descriptions+negative scope; cross-cutting prompt discipline; 3.5 KV-stable prefixes | 1 day | planner mis-routing ↓ in agent-loop tests; byte-identical prefixes verified |
| **P1** | 2.1 RunLedger + 6.1 transcript + 1.1 hooks + 2.3 streaming | 2-3 days | live step/tool stream visible in workspace; provenance = ledger view; all tests green |
| **P2** | 6.2 resume | 1-2 days | kill a run mid-pipeline → `resume` completes it without re-running finished steps (test: ≤1 duplicated LLM call) |
| **P3** | 4.1 scheduler + 4.2 permission gateway + 4.3 poka-yoke | 2-3 days | OSM/Census/BLS fan out in parallel (wall-clock ↓ ~30%); per-run quota enforced; bad args fail at boundary with clear errors |
| **P4** | 3.1 CASTOR.md hierarchy + 3.3 reminder channel + 3.2 compaction | 2-3 days | operator prefs honored across runs; agent limb survives 3× longer research without budget exhaustion |
| **P5** | 3.4 SKILL.md pilots + 5.1-5.3 spawn contracts + 2.2 plan-as-artifact | 3-5 days | 2 skills migrated with progressive disclosure; spawn without schema = validation error; plan.json gates rendering |

Total: ~2-3 weeks of focused work, each phase leaving the system better than before and fully
regression-tested against the 16-venture corpus (`/tmp/audit` harness + audit panel gate).

## Part 6 — How this maps to Castor's actual pain (the "why")
- **Trust/auditability** (the audit's #1 theme) → L2 ledger + L6 transcript + L4 gateway make
  every number's lineage a first-class artifact (extends the provenance panel we shipped).
- **Reliability on a flaky free tier** → P2 resume converts LLM blips from "regenerate the
  world" into "re-run one step"; P3 parallel fetchers cut the window in which blips can hit.
- **Consistency bugs (SOM, CAC, churn)** → 2.2 plan-as-artifact + 3.3 reminder channel give
  gates a uniform way to pin canonical values for every downstream call.
- **Dark capabilities** → 1.1 + 2.3 stream the backend's real work into the UI, which is the
  Manus-parity UX ask from the start.
