"""Offline black-box checks of the default selectable closed-loop report path."""
from __future__ import annotations

from dataclasses import asdict
import json
import time

import pytest

from packs.market_research_next import REPORT_ORDER, build_report_type
from packs.market_research_next.contracts import validate_prep_output, validate_section_output
from packs.market_research_next.prep import REPORT_SECTIONS, VENTURE_FIELDS, prepare
from treadle import register, run_report
from treadle.execution_limits import ExecutionLimits, charge_model_call, charge_tool_call
from treadle.registry import REPORT_TYPES
from treadle.workflow import ReviewIssue
from test_treadle_business_pipeline import minimal_business_model


def fake_prep(**kwargs):
    charge_model_call()
    return {
        "venture": {field: None for field in VENTURE_FIELDS},
        "research_spec": {
            name: {"scope": "Customer's stated venture", "questions": [f"Research {name}"],
                   "assumptions_to_test": []}
            for name in REPORT_SECTIONS
        },
    }


def fake_research(name, context, *, issues=()):
    charge_model_call()
    if name in {"competitive_landscape", "customer_voice", "market_size"}:
        charge_tool_call()
    return {"title": name, "narrative": "Offline fixture analysis", "findings": [],
            "assumptions": [], "uncertainties": ["Offline fixture, no real research"],
            "metrics": {"input_keys": sorted(context), "repair": bool(issues)}}


def no_issues(result, verdicts):
    charge_model_call()
    return ()


def repair_once(result, verdicts):
    charge_model_call()
    if not result.get("market_size", {}).get("metrics", {}).get("repair"):
        return (ReviewIssue("scope_conflict", "Refresh market scope", ("market_size",)),)
    return ()


def run_fixture(key, **kwargs):
    report_type = build_report_type(key=key, prep_model=fake_prep,
                                    business_model=minimal_business_model,
                                    research=fake_research, review=no_issues, **kwargs)
    register(report_type)
    try:
        return run_report(key, {"description": "A hybrid clinic service", "geography": "Canada"})
    finally:
        REPORT_TYPES.pop(key, None)


def test_default_pack_is_isolated_and_has_finite_limits():
    default = build_report_type()
    assert isinstance(default.workflow.limits, ExecutionLimits)
    report = run_fixture("closed_loop_success")
    assert report.ok
    assert set(report.result) == {"brief", "prep", *REPORT_ORDER}
    assert report.execution["correction_rounds"] == 0
    assert report.execution["usage"]["actual"] == {"model_calls": 12, "tool_calls": 3}
    assert report.execution["usage"]["charged"] == report.execution["usage"]["actual"]
    assert report.result["prep"]["venture"]["customer_inputs"] == report.result["brief"]
    assert set(report.result["revenue"]["metrics"]["input_keys"]) == {
        "prep", "market_size", "competitive_landscape"}
    assert not report.stale
    json.dumps(asdict(report), allow_nan=False)


def test_targeted_correction_preserves_unrelated_results_in_isolated_mode():
    report_type = build_report_type(key="closed_loop_repair", prep_model=fake_prep,
                                    business_model=minimal_business_model,
                                    research=fake_research, review=repair_once)
    register(report_type)
    try:
        report = run_report(report_type.key, {"description": "Offline report"})
    finally:
        REPORT_TYPES.pop(report_type.key, None)
    assert report.ok
    assert report.execution["correction_rounds"] == 1
    assert set(report.execution["history"][1]["executed"]) == {"market_size", "revenue", "summary"}
    assert report.result["summary"]["metrics"]["repair"]
    assert not report.result["competitive_landscape"]["metrics"]["repair"]
    assert report.execution["usage"]["actual"] == {"model_calls": 16, "tool_calls": 4}
    assert not report.stale


def test_real_global_model_budget_stops_before_more_dispatches():
    report = run_fixture("closed_loop_budget", limits=ExecutionLimits(max_model_calls=2))
    assert not report.ok
    assert report.execution["usage"]["actual"]["model_calls"] <= 2
    assert report.execution["usage"]["charged"]["model_calls"] <= 2
    assert report.execution["issues"]
    assert "summary" not in report.result


