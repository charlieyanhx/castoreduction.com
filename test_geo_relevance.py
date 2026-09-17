import json
from unittest.mock import patch

import pytest

from geo_relevance import rank_geo_competitors
from tools.geo import _osm_competitor_record


def rows(n=3):
    return [{"brand": f"Cafe {i}", "description": "espresso and coffee", "amenity": "cafe"}
            for i in range(n)]


@pytest.mark.parametrize("reply", [{"_parse_error": "truncated"}, {}, None,
                                  {"decisions": "bad"}, {"decisions": [[0, "direct", "high", "missing"]]},
                                  {"decisions": [[0, "direct", "high", "description"],
                                                 [0, "unrelated", "high", "description"]]}])
def test_failed_or_ambiguous_decisions_never_invent_a_score(reply):
    with patch("geo_relevance.call_json", return_value=reply):
        out = rank_geo_competitors(rows(), "local cafe", "coffee")
    assert len(out) == 3
    assert all(r["local_relevance_score"] is None for r in out)
    assert all(r["relevance_status"] == "unscored" for r in out)


def test_small_batches_cover_thirty_without_verbose_names_in_output():
    def model(**kw):
        data = json.loads(kw["user"])
        assert kw["tier"] == "utility"
        assert len(data["venues"]) <= 8
        return {"decisions": [[r["id"], "direct", "high", "description"] for r in data["venues"]]}
    with patch("geo_relevance.call_json", side_effect=model) as call:
        out = rank_geo_competitors(rows(30), "local cafe", "coffee", limit=30)
    assert call.call_count == 4
    assert len(out) == 30
    assert all(r["local_relevance_score"] == 90 for r in out)


def test_reordered_ids_rank_offerings_not_input_order_and_keep_uncertainty():
    data = rows(4)
    data[1]["description"] = "bicycle repairs"
    reply = {"decisions": [[2, "direct", "high", "description"],
                            [0, "adjacent", "high", "amenity"],
                            [1, "unrelated", "high", "description"],
                            [3, "unrelated", "high", "name"]]}
    with patch("geo_relevance.call_json", return_value=reply):
        out = rank_geo_competitors(data, "local cafe", "coffee")
    assert [r["brand"] for r in out] == ["Cafe 2", "Cafe 0", "Cafe 3"]
    assert out[-1]["local_relevance_score"] is None


def test_map_parser_preserves_classification_evidence():
    rec = _osm_competitor_record({"tags": {"name": "Cafe", "amenity": "cafe",
                                            "cuisine": "coffee_shop", "shop": "coffee"}})
    assert (rec["amenity"], rec["cuisine"], rec["shop"]) == ("cafe", "coffee_shop", "coffee")


def test_exception_preserves_unscored_candidates():
    with patch("geo_relevance.call_json", side_effect=TimeoutError):
        out = rank_geo_competitors(rows(), "local cafe", "coffee")
    assert len(out) == 3
    assert all(r["local_relevance_score"] is None for r in out)


def test_report_labels_local_scores_and_missing_assessments():
    from pathlib import Path
    from bs4 import BeautifulSoup
    from report.render_html import render_report_html
    result = json.loads((Path(__file__).parent / "tests/fixtures/synthesis/diag01_result.json").read_text())
    with patch("geo_relevance.call_json", return_value={"decisions": [[0, "direct", "high", "description"]]}):
        roster = rank_geo_competitors(rows(2), "coffee", "coffee")
    result["discover"]["synthesis"]["ranked_opportunities"] = [
        {**r, "geo_sourced": True} for r in roster]
    html = render_report_html(result, job_id="geo-test")
    soup = BeautifulSoup(html, "html.parser")
    scores = [x.get_text(strip=True) for x in soup.select(".comp-row .score")]
    assert scores == ["90", "unscored"]
    assert "not measured customer" in soup.get_text()
