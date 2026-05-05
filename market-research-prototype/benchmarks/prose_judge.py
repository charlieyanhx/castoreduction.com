"""
benchmarks/prose_judge.py — LLM-as-judge head-to-head against consulting prose.

Compares our 4Ps narrative prose against the structural traits of professional
consulting writeups (McKinsey, BCG, Deloitte, Bain, PwC public insights).

Approach (no copyright issues):
  1. We DO NOT include verbatim consulting prose passages — that would be a
     copyright/redistribution problem. Instead we encode the structural rubric
     those firms publicly publish in their style guides + research methodology
     pages.
  2. The LLM judge evaluates each of our 4Ps sections against the rubric on:
       - Specificity (use of named numbers / brands / percentages)
       - Citation density (claims per ¹² ³ marker)
       - Action orientation (recommendations vs description)
       - Hedging discipline (flagging data gaps where appropriate)
       - Executive readability (plain English, no buzzword padding)
  3. Each section gets a 0-100 score. Average is the prose-quality dimension.

Two of the five traits are MEASURED deterministically (specificity and citation
density) via regex over the prose. Three are LLM-judged. Final score per section
is a weighted blend.

Usage:
  from benchmarks.prose_judge import judge_prose
  score = judge_prose(four_ps_dict)  # 0-100
"""
from __future__ import annotations
import re
from typing import Any

# ---------------------------------------------------------------------------
# Deterministic measures
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"\$?\b\d+(?:,\d{3})*(?:\.\d+)?\s*(?:%|[KMB]?\b|million|billion)?", re.I)
_BRAND_RE = re.compile(r"\b[A-Z][a-zA-Z]+(?:\.[a-z]+)?\b")  # crude proper-noun proxy
_CITATION_RE = re.compile(r"[¹²³⁴⁵⁶⁷⁸⁹⁰]")
_HEDGE_RE = re.compile(
    r"\b(data is thin|data-thin|insufficient signal|cannot be determined|"
    r"requires validation|directional only|no clear|tbd|"
    r"operator should validate|further research|interview validation)\b",
    re.I,
)
_BUZZWORD_RE = re.compile(
    r"\b(synergies|leverage(?:s|d|ing)?|paradigm|holistic|robust|"
    r"best-in-class|unlock value|streamline|cutting[- ]edge|"
    r"world-class|transformational journey|proactively)\b",
    re.I,
)


def _count_specifics(text: str) -> dict:
    """Count concrete data points in a passage."""
    return {
        "numbers": len(_NUMBER_RE.findall(text)),
        "proper_nouns": len(_BRAND_RE.findall(text)),
        "citations": len(_CITATION_RE.findall(text)),
        "hedges": len(_HEDGE_RE.findall(text)),
        "buzzwords": len(_BUZZWORD_RE.findall(text)),
        "words": len(text.split()),
    }


def _specificity_score(stats: dict) -> int:
    """0-100. Target: ≥1 number per 50 words, ≥1 proper-noun per 30 words."""
    w = max(1, stats["words"])
    nums_per_100w = stats["numbers"] / w * 100
    nouns_per_100w = stats["proper_nouns"] / w * 100
    # 2 numbers per 100 words → full credit; <0.5 → zero credit
    n_score = min(100, max(0, (nums_per_100w - 0.5) / (2 - 0.5) * 100))
    # 4 proper-nouns per 100 words → full credit
    p_score = min(100, max(0, (nouns_per_100w - 1.0) / (4 - 1.0) * 100))
    return round((n_score + p_score) / 2)


def _citation_density_score(stats: dict) -> int:
    """0-100. Target: ≥1 citation per 75 words."""
    w = max(1, stats["words"])
    cites_per_100w = stats["citations"] / w * 100
    # 1.3 citations per 100 words → full credit (= 1 per 75 words)
    return round(min(100, max(0, cites_per_100w / 1.3 * 100)))


