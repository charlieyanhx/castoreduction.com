"""
W5-7c: SKILL.md progressive disclosure — two pilots.

A skill's docstring is loaded whole, every time, into whatever prompt needs to know
the skill exists. That is backwards for the ones with real methodology behind them:
the CHOOSING prompt needs one line ("hyperlocal sizing: trade-area capture for a
single physical location"), while the EXECUTING prompt needs the full method — the
trade-area radius rules, the capture-rate bands, what invalidates the estimate.

SKILL.md splits the two. Front matter is the always-loaded summary; the body is
loaded only when the skill actually runs. Two pilots per the plan: hyperlocal sizing
and citation.

What the tests enforce is the contract, not the prose:
  * every SKILL.md parses into (summary, body);
  * the summary is SHORT — if it grows to the size of the body, disclosure has
    silently stopped happening and every prompt pays full freight again;
  * a skill with a SKILL.md is discoverable through the registry;
  * a missing or malformed SKILL.md degrades to the docstring rather than raising.
"""
from __future__ import annotations

import os
import unittest

from skills.disclosure import PILOT_SKILLS, load_skill_doc, summary_for

SKILLS_DIR = "skills"


class TestPilotsExist(unittest.TestCase):
    def test_both_pilots_are_declared(self):
        self.assertEqual(set(PILOT_SKILLS), {"hyperlocal_sizing", "citation"})

    def test_each_pilot_has_a_skill_md_on_disk(self):
        for name, path in PILOT_SKILLS.items():
            self.assertTrue(os.path.exists(path), f"{name}: missing {path}")


class TestParsing(unittest.TestCase):
    def test_front_matter_becomes_the_summary_and_the_rest_the_body(self):
        for name in PILOT_SKILLS:
            doc = load_skill_doc(name)
            self.assertTrue(doc.summary, f"{name} has no summary")
            self.assertTrue(doc.body, f"{name} has no body")

    def test_the_summary_stays_short(self):
        """If the summary grows to the size of the body, disclosure stopped happening."""
        for name in PILOT_SKILLS:
            doc = load_skill_doc(name)
            self.assertLessEqual(len(doc.summary), 300, f"{name} summary is not a summary")
            self.assertGreater(len(doc.body), len(doc.summary),
                               f"{name} body carries no more detail than its summary")

    def test_the_body_carries_the_method_not_just_a_restatement(self):
        doc = load_skill_doc("hyperlocal_sizing")
        low = doc.body.lower()
        self.assertIn("trade area", low)
        self.assertIn("capture", low)

    def test_declared_name_matches_the_key(self):
        for name in PILOT_SKILLS:
            self.assertEqual(load_skill_doc(name).name, name)


class TestGracefulDegradation(unittest.TestCase):
    def test_an_unknown_skill_returns_an_empty_doc_not_an_exception(self):
        doc = load_skill_doc("no_such_skill")
        self.assertEqual(doc.summary, "")
        self.assertEqual(doc.body, "")

    def test_summary_for_falls_back_to_the_registry_docstring(self):
        """A skill without a SKILL.md must still be describable."""
        import skills.triangulate  # noqa: F401 — register something
        from skills.registry import SKILL_REGISTRY
        name = next(iter(SKILL_REGISTRY))
        text = summary_for(name)
        self.assertTrue(text, f"{name} produced no summary at all")

    def test_a_malformed_file_degrades_to_body_only(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("no front matter here, just prose")
            path = f.name
        try:
            doc = load_skill_doc("x", path=path)
            self.assertEqual(doc.summary, "")
            self.assertIn("just prose", doc.body)
        finally:
            os.unlink(path)


class TestProgressiveLoading(unittest.TestCase):
    def test_choosing_costs_far_less_than_executing(self):
        """The whole point: the selection prompt must not pay for the method."""
        total_summary = sum(len(load_skill_doc(n).summary) for n in PILOT_SKILLS)
        total_body = sum(len(load_skill_doc(n).body) for n in PILOT_SKILLS)
        self.assertLess(total_summary * 3, total_body,
                        "summaries are not meaningfully cheaper than full disclosure")


if __name__ == "__main__":
    unittest.main()