def test_hung_custom_reviewer_cannot_hold_report_open():
    def stuck_review(result, verdicts):
        time.sleep(30)
        return ()

    report_type = build_report_type(key="closed_loop_hung_review", prep_model=fake_prep,
                                    business_model=minimal_business_model,
                                    research=fake_research, review=stuck_review,
                                    limits=ExecutionLimits(review_timeout_s=0.4))
    register(report_type)
    started = time.monotonic()
    try:
        report = run_report(report_type.key, {"description": "Offline report"})
    finally:
        REPORT_TYPES.pop(report_type.key, None)
    assert time.monotonic() - started < 15
    assert not report.ok
    assert report.execution["issues"]
    assert all(name in report.result for name in REPORT_ORDER)


def test_contract_rejects_nonfinite_or_malformed_section_output():
    valid = fake_research("summary", {})
    for bad in ({}, {**valid, "metrics": {"revenue": float("nan")}},
                {**valid, "uncertainties": "none"}, {**valid, "error": "failed"},
                {**valid, "research_status": "partial", "uncertainties": []}):
        with pytest.raises(ValueError):
            validate_section_output(bad)


def test_pack_rejects_nonfinite_customer_input_before_starting_workers():
    pack = build_report_type(prep_model=lambda **kwargs: pytest.fail("Must not run"))
    with pytest.raises(ValueError, match="finite"):
        pack.validate({"description": "A business", "pricing": float("inf")})


def test_prep_contract_preserves_fixed_deliverables_and_input_fingerprint():
    prep = prepare({"description": "A business"}, model=fake_prep)
    validate_prep_output(prep)
    prep["research_spec"]["revenue"]["required_deliverables"] = ["Invented section contract"]
    with pytest.raises(ValueError, match="code-owned"):
        validate_prep_output(prep)
    prep = prepare({"description": "A business"}, model=fake_prep)
    prep["venture"]["customer_inputs"]["description"] = "Changed afterward"
    with pytest.raises(ValueError, match="fingerprint"):
        validate_prep_output(prep)


def test_cli_describes_selected_limits_without_research(capsys):
    from scripts.treadle_market_research import main

    assert main(["--describe", "--run-timeout", "30", "--model-calls", "5",
                 "--correction-rounds", "1"]) == 0
    description = json.loads(capsys.readouterr().out)
    assert description["execution_limits"]["run_timeout_s"] == 30
    assert description["execution_limits"]["max_model_calls"] == 5
    assert description["max_correction_rounds"] == 1
    assert description["execution_mode"] == "isolated_processes"
    assert description["source_tracing_enabled"] is False
    assert description["preparation"] == {"schema_version": "market_research.prep/2",
        "business_object_enabled": True, "max_calls": 6, "max_depth": 6}


def test_cli_rejects_nonfinite_deadline_before_any_calls():
    from scripts.treadle_market_research import main

    with pytest.raises(SystemExit) as error:
        main(["--describe", "--task-timeout", "nan"])
    assert error.value.code == 2


@pytest.mark.parametrize("model_calls,expected_exit", [(120, 0), (2, 1)])
def test_cli_saves_success_and_incomplete_artifacts(tmp_path, monkeypatch, capsys,
                                                   model_calls, expected_exit):
    import packs.market_research_next as pack
    from scripts.treadle_market_research import main

    original_factory = pack.build_report_type

    def fixture_factory(**kwargs):
        return original_factory(prep_model=fake_prep, research=fake_research,
                                business_model=minimal_business_model,
                                review=no_issues, **kwargs)

    monkeypatch.setattr(pack, "build_report_type", fixture_factory)
    brief = tmp_path / "venture.json"
    brief.write_text(json.dumps({"description": "Hybrid clinics and online subscriptions"}))
    prefix = tmp_path / "report"
    try:
        code = main(["--brief", str(brief), "--out", str(prefix),
                     "--model-calls", str(model_calls)])
    finally:
        REPORT_TYPES.pop("market_research_selected", None)
    assert code == expected_exit
    summary = json.loads(capsys.readouterr().out)
    saved = json.loads(prefix.with_suffix(".json").read_text())
    assert summary["stop_reason"] == saved["execution"]["stop_reason"]
    assert summary["ok"] is (expected_exit == 0)
    assert saved["execution"]["usage"]["charged"]["model_calls"] <= model_calls
    assert prefix.with_suffix(".html").read_text().startswith("<!doctype html>")
