"""Customer pipeline dispatch retains ownership, billing and bounded-run semantics."""
from copy import deepcopy
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

import api
import billing
import jobs
import plan
from packs.market_research_next import customer_pipeline as pipeline
from routes import research

DESCRIPTION = "Two tutoring studios in Canada, online lessons and school curriculum licenses."


@pytest.fixture
def client(tmp_path, monkeypatch):
    for key, value in {
        "JOBS_DB_PATH": str(tmp_path / "jobs.sqlite"), "CASTOR_ENV": "development",
        "CASTOR_REQUIRE_LOGIN": "0", "CASTOR_PAYWALL_OFF": "1", "CASTOR_STUB_REPORT": "",
        "STRIPE_SECRET_KEY": "", "STRIPE_WEBHOOK_SECRET": "", "RESEND_API_KEY": "",
        "CASTOR_DAILY_RUNS": "20",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(pipeline, "validate_customer_pipeline_config", lambda: None)
    monkeypatch.setattr(research, "_notify_owner", lambda *_: None)
    # Exercise the actual job runner and checkpoint/error publication synchronously.
    def run(job_id, work):
        outcome = jobs._run_one(job_id, work, lambda result: jobs.update(job_id, result=result))
        jobs.update(job_id, **outcome)
    monkeypatch.setattr(jobs, "run_async", run)
    return TestClient(api.app)


def test_treadle_receives_full_intake_without_inventing_default_us(client, monkeypatch):
    intake = {"confirmed": True, "facts": {"geography": "Canada"},
              "unknowns": ["pricing"], "unanswered": []}
    before = deepcopy(intake)
    selected = Mock(return_value={"_pipeline": "treadle", "_treadle_status": "complete"})
    legacy = Mock(side_effect=AssertionError("Treadle dispatched into legacy"))
    monkeypatch.setattr(pipeline, "run_customer_pipeline", selected)
    monkeypatch.setattr(plan, "run_plan", legacy)
    response = client.post("/plan", json={"description": DESCRIPTION,
                           "pipeline": "treadle", "intake": intake})
    assert response.status_code == 200, response.text
    row = client.get("/jobs/" + response.json()["job_id"]).json()
    assert row["params"]["pipeline"] == "treadle"
    assert row["state"] == "complete"
    assert selected.call_args.kwargs["intake"] == before
    assert selected.call_args.kwargs["geo"] is None
    assert selected.call_args.kwargs["report_style"] == "full"
    legacy.assert_not_called()


def test_every_new_api_report_defaults_to_treadle(client, monkeypatch):
    legacy = Mock(side_effect=AssertionError("New report dispatched into legacy"))
    selected = Mock(return_value={"_pipeline": "treadle", "_treadle_status": "complete"})
    monkeypatch.setattr(plan, "run_plan", legacy)
    monkeypatch.setattr(pipeline, "run_customer_pipeline", selected)
    # The old development stub must not become another way to bypass Treadle.
    monkeypatch.setenv("CASTOR_STUB_REPORT", "some-historical-report")
    response = client.post("/plan", json={"description": DESCRIPTION})
    assert response.status_code == 200, response.text
    row = client.get("/jobs/" + response.json()["job_id"]).json()
    assert row["params"]["pipeline"] == "treadle"
    assert row["params"]["report_style"] == "full"
    assert row["state"] == "complete"
    assert selected.call_count == 1
    legacy.assert_not_called()


def test_unavailable_prep_and_invalid_selection_do_not_create_or_charge(client, monkeypatch):
    def unavailable():
        raise ValueError("Set TREADLE_PREP_MODEL")
    monkeypatch.setattr(pipeline, "validate_customer_pipeline_config", unavailable)
    charge = Mock(side_effect=AssertionError("Invalid request charged"))
    monkeypatch.setattr(billing, "consume", charge)
    assert client.post("/plan", json={"description": DESCRIPTION}).status_code == 503
    assert client.post("/plan", json={"description": DESCRIPTION,
                                      "pipeline": "anything"}).status_code == 422
    for invalid in ({"pipeline": "legacy"}, {"report_style": "memo"},
                    {"report_style": "operating"}):
        assert client.post("/plan", json={"description": DESCRIPTION, **invalid}).status_code == 422
    assert client.get("/jobs").json() == []
    charge.assert_not_called()


def test_incomplete_run_preserves_checkpoint_and_refunds_credit(client, monkeypatch):
    owner = client.get("/auth/me").json()["owner"]
    billing._record(owner, "report", 1, None, None)
    partial = {"_pipeline": "treadle", "_treadle_status": "incomplete",
               "_treadle": {"execution": {"correction_rounds": 2}}}
    def incomplete(*_, progress=None, **__):
        progress(partial)
        raise pipeline.CustomerPipelineIncomplete(partial)
    monkeypatch.setattr(pipeline, "run_customer_pipeline", incomplete)
    response = client.post("/plan", json={"description": DESCRIPTION, "pipeline": "treadle"})
    assert response.status_code == 200
    row = client.get("/jobs/" + response.json()["job_id"]).json()
    assert row["state"] == "error"
    assert row["result"] == partial
    assert billing.balance(owner, "report") == 1


def test_restart_never_restarts_treadle_budget_or_switches_to_legacy(client, monkeypatch):
    owner = client.get("/auth/me").json()["owner"]
    job_id = jobs.create("plan", {"description": DESCRIPTION, "pipeline": "treadle",
                                  "_resumes": 1}, owner_id=owner)
    checkpoint = {"_pipeline": "treadle", "_steps_completed": ["prep"]}
    jobs.update(job_id, result=checkpoint)
    legacy = Mock(side_effect=AssertionError("Must not reset workflow on restart"))
    selected = Mock(side_effect=AssertionError("Must not reset workflow budget"))
    monkeypatch.setattr(plan, "run_plan", legacy)
    monkeypatch.setattr(pipeline, "run_customer_pipeline", selected)
    assert research.resume_interrupted() == 0
    row = jobs.get(job_id, owner_id=owner)
    assert row["state"] == "error"
    assert row["result"]["_steps_completed"] == ["prep"]
    assert "interrupted" in row["error"]
    legacy.assert_not_called()
    selected.assert_not_called()


@pytest.mark.parametrize("parent_pipeline", ["treadle", "legacy"])
def test_existing_report_cannot_launch_a_new_legacy_revision(client, parent_pipeline):
    owner = client.get("/auth/me").json()["owner"]
    parent = jobs.create("plan", {"pipeline": parent_pipeline}, owner_id=owner)
    jobs.update(parent, state="complete", result={"_pipeline": parent_pipeline})
    response = client.post("/plan", json={"description": DESCRIPTION, "previous_job_id": parent})
    assert response.status_code == 409


def test_treadle_workshop_refuses_before_any_model_or_credit(client):
    owner = client.get("/auth/me").json()["owner"]
    job_id = jobs.create("plan", {"pipeline": "treadle"}, owner_id=owner)
    jobs.update(job_id, state="complete", result={"_pipeline": "treadle"})
    response = client.post(f"/jobs/{job_id}/chat", json={"message": "Explain the market"})
    assert response.status_code == 409
    assert "Treadle" in response.json()["detail"]


def test_treadle_cannot_open_or_purchase_unusable_workshop_credits(client, monkeypatch):
    import iteration
    owner = client.get("/auth/me").json()["owner"]
    job_id = jobs.create("plan", {"pipeline": "treadle"}, owner_id=owner)
    jobs.update(job_id, state="complete", result={"_pipeline": "treadle"})
    grant = Mock(side_effect=AssertionError("Treadle granted unsupported workshop credits"))
    monkeypatch.setattr(iteration, "endow", grant)
    for path in (f"/jobs/{job_id}/workshop", f"/jobs/{job_id}/iteration"):
        assert client.get(path).status_code == 409
    # Even preview grants must be rejected before any entitlement changes.
    monkeypatch.setenv("CASTOR_PAYWALL_PREVIEW", "1")
    response = client.post("/billing/checkout", json={"kind": "workshop", "job_id": job_id})
    assert response.status_code == 409
    assert "Treadle" in response.json()["detail"]
    grant.assert_not_called()
    assert iteration.balance(job_id) == 0


@pytest.mark.parametrize("style", ["full", "memo", "operating"])
def test_historical_revision_is_refused_before_new_job_or_spending(client, monkeypatch, style):
    import iteration
    owner = client.get("/auth/me").json()["owner"]
    parent = jobs.create("plan", {"description": DESCRIPTION, "report_style": style}, owner_id=owner)
    jobs.update(parent, state="complete", result={"profile": {"name": "Historical report"}})
    charge = Mock(side_effect=AssertionError("Unavailable revision spent credit"))
    monkeypatch.setattr(billing, "consume", charge)
    monkeypatch.setattr(iteration, "spend", charge)
    response = client.post(f"/jobs/{parent}/revise")
    assert response.status_code == 409
    assert len(client.get("/jobs").json()) == 1
    charge.assert_not_called()


def test_paid_default_report_spends_once_and_normalizes_null_style(client, monkeypatch):
    owner = client.get("/auth/me").json()["owner"]
    billing._record(owner, "report", 2, None, None)
    selected = Mock(return_value={"_pipeline": "treadle", "_treadle_status": "complete"})
    monkeypatch.setattr(pipeline, "run_customer_pipeline", selected)
    response = client.post("/plan", json={"description": DESCRIPTION, "report_style": None})
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]
    assert billing.balance(owner, "report") == 1
    assert billing.paid_owner(job_id) == owner
    assert client.get("/jobs/" + job_id).json()["params"]["report_style"] == "full"
    assert selected.call_args.kwargs["report_style"] == "full"
    selected.assert_called_once()


