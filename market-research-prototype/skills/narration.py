"""
skills/narration.py — produce prose from numeric/structured Evidence.

Phase 3 of the cycle32 architecture migration: separate narration from data.

Why this matters:
  Today, prose is entangled with data extraction inside 4Ps prompts. A single
  LLM call asks for "scored features + recommended channels + narrative."
  Result: prose is judged on data we can't separately verify, and we can't
  swap narration styles without re-running the data step.

What this fixes:
  - Data skills (Phase 2) produce structured payloads with NO prose.
  - This narration module reads payloads and renders prose deterministically
    OR via LLM, with mode selectable per-section.
  - Three modes:
      'template' — deterministic Jinja-style template, zero LLM cost,
                   used for compliance/audit-grade output
      'llm'      — LLM call to render prose from structured numbers,
                   judged by the existing prose_judge harness
      'hybrid'   — template provides skeleton, LLM polishes specific anchors

  Adding a new narration style = 1 function. The data skills don't change.

Public API:
    narrate_section(section_name, payload, mode='llm') -> Evidence
    narrate_report(payloads_dict, mode='llm') -> {section: Evidence}

Both return Evidence with payload = {"narrative": "...", "key_takeaways": [...]}.
"""
from __future__ import annotations

from typing import Any, Optional

from .registry import skill
from tools import Evidence


# ---------------------------------------------------------------------------
# Deterministic templates — for compliance / audit / fully-reproducible runs
# ---------------------------------------------------------------------------
# Each template takes a payload dict and returns a tuple (narrative, takeaways).
# These produce real numbers from real data — no buzzwords, no LLM judgment.

def _template_market_sizing(payload: dict) -> tuple[str, list[str]]:
    """Templater: market sizing → 1 paragraph from real numbers."""
    tam = payload.get("tam") or {}
    sam = payload.get("sam") or {}
    som = payload.get("som") or {}
    cagr = payload.get("growth_cagr_pct")
    n_seg = len(payload.get("segmentation") or [])

    def _fmt_usd(n):
        if not isinstance(n, (int, float)) or n <= 0:
            return "n/a"
        if n >= 1e9:
            return f"${n/1e9:.1f}B"
        if n >= 1e6:
            return f"${n/1e6:.0f}M"
        return f"${n/1e3:.0f}K"

    tam_mid = _fmt_usd(tam.get("mid"))
    sam_mid = _fmt_usd(sam.get("mid"))
    som_mid = _fmt_usd(som.get("mid"))

    methods_filled = sum(
        1 for k in ("method_top_down", "method_bottom_up", "method_analog")
        if (tam.get(k) or {}).get("value_usd")
    )

    cagr_str = f"{cagr}% CAGR" if isinstance(cagr, (int, float)) else "growth rate not estimated"

    narrative = (
        f"Market sizing produced TAM {tam_mid} ¹, SAM {sam_mid} ², SOM {som_mid} ³, "
        f"with {cagr_str}. TAM was triangulated via {methods_filled}/3 independent "
        f"methods (top-down industry-report, bottom-up firm-count × ACV, analog comp). "
        f"The SAM was derived from TAM via the serviceability waterfall (geo × ICP-fit × "
        f"reachable channel). SOM is anchored to a comparable company's year-3 ARR. "
    )
    if n_seg:
        narrative += f"TAM is segmented across {n_seg} sub-segments (sum-to-100 share). "
    weakest = payload.get("weakest_assumptions") or []
    if weakest:
        narrative += f"⚠ Weakest assumptions: {'; '.join(weakest[:3])}."

    takeaways = [
        f"TAM {tam_mid} from {methods_filled}/3 reconciled methods",
        f"SAM {sam_mid}; SOM {som_mid}",
        f"{cagr_str}",
    ]
    if weakest:
        takeaways.append(f"Weakest: {weakest[0][:60]}")
    return narrative.strip(), takeaways


def _template_personas(payload: dict) -> tuple[str, list[str]]:
    personas = payload.get("personas") or []
    n = len(personas)
    if n == 0:
        return "No buyer personas synthesized.", []

    wedge = payload.get("recommended_wedge_persona", "")
    bullets = []
    for p in personas[:3]:
        bullets.append(
            f"{p.get('name', '?')} ({p.get('id', '?')}, attractiveness "
            f"{p.get('attractiveness_for_wedge', '?')}/100): "
            f"motivated by '{(p.get('core_motivation') or '')[:70]}', "
            f"reach via '{(p.get('best_channel') or '')[:50]}'."
        )

    narrative = (
        f"Synthesized {n} buyer persona(s) from decoded customer voice. "
        + " ".join(bullets)
        + (f" Recommended wedge: **{wedge}**." if wedge else "")
    )
    takeaways = [f"{p.get('name','?')}: {(p.get('key_pain') or '')[:60]}" for p in personas[:3]]
    return narrative, takeaways


def _template_differentiators(payload: dict) -> tuple[str, list[str]]:
    diffs = payload.get("differentiators") or []
    strength = payload.get("differentiation_strength", "?")
    if not diffs:
        return ("No differentiators identified — this is a critical finding. "
                "The venture may be competing on parity rather than uniqueness.",
                ["⚠ 0 differentiators — operator review required"])
    by_dim: dict[str, list[str]] = {}
    for d in diffs:
        if isinstance(d, dict):
            by_dim.setdefault(d.get("dimension", "other"), []).append(d.get("feature", ""))
    bullet_str = "; ".join(f"{dim}: {', '.join(feats[:2])}" for dim, feats in by_dim.items())
    narrative = (
        f"Identified {len(diffs)} differentiators across {len(by_dim)}/5 dimensions "
        f"(strength={strength}). {bullet_str}."
    )
    takeaways = [f"[{d.get('dimension', '?')}] {d.get('feature', '?')}" for d in diffs[:4]]
    return narrative, takeaways


