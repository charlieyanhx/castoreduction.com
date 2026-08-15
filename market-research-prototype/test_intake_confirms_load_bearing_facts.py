"""Before six minutes of research, confirm the handful of answers that decide the result.

MEASURED on the shipped intake, driving it as a real user (both the API and the browser):
it declared "Got what I need" after ONE user reply, having collected product, target
customer, business model and a geography of "San Francisco, CA, US" — with Pricing and
Differentiation never asked. Two of those gaps are not cosmetic.

NO PRICE. Measured against the description the intake actually produced:
    extract_stated_price(...)        -> None
    _r_volume_ladder(no price)       -> ''      (no ladder at all)
    planning_target_units_per_day()  -> None
So break-even, the planning target and the obtainable ceiling all vanish, D61 goes
not-applicable, and the 4Ps sections are back to inventing their own volumes — the exact
defect #97 closed.

A CITY IS NOT A TRADE AREA, and this one is silent:
    "San Francisco, California"                  -> lat 37.7879, tract 011700
    "Mission District of San Francisco, ..."     -> lat 37.7675, tract 017700
Two tracts, ~2.3 km apart, against a trade-area ring of 1.5 km — so the catchments barely
overlap. Households, the income index and the entire competitor census would be computed for
a neighbourhood the operator is not opening in, and nothing in the report would say so. Same
class as #59 (trade area = whole county, ~100x TAM) and the run16 location bug that returned
0 competitors.

WHY CONFIRMATION RATHER THAN JUST MORE REQUIRED FIELDS. A required field can still be filled
with the wrong thing — measured, `pricing` once came back "Pay per drink", a monetization
model where the pipeline needs a figure. The operator is the only one who knows whether
"San Francisco" meant the Mission or the Sunset. So the run stops once, shows the few answers
that move the numbers, says WHAT EACH ONE DRIVES, and asks. That is also the cheapest moment
to be wrong: correcting a location here costs a sentence, correcting it after costs a report.

Deliberately NOT everything. Confirming eight fields trains people to click through. Only
those whose value changes a published number get a card.
"""
from __future__ import annotations

import unittest


def _items(extracted):
    from intake import confirmation_items
    return {i["field"]: i for i in confirmation_items(extracted)}


_FULL = {"product": "Specialty coffee shop, espresso and pour-over",
         "target_customer": "Local residents and remote workers",
         "business_model": "Brick-and-mortar retail, pay per drink",
         "geography": "Mission District, San Francisco, CA",
         "pricing": "$5.50 per drink",
         "differentiation": "Single-origin, in-house roast",
         "stage": "idea",
         "key_features": ["espresso", "pour-over"]}


class TestOnlyResultDrivingFactsAreConfirmed(unittest.TestCase):
    def test_location_and_price_are_confirmed(self):
        items = _items(_FULL)
        self.assertIn("geography", items)
        self.assertIn("pricing", items)

    def test_decorative_fields_are_not(self):
        """Stage and key features change no published number. Confirming them would train
        the operator to click through the two that matter."""
        items = _items(_FULL)
        self.assertNotIn("stage", items)
        self.assertNotIn("key_features", items)

    def test_each_item_says_what_it_drives(self):
        """'Confirm your location' is a chore. 'This sets the 1.5 km trade area we count
        competitors and households in' is a reason to read it."""
        for field, item in _items(_FULL).items():
            self.assertTrue(item.get("drives"), f"{field} does not say what it affects")
            self.assertGreater(len(item["drives"]), 20, field)

    def test_the_location_item_names_the_trade_area(self):
        self.assertIn("trade area", _items(_FULL)["geography"]["drives"].lower())

    def test_the_price_item_names_break_even(self):
        self.assertIn("break-even", _items(_FULL)["pricing"]["drives"].lower())


class TestACityIsFlaggedAsTooCoarse(unittest.TestCase):
    """The silent failure: a geography that satisfies the field and misses the site."""

    def _geo(self, value, model="Brick-and-mortar retail"):
        return _items(dict(_FULL, geography=value, business_model=model))["geography"]

    def test_a_bare_city_is_not_precise_enough(self):
        for coarse in ("San Francisco", "San Francisco, CA", "San Francisco, California, US",
                       "California", "US"):
            self.assertFalse(self._geo(coarse)["precise"],
                             f"{coarse!r} accepted as a trade-area location")

    def test_a_neighbourhood_is(self):
        for fine in ("Mission District, San Francisco, CA",
                     "2101 Mission St, San Francisco, CA",
                     "corner of 24th and Valencia, San Francisco"):
            self.assertTrue(self._geo(fine)["precise"], f"{fine!r} rejected")

    def test_the_warning_says_how_far_off_it_could_be(self):
        w = (self._geo("San Francisco") or {}).get("warning") or ""
        self.assertIn("1.5", w, "the warning does not state the ring it has to resolve to")

    def test_a_non_physical_venture_is_not_asked_for_a_street_corner(self):
        """A national SaaS is sized top-down; demanding a neighbourhood would be a question
        with no consequence, and those are what teach people to skip the card."""
        item = self._geo("United States", model="SaaS subscription, sold nationally")
        self.assertTrue(item["precise"])


class TestAPriceMustBeAFigure(unittest.TestCase):
    def test_a_monetization_model_is_not_a_price(self):
        """MEASURED: the intake extracted 'Pay per drink' into the pricing field. It fills
        the slot and carries no number, so every downstream volume figure still vanishes."""
        item = _items(dict(_FULL, pricing="Pay per drink"))["pricing"]
        self.assertFalse(item["precise"])

    def test_a_figure_passes(self):
        for good in ("$5.50 per drink", "5.50", "about $5.50 a cup", "$12/month"):
            self.assertTrue(_items(dict(_FULL, pricing=good))["pricing"]["precise"], good)

    def test_a_missing_price_is_shown_as_missing_not_omitted(self):
        """Dropping the row would hide the gap; the operator must see that nothing was
        captured and that it costs them the break-even line."""
        item = _items(dict(_FULL, pricing=None))["pricing"]
        self.assertFalse(item["precise"])
        self.assertIn(item.get("value"), (None, ""))


class TestReadyIsNotTheSameAsConfirmed(unittest.TestCase):
    def test_a_session_is_not_confirmed_until_it_is_told_so(self):
        import intake
        s = {"extracted": dict(_FULL), "confirmed": False}
        self.assertFalse(intake.is_confirmed(s))

    def test_confirming_records_what_was_shown(self):
        """If an operator confirms a location and the report later disagrees with it, the
        artifact has to be able to say which one they were shown."""
        import intake
        s = {"extracted": dict(_FULL)}
        intake.mark_confirmed(s)
        self.assertTrue(intake.is_confirmed(s))
        self.assertTrue(s.get("confirmed_facts"))
        self.assertEqual(s["confirmed_facts"].get("geography"),
                         "Mission District, San Francisco, CA")


if __name__ == "__main__":
    unittest.main()
