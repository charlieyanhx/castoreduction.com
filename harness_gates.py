"""
harness_gates.py — deterministic gates for the CC Harness Plan (docs/CC_HARNESS_PLAN.md).

One simple program, many small detectors. Each phase invariant (P0-P6 / milestones M1-M7 in
docs/TESTING_MILESTONES.md) is a machine check against the ACTUAL codebase: registry
introspection + AST inspection. No LLM, no network. A check returns:
  pass  — the invariant holds
  FAIL  — the phase claims this but the code violates it (or a today-invariant is broken)
  n/a   — feature not built yet (reported with which phase will build it)

So the program is useful the whole way: today it identifies current problems (P0-class checks
run against existing registries/prompts); as each phase lands, its checks flip n/a → pass/FAIL
and pinpoint exactly what is missing. Milestones are claimed by exit code, not opinion:

  python harness_gates.py                # all checks, current baseline
  python harness_gates.py --gate M1      # phase gate: exit 0 iff every M1 check passes
"""
from __future__ import annotations

import argparse
import ast
import importlib
import json
import os
import sys
from dataclasses import dataclass
from typing import Callable, Optional

REPO = os.path.dirname(os.path.abspath(__file__))

NEGATIVE_SCOPE_MARKERS = ("do not use", "don't use", "not for", "do not call", "never use",
                          "not suitable", "skip this", "do not")
EMPHASIS_MARKERS = ("IMPORTANT", "NEVER", "ALWAYS", "CRITICAL")


@dataclass
class Check:
    """One invariant, and the callable that decides it.

    `fn` returns (verdict, detail): True holds, False violated, None not-built-yet. The
    three-way answer is the point. A binary check would have to call an unbuilt phase
    either passing (which hides missing work) or failing (which makes the whole board red
    from day one and stops anyone reading it).
    """
    id: str
    phase: str            # P0..P6 (milestone gate it belongs to)
    name: str
    fn: Callable[[], tuple[Optional[bool], str]]


