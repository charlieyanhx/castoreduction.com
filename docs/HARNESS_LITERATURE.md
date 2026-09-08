# Harness Literature Review — Building Harnesses, Claude for Financial Services, and the Claude Code Leak

Three research streams, **67 findings, ~60 verified** (fetched and read this run, not recalled).
Companion to [AGENT_DOC_LITERATURE.md](AGENT_DOC_LITERATURE.md) (skills/orchestration) and the
actionable plan in [CC_HARNESS_PLAN.md](CC_HARNESS_PLAN.md).

---

## 1. Executive summary — the 7 most Castor-relevant lessons

1. **"The loop belongs to the agent. The mechanisms belong to the harness."** (shareAI-lab's
   distilled principle.) Claude Code's power is a *dumb flat loop* + *rich deterministic
   mechanisms* (compaction, scheduling, permissions, persistence). Castor's split — deterministic
   `plan.py` + bounded agent limb — is the same shape and is **validated, not challenged**, by the leak.
2. **Anthropic's FS solution is Castor's product category, productized** — and their trust
   architecture is: connector-first data hierarchy (real sources FIRST, model knowledge last),
   an **`[UNSOURCED]` marker convention** identical to ours, **formulas-over-hardcodes**
   ("every derived value MUST be a formula referencing input cells"), claim→source output
   schemas, and human review staged **at artifact boundaries**, not end-of-run.
3. **Model tiering is the biggest cost lever CC validates:** >50% of CC's LLM calls go to
   Haiku-class models (summarization, parsing, classification, the whole Explore agent).
   Castor should route utility calls to Gemini's flash-lite tier.
4. **KV-cache discipline is a 10× cost lever:** byte-stable prefixes, append-only history,
   cache breakpoints, even a warm-up request. Cached input $0.30/MTok vs $3/MTok uncached.
5. **Compaction is structured, staged, and reversible:** CC compacts on headroom accounting
   (historically ~92%) into a *fixed checklist schema*; "microcompaction" demotes old tool
   results to disk references while keeping a hot tail inline; an anti-thrash circuit breaker
   halts runaway compaction. Reversibility (pointers, not deletion) is the invariant.
6. **Tool descriptions are the routing layer and outweigh the system prompt ~3:1 in token
   budget** (~9.4k vs ~2.8k tokens in CC). They deserve version control and 3× the
   prompt-engineering investment; emphasis markers (IMPORTANT/NEVER) + contrastive examples
   remain the top steering levers.
7. **Sub-agents are context quarantine, not parallelism:** one-branch-deep, restricted tool
   masks stated in *both* the mask and the prompt, single compact result returned. Evaluators
   should exercise the **live artifact**, and generator↔evaluator need a pre-agreed contract.

---

## 2. Stream A — Harness engineering (26 findings, all verified)

**Sources:** Anthropic (effective-agents, multi-agent research system, writing tools, memory
tool), Manus context-engineering, Cognition, Vercel, Claude Code compaction analyses.

**Context engineering:** the goal is "the smallest possible set of high-signal tokens."
Recitation (Manus rewrites the todo list at the *end* of context every step); errors left in
context shift the prior away from repeats; filesystem as externalized memory with **reversible**
compression (drop bulk, keep restorable pointers); avoid few-shot ruts by injecting small
structured variation; **context resets + structured handoff beat in-place compaction** for
long-running app builders.

