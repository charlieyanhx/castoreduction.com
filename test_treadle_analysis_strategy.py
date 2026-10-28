"""The five strategy sections: one model call each, payload checked in code, honest envelopes.

Offline: every test injects a fake model returning a fixed object.
"""
from __future__ import annotations

import copy
import json

import pytest

from packs.market_research_next.analysis import (
    analog_markets, base, five_forces, generic_strategy, go_to_market, positioning)
from packs.market_research_next.contracts import validate_section_output

RAW_BRIEF = "RAW-BRIEF-TEXT the founder typed, which must never reach these prompts"
PROSE = {"narrative": "Narrative <b>bold</b> per competitive_landscape.",
         "findings": ["Finding per competitive_landscape."],
         "assumptions": ["An assumption."], "uncertainties": ["An open question."]}


def _attr(value, basis="customer_stated"):
    return {"value": value, "basis": basis, "source_fields": ["intake.facts.x"] if value else []}


def _section(title, data=None, status="completed"):
    return base.envelope(title, f"{title} narrative.", data=data or {}, findings=[f"{title} finding"],
                         status=status)


def _context():
    attributes = {f: _attr(None, "unknown") for f in (
        "buyer", "user", "problem", "geography", "constraints", "decision",
        "monthly_cost_estimate", "named_competitors", "status_quo")}
    attributes.update(product=_attr("ShiftPal"), target_customer=_attr("independent cafes"),
                      business_model=_attr("subscription", "inferred"),
                      stage=_attr("pre_launch"), pricing=_attr("$49 per month"),
                      differentiation=_attr("built for single-site owners"))
    prep = {"venture": {"attributes": attributes,
                        "customer_inputs": {"brief": {"description": RAW_BRIEF},
                                            "intake": {"facts": {"description": RAW_BRIEF}}}}}
    pricing = {"category_median_monthly": 45.5, "competitors": [
        {"competitor": "Rival", "tiers": [{"name": "Pro", "price": 29, "currency": "USD"}]}]}
    return {"prep": prep,
            "competitive_landscape": _section("Competitive landscape"),
            "customer_voice": _section("Customer voice"),
            "target_personas": _section("Target personas", status="simulated"),
            "differentiators": _section("Differentiators"),
            "pricing": _section("Pricing", pricing, status="derived"),
            "sizing": _section("Sizing", {"methods": {}, "anchor_method": "bottom_up"},
                               status="derived")}


class Fake:
    """A model stand-in that records each call and returns a deep copy of its answer."""

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def __call__(self, system, user, **kwargs):
        self.calls.append({"system": system, "user": json.loads(user), "raw": user, **kwargs})
        return copy.deepcopy(self.answer)


def _keys(value):
    if isinstance(value, dict):
        return set(value) | {k for child in value.values() for k in _keys(child)}
    if isinstance(value, list):
        return {k for child in value for k in _keys(child)}
    return set()


# --------------------------------------------------------------------------- answers
def _forces_answer():
    forces = {name: {"rating": "Medium", "subfactors": [
        {"factor": f, "rating": "Low", "justification": f"{f} <x> per competitive_landscape."}
        for f in factors]} for name, factors in five_forces.SUBFACTORS.items()}
    return {**PROSE, "forces": forces, "governing_forces": ["rivalry"],
            "profit_potential": "Medium", "implications": ["One.", "Two."]}


def _strategy_answer(rec="differentiation", basis=None, flags_true=0):
    reqs = generic_strategy.requirements_for(rec, basis)
    flags = {k: {"flag": i < flags_true,
                 "reason": "seen in five_forces" if i < flags_true else "not assessable before launch"}
             for i, k in enumerate(generic_strategy.FLAGS)}
    return {**PROSE, "recommendation": rec, "focus_basis": basis, "confidence": "Medium",
            "requirements_fit": [{"requirement": r, "status": "partial", "gap": "gap"} for r in reqs],
            "risks": [{"risk": r, "relevance": "Low", "note": "note"}
                      for r in generic_strategy.RISKS[rec]],
            "stuck_in_the_middle": {**flags, "flag_count": 0, "likely_stuck": False}}


