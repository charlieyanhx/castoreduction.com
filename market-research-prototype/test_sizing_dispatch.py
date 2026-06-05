"""
Tests for size_market — the unified dispatcher (classify → route → validated).

classify_market_scale and the three sizing skills are mocked so routing,
input-guarding, and decision-annotation are verified deterministically.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools import Evidence
from skills import get_skill, SKILL_REGISTRY
from skills.sizing.dispatch import size_market


def _classified(scale, sizing_skill):
    return Evidence(source="classify_market_scale", category="skill_output", count=1,
                    payload={"scale": scale, "sizing_skill": sizing_skill,
                             "signals": {"is_physical": True, "geo_scope": "single_site",
                                         "delivery": "in_person"},
                             "rationale": "test"})


def _sized(tag):
    return Evidence(source=tag, category="skill_output", count=1,
                   payload={"tam_usd": 100, "sam_usd": 40, "som_usd": 10, "method": tag},
                   cost_meta={"tam_usd": 100})


class TestRegistration(unittest.TestCase):
    def test_registered(self):
        self.assertIn("size_market", SKILL_REGISTRY)
        self.assertEqual(get_skill("size_market").produces, "market_sizing")


class TestRouting(unittest.TestCase):
    def test_hyperlocal_route(self):
        with patch("skills.sizing.dispatch.classify_market_scale",
                   return_value=_classified("hyperlocal", "size_hyperlocal")), \
             patch("skills.sizing.dispatch.size_hyperlocal", return_value=_sized("trade_area_catchment")) as h:
            e = size_market("a restaurant", address="123 Main St, LA")
        h.assert_called_once()
        self.assertEqual(e.payload["scale_decision"]["scale"], "hyperlocal")
        self.assertEqual(e.cost_meta["sizing_skill"], "size_hyperlocal")
        self.assertEqual(e.payload["tam_usd"], 100)

    def test_regional_route_with_addresses(self):
        with patch("skills.sizing.dispatch.classify_market_scale",
                   return_value=_classified("regional", "size_regional")), \
             patch("skills.sizing.dispatch.size_regional", return_value=_sized("per_location_rollout")) as r:
            e = size_market("a chain", addresses=["a", "b"])
        r.assert_called_once()
        self.assertEqual(e.payload["scale_decision"]["sizing_skill"], "size_regional")

    def test_digital_route(self):
        with patch("skills.sizing.dispatch.classify_market_scale",
                   return_value=_classified("global_digital", "size_national_digital")), \
             patch("skills.sizing.dispatch.size_national_digital",
                   return_value=_sized("topdown_bottomup_digital")) as d:
            e = size_market("a global SaaS", profile={"name": "Acme"})
        d.assert_called_once()
        self.assertEqual(e.cost_meta["scale"], "global_digital")


class TestInputGuards(unittest.TestCase):
    def test_hyperlocal_missing_address(self):
        with patch("skills.sizing.dispatch.classify_market_scale",
                   return_value=_classified("hyperlocal", "size_hyperlocal")):
            e = size_market("a restaurant")  # no address
        self.assertTrue(e.skeleton)
        self.assertIn("address", e.error)
        self.assertEqual(e.payload["missing_input"], "address")

    def test_regional_missing_addresses(self):
        with patch("skills.sizing.dispatch.classify_market_scale",
                   return_value=_classified("regional", "size_regional")):
            e = size_market("a chain")
        self.assertTrue(e.skeleton)
        self.assertIn("addresses", e.error)

    def test_digital_defaults_profile_from_description(self):
        with patch("skills.sizing.dispatch.classify_market_scale",
                   return_value=_classified("national_digital", "size_national_digital")), \
             patch("skills.sizing.dispatch.size_national_digital",
                   return_value=_sized("topdown_bottomup_digital")) as d:
            size_market("an online tool")  # no profile passed
        # profile defaulted to {"description": ...}
        self.assertIn("description", d.call_args.kwargs["profile"])


if __name__ == "__main__":
    unittest.main()
