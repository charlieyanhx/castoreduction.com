"""The approved survey is a fixed optional paragraph form; legacy consumers still work."""
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


class TestTheApprovedParagraphSurvey(unittest.TestCase):
    def test_every_follow_up_is_optional_and_accepts_a_paragraph(self):
        for q in plan_questions({}, _cls(needs_fork=True)):
            with self.subTest(field=q["field"]):
                self.assertEqual(q["input_kind"], "para")
                self.assertTrue(q["optional"])
                self.assertNotIn("options", q)
                self.assertNotIn("period_choices", q)

    def test_the_two_groups_have_seven_questions_each(self):
        plan = plan_questions({}, _cls())
        self.assertEqual([q["group"] for q in plan], ["core"] * 7 + ["specific"] * 7)
        self.assertEqual(plan[0]["field"], "status_quo")
        self.assertEqual(plan[7]["field"], "geography")
        self.assertEqual(plan[-1]["field"], "constraints")

    def test_capacity_keeps_the_founders_choice_of_interval(self):
        q = _by_field(plan_questions({}, _cls()))["service_capacity"]
        self.assertIn("any interval", q["question"])
        self.assertNotIn("capacity", _by_field(plan_questions({}, _cls())))

    def test_unit_economics_asks_for_both_cost_and_customer_price(self):
        q = _by_field(plan_questions({}, _cls()))["unit_economics"]
        self.assertIn("cost to make per item", q["question"])
        self.assertIn("cost the consumer", q["question"])

    def test_quantified_targets_do_not_add_an_unrequested_followup(self):
        plan = plan_questions({"success_target": "$5M year one"}, _cls())
        self.assertEqual(len(plan), 14)
        self.assertNotIn("target_basis", _by_field(plan))

    def test_revenue_question_preserves_the_approved_punctuation(self):
        q = _by_field(plan_questions({}, _cls()))["business_model"]
        self.assertIn("make money — pay per item", q["question"])
        self.assertIn("multiple products or revenue streams", q["question"])


