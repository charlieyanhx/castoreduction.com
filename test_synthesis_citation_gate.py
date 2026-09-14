"""A number the analyst report did not take from the evidence must not reach a buyer.

THE FACT LAYER IS THE PRODUCT AND THE WRITING IS DELEGATED UNDER A GATE. MEASURED
2026-09-12 on the diag01 run: one Opus pass over the fact layer wrote 1,820 words with 60
[path] citations, every one resolving, and surfaced four pipeline defects nobody had found.
The same prompt on Sonnet 4.5 invented a BEA 21% figure and a $12,000 / $4,500 / $2,000 /
$23,000 cost sketch. A whole-blob number match passed both. D62 resolves each citation,
pools the numbers the cited values contain, and holds every number in the prose to that
pool at the scale its own suffix names, with a whole number of 100 or less also needing a
citation in its own paragraph. It is a fail-severity gate, so the verifier withholds the
report; D63 is its advisory half.

The acceptance test is the real artifact: the Opus report over the real run's fact layer
(tests/fixtures/synthesis, copied verbatim from out/live/diag01) passes with zero fails.
Three scoping decisions were needed to get there without touching the fixture, and each is
pinned below by a test that shows the strict reading would have blocked an honest report:
a small integer's citation may sit anywhere in its paragraph, a table's citation may sit in
the caption under it, and a count of hours hyphenated to its unit (an 11-hour window) is
clock arithmetic on the brief, not a claim. The founder's own words are pooled as evidence
for the same reason: the only numbers in the Opus report that are in NO cited value are
the three "$5.50" tokens, which is the founder's stated price, which the pipeline never
extracted. That is one of the four defects the report surfaced.

PASSING THE ARTIFACT IS NOT THE SAME AS DISCRIMINATING, and the first cut of this gate
proved it: it passed the Opus report and also passed the Sonnet cost sketch, because a
number could try every unit scale in both directions and "$12,000" borrowed the count 12.
Measured then: 69 of 99 round-thousand rents appended as an invented sentence were
accepted. So the tests below hold the gate to a measured acceptance rate on the artifact,
not only to one lucky invented figure.
"""
from __future__ import annotations

import json
import os
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_FIXTURES = os.path.join(_HERE, "tests", "fixtures", "synthesis")
_RESULT = os.path.join(_FIXTURES, "diag01_result.json")
_MARKDOWN = os.path.join(_FIXTURES, "diag01_opus.md")

# The founder's words exactly as reference-synth.py handed them to the model, outside the
# JSON evidence. The producer records them as synthesis.venture; the gate pools them.
VENTURE = ("An independent specialty coffee shop with a small roastery, opening on a corner "
           "site in the Mission District of San Francisco. Pour-over and espresso, about 14 "
           "seats, wholesale beans to a few local cafes, open 7am to 6pm. Price around 5.50 "
           "for a drink.")

INVENTED = "Rent in the Mission averages $9,400 a month."


def fixture_markdown() -> str:
    with open(_MARKDOWN, encoding="utf-8") as f:
        return f.read()


def fixture_result(markdown: str | None = None, venture: str | None = VENTURE) -> dict:
    """The diag01 fact layer with a synthesis over it. `markdown` defaults to the Opus
    report; pass venture=None to withhold the founder's words from the gate."""
    with open(_RESULT, encoding="utf-8") as f:
        result = json.load(f)
    synthesis = {"markdown": fixture_markdown() if markdown is None else markdown}
    if venture is not None:
        synthesis["venture"] = venture
    result["synthesis"] = synthesis
    return result


def _gate(det_id: str):
    import gates
    inv = next((i for i in gates.INVARIANTS if i.id == det_id), None)
    assert inv is not None, f"{det_id} is not registered in gates.INVARIANTS"
    return inv


def _d62(result: dict):
    return _gate("D62").check(result, None)


def _d63(result: dict):
    return _gate("D63").check(result, None)


def _small(markdown: str) -> dict:
    """A synthesis of one's own over the real fact layer, no founder's words."""
    return fixture_result(markdown=markdown, venture=None)


