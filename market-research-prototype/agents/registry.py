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

from logger import get
from tools import Evidence

log = get("agents")


@dataclass
class AgentSpec:
    name: str
    role: str                 # human-readable persona ("Competitive analyst")
    produces: str             # output category ("competitor_landscape")
    categories: list[str]     # tool categories this agent may use (its surface)
    max_steps: int            # step budget for the harness loop
    fn: Callable
    signature: str
    docstring: str


AGENT_REGISTRY: dict[str, AgentSpec] = {}


def agent(role: str, produces: str, categories: Optional[list[str]] = None,
          max_steps: int = 6):
    """Register a function as a research agent.

    Mirrors @tool/@skill: registers metadata, times the call, isolates errors,
    and normalizes the return into Evidence. The agent body is responsible for
    calling the harness (run_agent) over its curated surface.
    """
    cats = list(categories or [])

    def decorator(fn: Callable) -> Callable:
        name = fn.__name__
        AGENT_REGISTRY[name] = AgentSpec(
            name=name, role=role, produces=produces, categories=cats,
            max_steps=max_steps, fn=None,  # set to wrapper below
            signature=str(inspect.signature(fn)),
            docstring=inspect.getdoc(fn) or "",
        )

        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> Evidence:
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
    items = list(AGENT_REGISTRY.values())
    if produces is not None:
        items = [a for a in items if a.produces == produces]
    return sorted(items, key=lambda a: a.name)


def get_agent(name: str) -> Optional[AgentSpec]:
    return AGENT_REGISTRY.get(name)


def describe_agent(name: str) -> dict:
    a = AGENT_REGISTRY.get(name)
    if not a:
        return {"error": f"agent '{name}' not registered"}
    return {"name": a.name, "role": a.role, "produces": a.produces,
            "categories": a.categories, "max_steps": a.max_steps,
            "signature": a.signature, "docstring": a.docstring}


def describe_all_agents() -> dict:
    return {name: describe_agent(name) for name in sorted(AGENT_REGISTRY)}
