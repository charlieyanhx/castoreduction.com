"""The founder states a price; the report prices the venture at the model's guess instead.

MEASURED against the real chain — `unit_for_model` -> `extract_unit_price` /
`extract_device_price` / `extract_stated_price` -> `price_of_record` — over 20 ordinary
ways a founder writes their own price, with the PSM modelled point pinned at $38:

    PRICE OF RECORD CORRECT: 4/20 = 20%

Of the 16 misses, 13 land on `basis="PSM optimal"` with **`differs_from_psm: False`** — the
report tells the founder the model agrees with their price when no price was ever read:

    "we charge $12,000 per project"        -> $38    differs_from_psm: False
    "our serum retails at $42 a bottle"    -> $38    differs_from_psm: False
    "$499 per seat per month"              -> $38    differs_from_psm: False
    "EUR 3.50 per pastry"                  -> $38    differs_from_psm: False

The other three are worse, because they return a real number that is the wrong one:

    "the hardware costs $249 up front, then $9 monthly"  -> $9    basis "stated price"
    "buy the monitor for $329, subscribe at $12/mo"      -> $12   basis "stated price"

That is the app fee sold as the hardware price. It is the exact defect `extract_device_price`
was written for (B2/D17), still live: `_DEVICE_PRICE_RE` requires the `$` to come BEFORE the
device noun, and "the hardware costs $249" puts the noun first, so the guard never engages
and `extract_stated_price` takes the /mo figure. Against real hardware COGS that is a −700%
margin, economics errors out, and financials falls back to the subscription model — churn
and lifetime value on a one-time sale.

WHY IT IS THIS BAD: four regexes, hand-maintained, disagreeing with each other and with the
unit the same module picks.

  plan._UNIT_NOUN_RE       picks bottle/jar/box/kit/unit/device/project/engagement/sprint/
                           hour/day as the venture's unit -- the richest vocabulary
  plan._UNIT_PRICE_RE      cannot extract a price for ANY of those nouns
  plan._DEVICE_PRICE_RE    needs `$` within 3 words BEFORE the noun
  brief._STATED_PRICE_RE   dies on any noun between the price and the period, so
                           "$499 per seat per month" reads as no price at all
  all four                 hardcode `\\$`, so EUR/GBP/€/£ are invisible

The unit resolver and the price extractor must share one vocabulary. A module that can name
a venture's unit "sprint" and then cannot read "$9,500 per sprint" is not missing a pattern,
it is two parsers that were never introduced.

WHAT THIS FILE DOES NOT ASK FOR: currency CONVERSION. `extract_price` reports the currency
it read; a non-USD figure must be disclosed as unconverted rather than silently treated as
dollars or silently dropped. The lie is the silence, not the dollar sign.
"""
from __future__ import annotations

import unittest

#: (business kind, brief text, price, currency, unit noun the same brief should resolve to).
#: Every line is a plain sentence from a founder describing their own pricing.
BRIEFS = [
    ("subscription", "$499 per seat per month", 499.0, "USD", "seat"),
    ("subscription", "$29 per user per month", 29.0, "USD", "account"),
    ("subscription", "$99/mo per workspace", 99.0, "USD", "account"),
    ("services", "we charge $12,000 per project", 12_000.0, "USD", "project"),
    ("services", "$450 per engagement", 450.0, "USD", "engagement"),
    ("services", "$180 an hour for consulting", 180.0, "USD", "hour"),
    ("services", "two-week sprints at $9,500 per sprint", 9_500.0, "USD", "sprint"),
    ("ecommerce", "our serum retails at $42 a bottle", 42.0, "USD", "bottle"),
    ("ecommerce", "$38 per jar, direct to consumer", 38.0, "USD", "jar"),
    ("ecommerce", "each kit sells for $65", 65.0, "USD", "kit"),
    ("ecommerce", "the box is priced at $54", 54.0, "USD", "box"),
    ("hybrid", "$199 device plus $5/mo app", 199.0, "USD", "device"),
    ("hybrid", "the hardware costs $249 up front, then $9 monthly", 249.0, "USD", "unit"),
    ("hybrid", "buy the monitor for $329, subscribe at $12/mo", 329.0, "USD", "unit"),
    ("transactional", "$6 per drink", 6.0, "USD", "drink"),
    ("transactional", "$15 a cut", 15.0, "USD", "cut"),
    ("transactional", "EUR 3.50 per pastry", 3.50, "EUR", "item"),
    ("transactional", "€3.50 per loaf", 3.50, "EUR", "item"),
    ("transactional", "£28 per treatment", 28.0, "GBP", "treatment"),
    ("marketplace", "we take $35 per booking", 35.0, "USD", "booking"),
]


