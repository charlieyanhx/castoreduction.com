"""
agents/ — specialized, auto-discoverable research agents (cycle33).

Agents run the Castor harness autonomously over a curated tool surface. A crew
orchestrator runs workers in parallel and a lead agent synthesizes the result.

Usage:
    from agents import AGENT_REGISTRY, get_agent, run_research_crew

    brief = run_research_crew("A SaaS for restaurant inventory.", geo="US")
    print(brief.payload["brief"])

    # Or one specialist:
    e = get_agent("demand_signal_agent").fn("A SaaS for restaurant inventory.")
"""
from .registry import (
    AgentSpec,
    AGENT_REGISTRY,
    agent,
    list_agents,
    get_agent,
    describe_agent,
    describe_all_agents,
)

# Trigger registration of all agent modules.
from . import research_agents  # noqa: F401  — worker agents
from . import synthesis        # noqa: F401  — lead synthesis agent
from . import planner          # noqa: F401  — dynamic crew composition
from . import crew             # noqa: F401  — crew orchestrator

from .crew import run_research_crew  # noqa: F401  — convenience export

__all__ = [
    "AgentSpec", "AGENT_REGISTRY", "agent",
    "list_agents", "get_agent", "describe_agent", "describe_all_agents",
    "run_research_crew",
]
