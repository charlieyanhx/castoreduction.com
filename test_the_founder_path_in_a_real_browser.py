"""The founder's path, driven in a real browser: the report, the workshop, a new run.

Every other test of the page asserts on strings; this one opens headless Chromium on the
stand-in server (scripts/dev_workshop_server.py, the real app on a throwaway database
with a stand-in analyst and, under CASTOR_DEV_FAST_RUNS, the fixture in place of the
research) and does what a founder does: reads the report, asks, selects a passage and has
it explained, leaves a note, rewrites, runs out of credits and buys more, marks the report
final, starts a new report and follows the progress page to it, and opens it on a phone.
It fails on the page a founder sees, not on a Jinja string.

Skipped, with the reason, when Playwright or its Chromium is not installed, or when the
stand-in server cannot start here. The tests share one browser and run in name order.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
SERVER = HERE / "scripts" / "dev_workshop_server.py"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Browser(unittest.TestCase):
    proc = None
    pw = None
    browser = None
    port = 0

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest("playwright is not installed")
        cls.port = _free_port()
        env = {**os.environ, "CASTOR_DEV_FAST_RUNS": "1", "PYTHONUNBUFFERED": "1"}
        cls.proc = subprocess.Popen([sys.executable, str(SERVER), str(cls.port)], cwd=str(HERE),
                                    env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True)
        cls.base = f"http://127.0.0.1:{cls.port}"
        deadline = time.time() + 60
        while time.time() < deadline:
            if cls.proc.poll() is not None:
                out = (cls.proc.stdout.read() or "")[-1200:]
                raise unittest.SkipTest(f"the stand-in server did not start: {out}")
            try:
                with urllib.request.urlopen(cls.base + "/healthz", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.4)
        else:
            cls._stop()
            raise unittest.SkipTest("the stand-in server did not answer /healthz in 60s")
        try:
            cls.pw = sync_playwright().start()
            cls.browser = cls.pw.chromium.launch(headless=True)
        except Exception as e:                      # noqa: BLE001 - no chromium here
            cls._stop()
            raise unittest.SkipTest(f"headless Chromium is not available: {str(e)[:200]}")
        cls.context = cls.browser.new_context(viewport={"width": 1440, "height": 900})
        cls.page = cls.context.new_page()
        cls.page.set_default_timeout(20_000)

    @classmethod
    def _stop(cls):
        if cls.proc and cls.proc.poll() is None:
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.proc.kill()

    @classmethod
    def tearDownClass(cls):
        try:
            if cls.browser:
                cls.browser.close()
            if cls.pw:
                cls.pw.stop()
        finally:
            cls._stop()

    # ---- helpers ----------------------------------------------------------------------
    def js(self, code: str):
        return self.page.evaluate(code)

    def balance(self) -> int:
        """The balance once the panel has loaded it (it reads a dash until then)."""
        self.page.wait_for_function('/^\\d+$/.test(document.getElementById("wsBalN").textContent)')
        return int(self.page.locator("#wsBalN").inner_text())

    def select_in_report(self, nth: int = 0, chars: int = 36):
        """Select the first `chars` characters of a synthesis paragraph the way a founder
        does, with the mouse, so the selection pill's own listener fires."""
        p = self.page.locator("#synthesis p").nth(nth)
        p.scroll_into_view_if_needed()
        box = p.bounding_box()
        y = box["y"] + 10
        self.page.mouse.move(box["x"] + 1, y)
        self.page.mouse.down()
        self.page.mouse.move(box["x"] + min(box["width"] - 2, chars * 7), y, steps=6)
        self.page.mouse.up()
        self.page.wait_for_selector("#wsSel:not([hidden])")

    def ask(self, text: str, wait: bool = True):
        self.page.fill("#wsInput", text)
        self.page.press("#wsInput", "Enter")
        if wait:
            self.page.wait_for_selector("#wsPending", state="detached", timeout=30_000)