class TestTheAcceptanceArtifact(unittest.TestCase):
    """The Opus report over the diag01 fact layer. If this fails, fix the gate's scoping."""

    def test_the_opus_report_passes_with_zero_fails(self):
        f = _d62(fixture_result())
        self.assertTrue(f.ok, f.detail)
        self.assertIn("60 cited value", f.detail)

    def test_every_one_of_its_sixty_citations_resolves(self):
        f = _d63(fixture_result())
        self.assertTrue(f.ok, f.detail)
        self.assertIn("all 60 citation", f.detail)

    def test_without_the_founders_words_the_only_offender_is_the_founders_price(self):
        """The gate, with nothing but the fact layer, finds exactly the defect the report
        itself surfaced: the stated $5.50 was never extracted, so it is in no cited value."""
        f = _d62(fixture_result(venture=None))
        self.assertFalse(f.ok)
        self.assertEqual(f.detail.count("5.50 in"), 3, f.detail)
        self.assertNotIn("9,400", f.detail)
        self.assertTrue(f.detail.startswith("3 number(s)"), f.detail)


class TestAnInventedFigureFails(unittest.TestCase):
    def test_one_appended_sentence_with_an_invented_rent_fails_naming_it(self):
        f = _d62(fixture_result(markdown=fixture_markdown() + "\n\n" + INVENTED + "\n"))
        self.assertFalse(f.ok)
        self.assertIn("9,400", f.detail)
        self.assertIn("Rent in the Mission", f.detail, "the finding carries context")

    def test_the_finding_lists_at_most_five_offenders_with_context(self):
        lines = "\n\n".join(f"Line item {i} costs ${i * 1111 + 1234:,} a month." for i in range(1, 9))
        f = _d62(_small(f"The SOM is $645,289 [market_sizing.som.mid].\n\n{lines}\n"))
        self.assertFalse(f.ok)
        self.assertTrue(f.detail.startswith("8 number(s)"), f.detail)
        self.assertEqual(f.detail.count(" in '..."), 5, f.detail)

    def test_a_wrong_price_is_not_a_rounding(self):
        """$5.50 against a cited $5.75 is 4% off. The prototype's 0.5 absolute floor let
        it through; 0.6% does not."""
        f = _d62(_small("The optimal price is $5.50 [pricing.psm.optimal_price_point].\n"))
        self.assertFalse(f.ok)
        self.assertIn("5.50", f.detail)

    def test_a_number_that_ends_a_clause_is_named_without_its_punctuation(self):
        """The token pattern swallows a trailing comma or full stop ("$4,500," parses), so
        the first cut named the offender as "4,500," and "9,400.". The finding names the
        number."""
        f = _d62(_small("A barista costs $4,500, a part-timer $2,000, and rent is $9,400.\n"))
        self.assertFalse(f.ok)
        for named in ("4,500 in", "2,000 in", "9,400 in"):
            self.assertIn(named, f.detail)
        for wrong in ("4,500, in", "2,000, in", "9,400. in"):
            self.assertNotIn(wrong, f.detail)


class TestWhatPasses(unittest.TestCase):
    def test_a_synthesis_with_no_numbers_passes(self):
        f = _d62(_small("The evidence is thin on the cost side, and a signed lease would "
                        "settle more than any further research.\n"))
        self.assertTrue(f.ok, f.detail)
        self.assertIn("0 numeric token", f.detail)

    def test_a_heading_that_repeats_a_cited_number_is_not_a_fail(self):
        md = ("The obtainable revenue is $645,289 a year [market_sizing.som.mid].\n\n"
              "## The $645,289 question\n\nNothing else is cited here.\n")
        f = _d62(_small(md))
        self.assertTrue(f.ok, f.detail)

    def test_a_number_may_be_written_in_millions_or_as_a_percent(self):
        md = ("TAM is $150.3M [market_sizing.tam.mid] and the serviceable slice is 35% "
              "[market_sizing.sam.serviceable_slice_pct].\n")
        f = _d62(_small(md))
        self.assertTrue(f.ok, f.detail)

    def test_a_whole_number_may_stand_for_a_decimal(self):
        f = _d62(_small("Break-even is 128 drinks a day [economics.break_even_units_per_day].\n"))
        self.assertTrue(f.ok, f.detail)

    def test_trivial_integers_and_years_carry_no_claim(self):
        md = "Over 3 years from 2026, on 12 months a year, the 2022 Census is the base.\n"
        f = _d62(_small(md))
        self.assertTrue(f.ok, f.detail)


