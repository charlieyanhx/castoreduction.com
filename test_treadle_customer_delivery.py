"""Owned customer delivery uses the stored Treadle receipt, never the legacy renderer."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from packs.market_research_next import REPORT_ORDER
from packs.market_research_next.customer_pipeline import serialize_customer_report
from treadle.run import Report


SECRET = "Private Toronto hybrid venture"


def _result(status="complete"):
    keys = ("prep",) + REPORT_ORDER
    produced = keys if status == "complete" else keys[:3] if status == "incomplete" else ()
    values = {key: {"narrative": SECRET + ": " + key} for key in produced}
    if "prep" in values:
        values["prep"] = {"venture": {"attributes": {}}}
    values["brief"] = {"description": SECRET, "intake": {"facts": {"private": SECRET}}}
    verdicts = [{"key": key, "status": "ok" if key in produced else "failed",
                 "reason": "Provider unavailable" if key not in produced else ""}
                for key in keys]
    report = Report("market_research_next", values,
                    {"sections": verdicts, "failed": len(keys) - len(produced)})
    payload = serialize_customer_report(report)
    assert payload["_treadle_status"] == status
    return payload


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBS_DB_PATH", str(tmp_path / "jobs.sqlite"))
    monkeypatch.setenv("HTTP_CACHE_PATH", str(tmp_path / "http.sqlite"))
    monkeypatch.setenv("CASTOR_ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", "test-treadle-delivery-owner-isolation")
    import cache
    import jobs
    monkeypatch.setattr(cache, "DB", tmp_path / "cache.sqlite")
    jobs._reset_for_tests()
    import api
    client = TestClient(api.app, base_url="https://testserver")
    owner = client.get("/auth/me").json()["owner"]

    def seed(status="complete", state=None, result=None):
        jid = jobs.create("plan", {"description": SECRET, "pipeline": "treadle"},
                          owner_id=owner)
        jobs.update(jid, state=state or ("complete" if status == "complete" else "error"),
                    result=_result(status) if result is None else result,
                    error="" if status == "complete" else "Research did not finish")
        return jid

    yield client, seed
    client.close()
    jobs._reset_for_tests()


@pytest.mark.parametrize("status", ["complete", "incomplete"])
def test_owned_html_and_raw_receipt_remain_available(delivery, status):
    client, seed = delivery
    jid = seed(status)
    with patch("report.render_html.render_report_html", side_effect=AssertionError("legacy renderer")):
        response = client.get(f"/jobs/{jid}/report.html")
    assert response.status_code == 200
    assert SECRET in response.text
    assert f'/jobs/{jid}/report.pdf' in response.text
    assert "My reports" in response.text
    if status == "incomplete":
        assert "incomplete" in response.text.lower()
        assert "Provider unavailable" in response.text
    raw = client.get(f"/jobs/{jid}/report")
    assert raw.status_code == 200
    assert raw.json()["status"] == status
    assert raw.json()["report"]["result"]["brief"]["intake"]["facts"]["private"] == SECRET


@pytest.mark.parametrize("status", ["complete", "incomplete"])
def test_pdf_exports_the_same_treadle_sections_and_limitations(delivery, status):
    client, seed = delivery
    jid = seed(status)
    with patch("report.pdf.available_engine", return_value="test"), \
            patch("report.pdf.render_pdf", return_value=b"%PDF-test") as render:
        response = client.get(f"/jobs/{jid}/report.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert f'market-research-{jid[:8]}.pdf' in response.headers["content-disposition"]
    html, meta = render.call_args.args
    assert SECRET in html
    assert "My reports" not in html
    assert meta["job_id"] == jid
    if status == "incomplete":
        assert "incomplete" in html.lower()


@pytest.mark.parametrize("status,state", [("failed", "error"), ("failed", "complete"),
                                         ("complete", "running")])
def test_fatal_and_running_jobs_do_not_masquerade_as_reports(delivery, status, state):
    client, seed = delivery
    jid = seed(status, state=state)
    with patch("report.pdf.render_pdf", side_effect=AssertionError("no artifact")):
        assert client.get(f"/jobs/{jid}/report.pdf").status_code == 409
    assert client.get(f"/jobs/{jid}/report").status_code == 409
    response = client.get(f"/jobs/{jid}/report.html")
    assert response.status_code == (202 if state == "running" else 409)
    assert SECRET not in response.text
    assert not client.get(f"/jobs/{jid}").json()["report_available"]


def test_library_distinguishes_complete_partial_and_failed_without_fake_viability(delivery):
    client, seed = delivery
    ids = {status: seed(status) for status in ("complete", "incomplete", "failed")}
    rows = {row["id"]: row for row in client.get("/jobs").json()}
    for status, jid in ids.items():
        row = rows[jid]
        assert row["pipeline"] == "treadle"
        assert row["report_status"] == status
        assert row["report_available"] == (status != "failed")
        assert row["publishable"] == (status == "complete")
        assert row["idea_name"] == SECRET
        assert "viability_score" not in row
        assert "tier" not in row
    assert rows[ids["incomplete"]]["state"] == "error"


@pytest.mark.parametrize("result", [{}, {"_pipeline": "treadle", "_treadle_status": "complete"}])
def test_missing_receipt_cannot_fall_back_to_legacy_report(delivery, result):
    client, seed = delivery
    jid = seed(result=result)
    assert client.get(f"/jobs/{jid}/report.html").status_code == 409
    assert client.get(f"/jobs/{jid}/report.pdf").status_code == 409
    assert not client.get(f"/jobs/{jid}").json()["report_available"]


def test_legacy_only_operations_refuse_before_spending_or_running(delivery):
    client, seed = delivery
    jid = seed()
    operations = [("get", "onepager.html", None), ("get", "trace", None),
                  ("get", "share", None), ("get", "iteration", None),
                  ("post", "share", {}), ("post", "credits", {}),
                  ("post", "regenerate", {"section": "product"}),
                  ("post", "revise", None)]
    with patch("api.post_plan", side_effect=AssertionError("legacy pipeline")), \
            patch("four_ps.regenerate_section", side_effect=AssertionError("model call")), \
            patch("sharing.publish", side_effect=AssertionError("public share")):
        for method, path, body in operations:
            response = client.request(method, f"/jobs/{jid}/{path}", json=body)
            assert response.status_code == 409, path
            assert "Treadle" in response.json()["detail"]
    import iteration
    assert not (iteration.get_state(jid).get("workshop") or {}).get("granted")


def test_every_delivery_and_unsupported_operation_checks_owner_first(delivery):
    _, seed = delivery
    jid = seed("incomplete")
    import api
    stranger = TestClient(api.app, base_url="https://testserver")
    operations = [("get", "report.html", None), ("get", "report.pdf", None),
                  ("get", "report", None), ("get", "onepager.html", None),
                  ("get", "trace", None), ("get", "share", None),
                  ("get", "iteration", None),
                  ("post", "credits", {}),
                  ("post", "share", {}), ("post", "revise", None),
                  ("post", "regenerate", {"section": "product"})]
    for method, path, body in operations:
        response = stranger.request(method, f"/jobs/{jid}/{path}", json=body)
        assert response.status_code == 404, path
        assert SECRET not in response.text
    assert stranger.get("/jobs").json() == []
    stranger.close()


def test_public_route_does_not_render_an_accidentally_listed_treadle_receipt(delivery):
    client, seed = delivery
    jid = seed()
    with patch("sharing.is_shared", return_value=True):
        response = client.get(f"/library/{jid}/report.html")
    assert response.status_code == 404
    assert SECRET not in response.text
