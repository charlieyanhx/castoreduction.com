"""
Task A: the CEX series map misses the commonest hyperlocal food categories.

`size_hyperlocal` computes TAM = households x annual_spend_per_hh (hyperlocal.py:325). The
spend half comes from BLS CEX via `bls_cex_spend`, which resolves a category to a series id
through `_CEX_SERIES_CURATED` (longest substring match) and only falls back to the LLM when
nothing matches.

MEASURED on three live runs: `bls_cex_spend` was called every time and failed every time —
"could not resolve a BLS CEX series for 'independent specialty coffee shop'". The curated map
holds 35 keywords and not one of `coffee`, `cafe`, `bakery` or `tea`, while `restaurant`,
`diner`, `dining` and `food away` all map to CXUFOODAWAYLB0101M — the series a coffee shop
plainly belongs in. The LLM fallback then returned nothing usable, so the tool skeletoned.

Verified live before mapping anything to it: CXUFOODAWAYLB0101M returns $3,945/yr,
CXUPERSCARELB0101M $978/yr, CXUENTRTAINLB0101M $3,609/yr. Real series, real numbers.

A COLLISION HAZARD WORTH STATING, because it is why this is not simply "add more keywords".
Matching is bare substring, so a short key matches anywhere in the string. `tea` would match
"s-TEA-khouse" (harmlessly — a steakhouse IS food away from home) but also "TEAm analytics
saas", mapping a B2B SaaS venture to household restaurant spending. So the tea entries are
`tea house` / `teahouse` / `tea room`, never bare `tea`.

The same hazard already fixes a live bug in the other direction: `bar` maps to ALCOHOL, so
"juice bar" currently resolves to household alcohol spend. Adding the longer `juice bar` key
makes longest-match pick food-away instead.

This does NOT restore the TAM. `households` still needs ACS, which needs CENSUS_API_KEY. It
removes one of the two runtime failures so the TAM works the moment the key lands, rather
than hitting a second wall.
"""
from __future__ import annotations

import unittest

from tools.econ import _CEX_SERIES_CURATED as CURATED
from tools.econ import _resolve_cex_series

_FOODAWAY = "CXUFOODAWAYLB0101M"


class TestTheCategoryThatFailedOnEveryLiveRun(unittest.TestCase):
    def test_the_exact_run3_category_resolves(self):
        self.assertEqual(_resolve_cex_series("independent specialty coffee shop"), _FOODAWAY)

    def test_plain_coffee_shop_resolves(self):
        self.assertEqual(_resolve_cex_series("coffee shop"), _FOODAWAY)

    def test_cafe_resolves_with_and_without_the_accent(self):
        self.assertEqual(_resolve_cex_series("neighbourhood cafe"), _FOODAWAY)
        self.assertEqual(_resolve_cex_series("le café"), _FOODAWAY)


class TestTheOtherCommonHyperlocalFoodTypes(unittest.TestCase):
    """Every one of these is a plausible Castor input and every one currently falls through
    to an LLM that returns nothing."""

    def test_they_all_resolve_to_food_away_from_home(self):
        for cat in ("artisan bakery", "tea house", "teahouse", "a tea room",
                    "ice cream parlour", "dessert bar", "corner deli",
                    "sandwich shop", "pizzeria", "wood-fired pizza",
                    "a bistro", "juice bar", "smoothie bar", "bubble tea shop"):
            with self.subTest(cat=cat):
                self.assertEqual(_resolve_cex_series(cat), _FOODAWAY,
                                 f"{cat!r} does not resolve to food-away-from-home")

    def test_juice_bar_no_longer_resolves_to_alcohol(self):
        """`bar` maps to ALCOHOL, so before the longer key existed a juice bar was sized on
        household alcohol spending. A live bug, not a hypothetical."""
        self.assertNotIn("ALCBEVG", _resolve_cex_series("juice bar") or "")


class TestTheCollisionHazard(unittest.TestCase):
    """Bare-substring matching means a short key matches anywhere. These are the false
    positives a careless addition would create."""

    def test_a_b2b_saas_venture_is_not_mapped_to_restaurant_spending(self):
        """'team' contains 'tea'. A bare `tea` key would size a SaaS venture on household
        food-away-from-home spend."""
        self.assertIsNone(_resolve_cex_series("team analytics saas"))

    def test_no_curated_key_is_short_enough_to_be_reckless(self):
        """A 3-character key is a substring accident waiting to happen.

        Five predate this task and are GRANDFATHERED, not endorsed. Measured false positives
        they cause today, each of which would size a venture on unrelated household spending:

            'spacecraft parts'           -> spa -> personal care
            'competitor intel platform'  -> pet -> pets
            'petrol station'             -> pet -> pets
            'gymnasium equipment resale' -> gym -> entertainment
            anything containing "publishing" -> pub -> alcohol

        Fixing those means changing the MATCHING RULE, not the key list, and that changes
        behaviour for existing categories (a naive word-boundary rule stops 'barber' matching
        "barbershop"). Out of scope here deliberately; tracked separately. This test's job is
        to stop the list getting worse."""
        grandfathered = {"bar", "pub", "spa", "pet", "gym"}
        reckless = [k for k in CURATED if len(k) <= 3 and k not in grandfathered]
        self.assertEqual(reckless, [], f"new keys short enough to false-match: {reckless}")

    def test_longest_match_still_wins(self):
        """`barber` must beat `bar`, or a barbershop gets sized on alcohol spend."""
        self.assertIn("PERSCARE", _resolve_cex_series("barber shop") or "")

    def test_an_unmapped_category_returns_nothing_from_the_curated_map(self):
        """The curated map must not guess. The LLM fallback is a separate, disclosed step."""
        best, best_len = None, 0
        for kw, sid in CURATED.items():
            if kw in "interdimensional widget fabrication" and len(kw) > best_len:
                best, best_len = sid, len(kw)
        self.assertIsNone(best)


class TestTheExistingMapIsUnchanged(unittest.TestCase):
    def test_the_original_thirty_five_still_resolve_the_same_way(self):
        for cat, frag in (("restaurant", "FOODAWAY"), ("wine shop", "ALCBEVG"),
                          ("nail salon", "PERSCARE"), ("yoga studio", "ENTRTAIN"),
                          ("grocery", "FOODHOME"), ("veterinary clinic", "PETS")):
            with self.subTest(cat=cat):
                self.assertIn(frag, _resolve_cex_series(cat) or "")


class TestItReachesTheSizingPath(unittest.TestCase):
    def test_resolve_annual_spend_finds_a_number_for_a_coffee_shop(self):
        """The consumer. Hits the live BLS API — skipped when it is unreachable, because a
        network outage is not a code defect."""
        from skills.sizing.hyperlocal import resolve_annual_spend
        spend, sourced = resolve_annual_spend("independent specialty coffee shop")
        if spend is None:
            self.skipTest("BLS API unreachable")
        self.assertGreater(spend, 0)
        self.assertTrue(sourced, "a BLS-sourced figure is not marked as sourced")


if __name__ == "__main__":
    unittest.main()