class TestAbstention(unittest.TestCase):
    def test_a_result_with_no_synthesis_is_not_applicable(self):
        with open(_RESULT, encoding="utf-8") as f:
            result = json.load(f)
        self.assertNotIn("synthesis", result)
        for det in ("D62", "D63"):
            with self.subTest(gate=det):
                f = _gate(det).check(result, None)
                self.assertIsNone(f.ok)
                self.assertTrue(f.out_of_scope,
                                "a run without a synthesis is a fact about the run, and "
                                "must not thin D55's denominator on every older report")

    def test_an_empty_markdown_is_not_applicable_either(self):
        self.assertIsNone(_d62(fixture_result(markdown="   ")).ok)


class TestAnUnresolvableCitationIsAdvisoryOnly(unittest.TestCase):
    _MD = ("The obtainable revenue is $645,289 a year [market_sizing.som.mid]. "
           "The audience decode was dropped for want of any consumer signal [audiences.decode].\n")

    def test_d63_names_the_dead_path(self):
        f = _d63(_small(self._MD))
        self.assertFalse(f.ok)
        self.assertIn("[audiences.decode]", f.detail)
        self.assertIn("1 of 2", f.detail)

    def test_d62_is_untouched_by_it(self):
        self.assertTrue(_d62(_small(self._MD)).ok)

    def test_d63_is_warn_severity_so_the_verifier_only_annotates(self):
        from report.verifier import Severity, verify_report
        vr = verify_report(_small(self._MD))
        mine = [f for f in vr.findings if f.invariant in ("D62", "D63")]
        self.assertEqual([(f.invariant, f.severity) for f in mine], [("D63", Severity.ADVISORY)])

    def test_an_unresolved_citation_cannot_launder_a_number(self):
        """The number beside a dead path is still held to the pool of everything else."""
        md = ("The obtainable revenue is $645,289 a year [market_sizing.som.mid]. "
              "Regional prices run 21% above the national level [bea.regional_price_parity].\n")
        f = _d62(_small(md))
        self.assertFalse(f.ok)
        self.assertIn("21", f.detail)


class TestTheResolver(unittest.TestCase):
    def setUp(self):
        with open(_RESULT, encoding="utf-8") as f:
            self.facts = {k: v for k, v in json.load(f).items() if not k.startswith("_")}

    def test_dotted_keys_indexes_and_a_quoted_suffix(self):
        from gates import resolve_path
        self.assertEqual(resolve_path("competitor_pricing.per_domain[0].median", self.facts), 21.0)
        self.assertEqual(resolve_path('market_sizing.som: "mid"', self.facts)["mid"],
                         self.facts["market_sizing"]["som"]["mid"])
        self.assertIsNone(resolve_path("market_sizing.no_such_key", self.facts))
        self.assertIsNone(resolve_path("competitor_pricing.per_domain[99].median", self.facts))

    def test_an_empty_path_never_resolves_to_the_root(self):
        """The prototype resolved "0" to the whole fact layer, so one citation vouched for
        every number in the run."""
        from gates import resolve_path
        self.assertIsNone(resolve_path("", self.facts))
        self.assertIsNone(resolve_path("0", self.facts))
        self.assertIsNone(resolve_path("[0]", self.facts))

    def test_a_citation_with_a_nested_index_is_one_citation(self):
        """[competitor_pricing.per_domain[0].median] used to be read as the citation [0]."""
        from gates import audit_synthesis
        a = audit_synthesis("The one domain yielded a median of $21 "
                            "[competitor_pricing.per_domain[0].median].\n", self.facts)
        self.assertEqual((a["citations"], a["resolved"], a["unresolved"]), (1, 1, []))
        self.assertEqual(a["offenders"], [])

    def test_a_comma_separated_citation_pools_every_path(self):
        from gates import audit_synthesis
        a = audit_synthesis("Break-even is 127.9 drinks against a $16,500 fixed cost "
                            "[economics.break_even_units_per_day, economics.monthly_fixed_cost].\n",
                            self.facts)
        self.assertEqual(a["offenders"], [])

    def test_leaf_numbers_read_strings_and_stop_at_depth(self):
        from gates import leaf_numbers
        self.assertEqual(leaf_numbers("28,871 households within 1.5 km"), {28871.0, 1.5})
        deep = {"a": {"b": {"c": {"d": {"e": {"f": 7}}}}}}
        self.assertEqual(leaf_numbers(deep), set())
        self.assertEqual(leaf_numbers({"n": 3, "flag": True}), {3.0})


