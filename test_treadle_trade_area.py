"""The trade area in code, so one cafe does not get a county in one run and a tract in the next."""
from __future__ import annotations

from types import SimpleNamespace

from packs.market_research_next.analysis import trade_area
from treadle.core import Evidence

CONTEXT = {"prep": {"venture": {"attributes": {
    "product": {"value": "Coffee shop", "basis": "customer_stated"},
    "geography": {"value": "NW 23rd Avenue, Portland, Oregon. People walk here.",
                  "basis": "customer_stated"},
    "delivery_mode": {"value": "In person.", "basis": "customer_stated"}},
    "customer_inputs": {"description": "A coffee shop on NW 23rd Avenue in Portland.",
                        "intake": {"facts": {}}}}}}


class Tools(dict):
    def __init__(self, **answers):
        super().__init__()
        self.asked = {}
        defaults = {
            "geocode_address": {"lat": 45.5, "lng": -122.7, "state_fips": "41", "county_fips": "051"},
            "tracts_in_catchment": {"geoids": ["41051004700", "41051004800"], "land_km2": 7.0},
            "acs_income_distribution": {"households": 15062, "aggregate_income": 1.9e9},
            "bls_cex_spend": {"annual_usd": 4142.0, "covers": "all food away from home"}}
        for name, payload in {**defaults, **answers}.items():
            self[name] = SimpleNamespace(fn=self._tool(name, payload))

    def _tool(self, name, payload):
        def run(**args):
            self.asked[name] = args
            if payload is None:
                return Evidence.empty(name, "geo", error="down")
            return Evidence(source=name, category="geo", count=1, payload=payload)
        return run


def test_a_local_us_venture_gets_a_fixed_area_of_households_and_spend():
    tools = Tools()
    figures = trade_area.figures(CONTEXT, registry=tools)
    assert tools.asked["tracts_in_catchment"]["radius_m"] == trade_area.RADIUS_M
    assert tools.asked["acs_income_distribution"]["geoids"] == ["41051004700", "41051004800"]
    homes, spend = figures
    assert homes["kind"] == "customer_count" and homes["value"] == 15062
    assert homes["unit"] == "households" and homes["trade_area"] is True
    assert "1.5 km" in homes["geography"] and homes["source_tool"] == "acs_income_distribution"
    assert spend["kind"] == "broader_spend" and spend["value"] == 4142.0
    assert spend["unit"] == "USD per household per year" and spend["trade_area"] is True
    assert "all food away from home" in spend["name"]


def test_the_same_brief_always_asks_the_same_questions():
    first, second = Tools(), Tools()
    trade_area.figures(CONTEXT, registry=first)
    trade_area.figures(CONTEXT, registry=second)
    assert first.asked == second.asked


def test_a_place_written_as_a_possessive_is_turned_into_an_address():
    assert trade_area.address("Chicago's Lincoln Park neighbourhood") == "Lincoln Park, Chicago"
    assert trade_area.address("NW 23rd Avenue, Portland, Oregon") == "NW 23rd Avenue, Portland, Oregon"
    assert trade_area.address("the Mission district of San Francisco") == "Mission, San Francisco"


def test_a_place_outside_the_us_has_no_area():
    tools = Tools(geocode_address={"lat": 43.6, "lng": -79.4})
    assert trade_area.figures(CONTEXT, registry=tools) == []
    assert "tracts_in_catchment" not in tools.asked


def test_an_online_venture_has_no_area():
    online = {"prep": {"venture": {"attributes": {
        "product": {"value": "App", "basis": "customer_stated"},
        "geography": {"value": "United States", "basis": "customer_stated"},
        "delivery_mode": {"value": "Online", "basis": "customer_stated"}},
        "customer_inputs": {"description": "An app.", "intake": {"facts": {}}}}}}
    tools = Tools()
    assert trade_area.figures(online, registry=tools) == [] and not tools.asked


def test_a_failed_census_call_gives_no_area_and_no_error():
    assert trade_area.figures(CONTEXT, registry=Tools(tracts_in_catchment=None)) == []
    assert trade_area.figures(CONTEXT, registry=Tools(acs_income_distribution=None)) == []


def test_households_stand_when_spend_is_missing():
    figures = trade_area.figures(CONTEXT, registry=Tools(bls_cex_spend=None))
    assert [f["kind"] for f in figures] == ["customer_count"]


def test_spend_with_no_stated_coverage_is_left_out():
    figures = trade_area.figures(CONTEXT, registry=Tools(bls_cex_spend={"annual_usd": 900.0}))
    assert [f["kind"] for f in figures] == ["customer_count"]


def test_a_figure_the_writer_copied_from_the_area_is_not_counted_twice():
    from packs.market_research_next.research import _with_area

    area = [{"kind": "customer_count", "value": 27995.0, "trade_area": True}]
    copied = {"kind": "customer_count (households)", "value": 27995, "trade_area": False}
    other = {"kind": "customer_count", "value": 100.0}
    assert _with_area(area, [copied, other]) == area + [other]
