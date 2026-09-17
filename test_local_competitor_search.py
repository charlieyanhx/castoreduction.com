from types import SimpleNamespace
from unittest.mock import patch

from local_competitor_search import _search_locality, search_local_competitors


def hit():
    return {"title": "Acme repair", "snippet": "Acme offers quantum sensor repair in Austin.",
            "url": "https://example.com/repair"}


def test_search_uses_city_not_street_address():
    assert _search_locality("1200 W 6th St, Austin, TX 78703") == "Austin, TX"


def test_unmapped_trade_is_searchable_with_verbatim_evidence():
    with patch("local_competitor_search.call_json", side_effect=[
        {"queries": ["quantum sensor repair Austin"]},
        {"candidates": [[0, "Acme", "quantum sensor repair", "in Austin"]]}]), \
        patch("local_competitor_search.web_search", return_value=SimpleNamespace(payload=[hit()])):
        out = search_local_competitors("quantum sensor repair", "Austin")
    assert len(out) == 1
    assert out[0]["brand"] == "Acme"
    assert out[0]["source_url"] == hit()["url"]


def test_invented_name_service_location_and_invalid_indices_are_rejected():
    decisions = [[0, "Imaginary", "quantum sensor repair", "in Austin"],
                 [0, "Acme", "dog grooming", "in Austin"],
                 [0, "Acme", "quantum sensor repair", "in Boston"],
                 [42, "Acme", "quantum sensor repair", "in Austin"]]
    with patch("local_competitor_search.call_json", side_effect=[{}, {"candidates": decisions}, {}]), \
        patch("local_competitor_search.web_search", return_value=SimpleNamespace(payload=[hit()])):
        assert search_local_competitors("quantum sensor repair", "Austin") == []


def test_failed_query_planner_still_searches_the_description():
    with patch("local_competitor_search.call_json", side_effect=[TimeoutError, {}, {}]), \
        patch("local_competitor_search.web_search", return_value=SimpleNamespace(payload=[hit()])) as search:
        search_local_competitors("quantum sensor repair", "Austin")
    assert "quantum sensor repair" in search.call_args.args[0]


def test_stronger_extractor_runs_only_after_cheap_zero_and_keeps_quote_validation():
    with patch("local_competitor_search.call_json", side_effect=[
        {"queries": ["pottery Austin"]}, {"candidates": []},
        {"candidates": [[0, "Acme", "quantum sensor repair", "in Austin"]]}]) as model, \
        patch("local_competitor_search.web_search", return_value=SimpleNamespace(payload=[hit()])):
        out = search_local_competitors("quantum sensor repair", "Austin")
    assert out[0]["brand"] == "Acme"
    assert model.call_count == 3
    assert "tier" not in model.call_args.kwargs


def test_stronger_extractor_is_skipped_when_cheap_pass_succeeds():
    with patch("local_competitor_search.call_json", side_effect=[
        {"queries": ["repair Austin"]},
        {"candidates": [[0, "Acme", "quantum sensor repair", "in Austin"]]}]) as model, \
        patch("local_competitor_search.web_search", return_value=SimpleNamespace(payload=[hit()])):
        assert search_local_competitors("quantum sensor repair", "Austin")
    assert model.call_count == 2


def test_pipeline_uses_web_without_a_map_category_or_geocode_and_keeps_one_result():
    import plan
    def missing_tool(name):
        assert name == "geocode_address"
        return SimpleNamespace(fn=lambda *a, **kw: SimpleNamespace(payload={}))
    candidate = {"brand": "Acme", "name": "Acme", "description": "quantum sensor repair",
                 "source_url": "https://example.com"}
    with patch("tools.get_tool", side_effect=missing_tool), \
        patch("local_competitor_search.search_local_competitors", return_value=[candidate]), \
        patch("geo_relevance.call_json", return_value={}):
        out = plan.geo_competitor_opps("quantum sensor repair", {"category": "unmapped trade"},
                                      {"scale": "hyperlocal"}, location="Austin")
    assert len(out) == 1
    assert out[0]["brand"] == "Acme"


def test_map_failure_does_not_block_description_search():
    import plan
    def get_tool(name):
        if name == "geocode_address":
            return SimpleNamespace(fn=lambda **kw: SimpleNamespace(payload={"lat": 30.27, "lng": -97.75}))
        raise TimeoutError("map source down")
    with patch("tools.get_tool", side_effect=get_tool), \
        patch("local_competitor_search.search_local_competitors", return_value=[{"brand": "Acme"}]) as search, \
        patch("geo_relevance.call_json", return_value={}):
        out = plan.geo_competitor_opps("a coffee shop", {"category": "cafe"},
                                      {"scale": "hyperlocal"}, location="Austin")
    assert search.call_count == 1
    assert out[0]["brand"] == "Acme"
