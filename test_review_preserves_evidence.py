"""Review must see the actual facts; a rejected revision must not rewrite them."""
import json
from unittest.mock import patch

from harness.refine import evaluate_refine
from report.verifier import _llm_review
from skills.refine_report import _render


def test_rejected_mutating_refiner_cannot_change_the_original_or_retained_report():
    original = {"pricing": {"price": 10}}

    def evaluate(report):
        return {"quality": {"score": 2 if report["pricing"]["price"] == 10 else 1}}

    def mutate(report, weak, scores):
        report["pricing"]["price"] = 999
        scores["quality"]["score"] = 100
        return report

    result = evaluate_refine(original, evaluate, mutate, {"quality": 3})
    assert original == {"pricing": {"price": 10}}
    assert result.artifact == original
    assert result.final_scores["quality"]["score"] == 2


def test_candidate_evaluator_failure_preserves_the_last_evaluated_artifact():
    def evaluate(report):
        if report == "candidate":
            raise RuntimeError("judge unavailable")
        return {"quality": {"score": 1}}

    result = evaluate_refine("original", evaluate, lambda *args: "candidate",
                             {"quality": 3})
    assert result.artifact == "original"
    assert not result.passed


def test_quality_improvement_cannot_sacrifice_a_satisfied_validation_requirement():
    scores = iter([
        {"quality": {"score": 1}, "validation": {"score": 4}},
        {"quality": {"score": 5}, "validation": {"score": 3}},
    ])
    result = evaluate_refine("original", lambda _: next(scores), lambda *args: "broken",
                             {"quality": 3, "validation": 4})
    assert result.artifact == "original"
    assert result.weak_dims == ["quality"]


def test_meeting_the_contract_wins_even_when_an_unnecessarily_high_score_falls():
    scores = iter([
        {"quality": {"score": 5}, "validation": {"score": 3}},
        {"quality": {"score": 3}, "validation": {"score": 4}},
    ])
    result = evaluate_refine("original", lambda _: next(scores), lambda *args: "valid",
                             {"quality": 3, "validation": 4})
    assert result.passed
    assert result.artifact == "valid"


def test_evaluator_cannot_mutate_the_evidence_it_is_judging():
    def evaluate(report):
        report["price"] = 999
        return {"quality": {"score": 5}}

    result = evaluate_refine({"price": 10}, evaluate, lambda *args: None, {"quality": 3})
    assert result.artifact == {"price": 10}


def test_semantic_reviewer_receives_current_sizing_and_dependent_financials():
    report = {
        "market_sizing": {"tam": {"mid": 123456}, "sam": {"mid": 45678},
                          "som": {"value": 12345}},
        "financials": {"scenarios": {"base": {"revenue": 12345}}},
        "economics": {"unit_price": 17},
        "four_ps": {"price": {"narrative": "Charge $17."}},
    }
    with patch("llm.call_json", return_value={"findings": []}) as call:
        assert _llm_review(report, None) == []
    prompt = call.call_args.kwargs["user"]
    # Parse the individual evidence blocks, not just search for a coincidental number.
    blocks = {}
    for part in prompt.split("\n\n"):
        if part.startswith("["):
            header, payload = part.split("\n", 1)
            blocks[header[1:-1]] = json.loads(payload)
    assert blocks["market_sizing"] == report["market_sizing"]
    assert blocks["financials"] == report["financials"]
    assert blocks["economics"] == report["economics"]


def test_large_refinement_context_is_complete_json_with_disclosed_truncation():
    rendered = _render({"discover": {"notes": "a" * 20000}})
    assert len(rendered) <= 12000
    parsed = json.loads(rendered)
    assert "_truncated" in parsed


def test_duplicate_planner_names_do_not_duplicate_specialist_work():
    from agents.planner import _select
    with patch("agents.planner.call_json", return_value={"selected": [
        "pricing_intel_agent", "market_scan_agent", "pricing_intel_agent", {}, None,
    ]}):
        selected, _ = _select("A cafe", has_address=False)
    assert selected == ["pricing_intel_agent", "market_scan_agent"]


def test_malformed_planner_selection_uses_the_applicable_fallback():
    from agents.planner import _select, WORKER_ROSTER
    for response in (["bad shape"], {"selected": "market_scan_agent"}):
        with patch("agents.planner.call_json", return_value=response):
            selected, _ = _select("A digital service", has_address=False)
        assert set(selected) == set(WORKER_ROSTER) - {"local_market_agent"}


def test_live_runner_exit_status_reflects_report_failure(tmp_path):
    from tools import run_live
    cases = [
        ({"error": "research failed"}, False, 1),
        ({"verification": {"status": "blocked", "summary": {"publishable": False}}}, False, 1),
        ({}, False, 1),
        ({"verification": {"status": "verified", "summary": {"publishable": True}}}, True, 1),
        ({"verification": {"status": "verified", "summary": {"publishable": True}}}, False, 0),
    ]
    for result, render_fails, expected in cases:
        with patch.object(run_live, "PROJ", tmp_path), \
             patch("sys.argv", ["run_live", "audit", "A venture"]), \
             patch("llm.fallback_chain", return_value=[]), \
             patch("plan.run_plan", return_value=result), \
             patch("report.render_html.render_report_html", return_value="<html></html>",
                   side_effect=RuntimeError("render failed") if render_fails else None):
            assert run_live.main() == expected
        assert json.loads((tmp_path / "out/live/audit.json").read_text())["result"] == result