class TestTheScopeOfASmallInteger(unittest.TestCase):
    """A whole number of 100 or less matches somewhere in a 141KB fact layer by luck, so it
    also has to sit in a block that carries a citation. The block is the paragraph, or the
    table with its caption. Sentence scope would have blocked the Opus report on "The
    fair-share division used 84." and on every row of its price-band table."""

    def test_a_small_integer_needs_a_citation_in_its_paragraph(self):
        cited = "The roster holds 84 venues of this type [market_sizing.competitors].\n\n"
        f = _d62(_small(cited + "The fair-share division used 84. Nothing here is cited.\n"))
        self.assertFalse(f.ok, "84 is in the pool by an earlier citation, but its "
                               "paragraph carries none")
        self.assertIn("84", f.detail)

    def test_anywhere_in_the_paragraph_is_near_enough(self):
        md = ("The evidence carries 84 competing venues [market_sizing.competitors] and "
              "251 same-category venues [market_sizing.n_same_category]. The fair-share "
              "division used 84. If 251 is the right denominator, the demand anchor falls.\n")
        self.assertTrue(_d62(_small(md)).ok)

    def test_a_large_number_only_needs_the_document_pool(self):
        md = ("The evidence carries 251 same-category venues [market_sizing.n_same_category].\n\n"
              "If 251 is the right denominator, the demand anchor falls.\n")
        self.assertTrue(_d62(_small(md)).ok)

    _TABLE = ("| Venue | Rating | Price band |\n|---|---|---|\n"
              "| Sightglass Coffee | 4.4 | $10-20 |\n| CoffeeShop | 4.7 | $1-10 |\n")
    _ELSEWHERE = "Google price bands were read off four venues [market_sizing.geo_competitors].\n\n"

    def test_a_tables_citation_may_sit_in_the_caption_under_it(self):
        md = self._ELSEWHERE + self._TABLE + "\nSource: [market_sizing.geo_competitors].\n"
        self.assertTrue(_d62(_small(md)).ok, _d62(_small(md)).detail)

    def test_the_line_introducing_a_table_counts_as_its_caption_too(self):
        md = self._ELSEWHERE + self._TABLE + "\nThese are per-visit bands.\n"
        self.assertTrue(_d62(_small(md)).ok, _d62(_small(md)).detail)

    def test_a_table_with_no_citation_on_either_side_fails_on_its_small_integers(self):
        md = (self._ELSEWHERE + "The bands are per visit, not per drink.\n\n" + self._TABLE
              + "\nThese are per-visit bands.\n\nUnrelated prose.\n")
        f = _d62(_small(md))
        self.assertFalse(f.ok, "20 is in the pool through the citation two paragraphs up, "
                               "but neither the table nor a caption beside it cites anything")
        self.assertIn("20", f.detail)

    def test_numbers_in_a_table_header_row_are_skipped(self):
        md = ("| Option 11 | Option 13 |\n|---|---|\n| 127.9 | 187.0 |\n\n"
              "Source: [economics.break_even_units_per_day, "
              "financials.scenarios.base.year_1.units_per_day].\n")
        self.assertTrue(_d62(_small(md)).ok, _d62(_small(md)).detail)
        body = md.replace("| 127.9 | 187.0 |", "| 11 | 13 |")
        self.assertFalse(_d62(_small(body)).ok, "the same numbers in a body row are claims")

    def test_a_horizontal_rule_under_a_row_does_not_make_it_a_header(self):
        """"---" is a rule; a GFM separator carries a pipe. The first cut read the rule as
        a separator and skipped whatever row sat above it."""
        md = ("| 11 | 13 |\n---\n\nSource: [economics.break_even_units_per_day].\n")
        f = _d62(_small(md))
        self.assertFalse(f.ok, f.detail)
        self.assertIn("11 in", f.detail)

    def test_a_count_of_hours_hyphenated_to_its_unit_is_clock_arithmetic_not_a_claim(self):
        """"open 7am to 6pm" is in the brief; the 11 hours between are the model's sum,
        which it cannot cite by path. Nothing else earns this."""
        md = "Serving 311.7 drinks across an 11-hour window with 14 seats is untested.\n"
        cited = "The year-three ceiling is 311.7 drinks a day [financials.scenarios.base.year_3.units_per_day].\n\n"
        self.assertTrue(_d62(_small(cited + md)).ok, _d62(_small(cited + md)).detail)
        bare = cited + "Serving 311.7 drinks across 11 hours with 14 seats is untested.\n"
        self.assertFalse(_d62(_small(bare)).ok, "the same count written bare is a claim")

    def test_a_seat_count_or_a_day_count_hyphenated_to_its_unit_is_still_a_claim(self):
        """The first cut skipped every hyphenated hour/day/week/month/year/minute/seat, so
        an invented "45-seat room" with no citation passed. 45 is in the fact layer (the
        unit-economics health score) but its paragraph cites nothing."""
        f = _d62(_small("The 45-seat room upstairs adds capacity.\n"))
        self.assertFalse(f.ok)
        self.assertIn("45", f.detail)
        cited = "The stated 60-day test is three signed accounts [viability.recommended_next_steps].\n"
        self.assertTrue(_d62(_small(cited)).ok, _d62(_small(cited)).detail)
        f = _d62(_small("The stated 60-day test is three signed accounts.\n"))
        self.assertFalse(f.ok, "the same 60-day test with no citation in its paragraph")

    def test_money_and_percentages_are_never_descriptors(self):
        f = _d62(_small("Rent runs $9,400-a-month on Valencia.\n"))
        self.assertFalse(f.ok)
        self.assertIn("9,400", f.detail)

    def test_a_price_or_a_percentage_is_never_a_trivial_integer(self):
        """"5 personas" carries no claim; "$5 a drink" and "a 5% share" do. Both are in
        the fact layer somewhere, and neither paragraph cites anything."""
        self.assertTrue(_d62(_small("Five of them, or 5 personas, argue the same.\n")).ok)
        for claim in ("Charge $5 a drink.\n", "Take a 5% share of it.\n"):
            with self.subTest(claim=claim):
                f = _d62(_small(claim))
                self.assertFalse(f.ok)
                self.assertIn("5 in", f.detail)

    def test_an_ordered_list_marker_is_not_a_number(self):
        items = "\n".join(f"{i}. **Risk.** The evidence is thin here." for i in range(1, 13))
        self.assertTrue(_d62(_small(items + "\n")).ok, _d62(_small(items + "\n")).detail)
        named = items.replace("11. **Risk.**", "11. **Risk 11.**")
        self.assertFalse(_d62(_small(named + "\n")).ok, "the same 11 inside the text is a claim")


