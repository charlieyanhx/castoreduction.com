"""The paywall only ever fired at people who had already had free reports.

THE BUG WAS THE SEAM ITSELF. survey.js carried a comment reading "THE PAYWALL SEAM ...
Insert the checkout between these two lines", and nothing was ever inserted. The only
place the product asked for money was blocked(): the handler for a 429 from the daily
run cap. So the funnel was: use your free runs, and only when you run out does anyone
mention a price. Every first-time visitor got a full report, free, and was never asked.

Now the charge happens BEFORE the run, which is also the only place it can honestly
happen: the run is six minutes of metered research, and a product that does the work
first and asks after has already spent the money.

Three rules, and they live on the server so the browser cannot disagree with them:

  a credit in hand      the run proceeds, no gate
  no credit, can sell   the gate, before /plan is ever called
  no processor wired    no gate, because a wall with no till behind it is a dead end

The third is not a nicety. Without it, every developer install and every instance whose
operator has not finished setting up Stripe ends the survey at a button that does nothing.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path


class _Env(unittest.TestCase):
    """Each case gets its own database and a clean set of Stripe variables."""

    KEYS = ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_REPORT",
            "STRIPE_PRICE_BUNDLE5", "STRIPE_PRICE_BUNDLE10", "CASTOR_PAYWALL_PREVIEW")

    def setUp(self):
        self._old = {k: os.environ.get(k) for k in self.KEYS + ("JOBS_DB_PATH",)}
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        for k in self.KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    @staticmethod
    def _sell():
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_x"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_x"


class TheGateAsksOnlyWhenItCanTakeMoney(_Env):
    def test_an_instance_with_no_processor_never_walls_anyone_off(self):
        import api
        self.assertFalse(api._needs_purchase("guest-nobody"))

    def test_an_instance_that_can_sell_asks_before_the_run(self):
        import api
        self._sell()
        self.assertTrue(api._needs_purchase("guest-nobody"))

    def test_a_credit_in_hand_is_not_asked_for_twice(self):
        import api
        import billing
        self._sell()
        billing._record("guest-paid", "report", 1, None, None)
        self.assertFalse(api._needs_purchase("guest-paid"))

    def test_spending_the_credit_brings_the_gate_back(self):
        import api
        import billing
        self._sell()
        billing._record("guest-paid", "report", 1, None, None)
        self.assertTrue(billing.consume("guest-paid", "report"))
        self.assertTrue(api._needs_purchase("guest-paid"))


class TheGateNeverOffersWhatItCannotSell(_Env):
    """A Buy button that 402s is worse than no button, and the survey draws whatever this
    list contains."""

    def _status(self):
        import api
        with mock.patch.object(api, "_current_owner", lambda *a, **k: "guest-x"), \
             mock.patch.object(api, "_client_ip", lambda *a, **k: "127.0.0.1"):
            return api.billing_status()

    def test_a_half_configured_instance_offers_only_the_priced_kinds(self):
        self._sell()
        os.environ["STRIPE_PRICE_REPORT"] = "price_single"
        kinds = [o["kind"] for o in self._status()["offers"]]
        self.assertEqual(kinds, ["report"],
                         "a pack with no price id must not appear on the gate")

    def test_every_offer_carries_a_price_and_a_credit_count(self):
        self._sell()
        for k in ("REPORT", "BUNDLE5", "BUNDLE10"):
            os.environ[f"STRIPE_PRICE_{k}"] = f"price_{k.lower()}"
        offers = self._status()["offers"]
        self.assertEqual(len(offers), 3)
        for o in offers:
            self.assertIsInstance(o["price_usd"], float, o)
            self.assertGreaterEqual(o["credits"], 1, o)
            self.assertTrue(o["label"], o)

    def test_no_processor_means_no_offers_and_no_gate(self):
        st = self._status()
        self.assertEqual(st["offers"], [])
        self.assertFalse(st["needs_purchase"])


class ThePreviewSwitchIsHonestAboutItself(_Env):
    """CASTOR_PAYWALL_PREVIEW draws the gate on an instance that cannot charge, so the
    operator can look at the flow. It must SAY it is a preview, or a screenshot of it
    becomes evidence that payments work."""

    def test_preview_draws_the_gate_and_flags_it(self):
        import api
        os.environ["CASTOR_PAYWALL_PREVIEW"] = "1"
        with mock.patch.object(api, "_current_owner", lambda *a, **k: "guest-x"), \
             mock.patch.object(api, "_client_ip", lambda *a, **k: "127.0.0.1"):
            st = api.billing_status()
        self.assertTrue(st["needs_purchase"])
        self.assertTrue(st["preview"])
        self.assertFalse(st["configured"], "preview must never claim Stripe is wired")
        self.assertTrue(st["offers"], "a gate with nothing on it shows nothing useful")


class TheSurveyAsksBeforeItLaunches(unittest.TestCase):
    """A source assertion, because the ordering IS the fix. The gate has to be awaited
    before POST /plan; calling it after would charge for a run already in flight."""

    def test_gate_is_awaited_before_the_plan_is_posted(self):
        src = Path(__file__).parent.joinpath("web/survey.js").read_text(encoding="utf-8")
        gate = src.index("await gate(")
        plan = src.index('api("POST", "/plan"')
        self.assertLess(gate, plan,
                        "the run must not start before the gate has answered")

    def test_the_dead_seam_comment_is_gone(self):
        src = Path(__file__).parent.joinpath("web/survey.js").read_text(encoding="utf-8")
        self.assertNotIn("Insert the checkout between these two lines", src)

    def test_a_billing_outage_lets_the_run_through(self):
        """Ten minutes of answers must not be lost because /billing/status was down. The
        honest failure is to let it run, not to invent a wall we cannot take money at."""
        src = Path(__file__).parent.joinpath("web/survey.js").read_text(encoding="utf-8")
        body = src[src.index("async function gate("):src.index("function drawGate(")]
        catch = body[body.index("catch (e)"):]
        self.assertIn("return true", catch.split("}")[0] + catch.split("}")[1],
                      "the catch around /billing/status must proceed, not block")


if __name__ == "__main__":
    unittest.main()
