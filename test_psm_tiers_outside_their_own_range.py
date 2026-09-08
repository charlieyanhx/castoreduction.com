"""The PSM states an acceptable range, then recommends prices outside it, flat.

MEASURED on runs 12-15 — identical every time, because the same simulated panel produces
the same figures:

    acceptable range        $4.25 - $6.75      (PMC $4.25, expensive-but-OK median $6.75)
    too-cheap median        $3.00
    too-expensive median    $8.25   q3 $9.00
    tiers                   Value $3.85   Standard $5.50   Premium $9.50

Value sits BELOW the instrument's own floor, which is also its point of marginal
cheapness. Premium sits above the ceiling, above the too-expensive MEDIAN, and above its
q3 — so by the report's own simulated panel, appreciably more than half would call $9.50
too expensive. Both shipped carrying flat "PSM PRICING OUTPUT" citations, while the kill
criterion elsewhere in the same report treats the $4.25 floor as meaningful.

WHY NOT CLAMP. An out-of-range tier can be perfectly good strategy — a loss-leader to pull
commuter traffic, a halo SKU that sells rarely and anchors the menu. Silently dragging
$9.50 down to $6.75 would destroy a real recommendation and hide that the instrument
disagrees with it. What is not defensible is presenting it with no qualification at all, so
a reader cannot tell a deliberate halo SKU from a number the model drifted into.

So: annotate, never clamp. Each tier carries where it sits relative to the range, and the
strongest honest statement available — for Premium that is the panel rejection share, not
merely "above the ceiling".
"""
from __future__ import annotations

import unittest

_PSM = {
    "optimal_price_point": 5.5,
    "acceptable_range": [4.25, 6.75],
    "point_of_marginal_cheapness": 4.25,
    "too_cheap": {"median": 3.0, "q1": 2.5, "q3": 3.5},
    "bargain": {"median": 4.25, "q1": 3.75, "q3": 4.75},
    "expensive_but_ok": {"median": 6.75, "q1": 6.0, "q3": 7.25},
    "too_expensive": {"median": 8.25, "q1": 7.5, "q3": 9.0},
    "recommended_tiers": [
        {"name": "Value", "price": 3.85, "for_whom": "Daily commuters."},
        {"name": "Standard", "price": 5.5, "for_whom": "Core customers."},
        {"name": "Premium", "price": 9.5, "for_whom": "Enthusiasts."},
    ],
}


def _annotated(psm=None):
    from pricing import annotate_tiers_against_range

    import copy
    return annotate_tiers_against_range(copy.deepcopy(psm if psm is not None else _PSM))


class TestEachTierLearnsWhereItSits(unittest.TestCase):
    def test_the_in_range_tier_is_marked_within(self):
        tiers = _annotated()["recommended_tiers"]
        std = [t for t in tiers if t["name"] == "Standard"][0]
        self.assertEqual(std["range_status"], "within")

    def test_the_value_tier_is_marked_below_the_floor(self):
        tiers = _annotated()["recommended_tiers"]
        val = [t for t in tiers if t["name"] == "Value"][0]
        self.assertEqual(val["range_status"], "below_floor")
        self.assertIn("4.25", val["range_note"])

    def test_the_premium_tier_is_marked_above_the_ceiling(self):
        tiers = _annotated()["recommended_tiers"]
        prem = [t for t in tiers if t["name"] == "Premium"][0]
        self.assertEqual(prem["range_status"], "above_ceiling")
        self.assertIn("6.75", prem["range_note"])

    def test_the_premium_note_states_the_panel_rejection_not_just_the_ceiling(self):
        """$9.50 clears the too-expensive median AND its q3. "Above the acceptable
        ceiling" understates that; the panel figure is the fact a buyer needs."""
        prem = [t for t in _annotated()["recommended_tiers"]
                if t["name"] == "Premium"][0]
        self.assertIn("8.25", prem["range_note"])
        # The substantive claim, not a phrasing pin: "too-expensive" is hyphenated as a
        # compound adjective, and an assertion on the unhyphenated form was testing my
        # spelling rather than the finding.
        self.assertIn("reject", prem["range_note"].lower())

    def test_the_note_says_treat_it_as_a_halo_or_loss_leader_not_a_core_price(self):
        """A reader needs to know what to DO with an out-of-range tier."""
        notes = " ".join(t.get("range_note", "") for t in
                         _annotated()["recommended_tiers"])
        self.assertTrue(any(w in notes.lower()
                            for w in ("halo", "loss-leader", "low-volume")),
                        "the annotation flags the tier without saying how to read it")

    def test_an_in_range_tier_gets_no_note(self):
        std = [t for t in _annotated()["recommended_tiers"]
               if t["name"] == "Standard"][0]
        self.assertFalse(std.get("range_note"))


