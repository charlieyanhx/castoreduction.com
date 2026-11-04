"""The confirmation card confirms what the pipeline DECIDED, with where each decision came from.

The old card confirmed two raw fields (location, price). It never showed the decisions those
fields feed — and the decisions are what shipped wrong: the orbital brief said "Undetermined",
the classifier silently picked subscription, and a seat-priced report followed. Nobody was ever
shown "I will analyse this as a subscription" with a chance to say no.

Every line now carries provenance the founder can act on:
    stated    they said it; shown for correction
    inferred  the classifier derived it; shown WITH the inference chain, because an
              inference the founder never sees is a silent pick
    assumed   they said "not sure"; the report will label everything built on it
"""
from __future__ import annotations

import unittest

from intake import confirmation_payload
from intake_tree import mark_unknown


def _session(**extracted):
    base = {f: None for f in ("product", "target_customer", "business_model", "geography",
                              "pricing", "stage")}
    base.update(extracted)
    return {"extracted": base, "confirmed": False}


# Faithful to the measured case: the real orbital brief stated NO price — the report's own
# Pricing Detail says "No stated price found in the brief"; the $1,450/mo was the PSM's
# output, not the founder's words. A first draft of this fixture included it, and the kind
# correctly read as STATED — "$1,450 per month" genuinely declares recurring billing. The
# fixture was wrong, not the classifier.
ORBITAL = dict(product="A satellite-based orbital mirror system for solar farms.",
               target_customer="Utility-scale solar farms",
               business_model="Undetermined / early exploratory",
               geography="US")

CAFE = dict(product="A specialty coffee shop.", target_customer="Commuters",
            business_model="people buy drinks at the counter",
            geography="Portland, OR", avg_ticket="$6.50",
            site="NW 23rd and Lovejoy", named_competitors="Stumptown")


class TestTheDecisionLines(unittest.TestCase):
    def test_the_kind_decision_is_on_the_card(self):
        items = confirmation_payload(_session(**ORBITAL))["items"]
        kinds = [i for i in items if i["field"] == "kind"]
        self.assertTrue(kinds, "the card never shows what financial model will be used — "
                               "the decision that shipped wrong on job d62bc04f")

    def test_an_undetermined_model_reads_inferred_not_stated(self):
        items = confirmation_payload(_session(**ORBITAL))["items"]
        kind = next(i for i in items if i["field"] == "kind")
        self.assertEqual(kind["provenance"], "inferred",
                         "the classifier's silent pick is presented as if the founder said it")

    def test_a_stated_model_reads_stated(self):
        items = confirmation_payload(_session(**CAFE))["items"]
        kind = next(i for i in items if i["field"] == "kind")
        self.assertEqual(kind["provenance"], "stated")

    def test_the_kind_line_speaks_founder(self):
        items = confirmation_payload(_session(**ORBITAL))["items"]
        kind = next(i for i in items if i["field"] == "kind")
        text = (kind.get("value") or "") + " " + (kind.get("drives") or "")
        for jargon in ("subscription", "transactional", "b2b", "saas"):
            self.assertNotIn(jargon, text.lower(),
                             f"the card says {jargon!r} — our taxonomy, not their words")

    def test_a_not_sure_field_reads_assumed(self):
        s = _session(**CAFE)
        mark_unknown(s["extracted"], "rent_estimate")
        items = confirmation_payload(s)["items"]
        assumed = [i for i in items if i.get("provenance") == "assumed"]
        self.assertTrue(assumed, "a 'not sure' answer vanished from the card")

    def test_competitors_present_on_the_card(self):
        items = confirmation_payload(_session(**CAFE))["items"]
        self.assertTrue(any(i["field"] == "named_competitors" for i in items),
                        "the card never offers the one seed that anchors discovery")

    def test_no_competitors_is_an_explicit_empty_line_not_absence(self):
        items = confirmation_payload(_session(**ORBITAL))["items"]
        comp = next((i for i in items if i["field"] == "named_competitors"), None)
        self.assertIsNotNone(comp)
        self.assertIsNone(comp.get("value"))

    def test_the_original_precision_warnings_survive(self):
        """The old card's best property — 'San Francisco is a list of locations' — must
        not be lost in the upgrade."""
        s = _session(product="A coffee shop.", target_customer="locals",
                     business_model="walk-in cafe sales", geography="San Francisco",
                     pricing="pay per drink")
        items = confirmation_payload(s)["items"]
        geo = next(i for i in items if i["field"] in ("geography", "site"))
        self.assertFalse(geo.get("precise", True))
        self.assertTrue(geo.get("warning"))


if __name__ == "__main__":
    unittest.main()
