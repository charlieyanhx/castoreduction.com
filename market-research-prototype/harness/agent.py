"""
harness/agent.py — the Castor agent loop (isolated sub-agent).

This is the harness core: it turns the tool/skill registry into an agentic
substrate. Given a goal and a masked set of allowed tools, the agent loops:

    analyze  →  pick next tool (LLM)  →  execute (registry)  →  observe  →  repeat

until it declares the goal done or hits the step budget. It returns a single
consolidated Evidence envelope (the same shape every tool/skill returns), so an
agent run is invocable and composable exactly like any other capability.

Design rules (from docs/ARCHITECTURE.md — Manus + Claude Code patterns):
  * Stable tool definitions. The tool list is built once per run from the
    *masked* allowed set and never mutated mid-loop — this keeps the LLM prompt
    prefix byte-stable for KV-cache hits. Availability is gated by masking, not
    by add/remove.
  * Isolated context. Each agent run owns its own trace; nothing leaks back into
    a parent except the final summary Evidence.
  * No recursion. The agent only exposes registry *tools* (atomic, single
    external surface). It never exposes itself or another agent as a tool, so a
    sub-agent cannot spawn a sub-agent.
  * Failures stay in the trace. Errored steps are kept (not hidden) so the model
    learns from them across steps.
  * Recitation. The goal + remaining budget are re-injected each step to fight
    "lost-in-the-middle" drift on long runs.
  * Provenance. Every Evidence produced is collected and returned, so downstream
    validation (Layer 3 numbers-right) can trace every datum to its source tool.

v1 uses JSON tool-selection (the model emits {"tool","args"} or {"done"}), which
is safe and works with the existing llm.call_json. A CodeAct executor (model
writes Python over the registry, run in a sandbox) is a future upgrade tracked
in docs/STACK.md — the AgentResult / trace shape is designed to accommodate it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from logger import get
from tools import Evidence, TOOL_REGISTRY, ToolMeta

log = get("harness.agent")

# Hard ceiling so a misbehaving loop can never run forever, independent of the
# caller-supplied budget.
MAX_STEPS_CEILING = 25

# W1/H13 — observation-log compaction with an anti-thrash guard. On long runs the
# append-only log outgrows the planner prompt; when it exceeds the char budget, the
# oldest entries fold into ONE deterministic summary line (no LLM) and the newest
# OBS_KEEP_RECENT stay verbatim. MAX_COMPACTIONS is the anti-thrash guard: without
# it, a log whose *recent* entries alone exceed the budget would re-compact every
# step, progressively digesting its own summaries into nothing.
OBS_LOG_BUDGET_CHARS = 6000
OBS_KEEP_RECENT = 4
MAX_COMPACTIONS = 3


@dataclass
class Step:
    """One analyze→act→observe cycle, retained in the trace (failures included)."""
    index: int
    tool: str
    args: dict
    ok: bool
    summary: str
    error: Optional[str] = None


@dataclass
class AgentResult:
    """Outcome of an agent run. Mirrors the Evidence philosophy: data + metadata."""
    goal: str
    answer: str
    steps: list[Step] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    completed: bool = False
    stop_reason: str = ""
    compactions: int = 0
    # W5-6: full text of every observation folded out of the prompt, by ref. The
    # fold used to be terminal — this is what makes "what did step 3 return?"
    # answerable after the loop has moved on.
    compacted: dict = field(default_factory=dict)

    def to_evidence(self) -> Evidence:
        """Consolidate the run into a single Evidence envelope.

        payload carries the answer + a structured trace + every collected
        Evidence (as dicts), so a caller or a UI gets full provenance.
        """
        total = sum(e.count for e in self.evidence)
        return Evidence(
            source="agent",
            category="agent_output",
            count=total,
            payload={
                "goal": self.goal,
                "answer": self.answer,
                "completed": self.completed,
                "stop_reason": self.stop_reason,
                "steps": [
                    {"index": s.index, "tool": s.tool, "args": s.args,
                     "ok": s.ok, "summary": s.summary, "error": s.error}
                    for s in self.steps
                ],
                "evidence": [e.to_dict() for e in self.evidence],
            },
            cost_meta={
                "n_steps": len(self.steps),
                "n_evidence": len(self.evidence),
                "completed": self.completed,
                "stop_reason": self.stop_reason,
                "compactions": self.compactions,
            },
        )


# ---------------------------------------------------------------------------
# Tool surface (masking + stable description)
# ---------------------------------------------------------------------------
def select_tools(
    allowed_tools: Optional[list[str]] = None,
    allowed_categories: Optional[list[str]] = None,
) -> list[ToolMeta]:
    """Return the masked tool surface for a run.

    Masking, not mutation: we always look at the full registry and *filter*.
    With no filters, the full registry is exposed. Results are sorted so the
    prompt prefix is deterministic (KV-cache friendly).
    """
    metas = list(TOOL_REGISTRY.values())
    if allowed_tools is not None:
        allow = set(allowed_tools)
        metas = [m for m in metas if m.name in allow]
    if allowed_categories is not None:
        cats = set(allowed_categories)
        metas = [m for m in metas if m.category in cats]
    return sorted(metas, key=lambda m: (m.category, m.name))


def _tool_catalog(metas: list[ToolMeta]) -> str:
    """Stable, compact catalog string for the system prompt."""
    lines = []
    for m in metas:
        first_line = (m.docstring or "").strip().split("\n", 1)[0]
        lines.append(f"- {m.name}{m.signature} [{m.category}] → {m.returns}\n    {first_line}")
    return "\n".join(lines)


_SYSTEM_TEMPLATE = """You are a research sub-agent inside the Castor harness.