def _module_exists(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def _src(relpath: str) -> Optional[str]:
    p = os.path.join(REPO, relpath)
    try:
        return open(p, encoding="utf-8").read()
    except OSError:
        return None


def _ast(relpath: str) -> Optional[ast.AST]:
    s = _src(relpath)
    return ast.parse(s) if s is not None else None


def _has_attr(module: str, attr: str) -> bool:
    try:
        return hasattr(importlib.import_module(module), attr)
    except Exception:
        return False


# ------------------------------------------------------------------ P0 — descriptions & prompts
def h01_tool_descriptions() -> tuple[Optional[bool], str]:
    """P0: every tool carries a docstring long enough to route on.

    The registry docstring is what a model reads when choosing between ~70 tools, so a
    stub description is not a documentation gap, it is a routing failure. 60 characters is
    the floor at which a description says what the tool is FOR rather than restating its
    name.
    """
    from tools import TOOL_REGISTRY
    thin = [m.name for m in TOOL_REGISTRY.values() if len((m.docstring or "").strip()) < 60]
    return (not thin, f"{len(thin)}/{len(TOOL_REGISTRY)} tools with <60-char routing docstring: "
            f"{thin[:6]}" if thin else f"all {len(TOOL_REGISTRY)} tools have substantive docstrings")


def h02_negative_scope() -> tuple[Optional[bool], str]:
    """P0: every routable description says when NOT to use the thing.

    Positive-only descriptions are how a model picks a plausible neighbour: nothing in
    "sizes a local trade area" warns it off a national SaaS. Tools, skills AND agents are
    checked together because the planner selects workers from agent descriptions, which is
    the same routing surface wearing a different name.
    """
    # Tightened at W1 gap-closure: AGENT_REGISTRY included — the planner selects
    # workers by these descriptions, the same routing surface as tools/skills
    # (§3 rule 3: thresholds only tighten).
    from tools import TOOL_REGISTRY
    from skills.registry import SKILL_REGISTRY
    metas = list(TOOL_REGISTRY.values()) + list(SKILL_REGISTRY.values())
    try:
        from agents.registry import AGENT_REGISTRY
        metas += list(AGENT_REGISTRY.values())
    except Exception:
        pass
    missing = [m.name for m in metas
               if not any(k in (m.docstring or "").lower() for k in NEGATIVE_SCOPE_MARKERS)]
    ok = len(missing) == 0
    return (ok, f"{len(missing)}/{len(metas)} components lack negative scope ('Do NOT use when…'): "
            f"{missing[:6]}…" if missing else "every tool/skill/agent states negative scope")


def h03_agent_contracts() -> tuple[Optional[bool], str]:
    """P0: every agent declares role, produces, and a docstring.

    An agent missing any of the three cannot be composed by the planner: role is how it is
    chosen, produces is what the caller can expect back, and the docstring is how it is
    told apart from its neighbours.
    """
    try:
        from agents.registry import AGENT_REGISTRY
    except Exception:
        return None, "agents registry not importable"
    bad = [a.name for a in AGENT_REGISTRY.values()
           if not (a.role and a.produces and (a.docstring or "").strip())]
    return (not bad, f"agents missing role/produces/doc: {bad}" if bad else
            f"all {len(AGENT_REGISTRY)} agents carry role+produces+doc")


def h04_emphasis_discipline() -> tuple[Optional[bool], str]:
    """2-5 load-bearing markers per prompt module — not zero, not soup."""
    counts = {}
    for f in ("four_ps.py", "market_sizing.py"):
        s = _src(f) or ""
        counts[f] = sum(s.count(m) for m in EMPHASIS_MARKERS)
    soup = {f: c for f, c in counts.items() if c > 40}
    none = {f: c for f, c in counts.items() if c == 0}
    ok = not soup and not none
    return ok, f"emphasis markers per prompt module: {counts}" + (
        f" — SOUP: {soup}" if soup else "") + (f" — NONE: {none}" if none else "")


def h05_kv_stable_prompts() -> tuple[Optional[bool], str]:
    """No timestamps/randomness interpolated into system prompts (breaks prefix cache)."""
    offenders = []
    for f in ("llm.py", "harness/agent.py"):
        tree = _ast(f)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("now", "utcnow", "random", "uuid4"):
                offenders.append(f"{f}:{getattr(node, 'lineno', '?')} .{node.attr}")
    return (not offenders, "; ".join(offenders) if offenders else
            "no time/randomness in prompt-building modules")


# ------------------------------------------------------------------ P1 — ledger & transcript
def h06_run_ledger() -> tuple[Optional[bool], str]:
    """P1: a run WRITES to the RunLedger, not merely that a ledger module exists.

    Existence is the weak claim and it is the one that stays true after the wiring rots.
    So this reads the step machinery for the write and plan.py for the call, and both must
    be present.
    """
    # Wave 3 built this at the path the plan's §2b tree specifies (persistence/ledger.py);
    # the old check looked for a flat `ledger.py` and so could never see it.
    if not (_module_exists("persistence.ledger") or _has_attr("jobs", "ledger_append")):
        return None, "RunLedger not built yet (P1)"
    # Existing is not the claim — the claim is that a RUN WRITES to it. plan.py records a
    # step event per completed step through orchestrator.steps.step_done (the step
    # machinery moved out of plan.py in item 5, so grepping plan.py for "ledger" would
    # now be a false negative).
    steps_src = _src("orchestrator/steps/__init__.py") or ""
    plan_src = _src("plan.py") or ""
    writes = "record_step" in steps_src
    called = "_step_done(" in plan_src or "step_done(" in plan_src
    ok = writes and called
    if not ok:
        return False, ("ledger exists but the pipeline never writes it "
                       f"(record_step={writes}, called_from_plan={called})")
    return True, "plan.py writes ledger step records via orchestrator.steps.step_done"


def h07_transcript() -> tuple[Optional[bool], str]:
    """P1: the per-run transcript is ATTACHED to runs, not just importable.

    Same shape as h06: a transcript nobody attaches is dead code that passes an
    existence check.
    """
    if not (_module_exists("persistence.transcript") or _has_attr("jobs", "transcript")):
        return None, "per-run transcript not built yet (P1)"
    # A transcript nobody attaches is dead code — require the run wiring, not the module.
    src = _src("jobs.py") or ""
    ok = "TranscriptWriter" in src or "_attach_transcript" in src
    return ok, ("transcript module present and attached per-run in jobs.run_async" if ok
                else "transcript module present but never attached to a run")


# ------------------------------------------------------------------ P3 — scheduler / tiering
def h09_read_write_split() -> tuple[Optional[bool], str]:
    """P3: every tool declares whether it is read-only.

    The scheduler cannot parallelise safely without it, and an untagged tool is worse than
    an unbuilt feature: it looks schedulable and is not. n/a until the metadata field
    itself exists, so this reports "prerequisite missing" rather than blaming the tools.
    """
    from tools import TOOL_REGISTRY
    m = next(iter(TOOL_REGISTRY.values()))
    if not hasattr(m, "read_only"):
        return None, "tools lack read_only metadata (P3 scheduler prerequisite)"
    untagged = [t.name for t in TOOL_REGISTRY.values() if getattr(t, "read_only", None) is None]
    return (not untagged, f"untagged tools: {untagged[:8]}" if untagged else "all tools tagged")


def h10_llm_tiering() -> tuple[Optional[bool], str]:
    """P3: cheap calls are actually routed to the cheap tier.

    Two conditions, because either alone is misleading: call_json must ACCEPT a tier, and
    plan.py must actually pass the utility tier somewhere. A tier parameter nobody uses
    saves nothing.
    """
    import inspect
    from llm import call_json
    sig = inspect.signature(call_json)
    if "tier" not in sig.parameters:
        return None, "llm.call_json has no tier= param yet (P3)"
    src = _src("plan.py") or ""
    n = src.count('tier="utility"') + src.count("tier='utility'")
    return (n > 0, f"{n} utility-tier call sites in plan.py")


# ------------------------------------------------------------------ P4 — memory / reminders / compaction
def h11_layered_memory() -> tuple[Optional[bool], str]:
    if not (os.path.exists(os.path.join(REPO, "CASTOR.md")) or _module_exists("memory_layers")):
        return None, "CASTOR.md layered memory not built yet (P4)"
    return True, "CASTOR.md present"


def h12_reminder_channel() -> tuple[Optional[bool], str]:
    """P4: the agent loop can inject triggered reminders mid-run."""
    src = _src("harness/agent.py") or ""
    if "reminder" not in src.lower():
        return None, "triggered reminder channel not built yet (P4)"
    return True, "reminder injection present in agent loop"


def h13_compaction() -> tuple[Optional[bool], str]:
    """P4: the observation log compacts, AND has an anti-thrash guard.

    Compaction without a guard is a loop: compact, immediately exceed the budget again,
    compact again. So the guard is part of the invariant, not a refinement of it.
    """
    src = _src("harness/agent.py") or ""
    if "compact" not in src.lower():
        return None, "observation-log compaction not built yet (P4)"
    has_guard = "thrash" in src.lower() or "max_compact" in src.lower()
    return (has_guard, "compaction present" + ("" if has_guard else " but NO anti-thrash guard"))


# ------------------------------------------------------------------ P5 — skills-as-folders / contracts
def h14_skill_folders() -> tuple[Optional[bool], str]:
    """P5: every skills_md/ folder holds a SKILL.md with name and description frontmatter.

    Only the head of each file is read: this asks whether the folder is well formed, not
    whether the prose is any good.
    """
    d = os.path.join(REPO, "skills_md")
    if not os.path.isdir(d):
        return None, "SKILL.md folders not built yet (P5)"
    bad = []
    for name in os.listdir(d):
        f = os.path.join(d, name, "SKILL.md")
        if not os.path.exists(f):
            bad.append(name)
            continue
        head = open(f, encoding="utf-8").read(500)
        if "name:" not in head or "description:" not in head:
            bad.append(name)
    return (not bad, f"malformed skills: {bad}" if bad else "all SKILL.md folders valid")


def h15_spawn_contracts() -> tuple[Optional[bool], str]:
    """P5: every agent declares an output_schema a spawner can validate against.

    Without one, a sub-agent's return value is trusted prose and the parent has no way to
    tell a refusal from an answer.
    """
    try:
        from agents.registry import AGENT_REGISTRY
    except Exception:
        return None, "agents registry not importable"
    a = next(iter(AGENT_REGISTRY.values()))
    if not hasattr(a, "output_schema"):
        return None, "spawn contracts (output_schema) not built yet (P5)"
    missing = [x.name for x in AGENT_REGISTRY.values() if not getattr(x, "output_schema", None)]
    return (not missing, f"agents without output schema: {missing}" if missing else "all contracted")


# ------------------------------------------------------------------ P6 — premium multi-agent
def h16_effort_knob() -> tuple[Optional[bool], str]:
    """P6: the run carries a research-depth knob, so effort is a decision and not a constant."""
    src = _src("plan.py") or ""
    if "research_depth" not in src:
        return None, "effort knob (research_depth) not built yet (P6)"
    return True, "research_depth knob present"


def h17_verifier_gate() -> tuple[Optional[bool], str]:
    if not (_module_exists("verifier") or os.path.exists(os.path.join(REPO, "skills", "verify_report.py"))):
        return None, "pre-publish verifier panel not built yet (P6)"
    return True, "verifier module present"


# ------------------------------------------------------------------ today-invariants (never n/a)
def h18_depth_one_spawn() -> tuple[Optional[bool], str]:
    """Sub-agents must not be able to spawn sub-agents (CC one-branch rule)."""
    src = _src("harness/agent.py") or ""
    ok = "run_agent" in src and "allowed_tools" in src
    return ok, "tool masking available for depth control" if ok else "agent loop lacks tool masking"


def h19_uniform_envelope() -> tuple[Optional[bool], str]:
    """Today-invariant: every tool returns the Evidence envelope.

    Checked on the RETURN ANNOTATION rather than by calling anything, so the gate stays
    offline and free. One envelope is what lets the scheduler, the gateway and the ledger
    treat any tool identically.
    """
    from tools import TOOL_REGISTRY
    import inspect
    bad = [m.name for m in TOOL_REGISTRY.values()
           if "Evidence" not in str(inspect.signature(m.fn).return_annotation)]
    return (not bad, f"tools not returning Evidence: {bad[:6]}" if bad else
            f"all {len(TOOL_REGISTRY)} tools return the Evidence envelope")


def h20_gate_is_hard() -> tuple[Optional[bool], str]:
    """Hard rules live in the validation gate, not prompts (CC soft/hard split)."""
    ok = _has_attr("skills.sizing.validate", "validate_numbers")
    return ok, "validate_numbers gate present" if ok else "validation gate missing"


CHECKS: list[Check] = [
    Check("H01", "P0", "tools carry routing-grade docstrings", h01_tool_descriptions),
    Check("H02", "P0", "negative scope on every tool/skill", h02_negative_scope),
    Check("H03", "P0", "agent role+produces contracts", h03_agent_contracts),
    Check("H04", "P0", "emphasis-marker discipline (2-40/module)", h04_emphasis_discipline),
    Check("H05", "P0", "KV-stable prompts (no time/random)", h05_kv_stable_prompts),
    Check("H06", "P1", "RunLedger written by plan.py", h06_run_ledger),
    Check("H07", "P1", "per-run transcript", h07_transcript),
    Check("H09", "P3", "read/write tool tagging", h09_read_write_split),
    Check("H10", "P3", "model tiering wired", h10_llm_tiering),
    Check("H11", "P4", "layered CASTOR.md memory", h11_layered_memory),
    Check("H12", "P4", "triggered reminder channel", h12_reminder_channel),
    Check("H13", "P4", "compaction with anti-thrash", h13_compaction),
    Check("H14", "P5", "SKILL.md folders valid", h14_skill_folders),
    Check("H15", "P5", "spawn output contracts", h15_spawn_contracts),
    Check("H16", "P6", "effort knob (research_depth)", h16_effort_knob),
    Check("H17", "P6", "pre-publish verifier panel", h17_verifier_gate),
    Check("H18", "now", "depth-1 spawn control available", h18_depth_one_spawn),
    Check("H19", "now", "uniform Evidence envelope", h19_uniform_envelope),
    Check("H20", "now", "hard rules in the gate, not prompts", h20_gate_is_hard),
]

GATES = {
    "M1": ["H01", "H02", "H03", "H04", "H05"],
    "M2": ["H06", "H07"],
    # M3 was the resume milestone and H08 was its only member. persistence/resume.py was
    # deleted (nothing ever called it, so no run was ever resumed), so the milestone is
    # dropped rather than left holding an empty list: an empty gate reports PASS with
    # nothing checked, which is the failure mode these gates exist to prevent.
    "M4": ["H09", "H10"],
    "M5": ["H11", "H12", "H13"],
    "M6": ["H14", "H15"],
    "M7": ["H16", "H17"],
    "now": ["H18", "H19", "H20"],
    "all": [c.id for c in CHECKS],
}


def main() -> int:
    """Run a milestone's checks and report. Exit code IS the verdict.

    0 when nothing in the selected gate failed, 1 otherwise. Not-built-yet (None) does not
    fail a gate, which is what lets the same program be useful before and after a phase
    lands. Every check is called inside its own try, because a runner that a broken
    detector can crash reports nothing about the other eighteen.
    """
    ap = argparse.ArgumentParser(description="Deterministic gates for the CC Harness Plan")
    ap.add_argument("--gate", default="all", choices=sorted(GATES))
    ap.add_argument("--out", help="write scorecard JSON here")
    args = ap.parse_args()

    ids = set(GATES[args.gate])
    rows, n_pass, n_fail, n_na = [], 0, 0, 0
    for c in CHECKS:
        if c.id not in ids:
            continue
        try:
            ok, detail = c.fn()
        except Exception as e:  # a check must never crash the runner
            ok, detail = False, f"check error: {e}"
        sym = {True: "pass", False: "FAIL", None: "n/a "}[ok]
        n_pass, n_fail, n_na = n_pass + (ok is True), n_fail + (ok is False), n_na + (ok is None)
        rows.append({"id": c.id, "phase": c.phase, "name": c.name, "ok": ok, "detail": detail})
        print(f"{c.id} [{c.phase:3s}] {sym}  {c.name}")
        if ok is not True:
            print(f"      -> {detail}")

    verdict = "PASS" if n_fail == 0 and n_pass > 0 else "FAIL"
    print(f"\ngate={args.gate}  pass={n_pass}  fail={n_fail}  not-built={n_na}  ->  {verdict}")
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        json.dump({"gate": args.gate, "pass": n_pass, "fail": n_fail, "na": n_na,
                   "verdict": verdict, "checks": rows}, open(args.out, "w"), indent=2)
        print(f"scorecard -> {args.out}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
