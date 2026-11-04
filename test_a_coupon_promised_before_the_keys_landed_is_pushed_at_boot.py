"""The coupon a founder earned before Stripe was wired up has to become real once it is.

sharing.mint pays a founder the moment they publish, keys or no keys. Without keys the row
keeps stripe_promo_id NULL, and sharing.sync_pending exists to push that backlog the moment
keys land. Nothing called it. So an operator who added keys to an instance that had run
without them fixed every share from then on and none of the ones before: those founders
held a code that the checkout box rejects, which sharing._sync_to_stripe's own docstring
calls worse than having been offered nothing.

Boot is the one moment every instance passes through after its config changes, so the
backlog is pushed at startup. Four things have to hold:

  keys present     every pending code is pushed and none is left pending
  keys absent      nothing is called and the code stays pending, listed, honest
  Stripe refuses   the app still comes up and serves /healthz, and the code is still
                   listed as pending rather than lost
  Stripe is slow   the app is serving /healthz long before the push is done: each code is
                   up to two 20-second calls, and a platform that waits on the health
                   check would otherwise declare the deploy dead for a backlog

The push runs on a thread the api module exposes as _coupon_push_thread. Tests that care
what the push did join it before looking, inside the patches, so the thread sees the
same Stripe the test set up.
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest.mock import patch


class _Env(unittest.TestCase):
    KEYS = ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "CASTOR_ENV")

    def setUp(self):
        self._old = {k: os.environ.get(k) for k in self.KEYS + ("JOBS_DB_PATH",)}
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        # Genuinely unconfigured while minting, so the row is a real pending one and not
        # a mocked one.
        for k in self.KEYS:
            os.environ.pop(k, None)
        import jobs
        jobs._reset_for_tests()

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import jobs
        jobs._reset_for_tests()

    def _mint_pending(self, job_id: str) -> str:
        """A founder shares a report on an instance with no Stripe. The reward is minted,
        and Stripe has never heard of it."""
        import sharing
        sharing.publish(job_id, "owner-a", "A coffee shop")
        code = sharing.mint(job_id, "owner-a", None)["code"]
        self.assertIn(code, sharing.pending())
        return code

    @staticmethod
    def _boot():
        """`with` is what fires the startup handlers; a bare TestClient never does."""
        from fastapi.testclient import TestClient

        import api
        return TestClient(api.app)

    @staticmethod
    def _join_push(timeout: float = 10.0):
        """Wait for the push thread, if this boot started one.

        getattr rather than api._coupon_push_thread: a build that still pushes on the
        boot path has no handle, and its tests should fail on what they assert about,
        not on an AttributeError here.
        """
        import api
        t = getattr(api, "_coupon_push_thread", None)
        if t is not None:
            t.join(timeout=timeout)


class KeysAddedAfterTheShareStillPayTheFounder(_Env):
    def test_every_code_pending_at_boot_is_pushed(self):
        import sharing
        first = self._mint_pending("job-1")
        second = self._mint_pending("job-2")
        with patch("billing.configured", return_value=True), \
             patch("billing.create_promo_code", return_value="promo_fake") as create:
            with self._boot():
                self._join_push()
        self.assertEqual(sharing.pending(), [],
                         "a code Stripe does not know is one the checkout box rejects")
        self.assertEqual(sorted(c.args for c in create.call_args_list),
                         sorted([(first, 1000), (second, 1000)]))
        c = sharing._db()
        try:
            rows = c.execute("SELECT code, stripe_promo_id FROM coupons "
                             "ORDER BY code").fetchall()
        finally:
            c.close()
        self.assertEqual(rows, sorted([(first, "promo_fake"), (second, "promo_fake")]))


class WithoutKeysThereIsNowhereToPush(_Env):
    def test_nothing_is_called_and_the_code_stays_pending(self):
        import sharing
        code = self._mint_pending("job-1")
        with patch("billing.create_promo_code") as create:
            with self._boot():
                self._join_push()
        create.assert_not_called()
        self.assertIn(code, sharing.pending(),
                      "still pending: the operator can see it, and the founder is not "
                      "told it is redeemable")


class ABacklogPushThatFailsNeverBlocksBoot(_Env):
    def test_stripe_refusing_the_code_leaves_the_app_serving(self):
        import sharing
        code = self._mint_pending("job-1")
        with patch("billing.configured", return_value=True), \
             patch("billing.create_promo_code",
                   side_effect=RuntimeError("stripe is down")):
            with self._boot() as c:
                self.assertEqual(c.get("/healthz").status_code, 200)
                self._join_push()
        self.assertIn(code, sharing.pending(),
                      "deferred, not lost: it is pushed again at the next boot")

    def test_the_push_itself_blowing_up_leaves_the_app_serving(self):
        """The layer above _sync_to_stripe: if the coupons table is unreadable, the handler
        is the only thing between that and a boot loop."""
        with patch("billing.configured", return_value=True), \
             patch("sharing.sync_pending",
                   side_effect=RuntimeError("the coupons table is gone")):
            with self._boot() as c:
                self.assertEqual(c.get("/healthz").status_code, 200)
                self._join_push()


class ASlowStripeDoesNotHoldBootHostage(_Env):
    # Each code costs one call this long. Two codes pushed on the boot path would hold
    # /healthz for twice this; the bound below is well inside a single one.
    STRIPE_SECONDS = 2.0
    BOOT_BUDGET_SECONDS = 1.0

    def test_healthz_answers_while_the_backlog_is_still_being_pushed(self):
        import sharing

        def slow_stripe(code, amount_off_cents):
            time.sleep(self.STRIPE_SECONDS)
            return "promo_fake"

        first = self._mint_pending("job-1")
        second = self._mint_pending("job-2")
        with patch("billing.configured", return_value=True), \
             patch("billing.create_promo_code", side_effect=slow_stripe) as create:
            # Importing api is its own half second and not what is being timed; _boot
            # does that, and only `with` fires the startup handlers.
            client = self._boot()
            started = time.monotonic()
            with client as c:
                self.assertEqual(c.get("/healthz").status_code, 200)
                elapsed = time.monotonic() - started
                self.assertLess(
                    elapsed, self.BOOT_BUDGET_SECONDS,
                    "boot plus one /healthz took %.2fs: the coupon push is holding the "
                    "health check hostage, and the platform will call the deploy dead"
                    % elapsed)
                # Whether the thread is still pushing or already done is Stripe's
                # business; either way the server was up first.
                self._join_push()
        self.assertEqual(sharing.pending(), [],
                         "pushed in the background, not skipped")
        self.assertEqual(sorted(call.args for call in create.call_args_list),
                         sorted([(first, 1000), (second, 1000)]))


if __name__ == "__main__":
    unittest.main()