You accomplish a GOAL by calling tools one at a time. Each tool returns an
Evidence envelope (count + payload). After each result you decide the next
action. Never invent data — only use what tools return.

Available tools:
{catalog}

Respond ONLY with a JSON object, one of:
  {{"thought": "...", "tool": "<tool_name>", "args": {{...}}}}
  {{"thought": "...", "done": true, "answer": "<concise grounded answer>"}}

Rules:
- Pick exactly one tool per step, with args matching its signature.
- Stop (done=true) as soon as the goal is satisfied or further tools won't help.
- Base the final answer ONLY on evidence the tools returned.
"""


def _compact_observations(observations: list[str],
                          keep_recent: int = OBS_KEEP_RECENT,
                          store=None) -> list[str]:
    """Fold all but the newest `keep_recent` observations into ONE pointer line.

    W5-6: delegates to context.compaction. The fold is no longer terminal — pass a
    CompactionStore and every folded observation stays retrievable by ref, so the
    prompt shrinks without the evidence disappearing. Already-folded pointer lines
    pass through instead of being re-summarised.
    """
    from context.compaction import compact
    return compact(observations, keep_recent=keep_recent, store=store)


def _summarize_evidence(e: Evidence, limit: int = 240) -> str:
    """Short, deterministic observation string fed back into the loop."""
    if e.error:
        return f"ERROR: {e.error}"
    payload_str = json.dumps(e.payload, default=str)
    if len(payload_str) > limit:
        payload_str = payload_str[:limit] + "…"
    return f"count={e.count} payload={payload_str}"


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------
def run_agent(
    goal: str,
    allowed_tools: Optional[list[str]] = None,
    allowed_categories: Optional[list[str]] = None,
    max_steps: int = 8,
    context: str = "",
) -> AgentResult:
    """Run the agent loop to accomplish `goal`, return an AgentResult.

    Args:
      goal: what to accomplish (natural language).
      allowed_tools / allowed_categories: mask the tool surface. None = all.
      max_steps: per-run budget (clamped to MAX_STEPS_CEILING).
      context: optional extra grounding injected once at the start.

    The result is also obtainable as a single Evidence via .to_evidence().
    """
    budget = max(1, min(max_steps, MAX_STEPS_CEILING))
    metas = select_tools(allowed_tools, allowed_categories)
    result = AgentResult(goal=goal, answer="")

    if not metas:
        result.stop_reason = "no_tools_available"
        result.answer = "No tools available for this goal."
        log.warning("[agent] no tools matched the mask for goal: %s", goal[:80])
        return result

    # Stable system prefix (built once — KV-cache friendly).
    system = _SYSTEM_TEMPLATE.format(catalog=_tool_catalog(metas))
    valid_names = {m.name for m in metas}

    # Append-only observation log. Failures are kept here on purpose.
    observations: list[str] = []
    if context:
        observations.append(f"CONTEXT: {context}")
    from context.compaction import CompactionStore
    obs_store = CompactionStore()

    for i in range(budget):
        # H13: compact the observation log when it outgrows the prompt budget.
        # The MAX_COMPACTIONS cap is the anti-thrash guard — once spent, the loop
        # accepts a long log rather than digesting its own summaries repeatedly.
        if (sum(len(o) for o in observations) > OBS_LOG_BUDGET_CHARS
                and result.compactions < MAX_COMPACTIONS
                and len(observations) > OBS_KEEP_RECENT):
            observations = _compact_observations(observations, store=obs_store)
            result.compactions += 1
            result.compacted = obs_store.to_dict()
            log.info("[agent] observation log compacted (%d/%d)",
                     result.compactions, MAX_COMPACTIONS)

        # Recitation: re-state goal + remaining budget every step.
        remaining = budget - i
        user = (
            f"GOAL: {goal}\n"
            f"STEPS REMAINING: {remaining}\n\n"
            "OBSERVATIONS SO FAR:\n"
            + ("\n".join(observations) if observations else "(none yet)")
            + "\n\nDecide the next action as JSON."
        )

        decision = call_next(system, user)
        if decision.get("done"):
            result.completed = True
            result.answer = str(decision.get("answer", "")).strip()
            result.stop_reason = "done"
            break

        tool_name = decision.get("tool")
        args = decision.get("args") or {}
        if tool_name not in valid_names:
            obs = f"step {i}: invalid tool '{tool_name}' (not in allowed set)"
            observations.append(obs)
            result.steps.append(Step(i, str(tool_name), args, False, obs,
                                     error="invalid_tool"))
            continue
        if not isinstance(args, dict):
            obs = f"step {i}: args for '{tool_name}' must be an object"
            observations.append(obs)
            result.steps.append(Step(i, tool_name, {}, False, obs, error="bad_args"))
            continue

        # Execute via the registry. The @tool wrapper already catches internal
        # exceptions and returns error Evidence, so this won't raise.
        meta = TOOL_REGISTRY[tool_name]
        evidence = meta.fn(**args)
        if not isinstance(evidence, Evidence):  # defensive — should not happen
            evidence = Evidence(source=tool_name, category=meta.category,
                                count=0, payload=evidence)

        result.evidence.append(evidence)
        summary = _summarize_evidence(evidence)
        ok = evidence.error is None
        result.steps.append(Step(i, tool_name, args, ok, summary, error=evidence.error))
        observations.append(f"step {i}: {tool_name}({_args_str(args)}) → {summary}")

    if not result.completed and not result.stop_reason:
        result.stop_reason = "budget_exhausted"
        if not result.answer:
            result.answer = _fallback_answer(result)

    log.info("[agent] goal=%r steps=%d evidence=%d stop=%s",
             goal[:60], len(result.steps), len(result.evidence), result.stop_reason)
    return result


def call_next(system: str, user: str) -> dict:
    """One planner turn. Isolated so tests can patch it without an LLM."""
    from llm import call_json
    out = call_json(system, user, max_tokens=600)
    if not isinstance(out, dict) or out.get("_parse_error"):
        return {"done": True, "answer": "",
                "_error": (out or {}).get("_parse_error", "non-dict decision")}
    return out


def _args_str(args: dict, limit: int = 80) -> str:
    s = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return s if len(s) <= limit else s[:limit] + "…"


def _fallback_answer(result: AgentResult) -> str:
    """When the budget runs out without a 'done', synthesize a minimal summary."""
    good = [e for e in result.evidence if e.error is None and e.count > 0]
    if not good:
        return "No conclusive evidence gathered within the step budget."
    sources = ", ".join(sorted({e.source for e in good}))
    total = sum(e.count for e in good)
    return f"Gathered {total} item(s) from: {sources}. (Budget reached before completion.)"
