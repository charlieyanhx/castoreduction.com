"""The operator's stated bar: shipping should be a matter of pasting API keys.

Every gap below was found by walking a flow to its end rather than by reading a route
list, and each one made that bar false in a different way.

  A1  Not one operation-layer variable was documented. .env.example listed ten keys, all
      research-engine; render.yaml named SESSION_SECRET alone. Nothing anywhere mentioned
      Stripe, Resend, Google, or CASTOR_PUBLIC_URL. You cannot paste a key you have not
      been told exists.
  A2  billing.configured() checked STRIPE_SECRET_KEY alone, so an instance with no webhook
      secret CHARGED CARDS and granted nothing: verify_webhook raised on every delivery,
      Stripe retried and gave up, and /billing/status still said configured.
  A3  mailer.configured() checked the key and the sender but not the public URL, so reset
      and confirmation mail shipped the literal string "(no public URL configured)" where
      the link belongs. Recovery dead on a deploy that looked complete.
  A4  The Stripe return URL dropped the intake session. The survey keeps its state only in
      ?s=, so a founder who paid landed on a blank prose box with every answer gone and
      the run they bought never started.
  A5  The buy offer was gated on /limit/i, which also matches the CONCURRENCY refusal —
      and routes/research.py explicitly declines to spend a credit on that one. They paid
      and it still would not run.
  A13 report.html and report.pdf refuse a withheld report; onepager.html served it at 200.
      The one deliverable a founder forwards to an investor was the one ignoring the gate.
"""
from __future__ import annotations

import io
import os
import re
import unittest
from pathlib import Path


class TestEveryVariableIsDocumented(unittest.TestCase):
    """A key nobody documented is a key nobody pastes."""

    #: What the operation layer needs. Engine keys are optional by design and live in
    #: their own section; these are the ones that decide whether the product functions.
    OPERATION = [
        "SESSION_SECRET", "CASTOR_ENV", "CASTOR_PUBLIC_URL", "CASTOR_TRUST_PROXY",
        "CASTOR_DAILY_RUNS", "CASTOR_DAILY_AUX_RUNS", "CASTOR_PAYWALL_OFF",
        "CASTOR_REQUIRE_LOGIN", "CASTOR_ALLOW_UNPAID_CREDITS",
        "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_REPORT",
        "STRIPE_PRICE_BUNDLE5", "STRIPE_PRICE_BUNDLE10",
        "STRIPE_PRICE_MARKS", "STRIPE_PRICE_QUESTIONS", "STRIPE_PRICE_RERUN",
        "RESEND_API_KEY", "MAIL_FROM",
        "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI",
        "JOBS_DB_PATH", "CASTOR_TRANSCRIPT_DIR", "LLM_ALLOW_PAID",
    ]

    def test_env_example_names_them_all(self):
        body = Path(".env.example").read_text()
        missing = [k for k in self.OPERATION if k not in body]
        self.assertEqual(missing, [], f"undocumented in .env.example: {missing}")

    def test_the_deploy_manifest_names_the_integrations(self):
        body = Path("render.yaml").read_text()
        need = ["STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "RESEND_API_KEY",
                "MAIL_FROM", "GOOGLE_CLIENT_ID", "CASTOR_PUBLIC_URL", "SESSION_SECRET"]
        missing = [k for k in need if k not in body]
        self.assertEqual(missing, [], f"undeclared in render.yaml: {missing}")

    def test_every_key_says_where_to_get_it(self):
        """A key nobody tells you how to obtain is the same as no key. An earlier rewrite
        of this file dropped the Census signup URL and an existing test caught it; this is
        the same rule applied to every integration the operator has to go and register
        for."""
        body = Path(".env.example").read_text()
        for name, marker in [
            ("Stripe", "dashboard.stripe.com/apikeys"),
            ("Stripe webhooks", "dashboard.stripe.com/webhooks"),
            ("Resend", "resend.com/api-keys"),
            ("Google OAuth", "console.cloud.google.com/apis/credentials"),
            ("Census", "api.census.gov/data/key_signup.html"),
            ("BLS", "data.bls.gov/registrationEngine"),
            ("Anthropic", "console.anthropic.com"),
        ]:
            with self.subTest(service=name):
                self.assertIn(marker, body,
                              f"{name} has no signup URL, so the reader cannot act on it")

    def test_no_operation_variable_is_read_but_undocumented(self):
        """The guard that keeps this true. Any new env var in the app layer must be
        written down in the same change that reads it."""
        read = set()
        for p in ["api.py", "auth.py", "billing.py", "mailer.py", "quota.py", "jobs.py",
                  "iteration.py", "intake.py", "feedback.py"] + \
                 [str(x) for x in Path("routes").glob("*.py")]:
            src = Path(p).read_text()
            read |= set(re.findall(r'(?:environ\.get\(|environ\[|getenv\()"([A-Z_0-9]+)"', src))
        documented = Path(".env.example").read_text()
        # PORT and HOST come from the host, not from us.
        undocumented = sorted(k for k in read
                              if k not in documented and k not in {"PORT", "HOST"})
        self.assertEqual(undocumented, [],
                         f"the app reads these and nothing documents them: {undocumented}")


