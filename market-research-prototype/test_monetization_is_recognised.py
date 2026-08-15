"""The monetization model decides the whole economic model, and it is guessed from prose.

MEASURED before this file existed, across 35 natural phrasings of the seven supported
models, using profiles shaped the way extract_company_profile emits them:

    ad_supported  1/7   marketplace 2/6   services 4/6   ecommerce 2/4
    hybrid        1/3   subscription 5/5  transactional 4/4        OVERALL 19/35 = 54%

Every single miss resolved to `subscription`, because that is the documented default
("preserves original behavior so nothing regresses"). The failure mode is therefore not
"unclassified" — it is CONFIDENTLY WRONG:

    "we take 12% of each transaction"            -> subscription   want marketplace
    "monetized with display advertising"         -> subscription   want ad_supported
    "we bill hourly for design work"             -> subscription   want services
    "we sell physical goods online"              -> subscription   want ecommerce
    "hardware sale with recurring software fee"  -> ecommerce      want hybrid

WHY THAT MATTERS MORE THAN A LABEL. The kind picks the economics: is_per_unit routes to
retail_unit_economics rather than subscription CLV:CAC, unit_for_model names the unit
("booking" vs "account" vs "drink"), and financials picks the ramp. A marketplace modelled
as SaaS gets churn and lifetime value where it should get take-rate on GMV — and the report
will be internally consistent and entirely wrong, which is the hardest kind to catch.

WHY IT WAS BRITTLE. Matching was literal substrings from hand-written lists, so
"advertising-supported" hit and "advertising supported" missed; "monetized via ads" hit and
"monetized with display advertising" missed. The classifier recognised the phrasings someone
had thought of, and a founder describing their own business does not consult that list.

THE PHRASINGS BELOW ARE THE SPEC. They are ordinary ways a founder describes their own
monetization, not adversarial strings. A classifier that cannot read them cannot serve a
diverse customer base, which is the whole point of supporting seven models rather than one.
"""
from __future__ import annotations

import unittest

_DIGITAL = {"scale": "national_digital", "signals": {"is_physical": False}}
_PHYSICAL = {"scale": "hyperlocal", "signals": {"is_physical": True}}

#: How founders actually write it. Every entry was measured against the shipped classifier.
PHRASINGS: dict[str, list[str]] = {
    "ad_supported": [
        "free to user, advertising supported",
        "monetized with display advertising",
        "free app, revenue from ads",
        "ad-supported free tier",
        "we sell advertising inventory",
        "no charge to users; advertisers pay",
        "supported by sponsorships and ads",
    ],
    "marketplace": [
        "take-rate commission on third-party jobs",
        "we take 12% of each transaction",
        "two-sided platform connecting buyers and sellers",
        "commission on GMV",
        "we match supply and demand and take a cut",
        "peer-to-peer rental platform",
    ],
    "services": [
        "project-based agency retainer",
        "consultancy billing per engagement",
        "we bill hourly for design work",
        "done-for-you service, monthly retainer",
        "professional services firm",
        "custom implementation projects",
    ],
    "subscription": [
        "monthly subscription per seat",
        "SaaS billed annually",
        "members pay $30 a month",
        "recurring licence fee",
        "$99/mo per workspace",
    ],
    "ecommerce": [
        "one-time direct-to-consumer product sales",
        "we sell physical goods online",
        "DTC brand, single purchase",
        "customers buy the product once",
    ],
    "hybrid": [
        "one-time device purchase plus monthly app subscription",
        "hardware sale with recurring software fee",
        "buy the unit, subscribe for analytics",
    ],
    "transactional": [
        "brick-and-mortar retail, pay per drink",
        "customers pay per visit",
        "walk-in salon, per-service pricing",
        "counter service, pay per item",
    ],
}


def _classify(phrase: str, expected: str) -> str:
    from business_model import classify_business_model
    ms = _PHYSICAL if expected == "transactional" else _DIGITAL
    return classify_business_model(
        {"business_model": phrase, "category": "", "summary": phrase}, ms)


class TestEveryModelIsRecognisable(unittest.TestCase):
    """One subtest per phrasing, so a failure names the exact sentence that broke."""

    def test_natural_phrasings_classify(self):
        misses = []
        total = 0
        for expected, phrases in PHRASINGS.items():
            for phrase in phrases:
                total += 1
                got = _classify(phrase, expected)
                if got != expected:
                    misses.append(f"{expected}: {phrase!r} -> {got}")
        self.assertEqual(
            misses, [],
            f"{len(misses)}/{total} natural phrasings misclassify. Every miss becomes the "
            f"default and gets that model's economics:\n  " + "\n  ".join(misses))

    def test_punctuation_is_not_a_different_business_model(self):
        """'ad-supported', 'ad supported' and 'ad_supported' are one concept. Treating them
        as three is how a literal-substring matcher accumulates near-duplicate entries and
        still misses the fourth spelling."""
        for variant in ("ad-supported free tier", "ad supported free tier",
                        "ad—supported free tier"):
            self.assertEqual(_classify(variant, "ad_supported"), "ad_supported", variant)