class TestOneExtractorReadsThemAll(unittest.TestCase):
    def test_every_brief_yields_its_stated_price(self):
        from brief import extract_price
        from plan import unit_for_model
        misses = []
        for kind, text, want, _cur, _unit in BRIEFS:
            noun = unit_for_model(kind, text, {"summary": text, "category": ""})
            got = extract_price(text, noun)
            if not got or abs((got.get("value") or 0) - want) > 0.01:
                misses.append(f"{kind}: {text!r} -> {got and got.get('value')} "
                              f"(want {want})")
        self.assertEqual(misses, [],
                         f"{len(misses)}/{len(BRIEFS)} stated prices unread. Every one "
                         f"becomes the modelled guess, presented as agreeing with the "
                         f"founder:\n  " + "\n  ".join(misses))

    def test_the_currency_is_reported_not_assumed(self):
        from brief import extract_price
        for kind, text, _want, cur, _unit in BRIEFS:
            with self.subTest(text=text):
                got = extract_price(text, None)
                self.assertIsNotNone(got, text)
                self.assertEqual(got["currency"], cur)

    def test_the_basis_says_where_the_number_came_from(self):
        from brief import extract_price
        got = extract_price("we charge $12,000 per project", "project")
        self.assertIn("project", got["basis"],
                      "the basis must name the unit it read, so a reader can check it")


class TestTheUnitResolverAndTheExtractorShareAVocabulary(unittest.TestCase):
    """The root cause. Any noun the resolver can NAME, the extractor must be able to READ —
    otherwise the report states a unit it cannot price."""

    def test_every_nameable_unit_is_priceable(self):
        from brief import UNIT_NOUNS, extract_price
        unreadable = []
        for noun in UNIT_NOUNS:
            got = extract_price(f"we charge $75 per {noun}", noun)
            if not got or got.get("value") != 75.0:
                unreadable.append(noun)
        self.assertEqual(unreadable, [],
                         f"the unit resolver can name these units but the price extractor "
                         f"cannot read a price in them: {unreadable}")

    def test_the_resolver_uses_that_same_vocabulary(self):
        """One constant, two consumers. Two lists drift; this is how they drifted."""
        import plan
        from brief import UNIT_NOUNS
        for noun in ("bottle", "jar", "kit", "sprint", "engagement", "project", "hour"):
            self.assertIn(noun, UNIT_NOUNS)
            self.assertEqual(
                plan.unit_for_model("ecommerce", f"sold per {noun}", None), noun,
                f"the resolver no longer names {noun!r} from the shared vocabulary")


class TestNounBeforePriceIsStillAPrice(unittest.TestCase):
    """The shape that turned an app fee into a hardware price."""

    def test_the_hardware_price_wins_over_the_app_fee(self):
        from brief import extract_price
        got = extract_price("the hardware costs $249 up front, then $9 monthly", "unit")
        self.assertEqual(got["value"], 249.0,
                         "the recurring component was read as the one-time price")

    def test_a_price_after_its_noun_is_found(self):
        from brief import extract_price
        for text, want in (("each kit sells for $65", 65.0),
                           ("the box is priced at $54", 54.0),
                           ("buy the monitor for $329, subscribe at $12/mo", 329.0)):
            with self.subTest(text=text):
                self.assertEqual(extract_price(text, "unit")["value"], want)

    def test_a_noun_between_price_and_period_does_not_hide_it(self):
        """"$499 per seat per month" read as no price at all — 7 of 18 SaaS phrasings."""
        from brief import extract_price
        got = extract_price("$499 per seat per month", "seat")
        self.assertEqual(got["value"], 499.0)
        self.assertEqual(got["period"], "month")


class TestAPriceNeverReadIsNeverAgreement(unittest.TestCase):
    """The consequence that makes this a reporting defect rather than a parsing one.

    `price_of_record` falls back to the PSM optimal and then computes
    `differs_from_psm = |opt - opt| / opt > 0.15` -> False. The founder is told the model
    agrees with their price. It read no price."""

    def _por(self, **kw):
        from plan import price_of_record
        args = dict(unit_price=None, device_price=None, stated=None, opt=38.0,
                    unit_noun="unit", is_transactional=True)
        args.update(kw)
        return price_of_record(**args)

    def test_no_stated_price_is_its_own_state(self):
        por = self._por()
        self.assertTrue(por.get("no_stated_price"),
                        "a report with no price to reconcile must say so")
        self.assertIsNone(por["differs_from_psm"],
                          "'differs: False' asserts an agreement that was never tested")

    def test_a_real_agreement_still_reads_as_agreement(self):
        """The narrowing must not erase the true case: a stated $40 against a $38 model IS
        agreement, and must keep saying so."""
        por = self._por(unit_price=40.0)
        self.assertFalse(por["no_stated_price"])
        self.assertIs(por["differs_from_psm"], False)

    def test_a_real_disagreement_still_flags(self):
        por = self._por(unit_price=120.0)
        self.assertIs(por["differs_from_psm"], True)

    def test_the_page_says_it_read_no_price(self):
        """A state that stays in the JSON is not a disclosure — this repo has shipped that
        exact shape before (#83's som_anchor was computed and never rendered)."""
        import json
        import os
        if not os.path.exists("out/live/run18.json"):
            self.skipTest("no stored run to render")
        from report.render_html import render_report_html
        r = json.load(open("out/live/run18.json"))["result"]
        r.setdefault("pricing", {})["price_of_record"] = self._por()
        html = render_report_html(r, job_id="no-price-test")
        self.assertIn("no stated price", html.lower())


