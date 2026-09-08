"""capabilities/effort.py — how much work one report is worth (Wave 6, item 3).

Every run currently does the same amount of work regardless of what it is for. The
operator knows something the pipeline doesn't: a quick read on an idea and a report
going to an investment committee do not deserve the same depth, and for this buyer
the deep case is explicitly worth the tokens.

One dial, carried from intake through the API into run_plan. It scales the levers
that genuinely change depth — how wide discovery searches, how many steps an agent
limb gets, whether the LLM verification pass runs — and nothing else. A knob that
touched a dozen unrelated constants would make two runs incomparable.

The safety rule: an unknown level resolves to STANDARD. Effort arrives as a string
from a form field, so a typo is live, and one that silently downgraded a deep run to
quick would be invisible everywhere except in the thinner report the operator paid
extra for.
"""
from __future__ import annotations

QUICK = "quick"
STANDARD = "standard"
DEEP = "deep"

_LEVELS = (QUICK, STANDARD, DEEP)

# Levers only. Each must be monotonic across levels — a "deeper" setting that reduces
# any lever is a misconfiguration, and a test pins that.
_CONFIG: dict[str, dict] = {
    QUICK: {
        "max_candidates": 10,
        "agent_max_steps": 4,
        "competitor_enrich_limit": 5,
        "verify_with_llm": False,
        "research_crew": False,
    },
    STANDARD: {
        "max_candidates": 20,
        "agent_max_steps": 6,
        "competitor_enrich_limit": 10,
        "verify_with_llm": False,
        "research_crew": False,
    },
    DEEP: {
        "max_candidates": 35,
        "agent_max_steps": 12,
        "competitor_enrich_limit": 20,
        # The verification loop the buyer asked for: worth the tokens only when the
        # report is going somewhere that justifies them.
        "verify_with_llm": True,
        # Four specialist agents, each with its own harness loop. Same reasoning.
        "research_crew": True,
    },
}


def resolve_effort(effort: str | None) -> str:
    """Normalise an effort level. Unknown/empty/None -> STANDARD, never QUICK."""
    e = (effort or "").strip().lower()
    return e if e in _LEVELS else STANDARD


def effort_config(effort: str | None) -> dict:
    """The levers for this effort level."""
    return dict(_CONFIG[resolve_effort(effort)])