class TestTheLocateEcho(unittest.TestCase):
    def test_the_endpoint_echoes_the_resolved_level(self):
        """POST /intake/{sid}/locate: the live 'that resolves to X (street level)' echo
        behind the location entry. Geocoder patched; no network in tests."""
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        import api as api_mod
        import intake

        # ONE CLIENT, seeding and driving. Intake routes are owner-scoped now, and every
        # TestClient is its own guest, so a session started out-of-band (or under a
        # different client's identity) answers 404 to the one making the request.
        client = TestClient(api_mod.app)
        s = {"id": intake.start_session(
            owner_id=client.get("/auth/me").json()["owner"])["session_id"]}
        payload = {"lat": 34.05, "lng": -118.24, "matched_address": "Los Angeles, CA",
                   "state_fips": "06", "county_fips": "037", "level": "city"}

        class _Ev:
            def __init__(self, p):
                self.payload, self.error, self.skeleton = p, None, False

        class _T:
            def __init__(self, p):
                self.fn = lambda address: _Ev(p)

        with patch("tools.get_tool", return_value=_T(payload)):
            r = client.post(f"/intake/{s['id']}/locate", json={"q": "Los Angeles, CA"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["level"], "city")
        self.assertIn("Los Angeles", body["matched"])
        self.assertIn("city", body["echo"].lower())


class TestFounderVolumeBesideTheModel(unittest.TestCase):
    """Wave D part 2: the founder's expected volume becomes the disclosed alternative
    D59 demands, printed beside the model's SOM and never blended into it. This is the
    end-to-end fix for the run that started this redesign (b98df066: D59 blocked on an
    unsourced anchor with no alternative shown)."""

    def _ms(self, anchor=None):
        return {"method": "trade_area_catchment", "som": {"mid": 400_000},
                "som_anchor": anchor}

    def _result(self, facts, category="taco stand"):
        return {"intake": {"facts": facts, "unknowns": [], "confirmed": True},
                "profile": {"category": category}}

    def test_volume_and_price_become_the_anchor_alternative(self):
        from plan import _apply_founder_volume
        ms = self._ms(anchor={"method": "single_unit_revenue_estimate",
                              "sourced": False})
        _apply_founder_volume(ms, self._result(
            {"expected_volume": "40 per day", "avg_ticket": "$6.50"}))
        fe = ms.get("founder_estimate") or {}
        self.assertEqual(fe.get("annual_usd"), round(40 * 360 * 6.5))
        self.assertEqual(ms["som_anchor"]["alternative_usd"], fe["annual_usd"])
        self.assertEqual(ms["som_anchor"]["alternative_source"],
                         "founder_expected_volume")
        self.assertIn("founder", (fe.get("note") or "").lower())

    def test_an_existing_alternative_is_not_overwritten(self):
        from plan import _apply_founder_volume
        ms = self._ms(anchor={"method": "single_unit_revenue_estimate",
                              "sourced": False, "alternative_usd": 123_456})
        _apply_founder_volume(ms, self._result(
            {"expected_volume": "40 per day", "avg_ticket": "$6.50"}))
        self.assertEqual(ms["som_anchor"]["alternative_usd"], 123_456)
        self.assertNotIn("alternative_source", ms["som_anchor"])

    def test_volume_without_a_price_still_ships_the_estimate_text(self):
        from plan import _apply_founder_volume
        ms = self._ms(anchor={"method": "single_unit_revenue_estimate",
                              "sourced": False})
        _apply_founder_volume(ms, self._result({"expected_volume": "40 per day"}))
        fe = ms.get("founder_estimate") or {}
        self.assertEqual(fe.get("volume_text"), "40 per day")
        self.assertIsNone(fe.get("annual_usd"))
        self.assertNotIn("alternative_usd", ms["som_anchor"])

    def test_no_intake_record_is_a_no_op(self):
        from plan import _apply_founder_volume
        ms = self._ms()
        _apply_founder_volume(ms, {})
        self.assertNotIn("founder_estimate", ms)

    def test_weekly_and_monthly_periods_annualize(self):
        from plan import _apply_founder_volume
        for text, factor in (("100 per week", 52), ("300 per month", 12)):
            ms = self._ms(anchor={"method": "x", "sourced": False})
            _apply_founder_volume(ms, self._result(
                {"expected_volume": text, "pricing": "$10"}))
            self.assertEqual(ms["founder_estimate"]["annual_usd"],
                             round(float(text.split()[0]) * factor * 10), text)

    def test_the_fermi_check_uses_meal_window_assumptions(self):
        """Operator spec (2026-08-20, refined): 'it is a meal — have more rigorous
        assumptions.' Quick-service meal model: 3 turns/hour across 6 effective
        meal-window hours, every assumption printed so the reader can re-run the
        arithmetic. Stated as tension, never a correction."""
        from plan import _apply_founder_volume
        ms = self._ms(anchor={"method": "x", "sourced": False})
        _apply_founder_volume(ms, self._result(
            {"expected_volume": "1000 per day", "avg_ticket": "$6",
             "capacity": "15 seats"}))
        p = ms["founder_estimate"]["plausibility"]
        self.assertIn("270", p)                       # 15 seats x 3/hr x 6h
        self.assertIn("meal-window", p)
        self.assertIn("3.7x", p)
        self.assertIn("takeout", p.lower())
        for judgy in ("wrong", "unrealistic", "impossible"):
            self.assertNotIn(judgy, p.lower())

    def test_the_fermi_model_is_general_not_a_taco_special_case(self):
        """Operator rule (2026-08-20): 'make sure all these fixes are general.' The
        mechanism is stations x turns x hours everywhere; only parameters and nouns
        follow the trade. A salon never hears about lunch peaks; an unknown category
        gets the neutral service model."""
        from plan import _apply_founder_volume
        ms = self._ms(anchor={"method": "x", "sourced": False})
        _apply_founder_volume(ms, self._result(
            {"expected_volume": "100 per day", "avg_ticket": "$60",
             "capacity": "8 stations"}, category="hair salon"))
        p = ms["founder_estimate"]["plausibility"]
        self.assertIn("appointments", p)
        self.assertNotIn("meal", p.lower())
        ms2 = self._ms(anchor={"method": "x", "sourced": False})
        _apply_founder_volume(ms2, self._result(
            {"expected_volume": "500 per day", "avg_ticket": "$10",
             "capacity": "4 kiosks"}, category="parcel pickup point"))
        p2 = ms2["founder_estimate"]["plausibility"]
        self.assertIn("customers", p2)
        self.assertIn("capacity ceiling", p2)
        self.assertNotIn("meal", p2.lower())

    def test_a_volume_within_the_ceiling_reads_as_a_fit(self):
        from plan import _apply_founder_volume
        ms = self._ms(anchor={"method": "x", "sourced": False})
        _apply_founder_volume(ms, self._result(
            {"expected_volume": "150 per day", "avg_ticket": "$6",
             "capacity": "10 seats"}))
        self.assertIn("fits", ms["founder_estimate"]["plausibility"])

    def test_no_capacity_means_no_fermi_claim(self):
        from plan import _apply_founder_volume
        ms = self._ms(anchor={"method": "x", "sourced": False})
        _apply_founder_volume(ms, self._result(
            {"expected_volume": "1000 per day", "avg_ticket": "$6"}))
        self.assertIsNone(ms["founder_estimate"]["plausibility"])


if __name__ == "__main__":
    unittest.main()