class TestTheWholeChainThroughItsOwnCallSite(unittest.TestCase):
    """`price_from_brief` IS the call site — run_plan calls nothing else.

    It exists because the logic was inline, so measuring it meant reassembling it in a
    scratch script, and a script that reassembles a call site measures the script. My first
    two measurements of "4/20" were taken that way and agreed with each other while testing
    nothing the pipeline runs.
    """

    def _por(self, kind, text, opt=38.0):
        from business_model import is_per_unit
        from plan import price_from_brief, unit_for_model
        noun = unit_for_model(kind, text, {"summary": text, "category": ""})
        return price_from_brief(text, noun, opt, is_per_unit(kind))

    def test_a_stated_price_is_read_for_every_brief(self):
        """READING is the invariant. Which price then drives the economics is a per-model
        decision (a subscription keeps the PSM point, see price_of_record); being unable to
        read the founder's own sentence is not a decision, it is a defect."""
        unread = [f"{kind}: {text!r}" for kind, text, _w, _c, _u in BRIEFS
                  if self._por(kind, text)["stated_value"] is None]
        self.assertEqual(unread, [],
                         f"{len(unread)}/{len(BRIEFS)} briefs state a price the pipeline "
                         f"cannot read:\n  " + "\n  ".join(unread))

    def test_a_per_unit_venture_is_priced_at_what_it_said(self):
        from business_model import is_per_unit
        wrong = []
        for kind, text, want, _c, _u in BRIEFS:
            if not is_per_unit(kind):
                continue
            got = self._por(kind, text)["value"]
            if got is None or abs(got - want) > 0.01:
                wrong.append(f"{kind}: {text!r} -> {got} (want {want})")
        self.assertEqual(wrong, [], "\n  ".join(wrong))

    def test_the_hybrid_app_fee_never_becomes_the_hardware_price(self):
        """-700% margin, economics error, silent fall-back to subscription financials."""
        for text in ("$199 device plus $5/mo app",
                     "the hardware costs $249 up front, then $9 monthly",
                     "buy the monitor for $329, subscribe at $12/mo"):
            with self.subTest(text=text):
                got = self._por("hybrid", text)["value"]
                self.assertGreater(got, 100.0,
                                   f"the recurring leg won: {text!r} -> {got}")

    def test_a_recurring_venture_still_discloses_the_price_it_did_not_use(self):
        """The residue found while fixing this. A subscription keeps the PSM point as the
        figure driving economics — but MEASURED, a brief reading "$499 per seat per month"
        reported `differs_from_psm: False` beside a $38 model, because the comparison ran
        winner-vs-model and the winner IS the model. Asserting agreement with a price you
        read and discarded is the same lie as asserting it about one you never read."""
        por = self._por("subscription", "$499 per seat per month")
        self.assertEqual(por["stated_value"], 499.0)
        self.assertIs(por["differs_from_psm"], True)
        self.assertFalse(por["no_stated_price"])

    def test_a_recurring_price_close_to_the_model_does_not_cry_wolf(self):
        por = self._por("marketplace", "we take $35 per booking")
        self.assertEqual(por["stated_value"], 35.0)
        self.assertIs(por["differs_from_psm"], False)

    def test_the_disagreement_reaches_the_page_and_names_which_price_was_used(self):
        import json
        import os
        if not os.path.exists("out/live/run18.json"):
            self.skipTest("no stored run to render")
        from report.render_html import render_report_html
        r = json.load(open("out/live/run18.json"))["result"]
        r.setdefault("pricing", {})["price_of_record"] = self._por(
            "subscription", "$499 per seat per month")
        html = render_report_html(r, job_id="disagree-test")
        self.assertIn("stated price and this model disagree", html.lower())
        self.assertIn("499", html)


class TestANonUsdPriceIsDisclosedNotSwallowed(unittest.TestCase):
    """Not conversion — disclosure. A EUR 3.50 pastry currently becomes a modelled $7.50
    with nothing said; either number may be defensible, the silence is not."""

    def test_a_euro_price_is_read_and_labelled(self):
        from brief import extract_price
        got = extract_price("€3.50 per loaf", "item")
        self.assertEqual(got["value"], 3.50)
        self.assertEqual(got["currency"], "EUR")

    def test_price_of_record_carries_the_currency_forward(self):
        from plan import price_of_record
        por = price_of_record(unit_price=3.50, device_price=None, stated=None, opt=7.50,
                              unit_noun="item", is_transactional=True, currency="EUR")
        self.assertEqual(por["currency"], "EUR")
        self.assertTrue(por.get("currency_unconverted"),
                        "a non-USD figure sitting beside USD sizing must say it was not "
                        "converted")

    def test_a_usd_price_says_nothing(self):
        from plan import price_of_record
        por = price_of_record(unit_price=6.0, device_price=None, stated=None, opt=5.5,
                              unit_noun="drink", is_transactional=True)
        self.assertFalse(por.get("currency_unconverted"))


if __name__ == "__main__":
    unittest.main()
