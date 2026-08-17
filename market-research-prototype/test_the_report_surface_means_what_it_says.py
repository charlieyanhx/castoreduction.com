"""Five things the shipped PDF said that it did not mean. All measured on job d62bc04f.

None of these is a modelling judgement — each is a place where the pipeline printed a
literal, mechanical falsehood on a page a customer paid for. They are grouped because they
share one shape: a value was rendered without asking what KIND of value it is.

  1. "US Real GDP Growth  1.5 %YoY   +200.0%"                            (page 23)
     macro_anchors computes yoy_pct = (latest - prev)/prev * 100 for every series, and
     us_real_gdp_growth carries "unit": "% YoY" — it is ALREADY a rate. The growth rate
     moving 0.5 -> 1.5 printed as "+200.0%" beside a GDP figure, which every reader parses
     as "GDP grew 200%". A rate series changes by PERCENTAGE POINTS, not by percent. The
     unit is right there in the series metadata and nothing consulted it.

  2. "Only 1 of 4 simulated segments named a price ... (A per-visit business may not fit a
     monthly model.)"                                                    (page 7)
     A hardcoded cafe string at templates/report.html, shown to an orbital satellite utility
     because the only condition was `wtp_is_monthly`.

  3. "the higher-signal sources here are Google & Yelp reviews of nearby competitors and
     industry-specific forums (e.g. r/coffee, Home-Barista for a cafe)"  (page 16)
     Same class: the literal cafe example is baked into the empty-Reddit explainer for every
     venture in the product.

  4. "As an emerging orbital solar reflection infrastructure provider, Unknown can capture
     the market..."                                                      (page 12)
     `profile.name` is the literal string "Unknown" — the extractor's placeholder for a brief
     that named no company — and it was handed to the LLM as the company's name, which duly
     wrote it into customer-facing prose. A sentinel escaped into the document.

  5. "Prepared: 1786991389"                                              (page 1, the cover)
     A raw Unix timestamp. The HTML report formats the same value correctly ("2026-08-17
     11:47"); only the PDF cover prints it raw.

And one honest-but-misleading number:

  6. "its 3 estimates diverge 159%"                                      (page 10)
     The three TAM methods are $8M, $1.6B and $2.5B — a 312-FOLD spread — reported as 159%
     because it is computed as (max-min)/median. A percentage on a distribution spanning two
     and a half orders of magnitude reads as mild disagreement. The codebase already has the
     honest idiom for this: skills/sizing/validate.py writes "diverge {ratio:.1f}x".
"""
from __future__ import annotations

import unittest


class TestARateSeriesChangesByPercentagePoints(unittest.TestCase):
    def test_a_rate_unit_is_recognised(self):
        from macro_anchors import _is_rate_unit
        for u in ("% YoY", "%", "percent", "% yoy"):
            with self.subTest(u=u):
                self.assertTrue(_is_rate_unit(u))

    def test_a_level_unit_is_not_a_rate(self):
        from macro_anchors import _is_rate_unit
        for u in ("Billions USD", "Millions USD", "Index 2019=100", "months", "USD", ""):
            with self.subTest(u=u):
                self.assertFalse(_is_rate_unit(u))

    def test_the_measured_case_reads_as_percentage_points(self):
        """0.5 -> 1.5 is +1.0 pp, not +200%."""
        from macro_anchors import change_label
        got = change_label({"unit": "% YoY", "latest_value": 1.5, "prev_year_value": 0.5,
                            "yoy_pct": 200.0})
        self.assertIn("pp", got, f"a rate series still reports a percent change: {got!r}")
        self.assertIn("+1.0", got, got)
        self.assertNotIn("200", got, f"the +200.0% is still on the page: {got!r}")

    def test_a_level_series_still_reports_percent(self):
        """US Nominal GDP genuinely did grow 3.4% — that must not change."""
        from macro_anchors import change_label
        got = change_label({"unit": "Billions USD", "latest_value": 32475.2,
                            "prev_year_value": 31406.4, "yoy_pct": 3.4})
        self.assertIn("%", got)
        self.assertIn("+3.4", got)
        self.assertNotIn("pp", got)

    def test_no_change_data_yields_nothing_rather_than_zero(self):
        from macro_anchors import change_label
        self.assertFalse(change_label({"unit": "% YoY", "latest_value": 1.5}))
        self.assertFalse(change_label({}))

    def test_the_series_carries_the_label_so_one_place_owns_the_wording(self):
        """Both the report table and the methodology citation must read the same string,
        or they drift — the pattern this codebase keeps re-learning."""
        import inspect

        import macro_anchors
        self.assertIn("change_label", inspect.getsource(macro_anchors.fetch_anchors))
        self.assertIn("change_label",
                      inspect.getsource(macro_anchors.format_anchor_for_citation))


