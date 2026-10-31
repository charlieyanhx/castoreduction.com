"""An instance that can take money refuses to boot with the test switches on.

CASTOR_STUB_REPORT and CASTOR_PAYWALL_PREVIEW exist for exercising the flow around a
report without paying for the report: the stub answers POST /plan with a clone of a
finished job, and the preview draws the gate on an instance with no Stripe keys and grants
the purchase for free. Both are switches for a developer's machine. Neither of them was
consulted at boot, so the day one of them was left on where the paywall stood, a founder
paid $29 and the credit bought a clone stamped "(test run)".

The boot guard used to enforce exactly one thing, SESSION_SECRET under production. Now it
also refuses:

  a selling instance on the stub    billing.configured() and the paywall on, with
                                    CASTOR_STUB_REPORT or CASTOR_PAYWALL_PREVIEW set
  production on either switch       CASTOR_ENV=production with either set, even when
                                    CASTOR_PAYWALL_OFF says nobody is being charged,
                                    because production is not where tests run
  production mail with no origin    CASTOR_ENV=production with RESEND_API_KEY set and no
                                    CASTOR_PUBLIC_URL: every mail carries a link, and
                                    mailer.configured() would otherwise go quietly false

And it still boots the shapes that are legitimate: the stub behind CASTOR_PAYWALL_OFF on a
non-production instance, which is how the flow is tested with the keys in place, and the
stub with no processor at all.

`with TestClient(...)` is what enters the lifespan; a bare TestClient never does. A raise
before `yield` propagates out of `__enter__`, exactly as it does out of a real boot.
"""
from __future__ import annotations

import os
import tempfile
import unittest

KEYS = ("CASTOR_ENV", "SESSION_SECRET", "CASTOR_STUB_REPORT", "CASTOR_PAYWALL_PREVIEW",
        "CASTOR_PAYWALL_OFF", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
        "RESEND_API_KEY", "MAIL_FROM", "CASTOR_PUBLIC_URL")


class _Env(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in KEYS + ("JOBS_DB_PATH",)}
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        # A clean slate, so the only configuration in play is the one each test sets.
        for k in KEYS:
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

    @staticmethod
    def _boot():
        from fastapi.testclient import TestClient

        import api
        return TestClient(api.app)

    def _refuses(self, *names: str):
        """Boot raises RuntimeError, and the message names every variable in `names`
        so the operator learns which switch to flip without reading the source."""
        with self.assertRaises(RuntimeError) as caught:
            with self._boot():
                self.fail("a boot the guard should have refused started serving")
        for name in names:
            self.assertIn(name, str(caught.exception),
                          f"the refusal does not say {name} is the reason")

    def _boots(self):
        with self._boot() as c:
            self.assertEqual(c.get("/healthz").status_code, 200)

    @staticmethod
    def _selling():
        """A processor wired both halves, and no switch turning the paywall off."""
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_x"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_x"

    @staticmethod
    def _production():
        os.environ["CASTOR_ENV"] = "production"
        # The guard that was already there, satisfied, so it cannot be the reason.
        os.environ["SESSION_SECRET"] = "s" * 48