class TestItNeverClamps(unittest.TestCase):
    def test_the_prices_are_untouched(self):
        """Out-of-range tiers can be legitimate strategy; unexplained ones are not.
        Silently moving them would destroy a real recommendation."""
        prices = [t["price"] for t in _annotated()["recommended_tiers"]]
        self.assertEqual(prices, [3.85, 5.5, 9.5])

    def test_no_tier_is_dropped(self):
        self.assertEqual(len(_annotated()["recommended_tiers"]), 3)


class TestItDegradesQuietly(unittest.TestCase):
    def test_no_range_means_no_annotation_and_no_crash(self):
        psm = {"recommended_tiers": [{"name": "A", "price": 5.0}]}
        out = _annotated(psm)
        self.assertEqual(out["recommended_tiers"][0]["price"], 5.0)
        self.assertNotIn("range_status", out["recommended_tiers"][0])

    def test_a_malformed_range_is_ignored(self):
        psm = dict(_PSM, acceptable_range=["cheap", "dear"])
        self.assertNotIn("range_status", _annotated(psm)["recommended_tiers"][0])

    def test_an_inverted_range_is_ignored_rather_than_flagging_everything(self):
        psm = dict(_PSM, acceptable_range=[6.75, 4.25])
        self.assertNotIn("range_status", _annotated(psm)["recommended_tiers"][0])

    def test_a_non_numeric_tier_price_is_skipped(self):
        psm = dict(_PSM, recommended_tiers=[{"name": "A", "price": "ask us"}])
        self.assertNotIn("range_status", _annotated(psm)["recommended_tiers"][0])


class TestTheSimulationEmitsAnnotatedTiers(unittest.TestCase):
    """The annotation has to happen where tiers are PRODUCED — every consumer (the 4Ps
    price prompt, the benchmark table, the template) reads the same payload, so
    annotating at one call site would leave the others carrying bare numbers."""

    def test_simulate_van_westendorp_annotates_before_returning(self):
        import copy
        from unittest.mock import patch

        import pricing
        # deepcopy, not dict(): a shallow copy shares the tier dicts with the module-level
        # fixture, so annotating in place leaked into every later test and made the gate's
        # "unannotated" case silently arrive pre-annotated.
        with patch.object(pricing, "call_json", return_value=copy.deepcopy(_PSM)):
            out = pricing.simulate_van_westendorp(
                segment_summary="s", product_summary="p", top_features=[],
                unit="drink", recurring=False)
        statuses = {t["name"]: t.get("range_status")
                    for t in out.get("recommended_tiers") or []}
        self.assertEqual(statuses,
                         {"Value": "below_floor", "Standard": "within",
                          "Premium": "above_ceiling"})


class TestTheGateCatchesAnUnqualifiedTier(unittest.TestCase):
    def _report(self, annotated: bool):
        import copy
        psm = copy.deepcopy(_PSM)
        if annotated:
            from pricing import annotate_tiers_against_range
            psm = annotate_tiers_against_range(psm)
        return {"pricing": {"psm": psm}}

    def test_unannotated_out_of_range_tiers_fail(self):
        from gates import d58_psm_tiers_disclose_their_own_range as d58

        f = d58(self._report(annotated=False), None)
        self.assertIs(f.ok, False)
        self.assertIn("9.5", f.detail)

    def test_annotated_tiers_pass(self):
        from gates import d58_psm_tiers_disclose_their_own_range as d58

        self.assertIs(d58(self._report(annotated=True), None).ok, True)

    def test_a_report_with_no_psm_is_not_applicable_not_a_pass(self):
        """ok=None is 'could not be checked' — D55 counts that against coverage, and a
        silent True here would be the vacuous pass that gate exists to catch."""
        from gates import d58_psm_tiers_disclose_their_own_range as d58

        self.assertIsNone(d58({}, None).ok)

    def test_tiers_all_inside_the_range_pass(self):
        from gates import d58_psm_tiers_disclose_their_own_range as d58

        r = {"pricing": {"psm": dict(_PSM, recommended_tiers=[
            {"name": "Standard", "price": 5.5}])}}
        self.assertIs(d58(r, None).ok, True)


