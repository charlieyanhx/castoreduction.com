"""Offline checks for the browser runner's verdicts; these do not execute a report."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import xml.etree.ElementTree as ET

import pytest

from scripts.treadle_browser_e2e import (
    REPORT_SECTIONS,
    TERMINAL,
    _clean_message,
    _wait_for_run,
    inspect_report,
    main,
    write_test_reports,
)


def _report():
    brief = {"description": "Hybrid tutoring business", "pricing": {"unknown": True}}
    report = {
        "result": {
            "prep": {
                "schema_version": "market_research.prep/2",
                "venture": {"customer_inputs": copy.deepcopy(brief)},
                "business_context": {"object": {"nodes": [{"id": "venture"}]}},
                "source_tracing_enabled": False,
                "research_spec": {key: {} for key in REPORT_SECTIONS},
            },
            **{key: {"narrative": "Example"} for key in REPORT_SECTIONS},
        },
        "stale": [],
        "execution": {
            "correction_rounds": 2, "stop_reason": "completed", "issues": [],
            "limits": {"max_model_calls": 20, "max_tool_calls": 10},
            "usage": {"actual": {"model_calls": 15, "tool_calls": 3},
                      "charged": {"model_calls": 15, "tool_calls": 3}},
            "attempts": [{"section": "prep", "round": 0, "pid": 12, "usage_valid": True}],
        },
    }
    return report, brief, {"status": "completed", "ok": True}


def test_complete_report_contracts_pass_without_asserting_research_accuracy():
    report, brief, state = _report()
    checks = inspect_report(report, brief, state)
    assert checks
    assert all(item["passed"] for item in checks)
    assert all(item["category"] == "pipeline" for item in checks)


@pytest.mark.parametrize("mutation,expected", [
    (lambda report, state: state.update(status="incomplete", ok=False), "pipeline_completed"),
    (lambda report, state: report["result"].pop("revenue"), "eight_produced_sections"),
    (lambda report, state: report["result"]["prep"]["venture"]["customer_inputs"].update(
        pricing=100), "customer_input_preserved"),
    (lambda report, state: report["execution"].update(correction_rounds=3), "global_correction_cap"),
    (lambda report, state: report["execution"]["usage"]["charged"].update(
        model_calls=21), "model_calls_within_global_budget"),
    (lambda report, state: report.update(stale=["revenue"]), "no_stale_consumers"),
    (lambda report, state: report["execution"].update(attempts=[]), "isolated_attempt_records"),
])
def test_incomplete_or_unsafe_contracts_do_not_get_a_passing_verdict(mutation, expected):
    report, brief, state = _report()
    mutation(report, state)
    failures = {item["name"] for item in inspect_report(report, brief, state)
                if not item["passed"]}
    assert expected in failures


def test_artifact_reports_preserve_failure_and_escape_model_text(tmp_path):
    summary = {
        "passed": False, "elapsed_s": 1.2, "run_id": "example",
        "checks": [{"name": "pipeline_completed", "passed": False, "category": "pipeline",
                    "detail": "<script>alert('not executed')</script>"}],
    }
    write_test_reports(tmp_path, summary)
    assert json.loads((tmp_path / "summary.json").read_text())["passed"] is False
    junit = ET.parse(tmp_path / "junit.xml").getroot()
    assert junit.attrib["failures"] == "1"
    assert "<script>" not in (tmp_path / "index.html").read_text()
    assert "&lt;script&gt;" in (tmp_path / "index.html").read_text()


@pytest.mark.parametrize("arguments", [
    ["--base-url", "https://example.com"],
    ["--base-url", "http://127.0.0.1:8779/other"],
    ["--timeout", "nan"],
    ["--timeout", "0"],
    ["--resume-run", "../another"],
])
def test_bad_arguments_fail_before_starting_browser_or_dispatching(arguments):
    with pytest.raises(SystemExit) as raised:
        main(arguments)
    assert raised.value.code == 2


def test_diagnostics_redact_common_credential_shapes():
    assert "sk-" not in _clean_message("failed sk-" + "x" * 32)
    assert _clean_message("Authorization: Bearer sensitive-value") == (
        "Authorization: Bearer [REDACTED]")


def test_interrupted_saved_run_is_terminal_and_never_repolled():
    class Response:
        ok = True

        async def json(self):
            return {"status": "interrupted", "ok": False}

    class Request:
        def __init__(self):
            self.calls = 0

        async def get(self, url):
            self.calls += 1
            assert self.calls == 1
            return Response()

    class Context:
        request = Request()

    context = Context()
    # Its own thread: an earlier test in the full suite can leave an event loop running
    # in this one, and asyncio.run refuses to start inside it.
    with ThreadPoolExecutor(max_workers=1) as pool:
        state = pool.submit(asyncio.run, _wait_for_run(
            context, "http://127.0.0.1:8779", "saved", 1)).result()
    assert state["status"] in TERMINAL
    assert state["status"] == "interrupted"
    report, brief, _ = _report()
    checks = inspect_report(report, brief, state)
    assert next(c for c in checks if c["name"] == "pipeline_completed")["passed"] is False
