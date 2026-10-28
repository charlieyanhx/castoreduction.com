"""Offline integration: survey interpretation reaches research and corrections reach consumers."""
from __future__ import annotations

import copy
from dataclasses import asdict
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from treadle import ReportType, register, run_report
from treadle.core.section import Section
from treadle.registry import REPORT_TYPES, load_pack
from treadle.workflow import DeterministicWorkflow, ReviewIssue
from packs.market_research_next import REPORT_ORDER, build_report_type, consistency_review
from packs.market_research_next.models import ExplicitPrepModel
from packs.market_research_next.prep import REPORT_SECTIONS, VENTURE_FIELDS
from packs.market_research_next.render import render_html
from test_treadle_business_pipeline import minimal_business_model


def prep_response(**kwargs):
    return {
        "venture": {field: "model interpretation" for field in VENTURE_FIELDS},
        "research_spec": {
            key: {"scope": "Independent dental clinics", "questions": [f"Investigate {key}"],
                  "assumptions_to_test": ["Buyer willingness to switch"]}
            for key in REPORT_SECTIONS
        },
    }


def section_payload(name):
    return {"title": name, "narrative": f"Analysis for {name}",
            "findings": [], "assumptions": [], "uncertainties": []}


@pytest.fixture
def registered():
    keys = []

    def keep(rt):
        register(rt)
        keys.append(rt.key)
        return rt.key

    yield keep
    for key in keys:
        REPORT_TYPES.pop(key, None)


def test_survey_to_preparation_to_research_and_report(registered):
    calls = {}
    prep_model = Mock(side_effect=prep_response)

    def research(name, ctx, **kwargs):
        calls[name] = copy.deepcopy(ctx)
        assert ctx["prep"]["research_spec"][name]["questions"] == [f"Investigate {name}"]
        return section_payload(name)

    key = registered(build_report_type(analysis=False, key="venture_integration", prep_model=prep_model,
                                       business_model=minimal_business_model,
                                       research=research, review=lambda result, verdicts: (),
                                       limits=None))
    brief = {"description": "AI receptionist for dentists", "geography": "Canada",
             "intake": {"unknowns": ["pricing"]}}
    original = copy.deepcopy(brief)
    report = run_report(key, brief)
    assert report.ok
    assert report.execution["correction_rounds"] == 0
    assert prep_model.call_count == 1
    assert set(calls) == set(REPORT_ORDER)
    assert "market_size" in calls["revenue"]
    assert "competitive_landscape" in calls["revenue"]
    assert set(REPORT_ORDER) - {"summary"} <= set(calls["summary"])
    assert report.result["prep"]["source_tracing_enabled"] is False
    attrs = report.result["prep"]["venture"]["attributes"]
    assert attrs["geography"]["value"] == "Canada"
    assert attrs["pricing"]["basis"] == "unknown"
    assert brief == original
    assert not report.stale
    json.dumps(asdict(report))


def test_consistency_correction_refreshes_only_affected_sections(registered):
    counts = {}
    feedback = {}

    def research(name, ctx, *, issues=()):
        counts[name] = counts.get(name, 0) + 1
        feedback[name] = issues
        value = section_payload(name)
        if name == "revenue":
            value["narrative"] = ctx["market_size"]["narrative"]
        elif name == "market_size":
            value["narrative"] = f"Market size revision {counts[name]}"
        return value

    def review(result, verdicts):
        if counts["market_size"] == 1:
            return (ReviewIssue("scope_mismatch", "Use Canadian clinic counts", ("market_size",)),)
        return ()

    key = registered(build_report_type(analysis=False, key="venture_correction", prep_model=prep_response,
                                       business_model=minimal_business_model,
                                       research=research, review=review, limits=None))
    report = run_report(key, {"description": "Dental receptionist"})
    assert report.ok
    assert report.execution["correction_rounds"] == 1
    assert counts["market_size"] == counts["revenue"] == counts["summary"] == 2
    assert counts["competitive_landscape"] == counts["customer_voice"] == 1
    assert report.result["revenue"]["narrative"] == "Market size revision 2"
    assert feedback["revenue"][0].code == "scope_mismatch"
    assert not report.stale


def test_unresolved_issues_make_report_not_ok_and_visible(registered):
    key = registered(build_report_type(analysis=False, 
        key="venture_unresolved", prep_model=prep_response,
        business_model=minimal_business_model,
        research=lambda name, ctx, **kwargs: section_payload(name),
        review=lambda result, verdicts: (ReviewIssue("uncertain", "Scope still conflicts", ("market_size",)),),
    ))
    report = run_report(key, {"description": "An idea"})
    assert not report.ok
    assert report.execution["correction_rounds"] == 2
    assert len(report.execution["history"]) == 3
    assert "Scope still conflicts" in render_html(report)


def test_prep_failure_never_starts_research(registered):
    research = Mock()
    key = registered(build_report_type(analysis=False, key="venture_bad_prep", prep_model=lambda **kwargs: {},
                                       business_model=minimal_business_model,
                                       research=research, review=lambda result, verdicts: (),
                                       limits=None))
    report = run_report(key, {"description": "An idea"})
    assert not report.ok
    research.assert_not_called()
    assert report.execution["correction_rounds"] == 2
    assert "prep" not in report.result


