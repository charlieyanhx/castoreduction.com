# Castor Architecture — The Harness

> **Status:** Design locked (cycle33). This is the source-of-truth architecture doc.
> Component-level detail lives in `STACK.md`; numbers methodology in `SIZING.md`.

---

## The decision

Castor is built as a **custom agent harness**, not a fixed pipeline. The harness owns
orchestration (registry, sub-agents, action execution, context discipline, provenance).
Best-in-class open-source research agents — **STORM** and **GPT Researcher** — are forked
as *components that run inside* the harness, not as the system itself. The one layer no
open-source project provides — **authoritative, triangulated, bank-grade numbers** — is
built by us and is the moat.

In one line:

> **Castor = a Claude-Code-style harness, with STORM + GPT Researcher as forkable limbs,
> wrapped around a proprietary numbers-right engine, sold as the platform for new businesses.**

---

## Why a harness, not a pipeline

The original system was a deterministic 22-step pipeline (`plan.run_plan`). That gave us
reproducibility and a benchmark — both worth keeping. But a fixed pipeline cannot:

- handle *any scale* (global SaaS → a single restaurant in LA need different methods),
- explore open-ended sub-problems (competitor discovery is a search, not a step),
- compose new capabilities without editing the orchestrator,
- scale context as tool count grows past ~70.

A harness solves all four. The deterministic pipeline becomes **one mode** the harness can
run (the "spine"); open-ended work runs in **agentic limbs**. Nothing built so far is thrown
away — `tools/registry.py` and `skills/registry.py` already *are* the harness core in embryo
(uniform `Evidence` envelope, auto-discovery, per-call timing, error isolation).

This is the convergent design every serious research agent independently arrived at
(Manus, GPT Researcher, STORM, Claude Code — see `docs/archive/process/` references). We are
assembling it from proven parts, not inventing it.

---

## The four layers

```
┌─────────────────────────────────────────────────────────────────────┐
│  LAYER 4 — BUSINESS SURFACE                                          │
│  SaaS tiers · capital marketplace · ecosystem partners · accounts   │
├─────────────────────────────────────────────────────────────────────┤
│  LAYER 3 — NUMBERS-RIGHT ENGINE  (the moat — Castor-only)           │
│  scale classifier · sizing methods · triangulation · validate · CI  │
│  every figure carries provenance; LLM assembles, never invents      │
├─────────────────────────────────────────────────────────────────────┤
│  LAYER 2 — AGENTIC LIMBS  (forked OSS, runs inside the harness)     │
│  GPT-Researcher executors · STORM perspective engine · CodeAct loop │
│  open-ended discovery, each result source-tracked                   │
├─────────────────────────────────────────────────────────────────────┤
│  LAYER 1 — HARNESS CORE  (ours, exists today, expand)              │
│  tool registry · skill registry · Evidence envelope · sub-agents    │
│  CodeAct executor · todo recitation · KV-cache discipline · spine   │
└─────────────────────────────────────────────────────────────────────┘
```

### Layer 1 — Harness core (exists, expand)

What it owns, and where it lives today:

| Capability | Status | Source |
|---|---|---|
| Tool registry + `@tool` decorator | **exists** | `tools/registry.py` |
| Skill registry + `@skill` decorator | **exists** | `skills/registry.py` |
| `Evidence` envelope (uniform return) | **exists** | `tools/registry.py` |
| Scraper modules (first shimmed split, W2) | **exists** | `tools/sources/{trustpilot,forums,articles,vertical}.py`; `sources.py` re-exports (domain/trends/ads/social clusters still there, moving with the waves that touch them) |
| Error isolation (no crash propagation) | **exists** | both registries |
| Deterministic spine (22-step pipeline) | **exists** | `plan.py`, `skills/pipeline.py` |
| **CodeAct executor** (model writes Python over the registry) | build | `harness/codeact.py` |
| **Sub-agent dispatch** (isolated context, summary-back, no recursion) | build | `harness/subagent.py` |
| **Todo recitation** (re-inject plan to fight drift) | build | `harness/context.py` |
| **KV-cache discipline** (stable prefixes, tool masking) | build | `harness/context.py` |

Harness-core design rules (lifted from Manus + Claude Code, both independently verified):

1. **Stable tool definitions.** Tool descriptions never change mid-run. Availability is
   gated by *masking*, never by add/remove — this preserves the KV-cache prefix.
2. **Append-only context.** No mutation of prior turns; failures stay in context so the
   model learns from them.
3. **Isolated sub-agents.** Heavy work (a competitor deep-dive, a Census pull) runs in a
   sub-agent with its own context and returns only a concise `Evidence` summary. Sub-agents
   cannot spawn sub-agents (no recursion).
4. **Recitation.** The active todo/plan is re-injected near the end of context to fight
   "lost-in-the-middle" drift on long runs. (Directly mitigates Bug K — degradation under
   sustained LLM load.)
