"""
skills/ — auto-discoverable workflows that compose tools.

Importing this package triggers registration of all skill modules.

Usage:
    from skills import list_skills, get_skill

    for s in list_skills():
        print(s.name, "→", s.produces, "(consumes:", s.consumes, ")")

    # Run a skill by name:
    e = get_skill("personas_skill").fn(taste_profiles, product_summary="...")
"""
from .registry import (
    SkillMeta,
    SKILL_REGISTRY,
    skill,
    list_skills,
    produces_set,
    get_skill,
    describe_skill,
    describe_all_skills,
)

# Trigger registration of all skill modules
from . import pipeline_steps  # noqa: F401  — 9 individual step skills
from . import narration       # noqa: F401  — prose generation (template + LLM)
from . import discovery       # noqa: F401  — harness-driven competitor discovery limb
from . import discovery_multi # noqa: F401  — multi-strategy fan-out discovery + direct/indirect
from . import perspective     # noqa: F401  — STORM-style consumer research (multi-perspective)
from . import sizing          # noqa: F401  — numbers-right engine (scale classifier, sizing)
from . import refine_report   # noqa: F401  — generator-evaluator-refine loop for reports
from . import pipeline        # noqa: F401  — TOP-LEVEL composition (the full /plan)

__all__ = [
    "SkillMeta", "SKILL_REGISTRY", "skill",
    "list_skills", "produces_set",
    "get_skill", "describe_skill", "describe_all_skills",
]
