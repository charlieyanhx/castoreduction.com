"""
agents/synthesis.py — the lead synthesis agent.

Unlike the worker agents, the lead uses NO tools. It receives the workers'
Evidence as context and reasons over it to produce an integrated research brief
— the classic multi-agent "manager summarizes the specialists" pattern.
"""
from __future__ import annotations

import json

from llm import call_text
from tools import Evidence
from .registry import agent

_SYNTH_SYSTEM = (
    "You are the lead analyst of a market-research crew. Specialist agents have "
    "each investigated one sub-domain and returned findings. Synthesize them into "
    "a tight, decision-useful brief. Be concrete, cite which agent surfaced each "
    "point, flag contradictions, and never invent numbers not present in the "
    "findings. End with a one-line confidence read."
)


def _findings_digest(worker_evidence: dict) -> str:
    """Compact the workers' payloads into a JSON digest for the lead's context."""
    digest = {}
    for name, ev in worker_evidence.items():
        if ev is None:
            continue
        digest[name] = {
            "produces": (ev.cost_meta or {}).get("produces"),
            "answer": (ev.payload or {}).get("answer"),
            "n_findings": (ev.payload or {}).get("n_findings"),
            "error": ev.error,
        }
    return json.dumps(digest, indent=2, default=str)[:6000]


@agent(role="Lead research synthesist", produces="research_brief",
       categories=[], max_steps=1)
def synthesis_agent(description: str, worker_evidence: dict, geo: str = "US") -> Evidence:
    """Integrate the worker agents' findings into one research brief (no tools)."""
    digest = _findings_digest(worker_evidence)
    brief = call_text(
        system=_SYNTH_SYSTEM,
        user=(f"VENTURE: {description}\nGEOGRAPHY: {geo}\n\n"
              f"SPECIALIST FINDINGS (JSON):\n{digest}\n\n"
              "Write the integrated brief now."),
        max_tokens=1200,
    )
    contributing = [n for n, e in worker_evidence.items() if e and e.error is None]
    return Evidence(
        source="synthesis_agent", category="agent_output",
        count=len(contributing),
        payload={"brief": brief, "contributing_agents": contributing},
        cost_meta={"n_agents": len(contributing)},
    )
