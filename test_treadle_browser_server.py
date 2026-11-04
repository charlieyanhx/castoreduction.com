"""Offline HTTP checks for the localhost browser runner; no provider calls."""
from dataclasses import asdict
import json
from threading import Event
import time

from fastapi.testclient import TestClient
import pytest

from scripts.treadle_browser_server import MAX_BODY_BYTES, create_app
from treadle.run import Report


HEADERS = {"X-Treadle-Run": "1", "Origin": "http://127.0.0.1:8779"}
BRIEF = {"description": "A hybrid tutoring and school licensing business"}


def _report(*, incomplete=False):
    return Report(
        type="market_research_next",
        result={"brief": BRIEF, "summary": {
            "narrative": "<script>alert('untrusted')</script>",
            "findings": [], "assumptions": [], "uncertainties": [],
        }},
        sections={"sections": []},
        execution={"correction_rounds": 2 if incomplete else 0,
                   "stop_reason": "correction_limit" if incomplete else "completed",
                   "usage": {"actual": {"model_calls": 1, "tool_calls": 0}},
                   "issues": [{"message": "Material conflict"}] if incomplete else []},
    )


def _wait(client, run_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get(f"/runs/{run_id}")
        assert response.status_code == 200
        value = response.json()
        if value["status"] != "running":
            return value
        time.sleep(0.01)
    raise AssertionError("Offline report did not finish.")


def _client(tmp_path, runner):
    return TestClient(create_app(out_dir=tmp_path, runner=runner),
                      base_url="http://127.0.0.1:8779")


@pytest.mark.parametrize("incomplete", [False, True])
def test_local_submission_preserves_status_and_artifacts(tmp_path, incomplete):
    expected = _report(incomplete=incomplete)

    def runner(brief, directory):
        assert brief == BRIEF
        (directory / "events.jsonl").write_text(
            '{"key":"prep","status":"ok"}\n{"incomplete":', encoding="utf-8")
        return expected

    with _client(tmp_path, runner) as client:
        assert client.get("/health").json() == {
            "ok": True, "pipeline": "market_research_next", "live_providers": False}
        page = client.get("/")
        assert 'for="brief">Business brief (JSON)' in page.text
        assert "Start report" in page.text
        assert client.get("/favicon.ico").status_code == 204
        response = client.post("/runs", json={"brief": BRIEF}, headers=HEADERS)
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        status = _wait(client, run_id)
        assert status["status"] == ("incomplete" if incomplete else "completed")
        assert status["ok"] is not incomplete
        assert status["correction_rounds"] == (2 if incomplete else 0)
        assert status["elapsed_seconds"] >= 0
        stored = tmp_path / run_id
        assert json.loads((stored / "status.json").read_text()) == status
        assert json.loads((stored / "brief.json").read_text()) == BRIEF
        report_json = client.get(f"/runs/{run_id}/report.json")
        assert report_json.json() == json.loads(json.dumps(asdict(expected)))
        rendered = client.get(f"/runs/{run_id}/report")
        assert "<script>alert" not in rendered.text
        assert "&lt;script&gt;" in rendered.text
        assert rendered.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in rendered.headers["content-security-policy"]
        assert client.get(f"/runs/{run_id}/events").json() == [{"key": "prep", "status": "ok"}]


@pytest.mark.parametrize("payload", [
    {}, {"brief": []}, {"brief": {}}, {"brief": {"description": ""}},
    {"brief": {**BRIEF, "intake": []}}, {"brief": BRIEF, "model": "untrusted"},
])
def test_invalid_submission_never_dispatches(tmp_path, payload):
    def forbidden(*_):
        raise AssertionError("Invalid brief reached the runner.")

    with _client(tmp_path, forbidden) as client:
        assert client.post("/runs", json=payload, headers=HEADERS).status_code == 422
    assert list(tmp_path.iterdir()) == []


def test_nonfinite_and_oversize_body_never_dispatch(tmp_path):
    with _client(tmp_path, lambda *_: pytest.fail("Should not dispatch")) as client:
        headers = {**HEADERS, "Content-Type": "application/json"}
        response = client.post("/runs", content='{"brief":{"description":"test","price":NaN}}',
                               headers=headers)
        assert response.status_code == 422
        response = client.post("/runs", content="x" * (MAX_BODY_BYTES + 1), headers=headers)
        assert response.status_code == 413
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("headers,status", [
    ({}, 403),
    ({"X-Treadle-Run": "1", "Origin": "https://attacker.example"}, 403),
    ({"X-Treadle-Run": "1", "Origin": "null"}, 403),
    ({"X-Treadle-Run": "1", "Origin": "http://127.0.0.1:8778"}, 403),
    ({"X-Treadle-Run": "1", "Sec-Fetch-Site": "cross-site"}, 403),
    ({"X-Treadle-Run": "1", "Content-Type": "text/plain"}, 415),
    ({**HEADERS, "Host": "attacker.example"}, 403),
])
def test_cross_origin_and_simple_posts_are_rejected(tmp_path, headers, status):
    with _client(tmp_path, lambda *_: pytest.fail("Should not dispatch")) as client:
        assert client.post("/runs", json={"brief": BRIEF}, headers=headers).status_code == status
    assert list(tmp_path.iterdir()) == []


def test_running_submission_cannot_be_duplicated_and_resume_is_read_only(tmp_path):
    release = Event()
    entered = Event()
    count = []

    def runner(*_):
        count.append(1)
        entered.set()
        assert release.wait(3)
        return _report()

    with _client(tmp_path, runner) as client:
        try:
            run_id = client.post("/runs", json={"brief": BRIEF}, headers=HEADERS).json()["run_id"]
            assert entered.wait(1)
            duplicate = client.post("/runs", json={"brief": BRIEF}, headers=HEADERS)
            assert duplicate.status_code == 409
            assert duplicate.json()["run_id"] == run_id
            assert client.get(f"/?run={run_id}").status_code == 200
            assert client.get(f"/runs/{run_id}/report").status_code == 409
            assert client.get(f"/runs/{run_id}").json()["status"] == "running"
            assert count == [1]
        finally:
            release.set()
        assert _wait(client, run_id)["status"] == "completed"


def test_errors_do_not_leak_provider_messages(tmp_path):
    def runner(*_):
        raise RuntimeError("secret-api-key and private user payload")

    with _client(tmp_path, runner) as client:
        run_id = client.post("/runs", json={"brief": BRIEF}, headers=HEADERS).json()["run_id"]
        status = _wait(client, run_id)
        assert status["status"] == "failed"
        assert status["ok"] is False
        assert "secret" not in json.dumps(status)
        assert "secret" not in (tmp_path / run_id / "status.json").read_text()
        assert client.get(f"/runs/{run_id}/report.json").status_code == 409
        assert client.get("/runs/not-a-run").status_code == 404
        assert client.get("/runs/" + "0" * 32).status_code == 404


def test_idempotency_key_returns_original_run_without_redispatch(tmp_path):
    calls = []
    request_id = "b" * 32
    headers = {**HEADERS, "Idempotency-Key": request_id}

    def runner(*_):
        calls.append(1)
        return _report()

    with _client(tmp_path, runner) as client:
        first = client.post("/runs", json={"brief": BRIEF}, headers=headers)
        assert first.status_code == 202
        assert first.json()["run_id"] == request_id
        _wait(client, request_id)
        repeated = client.post("/runs", json={"brief": BRIEF}, headers=headers)
        assert repeated.status_code == 202
        assert repeated.json() == {"run_id": request_id, "status": "completed"}
        different = client.post("/runs", json={"brief": {"description": "Other business"}},
                                headers=headers)
        assert different.status_code == 409
        assert client.post("/runs", json={"brief": BRIEF},
                           headers={**HEADERS, "Idempotency-Key": "../bad"}).status_code == 422
    assert calls == [1]


@pytest.mark.parametrize("outcome", ["completed", "incomplete", "failed"])
def test_restart_can_read_saved_terminal_runs_and_deduplicate(tmp_path, outcome):
    request_id = "c" * 32
    headers = {**HEADERS, "Idempotency-Key": request_id}

    def original_runner(*_):
        if outcome == "failed":
            raise RuntimeError("Sensitive provider detail")
        return _report(incomplete=outcome == "incomplete")

    with _client(tmp_path, original_runner) as client:
        client.post("/runs", json={"brief": BRIEF}, headers=headers)
        assert _wait(client, request_id)["status"] == outcome

    with _client(tmp_path, lambda *_: pytest.fail("Recovery must never execute")) as client:
        assert client.get(f"/?run={request_id}").status_code == 200
        status = client.get(f"/runs/{request_id}")
        assert status.status_code == 200
        assert status.json()["status"] == outcome
        assert client.post("/runs", json={"brief": BRIEF}, headers=headers).json() == {
            "run_id": request_id, "status": outcome}
        expected_code = 409 if outcome == "failed" else 200
        assert client.get(f"/runs/{request_id}/report").status_code == expected_code
        assert client.get(f"/runs/{request_id}/report.json").status_code == expected_code


def test_saved_running_receipt_is_interrupted_without_restarting(tmp_path):
    request_id = "d" * 32
    directory = tmp_path / request_id
    directory.mkdir()
    original_status = {"run_id": request_id, "status": "running", "started_at": time.time()}
    (directory / "status.json").write_text(json.dumps(original_status))
    (directory / "brief.json").write_text(json.dumps(BRIEF))
    headers = {**HEADERS, "Idempotency-Key": request_id}
    with _client(tmp_path, lambda *_: pytest.fail("Must not restart interrupted work")) as client:
        value = client.get(f"/runs/{request_id}").json()
        assert value["status"] == "interrupted"
        assert value["ok"] is False
        assert value["error_type"] == "ServerRestart"
        assert client.get(f"/runs/{request_id}/report").status_code == 409
        assert client.post("/runs", json={"brief": BRIEF}, headers=headers).json() == {
            "run_id": request_id, "status": "interrupted"}
    assert json.loads((directory / "status.json").read_text()) == original_status


def test_missing_saved_artifact_is_visible_failure(tmp_path):
    request_id = "e" * 32
    directory = tmp_path / request_id
    directory.mkdir()
    (directory / "status.json").write_text(json.dumps({
        "run_id": request_id, "status": "completed", "ok": True,
        "started_at": time.time(), "finished_at": time.time(),
    }))
    with _client(tmp_path, lambda *_: pytest.fail("Must not regenerate missing artifacts")) as client:
        value = client.get(f"/runs/{request_id}").json()
        assert value["status"] == "failed"
        assert value["error_type"] == "MissingArtifact"
        assert client.get(f"/runs/{request_id}/report.json").status_code == 409