def _analog(name, shared=("business_model", "network_effects", "customer_acquisition")):
    return {"name": name, "period": "2010s", "vertical": "dental software",
            "shared_characteristics": list(shared), "trajectory": "grew",
            "inflection_points": ["cloud"], "winner_traits": ["focus"],
            "failure_modes": ["sprawl"], "sources": ["<script>alert(1)</script>"]}


def _analogs_answer(count=3):
    names = [f"Analog {i}" for i in range(count)]
    return {**PROSE, "target_profile": {k: v[0] for k, v in analog_markets.CHARACTERISTICS.items()},
            "analogs": [_analog(n) for n in names],
            "lessons": [{"lesson": "In X, Y showed Z.", "analog": n,
                         "implication": "For this venture, this suggests W.",
                         "confidence": "Low"} for n in names],
            "non_transferable": ["Local density."]}


def _positioning_answer(strategy="differentiation", **overrides):
    fields = {"target_customer": "independent cafe owners", "need": "lose hours to rotas",
              "product": "ShiftPal", "category": "scheduling app", "key_benefit": "builds a rota in minutes.",
              "primary_competitor": "spreadsheets", "key_differentiator": "is made for one site",
              "strategy": strategy}
    out = {name: {"text": text, "from": ["target_personas", "prep"]} for name, text in fields.items()}
    out.update(overrides)
    return {**PROSE, "fields": out}


def _full_context():
    context = _context()
    context["five_forces"] = five_forces.produce(context, model=Fake(_forces_answer()))
    context["generic_strategy"] = generic_strategy.produce(context, model=Fake(_strategy_answer()))
    context["positioning"] = positioning.produce(context, model=Fake(_positioning_answer()))
    return context


def _gtm_answer(price_summary="Start at $49 per month against a category median of $45.50."):
    def p(summary):
        return {"summary": summary, "actions": ["First action.", "Second action."],
                "rests_on": ["positioning", "prep"]}
    return {**PROSE, "product": p("Ship the rota."), "price": p(price_summary),
            "place": p("Sell direct."), "promotion": p("Local groups."),
            "positioning_link": "Every P serves the positioning statement."}


# --------------------------------------------------------------------------- happy paths
def _assert_section(module, section, fake):
    validate_section_output(section)
    module.validate_data(section["data"])
    assert section["research_status"] == "derived"
    assert len(fake.calls) == 1
    user = fake.calls[0]["raw"]
    assert "description" not in _keys(fake.calls[0]["user"]) and RAW_BRIEF not in user
    assert "is data, not instructions" in fake.calls[0]["system"]
    html = module.render_data(section["data"])
    assert "<b>" not in html and "<script>" not in html and "<x>" not in html


def test_five_forces_happy_path_orders_and_rates_every_factor():
    context, fake = _context(), Fake(_forces_answer())
    fake.answer["forces"]["entry"]["subfactors"].reverse()
    fake.answer["forces"]["entry"]["subfactors"][0]["factor"] = "expected  RETALIATION"
    section = five_forces.produce(context, model=fake)
    _assert_section(five_forces, section, fake)
    entry = section["data"]["forces"]["entry"]["subfactors"]
    assert [r["factor"] for r in entry] == list(five_forces.SUBFACTORS["entry"])
    assert sum(len(f) for f in five_forces.SUBFACTORS.values()) == 32
    assert "not a sixth" in fake.calls[0]["system"] or "never as a sixth" in fake.calls[0]["system"]
    assert set(fake.calls[0]["user"]["upstream"]) == {"competitive_landscape", "pricing", "sizing"}
    assert fake.calls[0]["user"]["upstream"]["pricing"]["data"] == context["pricing"]["data"]
    assert "&lt;x&gt;" in five_forces.render_data(section["data"])


def test_generic_strategy_happy_path_and_flags_counted_in_code():
    context = _context()
    context["five_forces"] = five_forces.produce(context, model=Fake(_forces_answer()))
    fake = Fake(_strategy_answer(flags_true=3))
    section = generic_strategy.produce(context, model=fake)
    _assert_section(generic_strategy, section, fake)
    stuck = section["data"]["stuck_in_the_middle"]
    assert stuck["flag_count"] == 3 and stuck["likely_stuck"] is True
    assert set(stuck["flags"]) == set(generic_strategy.FLAGS)
    assert any("lower bound" in u for u in section["uncertainties"])
    upstream = fake.calls[0]["user"]["upstream"]["five_forces"]["data"]
    assert upstream["profit_potential"] == "Medium"