SKETCH = ("Assume $12,000 a month in rent, $4,500 for a barista, $2,000 for a part-timer, "
          "for about $23,000 of monthly cost.")


class TestTheSonnetCostSketch(unittest.TestCase):
    """The failure that motivates the gate, appended to the report that passes it."""

    def test_the_cost_sketch_fails_on_three_of_its_four_figures(self):
        f = _d62(fixture_result(markdown=fixture_markdown() + "\n\n" + SKETCH + "\n"))
        self.assertFalse(f.ok)
        self.assertTrue(f.detail.startswith("3 number(s)"), f.detail)
        for invented in ("4,500", "2,000", "23,000"):
            self.assertIn(f"{invented} in", f.detail)

    def test_twelve_thousand_is_genuinely_in_a_cited_value(self):
        """Not a miss: 12,000 is a follower count inside [discover.steps.signals], which
        the report cites. The other three are in no value it cites at their own scale."""
        from gates import leaf_numbers, resolve_path
        with open(_RESULT, encoding="utf-8") as fh:
            facts = json.load(fh)
        self.assertIn(12000.0, leaf_numbers(resolve_path("discover.steps.signals", facts)))
        f = _d62(fixture_result(markdown=fixture_markdown() + "\n\n" + SKETCH + "\n"))
        self.assertNotIn("12,000 in", f.detail)


