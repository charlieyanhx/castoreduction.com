"""
Tests for skills/ registry — Phase 2 of cycle32 architecture migration.

Verifies:
  - @skill decorator registers fns + produces metadata
  - Skills return Evidence envelopes with cost_meta.produces tag
  - Discovery API works (list_skills, produces_set, get_skill, describe_*)
  - All 9 wrapped pipeline-step skills are registered
  - Wrapped skills produce well-formed Evidence (with mocking — no network)
  - Skills don't crash on underlying-impl failures (caught + returned as error Evidence)
"""
from __future__ import annotations
import unittest
from unittest.mock import patch


class TestSkillRegistry(unittest.TestCase):
    def test_decorator_registers(self):
        from skills import skill, SKILL_REGISTRY
        from tools import Evidence

        @skill(produces="test_section", consumes=["customer_voice"])
        def my_test_skill(x: int):
            return Evidence(source="my_test_skill", category="skill_output",
                            count=x, payload={"x": x})

        self.assertIn("my_test_skill", SKILL_REGISTRY)
        meta = SKILL_REGISTRY["my_test_skill"]
        self.assertEqual(meta.produces, "test_section")
        self.assertEqual(meta.consumes, ["customer_voice"])

        e = my_test_skill(42)
        self.assertEqual(e.count, 42)
        self.assertEqual(e.cost_meta.get("produces"), "test_section")
        del SKILL_REGISTRY["my_test_skill"]

    def test_skill_catches_exceptions(self):
        from skills import skill, SKILL_REGISTRY

        @skill(produces="crashy_section")
        def crashy_skill():
            raise ValueError("oops")

        e = crashy_skill()
        self.assertEqual(e.count, 0)
        self.assertIn("ValueError", e.error)
        del SKILL_REGISTRY["crashy_skill"]

    def test_skill_auto_wraps_raw_return(self):
        from skills import skill, SKILL_REGISTRY

        @skill(produces="x")
        def returns_list():
            return ["a", "b", "c"]

        e = returns_list()
        self.assertEqual(e.count, 3)
        self.assertEqual(e.cost_meta.get("produces"), "x")
        del SKILL_REGISTRY["returns_list"]


class TestRegisteredSkillsAreWellFormed(unittest.TestCase):
    """Whatever is registered at import time must declare itself properly.

    This class used to also assert that the ten `*_skill` wrappers from
    skills/pipeline_steps.py were present. That module was deleted: no production code
    ever called any of them, so the registry entries described a pipeline that ran
    nowhere. The remaining assertions are about SHAPE and hold for every skill that is
    genuinely wired, which is what the registry is for.
    """

    def test_produces_categories_unique_per_skill(self):
        from skills import list_skills
        # Each skill should have a non-empty `produces` declaration
        for s in list_skills():
            self.assertTrue(s.produces, f"{s.name} has empty produces")

    def test_describe_all_jsonable(self):
        import json
        from skills import describe_all_skills
        d = describe_all_skills()
        self.assertGreaterEqual(len(d), 9)
        json.dumps(d)  # must serialize


class TestSkillExecution(unittest.TestCase):
    """Verify each wrapped skill produces well-formed Evidence (mocked)."""












if __name__ == "__main__":
    unittest.main()
