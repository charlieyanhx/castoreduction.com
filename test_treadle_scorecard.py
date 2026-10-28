"""The scorecard counts what a report holds, and the eval corpus stays answerable.

Offline: a hand-built report is scored, and every corpus case is turned into a brief by the
pack's own form and validated. No run is started.
"""
from __future__ import annotations

import json

from scripts.treadle_scorecard import CORPUS, load_cases, run_id_for, score_report
from treadle.registry import get, load_pack


def _attempt(model, usd, calls, tokens_in, tokens_out):
    return {"llm_usage": {"by_model": {model: {"calls": calls, "in": tokens_in,
                                               "out": tokens_out, "usd": usd}}}}


REPORT = {
    "result": {
        "prep": {"business_context": {
            "object": {"nodes": [1, 2], "facts": [1, 2, 3], "relationships": [1], "gaps": []},
            "construction": {"quarantined_count": 1}}},
        "market_size": {"research_status": "completed",
                        "research_note": "External research returned 4 usable tool result(s); "
                                         "stop reason: done.",
                        "narrative": "About 12,000 households spend $6.50 a week.",
                        "findings": ["Growth near 3.5%"], "assumptions": [],
                        "uncertainties": ["a", "b"], "metrics": {"tam": 1}},
        "summary": {"research_status": "derived", "research_note": "Derived.",
                    "narrative": "No figures here.", "findings": [], "uncertainties": []},
    },
    "sections": {"sections": [{"status": "ok"}, {"status": "ok"}, {"status": "skipped"}]},
    "execution": {"correction_rounds": 0, "stop_reason": "completed",
                  "usage": {"actual": {"model_calls": 3, "tool_calls": 4}},
                  "attempts": [_attempt("claude-opus-5-5", 0.5, 2, 100, 50),
                               _attempt("claude-opus-5-5", 0.25, 1, 10, 5), {}]},
}


def test_a_report_is_counted_not_judged():
    card = score_report(REPORT, {"status": "completed", "ok": True, "elapsed_seconds": 90})
    assert (card["status"], card["seconds"], card["cost_usd"]) == ("completed", 90, 0.75)
    assert card["tokens"] == {"claude-opus-5-5": {"calls": 3, "in": 110, "out": 55}}
    assert card["verdicts"] == {"ok": 2, "skipped": 1}
    assert card["object"] == {"nodes": 2, "facts": 3, "relationships": 1, "gaps": 0,
                              "quarantined": 1}
    sizing = card["sections"]["market_size"]
    assert (sizing["usable_evidence"], sizing["numbers"], sizing["metrics"]) == (4, 3, 1)
    assert card["sections"]["summary"]["usable_evidence"] is None
    assert card["sections"]["revenue"] is None
    assert card["totals"] == {"usable_evidence": 4, "numbers": 3, "metrics": 1,
                              "partial_sections": 0}


def test_a_failed_run_with_no_report_still_scores():
    card = score_report({}, {"status": "failed", "ok": False})
    assert (card["status"], card["cost_usd"], card["totals"]["numbers"]) == ("failed", 0, 0)


def test_run_ids_are_fixed_by_label_and_case():
    assert run_id_for("baseline", "vague") == run_id_for("baseline", "vague")
    assert run_id_for("baseline", "vague") != run_id_for("after", "vague")
    assert len(run_id_for("baseline", "vague")) == 32


def test_every_corpus_case_is_a_valid_brief_through_the_form():
    load_pack("market_research_next")
    rt = get("market_research_next")
    cases = load_cases(CORPUS)
    assert len({c["id"] for c in cases}) == len(cases) == 5
    for case in cases:
        rt.validate(rt.from_form(case["answers"]))
    assert json.loads(CORPUS.read_text())["corpus"] == "market_research_next/eval/1"


def test_an_interrupted_case_is_scored_from_its_latest_attempt(tmp_path):
    from scripts.treadle_scorecard import latest_attempt

    for attempt, state in ((1, "running"), (2, "completed")):
        directory = tmp_path / run_id_for("b", "cafe", attempt)
        directory.mkdir()
        (directory / "status.json").write_text(json.dumps({"status": state}))
    run_id, status = latest_attempt(tmp_path, "b", "cafe")
    assert (run_id, status["status"]) == (run_id_for("b", "cafe", 2), "completed")
    assert run_id_for("b", "cafe", 1) == run_id_for("b", "cafe")
    assert latest_attempt(tmp_path, "b", "other") == (run_id_for("b", "other"), None)