def _buzzword_penalty(stats: dict) -> int:
    """0-100 (higher = better, less buzzword density). Target: ≤1 per 200 words."""
    w = max(1, stats["words"])
    bw_per_100w = stats["buzzwords"] / w * 100
    # 0 → 100, 0.5/100w → 50, ≥1/100w → 0
    return round(max(0, 100 - bw_per_100w * 100))


# ---------------------------------------------------------------------------
# LLM-judged dimensions
# ---------------------------------------------------------------------------

_JUDGE_SYSTEM = (
    "You are a senior partner at a tier-1 consulting firm (McKinsey/BCG/Bain/Deloitte) "
    "reviewing a junior associate's draft. Your reviews are blunt, specific, and grounded "
    "in the public style standards your firm enforces. Return only JSON."
)

_JUDGE_PROMPT = """Rate this 4Ps section against the structural traits that
distinguish professional consulting prose. Score each trait 0-100:

  • action_orientation — Does the prose make concrete recommendations
    (verbs: "do X", "shift to Y", "stop Z") or only describe? 100 = every
    paragraph ends with an action; 0 = pure description.

  • hedging_discipline — Does the prose flag data gaps honestly when
    appropriate (e.g. "data is thin on Z, validate via interviews") OR
    over-hedge to the point of being useless? 100 = flags ≥1 explicit gap
    AND still commits to a directional view; 0 = either wholly unhedged
    fake conviction OR endless qualifiers without a recommendation.

  • executive_readability — Plain English, short sentences, parallel
    structure, no buzzword padding. 100 = a CFO could skim and act on it
    in 30 seconds; 0 = MBA word salad.

SECTION NAME: {section_name}

PROSE TO EVALUATE:
\"\"\"
{prose}
\"\"\"

Return ONLY this JSON:
{{
  "action_orientation_score": <0-100 integer>,
  "action_orientation_reasoning": "1 short sentence — quote the BEST and WORST line",
  "hedging_discipline_score": <0-100 integer>,
  "hedging_discipline_reasoning": "1 short sentence",
  "executive_readability_score": <0-100 integer>,
  "executive_readability_reasoning": "1 short sentence",
  "blunt_partner_takeaway": "≤25 words — what would your senior partner say?"
}}"""


def _llm_judge_section(section_name: str, prose: str) -> dict:
    """Call the LLM to judge one 4Ps section. Returns dict of scores."""
    from llm import call_json  # local import to avoid top-level dep when unused
    if not prose or len(prose.strip()) < 100:
        return {
            "action_orientation_score": 0,
            "hedging_discipline_score": 0,
            "executive_readability_score": 0,
            "blunt_partner_takeaway": "(prose too short to evaluate)",
            "_skipped": True,
        }
    result = call_json(
        system=_JUDGE_SYSTEM,
        user=_JUDGE_PROMPT.format(section_name=section_name, prose=prose[:3000]),
        max_tokens=1200,
    )
    if "_parse_error" in result:
        return {
            "action_orientation_score": 50,
            "hedging_discipline_score": 50,
            "executive_readability_score": 50,
            "blunt_partner_takeaway": "(LLM judge returned malformed JSON)",
            "_parse_error": True,
        }
    # cycle31-r3: Gemini occasionally returns a JSON LIST at top-level (e.g.
    # [{action_orientation_score: ...}]) instead of a dict — coerce.
    if isinstance(result, list):
        if result and isinstance(result[0], dict):
            result = result[0]
        else:
            return {
                "action_orientation_score": 50,
                "hedging_discipline_score": 50,
                "executive_readability_score": 50,
                "blunt_partner_takeaway": "(LLM judge returned non-dict list)",
                "_shape_error": True,
            }
    if not isinstance(result, dict):
        return {
            "action_orientation_score": 50,
            "hedging_discipline_score": 50,
            "executive_readability_score": 50,
            "blunt_partner_takeaway": f"(LLM judge returned {type(result).__name__})",
            "_shape_error": True,
        }
    return result


# ---------------------------------------------------------------------------
# Composite per-section + overall score
# ---------------------------------------------------------------------------