class TestAScaleIsTheTokensOwn(unittest.TestCase):
    """A number is matched at the scale its suffix names and at no other. The first cut
    let every number try x1000, x1e6, x0.001, x1e-6, x100 and x0.01, so a five-digit
    dollar figure could borrow a rating, a count or a percentage from the pool."""

    def test_a_dollar_figure_cannot_borrow_a_percent_scale(self):
        """$4,500 x 100 lands within 0.6% of the $451,702 SOM low."""
        f = _d62(_small("The downside is $451,702 [market_sizing.som.low]. A barista costs $4,500 a month.\n"))
        self.assertFalse(f.ok)
        self.assertIn("4,500 in", f.detail)

    def test_a_dollar_figure_cannot_borrow_a_thousands_scale_from_a_count(self):
        """$12,000 / 1000 is the customer-universe count of 12."""
        f = _d62(_small("The universe holds 12 companies [customer_universe.count]. Rent is $12,000 a month.\n"))
        self.assertFalse(f.ok)
        self.assertIn("12,000 in", f.detail)

    def test_a_k_or_thousand_suffix_multiplies_by_a_thousand(self):
        for text in ("Fixed cost is $16.5k a month [economics.monthly_fixed_cost].\n",
                     "Fixed cost is 16.5 thousand dollars a month [economics.monthly_fixed_cost].\n"):
            with self.subTest(text=text):
                self.assertTrue(_d62(_small(text)).ok, _d62(_small(text)).detail)

    def test_an_m_or_million_suffix_multiplies_by_a_million_and_a_bare_figure_does_not(self):
        for text in ("SAM is $52.6M [market_sizing.sam.mid].\n",
                     "SAM is $52.6 million [market_sizing.sam.mid].\n"):
            with self.subTest(text=text):
                self.assertTrue(_d62(_small(text)).ok, _d62(_small(text)).detail)
        f = _d62(_small("SAM is 52.6 [market_sizing.sam.mid].\n"))
        self.assertFalse(f.ok, "52.6 with no suffix is 52.6, not 52.6 million")

    def test_a_percent_sign_allows_the_fraction_form_and_a_bare_decimal_does_not(self):
        self.assertTrue(_d62(_small("The slice is 35% [market_sizing.sam.serviceable_slice_pct].\n")).ok)
        f = _d62(_small("The slice is 0.35 [market_sizing.sam.serviceable_slice_pct].\n"))
        self.assertFalse(f.ok, "0.35 with no percent sign is 0.35")

    def test_a_year_is_exact_and_a_count_of_the_same_size_is_not(self):
        """0.6% of a year is twelve years either side. 2014 is in the fact layer; 2019 is
        not, and the first cut accepted it against 2014, 2020 and 2022."""
        md = fixture_markdown()
        self.assertTrue(_d62(fixture_result(markdown=md + "\n\nThe 2014 vintage is the base.\n")).ok)
        f = _d62(fixture_result(markdown=md + "\n\nThe 2019 vintage is the base.\n"))
        self.assertFalse(f.ok)
        self.assertIn("2019 in", f.detail)
        self.assertTrue(_d62(fixture_result(markdown=md + "\n\nThere are 2,019 of them.\n")).ok,
                        "2,019 with a thousands comma is a count and gets the 0.6%")

    def test_a_leading_dot_decimal_is_a_number(self):
        f = _d62(_small("Call it .5 of the SOM [market_sizing.som.mid].\n"))
        self.assertFalse(f.ok)
        self.assertIn(".5 in", f.detail)