def _template_viability(payload: dict) -> tuple[str, list[str]]:
    score = payload.get("viability_score") or payload.get("score")
    confidence = payload.get("confidence", "?")
    summary = payload.get("summary") or payload.get("narrative") or ""
    risks = payload.get("risks") or []
    risk_count = len(risks)

    narrative = (
        f"Viability score: **{score}/100** (confidence: {confidence}). "
        f"{summary[:300]} "
        f"{risk_count} key risks identified."
    )
    takeaways = [f"Score {score}/100 ({confidence})"]
    for r in (risks[:3] if isinstance(risks, list) else []):
        if isinstance(r, dict):
            takeaways.append(f"Risk: {(r.get('risk') or r.get('text') or '')[:70]}")
    return narrative.strip(), takeaways


# Map of section_name → template function
_TEMPLATES = {
    "market_sizing": _template_market_sizing,
    "personas":      _template_personas,
    "differentiators": _template_differentiators,
    "viability":     _template_viability,
}


# ---------------------------------------------------------------------------
# LLM mode — for sections where richer prose is wanted
# ---------------------------------------------------------------------------
_LLM_NARRATE_SYSTEM = (
    "You are a senior consultant writing one section of a market research report. "
    "You receive a STRUCTURED PAYLOAD with all the underlying numbers. Your only job "
    "is to render the prose: every sentence anchored to a number from the payload, "
    "every paragraph opens with an imperative-verb recommendation, no buzzwords."
)

_LLM_NARRATE_USER = """Render the {section_name} section as a 2-3 paragraph prose
narrative + 3-5 key_takeaways. The structured payload is:

{payload_json}

RULES:
- Anchor every claim to a specific number from the payload (cite as ¹ ² ³)
- Open each paragraph with an imperative verb
- ≤20 words per sentence average
- BANNED words: leverage, synergies, holistic, robust, best-in-class, paradigm,
  streamline, cutting-edge, world-class, transformational, unlock, proactively
- If the payload is thin/empty, say so honestly: "Data is thin on X — operator
  should validate via Y." Don't fake conviction.

Return EXACTLY this JSON:
{{
  "narrative": "2-3 paragraphs ...",
  "key_takeaways": ["≤18 word bullet 1", "bullet 2", "bullet 3"]
}}"""


def _llm_narrate(section_name: str, payload: Any) -> dict:
    """Call the LLM to produce prose from a structured payload.
    Returns {"narrative": str, "key_takeaways": list}."""
    import json
    from llm import call_json
    payload_str = json.dumps(payload, indent=2, default=str)[:4000]
    result = call_json(
        system=_LLM_NARRATE_SYSTEM,
        user=_LLM_NARRATE_USER.format(section_name=section_name, payload_json=payload_str),
        max_tokens=1500,
    )
    if "_parse_error" in result:
        return {"narrative": "(narration LLM failed — payload available)",
                "key_takeaways": [], "_parse_error": True}
    if isinstance(result, list) and result and isinstance(result[0], dict):
        result = result[0]
    if not isinstance(result, dict):
        return {"narrative": "(unexpected narration shape)", "key_takeaways": []}
    return {
        "narrative": str(result.get("narrative") or ""),
        "key_takeaways": list(result.get("key_takeaways") or []),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
@skill(produces="narration", consumes=[
    "market_sizing", "personas", "differentiators", "viability",
    "psm_pricing", "feature_ranking", "four_ps", "customer_universe",
])
def narrate_section(section_name: str, payload: Any, mode: str = "llm") -> Evidence:
    """Render prose for one report section from a structured payload.

    Args:
      section_name: which section (e.g. 'market_sizing', 'personas', 'viability')
      payload:      the structured data to narrate (dict)
      mode:         'template' (deterministic, no LLM) | 'llm' (rich prose)
                    | 'hybrid' (currently same as 'llm' — placeholder for future)

    Returns Evidence with payload = {"narrative": str, "key_takeaways": list}.
    """
    if mode == "template":
        templater = _TEMPLATES.get(section_name)
        if templater is None:
            return Evidence(
                source="narrate_section", category="skill_output",
                count=0, payload={"narrative": "", "key_takeaways": []},
                error=f"No deterministic template for section '{section_name}'",
                cost_meta={"mode": "template", "section": section_name},
            )
        narrative, takeaways = templater(payload or {})
        return Evidence(
            source="narrate_section", category="skill_output",
            count=1 if narrative else 0,
            payload={"narrative": narrative, "key_takeaways": takeaways},
            cost_meta={"mode": "template", "section": section_name, "llm_calls": 0},
        )

    # LLM mode (default)
    out = _llm_narrate(section_name, payload)
    if out.get("_parse_error"):
        return Evidence(
            source="narrate_section", category="skill_output",
            count=0, payload={"narrative": "", "key_takeaways": []},
            error="LLM narration parse_error",
            cost_meta={"mode": mode, "section": section_name, "llm_calls": 1},
        )
    return Evidence(
        source="narrate_section", category="skill_output",
        count=1 if out["narrative"] else 0,
        payload=out,
        cost_meta={"mode": mode, "section": section_name, "llm_calls": 1,
                   "narrative_chars": len(out["narrative"])},
    )


def narrate_report(payloads: dict, mode: str = "llm") -> dict:
    """Narrate every section of a report. Returns {section: Evidence}.

    Args:
      payloads: dict mapping section_name → payload dict (e.g. from skill outputs)
      mode:     applied to every section uniformly
    """
    return {
        section: narrate_section(section, payload, mode=mode)
        for section, payload in payloads.items()
    }
