"""
Tests for size_regional — per-location rollout (composes size_hyperlocal).

size_hyperlocal is mocked so aggregation, scaling, ceiling, phasing, and the
validation gate are verified deterministically.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

# These patch `skills.sizing.hyperlocal.size_hyperlocal` — the module that DEFINES it — not
# `skills.sizing.regional.size_hyperlocal`, which was regional's own copy of the name. The
# copy is gone: a sizing skill now reaches another through the module object, so one patch
# at the definition intercepts every caller. See
# test_a_sizing_skill_has_one_patchable_seam.py for why (#87 wave 4, and a test that hung
# for 100s because it patched the definition while regional held a stale binding).

from tools import Evidence
from skills import get_skill, SKILL_REGISTRY
from skills.sizing.regional import size_regional


def _site(tam, sam, som):
    return Evidence(source="size_hyperlocal", category="skill_output", count=1,
                    payload={"tam_usd": tam, "sam_usd": sam, "som_usd": som,
                             "figures": [], "method": "trade_area_catchment"})


class TestRegistration(unittest.TestCase):
    def test_registered(self):
        self.assertIn("size_regional", SKILL_REGISTRY)
        self.assertEqual(get_skill("size_regional").produces, "market_sizing")


class TestExplicitAddresses(unittest.TestCase):
    def test_sums_sites(self):
        sites = iter([_site(100, 40, 10), _site(200, 80, 20)])
        with patch("skills.sizing.hyperlocal.size_hyperlocal", side_effect=lambda **k: next(sites)):
            e = size_regional(addresses=["a", "b"])
        self.assertEqual(e.payload["tam_usd"], 300)
        self.assertEqual(e.payload["som_usd"], 30)
        self.assertEqual(e.payload["n_locations"], 2)
        self.assertTrue(e.payload["validation"]["passed"])

    def test_failed_site_skipped(self):
        ok = _site(100, 40, 10)
        bad = Evidence(source="size_hyperlocal", category="skill_output", count=0,
                       skeleton=True, error="geocode failed")
        seq = iter([ok, bad])
        with patch("skills.sizing.hyperlocal.size_hyperlocal", side_effect=lambda **k: next(seq)):
            e = size_regional(addresses=["a", "b"])
        self.assertEqual(e.payload["n_locations"], 1)
        self.assertTrue(any("skipped" in n for n in e.payload["notes"]))


class TestRepresentativeMode(unittest.TestCase):
    def test_scales_by_planned_count(self):
        with patch("skills.sizing.hyperlocal.size_hyperlocal", return_value=_site(100, 40, 10)):
            e = size_regional(representative_address="hq", planned_locations=5)
        self.assertEqual(e.payload["tam_usd"], 500)
        self.assertEqual(e.payload["som_usd"], 50)
        self.assertEqual(e.payload["n_locations"], 5)
        self.assertTrue(any("overlap" in n for n in e.payload["notes"]))

    def test_national_ceiling_caps_tam(self):
        with patch("skills.sizing.hyperlocal.size_hyperlocal", return_value=_site(100, 40, 10)):
            e = size_regional(representative_address="hq", planned_locations=100,
                              national_ceiling_usd=2000)
        self.assertEqual(e.payload["tam_usd"], 2000)  # capped from 10,000
        self.assertTrue(any("ceiling" in n for n in e.payload["notes"]))

    def test_phasing_schedule(self):
        with patch("skills.sizing.hyperlocal.size_hyperlocal", return_value=_site(100, 40, 10)):
            e = size_regional(representative_address="hq", planned_locations=3,
                              phasing=[0.33, 0.66, 1.0])
        sched = e.payload["phasing_schedule"]
        self.assertEqual(len(sched), 3)
        self.assertEqual(sched[-1]["value_usd"], e.payload["som_usd"])  # full at maturity


class TestGuards(unittest.TestCase):
    def test_no_input_returns_skeleton(self):
        e = size_regional()
        self.assertTrue(e.skeleton)
        self.assertIn("provide addresses", e.error)


if __name__ == "__main__":
    unittest.main()
