"""A syndicated market-research storefront carries the same weight as a federal filing.

MEASURED, across the full public corpora of both competitors (31 reports, 5,151 citations,
1,247 domains, censused 2026-08-19/20). Their sizing rests on a source tier nothing in
either pipeline distinguishes:

    dimeadozen: SAM fails to derive from its own stated TAM in 12 of 16 reports (75%);
                14 of 14 reports that state a TAM twice contradict themselves (up to 9.4x);
                grandviewresearch.com supplies the TAM or SAM in 9 reports and 403s bots,
                so no reader can check the one number the report is built on.
    preuve:     every headline TAM rests on <=4 source links (median 2); one report
                publishes a $50B TAM on ZERO market-research sources; another's source
                field reads "Mordor Intelligence / Prompt Ground Truth".

The load-bearing figures trace to deepmarketinsights, dataintelo, emergenresearch,
wantstats, mmrstatistics — long-tail storefronts that republish each other. The citation
RESOLVES, so a frequency audit scores it a hit. It is the authority that is missing, and
neither of them measures authority at all.

Castor has the same hole today. `Method.source` (report/forecast.py) is free text —
"Gartner Digital Wellness 2024", "BLS QCEW / Census SUSB", or a bare domain — and
`triangulate` counts independent ORIGINS without ever asking what KIND of source each
origin is. docs/TRIANGULATION.md already names the consequence: three draws that agree
are not corroboration when the paths are not methodologically independent.

This pins the classifier that makes authority a first-class property. It is deliberately
a LOOKUP, not a judgement: a curated domain roster harvested from the competitor corpora,
plus the government/filing hosts we already fetch. An unrecognised source is UNKNOWN and
stays unknown — silently promoting it is the failure mode this exists to prevent.

DELIBERATELY NOT DONE: scoring source quality with an LLM, and auto-rejecting content
mills at fetch time. The tier is DECLARED here; what each caller does with it is that
caller's decision, made explicit at the call site.
"""
from __future__ import annotations

import source_tiers as st


class TestTheRosterIsALookupNotAGuess:
    def test_a_federal_filing_host_is_primary(self):
        for src in ("https://www.sec.gov/Archives/edgar/data/1400118/tm237052-9_s1.htm",
                    "sec.gov", "https://www.fda.gov/news-events", "data.census.gov",
                    "https://www.bls.gov/cex/", "federalreserve.gov"):
            assert st.classify(src) is st.Tier.PRIMARY, src

    def test_an_issuer_investor_relations_subdomain_is_primary(self):
        """A comparable's own filing is the analog path's real anchor (TRIANGULATION.md)."""
        assert st.classify("https://ir.hellofreshgroup.com/annual-report-2020.pdf") is st.Tier.PRIMARY

    def test_a_content_mill_is_its_own_tier_not_research(self):
        """These are exactly the domains the competitors' headline TAMs trace to."""
        for src in ("deepmarketinsights.com", "https://dataintelo.com/report/x",
                    "emergenresearch.com", "wantstats.com", "mmrstatistics.com",
                    "verifiedmarketresearch.com", "precedenceresearch.com",
                    "futuremarketinsights.com", "imarcgroup.com"):
            assert st.classify(src) is st.Tier.CONTENT_MILL, src

    def test_a_named_research_house_is_research_not_primary(self):
        """Usable as ONE triangulation path. Never mistaken for a filing."""
        for src in ("grandviewresearch.com", "Gartner Digital Wellness 2024",
                    "mordorintelligence.com", "statista.com", "IDC"):
            assert st.classify(src) is st.Tier.RESEARCH, src

    def test_vendor_documentation_is_padding(self):
        """32.4% of dimeadozen's 4,087 citations. figma.com appears in 17 of 17 reports."""
        for src in ("https://figma.com/", "aws.amazon.com", "stripe.com",
                    "https://nextjs.org/docs", "sentry.io", "postgresql.org"):
            assert st.classify(src) is st.Tier.PADDING, src

    def test_a_community_platform_is_voice(self):
        for src in ("reddit.com", "https://news.ycombinator.com/item?id=44739556",
                    "indiehackers.com", "trustpilot.com"):
            assert st.classify(src) is st.Tier.COMMUNITY, src

    def test_an_unrecognised_source_stays_unknown(self):
        """The whole point. Promotion-by-default is what lets a content mill pass."""
        for src in ("https://some-blog-nobody-audited.example/post", "",
                    "estimated from category norms", None):
            assert st.classify(src) is st.Tier.UNKNOWN, src


    def test_reported_journalism_is_press_and_cannot_size_a_market(self):
        """Press corroborates that an EVENT happened. It is not a market estimate."""
        for src in ("techcrunch.com", "https://www.cnbc.com/2026/02/09/housing.html",
                    "reuters.com", "finextra.com"):
            assert st.classify(src) is st.Tier.PRESS, src
        assert st.can_anchor_sizing(st.Tier.PRESS) is False
        assert st.is_market_evidence(st.Tier.PRESS) is True

    def test_a_free_text_citation_still_classifies(self):
        """`Method.source` is prose, not a URL — the roster has to reach it anyway."""
        assert st.classify("BLS QCEW / Census SUSB") is st.Tier.PRIMARY
        assert st.classify("Mordor Intelligence / Prompt Ground Truth") is st.Tier.RESEARCH

    def test_a_press_release_wire_is_not_an_independent_market_source(self):
        """A wire reprints what an issuer wrote; it adds distribution, not verification."""
        for src in ("prnewswire.com", "businesswire.com", "globenewswire.com"):
            assert st.can_anchor_sizing(st.classify(src)) is False, src

    def test_a_substring_does_not_steal_an_identity(self):
        """`test_a_substring_is_not_an_identity` already pinned this class of bug."""
        assert st.classify("https://notsec.gov.phishing.example/x") is not st.Tier.PRIMARY
        assert st.classify("https://mystripe.com.example/") is not st.Tier.PADDING


class TestWhatTheTierEntitlesASourceToDo:
    def test_only_primary_and_research_can_anchor_a_market_figure(self):
        assert st.can_anchor_sizing(st.Tier.PRIMARY) is True
        assert st.can_anchor_sizing(st.Tier.RESEARCH) is True
        for t in (st.Tier.CONTENT_MILL, st.Tier.PADDING, st.Tier.COMMUNITY, st.Tier.UNKNOWN):
            assert st.can_anchor_sizing(t) is False, t

    def test_padding_and_community_are_not_market_evidence(self):
        """A design tool cited in a sizing section is not evidence about a market."""
        assert st.is_market_evidence(st.Tier.PADDING) is False
        assert st.is_market_evidence(st.Tier.COMMUNITY) is False
        assert st.is_market_evidence(st.Tier.PRIMARY) is True

    def test_a_content_mill_is_never_a_sole_anchor_even_when_it_resolves(self):
        """The competitor failure in one assertion: the URL is live, the authority is not."""
        assert st.can_anchor_sizing(st.classify("deepmarketinsights.com")) is False