class TestTheGateDiscriminates(unittest.TestCase):
    """The measurement, kept as a test. Append one invented round-thousand rent from
    $1,000 to $99,000 to the Opus report and count how many the gate accepts. The first
    cut accepted 69; under the suffix rule 13 remain, and every one of those 13 is a
    genuine value in a cited list within 0.6% (12,000 and 22,000 exactly, 28,871 for
    29,000, 40,223 for 40,000). That residue is the 0.6% tolerance the item specifies,
    not a scale leak, so the ceiling sits just above it."""

    _CEILING = 15

    def test_round_thousand_rents_are_mostly_refused(self):
        md = fixture_markdown()
        accepted = [k * 1000 for k in range(1, 100)
                    if _d62(fixture_result(markdown=f"{md}\n\nRent in the Mission averages "
                                                    f"${k * 1000:,} a month.\n")).ok]
        self.assertLessEqual(len(accepted), self._CEILING,
                             f"{len(accepted)} of 99 invented rents pass: {accepted}")


class TestBracketsAreNotAHidingPlace(unittest.TestCase):
    """A citation path contributes no numeric token of its own, so every number on the
    page is checked wherever it sits. The first cut skipped everything inside any bracket,
    and "[about $9,400 a month]" walked through."""

    _CITED = "The obtainable revenue is $645,289 a year [market_sizing.som.mid]. "

    def test_a_bracket_that_resolves_to_nothing_is_prose(self):
        f = _d62(_small(self._CITED + "Rent averages [about $9,400 a month].\n"))
        self.assertFalse(f.ok)
        self.assertIn("9,400 in", f.detail)

    def test_the_text_of_a_markdown_link_is_prose_and_its_destination_is_not(self):
        f = _d62(_small(self._CITED + "See [the $9,400 lease listing](https://example.com/listing).\n"))
        self.assertFalse(f.ok)
        self.assertIn("9,400 in", f.detail)
        f = _d62(_small(self._CITED + "See [the lease listing](https://example.com/listing/9400).\n"))
        self.assertTrue(f.ok, f.detail)

    def test_a_quoted_suffix_on_a_real_path_cannot_carry_an_invented_figure(self):
        """[market_sizing.som.mid: "low"] is the convention; the suffix is dropped for
        resolution, not for checking."""
        f = _d62(_small(self._CITED + "Rent is [market_sizing.som.mid: about $9,400 a month].\n"))
        self.assertFalse(f.ok)
        self.assertIn("9,400 in", f.detail)

    def test_d63_still_reports_the_prose_bracket_as_unresolved(self):
        f = _d63(_small(self._CITED + "Rent averages [about $9,400 a month].\n"))
        self.assertFalse(f.ok)
        self.assertIn("[about $9,400 a month]", f.detail)


class TestTheReportCannotCiteItself(unittest.TestCase):
    """result["synthesis"] is a non-underscore key. Left in the fact layer, a citation to
    [synthesis.markdown] resolved to the report and pooled every number in it."""

    def test_a_citation_to_the_synthesis_resolves_to_nothing(self):
        for path in ("synthesis.markdown", "synthesis"):
            with self.subTest(path=path):
                result = fixture_result(markdown=f"{INVENTED[:-1]} [{path}].\n")
                f = _d62(result)
                self.assertFalse(f.ok)
                self.assertIn("9,400 in", f.detail)
                self.assertIn(f"[{path}]", _d63(result).detail)