5. **Filesystem/parquet as memory.** Heavy intermediate results (scrapes, embeddings,
   Census joins) persist to disk and are read back, not re-stuffed into context.

### Generator-evaluator-refine loop (Anthropic harness pattern)

Closing the #1 gap from Anthropic's "Harness design for long-running apps": an
artifact is generated, scored by an **independent evaluator** against a **contract
agreed up front**, and **refined until it passes** — not generated once and shipped.

- `harness/evaluate_refine` — generic loop: keep-the-best (never regress), bounded
  rounds, stops on contract-met / no-improvement. Knows nothing about reports.
- `skills/refine_report` — the report adapter: evaluator = the independent judge
  (`benchmarks/judge`) **anchored by the deterministic `validate_numbers` gate** (a
  blocked gate forces `validation→0` regardless of the judge, closing the
  "evaluator talks itself into approval" anti-pattern); refiner regenerates only the
  sections feeding the failing dimensions; contract = per-dimension thresholds.

This makes external evaluation + concrete criteria + a refine loop first-class, and
reuses the judge we built for the Manus benchmark as the product's quality engine.

### Layer 2 — Agentic limbs (fork, don't rebuild)

Open-ended research is delegated to forked OSS, wrapped as `@skill`s:

| Limb | Forked from | Wrapped as | Job |
|---|---|---|---|
| Parallel research executors | **GPT Researcher** | `discover_competitors_skill` | planner → concurrent executors → publisher, each snippet source-tracked |
| Perspective / dialogue engine | **STORM** | `personas_skill`, `consumer_research_skill` | simulated editor↔expert Q&A → multi-perspective output, outline-first |
| Action loop | **Manus CodeAct pattern** | `harness/codeact.py` | model writes Python calling registry tools; sandbox executes; result observed |

We take their *code and techniques*, not their products. Each limb returns `Evidence` into
the harness, where Layer 3 validates anything numeric before it ships.

**License note:** GPT Researcher and STORM licenses must be confirmed compatible with
commercial use before code is vendored — tracked in `STACK.md` license audit. Use as a
dependency/import where the license requires it; vendor only what is permissibly licensed.

**Specialized agents (`agents/`, cycle33).** On top of the harness sits a registry of
named research agents — the orchestrator-worker pattern. Each worker owns one sub-domain
and runs the harness over a *masked* tool surface in isolated context:

| Agent | Role | Surface | Produces |
|---|---|---|---|
| `market_scan_agent` | competitive/landscape analyst | scrape, ads, trend | `market_scan` |
| `demand_signal_agent` | voice-of-customer / demand | customer_voice, trend | `demand_signal` |
| `pricing_intel_agent` | pricing intelligence | scrape, ads | `pricing_intel` |
| `local_market_agent` | trade-area analyst | geo | `local_market` |
| `plan_research` | lead planner (dynamic crew composition) | — | `research_plan` |
| `synthesis_agent` | lead synthesist (no tools) | — | `research_brief` |
| `run_research_crew` | orchestrator (fan-out/fan-in) | — | `research_brief` |

`run_research_crew(dynamic=True)` asks `plan_research` which specialists fit the venture,
dispatches them in **parallel with per-worker isolation** (a dead worker never sinks the
crew), then the tool-less `synthesis_agent` integrates their Evidence into one brief.
Agents are auto-discoverable (`AGENT_REGISTRY`, `/api/agents`) and return the same Evidence
envelope as tools and skills. Tool→skill→agent is the capability ladder: atomic →
deterministic composition → autonomous goal-driven loop.

### Layer 3 — Numbers-right engine (the moat)

This is the layer none of the references have, and the reason Castor is defensible. Every
research agent in the wild produces a *cited prose report*; none produce *triangulated,
authoritative-data-grounded numbers* a lender can underwrite.

| Component | Job | Build |
|---|---|---|
| `classify_market_scale` | route to the right sizing method | `skills/sizing/classify.py` |
| `size_hyperlocal` | trade-area catchment model (restaurant-in-LA) | `skills/sizing/hyperlocal.py` |
| `size_regional` | per-location × rollout | `skills/sizing/regional.py` |
| `size_national_digital` | top-down ÷ bottom-up (refactor existing) | `skills/sizing/digital.py` |
| `validate_numbers` | triangulation + sanity gate; fails loud | `skills/sizing/validate.py` |
| `geo_trade_area` | isochrone (OSRM) ∩ Census block groups | `tools/geo.py` |

Discipline (full spec in `SIZING.md`):

1. **Bottom-up beats top-down.** Top-down only as a sanity ceiling.
2. **LLM never generates a number.** Census / BLS / BEA / World Bank / OSM are the ground
   truth. The LLM fetches, assembles, narrates. Any figure without a `source` does not ship.