class TestTheQualificationReachesThePrompts(unittest.TestCase):
    """MEASURED on run15: the annotated tier blob is 1,228 characters against the price
    prompt's [:1000] slice — two of three notes were cut and the JSON left mid-structure.
    A guardrail delivered by truncation is not delivered, so it goes through the reminder
    registry instead: byte-stable, every section, attested in _reminders_fired."""

    def _annotated_psm(self):
        import copy

        from pricing import annotate_tiers_against_range
        return annotate_tiers_against_range(copy.deepcopy(_PSM))

    def test_the_rule_names_both_out_of_range_tiers(self):
        from four_ps import _r_tier_range

        text = _r_tier_range({"van_westendorp": self._annotated_psm()})
        self.assertIn("TIER RANGE — HARD RULE", text)
        self.assertIn("Value", text)
        self.assertIn("Premium", text)
        self.assertIn("below the acceptable floor", text)
        self.assertIn("above the acceptable ceiling", text)

    def test_the_in_range_tier_is_not_named(self):
        from four_ps import _r_tier_range

        self.assertNotIn("Standard", _r_tier_range(
            {"van_westendorp": self._annotated_psm()}))

    def test_all_tiers_in_range_emits_no_rule(self):
        """A directive the model cannot act on is noise in every prompt."""
        from four_ps import _r_tier_range

        self.assertEqual(_r_tier_range({"van_westendorp": {"recommended_tiers": [
            {"name": "Standard", "price": 5.5, "range_status": "within"}]}}), "")

    def test_it_survives_the_prompt_budget(self):
        """The whole reason this is a reminder and not the JSON blob."""
        from four_ps import _r_tier_range

        self.assertLess(len(_r_tier_range({"van_westendorp": self._annotated_psm()})),
                        700)

    def test_it_reaches_every_section_and_the_artifact_records_it(self):
        from unittest.mock import patch

        import four_ps as F
        captured = {}

        def fake(system, user, max_tokens, response_model=None):
            for n in ("Product", "Price", "Place", "Promotion"):
                if n in system:
                    captured[n.lower()] = user
                    break
            return {"narrative": "n.", "key_takeaways": ["t"], "citations": []}

        with patch.object(F, "call_json", side_effect=fake):
            out = F.assemble_4ps_split(
                profile={"name": "A", "summary": "s"}, competitors=[], top_audience={},
                max_diff={}, van_westendorp=self._annotated_psm(), place={},
                pricing_benchmark=None,
                economics={"unit": "drink", "price_per_unit": 5.5},
                reddit_signal={}, business_model_kind="transactional",
                competitor_density=30, active_signal_density=None,
                market_sizing={"som": {"mid": 650_000.0}})
        self.assertEqual(len(captured), 4, f"only {sorted(captured)} were prompted")
        for name, prompt in captured.items():
            self.assertIn("TIER RANGE — HARD RULE", prompt, name)
        self.assertTrue((out.get("_reminders_fired") or {}).get("tier_range"))


class TestTheReportShowsTheQualification(unittest.TestCase):
    def test_the_tier_card_renders_the_note(self):
        import copy

        from jinja2 import Environment, FileSystemLoader

        from pricing import annotate_tiers_against_range
        env = Environment(loader=FileSystemLoader("templates"), autoescape=True)
        src = env.loader.get_source(env, "report.html")[0]
        start = src.index('{% if psm.recommended_tiers %}')
        end = src.index('{% endif %}', src.index('{% endfor %}', start))
        tpl = env.from_string(src[start:end + len('{% endif %}')])
        html = tpl.render(psm=annotate_tiers_against_range(copy.deepcopy(_PSM)),
                          pricing_benchmark=None)
        self.assertIn("BELOW the panel&#39;s acceptable floor", html)
        self.assertIn("halo SKU", html)

    def test_an_in_range_tier_shows_no_warning_box(self):
        from jinja2 import Environment, FileSystemLoader

        env = Environment(loader=FileSystemLoader("templates"), autoescape=True)
        src = env.loader.get_source(env, "report.html")[0]
        start = src.index('{% if psm.recommended_tiers %}')
        end = src.index('{% endif %}', src.index('{% endfor %}', start))
        tpl = env.from_string(src[start:end + len('{% endif %}')])
        html = tpl.render(psm={"recommended_tiers": [
            {"name": "Standard", "price": 5.5, "range_status": "within"}]},
            pricing_benchmark=None)
        self.assertNotIn("#fffbeb", html)


if __name__ == "__main__":
    unittest.main()