class TestTheFoundersWordsAreEvidence(unittest.TestCase):
    def test_a_number_the_founder_stated_is_not_an_invented_one(self):
        md = "Every figure uses $5.75 [economics.price_per_unit], not your stated $5.50.\n"
        self.assertFalse(_d62(fixture_result(markdown=md, venture=None)).ok)
        self.assertTrue(_d62(fixture_result(markdown=md, venture=VENTURE)).ok)

    def test_the_intake_record_counts_the_same_way(self):
        md = "Every figure uses $5.75 [economics.price_per_unit], not your stated $5.50.\n"
        result = fixture_result(markdown=md, venture=None)
        result["intake"] = {"facts": {"price": "$5.50 a drink"}, "confirmed": True}
        self.assertTrue(_d62(result).ok)


class TestTheRegistry(unittest.TestCase):
    def test_d62_is_a_fail_severity_gate_in_core(self):
        import gates
        inv = _gate("D62")
        self.assertEqual(inv.severity, "fail")
        self.assertIn("D62", gates.GATES["core"])
        self.assertIs(inv.check, gates.d62_synthesis_numbers_are_in_the_evidence_it_cites)

    def test_d63_is_a_warn_severity_gate_outside_core(self):
        import gates
        inv = _gate("D63")
        self.assertEqual(inv.severity, "warn")
        self.assertNotIn("D63", gates.GATES["core"])
        self.assertIn("D63", gates.GATES["all"])

    def test_the_table_counts_sixty_three_detectors(self):
        """The visible number. Appended after D60, because the order is historical."""
        import gates
        ids = [i.id for i in gates.INVARIANTS]
        self.assertEqual(len(ids), 63, ids)
        self.assertEqual(ids[-3:], ["D60", "D62", "D63"])

    def test_the_clean_fixture_of_the_other_gates_is_untouched(self):
        from test_gates import CLEAN_HTML, clean_result
        for det in ("D62", "D63"):
            self.assertIsNone(_gate(det).check(clean_result(), CLEAN_HTML).ok)


class TestTheVerifierWithholdsIt(unittest.TestCase):
    """A failing synthesis is a blocking finding like any other fail-severity gate, and the
    withheld page names the number."""

    def _verified(self, result: dict) -> dict:
        """Store the verdict the way plan.py does at the end of a run."""
        import report.verifier as verifier_mod
        vr = verifier_mod.verify_report(result)
        result["verification"] = {
            "status": verifier_mod.BLOCKED if not vr.publishable else verifier_mod.VERIFIED,
            "summary": vr.summary(),
            "findings": [{"invariant": f.invariant, "severity": f.severity,
                          "detail": f.detail, "audit_class": f.audit_class}
                         for f in vr.findings],
        }
        return result

    def test_the_invented_figure_is_a_block_finding(self):
        from report.verifier import Severity, verify_report
        vr = verify_report(fixture_result(markdown=fixture_markdown() + "\n\n" + INVENTED + "\n"))
        d62 = [f for f in vr.findings if f.invariant == "D62"]
        self.assertEqual(len(d62), 1, [f.invariant for f in vr.findings])
        self.assertEqual(d62[0].severity, Severity.BLOCK)
        self.assertIn("9,400", d62[0].detail)
        self.assertFalse(vr.publishable)

    def test_the_opus_report_adds_no_finding_of_its_own(self):
        from report.verifier import verify_report
        vr = verify_report(fixture_result())
        self.assertEqual([f.invariant for f in vr.findings if f.invariant in ("D62", "D63")], [])

    def test_the_withheld_page_shows_the_number(self):
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        import api
        result = self._verified(fixture_result(markdown=fixture_markdown() + "\n\n" + INVENTED + "\n"))
        from report.verifier import blocking_findings
        self.assertTrue(any(f["invariant"] == "D62" for f in blocking_findings(result)))
        job = {"kind": "plan", "state": "complete", "result": result}
        with patch.object(api.jobs, "get", return_value=job):
            r = TestClient(api.app).get("/jobs/j1/report.html")
        self.assertEqual(r.status_code, 409)
        self.assertIn("D62", r.text)
        self.assertIn("9,400", r.text)
        self.assertIn("Rent in the Mission", r.text)


if __name__ == "__main__":
    unittest.main()
