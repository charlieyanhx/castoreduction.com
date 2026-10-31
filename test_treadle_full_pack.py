"""The whole pack, offline: eight research sections and nine analysis sections, run by
the real isolated workflow.

Fakes stand in for the models and outside services; everything else is real: each
section's module, the contracts, the dependency order, pickling into worker processes,
the number review, the renderer and the customer pipeline's completeness check.
"""
from __future__ import annotations

from functools import partial

from packs.market_research_next import (FULL_ORDER, REPORT_ORDER, build_report_type,
                                        number_review)
from packs.market_research_next.analysis import (analog_markets, community_discovery,
                                                 competitor_count, five_forces,
                                                 generic_strategy, go_to_market, positioning,
                                                 pricing)
from packs.market_research_next.analysis.registry import ANALYSIS, label
from packs.market_research_next.customer_pipeline import serialize_customer_report
from packs.market_research_next.render import render_html
from test_treadle_analysis_strategy import (_analogs_answer, _forces_answer, _gtm_answer,
                                            _positioning_answer, _strategy_answer)
from test_treadle_business_pipeline import minimal_business_model
from test_treadle_closed_loop_report import fake_prep, fake_research
from treadle import register, run_report
from treadle.execution_limits import ExecutionLimits
from treadle.registry import REPORT_TYPES

FIGURES = [
    {"name": "US clinic software market", "kind": "market_value", "value": 2.4e9,
     "unit": "USD per year", "geography": "US", "year": 2025,
     "source_url": "https://www.example-research.com/report", "source_title": "Report",
     "retrieved_at": "2026-10-01"},
    {"name": "Physiotherapy clinics", "kind": "customer_count", "value": 38000,
     "unit": "clinics", "geography": "US", "year": 2025,
     "source_url": "https://www.bls.gov/clinics", "source_title": "BLS",
     "retrieved_at": "2026-10-01"},
]


def full_research(name, context, *, issues=()):
    """The closed-loop fake, plus the typed metrics two sections now owe."""
    section = fake_research(name, context, issues=issues)
    if name == "market_size":
        section["metrics"]["figures"] = FIGURES
    if name == "competitive_landscape":
        section["metrics"]["competitors"] = [{"name": "Jane App", "url": "https://jane.app",
                                              "kind": "direct"}]
    return section


class OfflineReddit:
    def request(self, method, url, **kwargs):
        return _Response({"data": {"children": [{"data": {
            "display_name": "physiotherapy", "title": "Physiotherapy",
            "public_description": "clinic scheduling and practice talk",
            "subscribers": 52000, "over18": False, "url": "/r/physiotherapy/"}}]}})


class _Response:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


class _Evidence:
    source, error, skeleton, count = "web_search", "", False, 1
    payload = [{"url": "https://jane.app/pricing", "title": "Jane pricing $79 per month"}]


class _AgentResult:
    evidence, steps = [_Evidence()], []
    answer, completed, stop_reason = "Jane lists $79 per month.", True, "done"


def no_agent_found(*args, **kwargs):
    return _AgentResult()


def pricing_model(system, user, **kwargs):
    return {"competitors": [{"competitor": "Jane App", "url": "https://jane.app",
                             "tiers": [{"name": "Practitioner", "price": 79, "currency": "USD",
                                        "billing_model": "monthly", "unit": "practitioner",
                                        "features": ["online booking"]}],
                             "source_urls": ["https://jane.app/pricing"], "notes": ""}]}


def forces_model(system, user, **kwargs):
    return _forces_answer()


def strategy_model(system, user, **kwargs):
    return _strategy_answer()


def analogs_model(system, user, **kwargs):
    return _analogs_answer()


def positioning_model(system, user, **kwargs):
    return _positioning_answer()


def gtm_model(system, user, **kwargs):
    return _gtm_answer(price_summary="Price at $79 per practitioner per month, the level "
                                     "the category already charges.")


PRODUCERS = {
    "competitor_count": partial(competitor_count.produce, env={}),
    "community_discovery": partial(community_discovery.produce, session=OfflineReddit()),
    "pricing": partial(pricing.produce, agent=no_agent_found, model=pricing_model),
    "five_forces": partial(five_forces.produce, model=forces_model),
    "generic_strategy": partial(generic_strategy.produce, model=strategy_model),
    "analog_markets": partial(analog_markets.produce, model=analogs_model),
    "positioning": partial(positioning.produce, model=positioning_model),
    "go_to_market": partial(go_to_market.produce, model=gtm_model),
}


def run_full(key, **overrides):
    options = {"prep_model": fake_prep, "business_model": minimal_business_model,
               "research": full_research, "review": number_review,
               "analysis_producers": PRODUCERS,
               "limits": ExecutionLimits(run_timeout_s=240, task_timeout_s=60), **overrides}
    pack = build_report_type(key=key, **options)
    register(pack)
    try:
        return run_report(key, {"description": "A scheduling tool for small physiotherapy "
                                               "clinics, paid monthly per practitioner."})
    finally:
        REPORT_TYPES.pop(key, None)


def test_every_section_runs_in_order_and_the_report_is_complete():
    report = run_full("full_pack_offline")
    verdicts = {v["key"]: v for v in report.sections["sections"]}
    assert set(verdicts) == {"prep", *FULL_ORDER}
    assert set(FULL_ORDER) == set(REPORT_ORDER) | set(ANALYSIS)
    bad = {k: (v["status"], v["reason"]) for k, v in verdicts.items()
           if v["status"] not in ("ok", "not_applicable")}
    assert bad == {}
    # No geography was given, so there is nothing local to count.
    assert verdicts["competitor_count"]["status"] == "not_applicable"
    assert report.execution["stop_reason"] == "completed", report.execution["issues"]
    assert report.ok

    sizing = report.result["sizing"]["data"]
    top_down = sizing["methods"]["top_down"]["tam"]
    assert top_down["value"] == 2.4e9 and top_down["citations"]
    assert report.result["pricing"]["data"]["category_median_monthly"] == 79
    # One landscape competitor: the obtainable share is computed from that count.
    assert sizing["som_share"]["value"] is not None
    assert report.result["positioning"]["data"]["statement"].startswith("For ")
    # The fake PREP states no product, so discovery has no category words to search with,
    # and says so rather than searching on the brief.
    discovery = report.result["community_discovery"]
    assert discovery["data"]["queries"] == [] and discovery["research_status"] == "partial"

    page = render_html(report)
    for key in ANALYSIS:
        if key in report.result:
            assert f'<h2 id="{key}">' in page and label(key) in page
    assert "<b>bold</b>" not in page            # every model string is escaped
    assert serialize_customer_report(report)["_treadle_status"] == "complete"


def test_analysis_off_keeps_the_eight_section_report():
    report = run_full("full_pack_off", analysis=False, analysis_producers=None)
    assert {v["key"] for v in report.sections["sections"]} == {"prep", *REPORT_ORDER}
    assert report.ok
    assert all(f'id="{key}"' not in render_html(report) for key in ANALYSIS)
