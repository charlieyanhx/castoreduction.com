# Skills, Tools & Orchestration: making Castor generate documents like Claude & Manus

A design document grounded in primary sources (Anthropic Agent Skills, the Anthropic multi-agent research system, Stanford STORM/Co-STORM, OpenAI/Google/Perplexity deep research, GPT-Researcher) and mapped against Castor's actual code: a `@tool/@skill/@agent` registry, a deterministic `plan.py` orchestrator, an `llm.py` multi-backend layer, a per-run `provenance.py` trace, a weighted LLM judge (`benchmarks/judge.py`), a generator-evaluator-refine loop (`harness/refine.py` + `skills/refine_report.py`), and Jinja templates (`templates/report.html`).

---

## 1. Executive summary — the 5 highest-leverage ideas

1. **Package procedural knowledge as SKILL.md folders with 3-level progressive disclosure.** A skill is a directory: YAML frontmatter (`name` + `description`) + a Markdown body + bundled scripts/templates/references. Metadata (~100 tokens) is always loaded; the body (<5k tokens) loads on trigger; bundled files/scripts load or *execute* only when referenced — so their source never enters context. Castor's `@skill` registry stores Python `produces`/`consumes` metadata but **no procedural body and no progressive disclosure**. This is the single biggest structural gap.

2. **Retrieve-then-write with a structured claim→source evidence store, plus a decoupled post-draft CitationAgent.** Every SOTA system writes prose *only against a curated, attributed evidence set*, and Anthropic runs citation as a *separate* final pass. Castor's `Evidence` envelope tracks source-per-*call* but there is no claim→source-span store and no "no sentence ships without a backing record" rule. Its `integrity.provenance.n_sourced/n_total` counts sourced *figures*, not sentence-level attribution.

3. **Plan-validate-execute on a first-class, separately-gated outline.** STORM proves outline quality drives final organization; Anthropic's skills emit a structured plan, validate it with a script *before* applying. Castor's `plan.py` is a fixed 14-step chain whose section structure is a side effect of writing — there is no validated `plan.json` of sections/figures referencing only existing data fields.

4. **Visual QA of rendered output, not just content QA.** The pptx skill renders slides to images and inspects them with the mindset "assume there are problems." Castor validates *numbers* (`skills/sizing/validate.py`) and *prose* (judge) but never looks at the rendered HTML/PDF for overflow, empty sections, or broken layout.

5. **Degrees-of-freedom discipline: push fragile, consistency-critical work into LOW-freedom scripts/Jinja; keep narrative HIGH-freedom.** Anthropic's guidance — "prefer pre-made utility scripts," "solve, don't punt," no "voodoo constants." Castor already does this for sizing math and 4Ps; the principle should govern *every* deterministic step (citation formatting, table sums, schema-valid HTML) and be the explicit rule when adding components.

---

## 2. The four layers

### Layer 1 — Skills (packaged procedural knowledge)

**SOTA pattern.** A skill = a folder whose entrypoint is `SKILL.md`: required YAML frontmatter (`name` ≤64 chars lowercase-hyphen, `description` ≤1024 chars), then a Markdown procedural body, plus optional sibling `scripts/`, `references/`, `assets/`. Three loading tiers with explicit costs: **Level 1 metadata** (~100 tokens, always preloaded — dozens of skills cost nothing), **Level 2 body** (loaded on trigger, target <5k tokens / <500 lines), **Level 3+ bundled files** (loaded or *executed* only when referenced; executed scripts return only stdout, so their code never costs context → "effectively unbounded"). The `description` is the load-bearing routing field: third-person, state WHAT + WHEN, pack trigger keywords, add **negative scope** ("Do NOT use for…"). Match *degrees of freedom* to task fragility (prose for open-ended, exact scripts for fragile). Bake in templates + worked input→output examples. Develop evals-first with two-Claude A/B iteration.

