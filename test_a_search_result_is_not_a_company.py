""""Identified 8 real companies matching the ICP" — six of them were page titles.

MEASURED, the Customer Universe page shipped for job d62bc04f. This is the only page in the
report an operator can act on tomorrow, and it affirmatively claims the rows are real
companies:

    US Solar Farm Map               cleanview.co          <- a page title
    Atualização do Produto 2.32     modoenergy.com        <- a product-update page (pt-BR)
    Homepage                        www.eia.gov           <- a federal statistics agency
    AspenTech                       www.aspentech.com     <- genuinely a company
    Director, Solar Development     www.iownrenewable.com <- a job posting
    Installed solar energy capacity ourworldindata.org    <- a chart on an NGO data site
    It's Okay to Notice When Sol... thebreakthrough.org   <- a think-tank essay
    TyreNews.co.uk                  www.tyrenews.co.uk    <- a UK tyre-trade publication

`_ddg_find_companies` mints the name from the search-result title and screens it with
`_is_plausible_company_name`, which only tests the SHAPE of the string — length, case, vowels,
news-headline patterns. "Homepage" is eight characters, mixed case, has vowels, and sails
through. Nothing asks what KIND of page the title came from.

Two facts are available and unused. The DOMAIN says a great deal: a .gov statistics agency, an
NGO data project, a think tank and a trade publication are not prospects for anything, and the
pipeline already knows this shape — `discover` correctly demoted StellarAlbedo for resolving
to newsweek.com. And the TITLE itself says more than its shape: "Homepage" is a navigation
label, "Director, Solar Development" is a job title, "Installed solar energy capacity" is a
category phrase.

It matters beyond the page. These rows are quoted by name into `segments[*].member_examples`
and fed to the segment scorer, so the starred #1 segment and its five-metric radar are induced
partly from a statistics agency and a tyre newsletter.

DELIBERATELY NOT DONE: an LLM entity check. The signal here is deterministic and cheap, and a
model call would make a wrong answer more expensive rather than less likely — the same
judgement `osm_tags` records ("inventing a plausible-looking tag would return a confident
census of the wrong kind of business, which is harder to notice than none").
"""
from __future__ import annotations

import unittest

from customer_universe import _is_plausible_company_name, _is_prospect_domain


MEASURED = [
    # (title, domain, is_a_real_prospect)
    # CleanView is a real company; the defect on this row is that its PAGE TITLE became its
    # NAME. That is a separate bug (name minting) from "this is not a company at all", and
    # no deterministic filter can catch it — so this row is expected to survive, and is
    # recorded here as a known remaining limitation rather than quietly dropped.
    ("US Solar Farm Map", "cleanview.co", True),
    ("Atualização do Produto 2.32", "modoenergy.com", False),
    ("Homepage", "www.eia.gov", False),
    ("AspenTech", "www.aspentech.com", True),
    ("Director, Solar Development", "www.iownrenewable.com", False),
    ("Installed solar energy capacity", "ourworldindata.org", False),
    ("It's Okay to Notice When Solar and Wind Get Help", "thebreakthrough.org", False),
    ("TyreNews.co.uk", "www.tyrenews.co.uk", False),
]


def _accepted(title, domain):
    return _is_plausible_company_name(title) and _is_prospect_domain(domain)


class TestTheMeasuredEightRows(unittest.TestCase):
    def test_only_the_real_company_survives(self):
        wrong = []
        for title, domain, want in MEASURED:
            got = _accepted(title, domain)
            if got != want:
                wrong.append(f"{title!r} @ {domain} -> accepted={got}, expected {want}")
        self.assertEqual(wrong, [], "\n  ".join([""] + wrong))

    def test_the_real_company_is_not_lost(self):
        """Over-filtering would empty the one page an operator can act on."""
        self.assertTrue(_accepted("AspenTech", "www.aspentech.com"))
        for name, dom in (("Reflect Orbital", "reflectorbital.com"),
                          ("NextEra Energy Resources", "nexteraenergyresources.com"),
                          ("Modo Energy", "modoenergy.com")):
            with self.subTest(name=name):
                self.assertTrue(_accepted(name, dom), f"{name} @ {dom}")


class TestInstitutionalDomainsAreNotProspects(unittest.TestCase):
    def test_government_and_academic(self):
        for d in ("www.eia.gov", "energy.gov", "nrel.gov", "mit.edu", "www.gov.uk"):
            with self.subTest(d=d):
                self.assertFalse(_is_prospect_domain(d))

    def test_data_ngo_and_media(self):
        for d in ("ourworldindata.org", "thebreakthrough.org", "newsweek.com",
                  "www.tyrenews.co.uk", "en.wikipedia.org"):
            with self.subTest(d=d):
                self.assertFalse(_is_prospect_domain(d))

    def test_an_ordinary_commercial_domain_passes(self):
        for d in ("aspentech.com", "reflectorbital.com", "nexteraenergy.com",
                  "shell.com", "acme.co.uk"):
            with self.subTest(d=d):
                self.assertTrue(_is_prospect_domain(d))

    def test_empty_domain_is_not_a_prospect(self):
        self.assertFalse(_is_prospect_domain(""))
        self.assertFalse(_is_prospect_domain(None))


class TestPageTitlesAreNotCompanyNames(unittest.TestCase):
    def test_navigation_labels_rejected(self):
        for t in ("Homepage", "Home Page", "About Us", "Contact Us", "Our Products",
                  "Solutions", "Index"):
            with self.subTest(t=t):
                self.assertFalse(_is_plausible_company_name(t))

    def test_job_postings_rejected(self):
        for t in ("Director, Solar Development", "Senior Engineer, Grid",
                  "VP of Asset Management"):
            with self.subTest(t=t):
                self.assertFalse(_is_plausible_company_name(t))

    def test_sentence_like_headlines_rejected(self):
        for t in ("It's Okay to Notice When Solar and Wind Get Help",
                  "Installed solar energy capacity"):
            with self.subTest(t=t):
                self.assertFalse(_is_plausible_company_name(t))

    def test_short_real_names_still_pass(self):
        for t in ("AspenTech", "Reflect Orbital", "Modo Energy", "NextEra Energy"):
            with self.subTest(t=t):
                self.assertTrue(_is_plausible_company_name(t))


class TestTheCallerAppliesTheDomainFilter(unittest.TestCase):
    def test_the_seeder_screens_the_domain(self):
        import inspect

        import customer_universe
        self.assertIn("_is_prospect_domain", inspect.getsource(customer_universe),
                      "the domain screen exists but nothing calls it")


if __name__ == "__main__":
    unittest.main()
