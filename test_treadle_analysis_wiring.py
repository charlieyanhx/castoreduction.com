"""The pack's own wiring around the analysis sections: what a figure must cite, what a
rerun is told, and what the number review may send back for repair. Offline."""
from __future__ import annotations

from datetime import date

from packs.market_research_next import REPORT_ORDER, _model_review, number_review
from packs.market_research_next.analysis import base
from packs.market_research_next.research import _ground_metrics
from treadle.core.section import SectionResult


def test_a_figure_stays_only_when_its_page_was_fetched_and_is_dated_this_run():
    material = {"hits": [{"url": "https://www.census.gov/data/clinics/"},
                         {"link": "https://ibisworld.com/report"}]}
    metrics = {"figures": [
        {"name": "clinics", "value": 38000, "source_url": "https://www.census.gov/data/clinics",
         "retrieved_at": "2019-01-01"},
        {"name": "invented", "value": 9e9, "source_url": "https://made-up.example/x"},
        {"name": "no source", "value": 1}]}
    out = _ground_metrics("market_size", metrics, material)
    assert [f["name"] for f in out["figures"]] == ["clinics"]
    assert out["figures"][0]["retrieved_at"] == date.today().isoformat()
    assert out["figures_unsupported"] == 2
    assert metrics["figures"][0]["retrieved_at"] == "2019-01-01"   # input left alone


def test_a_competitor_keeps_its_name_but_a_site_only_when_seen():
    material = [{"url": "https://www.jane.app/features"}]
    out = _ground_metrics("competitive_landscape", {"competitors": [
        {"name": "Jane App", "url": "https://jane.app", "kind": "direct"},
        {"name": "Cliniko", "url": "https://cliniko.com", "kind": "direct"}]}, material)
    assert [(c["name"], c["url"]) for c in out["competitors"]] == [
        ("Jane App", "https://jane.app"), ("Cliniko", None)]


def test_a_rerun_shows_the_model_what_the_review_found():
    seen = {}

    def model(system, user, **kwargs):
        seen["system"], seen["user"] = system, user
        return {"ok": True}

    base.call_model("Write it.", {"a": 1}, model=model)
    assert "previous_attempt_review_issues" not in seen["user"]
    with base.repairing(["five_forces: a sub-factor was missing"]):
        base.call_model("Write it.", {"a": 1}, model=model)
    assert "a sub-factor was missing" in seen["user"]
    assert "previous_attempt_review_issues" in seen["system"]
    base.call_model("Write it.", {"a": 1}, model=model)        # and only during the rerun
    assert "previous_attempt_review_issues" not in seen["user"]


def _section(text):
    return {"title": "t", "narrative": text, "findings": [], "assumptions": [],
            "uncertainties": [], "metrics": {}}


def test_number_review_sends_back_research_sections_only():
    claim = "The serviceable market for independent physiotherapy clinics in Ontario is {}."
    result = {"prep": {}, "market_size": _section(claim.format("$4.2M")),
              "revenue": _section(claim.format("$9.8M")),
              "five_forces": _section(claim.format("$1.1M"))}
    issues = number_review(result)
    assert issues and all(set(i.repair) <= set(REPORT_ORDER) for i in issues)


def test_the_model_review_may_only_name_planned_sections(monkeypatch):
    import treadle.llm

    monkeypatch.setattr(treadle.llm, "call_json", lambda *a, **k: {"issues": [
        {"code": "x", "message": "fix it", "repair": ["five_forces"]}]})
    verdicts = (SectionResult("market_size", "ok"),)
    try:
        _model_review({"prep": {}}, verdicts)
    except ValueError as exc:
        assert "invalid repair issue" in str(exc)
    else:
        raise AssertionError("an unplanned section was accepted as a repair target")


def test_the_smoke_runs_false_alarms_are_not_conflicts():
    """Measured 2026-10-28: a street address and a walk/drive radius stated in two
    sections cost a whole correction round."""
    from packs.market_research_next.analysis.consistency import _REVIEWED, find_number_conflicts

    texts = {
        "competitive_landscape": "a catchment of a 10-minute walk or approximately 2-mile "
                                 "drive for walk-in coffee customers on NW 23rd Avenue.",
        "target_personas": "Residents within a 10-minute walk or 2-mile drive of the coffee shop.",
        "differentiators": "competitors such as Barista (823 NW 23rd Ave) and Case Study "
                           "Coffee (1422 NW 23rd Ave)."}
    assert find_number_conflicts(texts, _REVIEWED) == []
    assert find_number_conflicts(texts) == []


def test_a_dataset_figure_stays_only_when_its_tool_returned_data():
    material = {"evidence": [
        {"source": "acs_demographics", "count": 1, "error": "", "skeleton": False,
         "payload": {"households": 1761}},
        {"source": "bls_cex_spend", "count": 0, "error": "timeout", "skeleton": False},
        {"source": "extract_structured", "count": 1, "error": "", "skeleton": False}]}
    figures = [
        {"name": "households", "kind": "customer_count", "value": 1761, "source_url": None,
         "source_tool": "acs_demographics"},
        {"name": "food spend", "kind": "price", "value": 3945, "source_tool": "bls_cex_spend"},
        {"name": "parsed", "kind": "price", "value": 6, "source_tool": "extract_structured"}]
    out = _ground_metrics("market_size", {"figures": figures}, material)
    assert [f["name"] for f in out["figures"]] == ["households"]
    kept = out["figures"][0]
    assert kept["source_url"] == "https://data.census.gov/"
    assert "Census" in kept["source_title"] and out["figures_unsupported"] == 2


def test_the_statement_uses_an_before_a_vowel():
    from packs.market_research_next.analysis.positioning import assemble

    def fields(category):
        texts = {"target_customer": "locals", "need": "want coffee", "product": "Cafe One",
                 "category": category, "key_benefit": "brews well", "primary_competitor": "X",
                 "key_differentiator": "roasts in house", "strategy": "focus"}
        return {k: {"text": v, "from": ["prep"]} for k, v in texts.items()}

    assert "is an in-person cafe that" in assemble(fields("in-person cafe"))
    assert "is a neighbourhood cafe that" in assemble(fields("neighbourhood cafe"))