# Within each section: 5 traits, equal weight.
_TRAIT_WEIGHTS = {
    "specificity": 0.25,        # deterministic
    "citation_density": 0.20,    # deterministic
    "no_buzzwords": 0.15,        # deterministic
    "action_orientation": 0.15,  # LLM
    "hedging_discipline": 0.10,  # LLM
    "executive_readability": 0.15,  # LLM
}


def _score_section(section_name: str, section_data: dict, use_llm: bool = True) -> dict:
    prose = section_data.get("narrative") or ""
    stats = _count_specifics(prose)
    deterministic = {
        "specificity": _specificity_score(stats),
        "citation_density": _citation_density_score(stats),
        "no_buzzwords": _buzzword_penalty(stats),
    }
    if use_llm and len(prose) >= 100:
        llm = _llm_judge_section(section_name, prose)
        # cycle31-r3: coerce non-numeric LLM scores to 50 (was previously crashing)
        def _coerce(v):
            if isinstance(v, (int, float)): return float(v)
            if isinstance(v, str):
                import re as _re
                m = _re.search(r"\d+(?:\.\d+)?", v)
                return float(m.group()) if m else 50.0
            return 50.0
        llm_scores = {
            "action_orientation": _coerce(llm.get("action_orientation_score", 50)) if isinstance(llm, dict) else 50,
            "hedging_discipline": _coerce(llm.get("hedging_discipline_score", 50)) if isinstance(llm, dict) else 50,
            "executive_readability": _coerce(llm.get("executive_readability_score", 50)) if isinstance(llm, dict) else 50,
        }
    else:
        # No LLM call — use deterministic-only score, default LLM dims to 50
        llm = {"_skipped": True, "blunt_partner_takeaway": "(LLM judge skipped)"}
        llm_scores = {
            "action_orientation": 50,
            "hedging_discipline": 50,
            "executive_readability": 50,
        }
    all_scores = {**deterministic, **llm_scores}
    weighted = sum(all_scores[k] * _TRAIT_WEIGHTS[k] for k in _TRAIT_WEIGHTS)
    return {
        "section": section_name,
        "score": round(weighted, 1),
        "trait_scores": all_scores,
        "stats": stats,
        "blunt_partner_takeaway": llm.get("blunt_partner_takeaway", ""),
        "llm_skipped": llm.get("_skipped", False),
    }


def judge_prose(four_ps: dict, use_llm: bool = True) -> dict:
    """
    Score the 4Ps prose quality vs consulting standards.

    Returns:
      {
        "score": 0-100 average,
        "per_section": [{section, score, trait_scores, stats, blunt_partner_takeaway}],
        "blunt_partner_takeaway": "overall comment"
      }
    """
    if not four_ps or not isinstance(four_ps, dict):
        return {"score": 0, "per_section": [], "detail": "no four_ps block"}
    per_section = []
    for sec in ("product", "price", "place", "promotion"):
        sec_data = four_ps.get(sec) or {}
        per_section.append(_score_section(sec, sec_data, use_llm=use_llm))
    avg = sum(s["score"] for s in per_section) / max(1, len(per_section))
    # Compose an overall takeaway from worst-rated section
    worst = min(per_section, key=lambda s: s["score"])
    return {
        "score": round(avg, 1),
        "per_section": per_section,
        "detail": (
            f"avg across 4 sections: {avg:.1f}/100 — "
            f"weakest: {worst['section']} ({worst['score']}/100)"
        ),
        "weakest_section": worst["section"],
        "weakest_takeaway": worst["blunt_partner_takeaway"],
    }


# ---------------------------------------------------------------------------
# CLI for quick standalone testing
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m benchmarks.prose_judge <path-to-job-or-result.json> [--no-llm]", file=sys.stderr)
        sys.exit(1)
    use_llm = "--no-llm" not in sys.argv
    src = sys.argv[1]
    data = json.loads(open(src).read())
    four_ps = (data.get("result") or data).get("four_ps") or {}
    out = judge_prose(four_ps, use_llm=use_llm)
    print(json.dumps(out, indent=2, default=str))