class TestNoCafeStringsOnANonCafe(unittest.TestCase):
    def test_the_per_visit_aside_is_not_shown_to_every_monthly_venture(self):
        tpl = open("templates/report.html").read()
        i = tpl.find("per-visit business may not fit")
        self.assertGreater(i, 0, "the string moved; re-point this test")
        window = tpl[max(0, i - 400):i]
        self.assertIn(
            "business_model_kind", window,
            "the cafe aside is still gated on wtp_is_monthly alone, so a subscription "
            "satellite venture is still told it might be a per-visit business")
        self.assertNotIn(
            "subscription", window.split("business_model_kind")[-1][:200],
            "a subscription venture is inside the set that gets the per-visit aside")

    def test_the_reddit_explainer_names_no_cafe(self):
        tpl = open("templates/report.html").read()
        for needle in ("r/coffee", "Home-Barista"):
            self.assertNotIn(needle, tpl,
                             f"{needle!r} is hardcoded into an explainer every venture sees")


class TestAPlaceholderNameNeverReachesProse(unittest.TestCase):
    def test_unknown_is_normalised_away(self):
        from company_profile import clean_company_name
        for placeholder in ("Unknown", "unknown", "N/A", "n/a", "None", "", "  ", "TBD",
                            "Not specified", "Unnamed"):
            with self.subTest(p=placeholder):
                self.assertIsNone(clean_company_name(placeholder),
                                  f"{placeholder!r} survives as a company name and can be "
                                  "written into prose as if it were one")

    def test_a_real_name_is_kept_exactly(self):
        from company_profile import clean_company_name
        for real in ("Reflect Orbital", "Planet Labs", "X", "3M"):
            with self.subTest(real=real):
                self.assertEqual(clean_company_name(real), real)

    def test_the_extractor_applies_it(self):
        import inspect

        import company_profile
        self.assertIn("clean_company_name", inspect.getsource(company_profile),
                      "the normaliser exists but the extractor never calls it")


class TestTheCoverPrintsADate(unittest.TestCase):
    def test_an_epoch_is_formatted(self):
        from report.pdf import _cover_date
        got = _cover_date("1786991389")
        self.assertNotIn("1786991389", got, f"the raw epoch is still on the cover: {got!r}")
        self.assertIn("2026", got, got)

    def test_an_already_formatted_date_is_left_alone(self):
        from report.pdf import _cover_date
        self.assertEqual(_cover_date("2026-08-17 11:47"), "2026-08-17 11:47")

    def test_empty_stays_empty(self):
        from report.pdf import _cover_date
        self.assertEqual(_cover_date(""), "")
        self.assertEqual(_cover_date(None), "")


class TestAWideSpreadIsNotAPercentage(unittest.TestCase):
    def test_the_measured_tam_spread_reads_as_a_multiple(self):
        """$8M / $1.6B / $2.5B is 312x apart. '159%' reads as mild disagreement."""
        from report.forecast import spread_phrase
        got = spread_phrase([8.0e6, 1.568e9, 2.5e9], 1.568e9)
        self.assertIn("×", got, f"a 312-fold spread is still a percentage: {got!r}")
        self.assertNotIn("159%", got, got)

    def test_a_genuinely_narrow_spread_stays_a_percentage(self):
        from report.forecast import spread_phrase
        got = spread_phrase([9.0e8, 1.0e9, 1.1e9], 1.0e9)
        self.assertIn("%", got)
        self.assertNotIn("×", got)

    def test_degenerate_input_does_not_raise(self):
        from report.forecast import spread_phrase
        for vals, mid in (([], 1.0), ([1.0], 1.0), ([1.0, 2.0], 0), ([0.0, 0.0], 1.0)):
            with self.subTest(vals=vals):
                self.assertIsInstance(spread_phrase(vals, mid), str)


if __name__ == "__main__":
    unittest.main()
