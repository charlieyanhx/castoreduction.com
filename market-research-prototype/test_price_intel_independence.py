"""
Audit high #6 — an aggregator median was passed off as independent scraped pricing.

`scrape_market_price` searches for competitor pricing pages, drops aggregators
(G2/Capterra/TrustRadius/…) because their pages list MANY vendors' prices, and takes the
median of what it scrapes. The filter result was consumed as:

    real = (filt.payload or rows)

An empty filtered list is falsy, so "every hit was an aggregator" — the case the filter
exists to catch — fell back to the UNFILTERED rows. The aggregator pages then got scraped
and their mixed multi-vendor prices returned as a median tagged `origin="scrape"`, which is
what the bottom-up TAM leans on as an independent origin for triangulation.

Latent on the corpus, measured: 0/16 reports carry `median_monthly_usd` at all, 0/16 carry
`"origin": "scrape"`, and 0/16 have a multi-origin triangulation — 10 report exactly one
independent origin and 6 never got that far. So this protects the independence claim rather
than correcting a shipped figure.

THE SECOND HALF IS MANDATORY. Returning the skeleton instead of an aggregator median sends
`ground_sizing_bottom_up` to its next ARPU basis — `arpu_monthly_fallback`, the PSM optimal
price — and that price carries the VENTURE's unit, not months. For a per-unit venture the
grounding then computes `arpu_monthly * 12` on a per-unit price and tags the result
`data_origin="census"`: a fabricated annual subscription wearing real Census provenance.
Corpus shape 800c261b (superconducting tape for grid operators, ecommerce, PSM optimal
$125,000 per *unit*) is exactly this. So the fix that stops trusting aggregators must also
stop annualizing a non-monthly basis — otherwise it trades a weak number for a wrong one.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.registry import Evidence


def _hits(*urls):
    return Evidence(source="web_search", category="scrape", count=len(urls),
                    payload=[{"url": u, "title": u} for u in urls])


class TestFilteredToEmptyIsNotAFallback(unittest.TestCase):
    def test_all_aggregators_returns_a_skeleton_not_a_scraped_median(self):
        import skills.price_intel as P
        with patch.object(P, "web_search", return_value=_hits(
                "https://www.g2.com/categories/crm", "https://www.capterra.com/crm/")), \
             patch.object(P, "filter_aggregator_domains",
                          return_value=Evidence("filter", "scrape", 0, payload=[])), \
             patch.object(P, "_prices_from_page",
                          side_effect=AssertionError("aggregator page was scraped")):
            ev = P.scrape_market_price("crm", "US")
        self.assertTrue(ev.skeleton)
        self.assertIsNone((ev.payload or {}).get("median_monthly_usd"))
        self.assertIn("aggregator", (ev.error or "").lower())

    def test_a_filter_that_could_not_run_still_permits_the_raw_rows(self):
        """"Filtered to empty" and "filter unavailable" are different facts. Only the
        second may fall back — otherwise a broken filter silently kills all pricing."""
        import skills.price_intel as P
        with patch.object(P, "web_search", return_value=_hits("https://acme.com/pricing")), \
             patch.object(P, "filter_aggregator_domains",
                          return_value=Evidence("filter", "scrape", 0, payload=None,
                                                skeleton=True, error="embedder down")), \
             patch.object(P, "_prices_from_page",
                          return_value=[{"value": 49.0, "source": "https://acme.com/pricing"}]):
            ev = P.scrape_market_price("crm", "US")
        self.assertFalse(ev.skeleton)
        self.assertEqual(ev.payload["median_monthly_usd"], 49.0)

    def test_surviving_hits_are_scraped_normally(self):
        import skills.price_intel as P
        with patch.object(P, "web_search", return_value=_hits(
                "https://www.g2.com/x", "https://acme.com/pricing")), \
             patch.object(P, "filter_aggregator_domains",
                          return_value=Evidence("filter", "scrape", 1, payload=[
                              {"url": "https://acme.com/pricing"}])), \
             patch.object(P, "_prices_from_page",
                          return_value=[{"value": 30.0, "source": "https://acme.com/pricing"}]):
            ev = P.scrape_market_price("crm", "US")
        self.assertEqual(ev.payload["median_monthly_usd"], 30.0)
        self.assertEqual(ev.payload["origin"], "scrape")


class TestTheGroundingRefusesANonMonthlyBasis(unittest.TestCase):
    """The guard that makes the skeleton safe. Only a genuinely monthly basis may be
    annualized: a stated $/mo, or a scraped price (price_intel keeps only recurring
    $5–$5,000/month values). The PSM optimal price carries the venture's own unit."""

    def _ground(self, **kw):
        import plan
        with patch.object(plan, "extract_stated_price", return_value=None), \
             patch.dict("os.environ", {"CASTOR_SCRAPE_PRICE": "0"}):
            return plan.ground_sizing_bottom_up(
                {"tam": {"mid": 1e9, "method_top_down": {"value_usd": 1e9}}},
                "superconducting tape for grid operators", {}, **kw)

    def test_a_per_unit_psm_price_does_not_ground_the_bottom_up(self):
        """$125,000 per unit x 12 is not an annual contract value. Grounding on it would
        publish a fabricated figure tagged data_origin='census'."""
        out = self._ground(arpu_monthly_fallback=125_000.0, biz_kind="ecommerce")
        self.assertNotIn("method_bottom_up", out.get("tam") or {},
                         "a per-unit price was annualized as monthly recurring")

    def test_a_subscription_psm_price_still_grounds(self):
        """The basis IS monthly for a subscription, so the existing behaviour stands."""
        import plan
        called = {}

        def _fake_bottom_up(annual_arpu, category):
            called["annual_arpu"] = annual_arpu
            return Evidence("grounded_bottom_up", "skill_output", 1, payload={
                "tam_usd": 5e8, "establishments": 3100,
                "figures": [{"formula": "3,100 x $1,188", "source": "US Census CBP"}]})

        with patch.object(plan, "extract_stated_price", return_value=None), \
             patch.dict("os.environ", {"CASTOR_SCRAPE_PRICE": "0"}), \
             patch("skills.sizing.bottom_up.grounded_bottom_up", _fake_bottom_up):
            out = plan.ground_sizing_bottom_up(
                {"tam": {"mid": 1e9, "method_top_down": {"value_usd": 1e9}}},
                "team analytics saas", {}, arpu_monthly_fallback=99.0,
                biz_kind="subscription")
        self.assertEqual(called["annual_arpu"], 99.0 * 12)
        # The grounding still fires — only the LABEL changed. A real Census count times a
        # MODELLED price is not a fetched figure, and data_origin is what triangulation
        # reads to decide which estimates are independent, so an LLM-priced product must
        # not claim 'census' and manufacture a second origin from the same model draw.
        # count_origin still credits the real establishment count.
        _bu = out["tam"]["method_bottom_up"] or {}
        self.assertEqual(_bu["data_origin"], "llm")
        self.assertEqual(_bu["count_origin"], "census")

    def test_an_unknown_business_model_is_not_assumed_monthly(self):
        """Absent a kind, the safe reading of a modeled price is 'unit unknown'."""
        out = self._ground(arpu_monthly_fallback=125_000.0, biz_kind=None)
        self.assertNotIn("method_bottom_up", out.get("tam") or {})

    def test_a_stated_monthly_price_is_unaffected_by_the_guard(self):
        """A price extracted as $/mo is monthly by construction, whatever the model."""
        import plan
        called = {}

        def _fake_bottom_up(annual_arpu, category):
            called["annual_arpu"] = annual_arpu
            return Evidence("grounded_bottom_up", "skill_output", 1, payload={
                "tam_usd": 5e8, "establishments": 100,
                "figures": [{"formula": "f", "source": "US Census CBP"}]})

        with patch.object(plan, "extract_stated_price", return_value=50.0), \
             patch.dict("os.environ", {"CASTOR_SCRAPE_PRICE": "0"}), \
             patch("skills.sizing.bottom_up.grounded_bottom_up", _fake_bottom_up):
            plan.ground_sizing_bottom_up(
                {"tam": {"mid": 1e9}}, "anything", {}, biz_kind="ecommerce")
        self.assertEqual(called["annual_arpu"], 50.0 * 12)

    def test_the_refusal_is_disclosed_in_the_notes(self):
        """Silently declining to ground is how a gap becomes invisible."""
        out = self._ground(arpu_monthly_fallback=125_000.0, biz_kind="ecommerce")
        self.assertTrue(any("per-unit" in n.lower() or "not monthly" in n.lower()
                            for n in (out.get("notes") or [])),
                        f"no disclosure of the refusal: {out.get('notes')}")


