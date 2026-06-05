"""
harness/ — the Castor agent harness core.

Turns the tool/skill registry into an agentic substrate. The harness owns
orchestration; open-source research agents (STORM, GPT Researcher) plug in as
limbs; the numbers-right engine validates anything numeric. See docs/ARCHITECTURE.md.

Usage:
    from harness import run_agent

    # Isolated sub-agent over a masked tool surface:
    result = run_agent(
        goal="Find competitors of Acme in the project-management space",
        allowed_categories=["scrape", "ads", "customer_voice"],
        max_steps=6,
    )
    print(result.answer)
    ev = result.to_evidence()   # same Evidence envelope as any tool/skill
"""
from .agent import (
    run_agent,
    select_tools,
    AgentResult,
    Step,
    MAX_STEPS_CEILING,
)
from .refine import evaluate_refine, RefineResult

__all__ = [
    "run_agent",
    "select_tools",
    "AgentResult",
    "Step",
    "MAX_STEPS_CEILING",
    "evaluate_refine",
    "RefineResult",
]