def test_execution_strategy_can_be_replaced_without_changing_producers(registered):
    key = registered(ReportType(key="workflow_override", label="Workflow",
                               sections=lambda b: [Section("answer", lambda ctx: 42)]))
    report = run_report(key, {}, workflow=DeterministicWorkflow(max_workers=1))
    assert report.result["answer"] == 42
    assert report.execution["correction_rounds"] == 0
    assert run_report(key, {}).execution is None


def test_legacy_market_research_keeps_its_runner():
    load_pack("market_research")
    with patch("packs.market_research.run", return_value={"legacy": True}):
        # The registered runner is an existing function, so patch its domain callee.
        with patch("plan.run_plan", return_value={"legacy": True}) as legacy:
            report = run_report("market_research", {"description": "Original business"})
    assert report.result == {"legacy": True}
    assert report.execution is None
    legacy.assert_called_once_with("Original business")
    with pytest.raises(ValueError, match="runs itself"):
        run_report("market_research", {"description": "x"}, workflow=DeterministicWorkflow())


def test_renderer_uses_fixed_order_and_escapes_source_text(registered):
    key = registered(build_report_type(analysis=False, 
        key="venture_html", prep_model=prep_response,
        business_model=minimal_business_model,
        research=lambda name, ctx, **kwargs: {
            **section_payload(name), "narrative": "<script>bad()</script>",
            "metrics": {"year_one": {"revenue": 12345, "currency": "USD"}},
        },
        review=lambda result, verdicts: (),
    ))
    html = render_html(run_report(key, {"description": "Example"}))
    positions = [html.index(f'id="{key}"') for key in REPORT_ORDER]
    assert positions == sorted(positions)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "12345" in html
    assert "year one / revenue" in html
    from report.pdf import build_print_html

    printable = build_print_html(html)
    assert 'href="#summary"' in printable
    assert 'id="summary"' in printable


def test_consistency_review_rejects_unknown_repair_targets():
    with patch("treadle.llm.call_json", return_value={"issues": [
        {"code": "bad", "message": "Fix invented task", "repair": ["invented_task"]}
    ]}):
        with pytest.raises(ValueError, match="invalid repair"):
            consistency_review({"prep": {}}, ())


def test_prep_model_requires_explicit_choice_and_never_falls_back(monkeypatch):
    monkeypatch.delenv("TREADLE_PREP_MODEL", raising=False)
    with pytest.raises(ValueError, match="TREADLE_PREP_MODEL"):
        ExplicitPrepModel()(system="system", user="survey")
    response = SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text='{"ok":true}')],
        usage=SimpleNamespace(input_tokens=10, output_tokens=4),
    )
    client = Mock()
    client.messages.create.return_value = response
    with patch("treadle.llm.backend_configured", return_value=True), \
         patch("treadle.llm.paid_backend_allowed", return_value=True), \
         patch("anthropic.Anthropic", return_value=client):
        assert ExplicitPrepModel("chosen-model")(system="system", user="survey") == {"ok": True}
    assert client.messages.create.call_args.kwargs["model"] == "chosen-model"


@pytest.mark.parametrize("raw", [
    '{"ok":true}',
    ' \n```json\n{"ok":true}\n```\n ',
])
def test_prep_model_accepts_json_and_one_complete_json_fence(raw):
    response = SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text=raw)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=4),
    )
    client = Mock()
    client.messages.create.return_value = response
    with patch("treadle.llm.backend_configured", return_value=True), \
         patch("treadle.llm.paid_backend_allowed", return_value=True), \
         patch("anthropic.Anthropic", return_value=client):
        assert ExplicitPrepModel("chosen-model")(system="system", user="survey") == {"ok": True}
    assert client.messages.create.call_count == 1


@pytest.mark.parametrize("raw,stop_reason", [
    ('Here is the object:\n```json\n{"ok":true}\n```', "end_turn"),
    ('```json\n{"ok":true}\n```\nExtra explanation', "end_turn"),
    ('```json\n{"ok":true}', "end_turn"),
    ('```json\n{"ok":true}\n```\n```json\n{}\n```', "end_turn"),
    ('{"ok":true', "end_turn"),
    ('[]', "end_turn"),
    ('{"ok":true}', "max_tokens"),
])
def test_prep_model_rejects_prose_malformed_and_truncated_responses(raw, stop_reason):
    response = SimpleNamespace(
        stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=raw)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=4),
    )
    client = Mock()
    client.messages.create.return_value = response
    with patch("treadle.llm.backend_configured", return_value=True), \
         patch("treadle.llm.paid_backend_allowed", return_value=True), \
         patch("anthropic.Anthropic", return_value=client), \
         pytest.raises(ValueError):
        ExplicitPrepModel("chosen-model")(system="system", user="survey")
    assert client.messages.create.call_count == 1
