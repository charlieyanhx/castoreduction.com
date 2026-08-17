"""
Step 2 of spec: Extract a structured company profile from a raw startup description.

The profile drives every downstream step — category is used for discover,
core_features for Max-Diff, target_pain_points for taste matching, etc.
"""
from __future__ import annotations
import json
import sys

from llm import call_json
from logger import get

log = get("profile")


EXTRACT_PROMPT = """You are a market research analyst. Extract structured data from the following company/startup description.

Company description:
{description}

Return JSON:
{{
  "name": "brand/company name, or 'Unknown' if not mentioned",
  "summary": "2-3 sentences in plain English describing what they do",
  "category": "3-5 word product category, e.g. 'protein bars', 'B2B project management software', 'cold brew coffee'",
  "core_features": ["bullet list of what the product actually does — 3-8 items"],
  "target_pain_points": ["bullet list of problems it solves for customers — 3-6 items"],
  "apparent_target_customer": "one sentence describing who seems to be the buyer",
  "target_employee_band": "if B2B and the description mentions a target customer-size band (e.g. '200-2000 employees', 'mid-market', 'SMB'), return it verbatim; else empty string",
  "business_model": "e.g. 'DTC e-commerce', 'B2B SaaS subscription', 'marketplace', 'service', 'consumer app'",
  "geography": "primary market — e.g. 'US', 'Global', 'UK'",
  "named_competitors": ["EXTRACT competitor brand names explicitly mentioned in the description (look for phrases like 'we compete with X, Y, Z', 'alternatives are X', 'similar to X'). Empty list if none mentioned. Do NOT invent competitors not in the description."],
  "tam_scope_hint": "cycle31-r2: a SHORT phrase (≤15 words) describing the SUBSEGMENT this venture targets, NOT the full category. Use this to anchor TAM scoping. Examples: 'AI customer-support agents (subsegment of CS software)' / 'AI-native APM for OTel-instrumented teams (vs full APM market)' / 'cyber insurance for SMBs without a CISO (vs full insurance market)'. If venture is broad/horizontal, return 'broad horizontal — no narrower scope hint'."
}}

Rules:
- If a field can't be inferred from the description, use "unknown" or an empty list
- Keep category PRECISE — this drives competitive research
- Don't invent details not in the description
- named_competitors: ONLY copy brands that literally appear in the source text"""


_PLACEHOLDER_NAMES = frozenset((
    "unknown", "n/a", "na", "none", "null", "tbd", "tba", "unnamed", "not specified",
    "not stated", "not mentioned", "unspecified", "no name", "-", "?",
))


def clean_company_name(name: str | None) -> str | None:
    """The company's real name, or None when the brief never gave one.

    The extraction prompt asks for the literal string 'Unknown' when no company is named, and
    a downstream prompt then treated it as the name. Every consumer must be able to tell "no
    name" from "a company called Unknown", and the only way to guarantee that is to stop
    representing absence with a word.
    """
    n = (name or "").strip()
    return None if not n or n.lower().strip(".") in _PLACEHOLDER_NAMES else n


def extract_company_profile(description: str) -> dict:
    """Parse a raw startup/company description into structured profile."""
    log.info(f"[profile] extracting from {len(description)} chars")

    if len(description.strip()) < 30:
        return {
            "error": "Description too short. Please provide at least a few sentences about what the company does.",
        }

    profile = call_json(
        system="You extract structured company profiles from raw text descriptions. Be precise and factual.",
        user=EXTRACT_PROMPT.format(description=description[:5000]),
        max_tokens=1500,
    )

    if "_parse_error" in profile:
        return {
            "error": f"LLM returned malformed JSON: {profile.get('_parse_error')}",
            "_raw_preview": profile.get("_raw", "")[:500],
        }

    # Validate minimum required fields
    required = ["name", "summary", "category"]
    missing = [f for f in required if not profile.get(f)]
    if missing:
        return {
            **profile,
            "error": f"LLM missed required fields: {missing}",
        }

    # The prompt above ASKS for 'Unknown' when the brief names no company, which is the right
    # thing for the extractor and the wrong thing for everything downstream: a live report
    # told its reader "As an emerging orbital solar reflection infrastructure provider,
    # Unknown can capture the market by bridging the gap...". The sentinel had been handed to
    # the differentiators prompt as the company's name and came back as prose. Normalise it
    # to None at the boundary, so no consumer can mistake a placeholder for a name.
    profile["name"] = clean_company_name(profile.get("name"))

    log.info(f"[profile] extracted: {profile.get('name')} | {profile.get('category')}")
    return profile


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    if len(sys.argv) < 2:
        log.info("usage: python profile.py 'your startup description here'")
        sys.exit(1)
    desc = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    out = extract_company_profile(desc)
    print(json.dumps(out, indent=2, default=str))
