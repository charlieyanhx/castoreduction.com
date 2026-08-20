"""Wave D of the shift-left redesign: the survey becomes a structured form.

The operator's spec (2026-08-19/20): money-mechanics and place are MULTIPLE CHOICE with a
write-in escape; the location is one entry field accepting any level (the geocoder
detects which); prices and volumes are required fill-ins with unit awareness and a period
selector; competitors and status quo are encouraging OPTIONAL pluses; evidence is
reframed as FEEDBACK; the year-one target gets a how-did-you-arrive follow-up; and no
copy anywhere carries an em dash.

These tests pin the QUESTION PAYLOAD contract the client renders from:
  {field, question, drives, consumer, input_kind, options?, write_in?, unit_hint?,
   optional?, period_choices?}
"""
from __future__ import annotations

import unittest

from intake_tree import plan_questions, next_question


def _cls(kind="transactional", physical=True, **kw):
    base = {"kind": kind, "is_physical": physical, "multi_location": False,
            "non_us": False, "launched": False, "regulated": False,
            "needs_fork": False, "fork_question": None}
    base.update(kw)
    return base


def _by_field(plan):
    return {q["field"]: q for q in plan}


class TestChoiceQuestions(unittest.TestCase):
    def test_the_kind_fork_is_multiple_choice_with_write_in(self):
        plan = plan_questions({}, _cls(needs_fork=True))
        fork = _by_field(plan)["kind_fork"]
        self.assertEqual(fork.get("input_kind"), "choice")
        self.assertTrue(fork.get("write_in"), "founders must be able to answer freely")
        opts = fork.get("options") or []
        self.assertGreaterEqual(len(opts), 6)
        blob = " ".join(o["label"] for o in opts).lower()
        # founder words, not taxonomy: the orbital founder answered our vocabulary
        # with "Undetermined"
        for anchor in ("netflix", "uber", "shop"):
            self.assertIn(anchor, blob)
        values = {o["value"] for o in opts}
        self.assertIn("subscription", values)
        self.assertIn("marketplace", values)


class TestTheLocationEntry(unittest.TestCase):
    def test_the_site_question_is_a_location_entry(self):
        q = _by_field(plan_questions({}, _cls()))["site"]
        self.assertEqual(q.get("input_kind"), "location")

    def test_the_site_copy_names_the_downgrade_not_a_threat(self):
        q = _by_field(plan_questions({}, _cls()))["site"]
        self.assertIn("corner", q["question"].lower())
        self.assertNotIn("withheld", q["question"].lower(),
                         "the consequence is the city-wide report now, not a withhold")


class TestNumberQuestions(unittest.TestCase):
    def test_prices_are_number_entries_with_a_unit(self):
        q = _by_field(plan_questions({}, _cls()))["avg_ticket"]
        self.assertEqual(q.get("input_kind"), "number")
        self.assertTrue(q.get("unit_hint"))

    def test_a_physical_venture_is_asked_expected_volume_with_a_period(self):
        plan = _by_field(plan_questions({}, _cls()))
        self.assertIn("expected_volume", plan)
        q = plan["expected_volume"]
        self.assertEqual(q.get("input_kind"), "number")
        self.assertEqual(set(q.get("period_choices") or []),
                         {"per day", "per week", "per month"})
        self.assertIn("founder", q["question"].lower(),
                      "the answer is printed as the founder's own estimate")

    def test_a_digital_subscription_is_not_asked_daily_sales(self):
        plan = _by_field(plan_questions({}, _cls(kind="subscription", physical=False)))
        self.assertNotIn("expected_volume", plan)


class TestTheOptionalTier(unittest.TestCase):
    def test_competitors_and_status_quo_are_optional_pluses(self):
        plan = _by_field(plan_questions({}, _cls()))
        self.assertTrue(plan["named_competitors"].get("optional"))
        self.assertTrue(plan["status_quo"].get("optional"))

    def test_the_number_deciders_are_not_optional(self):
        plan = _by_field(plan_questions({}, _cls()))
        for f in ("avg_ticket", "capacity", "monthly_cost_estimate"):
            self.assertFalse(plan[f].get("optional"), f)

    def test_evidence_is_reframed_as_feedback(self):
        q = _by_field(plan_questions({}, _cls()))["customer_evidence"]
        self.assertIn("feedback", q["question"].lower())


class TestTheTargetFollowUp(unittest.TestCase):
    def test_a_quantified_target_earns_the_how_question(self):
        ex = {"success_target": "about $200k in year one"}
        plan = _by_field(plan_questions(ex, _cls()))
        self.assertIn("target_basis", plan)
        self.assertIn("arrive", plan["target_basis"]["question"].lower())

    def test_no_target_no_follow_up(self):
        plan = _by_field(plan_questions({}, _cls()))
        self.assertNotIn("target_basis", plan)

    def test_the_survey_listens_it_does_not_argue(self):
        ex = {"success_target": "$5M year one"}
        q = _by_field(plan_questions(ex, _cls()))["target_basis"]
        low = q["question"].lower()
        for judgy in ("unrealistic", "too high", "unlikely"):
            self.assertNotIn(judgy, low)


class TestCopyRules(unittest.TestCase):
    def test_no_em_dashes_anywhere_in_the_question_set(self):
        for cls in (_cls(), _cls(kind="subscription", physical=False),
                    _cls(kind="marketplace", physical=False, needs_fork=True)):
            for q in plan_questions({"success_target": "$100k"}, cls):
                for key in ("question", "drives"):
                    self.assertNotIn("—", q.get(key) or "",
                                     f"{q['field']}.{key} carries an em dash")


class TestTheLocateEcho(unittest.TestCase):
    def test_the_endpoint_echoes_the_resolved_level(self):
        """POST /intake/{sid}/locate: the live 'that resolves to X (street level)' echo
        behind the location entry. Geocoder patched; no network in tests."""
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        import api as api_mod
        import intake

        s = {"id": intake.start_session()["session_id"]}
        payload = {"lat": 34.05, "lng": -118.24, "matched_address": "Los Angeles, CA",
                   "state_fips": "06", "county_fips": "037", "level": "city"}

        class _Ev:
            def __init__(self, p):
                self.payload, self.error, self.skeleton = p, None, False

        class _T:
            def __init__(self, p):
                self.fn = lambda address: _Ev(p)

        with patch("tools.get_tool", return_value=_T(payload)):
            client = TestClient(api_mod.app)
            r = client.post(f"/intake/{s['id']}/locate", json={"q": "Los Angeles, CA"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["level"], "city")
        self.assertIn("Los Angeles", body["matched"])
        self.assertIn("city", body["echo"].lower())


if __name__ == "__main__":
    unittest.main()