class ASellingInstanceCannotBootOnTheStub(_Env):
    def test_stripe_keys_and_the_stub_with_the_paywall_on_refuse_to_boot(self):
        """The plan's named proof: STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET and
        CASTOR_STUB_REPORT set, CASTOR_PAYWALL_OFF unset. Yesterday the lifespan yielded
        and the first purchase bought a clone."""
        self._selling()
        os.environ["CASTOR_STUB_REPORT"] = "some-finished-job"
        self._refuses("CASTOR_STUB_REPORT")

    def test_stripe_keys_and_the_preview_with_the_paywall_on_refuse_to_boot(self):
        """The preview grants the purchase for free. On an instance that could have
        charged, that is a checkout nobody can tell from a real one."""
        self._selling()
        os.environ["CASTOR_PAYWALL_PREVIEW"] = "1"
        self._refuses("CASTOR_PAYWALL_PREVIEW")

    def test_the_stub_behind_the_paywall_off_switch_still_boots(self):
        """The developer shape: keys in place, nobody charged, the flow on a clone.
        This is the configuration the dev server runs on and it must keep booting."""
        self._selling()
        os.environ["CASTOR_STUB_REPORT"] = "some-finished-job"
        os.environ["CASTOR_PAYWALL_OFF"] = "1"
        self._boots()

    def test_the_stub_with_no_processor_still_boots(self):
        """No keys means no wall and nothing to charge; the stub is harmless here."""
        os.environ["CASTOR_STUB_REPORT"] = "some-finished-job"
        self._boots()

    def test_the_stub_needs_only_to_be_set_not_to_name_a_real_report(self):
        """_stub_run falls through to a real run when the id names nothing, so a
        misspelt id is one edit away from a clone. The guard reads the switch, not
        the database."""
        self._selling()
        os.environ["CASTOR_STUB_REPORT"] = "not-a-job-id-at-all"
        self._refuses("CASTOR_STUB_REPORT")


class ProductionRefusesEitherSwitch(_Env):
    def test_production_on_the_stub_refuses_even_with_the_paywall_off(self):
        """Production is not where tests run. CASTOR_PAYWALL_OFF makes the stub free,
        not safe: the flag flips independently, and the next edit that turns the
        paywall back on would ship the clone."""
        self._production()
        os.environ["CASTOR_STUB_REPORT"] = "some-finished-job"
        os.environ["CASTOR_PAYWALL_OFF"] = "1"
        self._refuses("CASTOR_STUB_REPORT")

    def test_production_on_the_preview_refuses(self):
        self._production()
        os.environ["CASTOR_PAYWALL_PREVIEW"] = "1"
        self._refuses("CASTOR_PAYWALL_PREVIEW")

    def test_production_with_neither_switch_boots(self):
        """The guard adds refusals; it does not take away the boot that worked."""
        self._production()
        self._boots()

    def test_the_session_secret_guard_is_still_there(self):
        """Extending the guard must not have dropped the refusal it started with."""
        os.environ["CASTOR_ENV"] = "production"
        self._refuses("SESSION_SECRET")


class ProductionMailNeedsAnOrigin(_Env):
    def test_a_resend_key_without_a_public_url_refuses_to_boot(self):
        """Every mail carries a link. mailer.configured() goes false without the origin,
        so a deploy that pasted the key would look complete and send nothing, and account
        recovery would be dead until someone noticed."""
        self._production()
        os.environ["RESEND_API_KEY"] = "re_x"
        os.environ["MAIL_FROM"] = "castor@example.com"
        self._refuses("CASTOR_PUBLIC_URL")

    def test_a_resend_key_with_a_public_url_boots(self):
        self._production()
        os.environ["RESEND_API_KEY"] = "re_x"
        os.environ["MAIL_FROM"] = "castor@example.com"
        os.environ["CASTOR_PUBLIC_URL"] = "https://app.example.com"
        self._boots()

    def test_outside_production_a_resend_key_without_a_public_url_boots(self):
        """Local development pastes a key to try the mail path; the missing origin
        leaves mailer.configured() false there, which is not a failed deploy."""
        os.environ["RESEND_API_KEY"] = "re_x"
        os.environ["MAIL_FROM"] = "castor@example.com"
        self._boots()


class TheRefusalIsPartOfTheOneBootStep(_Env):
    def test_the_refuse_step_itself_raises(self):
        """test_boot_has_one_place_to_read.py watches one sequence of three names. The
        new refusals live inside the first of them, so that test still sees the same
        sequence and a reader still finds every boot refusal in one function."""
        import api
        self._selling()
        os.environ["CASTOR_STUB_REPORT"] = "some-finished-job"
        with self.assertRaises(RuntimeError):
            api._refuse_to_boot_misconfigured()


if __name__ == "__main__":
    unittest.main()