**Tools:** mask, don't remove (dynamic tool-set changes break KV-cache); poka-yoke arguments
(SWE-bench harness required absolute paths); `response_format` enum (DETAILED 206 vs CONCISE 72
tokens measured); Vercel's quantified case: replacing 16 specialized tools with one general
capability moved success 60%→100%; descriptions are load-bearing ("wrong descriptions send
agents down completely wrong paths").

**Orchestration & verification:** workflows beat agents for well-understood tasks; sub-agent
delegation must carry objective + output format + tool guidance + boundaries; multi-agent costs
~15× a chat turn (use only where breadth pays); generator-evaluator with a pre-agreed "sprint
contract" and calibrated few-shot scoring; the evaluator exercises the **live artifact**
(Playwright clicking the app), not static output; resume-from-where-it-was beats
restart-from-scratch; "build for deletion" — every harness component encodes an assumption
about what the model can't do; stress-test by removing pieces.

**Castor mapping:** already does — recitation, errors-as-Evidence, append-only log, masking,
judge+refine. Adopt — reversible microcompaction (P4), resume (P2), poka-yoke args + response
format (P3), live-artifact QA (REPORT_SPEC wish #5).

---

## 3. Stream B — Claude for Financial Services (18 findings, all verified)

**The pitch IS verification:** "information is verified across sources to reduce errors, every
claim links directly to its source." 20+ data vendors exposed as plain MCP endpoints (FactSet,
S&P Kensho, PitchBook, Morningstar, Daloopa, Databricks, Snowflake…).

**Patterns Castor should copy (several we independently converged on):**
- **Hard data-source hierarchy** written into skills: "ALWAYS follow: 1. FIRST check MCP data
  sources… model knowledge LAST." → Castor: codify source-first in every sizing/pricing skill.
- **`[UNSOURCED]` marker:** "Cite every number. If a figure can't be sourced… mark it
  [UNSOURCED]" — identical to Castor's UNSOURCED labeling. Convergent evolution; keep ours.
- **Claim→source store as an output schema:** the sector-reader agent MUST emit
  `facts: [{claim (≤256 chars, pattern-validated), source}]` — the literature's #1 gap for
  Castor (sentence-level citations) already has a production-tested schema shape.
- **Trust isolation:** only ONE subagent may open untrusted third-party documents, and it gets
  read-only tools; cross-agent handoffs are allowlisted + schema-validated (injection surface).
- **Formulas-over-hardcodes:** every derived value must be a formula referencing input cells,
  never a pre-computed number → Castor's calculation-string + `safe_eval_formula` gate is the
  same doctrine; extend it to financials scenarios (the SOM-consistency fix).
- **Domain sanity rules codified:** "Gross margin > EBITDA margin > Net margin (always true by
  definition)" — add analogous hard identities to `validate_numbers` (funnel ordering already
  exists; add margin/ratio identities per business model).
- **Human review at artifact boundaries** ("stop after the comps spread, again after the note")
  → Castor: optional operator gates after sizing and after pricing, before final render.
- **Benchmarks as trust marketing:** Vals AI Finance Agent (537 expert questions) — a Castor
  analog ("N ventures, M verified numbers, X% gate pass") would be a sales asset, not just QA.
- The May-2026 **"agents for FS" templates include a market-researcher agent** that is nearly
  Castor's product (TAM w/ source, CAGR, segmentation, competitive landscape) — validating both
  the category and the skills+connectors+schemas architecture.

---

## 4. Stream C — Claude Code internals (23 findings, 21 verified, 11 sources)

**Master loop (`nO`):** single-threaded; `while(tool_call) → execute → append → call again`;
flat history; chosen for debuggability. *Castor: already this shape; keep flat.*

**Sub-agents:** one-branch-deep (children cannot spawn); exist for **context quarantine** —
"dirty context disappears when the sub-agent completes"; restricted tool lists enforced by mask
AND prompt ("STRICTLY PROHIBITED from creating files…"); return one compact result. *Castor:
enforce depth-1 in the registry; state masks in prompts too.*

**TodoWrite recitation:** the tool does NOT support partial updates — full-list rewrite is the
mechanism (pushes the plan into recency), plus conditional system-message re-injection.
*Castor: recite the whole remaining plan each cycle, not deltas.*

**system-reminders:** out-of-band steering embedded in user messages/tool results (not the
system prompt), fired conditionally on session state ("DO NOT mention this to the user").
*Castor: generalize `model_directive` into a triggered reminder channel (budget 50/80%,
gate pre-warnings, plan drift).*

**Compaction:** historically ~92% trigger (wU2), now headroom accounting; fixed checklist
schema (intent/decisions/files/errors/pending/next); post-compaction re-reads hot files from
disk; **microcompaction** demotes old tool results to disk references keeping a hot tail;
anti-thrash circuit breaker (unverified but principled). *Castor: token watermark on the agent
limb; compact via fixed schema; re-materialize hot state from provenance, not the summary.*

**Memory:** CLAUDE.md is a 4-layer concatenated hierarchy (managed→user→project→local,
specific-last for recency), delivered as a **user message after the system prompt**, re-injected
after compaction; soft guidance explicitly separated from hard enforcement (hooks/permissions).
*Castor: CASTOR.md layering (methodology→vertical→run brief); hard rules stay in the validation
gate, never prompts.*

