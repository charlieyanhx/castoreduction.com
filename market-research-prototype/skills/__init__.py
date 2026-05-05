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
from . import pipeline_steps  # noqa: F401
from . import narration       # noqa: F401

__all__ = [
    "SkillMeta", "SKILL_REGISTRY", "skill",
    "list_skills", "produces_set",
    "get_skill", "describe_skill", "describe_all_skills",
]
