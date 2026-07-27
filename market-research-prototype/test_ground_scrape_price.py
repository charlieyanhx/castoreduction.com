"""ARPU basis priority: stated > scraped (real, origin=scrape) > LLM-modeled fallback."""
from __future__ import annotations
import unittest
from unittest.mock import patch
from tools import Evidence
from plan import ground_sizing_bottom_up

_SIZING = {"tam": {"method_top_down": {"value_usd": 1e9},
                   "method_analog": {"value_usd": 2e9}}}
_GB = Evidence("grounded_bottom_up", "skill_output", 1,
               payload={"tam_usd": 1.2e9, "establishments": 100000,
                        "figures": [{"formula": "100000 x $X", "source": "US Census CBP 2022"}]})


class TestScrapedArpuBasis(unittest.TestCase):
    def test_scraped_price_used_when_no_stated_price(self):
        scrape = Evidence("scrape_market_price", "skill_output", 3,
                          payload={"median_monthly_usd": 120.0, "source_label": "scraped from 2 pages"})
        cap = {}
        def fake_gb(**kw): cap.update(kw); return _GB
        with patch("skills.price_intel.scrape_market_price", return_value=scrape), \
             patch("skills.sizing.bottom_up.grounded_bottom_up", side_effect=fake_gb), \
             patch.dict("os.environ", {"CASTOR_SCRAPE_PRICE": "1"}):
            out = ground_sizing_bottom_up(_SIZING, "a CRM, no price mentioned",
                                          {"geography": "US", "target_customer": "crm software"})
        self.assertEqual(cap["annual_arpu"], 120.0 * 12)             # scraped price drove ARPU
        self.assertTrue(any("scraped" in n.lower() for n in out["notes"]))

    def test_stated_price_beats_scrape(self):
        scrape = Evidence("scrape_market_price", "skill_output", 3, payload={"median_monthly_usd": 120.0})
        cap = {}
        def fake_gb(**kw): cap.update(kw); return _GB
        with patch("skills.price_intel.scrape_market_price", return_value=scrape) as sp, \
             patch("skills.sizing.bottom_up.grounded_bottom_up", side_effect=fake_gb):
            ground_sizing_bottom_up(_SIZING, "a CRM at $99/month", {"geography": "US"})
        self.assertEqual(cap["annual_arpu"], 99.0 * 12)             # stated wins
        sp.assert_not_called()                                       # scrape skipped

    def test_falls_back_to_modeled_when_scrape_skeletons(self):
        skel = Evidence("scrape_market_price", "skill_output", 0, skeleton=True)
        cap = {}
        def fake_gb(**kw): cap.update(kw); return _GB
        with patch("skills.price_intel.scrape_market_price", return_value=skel), \
             patch("skills.sizing.bottom_up.grounded_bottom_up", side_effect=fake_gb), \
             patch.dict("os.environ", {"CASTOR_SCRAPE_PRICE": "1"}):
            # biz_kind is now load-bearing: the modeled price carries the VENTURE's unit,
            # so it may only be annualized for a genuinely monthly model. This test always
            # meant a subscription CRM — it just never had to say so before.
            ground_sizing_bottom_up(_SIZING, "a CRM, no price", {"geography": "US"},
                                    arpu_monthly_fallback=80.0,
                                    biz_kind="subscription")
        self.assertEqual(cap["annual_arpu"], 80.0 * 12)            # modeled fallback used


if __name__ == "__main__":
    unittest.main()
