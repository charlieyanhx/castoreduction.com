"""
Step 6 of spec (deeper version): synthesize 2-3 distinct buyer personas
from multiple decoded audience profiles.

Currently we decode taste for ONE top competitor. This module:
  - Takes 2-3 decoded taste profiles (already gathered from top brands)
  - Asks the LLM to identify if they represent the SAME audience or
    DIFFERENT audiences, and if different, label each persona
  - Returns 1-3 distinct persona definitions with attractiveness ranking

This makes the 4Ps actionable per-persona instead of averaged.
"""
from __future__ import annotations
import json
from llm import call_json
from logger import get

log = get("personas")


PERSONAS_PROMPT = """You are a senior market research analyst. You have decoded customer taste profiles from {n} competitor brands. Identify the distinct BUYER PERSONAS within them.

PROFILES:
{profiles}

PRODUCT BEING MARKETED: {product_summary}

Your job:
1. Identify 2-3 DISTINCT buyer personas hidden within these profiles.
   - Even if all profiles describe roughly the same audience, look harder: there
     are almost always sub-segments by motivation (results-seeker vs novelty-seeker),
     by life-stage (early-career vs senior leader), or by buying-context (budget
     constrained vs premium).
   - Aim for 2-3 personas; only return 1 if the audience is genuinely
     monolithic (e.g. a hyper-niche product with one ICP).
2. For each persona, surface what makes them DIFFERENT from the others.
3. Rank personas by attractiveness for a NEW entrant: which one is the easiest wedge to win first?

Return JSON. CRITICAL: each persona MUST have ALL 9 fields filled — never leave
key_pain, winning_message, best_channel blank.

{{
  "personas_count": <1 or 2 — keep it tight; 3 truncates>,
  "personas": [
    {{
      "id": "P1",
      "name": "<2-4 word memorable label, e.g. 'Anxious First-Time Owners'>",
      "size_estimate": "<small | medium | large> portion of total market",
      "attractiveness_for_wedge": <1-100>,
      "key_pain": "<≤20 words — one specific complaint they would voice>",
      "best_channel": "<≤15 words — where to reach them, be specific>",
      "winning_message": "<≤25 words — one ad copy line that would resonate>",
      "evidence_brands": ["competitor profile(s) revealed this"],
      "what_makes_them_different": "<≤25 words comparing to the other personas>",
      "core_motivation": "<≤30 words — why they buy. Put longest field LAST so truncation kills only this>"
    }}
  ],
  "recommended_wedge_persona": "<P1|P2|P3>",
  "wedge_reasoning": "<one paragraph — why this persona is the right first beachhead>"
}}

CRITICAL:
- If all profiles describe basically the same audience, return 1 persona — don't manufacture diversity
- Use customer vocabulary from the source profiles, not marketing-speak
- Personas must be specific enough to design ads against
- "Attractiveness for wedge" considers: ease of reach, willingness to pay, low competition, viral potential
- EVERY persona MUST have non-empty: name, core_motivation, key_pain, winning_message, best_channel.
  If you can't fill any of these from the source data, fabricate a SHORT honest placeholder
  like "Specific pain not yet evidenced — needs interview validation" — but never leave blank."""


def synthesize_personas(taste_profiles: list[dict], product_summary: str) -> dict:
    """
    Cluster 1-3 decoded audiences into distinct buyer personas.

    Input: list of taste profiles (output of taste.decode_taste).
    Output: 1-3 personas with names, motivations, channels, attractiveness.
    """
    valid = [p for p in taste_profiles if p and not p.get("error") and p.get("brand")]
    if not valid:
        return {"error": "No valid taste profiles to synthesize personas from"}

    # Build compact source-of-truth blob for the prompt
    profiles_blob = []
    for p in valid:
        celebrated = (p.get("emotional_triggers") or {}).get("celebrated", [])[:4]
        complained = (p.get("emotional_triggers") or {}).get("complained", [])[:4]
        profiles_blob.append(
            f"BRAND: {p.get('brand')}\n"
            f"  Confidence: {p.get('confidence', '?')}\n"
            f"  Motivation: {(p.get('purchase_motivation') or '')[:200]}\n"
            f"  Celebrated: {celebrated}\n"
            f"  Complained: {complained}\n"
            f"  Life context: {(p.get('life_context') or [])[:3]}\n"
            f"  Hooks that work: {(p.get('hook_angles_that_would_work') or [])[:2]}\n"
        )
    profiles_text = "\n".join(profiles_blob)[:3500]  # iter 35: tightened from 6000 — already pre-distilled

    result = call_json(
        system="You synthesize buyer personas from raw customer voice. Return only JSON.",
        user=PERSONAS_PROMPT.format(
            n=len(valid),
            profiles=profiles_text,
            product_summary=product_summary[:500],
        ),
        max_tokens=4000,  # iter 43 (issue A): bumped 2500→4000 — 3 personas × 9 fields each was getting truncated mid-field
    )

    if "_parse_error" in result:
        return {
            "error": "Persona synthesis returned malformed JSON",
            "_raw_preview": (result.get("_raw") or "")[:400],
        }

    # Sanity: ensure personas list exists
    if "personas" not in result:
        result["personas"] = []
    # Iter 42 (issue 3): force personas_count = actual list length, ignore LLM's
    # claim. Was producing "Decoded from 3 segments" but only 1 persona rendered.
    result["personas_count"] = len(result.get("personas") or [])

    # Iter 43-cycle20: backstop required persona fields the LLM still sometimes
    # omits despite the prompt instruction. Never ship a persona with blanks.
    # cycle24: cleaner backstop strings — previous "Buys to resolve: X. Resonates with: Y"
    # read as machine-stitched garbage. Also dedupe key_pain when LLM crammed the
    # winning_message into it (common shape drift).
    for p in result.get("personas") or []:
        if not isinstance(p, dict):
            continue
        # Dedupe: if winning_message text appears inside key_pain, strip it out
        pain = (p.get("key_pain") or "").strip()
        msg = (p.get("winning_message") or "").strip()
        if pain and msg and msg in pain:
            cleaned = pain.replace(msg, "").strip(" .;,-")
            if cleaned:
                p["key_pain"] = cleaned
                pain = cleaned
        if not (p.get("core_motivation") or "").strip():
            # Honest placeholder beats stitched garbage. Operator can spot the gap.
            p["core_motivation"] = "Motivation not directly evidenced in source data — synthesize from interviews"
        for fld, default in (
            ("key_pain", "Pain point not directly evidenced — needs interview validation"),
            ("winning_message", "Message angle TBD — needs ad-copy testing"),
            ("best_channel", "Channel TBD — needs paid-channel discovery test"),
        ):
            if not (p.get(fld) or "").strip():
                p[fld] = default
    return result
