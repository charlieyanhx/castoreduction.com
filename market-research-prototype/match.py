"""
Match scoring: given a product idea and a taste profile, return a 0-100
match score + reasoning + suggested creative angles + risks.
"""
from __future__ import annotations
import json
import sys

from llm import call_json
from logger import get

log = get("match")


MATCH_PROMPT = """You are scoring how well a product idea matches a decoded audience taste profile.

Product idea:
{idea}

Audience taste profile:
{profile}

Return JSON (keep all values concise — max 1-2 sentences per list item):
{{
  "match_score": 0-100,
  "score_breakdown": {{
    "pain_alignment": 0-100,
    "aesthetic_fit": 0-100,
    "vocabulary_fit": 0-100,
    "motivation_match": 0-100
  }},
  "why_it_matches": ["up to 4 specific reasons, short sentences"],
  "why_it_might_not": ["up to 3 specific risks, short sentences"],
  "recommended_positioning": "one short paragraph (2-3 sentences) in this audience's voice",
  "recommended_hook_angles": [
    {{"hook": "actual ad copy, ≤20 words", "reasoning": "≤15 words why this hits"}},
    {{"hook": "...", "reasoning": "..."}},
    {{"hook": "...", "reasoning": "..."}}
  ],
  "required_proof_points": ["up to 3 short bullets"],
  "offer_shape_suggestion": "one short sentence"
}}

Rules:
- Quote specific phrases from the taste profile in your reasoning
- If the audience is a poor fit, say so clearly — do not force a match
- Hook angles must be concrete ad copy, not generic marketing speak
- Keep everything CONCISE — long JSON gets truncated"""


def score_match(product_idea: str, taste_profile: dict) -> dict:
    result = call_json(
        system="You are a DTC strategist matching products to audiences. Return concise JSON.",
        user=MATCH_PROMPT.format(
            idea=product_idea[:2000],
            profile=json.dumps(taste_profile, indent=2)[:5000],
        ),
        max_tokens=3500,
    )
    # If LLM output was malformed/truncated, return honest error
    if isinstance(result, dict) and "_parse_error" in result:
        return {
            "error": (
                "LLM returned malformed JSON (likely truncated). "
                "Try again — Gemini may have hit a size limit."
            ),
            "match_score": None,
            "_raw_preview": (result.get("_raw") or "")[:600],
        }
    return result


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    if len(sys.argv) < 3:
        log.info('usage: python match.py "product idea description" path/to/taste_profile.json')
        sys.exit(1)
    idea = sys.argv[1]
    with open(sys.argv[2]) as f:
        profile = json.load(f)
    out = score_match(idea, profile)
    print(json.dumps(out, indent=2, default=str))