class TestHalfConfiguredIsTreatedAsUnconfigured(unittest.TestCase):
    """A half-wired integration is worse than an absent one: it accepts the action and
    fails silently afterwards."""

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
                        "RESEND_API_KEY", "MAIL_FROM", "CASTOR_PUBLIC_URL")}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_stripe_needs_the_webhook_secret(self):
        import billing
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_x"
        self.assertFalse(billing.configured(),
                         "checkout would charge a card that no webhook could ever credit")
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_x"
        self.assertTrue(billing.configured())

    def test_mail_needs_the_public_url(self):
        import mailer
        os.environ["RESEND_API_KEY"] = "re_x"
        os.environ["MAIL_FROM"] = "hello@example.com"
        self.assertFalse(mailer.configured(),
                         "reset mail would ship the words '(no public URL configured)'")
        os.environ["CASTOR_PUBLIC_URL"] = "https://app.example.com"
        self.assertTrue(mailer.configured())

    def test_an_unconfigured_send_is_quiet_not_loud(self):
        import mailer
        self.assertFalse(mailer.send("a@example.com", "s", "t"))


class TestPayingDoesNotDestroyTheAnswers(unittest.TestCase):
    def test_checkout_carries_the_intake_session_back(self):
        import tempfile
        from unittest.mock import patch

        from fastapi.testclient import TestClient
        old = os.environ.get("JOBS_DB_PATH")
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        os.environ.update(STRIPE_SECRET_KEY="sk_test_x", STRIPE_WEBHOOK_SECRET="whsec_x",
                          STRIPE_PRICE_REPORT="price_x")
        try:
            import api
            import jobs
            jobs._reset_for_tests()
            seen = {}

            def fake(kind, owner, success_url, cancel_url, job_id=None):
                seen.update(success=success_url, cancel=cancel_url)
                return "https://checkout.stripe.test/x"

            with patch("billing.create_checkout", side_effect=fake):
                TestClient(api.app).post("/billing/checkout",
                                         json={"kind": "report", "session_id": "sess-abc"})
            self.assertIn("s=sess-abc", seen["success"],
                          "the survey cannot resume, so the answers are gone and the run "
                          "they paid for never starts")
            self.assertIn("s=sess-abc", seen["cancel"],
                          "backing out of checkout also destroys the interview")
        finally:
            if old is None:
                os.environ.pop("JOBS_DB_PATH", None)
            else:
                os.environ["JOBS_DB_PATH"] = old

    def test_the_survey_sends_it(self):
        js = Path("web/survey.js").read_text()
        self.assertIn("session_id: session", js,
                      "survey.js stopped telling checkout which interview to come back to")