class TestSourceHostsAreExposed(unittest.TestCase):
    def test_the_payload_names_the_hosts_the_median_came_from(self):
        """A downstream independence claim needs to know WHICH hosts contributed, not just
        how many — one owner of that list, computed where the prices are."""
        import skills.price_intel as P
        with patch.object(P, "web_search", return_value=_hits("https://acme.com/pricing")), \
             patch.object(P, "filter_aggregator_domains",
                          return_value=Evidence("filter", "scrape", 1, payload=[
                              {"url": "https://acme.com/pricing"}])), \
             patch.object(P, "_prices_from_page", return_value=[
                 {"value": 20.0, "source": "https://acme.com/pricing"},
                 {"value": 40.0, "source": "https://beta.io/plans"}]):
            ev = P.scrape_market_price("crm", "US")
        self.assertEqual(ev.payload["source_hosts"], ["acme.com", "beta.io"])

    def test_an_unparseable_source_does_not_raise(self):
        import skills.price_intel as P
        with patch.object(P, "web_search", return_value=_hits("https://acme.com/pricing")), \
             patch.object(P, "filter_aggregator_domains",
                          return_value=Evidence("filter", "scrape", 1, payload=[
                              {"url": "https://acme.com/pricing"}])), \
             patch.object(P, "_prices_from_page", return_value=[
                 {"value": 20.0, "source": ""}, {"value": 40.0, "source": None}]):
            ev = P.scrape_market_price("crm", "US")
        self.assertEqual(ev.payload["source_hosts"], [])


if __name__ == "__main__":
    unittest.main()
