"""
Render a discover + taste + match result bundle as Markdown.

Markdown is chosen over PDF because:
  - Renders in any browser / editor / GitHub / email
  - Easy to pipe into other tools
  - Converts to PDF trivially via pandoc or mdpdf if needed
  - Zero additional dependencies
"""
from __future__ import annotations
import json
from datetime import datetime


def render_discover(result: dict) -> str:
    """Render the output of discover.discover() as a Markdown report."""
    lines: list[str] = []
    cat = result.get("category", "?")
    lines.append(f"# Opportunity Report — {cat}")
    lines.append("")
    lines.append(f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} · geo: {result.get('geo', '?')}_")
    lines.append("")

    # Category read (synthesis)
    synth = result.get("synthesis") or {}
    read = synth.get("category_read") or synth.get("summary")
    if read:
        lines.append("## Category summary")
        lines.append("")
        lines.append(str(read))
        lines.append("")

    # Meta signals
    density = result.get("competitor_density")
    avg = result.get("avg_opportunity_score")
    if density is not None or avg is not None:
        lines.append("## Meta signals")
        lines.append("")
        if density is not None:
            lines.append(f"- **Competitor density:** {density} brands with meaningful signal")
        if avg is not None:
            lines.append(f"- **Average opportunity score:** {avg}")
        trends = (result.get("steps") or {}).get("trends") or {}
        if trends.get("slope_12m") is not None:
            lines.append(f"- **Category trend slope (12mo):** {trends['slope_12m']:+.1%}")
        lines.append("")

    # Ranked opportunities
    opps = synth.get("ranked_opportunities") or []
    if opps:
        lines.append("## Ranked opportunities")
        lines.append("")
        for i, o in enumerate(opps, 1):
            lines.append(f"### {i}. {o.get('brand', '?')} — score {o.get('opportunity_score', '?')}")
            lines.append("")
            if o.get("domain"):
                lines.append(f"**Domain:** [{o['domain']}](https://{o['domain']})")
                lines.append("")
            if o.get("thesis"):
                lines.append(f"**Thesis:** {o['thesis']}")
                lines.append("")
            sigs = o.get("signals") or {}
            if sigs:
                lines.append("**Signals:**")
                for k, v in sigs.items():
                    if v is not None and v != "":
                        lines.append(f"- {k}: {v}")
                lines.append("")
            if o.get("suggested_next_step"):
                lines.append(f"_Suggested next step: **{o['suggested_next_step']}**_")
                lines.append("")

    # Raw signals (collapsed)
    signals = (result.get("steps") or {}).get("signals") or []
    if signals:
        lines.append("## Raw signal data")
        lines.append("")
        lines.append("<details><summary>Click to expand</summary>")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(signals, indent=2, default=str)[:8000])
        lines.append("```")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    return "\n".join(lines)


def render_taste(profile: dict) -> str:
    """Render a taste profile as Markdown."""
    lines: list[str] = []
    lines.append(f"# Audience taste profile — {profile.get('brand', '?')}")
    lines.append("")
    if profile.get("error"):
        lines.append(f"> Error: {profile['error']}")
        return "\n".join(lines)

    conf = profile.get("confidence")
    if conf is not None:
        lines.append(f"**Confidence:** {conf} — {profile.get('confidence_reasoning', '')}")
        lines.append("")

    motivation = profile.get("purchase_motivation")
    if motivation:
        lines.append(f"**Purchase motivation:** {motivation}")
        lines.append("")

    for section, title in [
        ("aesthetic_descriptors", "Aesthetic"),
        ("vocabulary_repeats", "Recurring vocabulary"),
        ("adjacent_brands_mentioned", "Adjacent brands"),
        ("life_context", "Life context"),
        ("pre_purchase_objections", "Pre-purchase objections"),
    ]:
        items = profile.get(section) or []
        if items:
            lines.append(f"## {title}")
            for it in items:
                lines.append(f"- {it}")
            lines.append("")

    triggers = profile.get("emotional_triggers") or {}
    if triggers:
        lines.append("## Emotional triggers")
        if triggers.get("celebrated"):
            lines.append("**Celebrated:**")
            for t in triggers["celebrated"]:
                lines.append(f"- {t}")
            lines.append("")
        if triggers.get("complained"):
            lines.append("**Complained:**")
            for t in triggers["complained"]:
                lines.append(f"- {t}")
            lines.append("")

    hooks = profile.get("hook_angles_that_would_work") or []
    if hooks:
        lines.append("## Hook angles that would work")
        for h in hooks:
            lines.append(f"1. {h}")
        lines.append("")

    return "\n".join(lines)


def render_match(result: dict) -> str:
    lines: list[str] = []
    score = result.get("match_score")
    lines.append(f"# Match report — score {score}/100")
    lines.append("")
    breakdown = result.get("score_breakdown") or {}
    if breakdown:
        lines.append("## Score breakdown")
        for k, v in breakdown.items():
            lines.append(f"- **{k}:** {v}")
        lines.append("")
    if result.get("recommended_positioning"):
        lines.append("## Recommended positioning")
        lines.append("")
        lines.append(result["recommended_positioning"])
        lines.append("")
    hooks = result.get("recommended_hook_angles") or []
    if hooks:
        lines.append("## Recommended hook angles")
        for h in hooks:
            if isinstance(h, dict):
                lines.append(f"- **{h.get('hook', '')}** — _{h.get('reasoning', '')}_")
            else:
                lines.append(f"- {h}")
        lines.append("")
    for section in ["why_it_matches", "why_it_might_not", "required_proof_points"]:
        items = result.get(section) or []
        if items:
            lines.append(f"## {section.replace('_', ' ').title()}")
            for it in items:
                lines.append(f"- {it}")
            lines.append("")
    if result.get("offer_shape_suggestion"):
        lines.append("## Offer shape")
        lines.append("")
        lines.append(result["offer_shape_suggestion"])
        lines.append("")
    return "\n".join(lines)


def render_full(bundle: dict) -> str:
    """bundle has keys 'discover' and 'tastes'."""
    parts = []
    disc = bundle.get("discover") or {}
    parts.append(render_discover(disc))
    parts.append("\n---\n")
    tastes = bundle.get("tastes") or {}
    for brand, profile in tastes.items():
        parts.append(render_taste(profile))
        parts.append("\n---\n")
    return "\n".join(parts)