**Who does it.** Anthropic (the standard's originator; production docx/pptx/xlsx/pdf skills ship with `validate.py`/`recalc.py`/`pack.py`). Open standard released Dec 2025, now adopted by 40+ tools (Cursor, Copilot, Codex, Gemini CLI, Goose).

**Primary sources.**
- Anthropic Engineering — *Equipping agents for the real world with Agent Skills*: https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
- Claude Platform Docs — *Agent Skills overview* (3 levels + token costs): https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- Claude Platform Docs — *Skill authoring best practices*: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
- `anthropics/skills` (production SKILL.md + validators): https://github.com/anthropics/skills
- Agent Skills open standard: https://agentskills.io/home

### Layer 2 — Tools (atomic capabilities)

**SOTA pattern.** Tools hit one external surface and return a uniform envelope; their source is bundled and *executed*, returning only results to keep context lean and output reproducible. Reliability beats flexibility: pre-made utility scripts that handle their own errors ("solve, don't punt"). Dynamic context injection — run a fetch/computation and inline its output *before* the model writes — grounds generation in deterministic live data.

**Who does it.** Anthropic (skills' bundled scripts; Claude Code's `!​`command`` preprocessing). GPT-Researcher (per-source summarize + source-track, ContextManager compression). All deep-research systems separate retrieval from generation.

**Primary sources.**
- Anthropic best-practices (degrees of freedom, scripts-over-generated-code): same URL as above.
- `assafelovic/gpt-researcher`: https://github.com/assafelovic/gpt-researcher
- Claude Code skills (dynamic injection, `context:fork`, `${CLAUDE_SKILL_DIR}`): https://code.claude.com/docs/en/skills

**Castor today.** `tools/registry.py` already nails this: `@tool` returns a uniform `Evidence` envelope (`source/category/count/payload/error/skeleton/cost_meta`), auto-times, catches exceptions, and records to `provenance.py` at the choke point. This layer is essentially at SOTA.

### Layer 3 — Orchestration

**SOTA pattern.** **Orchestrator-worker**: a lead agent plans via extended thinking, then spawns 3–5 parallel subagents that act as "intelligent filters," each with an explicit objective + output schema + tool/source guidance + boundaries (Anthropic's multi-agent system beat single-agent Opus by 90.2%; token usage explained ~80% of performance variance; cost ~15× a chat turn). **Effort scaling**: 1 agent / 3–10 calls for simple fact-finding, 10+ subagents for complex research. **Iterative search-read-reason** with mid-run plan refinement (OpenAI trained this end-to-end with RL; Perplexity/Gemini run adaptive loops; Perplexity flags conflicting claims). **Outline-first** pre-writing (STORM: Knowledge Curation → Outline Generation → Article Generation → Polishing). **Multi-perspective** question-asking widens coverage (STORM personas; Co-STORM moderator + mind map). Keep the *orchestrator* deterministic and add **checkpoint/resume**.

**Who does it.** Anthropic (orchestrator-worker, CitationAgent, end-state eval, checkpoints/rainbow deploys); Stanford OVAL (STORM/Co-STORM); OpenAI/Google/Perplexity (deep research loops); GPT-Researcher (planner + parallel executors).

**Primary sources.**
- Anthropic — *How we built our multi-agent research system*: https://www.anthropic.com/engineering/multi-agent-research-system
- STORM (NAACL 2024): https://arxiv.org/abs/2402.14207 · repo: https://github.com/stanford-oval/storm
- Co-STORM (EMNLP 2024): https://arxiv.org/abs/2408.15232
- OpenAI deep research: https://openai.com/index/introducing-deep-research/
- Google Gemini deep research: https://blog.google/products/gemini/google-gemini-deep-research/
- Perplexity deep research: https://www.perplexity.ai/hub/blog/introducing-perplexity-deep-research

**Castor today.** Strong. `agents/planner.py` is genuine orchestrator-worker (lead picks a subset of `WORKER_ROSTER`, with deterministic guards dropping hallucinated names + default-crew fallback); `harness/run_agent` runs isolated sub-agents over masked tool surfaces with a step budget; `skills/perspective.py` is a faithful STORM adaptation (N personas → grounded interviews → deterministic aggregation). Gaps: no *validated outline* artifact, no *bounded gap-filling loop* after first-pass retrieval, no checkpoint/resume.

### Layer 4 — Document generation (trust + format)

**SOTA pattern.** **Retrieve-then-write**: no sentence without a backing source record. **Decoupled citation** (Anthropic's CitationAgent is a final pass aligning every claim to evidence). **Generator-evaluator loop / LLM-as-judge against an explicit rubric**: factual accuracy, citation accuracy, completeness, source quality, plus STORM's verifiability/coverage/coherence — and evaluate the **end-state**, not the agent's path. **Feedback loops with verifiable intermediate outputs**: validate→fix→repeat until clean (xlsx requires zero formula errors); **visual QA** by rendering to images and inspecting. **Known failure modes to guard**: source-bias transfer, over-association of unrelated facts, unverifiable claims, poorly conveyed uncertainty (so source-diversity checks, single-source co-location verification, and mandatory hedging when evidence is thin). **Provenance convention**: every hardcoded figure carries `Source: [system], [date], [ref], [URL]`.

**Who does it.** Anthropic (CitationAgent, judge rubric, end-state eval, xlsx provenance notes); Stanford (outline + article grading, editor study naming the failure modes); OpenAI/Gemini ("cite specific sentences/passages"); GPT-Researcher (aggregate 20+ sources to reduce hallucination).

**Primary sources.** Anthropic multi-agent (CitationAgent, rubric, end-state) and STORM (failure modes, grading) — URLs above; `anthropics/skills` (validate/QA loops) — URL above.

**Castor today.** Best-developed layer for *rigor*: `benchmarks/judge.py` is a 10-dimension weighted blind judge (provenance/method_fit/triangulation/defensibility weighted 1.5×); `skills/refine_report.py` + `harness/refine.py` run a real generator-evaluator-refine loop with a *deterministic gate anchor* that overrides the judge on `validation` (closing the "evaluator talks itself into approval" trap — a pattern even Anthropic doesn't document); `skills/triangulate.py` enforces independence-by-origin; `skills/sizing/validate.py` catches `SOM>SAM`, share>100%, formula mismatches. Gaps: **no claim→source store**, **no post-draft CitationAgent**, **no visual/render QA**, no source-diversity / over-association / mandatory-hedging guards.

---

## 3. Manus vs Claude vs deep-research systems — for DOCUMENT output

| System | Strongest for document output | Weakness for a paid report product |
|---|---|---|
| **Claude (Anthropic skills + multi-agent)** | **Format fidelity + verifiable artifacts.** SKILL.md packaging with progressive disclosure; production docx/pptx/xlsx/pdf skills with bundled validators and validate→fix→repeat loops; **decoupled CitationAgent**; LLM-judge rubric; end-state evaluation; provenance notes on every hardcode. The reference for *trustworthy, well-formatted* output. | Multi-agent cost ~15× a chat turn; needs the harness around it. |
| **Manus** | **Autonomous end-to-end artifact delivery** — does the whole job (research → structured deliverable) with strong polish; the bar Castor benchmarks against in `benchmarks/manus_comparison.md` (and the source of Castor's real failure cases: "166k × $50 = $845M", segmentation summing to SAM). | Less transparent methodology; the exposed failure modes are exactly *numeric-rigor* gaps Castor's validators target. |
| **STORM/Co-STORM** | **Organization + coverage breadth.** Outline-first pre-writing; perspective-guided multi-angle question-asking; Co-STORM mind map; explicit grading of *both* outline and article; names the trust failure modes (source-bias transfer, over-association). Best **pre-writing structure** model. | Wikipedia-style prose, not paid-report formatting; citations grounded but not a polished deliverable. |
| **OpenAI / Gemini / Perplexity deep research** | **Adaptive search-read-reason loops** that refine the plan mid-run and cite specific sentences/passages; aggregate hundreds of sources fast; Perplexity flags conflicting claims. Best **live-evidence gathering + iterative gap-filling**. | Document is a sourced web summary, not a designed report; openly hallucinate and convey uncertainty poorly. |
| **GPT-Researcher (open)** | **Practical retrieve-then-write reference impl** to port from: planner → parallel scrapers → per-source summarize+track → ContextManager compression → multi-format ReportGenerator. | Generic formatting; quality depends on the wrapping pipeline. |

**Net:** Castor should keep its STORM-style breadth and its judge/refine/numeric rigor (already strong), and **borrow from Claude**: SKILL.md packaging + progressive disclosure, the decoupled CitationAgent, validate→fix→repeat with **visual QA**, and provenance-on-every-figure.

---

## 4. Gap analysis & ranked upgrades

**Where Castor already matches SOTA** (don't rebuild): the `@tool` Evidence envelope (Layer 2 ≈ done); orchestrator-worker (`agents/planner.py` + `harness/`); STORM perspectives (`skills/perspective.py`); weighted blind judge + gate-anchored refine loop (`benchmarks/judge.py`, `skills/refine_report.py`); triangulation-by-origin; numeric validation gate; per-run provenance trace.

**The structural gaps:** (a) `@skill` carries no *procedural body* and no *progressive disclosure* — it's a Python decorator, not a SKILL.md folder; (b) no claim→source-span store and no post-draft citation pass; (c) `plan.py` is a fixed chain with no validated outline/figure plan; (d) no render/visual QA; (e) no anti-bias / over-association / mandatory-hedging guards; (f) routing blurbs (`produces`, docstrings) aren't written as discovery descriptions.

### Ranked upgrades (impact × effort)

| # | Upgrade | Impact | Effort | Castor file / subsystem |
|---|---|---|---|---|
| **1** | **Claim→source evidence store + post-draft CitationAgent.** Persist `{claim, source, span, url, date}` records in the retrieval layer; add a `@skill`/`@agent` that runs after draft assembly, aligns each sentence to a record, inserts/repairs citations, and flags unsupported claims. Forbid the Jinja layer from emitting an unbacked sentence. This is the #1 paid-report trust lever and is *missing*. | **Very high** | Med | `tools/registry.py` (extend `Evidence`/add store), new `skills/citation.py`, `templates/report.html` `.citations`, `plan.py` post-draft step |
| **2** | **Routing-grade descriptions + negative scope on every component.** Rewrite each `@tool`/`@skill`/`@agent` selection blurb in third person stating WHAT + WHEN + trigger keywords + "Do NOT use when…". Use them in `agents/planner.py` selection. Cheap, immediately cuts mis-routing/over-triggering. | **High** | **Low** | `tools/registry.py`, `skills/registry.py`, `agents/registry.py`, `agents/planner.py` `WORKER_ROSTER` |
| **3** | **Plan-validate-execute outline as a first-class artifact.** Have the LLM emit `plan.json` (sections + figures, referencing only existing result keys); validate with a script (catch nonexistent fields/conflicts/empty sections); score outline coverage; gate before rendering. STORM-proven; fits a deterministic orchestrator. | **High** | Med | `plan.py` (new gated step before assembly), reuse `skills/sizing/validate.py` pattern |
| **4** | **Visual/render QA pass.** Render `report.html`/PDF to images, inspect for overflow, empty sections, broken tables/charts ("assume there are problems"); loop until clean. Complements the existing numeric/prose checks. | **High** | Med | new `skills/render_qa.py` (soffice/pdftoppm or Playwright), `templates/report.html`, `plan.py` finalize |
| **5** | **SKILL.md-style packaging + progressive disclosure for section procedures.** Give each report section a folder: tiny third-person description (Level 1, fed to planner routing) + concise procedural body (Level 2, loaded only when selected) + bundled templates/validators/reference (Level 3, executed/loaded on reference). Turns scattered prose in `plan.py`/`llm.py` into an inspectable, low-context-cost library. | **High** | High | `skills/` (add bodies + `produces`-keyed folders), `skills/registry.py` (load description/body/budget), `llm.py` (selective loading) |
| **6** | **Anti-failure guards.** Source-diversity/quality check before write (limit bias transfer); a verifier rejecting "over-associated" claims not co-located in one source; mandatory hedged language when evidence is thin or sources conflict (surface as a flag like the existing validation gate). Directly answers STORM's named failure modes. | **High** | Med | `skills/triangulate.py` (already has origin independence), new checks in `skills/refine_report.py` / `_validation_gate` in `plan.py` |
| **7** | **Bounded gap-filling loop after first-pass retrieval.** An evaluator finds under-covered outline sections + unresolved conflicts and issues targeted follow-up queries before writing; cap iterations for determinism/cost. Mirrors deep-research adaptive loops within Castor's deterministic budget. | Med | Med | `plan.py` (post-retrieval), `harness/run_agent`, `agents/planner.py` |
| **8** | **Checkpoint/resume + provenance-on-every-figure.** Add checkpoint/resume so a long run recovers from a failed subagent instead of restarting; attach `Source: [system], [date], [ref], [URL]` to every non-derived number (extends the existing trace and `integrity.provenance.n_sourced/n_total` from figure-counts toward true per-figure provenance). | Med | Low–Med | `provenance.py`, `plan.py` `_run_with_timeout`, `templates/report.html` provenance panel |

**Sequencing:** do #2 (a day, big routing win) and #8's provenance string alongside #1 (the trust centerpiece). #1, #3, #4 together make reports *feel* Claude-grade (every claim cited, structure validated, output visually clean). #5 is the largest refactor — defer until the cheaper wins land, then adopt evals-first/two-Claude iteration (define representative-query scenarios with expected report properties, baseline with/without each component) so the rewrite is measured, not guessed.

---

## 5. Reading list (primary sources, deduped)

**Agent Skills (packaging, progressive disclosure, validators, degrees of freedom):**
- Anthropic Engineering — Equipping agents with Agent Skills: https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
- Claude Platform — Agent Skills overview: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- Claude Platform — Skill authoring best practices: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
- Claude Code — Extend Claude with skills: https://code.claude.com/docs/en/skills
- `anthropics/skills` (production docx/pptx/xlsx/pdf + validate.py/recalc.py): https://github.com/anthropics/skills
- Agent Skills open standard: https://agentskills.io/home

**Orchestration, citation, evaluation (deep-research systems):**
- Anthropic — How we built our multi-agent research system: https://www.anthropic.com/engineering/multi-agent-research-system
- STORM (NAACL 2024): https://arxiv.org/abs/2402.14207
- `stanford-oval/storm` (reference impl): https://github.com/stanford-oval/storm
- Co-STORM (EMNLP 2024): https://arxiv.org/abs/2408.15232
- Stanford STORM project page: https://storm-project.stanford.edu/research/storm/
- OpenAI — Introducing deep research: https://openai.com/index/introducing-deep-research/
- Google — Gemini deep research: https://blog.google/products/gemini/google-gemini-deep-research/
- Google — Gemini Deep Research API docs: https://ai.google.dev/gemini-api/docs/deep-research
- Perplexity — Introducing Deep Research: https://www.perplexity.ai/hub/blog/introducing-perplexity-deep-research
- `assafelovic/gpt-researcher`: https://github.com/assafelovic/gpt-researcher