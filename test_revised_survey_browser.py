"""Opt-in Playwright regression of the real survey with offline model boundaries.

Run:
    CASTOR_RUN_SURVEY_BROWSER=1 .venv/bin/python -m unittest -v test_revised_survey_browser

Artifacts are saved under out/survey-revision-e2e/<run>. The local test server
uses temporary storage and blocks external connections. Only model/research
boundaries are fixtures; the recursive business object, PREP and scheduler run.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OPENING = (
    "Hi! I'll help you put together a market-research report. To start: in as much "
    "detail as you have, what does your product do and who is it for?"
)
BRIEF = (
    "Northstar Learning runs tutoring centres in Toronto and Vancouver, live online "
    "classes across Canada, a self-service subscription, and curriculum licenses for "
    "independent schools. Families buy tutoring; schools buy annual licenses."
)
CORE = {
    "status_quo": "What other product or service might your customer be already using? "
                  "If yours is a brand-new idea, what else might fill your customer's "
                  "time or needs?",
    "monthly_cost_estimate": "Roughly what will it cost you to run each month? Feel free "
                             "to give the end number, or the cost breakdown.",
    "customer_evidence": "Has anyone tried your product already? If so, what feedback "
                         "have you gotten?",
    "named_competitors": "A bonus if you know one: which company does the closest thing "
                         "to this?",
    "success_target": "If this works, what does the first year look like? A rough "
                      "revenue figure or customer count you'd be happy with.",
    "business_model": "What is your revenue model? In other words, how do you make "
                      "money — pay per item, a subscription, free with ads? If you have "
                      "multiple products or revenue streams, tell us about your "
                      "flagship model.",
    "delivery_mode": "Is your business in person, online, or a mix of both?",
}
SPECIFIC = {
    "geography": "Where is your business located? If it's in person, give us the "
                 "location and the radius you think people would be willing to drive "
                 "to get to you. If it's online, give us the regions you would want to "
                 "service (local shipping, US, international, etc). If you have "
                 "multiple locations, give us the primary or first one of interest.",
    "service_capacity": "How many people could your business service? Feel free to "
                         "answer in any interval (daily, weekly, etc).",
    "scalability": "Could your business easily scale, or does it have fixed capacity? "
                   "(Can you hire more workers, contract out, etc?)",
    "acquisition_channels": "How will your customer find you? If it's in person, will "
                            "you rely on foot traffic, billboards, word of mouth? If "
                            "online, will you be hosted on a platform (Amazon, Etsy, "
                            "GitHub), appearing in search results, or have referrals?",
    "unit_economics": "What is your marginal profit? What does your flagship product "
                      "cost to make per item, and how much will it cost the consumer?",
    "fixed_costs": "What are your fixed costs? Things like physical space, worker "
                   "salaries, or monthly subscriptions.",
    "constraints": "Is there anything else we need to know? Rules, licenses, barriers, "
                    "etc?",
}
ANSWERS = {
    "status_quo": "Private tutors and homework clubs; families sometimes use free videos.",
    "monthly_cost_estimate": "CAD 9,000 rent + CAD 18,000 payroll + CAD 850 software per "
                             "month; launch ads are separate.",
    "customer_evidence": "12 families tried it. They liked online flexibility but wanted "
                         "more local weekend sessions.",
    "named_competitors": "Kumon for centres; independent tutors for one-to-one lessons.",
    "success_target": "120 recurring families and 8 school licenses in the first year.",
    "business_model": "Flagship tutoring is pay per session. Also monthly learning "
                      "subscriptions and annual school curriculum licenses.",
    "delivery_mode": "Hybrid: two physical centres, live video, and self-service online.",
    "geography": "Toronto is primary, with a 20-minute drive radius. Vancouver is second; "
                 "online classes serve all of Canada.",
    "service_capacity": "Toronto: 40 learners daily; Vancouver: 25 daily. Online: 120 "
                         "learners weekly. School licenses have no learner cap.",
    "scalability": "Hire teachers for live lessons; subscriptions scale without "
                   "classrooms. Physical rooms cap peak-hour attendance.",
    "acquisition_channels": "School referrals and local word of mouth, plus online "
                            "search and partner newsletters.",
    "unit_economics": "Tutoring costs CAD 35 per lesson and sells for CAD 65. Software "
                      "costs CAD 2 per learner per month and sells for CAD 12.",
    "fixed_costs": "CAD 9,000 rent, CAD 18,000 salaries, CAD 850 subscriptions monthly; "
                   "annual insurance is CAD 4,000.",
    "constraints": "Teacher background checks, provincial curriculum requirements, and "
                    "parental consent for recorded online sessions.",
}


@unittest.skipUnless(
    os.environ.get("CASTOR_RUN_SURVEY_BROWSER") == "1",
    "Set CASTOR_RUN_SURVEY_BROWSER=1 to run the isolated Playwright survey tests",
)
class RevisedSurveyBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright

        cls.artifacts = ROOT / "out/survey-revision-e2e" / (
            time.strftime("%Y%m%d-%H%M%S") + "-" + str(os.getpid())
        )
        cls.artifacts.mkdir(parents=True)
        cls.results = []
        cls.addClassCleanup(cls._write_summary)
        cls.log = (cls.artifacts / "server.log").open("w", encoding="utf-8")
        cls.addClassCleanup(cls.log.close)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.server = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts/treadle_survey_test_server.py"),
             "--port", str(cls.port)],
            cwd=ROOT, env={**os.environ, "PYTHONUNBUFFERED": "1"},
            stdout=cls.log, stderr=subprocess.STDOUT,
        )
        cls.addClassCleanup(cls._stop_server)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if cls.server.poll() is not None:
                raise RuntimeError(f"Survey server exited; see {cls.artifacts / 'server.log'}")
            try:
                with urlopen(cls.base + "/healthz", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Survey server did not become ready")
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        launch = {"headless": True}
        if Path(CHROME).is_file():
            launch["executable_path"] = CHROME
        cls.browser = cls.playwright.chromium.launch(**launch)
        cls.addClassCleanup(cls.browser.close)

    @classmethod
    def _stop_server(cls):
        if cls.server.poll() is None:
            cls.server.terminate()
            try:
                cls.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.server.kill()
                cls.server.wait(timeout=5)

    @classmethod
    def _write_summary(cls):
        (cls.artifacts / "results.json").write_text(
            json.dumps({"browser": "Chrome / Playwright", "live_models": False,
                        "tests": cls.results}, indent=2), encoding="utf-8"
        )
        print(f"\nSurvey browser artifacts: {cls.artifacts}", flush=True)

    def setUp(self):
        self.errors = []
        self.external = []
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 1000})
        self.context.route("**/*", self._local_only)
        self.context.tracing.start(screenshots=True, snapshots=True, sources=True)
        self.page = self.context.new_page()
        self.page.set_default_timeout(15_000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.on("console", lambda message: self.errors.append(message.text)
                     if message.type == "error" else None)

    def tearDown(self):
        name = self._testMethodName
        try:
            self.page.screenshot(path=str(self.artifacts / f"{name}.png"), full_page=True)
            self.context.tracing.stop(path=str(self.artifacts / f"{name}.zip"))
            failures = self._outcome.result.failures + self._outcome.result.errors
            passed = not any(test is self for test, _ in failures)
            self.results.append({
                "test": name, "passed": passed,
                "browser_errors": self.errors, "external_resources_stubbed": self.external,
            })
        finally:
            self.context.close()

    def _local_only(self, route):
        url = urlparse(route.request.url)
        if url.hostname == "127.0.0.1" and url.port == self.port:
            route.continue_()
        elif url.hostname in ("fonts.googleapis.com", "fonts.gstatic.com"):
            self.external.append(route.request.url)
            route.fulfill(status=200, body="", content_type="text/css")
        else:
            self.errors.append("Unexpected external request: " + route.request.url)
            route.abort()

    def _heading(self, text):
        from playwright.sync_api import expect

        expect(self.page.locator("#head")).to_have_text(text)

    def _start(self, description=BRIEF):
        from playwright.sync_api import expect

        self.page.goto(self.base + "/survey")
        expect(self.page.get_by_label("Describe your venture")).to_be_visible()
        expect(self.page.locator("#sub")).to_have_text(OPENING)
        self.page.get_by_label("Describe your venture").fill(description)
        with self.page.expect_response(
            lambda response: response.url.endswith("/intake/start")
                             and response.request.method == "POST"
        ) as started:
            self.page.locator("#go").click()
        self.assertEqual(started.value.status, 200)
        self.session_id = started.value.json()["session_id"]
        self._heading("Core Pack")
        self.assertEqual(parse_qs(urlparse(self.page.url).query)["s"], [self.session_id])

    def _questions(self, expected):
        self.assertEqual(
            self.page.locator(".q .q-ask:not(#ask-report_style)").count(), len(expected)
        )
        for key, question in expected.items():
            field = self.page.get_by_label(question, exact=True)
            self.assertEqual(field.evaluate("(element) => element.tagName"), "TEXTAREA")
            self.assertFalse(field.evaluate("(element) => element.required"))
            self.assertIn(question, self.page.locator("#ask-" + key).inner_text())
        self.assertEqual(
            self.page.locator(".ctl[data-field]:not([data-field=report_style])").evaluate_all(
                "(elements) => elements.map(element => element.dataset.field)"
            ),
            list(expected),
        )

    def _fill(self, questions):
        for key, question in questions.items():
            self.page.get_by_label(question, exact=True).fill(ANSWERS[key])

    def _assert_no_horizontal_overflow(self):
        self.assertTrue(self.page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth + 1"
        ))

    def _assert_treadle_only(self):
        from playwright.sync_api import expect

        expect(self.page.get_by_role("radio")).to_have_count(0)
        expect(self.page.locator("[data-field=report_style]")).to_have_count(0)
        expect(self.page.locator("#form")).not_to_contain_text("Original Castor")

    def _run(self, *, expected_state="complete"):
        with self.page.expect_response(
            lambda response: response.url.endswith("/plan")
                             and response.request.method == "POST"
        ) as submitted:
            self.page.locator("#go").focus()
            self.page.keyboard.press("Enter")
        return self._complete_run(submitted.value, expected_state=expected_state)

    def _complete_run(self, response, *, expected_state="complete"):
        from playwright.sync_api import expect
        from packs.market_research_next import REPORT_ORDER

        self.assertEqual(response.status, 200, response.text())
        body = response.request.post_data_json
        self.assertEqual(body["pipeline"], "treadle")
        self.assertEqual(body["report_style"], "full")
        self.page.wait_for_url("**/progress.html?job=*")
        job_id = response.json()["job_id"]
        # Wait for the actual progress page to deliver the report link. An async
        # wait_for_function predicate can treat its pending Promise as truthy.
        expect(self.page.locator("#outcome a.open")).to_be_visible(timeout=60_000)
        job = self.context.request.get(self.base + "/jobs/" + job_id).json()
        self.assertEqual(job["state"], expected_state, job.get("error"))
        result = job["result"]
        self.assertEqual(result["_pipeline"], "treadle")
        self.assertEqual(result["_treadle_status"],
                         "complete" if expected_state == "complete" else "incomplete")
        report = result["_treadle"]
        prep = report["result"]["prep"]
        self.assertEqual(prep["venture"]["customer_inputs"]["intake"], body["intake"])
        self.assertEqual(set(prep["research_spec"]), set(REPORT_ORDER))
        self.assertFalse(prep["source_tracing_enabled"])
        business = prep["business_context"]
        self.assertTrue({"toronto", "vancouver", "online", "tutoring", "subscriptions", "licenses"}
                        <= {node["id"] for node in business["object"]["nodes"]})
        self.assertTrue(business["construction"]["expansion_complete"])
        self.assertGreaterEqual(business["construction"]["calls"], 3)
        self.assertLessEqual(report["execution"]["correction_rounds"], 2)
        if expected_state == "complete":
            self.assertTrue(all(isinstance(report["result"].get(key), dict)
                                for key in REPORT_ORDER))
        title = ("Your Treadle report is ready" if expected_state == "complete"
                 else "Your Treadle report is partial")
        expect(self.page.locator("#title")).to_have_text(title, timeout=10_000)
        expect(self.page.locator(".phase[data-section]")).to_have_count(9)
        expect(self.page.locator("#outcome")).to_contain_text("Source tracing is disabled")
        link = self.page.get_by_role(
            "link", name="Open the report" if expected_state == "complete"
            else "Open the partial report", exact=True,
        )
        self.page.screenshot(path=str(self.artifacts / (
            self._testMethodName + "-progress.png")), full_page=True)
        with self.page.expect_response(
            lambda item: item.url.endswith("/jobs/" + job_id + "/report.html")
        ) as delivered:
            link.click()
        self.assertEqual(delivered.value.status, 200)
        expect(self.page.get_by_role("heading", level=1)).to_have_text("Venture research report")
        for key in REPORT_ORDER:
            expect(self.page.locator("h2#" + key)).to_be_visible()
        if expected_state != "complete":
            expect(self.page.locator("body")).to_contain_text("incomplete research")
        (self.artifacts / (self._testMethodName + "-report.json")).write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        (self.artifacts / (self._testMethodName + "-plan.json")).write_text(
            json.dumps(body, indent=2), encoding="utf-8"
        )
        return body

    def test_complete_hybrid_answers_survive_back_reload_and_real_plan_route(self):
        from playwright.sync_api import expect

        self._start()
        self._questions(CORE)
        self._fill(CORE)
        evidence = self.page.get_by_label(CORE["customer_evidence"], exact=True)
        evidence.press("End")
        evidence.press("Enter")
        evidence.press("Backspace")
        self._heading("Core Pack")  # Enter within an answer must not submit.
        self.page.locator("#go").click()
        self._heading("More Specific Questions")
        self._questions(SPECIFIC)
        self._fill(SPECIFIC)
        self.page.reload()
        self._heading("More Specific Questions")
        for key, question in SPECIFIC.items():
            expect(self.page.get_by_label(question, exact=True)).to_have_value(ANSWERS[key])
        self.page.locator("#back").click()
        self._heading("Core Pack")
        for key, question in CORE.items():
            expect(self.page.get_by_label(question, exact=True)).to_have_value(ANSWERS[key])
        self.page.get_by_label(CORE["status_quo"], exact=True).fill(
            "Unsaved replacement that must not overwrite the saved founder answer"
        )
        self.page.get_by_role("button", name="Skip these questions", exact=True).click()
        self._heading("More Specific Questions")
        self.page.locator("#go").click()
        self._heading("Review your business")
        self._assert_treadle_only()
        self.page.reload()
        self._heading("Review your business")
        self._assert_treadle_only()
        self.assertIn(ANSWERS["monthly_cost_estimate"], self.page.locator("#form").inner_text())
        self.assertIn(ANSWERS["service_capacity"], self.page.locator("#form").inner_text())
        self._assert_no_horizontal_overflow()
        self.page.screenshot(path=str(self.artifacts / "desktop-review.png"), full_page=True)
        body = self._run()
        for key, answer in ANSWERS.items():
            self.assertEqual(body["intake"]["facts"][key], answer, key)
        self.assertEqual(body["intake"]["slots"]["monthly_cost_estimate"]["kind"], "text")
        self.assertNotIn("pricing", body["intake"]["facts"])
        self.assertEqual(body["report_style"], "full")
        self.assertEqual(self.errors, [])

    def test_old_pipeline_and_style_draft_launches_treadle_full_after_reload(self):
        from playwright.sync_api import expect

        self._start()
        self.page.get_by_label(CORE["status_quo"], exact=True).fill(ANSWERS["status_quo"])
        self.page.locator("#go").click()
        self._heading("More Specific Questions")
        self._assert_treadle_only()
        self.page.get_by_role("button", name="Skip these questions", exact=True).click()
        self._heading("Review your business")
        self.page.evaluate("""(session) => {
            const key = 'castor-survey:' + session;
            const draft = JSON.parse(sessionStorage.getItem(key));
            draft.pipeline = 'legacy';
            draft.report_style = 'memo';
            sessionStorage.setItem(key, JSON.stringify(draft));
        }""", self.session_id)
        self.page.reload()
        self._heading("Review your business")
        self._assert_treadle_only()
        self.page.locator("#back").click()
        self._heading("More Specific Questions")
        self._assert_treadle_only()
        self.page.locator("#back").click()
        self._heading("Core Pack")
        expect(self.page.get_by_label(CORE["status_quo"], exact=True)).to_have_value(
            ANSWERS["status_quo"]
        )
        self.page.locator("#go").click()
        self._heading("More Specific Questions")
        self.page.locator("#go").click()
        self._heading("Review your business")
        self.page.reload()
        self._heading("Review your business")
        self._assert_treadle_only()
        body = self._run()
        self.assertEqual(body["intake"]["facts"]["status_quo"], ANSWERS["status_quo"])
        self.assertEqual(body["intake"]["report_style"], "full")
        self.assertEqual(self.errors, [])

    def test_partial_treadle_run_stops_and_delivers_saved_sections(self):
        self._start(BRIEF + " E2E_PARTIAL_REPORT")
        self.page.get_by_role("button", name="Skip these questions", exact=True).click()
        self._heading("More Specific Questions")
        self.page.get_by_role("button", name="Skip these questions", exact=True).click()
        self._heading("Review your business")
        self._run(expected_state="error")
        self.assertEqual(self.errors, [])

    def test_mobile_skip_both_groups_keeps_unknowns_and_starts_report(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self._start()
        self._questions(CORE)
        self._assert_no_horizontal_overflow()
        self.page.get_by_label(CORE["status_quo"], exact=True).fill("Discard this draft answer")
        self.page.get_by_role("button", name="Skip these questions", exact=True).click()
        self._heading("More Specific Questions")
        self._questions(SPECIFIC)
        self._assert_no_horizontal_overflow()
        self.page.screenshot(path=str(self.artifacts / "mobile-specific.png"), full_page=True)
        self.page.get_by_role("button", name="Skip these questions", exact=True).click()
        self._heading("Review your business")
        self._assert_no_horizontal_overflow()
        self.assertNotIn("Discard this draft answer", self.page.locator("#form").inner_text())
        body = self._run()
        unanswered = (set(CORE) | set(SPECIFIC)) - {"business_model"}
        self.assertTrue(unanswered <= set(body["intake"]["unknowns"]))
        self.assertTrue(unanswered <= set(body["intake"]["unanswered"]))
        # The opening already describes revenue streams. Skipping follow-ups
        # preserves those original founder words instead of declaring them unknown.
        self.assertEqual(body["intake"]["facts"]["business_model"], BRIEF)
        self.assertNotIn("Discard this draft answer", json.dumps(body))
        self.assertEqual(self.errors, [])

    def test_claim_fields_enter_and_competing_launch_preserve_account_creation(self):
        from playwright.sync_api import expect

        self._start()
        self.page.get_by_role("button", name="Skip these questions", exact=True).click()
        self._heading("More Specific Questions")
        self.page.get_by_role("button", name="Skip these questions", exact=True).click()
        self._heading("Review your business")

        # Only the payment receipt is a fixture. Signup, ownership transfer,
        # confirmation and the final /plan request still use the real local API.
        self.page.route(
            "**/billing/status",
            lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({
                    "needs_purchase": False,
                    "prepaid_email": "survey-browser@example.test",
                }),
            ),
        )
        mutations = []
        self.page.on(
            "request",
            lambda request: mutations.append(request.url)
            if request.method == "POST" else None,
        )
        self.page.goto(self.base + "/survey?s=" + self.session_id + "&paid=bundle5")
        create = self.page.get_by_role(
            "button", name="Create account and start my report", exact=True
        )
        expect(create).to_be_visible()
        email = self.page.get_by_placeholder("you@example.com", exact=True)
        password = self.page.get_by_placeholder("at least 12 characters", exact=True)
        email.fill("survey-browser@example.test")
        email.press("Enter")
        password.fill("survey-test-password-2026")
        password.press("Enter")
        # Let native keyboard actions and both animation frames complete. The old
        # bubbling Enter shortcut dispatched /confirm synchronously in this window.
        self.page.evaluate(
            "() => new Promise(resolve => requestAnimationFrame("
            "() => requestAnimationFrame(resolve)))"
        )
        expect(create).to_be_visible()
        self.assertEqual(mutations, [], "Typing Enter must not launch or create an account")

        pending_signup = []
        self.page.route("**/auth/signup", lambda route: pending_signup.append(route))
        with self.page.expect_request(
            lambda request: request.url.endswith("/auth/signup")
                            and request.method == "POST"
        ):
            create.click()
        self.assertEqual(len(pending_signup), 1)
        expect(self.page.get_by_role(
            "button", name="Creating your account…", exact=True
        )).to_be_disabled()
        # The footer is a distinct launch entry point. Clicking it while signup is
        # pending must join that operation, not run before account creation.
        self.page.locator("#go").click()
        expect(self.page.locator("#go")).to_be_disabled()
        self.assertEqual(
            mutations, [self.base + "/auth/signup"],
            "The competing CTA must wait for signup before confirming or launching",
        )
        self.page.screenshot(
            path=str(self.artifacts / "claim-signup-pending.png"), full_page=True
        )
        with self.page.expect_response(
            lambda response: response.url.endswith("/plan")
                             and response.request.method == "POST"
        ) as submitted:
            pending_signup[0].continue_()
        body = self._complete_run(submitted.value)
        self.assertEqual(mutations.count(self.base + "/auth/signup"), 1)
        self.assertEqual(mutations.count(self.base + "/plan"), 1)
        self.assertTrue(self.context.request.get(self.base + "/auth/me").json()["authenticated"])
        self.assertEqual(body["intake"]["facts"]["original_description"], BRIEF)
        self.assertEqual(self.errors, [])

    def test_legacy_cost_display_and_continue_keep_currency_and_period(self):
        from playwright.sync_api import expect

        self._start()
        legacy_cost = {
            "value": 9000, "kind": "cost", "currency": "USD",
            "period": "year", "unit": None, "source": "form",
        }
        display = "USD 9,000 per year"
        form_url = self.base + "/intake/" + self.session_id + "/form"

        def legacy_card(route):
            response = route.fetch()
            card = response.json()
            cost = next(
                question for question in card["questions"]
                if question["field"] == "monthly_cost_estimate"
            )
            cost.update(
                value=legacy_cost, display_value=display,
                source="founder", confirmed=True,
            )
            route.fulfill(response=response, json=card)

        # A legacy GET response is the fixture; saving and confirming the migrated
        # answer use the actual API and its new versioned free-form conversion.
        self.page.route(form_url, legacy_card)
        self.page.reload()
        self._heading("Core Pack")
        expect(self.page.get_by_label(
            CORE["monthly_cost_estimate"], exact=True
        )).to_have_value(display)
        self.page.unroute(form_url, legacy_card)
        with self.page.expect_response(
            lambda response: response.url == form_url
                             and response.request.method == "POST"
        ) as saved:
            self.page.locator("#go").click()
        self.assertEqual(saved.value.status, 200)
        posted = saved.value.request.post_data_json
        self.assertEqual(posted["survey_version"], "2026-10")
        self.assertEqual(posted["answers"]["monthly_cost_estimate"], legacy_cost)
        self._heading("More Specific Questions")
        self.page.get_by_role("button", name="Skip these questions", exact=True).click()
        self._heading("Review your business")
        expect(self.page.locator("#form")).to_contain_text(display)
        body = self._run()
        self.assertEqual(body["intake"]["facts"]["monthly_cost_estimate"], display)
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
