"""
agents/registry.py — registry for specialized research agents (cycle33).

An AGENT is a goal-driven actor that runs the Castor harness over a CURATED tool
surface to accomplish one research sub-domain (competitors, demand, pricing,
local market), then returns an Evidence envelope. Agents differ from skills:

  tool   — atomic external surface (one call)
  skill  — deterministic composition of tools (fixed control flow)
  agent  — autonomous loop: the model chooses which tools to call, in what order,
           until the goal is met or the step budget runs out (dynamic control flow)

Agents are the SOTA layer: isolated context, a role/persona, a masked tool surface,
and a step budget — composable by a crew orchestrator. Like tools and skills they
are auto-discoverable (AGENT_REGISTRY) and return the same Evidence shape.

Pattern:
    @agent(role="Competitive analyst", produces="competitor_landscape",
           categories=["scrape", "ads", "customer_voice"], max_steps=6)
    def competitor_agent(description: str, geo: str = "US", context: str = "") -> Evidence:
        ...
"""
from __future__ import annotations

import functools
import inspect
import time
import traceback
from dataclasses import dataclass
from typing import Callable, Optional

from core import Registry
from logger import get
from core import Evidence

log = get("agents")


@dataclass
class AgentSpec:
    """What the registry knows about one agent.

    `categories` is the agent's tool SURFACE and `max_steps` its budget in the harness
    loop. Both are ceilings: they bound what a sub-agent can reach and how long it can
    spend before the parent takes back control.
    """
    name: str
    role: str                 # human-readable persona ("Competitive analyst")
    produces: str             # output category ("competitor_landscape")
    categories: list[str]     # tool categories this agent may use (its surface)
    max_steps: int            # step budget for the harness loop
    fn: Callable
    signature: str
    docstring: str


AGENT_REGISTRY: Registry[AgentSpec] = Registry("agent")


def agent(role: str, produces: str, categories: Optional[list[str]] = None,
          max_steps: int = 6):
    """Register a function as a research agent.

    Mirrors @tool/@skill: registers metadata, times the call, isolates errors,
    and normalizes the return into Evidence. The agent body is responsible for
    calling the harness (run_agent) over its curated surface.
    """
    cats = list(categories or [])

    def decorator(fn: Callable) -> Callable:
        """Register the agent, then replace its entry's fn with the Evidence-returning wrapper."""
        name = fn.__name__
        AGENT_REGISTRY[name] = AgentSpec(
            name=name, role=role, produces=produces, categories=cats,
            max_steps=max_steps, fn=None,  # set to wrapper below
            signature=str(inspect.signature(fn)),
            docstring=inspect.getdoc(fn) or "",
        )

        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> Evidence:
            """Run the agent and always return Evidence, converting a raise into error Evidence.

            A sub-agent that throws must not take its parent down: the crew is built to
            synthesise from the workers that succeeded.
            """
            t0 = time.time()
            try:
                result = fn(*args, **kwargs)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                log.warning("[agent/%s] failed: %s", name, err)
                log.debug(traceback.format_exc())
                return Evidence(source=name, category="agent_output", count=0,
                                payload=None, fetched_at=t0,
                                duration_s=round(time.time() - t0, 3), error=err)
            duration = round(time.time() - t0, 3)
            if isinstance(result, Evidence):
                if result.fetched_at == 0.0:
                    result.fetched_at = t0
                if result.duration_s == 0.0:
                    result.duration_s = duration
                if not result.source:
                    result.source = name
                if not result.category:
                    result.category = "agent_output"
                result.cost_meta.setdefault("produces", produces)
                result.cost_meta.setdefault("role", role)
                return result
            return Evidence(source=name, category="agent_output",
                            count=1 if result is not None else 0,
                            payload=result, fetched_at=t0, duration_s=duration,
                            cost_meta={"produces": produces, "role": role})

        AGENT_REGISTRY[name].fn = wrapper
        wrapper.__agent_meta__ = AGENT_REGISTRY[name]
        wrapper.__wrapped_fn__ = fn
        return wrapper

    return decorator


def list_agents(produces: Optional[str] = None) -> list[AgentSpec]:
    """Registered agents, optionally only those producing one output category."""
    match = {"produces": produces} if produces is not None else {}
    return AGENT_REGISTRY.entries(**match)


def get_agent(name: str) -> Optional[AgentSpec]:
    return AGENT_REGISTRY.get(name)


def describe_agent(name: str) -> dict:
    """One agent as a JSON-able dict. An unknown name returns {"error": ...} rather than
    raising, because the callers are description surfaces where a miss is data."""
    return AGENT_REGISTRY.describe(name, (
        "name", "role", "produces", "categories", "max_steps", "signature", "docstring"))


def describe_all_agents() -> dict:
    return {name: describe_agent(name) for name in sorted(AGENT_REGISTRY)}
