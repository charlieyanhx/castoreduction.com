"""R1 (88b416f6 audit): a shared platform host can never be one brand's identity.

MEASURED: Dify.ai was rostered with domain github.com. Five surfaces then described
GitHub instead of Dify: both personas were decoded from github.com's 60 Trustpilot
reviews, "Dify's low customer satisfaction" is github.com's 1.93 stars, the momentum
score and 2007 domain age are GitHub's public footprint, and firmographics guessed
the org "github" ("built on Go"). Glean separately matched an unrelated 147-star org
"glean" ("built on Ruby") because org acceptance had no link-back verification.

Contract pinned here: hosts that host MANY companies' content (github, gitlab, app
stores, site builders) are refused as identity domains at every consumer — resolver,
aggregator filter, taste decode, firmographics — and D28 fails a roster that carries
one. Firmographics accepts a GitHub org only when the org links back to the brand's
own domain (null-beats-wrong, the module's stated contract). No network here.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock


class TestSharedHostConstant(unittest.TestCase):
    def test_the_measured_offenders_are_listed(self):
        from scrape.search import is_shared_platform_host
        for h in ("github.com", "gitlab.com", "bitbucket.org", "sourceforge.net",
                  "apps.apple.com", "play.google.com", "npmjs.com", "pypi.org",
                  "wordpress.com", "wixsite.com", "notion.site", "github.io"):
            self.assertTrue(is_shared_platform_host(h), h)
        self.assertTrue(is_shared_platform_host("www.github.com"), "www prefix")
        self.assertFalse(is_shared_platform_host("dify.ai"))
        self.assertFalse(is_shared_platform_host("glean.com"))


class TestResolverRefusesSharedHosts(unittest.TestCase):
    def test_github_result_is_skipped_for_the_next_organic_hit(self):
        import sources
        html = """<html><body>
        <a href="https://github.com/langgenius/dify">Dify GitHub</a>
        <a href="https://dify.ai/">Dify official</a>
        </body></html>""" + "x" * 5000
        fake = MagicMock(status_code=200, text=html)
        with patch("cache.get", return_value=None), patch("cache.put"), \
             patch.object(sources.mrp_http, "post", return_value=fake):
            got = sources.resolve_brand_domain("Dify.ai", "RAG platform")
        self.assertEqual(got, "dify.ai")

    def test_a_pre_fix_poisoned_cache_entry_is_refused_not_served(self):
        """The Dify->github.com binding was already CACHED when the guard shipped;
        a guard inside the cached body would serve the poison for its whole TTL."""
        import sources
        with patch("cache.get", return_value="github.com"):
            got = sources.resolve_brand_domain("Dify.ai", "RAG platform")
        self.assertIsNone(got)


class TestAggregatorFilterStripsSharedHosts(unittest.TestCase):
    def test_github_urls_are_not_companies(self):
        from scrape.search import filter_aggregator_domains
        kept = filter_aggregator_domains([
            {"url": "https://github.com/langgenius/dify", "title": "x"},
            {"url": "https://dify.ai/", "title": "y"},
        ])
        self.assertEqual([r["url"] for r in kept], ["https://dify.ai/"])


class TestTasteRefusesSharedHostDomains(unittest.TestCase):
    def test_decode_treats_github_domain_as_no_domain(self):
        """The 60 Trustpilot reviews of github.com must never count as Dify's voice."""
        import taste
        with patch.object(taste, "trustpilot_reviews", return_value=[]) as tp, \
             patch.object(taste, "reddit_search", return_value=([], None)), \
             patch.object(taste, "search_review_articles", return_value=[]), \
             patch.object(taste, "hackernews_mentions", return_value=[]), \
             patch.object(taste, "scrape_homepage_testimonials", return_value=[]):
            out = taste.decode_taste("Dify.ai", "github.com")
        tp.assert_not_called()
        self.assertTrue(out.get("cannot_decode"), str(out)[:200])


class TestFirmographicsOrgVerification(unittest.TestCase):
    def _org_api(self, blog):
        def fake_get(url, **kw):
            r = MagicMock()
            if url.endswith("/repos"):
                r.status_code = 200
                r.json.return_value = [{"language": "Ruby", "stargazers_count": 10}]
            else:
                r.status_code = 200
                r.json.return_value = {"html_url": url, "blog": blog,
                                       "public_repos": 10}
            return r
        return fake_get

    def test_an_org_without_linkback_is_rejected(self):
        # MEASURED: org 'glean' (147 stars, Ruby) is not Glean the company; its blog
        # does not point at glean.com, so null-beats-wrong applies.
        import firmographics
        with patch.object(firmographics, "cache_get", return_value=None), \
             patch.object(firmographics, "cache_put"), \
             patch.object(firmographics.requests, "get",
                          side_effect=self._org_api(blog="https://someoneelse.dev")):
            got = firmographics._github_org("glean.com", "Glean")
        self.assertEqual(got, {})

    def test_an_org_linking_back_to_the_brand_domain_is_accepted(self):
        import firmographics
        with patch.object(firmographics, "cache_get", return_value=None), \
             patch.object(firmographics, "cache_put"), \
             patch.object(firmographics.requests, "get",
                          side_effect=self._org_api(blog="https://dify.ai")):
            got = firmographics._github_org("dify.ai", "Dify")
        self.assertTrue(got.get("github_org"))

    def test_a_shared_host_domain_contributes_no_org_candidate(self):
        # domain.split('.')[0] of github.com produced the org guess 'github'.
        import firmographics
        calls = []
        def spy_get(url, **kw):
            calls.append(url)
            r = MagicMock(); r.status_code = 404
            return r
        with patch.object(firmographics, "cache_get", return_value=None), \
             patch.object(firmographics, "cache_put"), \
             patch.object(firmographics.requests, "get", side_effect=spy_get):
            firmographics._github_org("github.com", "Dify.ai")
        self.assertFalse(any("/orgs/github" == c.rsplit("com", 1)[-1].rstrip("/")
                             or c.endswith("/orgs/github") for c in calls),
                         f"the shared host leaked an org candidate: {calls}")


class TestD28FlagsSharedHostRoster(unittest.TestCase):
    def test_a_roster_entry_with_github_domain_fails_identity(self):
        from gates import d28_domain_identity_verified
        r = {"discover": {"synthesis": {"ranked_opportunities": [
            {"brand": "Dify.ai", "domain": "github.com", "rank": 2},
            {"brand": "Glean", "domain": "glean.com", "rank": 3},
        ]}}}
        f = d28_domain_identity_verified(r, None)
        self.assertFalse(f.ok)
        self.assertIn("github.com", f.detail)


if __name__ == "__main__":
    unittest.main()