def test_paid_quota_refusal_returns_credit_and_discards_job(client, monkeypatch):
    import quota
    owner = client.get("/auth/me").json()["owner"]
    billing._record(owner, "report", 1, None, None)
    def refuse(*_, **__):
        raise quota.QuotaExceeded("One report is already running.")
    monkeypatch.setattr(quota, "claim_run_slot", refuse)
    selected = Mock(side_effect=AssertionError("Refused request started research"))
    monkeypatch.setattr(pipeline, "run_customer_pipeline", selected)
    response = client.post("/plan", json={"description": DESCRIPTION})
    assert response.status_code == 429
    assert billing.balance(owner, "report") == 1
    assert client.get("/jobs").json() == []
    selected.assert_not_called()


def test_foreign_revision_reference_stays_private_and_cannot_spend(client, monkeypatch):
    foreign = jobs.create("plan", {"pipeline": "treadle"}, owner_id="another-owner")
    charge = Mock(side_effect=AssertionError("Foreign reference spent credit"))
    monkeypatch.setattr(billing, "consume", charge)
    response = client.post("/plan", json={"description": DESCRIPTION, "previous_job_id": foreign})
    assert response.status_code == 404
    assert client.post(f"/jobs/{foreign}/revise").status_code == 404
    assert client.get("/jobs").json() == []
    charge.assert_not_called()