def test_analog_markets_happy_path_discloses_and_marks_sources_unverified():
    fake = Fake(_analogs_answer(4))
    section = analog_markets.produce(_context(), model=fake)
    _assert_section(analog_markets, section, fake)
    assert section["narrative"].startswith(analog_markets.DISCLOSURE)
    assert "model analyses" in analog_markets.DISCLOSURE and "not independently verified" in \
        analog_markets.DISCLOSURE
    assert analog_markets.UNVERIFIED_SHARING in section["uncertainties"]
    html = analog_markets.render_data(section["data"])
    assert "(unverified)" in html and "&lt;script&gt;" in html


def test_positioning_happy_path_assembles_statement_exactly():
    context = _full_context()
    fake = Fake(_positioning_answer())
    section = positioning.produce(context, model=fake)
    _assert_section(positioning, section, fake)
    assert section["data"]["statement"] == (
        "For independent cafe owners who lose hours to rotas, ShiftPal is a scheduling app "
        "that builds a rota in minutes. Unlike spreadsheets, ShiftPal is made for one site.")
    assert section["data"]["strategy"] == "differentiation"
    assert "prep" in fake.calls[0]["user"]["allowed_sources"]


def test_go_to_market_happy_path_quotes_median_and_founder_price():
    context, fake = _full_context(), Fake(_gtm_answer())
    section = go_to_market.produce(context, model=fake)
    _assert_section(go_to_market, section, fake)
    quotes = section["data"]["price_quotes"]
    assert quotes["category_median_monthly"] == 45.5 and quotes["founder_price"] == "$49 per month"
    assert fake.calls[0]["user"]["upstream"]["positioning"]["data"]["statement"]


# --------------------------------------------------------------------------- five forces
@pytest.mark.parametrize("change", ["missing", "renamed", "extra", "duplicate"])
def test_five_forces_rejects_a_wrong_sub_factor(change):
    answer = _forces_answer()
    rows = answer["forces"]["buyer"]["subfactors"]
    if change == "missing":
        rows.pop()
    elif change == "renamed":
        rows[0]["factor"] = "Buyer size"
    elif change == "extra":
        rows.append({"factor": "Technology", "rating": "Low", "justification": "x"})
    else:
        rows.append(dict(rows[0]))
    with pytest.raises(base.AnalysisError, match="buyer sub-factors"):
        five_forces.produce(_context(), model=Fake(answer))


@pytest.mark.parametrize("where", ["subfactor", "force", "profit"])
def test_five_forces_rejects_bad_ratings(where):
    answer = _forces_answer()
    if where == "subfactor":
        answer["forces"]["rivalry"]["subfactors"][0]["rating"] = "Very high"
    elif where == "force":
        answer["forces"]["rivalry"]["rating"] = 3
    else:
        answer["profit_potential"] = "Unknown"
    with pytest.raises(base.AnalysisError):
        five_forces.produce(_context(), model=Fake(answer))


def test_five_forces_rejects_a_sixth_force_and_bad_governing_forces():
    answer = _forces_answer()
    answer["forces"]["government"] = answer["forces"]["entry"]
    with pytest.raises(base.AnalysisError):
        five_forces.produce(_context(), model=Fake(answer))
    answer = _forces_answer()
    answer["governing_forces"] = ["rivalry", "buyer", "entry"]
    with pytest.raises(base.AnalysisError, match="governing_forces"):
        five_forces.produce(_context(), model=Fake(answer))


# --------------------------------------------------------------------------- generic strategy
def test_generic_strategy_ignores_the_models_own_stuck_verdict():
    context = _context()
    context["five_forces"] = five_forces.produce(context, model=Fake(_forces_answer()))
    answer = _strategy_answer(flags_true=4)
    answer["stuck_in_the_middle"].update(flag_count=0, likely_stuck=False)
    stuck = generic_strategy.produce(context, model=Fake(answer))["data"]["stuck_in_the_middle"]
    assert stuck == {**stuck, "flag_count": 4, "likely_stuck": True}
    tampered = generic_strategy.produce(context, model=Fake(_strategy_answer(flags_true=2)))["data"]
    assert tampered["stuck_in_the_middle"]["likely_stuck"] is False
    tampered["stuck_in_the_middle"]["likely_stuck"] = True
    with pytest.raises(ValueError, match="likely_stuck"):
        generic_strategy.validate_data(tampered)