3. **Triangulate.** Every headline number computed ≥2 independent ways; convergence = high
   confidence, divergence = a visible flag.
4. **Provenance.** Every number links to its source + the formula that produced it.
5. **Confidence intervals.** P10/P50/P90 with stated assumptions — never false precision.
6. **Validate loud.** Impossible numbers (share >100%, SOM > trade-area spend) are flagged,
   not shipped.

### Layer 4 — Business surface

The platform layers from the business plan (`docs/` business notes): tiered SaaS (free →
Founder → Growth → Enterprise), the capital marketplace (SBA/CDFI/RBF/equity/direct raise),
the ecosystem partners, and accounts/billing. Each capital partner and each ecosystem vendor
is — by the same registry pattern — one new `@tool`. Adding a lender = one file.

---

## How each reference maps in

| Reference | What we take | Where it lands |
|---|---|---|
| **Claude Code** | harness shape: registry, isolated sub-agents, todo recitation, plan mode, permission model | Layer 1 |
| **Manus** | CodeAct action format, KV-cache discipline, tool masking, filesystem-as-memory, keep-failures-in-context | Layer 1 |
| **GPT Researcher** | planner → concurrent executors → publisher; per-snippet source-tracking | Layer 2 |
| **STORM** | perspective-oriented simulated dialogue; outline-first; claim-level citation | Layer 2 |
| **Castor (ours)** | numbers-right engine: scale-adaptive sizing, triangulation, provenance, validation | Layer 3 |

The convergent flow:

```
PLAN  →  PARALLEL ISOLATED WORKERS  →  AGGREGATE w/ PROVENANCE  →  WRITE
classify   sub-agents running CodeAct       source-track +            STORM outline-first
+ select   over the registry (GPTR-style)   triangulate (Layer 3)     + narration skill
+ recite                                     + validate_numbers
```

---

## The moat (why this is defensible)

Three compounding layers of defensibility, each enabled by this architecture:

1. **Orchestration quality.** A harness that composes 70+ best-in-class OSS components with
   benchmark-tuned selection is hard to copy — the value is in the composition and the tuning,
   which compound with every cycle. (Our 17-case benchmark is the tuning signal.)
2. **The numbers-right engine.** The hard, valuable, *un-open-sourced* part. Anyone can emit a
   plausible TAM; almost no one grounds every number in authoritative data with triangulation
   and provenance. This is the layer a bank, an SBA officer, or an investor pays for.
3. **The data flywheel + business graph.** Every plan generated, every funded deal, every
   capital outcome becomes proprietary signal that sharpens matching and underwriting — and a
   founder whose plan, capital, banking, and cap table all touch Castor is structurally hard to
   dislodge. The harness makes each new capital/ecosystem partner a one-file addition, so the
   graph grows cheaply.

References give us the engine. The numbers-right layer and the business graph are what turn an
engine into a company.

---

## Current state → target

**Have today:** tool registry (30 tools / 7 categories), skill registry (12 skills), Evidence
envelope, deterministic 22-step spine, 17-case benchmark, config profiles, deploy artifacts.

**Build next (harness + moat), ordered:**

1. **Harness core** — CodeAct executor, sub-agent dispatch, todo recitation, cache discipline
   (`harness/`). Turns the registry into a real agentic substrate.
2. **Numbers-right engine** — `geo` tools (Census/BLS/BEA/OSRM), scale classifier, four sizing
   skills, `validate_numbers`. Delivers "any scale, numbers right" — the restaurant-in-LA test.
3. **Agentic limbs** — fork GPT Researcher executor for competitor discovery; fork STORM
   perspective engine for personas + consumer research.
4. **OSS stack expansion** — the remaining `STACK.md` components (NLP, search/scrape, pricing,
   eval, orchestration infra).
5. **Business surface** — accounts, billing, capital marketplace, ecosystem partner tools.

Every step is additive to the registry. The deterministic spine keeps passing the benchmark
throughout; the harness and moat layers light up alongside it.

---

## Design invariants (do not violate)

- The spine has **deterministic control flow, stochastic content**: step order and the
  validation gate are reproducible; the LLM-generated *content* of each step is not.
  Do not claim reproducible *numbers* — claim a reproducible *process* + an enforced gate.
- No number ships *as sourced* unless it has a real `source`; LLM estimates must be
  labeled estimates (see C1). The `validate_numbers` gate is mandatory, and a failed
  gate marks sizing **unpublishable** (the renderer hard-banners it) — not advisory.
- Authoritative data (Census, BLS) is the ground truth for counts and per-unit values;
  the LLM may classify/route but should not *invent* a load-bearing figure.
- Tool/skill definitions are stable; availability is masked, not mutated.
- New capabilities are new `@tool`/`@skill` files — the orchestrator is not edited to add one.
- Sub-agents are isolated and non-recursive.
