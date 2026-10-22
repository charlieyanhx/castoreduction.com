"""Offline customer adapter tests exercise the real isolated PREP/report workflow."""
from __future__ import annotations

from copy import deepcopy
import json
import os
import threading
from unittest.mock import patch

import pytest

from packs.market_research_next import REPORT_ORDER
from packs.market_research_next.customer_pipeline import (
    CustomerPipelineIncomplete, customer_report, render_customer_report,
    run_customer_pipeline, serialize_customer_report, validate_customer_pipeline_config,
)
from packs.market_research_next.prep import REPORT_SECTIONS, VENTURE_FIELDS
from treadle.execution_limits import charge_model_call
from treadle.registry import REPORT_TYPES
from treadle.run import Report
from treadle.workflow import ReviewIssue

DESCRIPTION = "A hybrid fitness business serves Toronto in two studios and online."
INTAKE = {
    "confirmed": True,
    "facts": {"original_description": DESCRIPTION,
              "business_model": "Memberships $50/month; training $80/session",
              "locations": [{"name": "North"}, {"name": "South"}],
              "delivery_mode": "In person and online", "geography": "Toronto"},
    "slots": {}, "unknowns": ["pricing", "service_capacity"],
    "unanswered": ["service_capacity"],
}


def fixture_business_model(*, system, user):
    charge_model_call()
    payload = json.loads(user)
    if "assessment_ids" in payload:
        return {"assessments": [{"id": key, "status": "supported", "reason": "Input fixture"}
                                for key in payload["assessment_ids"]]}
    return {"base_revision": payload["base_revision"], "target_id": payload["target_id"],
            "nodes": [], "facts": [], "relationships": [], "gaps": [],
            "expand_node_ids": []}


def fixture_prep_model(*, system, user):
    charge_model_call()
    assert "ACCEPTED BUSINESS STRUCTURE" in user
    return {
        "venture": {field: None for field in VENTURE_FIELDS},
        "research_spec": {key: {"scope": "Analyze the hybrid fitness business",
                                 "questions": ["What remains uncertain?"],
                                 "assumptions_to_test": []} for key in REPORT_SECTIONS},
    }


def fixture_research(key, context, *, issues=()):
    charge_model_call()
    assert context["prep"]["business_context"]["construction"]["support_review_status"] == "complete"
    return {"title": key, "narrative": "<script>danger</script> is untrusted input.",
            "findings": [], "assumptions": [], "uncertainties": [],
            "metrics": {"repaired": bool(issues)}}


def clean_review(result, verdicts):
    charge_model_call()
    return ()


def unresolved_review(result, verdicts):
    charge_model_call()
    return (ReviewIssue("uncertain_scope", "Conflicting market scope remains.", ("market_size",)),)


def run_fixture(**kwargs):
    return run_customer_pipeline(DESCRIPTION, intake=deepcopy(INTAKE),
                                 prep_model=fixture_prep_model,
                                 business_model=fixture_business_model,
                                 research=fixture_research, review=kwargs.pop("review", clean_review),
                                 **kwargs)


def test_customer_inputs_reach_real_business_prep_and_bounded_workflow_with_parent_progress():
    before = set(REPORT_TYPES)
    original = deepcopy(INTAKE)
    caller = (os.getpid(), threading.get_ident())
    checkpoints = []

    def checkpoint(value):
        assert (os.getpid(), threading.get_ident()) == caller
        checkpoints.append(deepcopy(value))
        value["_steps_completed"].clear()  # A consumer cannot mutate the engine receipt.

    result = run_fixture(progress=checkpoint, report_style="full")
    assert set(REPORT_TYPES) == before
    assert INTAKE == original
    assert result["_pipeline"] == "treadle"
    assert result["_treadle_status"] == "complete"
    assert result["profile"]["name"] == "Venture research report"
    saved = result["_treadle"]
    assert saved["type"] == "market_research_next"
    brief = saved["result"]["brief"]
    assert brief == {"description": DESCRIPTION, "intake": original, "report_style": "full"}
    prep = saved["result"]["prep"]
    assert prep["venture"]["customer_inputs"] == brief
    assert prep["source_tracing_enabled"] is False
    assert "business_context" in prep
    assert prep["venture"]["attributes"]["pricing"]["basis"] == "unknown"
    execution = saved["execution"]
    assert execution["limits"]["run_timeout_s"] == 900
    assert execution["correction_rounds"] == 0
    assert set(result["_steps_completed"]) == {"prep", *REPORT_ORDER}
    assert checkpoints[0]["_treadle_status"] == "running"
    assert any("prep" in item["_steps_completed"] and "_treadle" not in item
               for item in checkpoints)
    assert checkpoints[-1] == result
    assert customer_report(json.loads(json.dumps(result))).ok
    rendered = render_customer_report(result)
    assert "<script>danger</script>" not in rendered
    assert "&lt;script&gt;danger&lt;/script&gt;" in rendered
    for key in REPORT_ORDER:
        assert f'id="{key}"' in rendered


