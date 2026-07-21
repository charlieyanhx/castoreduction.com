"""
API-level tests. Uses FastAPI TestClient + monkeypatches the work
functions so no real HTTP or LLM calls happen.
"""
from __future__ import annotations
import os
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("MRP_LOG_LEVEL", "ERROR")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-fake")

# Isolate the job DB for this session — via the ENV var, set before importing api:
# jobs._db_path() resolves JOBS_DB_PATH per connection (the old `jobs_mod.DB = ...`
# attribute patch no longer has any effect). Without this, standalone runs
# (`python test_api.py`) would write test jobs into the production .jobs.sqlite.
_tmp_jobs = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
_tmp_jobs.close()
os.environ["JOBS_DB_PATH"] = _tmp_jobs.name

from fastapi.testclient import TestClient
import api
import jobs as jobs_mod

client = TestClient(api.app)


def _wait_for_job(job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/jobs/{job_id}")
        assert r.status_code == 200
        data = r.json()
        if data["state"] in ("complete", "error"):
            return data
        time.sleep(0.05)
    raise TimeoutError(f"job {job_id} did not finish within {timeout}s")


class TestAAADbIsolation(unittest.TestCase):
    """Guard: the whole file must run against an isolated temp DB — never the
    production .jobs.sqlite (named to sort/define first)."""

    def test_jobs_db_is_isolated_from_production(self):
        from pathlib import Path
        prod = Path(jobs_mod.__file__).parent / ".jobs.sqlite"
        self.assertNotEqual(jobs_mod._db_path(), prod)


class TestBasicRoutes(unittest.TestCase):
    def test_healthz(self):
        r = client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

    def test_usage(self):
        r = client.get("/usage")
        self.assertEqual(r.status_code, 200)
        self.assertIn("calls", r.json())

    def test_jobs_list_empty_ok(self):
        r = client.get("/jobs?limit=5")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    def test_get_missing_job_404(self):
        r = client.get("/jobs/nonexistent-id")
        self.assertEqual(r.status_code, 404)


class TestDiscoverEndpoint(unittest.TestCase):
    def test_post_discover_runs_async(self):
        fake_result = {"category": "test", "synthesis": {"ranked_opportunities": []}}

        with patch("discover.discover", return_value=fake_result):
            r = client.post("/discover", json={"category": "test-cat", "geo": "US"})
        self.assertEqual(r.status_code, 200)
        job_id = r.json()["job_id"]
        self.assertTrue(job_id)

        final = _wait_for_job(job_id)
        self.assertEqual(final["state"], "complete")
        self.assertEqual(final["result"]["category"], "test")

    def test_post_discover_validates_min_length(self):
        r = client.post("/discover", json={"category": "x"})  # too short
        self.assertEqual(r.status_code, 422)

    def test_post_discover_error_captured(self):
        with patch("discover.discover", side_effect=RuntimeError("boom")):
            r = client.post("/discover", json={"category": "broken-cat"})
        job_id = r.json()["job_id"]
        final = _wait_for_job(job_id)
        self.assertEqual(final["state"], "error")
        self.assertIn("boom", final["error"])


class TestTasteEndpoint(unittest.TestCase):
    def test_post_taste(self):
        fake_profile = {"brand": "Foo", "confidence": 0.9}
        with patch("taste.decode_taste", return_value=fake_profile):
            r = client.post("/taste", json={"brand": "Foo", "domain": "foo.com"})
        self.assertEqual(r.status_code, 200)
        final = _wait_for_job(r.json()["job_id"])
        self.assertEqual(final["state"], "complete")
        self.assertEqual(final["result"]["brand"], "Foo")


class TestMatchEndpoint(unittest.TestCase):
    def test_post_match(self):
        fake_result = {"match_score": 77}
        with patch("match.score_match", return_value=fake_result):
            r = client.post("/match", json={
                "idea": "a fake product idea",
                "taste_profile": {"brand": "X"},
            })
        self.assertEqual(r.status_code, 200)
        final = _wait_for_job(r.json()["job_id"])
        self.assertEqual(final["result"]["match_score"], 77)

    def test_match_requires_taste_profile(self):
        r = client.post("/match", json={"idea": "valid idea string"})
        self.assertEqual(r.status_code, 422)


class TestFullPipelineEndpoint(unittest.TestCase):
    def test_post_full_runs_both_stages(self):
        fake_disc = {
            "category": "test",
            "synthesis": {
                "ranked_opportunities": [
                    {"brand": "Foo", "domain": "foo.com"},
                    {"brand": "Bar", "domain": "bar.com"},
                ]
            },
        }
        fake_taste = {"brand": "x", "confidence": 0.8}
        with patch("discover.discover", return_value=fake_disc), \
             patch("taste.decode_taste", return_value=fake_taste):
            r = client.post("/full", json={"category": "test-cat"})
        final = _wait_for_job(r.json()["job_id"], timeout=10)
        self.assertEqual(final["state"], "complete")
        self.assertIn("discover", final["result"])
        self.assertIn("tastes", final["result"])
        self.assertIn("Foo", final["result"]["tastes"])
        self.assertIn("Bar", final["result"]["tastes"])


class TestReportRoutesAreWiredToTheirHandlers(unittest.TestCase):
    """A decorator must sit directly above the function it registers.

    Inserting a helper BETWEEN @app.get("/jobs/{id}/report.html") and
    get_job_report_html registered the HELPER as the route: every request 422'd
    asking for a request body, and the whole suite stayed green because nothing
    exercised the route itself. It shipped, and a corpus regen wrote 16 reports
    whose HTML was an 82-byte validation error.
    """

    def test_report_html_route_maps_to_get_job_report_html(self):
        import api
        routes = {r.path: r for r in api.app.routes if hasattr(r, "endpoint")}
        self.assertEqual(routes["/jobs/{job_id}/report.html"].endpoint.__name__,
                         "get_job_report_html")

    def test_report_html_takes_no_request_body(self):
        """The failure signature: FastAPI asking for a body on a GET."""
        import api
        r = next(r for r in api.app.routes
                 if getattr(r, "path", "") == "/jobs/{job_id}/report.html")
        params = set(r.dependant.path_params and
                     [p.name for p in r.dependant.path_params] or [])
        self.assertEqual(params, {"job_id"})
        self.assertEqual(r.dependant.body_params, [])

    def test_report_pdf_route_maps_to_its_handler(self):
        import api
        routes = {r.path: r for r in api.app.routes if hasattr(r, "endpoint")}
        self.assertEqual(routes["/jobs/{job_id}/report.pdf"].endpoint.__name__,
                         "get_job_report_pdf")


class TestReportEndpoint(unittest.TestCase):
    def test_report_for_discover(self):
        fake = {
            "category": "skincare",
            "geo": "US",
            "synthesis": {
                "category_read": "Growing category",
                "ranked_opportunities": [
                    {"rank": 1, "brand": "Foo", "domain": "foo.com",
                     "opportunity_score": 85, "thesis": "strong trend",
                     "signals": {"trend_slope": 0.8},
                     "suggested_next_step": "decode_taste"},
                ],
            },
            "competitor_density": 2,
            "avg_opportunity_score": 45.0,
        }
        with patch("discover.discover", return_value=fake):
            r = client.post("/discover", json={"category": "skincare"})
        job_id = r.json()["job_id"]
        _wait_for_job(job_id)
        r = client.get(f"/jobs/{job_id}/report")
        self.assertEqual(r.status_code, 200)
        md = r.json()["markdown"]
        self.assertIn("# Opportunity Report", md)
        self.assertIn("Foo", md)
        self.assertIn("foo.com", md)
        self.assertIn("strong trend", md)

    def test_report_for_taste(self):
        # Unique brand/domain: /taste DEDUPES by params against completed jobs, and
        # TestTasteEndpoint.test_post_taste already completes a Foo/foo.com job with a
        # minimal mock — reusing its params returns THAT job (cached=True) and this
        # test's richer mock never runs (the old "flake": deterministic collision,
        # not randomness).
        fake = {
            "brand": "ReportCo",
            "confidence": 0.9,
            "confidence_reasoning": "rich data",
            "purchase_motivation": "fast results",
            "hook_angles_that_would_work": ["hook 1", "hook 2"],
        }
        with patch("taste.decode_taste", return_value=fake):
            r = client.post("/taste", json={"brand": "ReportCo", "domain": "reportco.example"})
        self.assertNotIn("cached", r.json())        # must be a fresh job, not a dedup hit
        job_id = r.json()["job_id"]
        _wait_for_job(job_id)
        r = client.get(f"/jobs/{job_id}/report")
        md = r.json()["markdown"]
        self.assertIn("# Audience taste profile", md)
        self.assertIn("fast results", md)
        self.assertIn("hook 1", md)

    def test_report_on_incomplete_job_409(self):
        # Make a job that errors
        with patch("discover.discover", side_effect=RuntimeError("x")):
            r = client.post("/discover", json={"category": "bad-cat"})
        job_id = r.json()["job_id"]
        _wait_for_job(job_id)
        # It's in 'error' state, not 'complete' → 409
        r = client.get(f"/jobs/{job_id}/report")
        self.assertEqual(r.status_code, 409)


class TestJobStore(unittest.TestCase):
    def test_create_and_get(self):
        jid = jobs_mod.create("test", {"a": 1})
        j = jobs_mod.get(jid)
        self.assertEqual(j["state"], "pending")
        self.assertEqual(j["params"]["a"], 1)

    def test_update_state(self):
        jid = jobs_mod.create("test", {})
        jobs_mod.update(jid, state="running")
        self.assertEqual(jobs_mod.get(jid)["state"], "running")
        jobs_mod.update(jid, state="complete", result={"x": 1})
        done = jobs_mod.get(jid)
        self.assertEqual(done["state"], "complete")
        self.assertEqual(done["result"]["x"], 1)

    def test_run_async_captures_error(self):
        def boom():
            raise ValueError("fail")

        jid = jobs_mod.create("test", {})
        jobs_mod.run_async(jid, boom)
        # Give the thread time
        for _ in range(50):
            j = jobs_mod.get(jid)
            if j["state"] in ("complete", "error"):
                break
            time.sleep(0.02)
        self.assertEqual(j["state"], "error")
        self.assertIn("fail", j["error"])


class TestRegenerateSection(unittest.TestCase):
    """Iter 32: POST /jobs/{id}/regenerate updates ONE 4P section in place."""

    def _seed_complete_plan(self):
        jid = jobs_mod.create("plan", {"description": "x" * 50})
        result = {
            "profile": {"name": "MintBox", "category": "candy"},
            "discover": {"synthesis": {"ranked_opportunities": [
                {"brand": "Brand A", "domain": "a.com", "thesis": "fresh"}
            ]}},
            "tastes": {"top": {"brand": "Brand A", "purchase_motivation": "treat"}},
            "max_diff": {"ranked_features": []},
            "van_westendorp": {"optimal_price_point": 12},
            "place": {"primary_channel": "DTC"},
            "4ps": {
                "executive_summary": "...",
                "product": {"narrative": "old product text", "key_takeaways": ["a"]},
                "price": {"narrative": "old price text", "key_takeaways": ["b"]},
                "place": {"narrative": "old place text", "key_takeaways": ["c"]},
                "promotion": {"narrative": "old promo text", "key_takeaways": ["d"]},
            },
        }
        jobs_mod.update(jid, state="complete", result=result)
        return jid

    def test_regenerate_happy_path(self):
        jid = self._seed_complete_plan()
        with patch("four_ps.call_json") as mock_llm:
            mock_llm.return_value = {
                "narrative": "NEW product narrative with citation¹.",
                "key_takeaways": ["sharper", "tighter", "clearer"],
            }
            r = client.post(f"/jobs/{jid}/regenerate", json={"section": "product", "steering": "more concrete"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["section"], "product")
        self.assertIn("NEW product narrative", data["revised"]["narrative"])
        # Verify the job was actually mutated and history kept
        job = jobs_mod.get(jid)
        self.assertEqual(job["result"]["4ps"]["product"]["narrative"], "NEW product narrative with citation¹.")
        self.assertEqual(len(job["result"]["_regen_history"]["product"]), 1)
        self.assertEqual(job["result"]["_regen_history"]["product"][0]["previous"]["narrative"], "old product text")

    def test_regenerate_invalid_section_400(self):
        jid = self._seed_complete_plan()
        r = client.post(f"/jobs/{jid}/regenerate", json={"section": "bogus", "steering": ""})
        self.assertEqual(r.status_code, 422)  # pydantic pattern rejection

    def test_regenerate_unknown_job_404(self):
        r = client.post("/jobs/does-not-exist/regenerate", json={"section": "product", "steering": ""})
        self.assertEqual(r.status_code, 404)

    def test_regenerate_incomplete_job_409(self):
        jid = jobs_mod.create("plan", {"description": "x" * 50})
        jobs_mod.update(jid, state="running")
        r = client.post(f"/jobs/{jid}/regenerate", json={"section": "product", "steering": ""})
        self.assertEqual(r.status_code, 409)

    def test_regenerate_wrong_kind_400(self):
        jid = jobs_mod.create("discover", {"category": "x"})
        jobs_mod.update(jid, state="complete", result={"foo": "bar"})
        r = client.post(f"/jobs/{jid}/regenerate", json={"section": "product", "steering": ""})
        self.assertEqual(r.status_code, 400)


class TestIntakeEndpoints(unittest.TestCase):
    """Iter 37: chat-based intake API endpoints."""

    def test_start_returns_session_and_opener(self):
        r = client.post("/intake/start", json={})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("session_id", data)
        self.assertEqual(data["ready"], False)
        self.assertTrue(len(data["assistant_message"]) > 10)

    def test_start_with_initial_message_calls_llm(self):
        with patch("intake.call_json") as m:
            m.return_value = {
                "extracted": {"product": "X"},
                "next_action": "ask",
                "next_question": "Tell me more about your target customer.",
            }
            r = client.post("/intake/start", json={"initial_message": "MintBox is a candy box"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["extracted"]["product"], "X")

    def test_message_unknown_session_404(self):
        r = client.post("/intake/message", json={"session_id": "no-such", "user_message": "hi"})
        self.assertEqual(r.status_code, 404)

    def test_message_validates_min_length(self):
        # Pydantic should reject empty user_message (min_length=1)
        r = client.post("/intake/message", json={"session_id": "x", "user_message": ""})
        self.assertEqual(r.status_code, 422)

    def test_get_intake_session(self):
        r = client.post("/intake/start", json={})
        sid = r.json()["session_id"]
        r2 = client.get(f"/intake/{sid}")
        self.assertEqual(r2.status_code, 200)
        body = r2.json()
        self.assertEqual(body["id"], sid)
        self.assertIn("messages", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