class TestSpecificityOrderSurvives(unittest.TestCase):
    """Broadening the patterns must not let a general signal outrank a specific one."""

    def test_a_marketplace_that_also_charges_a_subscription_is_a_marketplace(self):
        self.assertEqual(
            _classify("we take 15% commission and charge sellers $29/mo", "marketplace"),
            "marketplace")

    def test_an_agency_on_retainer_is_services_not_subscription(self):
        """A retainer IS recurring. Services wins because it is the more specific claim."""
        self.assertEqual(
            _classify("design agency, monthly retainer", "services"), "services")

    def test_a_cafe_with_a_coffee_club_is_not_an_ad_business(self):
        self.assertEqual(
            _classify("pay per drink, plus an optional monthly coffee club", "transactional"),
            "hybrid")

    def test_a_saas_that_mentions_advertising_its_product_is_not_ad_supported(self):
        """'we advertise on Instagram' is a CHANNEL, not a revenue model. This is the
        false-positive risk that comes with broader matching, so it is pinned."""
        self.assertEqual(
            _classify("monthly SaaS subscription; we advertise on Instagram", "subscription"),
            "subscription")


class TestAnUnsignalledModelIsDisclosedNotAssumed(unittest.TestCase):
    """The root defect. Defaulting to subscription in silence is why 16 misclassifications
    were invisible: the report presents CLV, churn and MRR for a venture that never said it
    was recurring, and every number is internally consistent."""

    def test_a_brief_with_no_monetization_signal_is_flagged(self):
        from business_model import classify_with_confidence

        out = classify_with_confidence({"business_model": "a really useful app for chefs",
                                        "category": "cooking app", "summary": ""}, _DIGITAL)
        self.assertFalse(out["explicit"],
                         "a brief that never says how it charges was treated as explicit")
        self.assertTrue(out["disclosure"], "no disclosure text for an inferred model")

    def test_an_explicit_model_is_not_flagged(self):
        from business_model import classify_with_confidence

        out = classify_with_confidence({"business_model": "we take 12% of each transaction",
                                        "category": "", "summary": ""}, _DIGITAL)
        self.assertTrue(out["explicit"])
        self.assertEqual(out["kind"], "marketplace")
        self.assertFalse(out.get("disclosure"))

    def test_the_kind_is_still_returned_so_the_pipeline_can_run(self):
        """Refusing to classify would block a report over a missing sentence. The default
        stays; it just stops being silent."""
        from business_model import classify_with_confidence

        out = classify_with_confidence({"business_model": "", "category": "app",
                                        "summary": ""}, _DIGITAL)
        self.assertTrue(out["kind"])

    def test_the_plain_classifier_is_unchanged_for_every_caller(self):
        """classify_business_model has many call sites; the confidence variant is additive."""
        from business_model import classify_business_model, classify_with_confidence

        for expected, phrases in PHRASINGS.items():
            ms = _PHYSICAL if expected == "transactional" else _DIGITAL
            prof = {"business_model": phrases[0], "category": "", "summary": phrases[0]}
            self.assertEqual(classify_business_model(prof, ms),
                             classify_with_confidence(prof, ms)["kind"])


if __name__ == "__main__":
    unittest.main()


class TestTheDisclosureReachesTheReader(unittest.TestCase):
    """A disclosure that lands in the JSON and not on the page is not a disclosure.

    This repo has shipped that exact shape before: #83 computed a full som_anchor block
    explaining how the headline SOM was derived, and nothing in templates/report.html ever
    rendered it — the reasoning existed only for whoever read the artifact. So the wiring is
    pinned here rather than assumed, in both directions.
    """

    def _render(self, business_model):
        import json
        import os
        if not os.path.exists("out/live/run18.json"):
            self.skipTest("no stored run to render")
        from report.render_html import render_report_html
        r = json.load(open("out/live/run18.json"))["result"]
        r["business_model"] = business_model
        return render_report_html(r, job_id="disclosure-test")

    def test_an_inferred_model_is_visible_on_the_page(self):
        html = self._render({"kind": "subscription", "explicit": False,
                             "disclosure": "Monetization model INFERRED as 'subscription' — "
                                           "the brief does not say how this venture charges."})
        self.assertIn("Monetization model INFERRED", html)

    def test_an_explicit_model_says_nothing(self):
        """Disclosing a non-issue on every report is how readers learn to skip the box."""
        html = self._render({"kind": "marketplace", "explicit": True, "disclosure": None})
        self.assertNotIn("Monetization model INFERRED", html)

    def test_a_report_without_the_key_still_renders(self):
        """Older artifacts have no business_model block; the template must not explode."""
        html = self._render(None)
        self.assertTrue(len(html) > 1000)