def test_focus_requires_a_focus_basis_and_uses_its_requirements():
    context = _context()
    context["five_forces"] = five_forces.produce(context, model=Fake(_forces_answer()))
    answer = _strategy_answer("focus", "cost")
    data = generic_strategy.produce(context, model=Fake(answer))["data"]
    assert data["focus_basis"] == "cost"
    assert [r["requirement"] for r in data["requirements_fit"]] == list(
        generic_strategy.REQUIREMENTS["cost_leadership"])
    answer["focus_basis"] = None
    with pytest.raises(base.AnalysisError, match="focus_basis"):
        generic_strategy.produce(context, model=Fake(answer))


def test_generic_strategy_rejects_a_missing_requirement_or_flag():
    context = _context()
    context["five_forces"] = five_forces.produce(context, model=Fake(_forces_answer()))
    answer = _strategy_answer()
    answer["requirements_fit"].pop()
    with pytest.raises(base.AnalysisError, match="requirement"):
        generic_strategy.produce(context, model=Fake(answer))
    answer = _strategy_answer()
    del answer["stuck_in_the_middle"]["blurred_culture"]
    with pytest.raises(base.AnalysisError, match="six named flags"):
        generic_strategy.produce(context, model=Fake(answer))


def test_generic_strategy_needs_five_forces():
    with pytest.raises(base.AnalysisError, match="five_forces"):
        generic_strategy.produce(_context(), model=Fake(_strategy_answer()))


# --------------------------------------------------------------------------- analogs
@pytest.mark.parametrize("shared", [
    ("business_model", "network_effects"),
    ("business_model", "network_effects", "moat_depth"),
    ("business_model", "business_model", "network_effects"),
])
def test_analogs_need_three_real_shared_characteristics(shared):
    answer = _analogs_answer()
    answer["analogs"][1] = _analog("Analog 1", shared)
    with pytest.raises(base.AnalysisError, match="characteristic"):
        analog_markets.produce(_context(), model=Fake(answer))


@pytest.mark.parametrize("count", [2, 6])
def test_analogs_count_must_be_three_to_five(count):
    with pytest.raises(base.AnalysisError, match="analogs must hold"):
        analog_markets.produce(_context(), model=Fake(_analogs_answer(count)))


def test_analog_lessons_must_name_an_analog_and_cover_each():
    answer = _analogs_answer()
    answer["lessons"][0]["analog"] = "Somewhere else"
    with pytest.raises(base.AnalysisError, match="lesson"):
        analog_markets.produce(_context(), model=Fake(answer))
    answer = _analogs_answer()
    answer["lessons"].pop()
    with pytest.raises(base.AnalysisError, match="at least one lesson"):
        analog_markets.produce(_context(), model=Fake(answer))


def test_analog_profile_values_are_checked():
    answer = _analogs_answer()
    answer["target_profile"]["network_effects"] = "huge"
    with pytest.raises(base.AnalysisError, match="network_effects"):
        analog_markets.produce(_context(), model=Fake(answer))


# --------------------------------------------------------------------------- positioning
def test_positioning_strategy_must_match_generic_strategy():
    with pytest.raises(base.AnalysisError, match="generic strategy recommendation"):
        positioning.produce(_full_context(), model=Fake(_positioning_answer("cost_leadership")))


def test_positioning_rejects_sources_not_in_the_context():
    context = _full_context()
    del context["pricing"]
    answer = _positioning_answer(category={"text": "app", "from": ["pricing"]})
    with pytest.raises(base.AnalysisError, match="not supplied"):
        positioning.produce(context, model=Fake(answer))
    answer = _positioning_answer(category={"text": "app", "from": ["the internet"]})
    with pytest.raises(base.AnalysisError, match="not supplied"):
        positioning.produce(_full_context(), model=Fake(answer))