class TheFounderPath(_Browser):
    def test_01_the_report_opens_with_the_workshop(self):
        self.page.goto(self.base + "/dev/claim")
        self.page.wait_for_selector("#ws")
        self.assertIn("/report.html", self.page.url)
        self.assertTrue(self.js('document.documentElement.classList.contains("ws-open")'),
                        "the panel opens by itself on a desktop")
        self.assertEqual(self.balance(), 30, "a paid report opens with thirty credits")
        self.assertEqual(self.page.locator(".ws-tog b").inner_text(), "30")
        # the analyst report comes first, the evidence appendix after it
        syn, ev = self.js('[document.getElementById("synthesis").offsetTop, document.getElementById("evidence").offsetTop]')
        self.assertLess(syn, ev)
        self.assertEqual(self.page.locator("#wsChips .ws-chip").count(), 3)

    def test_02_a_question_is_answered_with_citations_that_land(self):
        self.page.locator("#wsChips .ws-chip").first.click()
        self.page.press("#wsInput", "Enter")
        self.page.wait_for_selector("#wsPending")               # the clock, while it thinks
        self.page.wait_for_selector("#wsPending", state="detached", timeout=30_000)
        answer = self.page.locator("#wsTurns .ws-analyst").last
        self.assertIn("$645,289", answer.inner_text())
        self.assertEqual(self.balance(), 29, "one credit per answer")
        self.assertEqual(self.page.locator(".ws-tog b").inner_text(), "29")
        cite = self.page.locator("#wsTurns .ws-cites a.cite").first
        cite.click()
        self.page.wait_for_function('location.hash === "#fact-market-sizing-som-mid"')
        row = self.page.locator("#fact-market-sizing-som-mid")
        self.assertTrue(row.is_visible(), "the cited fact scrolls into view")

    def test_03_a_selected_passage_can_be_explained(self):
        self.select_in_report(0)
        self.page.locator('#wsSel button[data-verb="explain"]').click()
        self.assertFalse(self.page.locator("#wsQbox").is_hidden(), "the passage rides the turn")
        self.assertEqual(self.page.locator("#wsSend").inner_text(), "Explain · 1")
        self.assertEqual(self.page.input_value("#wsInput"), "Explain this passage.")
        self.page.press("#wsInput", "Enter")
        self.page.wait_for_selector("#wsPending", state="detached", timeout=30_000)
        founder = self.page.locator("#wsTurns .ws-founder").last
        self.assertTrue(founder.locator(".ws-q").count() == 1, "the quote shows on the turn")
        self.assertEqual(self.balance(), 28)
        self.assertTrue(self.page.locator("#wsQbox").is_hidden(), "and is cleared after")

    def test_04_a_note_is_free_and_painted_on_the_page(self):
        self.select_in_report(1)
        self.page.locator('#wsSel button[data-verb="note"]').click()
        self.page.fill("#wsInput", "Say the cost side is a placeholder, in the lede.")
        self.page.press("#wsInput", "Enter")
        self.page.wait_for_selector("#wsNotes .ws-note")
        self.assertEqual(self.page.locator("#wsNotesToggle").inner_text(), "1")
        self.assertEqual(self.page.locator("mark.ws-mark").count(), 1, "painted on the report")
        self.assertEqual(self.balance(), 28, "a note costs nothing")
        self.assertIn("Note saved", self.page.locator("#wsErr").inner_text())

    def test_05_a_rewrite_costs_ten_and_reloads_with_the_new_draft(self):
        self.assertFalse(self.page.locator("#wsRewrite").is_disabled())
        self.page.locator("#wsRewrite").click()
        self.page.wait_for_function('document.getElementById("wsRewrite").textContent.startsWith("Rewriting")')
        self.page.wait_for_function('document.querySelector("#synthesis").innerText.includes("(rewritten)")',
                                    timeout=40_000)
        self.page.wait_for_selector("#ws")
        self.assertEqual(self.balance(), 18)
        self.assertIn("Rewritten 1 time", self.page.locator("#wsHistory").text_content())
        self.assertIn("Rewritten.", self.page.locator("#wsErr").inner_text())
        self.assertEqual(self.page.locator("#wsNotesToggle").inner_text(), "1", "the note survives")
        self.assertEqual(self.page.locator("mark.ws-mark").count(), 1)

    def test_06_an_invented_number_is_refused_and_a_failed_turn_refunds(self):
        self.ask("please invent a rent figure")
        last = self.page.locator("#wsTurns .ws-analyst").last
        self.assertIn("cannot back from the evidence", last.inner_text())
        self.assertNotIn("9,400", self.page.locator("#wsTurns").inner_text())
        self.assertEqual(self.balance(), 17, "a refused answer was still a call")
        self.ask("please fail this one")
        self.assertEqual(self.balance(), 17, "a failed call gives the credit back")
        self.assertIn("Your credit was returned", self.page.locator("#wsErr").inner_text())
        self.assertEqual(self.page.input_value("#wsInput"), "please fail this one",
                         "and hands the question back")

    def test_07_an_empty_pool_offers_the_pack_and_the_pack_lands(self):
        self.page.goto(self.base + "/dev/claim?spend=17")
        self.page.wait_for_selector("#ws")
        self.assertEqual(self.balance(), 0)
        self.assertFalse(self.page.locator("#wsOffer").is_hidden())
        self.assertTrue(self.page.locator("#wsSend").is_disabled())
        self.assertIn("Out of workshop credits", self.page.locator("#wsOffer").inner_text())
        self.page.locator("#wsBuy").click()
        self.page.wait_for_function('document.getElementById("wsBalN").textContent === "30"')
        self.assertTrue(self.page.locator("#wsOffer").is_hidden())
        self.assertFalse(self.page.locator("#wsSend").is_disabled())

    def test_08_the_re_run_asks_first_and_final_brings_the_share_offer(self):
        self.page.locator("#wsRerun summary").click()
        self.assertIn("1 included", self.page.locator("#wsRerunHint").inner_text())
        self.page.locator("#wsRerunGo").click()
        self.assertIn("Start the re-run", self.page.locator("#wsRerunConfirm").inner_text())
        self.page.locator("#wsRerunConfirm [data-no]").click()
        self.assertEqual(self.page.locator("#wsRerunConfirm").inner_text().strip(), "")
        self.assertTrue(self.page.locator("#shareBlock").is_hidden(), "not before final")
        self.page.locator("#wsFinal").click()
        self.page.wait_for_selector("#shareBlock:not([hidden])")
        self.assertTrue(self.page.locator("#wsRewrite").is_disabled())
        self.assertIn("This report is final", self.page.locator("#wsRewriteHint").inner_text())

    def test_09_a_new_report_runs_and_the_progress_page_hands_over(self):
        job = self.js('''(async () => {
            const r = await fetch("/plan", {method: "POST", headers: {"Content-Type": "application/json"},
              body: JSON.stringify({description: "A neighbourhood wine bar in Sellwood, Portland, thirty seats, glasses about fourteen dollars."})});
            const b = await r.json(); return r.ok ? b.job_id : ("HTTP " + r.status + " " + JSON.stringify(b)); })()''')
        self.assertFalse(str(job).startswith("HTTP"), job)
        self.page.goto(self.base + "/progress.html?job=" + job)
        self.page.wait_for_selector("a.open", timeout=40_000)
        self.assertIn("Open the report", self.page.locator("a.open").inner_text())
        self.page.locator("a.open").click()
        self.page.wait_for_selector("#ws")
        self.assertEqual(self.balance(), 10, "a free-allowance report opens with ten")
        self.assertTrue(self.page.locator("#synthesis").is_visible())

    def test_10_on_a_phone_the_sheet_rises_and_nothing_overflows(self):
        self.page.set_viewport_size({"width": 375, "height": 812})
        self.page.goto(self.base + "/dev/claim")
        self.page.wait_for_selector("#ws", state="attached")
        self.assertEqual(self.js("document.documentElement.scrollWidth"), 375,
                         "a wide table must not widen the page")
        self.js('localStorage.setItem("ws-open", "0")')
        self.page.reload()
        self.page.wait_for_selector(".ws-tog")
        self.assertFalse(self.js('document.documentElement.classList.contains("ws-open")'))
        self.page.locator(".ws-tog").click()
        self.page.wait_for_function('document.documentElement.classList.contains("ws-open")')
        self.page.wait_for_timeout(400)                          # the sheet's transition
        box = self.page.locator("#ws").bounding_box()
        self.assertEqual(round(box["width"]), 375)
        self.assertLess(box["y"], 812 - 300, "the sheet rises from the bottom")
        self.page.set_viewport_size({"width": 1440, "height": 900})

    def test_11_a_stranger_gets_no_report_and_no_workshop(self):
        """Another visitor (a fresh browser context, no cookie) opening the owner's report
        URL gets 404, the same answer as for a report that does not exist."""
        self.assertIn("/jobs/", self.page.url)
        stranger = self.browser.new_context()
        try:
            r = stranger.new_page().goto(self.page.url.split("?")[0])
            self.assertEqual(r.status, 404, "another visitor's report is not yours")
        finally:
            stranger.close()


if __name__ == "__main__":
    unittest.main()