**Cost/caching:** >50% of calls on Haiku-class (summarize/parse/classify/Explore); cache
breakpoints on system/tools/prefix; a 1-token warm-up request pre-fills the cache; snapshots
deliberately static. *Castor: add a `tier` param to llm.py; Gemini explicit context caching.*

**Tool layer:** ~9.4k tokens of tool descriptions vs ~2.8k system prompt, maintained like source
(231 versions tracked); altitude levels (low Bash/Read → high Task/WebFetch); Edit requires
Read-first + unique exact-string match (anti-hallucination grounding); dual cheap-LLM injection
checks before every Bash (Castor: rule-based check suffices — tools are parameterized, not
model-composed shell). **"LLM search >>> RAG"**: agentic grep over vector DBs — *Castor already
tool-retrieval-first; never add a vector store for source docs.*

**Skips validated:** h2A async steering queue (Castor is batch; replay-from-cache is the right
substitute); full multi-agent everywhere (15× economics).

---

## 5. Merged adoption table (impact × effort, all three streams)

| P | Adoption | Stream | Castor target | Effort |
|---|---|---|---|---|
| 1 | Claim→source facts schema on research outputs (+ CitationAgent pass) | FS | new `skills/citation.py`, sector-reader-style schemas on agents | med |
| 2 | Resume-from-ledger (never re-run finished steps) | A | `jobs.py`/`plan.py` (`resume(job_id)`) | med |
| 3 | Model tiering — flash-lite for summarize/classify/extract | C | `llm.py` `tier=` param | low |
| 4 | Descriptions-as-routing + negative scope + IMPORTANT/NEVER + contrast pairs | A+C | all registries, prompt templates | low |
| 5 | Read-parallel/write-serial tool scheduler (≤10) + poka-yoke args + response_format | A+C | `tools/registry.py`, plan.py fan-outs | med |
| 6 | Microcompaction: old observations → Evidence-ID pointers, hot tail inline; fixed-schema compaction + anti-thrash | C | `harness/agent.py` | med |
| 7 | CASTOR.md layered memory (methodology→vertical→brief, specific-last), delivered post-system, re-injected | C | `llm.py` + intake | med |
| 8 | Triggered reminder channel (budget/gate/drift) | C | `plan.py`, `harness/agent.py` | low |
| 9 | Domain identity rules per model (margin ordering etc.) in the gate | FS | `skills/sizing/validate.py` | low |
| 10 | Operator review gates at artifact boundaries (sizing, pricing) | FS | workspace flow | med |
| 11 | Depth-1 spawn enforcement + masks stated in prompts | C | `agents/registry.py` | low |
| 12 | KV-stable prefixes + Gemini context caching (+ warm-up) | A+C | `llm.py` | low |
| 13 | Castor benchmark as trust asset (Vals-style) | FS | `benchmarks/` | med |

## 6. Reading list (verification status)

**Verified this run (fetched & read):** minusx.ai/blog/decoding-claude-code · kirshatrov.com/posts/claude-code-internals ·
github.com/Piebald-AI/claude-code-system-prompts · github.com/shareAI-lab/analysis_claude_code ·
github.com/Yuyz0112/claude-code-reverse · blog.promptlayer.com (master agent loop) ·
decodeclaude.com/compaction-deep-dive · medium.com/@outsightai (peeking under the hood) ·
medium.com/@georgesung (LLM traffic tracing) · code.claude.com/docs/en/memory ·
anthropic.com/engineering (building-effective-agents · multi-agent-research-system ·
harness-design-long-running-apps · writing-tools-for-agents) · manus.im context-engineering ·
cognition.ai (don't build multi-agents) · vercel.com (tool minimization) ·
anthropic.com/news/claude-for-financial-services (+ agents-for-FS May 2026, Claude for Excel) ·
github.com/anthropics FS agent templates (market-researcher, sector-reader, orchestrate.py) ·
Vals AI Finance Agent benchmark.

**Unverified (recall or paywalled):** ghuntley.com/tradecraft (paywalled) · claudefa.st
anti-thrash circuit breaker (plausible, unconfirmed) · gW5=10 concurrency constant (repo
restructured since).