def test_unresolved_review_stops_at_two_global_corrections_and_checkpoints_partial_report():
    before = set(REPORT_TYPES)
    checkpoints = []
    with pytest.raises(CustomerPipelineIncomplete) as failure:
        run_fixture(progress=lambda result: checkpoints.append(result), review=unresolved_review)
    result = failure.value.result
    assert set(REPORT_TYPES) == before
    assert result["_treadle_status"] == "incomplete"
    assert checkpoints[-1] == result
    assert result["_treadle"]["execution"]["correction_rounds"] == 2
    assert result["_treadle"]["execution"]["issues"]
    assert not customer_report(result).ok
    assert "unresolved findings" in render_customer_report(result)
    assert "Conflicting market scope remains." in render_customer_report(result)


def test_skipped_sections_are_not_called_complete_even_when_legacy_report_ok_is_true():
    report = Report("fixture", {"brief": {"description": DESCRIPTION}},
                    {"sections": [{"key": key, "status": "skipped", "reason": "missing"}
                                  for key in ("prep", *REPORT_ORDER)]})
    assert report.ok
    result = serialize_customer_report(report)
    assert result["_treadle_status"] == "failed"
    assert result["_steps_completed"] == []
    with pytest.raises(ValueError, match="No usable"):
        render_customer_report(result)


def test_unexpected_engine_failure_cleans_up_registration():
    before = set(REPORT_TYPES)
    with patch("packs.market_research_next.customer_pipeline.run_report",
               side_effect=RuntimeError("engine failed")):
        with pytest.raises(RuntimeError, match="engine failed"):
            run_fixture()
    assert set(REPORT_TYPES) == before


def test_no_default_geography_is_added_and_explicit_geography_is_preserved():
    observed = []

    def capture(key, brief, **kwargs):
        observed.append(deepcopy(brief))
        return Report(key, {}, {})

    with patch("packs.market_research_next.customer_pipeline.run_report", side_effect=capture):
        for options in ({}, {"geo": "Canada"}):
            with pytest.raises(CustomerPipelineIncomplete):
                run_fixture(**options)
    assert "geo" not in observed[0]
    assert observed[1]["geo"] == "Canada"


def test_preflight_requires_explicit_model_configured_provider_and_paid_opt_in(monkeypatch):
    monkeypatch.delenv("TREADLE_PREP_MODEL", raising=False)
    with pytest.raises(ValueError, match="TREADLE_PREP_MODEL"):
        validate_customer_pipeline_config()
    monkeypatch.setenv("TREADLE_PREP_MODEL", "configured-prep-model")
    with patch("treadle.llm.backend_configured", return_value=False), \
            patch("treadle.llm.paid_backend_allowed", return_value=True):
        with pytest.raises(ValueError, match="Anthropic"):
            validate_customer_pipeline_config()
    with patch("treadle.llm.backend_configured", return_value=True), \
            patch("treadle.llm.paid_backend_allowed", return_value=False):
        with pytest.raises(ValueError, match="opt-in"):
            validate_customer_pipeline_config()
    with patch("treadle.llm.backend_configured", return_value=True), \
            patch("treadle.llm.paid_backend_allowed", return_value=True):
        assert validate_customer_pipeline_config() is None


def test_nonfinite_input_rejected_before_any_engine_or_registry_mutation():
    before = set(REPORT_TYPES)
    with pytest.raises(ValueError):
        run_customer_pipeline(DESCRIPTION, intake={"facts": {"cost": float("nan")}},
                              prep_model=fixture_prep_model)
    assert set(REPORT_TYPES) == before


def test_restore_requires_the_distinguishable_treadle_receipt():
    with pytest.raises(ValueError, match="not a Treadle"):
        customer_report({"_treadle": {}})
    with pytest.raises(ValueError, match="not available"):
        customer_report({"_pipeline": "treadle", "_treadle_status": "running"})


def test_incomplete_render_is_prominent_even_when_legacy_report_ok_permits_skips():
    report = Report("fixture", {"summary": {"narrative": "Partial summary"}},
                    {"sections": [{"key": "summary", "status": "ok"}]})
    assert report.ok
    result = serialize_customer_report(report)
    assert result["_treadle_status"] == "incomplete"
    assert "<strong>Incomplete report</strong>" in render_customer_report(result)