def test_positioning_rejects_an_empty_field_and_a_tampered_statement():
    answer = _positioning_answer(need={"text": " ", "from": ["prep"]})
    with pytest.raises(base.AnalysisError, match="need"):
        positioning.produce(_full_context(), model=Fake(answer))
    data = positioning.produce(_full_context(), model=Fake(_positioning_answer()))["data"]
    with pytest.raises(ValueError, match="statement"):
        positioning.validate_data({**data, "statement": data["statement"] + " Trusted by 10,000."})


# --------------------------------------------------------------------------- go to market
def test_go_to_market_rejects_a_missing_p():
    answer = _gtm_answer()
    del answer["place"]
    with pytest.raises(base.AnalysisError, match="missing its place P"):
        go_to_market.produce(_full_context(), model=Fake(answer))


@pytest.mark.parametrize("summary, match", [
    ("Start at $49 per month.", "category median"),
    ("Match the category median of $45.50.", "founder"),
    ("Start at $49 per month, median $45.50, with a $10 launch credit.", "not in the supplied"),
])
def test_go_to_market_price_quotes_and_adds_no_numbers(summary, match):
    with pytest.raises(base.AnalysisError, match=match):
        go_to_market.produce(_full_context(), model=Fake(_gtm_answer(summary)))


def test_go_to_market_may_cite_competitor_prices_and_needs_actions():
    summary = "At $49 per month we sit above the $45.50 median and Rival's $29 tier."
    go_to_market.produce(_full_context(), model=Fake(_gtm_answer(summary)))
    answer = _gtm_answer()
    answer["promotion"]["actions"] = ["Only one."]
    with pytest.raises(base.AnalysisError, match="promotion needs"):
        go_to_market.produce(_full_context(), model=Fake(answer))


# --------------------------------------------------------------------------- shared
MODULES = {
    five_forces: (_context, _forces_answer),
    analog_markets: (_context, _analogs_answer),
    positioning: (_full_context, _positioning_answer),
    go_to_market: (_full_context, _gtm_answer),
}


@pytest.mark.parametrize("module", list(MODULES), ids=lambda m: m.KEY)
@pytest.mark.parametrize("marker", [{"error": "rate limited"}, {"_parse_error": "x"}, "not json"])
def test_model_error_markers_fail_the_section(module, marker):
    make_context, _ = MODULES[module]
    with pytest.raises(base.AnalysisError, match="no usable JSON"):
        module.produce(make_context(), model=lambda *a, **k: marker)


@pytest.mark.parametrize("module", list(MODULES), ids=lambda m: m.KEY)
def test_empty_prose_fails_the_section(module):
    make_context, make_answer = MODULES[module]
    answer = make_answer()
    answer["findings"] = ["  "]
    with pytest.raises(base.AnalysisError, match="findings"):
        module.produce(make_context(), model=Fake(answer))


def test_missing_required_upstream_fails_before_any_model_call():
    fake = Fake(_forces_answer())
    context = _context()
    del context["competitive_landscape"]
    with pytest.raises(base.AnalysisError, match="competitive_landscape"):
        five_forces.produce(context, model=fake)
    assert fake.calls == []


def test_upstream_limits_are_disclosed():
    context = _context()
    del context["pricing"]
    section = five_forces.produce(context, model=Fake(_forces_answer()))
    assert any("pricing section was not available" in u for u in section["uncertainties"])
    section = positioning.produce(_full_context(), model=Fake(_positioning_answer()))
    assert any("target_personas section is simulated" in u for u in section["uncertainties"])


def test_no_em_dashes_in_the_modules():
    for module in (*MODULES, generic_strategy):
        with open(module.__file__, encoding="utf-8") as handle:
            assert chr(0x2014) not in handle.read()


def test_the_price_p_holds_money_to_the_data_but_not_durations_or_percentages():
    from packs.market_research_next.analysis.go_to_market import _check_price

    quotes = {"category_median_monthly": 39.0, "founder_price": "$39 per practitioner, 15 staff",
              "competitor_prices": [49.0]}
    ok = {"summary": "Price at $39 a month against a $49 rival.",
          "actions": ["Offer a 14-day trial", "Give 20% off annual plans"]}
    _check_price(ok, quotes)
    with pytest.raises(ValueError, match="price that is not in the supplied data"):
        _check_price({"summary": "Price at $39, later $59.", "actions": []}, quotes)