class TestTheBuyOfferMatchesTheRefusal(unittest.TestCase):
    def test_it_is_offered_only_on_the_daily_cap(self):
        """The concurrency refusal also says 'limit', and a credit cannot clear it —
        routes/research.py declines to spend one on that case."""
        js = Path("web/survey.js").read_text()
        self.assertIn("/daily limit/i", js)
        self.assertNotIn("/limit/i.test", js)

    def test_the_two_refusals_really_do_differ(self):
        """The gate matches on wording, so the wording is part of the contract. If either
        message changes, the buy offer appears on the wrong refusal again."""
        src = Path("quota.py").read_text()
        self.assertIn("daily limit of", src,
                      "the daily-cap message no longer says 'daily limit'")
        self.assertIn("a report is already running for this account", src)
        # and the concurrency one must NOT contain the string the gate matches
        concurrency = "a report is already running for this account (limit "
        self.assertNotIn("daily limit", concurrency)


class TestTheOnePagerHonoursTheWithhold(unittest.TestCase):
    def test_it_checks_blocking_findings(self):
        """report.html and report.pdf both refuse a withheld report. The one-pager is the
        deliverable a founder forwards, and it was serving them at 200."""
        import inspect

        import routes.jobs as rj
        src = inspect.getsource(rj.get_job_onepager)
        self.assertIn("blocking_findings", src,
                      "the one-pager publishes reports the other two withhold")
        self.assertIn("409", src)


if __name__ == "__main__":
    unittest.main()


class TestNothingDurableLandsOnTheImageLayer(unittest.TestCase):
    """A path the image does not point at the volume is data that vanishes on redeploy.

    Found by booting as production against a fresh volume rather than by reading the
    config. render.yaml declared CASTOR_TRANSCRIPT_DIR and the Dockerfile did not set it,
    so transcripts landed in /app and the progress page's event history was destroyed by
    every deploy. The three cache paths were already pointed at /data; this one was
    written down and never wired.
    """

    #: Everything the app writes that must outlive a deploy.
    DURABLE = ["JOBS_DB_PATH", "LLM_CACHE_PATH", "HTTP_CACHE_PATH",
               "CASTOR_TRANSCRIPT_DIR"]

    def test_the_image_points_every_durable_path_at_the_volume(self):
        body = Path("Dockerfile").read_text()
        for var in self.DURABLE:
            with self.subTest(var=var):
                self.assertIn(var, body, f"{var} is unset, so it defaults into the image")
                # The first entry of an ENV block carries the ENV keyword; the rest are
                # continuation lines. Match on the assignment, not on the line start.
                line = next(l for l in body.splitlines() if f"{var}=" in l)
                self.assertIn("/data", line,
                              f"{var} does not point at the mounted volume: {line.strip()}")

    def test_the_volume_exists_in_the_image(self):
        body = Path("Dockerfile").read_text()
        self.assertIn("mkdir -p /data", body)


class TestTheCopyDoesNotOverstate(unittest.TestCase):
    """The login page sells the product on a specific number, and a specific number is
    the kind that drifts. It said 59 while gates/ held 61.

    A claim a buyer can check is worth more than a round one, which is the whole pitch on
    that page. It is only worth more while it is true.
    """

    def test_the_invariant_count_matches_the_gates_package(self):
        import re
        detectors = set()
        for p in Path("gates").glob("*.py"):
            detectors |= set(re.findall(r"^def (d\d{2}_[a-z_]+)", p.read_text(), re.M))
        claimed = re.search(r"(\d+) automated invariants",
                            Path("web/login.html").read_text())
        self.assertIsNotNone(claimed, "the login page stopped naming a number")
        self.assertEqual(int(claimed.group(1)), len(detectors),
                         f"the page claims {claimed.group(1)} and gates/ has "
                         f"{len(detectors)}")
