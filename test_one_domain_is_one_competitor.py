"""Three competitors, one website. The whole competitive landscape was one company.

MEASURED on the report a user actually received (job d62bc04f, an orbital solar reflection
venture). `discover.synthesis.ranked_opportunities`:

    AetherMirror B2B     reflectorbital.com   score 6.3   DIRECT
    ReflectX Logistics   reflectorbital.com   score 6.3   DIRECT
    SunFleet Ops         reflectorbital.com   score 6.3   DIRECT

One real company (Reflect Orbital), three invented brand names, three identical scores —
counted as three competitors. And "3 competitors" is not a footnote: it is repeated in
Product, Price, Place, Promotion, the segment risk, and four separate citation entries. The
report tells the founder the market "contains exactly 3 competitors operating in this
infrastructure category" and prices against them.

WHY THE EXISTING GUARD MISSED IT. `sources.collapse_near_dupes` (W2-4) collapses near-
duplicate BRAND NAMES with RapidFuzz, and it runs at discover.py:1083 — where a candidate is
still only `{"name", "query_evidence"}`. **Domains are not resolved until enrichment, later**,
and nothing collapses after that. So the guard ran before the evidence existed, and the three
names are not fuzzy-similar to each other anyway: "AetherMirror B2B" vs "SunFleet Ops" scores
nowhere near the 92 threshold. Name similarity cannot detect this class at all.

A shared resolved domain is STRONGER evidence of identity than any name match — it is the
same website. That is the signal to collapse on, and it exists by the time enrichment
finishes.

WHERE IT GOES, and why that spot. Collapsing after the score sort means the strongest record
for a company survives, and it lands BEFORE three things that were all being inflated
together: `_density_counts` (which D16 already fixed once to count ranked competitors rather
than web-momentum hits — and which was then handed duplicates), the synthesis prompt (so the
LLM never sees one company three times and cannot rank it three times), and the fallback
roster. The full pre-collapse list is still kept at `result["steps"]["signals"]`, so no
evidence is destroyed — only the count stops lying.

DELIBERATELY NOT DONE: collapsing on name similarity across different domains. Two genuinely
different companies can have similar names, and this codebase has already been burned by an
over-eager identity guess (the OSM tag fallback that benchmarked a bakery against
restaurants). A shared domain is a fact; a similar name is a hunch.
"""
from __future__ import annotations

import unittest


class TestCollapseByDomain(unittest.TestCase):
    def test_the_measured_case_three_names_one_site(self):
        from sources import collapse_by_domain
        rows = [
            {"brand": "AetherMirror B2B", "domain": "reflectorbital.com", "_score": 6.3},
            {"brand": "ReflectX Logistics", "domain": "reflectorbital.com", "_score": 6.3},
            {"brand": "SunFleet Ops", "domain": "reflectorbital.com", "_score": 6.3},
        ]
        out = collapse_by_domain(rows)
        self.assertEqual(len(out), 1,
                         f"three brand names on one website are still {len(out)} competitors")
        self.assertEqual(out[0]["brand"], "AetherMirror B2B", "first occurrence must win")

    def test_genuinely_different_companies_survive(self):
        from sources import collapse_by_domain
        rows = [{"brand": "Planet Labs", "domain": "planet.com"},
                {"brand": "Spire", "domain": "spire.com"},
                {"brand": "Iceye", "domain": "iceye.com"}]
        self.assertEqual(len(collapse_by_domain(rows)), 3)

    def test_www_and_subdomains_are_the_same_company(self):
        from sources import collapse_by_domain
        rows = [{"brand": "Reflect", "domain": "reflectorbital.com"},
                {"brand": "Reflect Ops", "domain": "www.reflectorbital.com"},
                {"brand": "Reflect Labs", "domain": "app.reflectorbital.com"}]
        self.assertEqual(len(collapse_by_domain(rows)), 1)

    def test_records_without_a_domain_are_all_kept(self):
        """No domain is no evidence. Collapsing these would silently delete competitors
        whose site simply failed to resolve — the opposite failure, and a worse one."""
        from sources import collapse_by_domain
        rows = [{"brand": "A", "domain": None}, {"brand": "B", "domain": ""},
                {"brand": "C"}]
        self.assertEqual(len(collapse_by_domain(rows)), 3)

    def test_a_domainless_record_does_not_collapse_into_a_domained_one(self):
        from sources import collapse_by_domain
        rows = [{"brand": "A", "domain": "x.com"}, {"brand": "B", "domain": None}]
        self.assertEqual(len(collapse_by_domain(rows)), 2)

    def test_it_reads_the_domain_under_either_key(self):
        """Enriched records carry `domain`; some upstream shapes carry `final_url`."""
        from sources import collapse_by_domain
        rows = [{"brand": "A", "domain": "x.com"},
                {"brand": "B", "final_url": "https://x.com/pricing"}]
        self.assertEqual(len(collapse_by_domain(rows)), 1)

    def test_empty_and_non_dict_input_do_not_raise(self):
        from sources import collapse_by_domain
        self.assertEqual(collapse_by_domain([]), [])
        self.assertEqual(len(collapse_by_domain(["a string", {"brand": "B"}])), 2)


class TestDiscoverCollapsesAfterDomainsExist(unittest.TestCase):
    """The fix that matters. A helper nobody calls at the right moment fixes nothing —
    the existing name-collapse ran before domains were resolved, which is exactly why this
    shipped."""

    def test_discover_collapses_the_enriched_roster(self):
        import inspect

        import discover
        src = inspect.getsource(discover)
        self.assertIn("collapse_by_domain", src,
                      "discover.py never collapses on domain, so three names for one "
                      "company still reach the synthesis prompt and the density count")

    def test_it_happens_after_enrichment_not_before(self):
        """Before enrichment there is no domain to collapse on — that is the whole bug."""
        import inspect

        import discover
        src = inspect.getsource(discover)
        i_enrich = src.find('result["steps"]["signals"] = enriched')
        i_collapse = src.find("collapse_by_domain")
        self.assertGreater(i_enrich, 0, "the enrichment landmark moved; re-point this test")
        self.assertGreater(i_collapse, i_enrich,
                           "collapse_by_domain runs BEFORE domains are resolved — the same "
                           "mistake collapse_near_dupes made")

    def test_the_full_roster_is_still_kept_as_evidence(self):
        """Collapsing the COUNT must not destroy the record of what was discovered."""
        import inspect

        import discover
        src = inspect.getsource(discover)
        self.assertIn('result["steps"]["signals"] = enriched', src,
                      "the pre-collapse roster is no longer retained for provenance")


class TestTheDensityCountIsCompanies(unittest.TestCase):
    def test_density_counts_one_company_once(self):
        from discover import _density_counts
        from sources import collapse_by_domain
        rows = [{"brand": "AetherMirror B2B", "domain": "reflectorbital.com", "_score": 30},
                {"brand": "ReflectX Logistics", "domain": "reflectorbital.com", "_score": 30},
                {"brand": "SunFleet Ops", "domain": "reflectorbital.com", "_score": 30}]
        density, active = _density_counts(collapse_by_domain(rows))
        self.assertEqual(density, 1, "competitor_density counted one company three times")
        self.assertEqual(active, 1)


if __name__ == "__main__":
    unittest.main()
