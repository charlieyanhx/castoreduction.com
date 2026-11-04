"""Pricing section and the cross-section number check, fully offline."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from packs.market_research_next.analysis import consistency, pricing
from packs.market_research_next.contracts import validate_section_output


# --------------------------------------------------------------------------- fixtures
def _ev(payload, source="web_search", *, error=None, skeleton=False, count=None):
    return SimpleNamespace(payload=payload, source=source, error=error, skeleton=skeleton,
                           count=len(payload) if count is None and isinstance(payload, list)
                           else (1 if count is None else count))


def _result(evidence, *, completed=True, stop_reason="answered"):
    return SimpleNamespace(evidence=evidence, answer="found pricing", completed=completed,
                           stop_reason=stop_reason, steps=[])


def _hits(*urls):
    return _ev([{"title": "t", "url": u, "snippet": "s"} for u in urls])


class Agent:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def __call__(self, goal, **kwargs):
        self.calls.append({"goal": goal, **kwargs})
        if self.error:
            raise self.error
        return self.result


class Model:
    def __init__(self, response):
        self.response, self.calls = response, []

    def __call__(self, system, user, **kwargs):
        self.calls.append(json.loads(user))
        return self.response


def _context(competitors=None, *, pricing_text="$29 per month", levels=None):
    attrs = {"pricing": {"value": pricing_text, "basis": "customer_stated",
                         "source_fields": ["pricing"]},
             "product": {"value": "Scheduling app", "basis": "customer_stated",
                         "source_fields": ["product"]}}
    context = {"prep": {"venture": {"attributes": attrs, "customer_inputs": {"intake": {
        "facts": {"unit_economics": "about $8 cost per customer"}}}}}}
    if competitors is not None:
        context["competitive_landscape"] = {"title": "Landscape", "narrative": "n",
                                            "metrics": {"competitors": competitors}}
    if levels is not None:
        context["competitor_count"] = {"title": "Count", "narrative": "n", "data": levels}
    return context


def _comp(name, kind="direct", url=None):
    return {"name": name, "kind": kind, "url": url}


def _tier(name="Pro", price=10, billing="monthly", currency="USD", unit=None):
    return {"name": name, "price": price, "currency": currency, "billing_model": billing,
            "unit": unit, "features": ["a"]}


def _entry(name, sources, tiers=None):
    return {"competitor": name, "url": None, "tiers": tiers or [_tier()],
            "source_urls": sources, "notes": None}


def _run(entries, urls, competitors=None, **kwargs):
    agent = Agent(_result([_hits(*urls)]))
    model = Model({"competitors": entries})
    out = pricing.produce(_context(competitors or [_comp(e["competitor"]) for e in entries],
                                   **kwargs), model=model, agent=agent)
    validate_section_output(out)
    pricing.validate_data(out["data"])
    return out, agent, model


# --------------------------------------------------------------------------- pricing
def test_selection_puts_direct_first_and_caps_at_five():
    comps = [_comp("I1", "indirect"), _comp("D1"), _comp("X", "other"), _comp("D2"),
             _comp("I2", "indirect"), _comp("D3"), _comp("D4"), _comp("d1"), "junk",
             {"name": ""}]
    out, agent, _ = _run([], ["https://a.com/p"], competitors=comps)
    sent = json.loads(agent.calls[0]["context"])["competitors"]
    assert [c["name"] for c in sent] == ["D1", "D2", "D3", "D4", "I1"]
    assert [c["competitor"] for c in out["data"]["competitors"]] == ["D1", "D2", "D3", "D4", "I1"]


@pytest.mark.parametrize("competitors", [None, [], "not a list", [{"kind": "direct"}]])
def test_no_competitors_is_partial_and_keeps_places_levels(competitors):
    agent = Agent(error=AssertionError("must not run"))
    levels = {"available": True, "price_level_counts": {"1": 3, "2": 5},
              "median_price_level": 2}
    out = pricing.produce(_context(competitors, levels=levels), model=Model({}), agent=agent)
    validate_section_output(out)
    pricing.validate_data(out["data"])
    assert out["research_status"] == "partial" and not agent.calls
    assert "No competitors were identified" in out["uncertainties"][0]
    assert out["data"]["local_price_levels"] == {"counts": {"1": 3, "2": 5}, "median": 2.0}
    assert "median price level 2" in out["narrative"]


def test_counted_places_stand_in_when_the_landscape_lists_no_competitors():
    # Marketplace, 2026-11-03: Places had counted Mutts in the 6ix while the landscape
    # returned no list, and pricing reported that no competitors were identified.
    context = _context(None)
    context["competitor_count"] = {"data": {"available": True, "competitors": [
        {"name": "Mutts in the 6ix", "website": "https://mutts.example/"},
        {"name": "No Site Walkers"}]}}
    assert pricing._selected(context) == [
        {"name": "Mutts in the 6ix", "url": "https://mutts.example/", "kind": None},
        {"name": "No Site Walkers", "url": None, "kind": None}]


def test_the_landscape_list_wins_over_counted_places():
    context = _context([_comp("Rover")])
    context["competitor_count"] = {"data": {"available": True, "competitors": [
        {"name": "Mutts in the 6ix", "website": "https://mutts.example/"}]}}
    assert [c["name"] for c in pricing._selected(context)] == ["Rover"]


def test_one_companys_tiers_are_not_a_category_median():
    # Vague brief, 2026-11-03: Todoist's own two tiers were reported as a median of $7.50.
    entry = _entry("Todoist", ["https://todoist.com/p"], [_tier(price=5), _tier("Biz", 10)])
    out, _, _ = _run([entry, _entry("Motion", [], [])], ["https://todoist.com/p"])
    assert out["data"]["category_median_monthly"] is None
    assert out["data"]["category_range_monthly"] == [5, 10]
    assert "the median is" not in out["narrative"] and "no category median" in out["narrative"]
    assert "Only one competitor" in out["narrative"]
    assert any("Todoist" in f for f in out["findings"])


def test_two_companies_make_a_median():
    entries = [_entry("A", ["https://a.com/p"], [_tier(price=5)]),
               _entry("B", ["https://b.com/p"], [_tier(price=15)])]
    out, _, _ = _run(entries, ["https://a.com/p", "https://b.com/p"])
    assert out["data"]["category_median_monthly"] == 10


def test_one_cafes_menu_is_not_a_per_item_median():
    menu = [_tier("Latte", 5, "per_unit", unit="per drink"), _tier("Mocha", 6, "per_unit", unit="per drink")]
    out, _, _ = _run([_entry("Acme", ["https://acme.com/p"], menu)], ["https://acme.com/p"])
    assert out["data"]["category_median_per_unit"] is None


def _read(name, url, content):
    return SimpleNamespace(source="read_page", error=None, skeleton=False, count=1,
                           payload={"url": url, "competitor": name, "content": content})


def _with_page(entry, page):
    agent = Agent(_result([page]))
    return pricing.produce(_context([_comp(entry["competitor"])]), model=Model({"competitors": [entry]}),
                           agent=agent)


def test_a_price_found_on_the_page_read_is_verified_without_a_citation():
    # B2B, 2026-11-03: Cliniko's six tiers were dropped because the writer cited no URL.
    out = _with_page(_entry("Cliniko", [], [_tier(price=45)]),
                     _read("Cliniko", "https://cliniko.com/pricing", "1 practitioner $45 a month"))
    comp = out["data"]["competitors"][0]
    assert comp["verified"] is True and comp["source_urls"] == ["https://cliniko.com/pricing"]


def test_a_price_not_on_the_page_is_not_verified():
    out = _with_page(_entry("Cliniko", [], [_tier(price=45)]),
                     _read("Cliniko", "https://cliniko.com/pricing", "Plans from $450 a month"))
    assert out["data"]["competitors"][0]["verified"] is False


def test_another_companys_page_never_verifies_a_price():
    out = _with_page(_entry("Cliniko", [], [_tier(price=45)]),
                     _read("Jane App", "https://jane.app/pricing", "Pro $45 a month"))
    assert out["data"]["competitors"][0]["verified"] is False


def test_the_website_places_returned_beats_an_article_from_the_landscape():
    context = _context([_comp("Barista", url="https://brewatlas.co/portland/barista")])
    context["competitor_count"] = {"data": {"available": True, "competitors": [
        {"name": "Barista", "website": "https://baristapdx.com/pages/nob-hill"}]}}
    assert pricing._selected(context)[0]["url"] == "https://baristapdx.com/pages/nob-hill"


def test_an_article_is_not_kept_as_a_competitors_website():
    context = _context([_comp("Todoist", url="https://reclaim.ai/blog/apps"),
                        _comp("Motion", url="https://usemotion.com/")])
    assert [c["url"] for c in pricing._selected(context)] == [None, "https://usemotion.com/"]


def test_hallucinated_source_url_is_dropped_with_a_warning():
    entry = _entry("Acme", ["https://acme.com/pricing/", "https://invented.io/prices"])
    out, _, model = _run([entry], ["https://acme.com/pricing"])
    comp = out["data"]["competitors"][0]
    assert comp["source_urls"] == ["https://acme.com/pricing/"]
    assert comp["confidence"] == "low" and comp["verified"]
    assert any("invented.io" in w for w in out["data"]["warnings"])
    assert model.calls[0]["allowed_source_urls"] == ["https://acme.com/pricing"]


@pytest.mark.parametrize("sources,expected", [
    ([], "none"),
    (["https://www.acme.com/p", "https://acme.com/q", "https://shop.acme.com/r"], "low"),
    (["https://acme.com/p", "https://www.g2.com/acme"], "medium"),
    (["https://acme.com/p", "https://g2.com/a", "https://news.bbc.co.uk/x"], "high"),
    (["https://acme.com/p", "https://g2.com/a", "https://capterra.com/a",
      "https://web.archive.org/web/2024/https://acme.com/p"], "high"),
])
def test_confidence_counts_registered_domains(sources, expected):
    out, _, _ = _run([_entry("Acme", sources)], sources or ["https://acme.com/p"])
    comp = out["data"]["competitors"][0]
    assert comp["confidence"] == expected
    assert comp["verified"] is (expected != "none")
    if expected == "none":
        assert out["research_status"] == "partial" and not out["findings"]


def test_wayback_copy_counts_as_its_original_site():
    sources = ["https://acme.com/p", "https://web.archive.org/web/2024/https://acme.com/p"]
    out, _, _ = _run([_entry("Acme", sources)], sources)
    assert out["data"]["competitors"][0]["source_domains"] == ["acme.com"]


def test_conflicting_tier_prices_are_never_high():
    sources = ["https://acme.com/p", "https://g2.com/a", "https://capterra.com/a"]
    tiers = [_tier(price=10), _tier(price=12), _tier(price=100, billing="annual")]
    out, _, _ = _run([_entry("Acme", sources, tiers)], sources)
    comp = out["data"]["competitors"][0]
    assert comp["conflict"] is True and comp["confidence"] == "medium"
    assert any("capped at medium" in u for u in out["uncertainties"])


def test_monthly_and_annual_prices_of_one_tier_are_not_a_conflict():
    sources = ["https://acme.com/p", "https://g2.com/a", "https://capterra.com/a"]
    tiers = [_tier(price=12), _tier(price=120, billing="annual")]
    out, _, _ = _run([_entry("Acme", sources, tiers)], sources)
    assert out["data"]["competitors"][0]["confidence"] == "high"


def test_monthly_normalization_median_and_range():
    entries = [
        _entry("A", ["https://a.com/p"], [_tier(price=10), _tier("Team", 240, "annual"),
                                          _tier("Setup", 99, "one_time"),
                                          _tier("Ent", None, "custom")]),
        _entry("B", ["https://b.com/p"], [_tier(price=30), _tier("EU", 5, currency="EUR")]),
        _entry("C", [], [_tier(price=1)]),  # unverified: not in the median
    ]
    out, _, _ = _run(entries, ["https://a.com/p", "https://b.com/p"])
    data = out["data"]
    tiers = data["competitors"][0]["tiers"]
    assert [t["monthly_price"] for t in tiers] == [10, 20, None, None]
    assert data["currency"] == "USD"
    assert data["category_median_monthly"] == 20 and data["category_range_monthly"] == [10, 30]
    assert [e["tier"] for e in data["excluded_from_median"]] == ["Setup"]
    assert any("EUR" in w for w in data["warnings"])
    assert out["research_status"] == "completed"
    assert any("A: Pro USD 10 per month" in f and "low confidence" in f for f in out["findings"])
    assert not any(f.startswith("C:") for f in out["findings"])
    assert "No verified public pricing for: C." in out["uncertainties"]
    assert data["founder_price_text"] == ("Pricing: $29 per month; Unit economics: about $8 "
                                          "cost per customer")


def test_agent_failure_is_partial_without_provider_text():
    agent = Agent(error=RuntimeError("anthropic 529 overloaded sk-secret"))
    out = pricing.produce(_context([_comp("Acme")]), model=Model({}), agent=agent)
    validate_section_output(out)
    assert out["research_status"] == "partial"
    assert "external research failed" in out["uncertainties"]
    assert "sk-secret" not in json.dumps(out) and "529" not in json.dumps(out)


def test_no_usable_evidence_skips_the_model():
    agent = Agent(_result([_ev(None), _ev([], count=0), _ev(["x"], error="boom"),
                           _ev(["x"], skeleton=True)], completed=False, stop_reason="max_steps"))
    model = Model(AssertionError)
    out = pricing.produce(_context([_comp("Acme")]), model=model, agent=agent)
    assert out["research_status"] == "partial" and not model.calls
    assert "max_steps" in out["uncertainties"][0]


def test_unknown_competitors_and_billing_models_are_contained():
    entries = [_entry("Acme", ["https://acme.com/p"], [_tier(billing="weekly")]),
               _entry("Ghost Corp", ["https://acme.com/p"])]
    out, _, _ = _run(entries, ["https://acme.com/p"], competitors=[_comp("Acme")])
    assert [c["competitor"] for c in out["data"]["competitors"]] == ["Acme"]
    assert out["data"]["competitors"][0]["tiers"][0]["billing_model"] == "custom"
    assert any("Ghost Corp" in w for w in out["data"]["warnings"])


def test_url_collection_is_recursive_and_ignores_failed_items():
    urls = set()
    pricing._collect_urls({"a": [{"link": "https://x.com/1"}, {"nested": {"href":
                           "http://y.com/2#frag"}}], "b": "https://web.archive.org/web/1/z"},
                          urls)
    assert urls == {"https://x.com/1", "http://y.com/2", "https://web.archive.org/web/1/z"}


def test_validate_data_rejects_bad_payloads():
    out, _, _ = _run([_entry("Acme", ["https://acme.com/p"])], ["https://acme.com/p"])
    good = out["data"]
    bad = json.loads(json.dumps(good))
    bad["competitors"][0]["tiers"][0]["billing_model"] = "weekly"
    with pytest.raises(ValueError, match="billing_model"):
        pricing.validate_data(bad)
    bad = json.loads(json.dumps(good))
    bad["competitors"][0].update(confidence="high", conflict=True)
    with pytest.raises(ValueError):
        pricing.validate_data(bad)
    with pytest.raises(ValueError):
        pricing.validate_data({**good, "category_range_monthly": [5, 1]})


def test_render_escapes_html():
    tiers = [_tier("<b>Pro</b>", 10, unit="<i>seat</i>")]
    out, _, _ = _run([_entry("<script>x</script>", ["https://acme.com/p"], tiers),
                      _entry("Other", ["https://other.com/p"], [_tier(price=10)])],
                     ["https://acme.com/p", "https://other.com/p"], pricing_text="<img src=x>")
    html = pricing.render_data(out["data"])
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<img" not in html and "<b>" not in html and "&lt;i&gt;seat" in html
    assert "Category median: USD 10 per month" in html


def test_the_agent_can_open_pages_and_has_each_competitors_website():
    # Live 2026-10-30: 0 of 5 cafe prices found; the agent could not open a page, and the
    # websites Google Places returned never reached it.
    places = {"available": True, "price_level_counts": {"1": 2}, "median_price_level": 1,
              "competitors": [{"name": "Barista", "website": "https://baristapdx.com/nob-hill"},
                              {"name": "Sterling Coffee Roasters",
                               "website": "http://sterling.coffee/"}]}
    agent = Agent(_result([_hits("https://baristapdx.com/menu")]))
    pricing.produce(_context([_comp("barista"), _comp("Case Study"),
                              _comp("Sterling Coffee Roasters", url="https://sterling.coffee/")],
                             levels=places), model=Model({"competitors": []}), agent=agent)
    sent = json.loads(agent.calls[0]["context"])["competitors"]
    assert {c["name"]: c["url"] for c in sent} == {
        "barista": "https://baristapdx.com/nob-hill", "Sterling Coffee Roasters":
        "https://sterling.coffee/", "Case Study": None}
    assert [c["name"] for c in sent][-1] == "Case Study"   # a known site is tried first
    assert "website" in agent.calls[0]["goal"] and "menu" in agent.calls[0]["goal"]


def test_menu_prices_get_a_per_item_median():
    urls = ["https://a.com/menu", "https://b.com/menu", "https://c.com/menu"]
    entries = [_entry(n, [u], [_tier("Latte", p, "per_unit", unit="per drink")])
               for n, u, p in (("A", urls[0], 5.0), ("B", urls[1], 6.0), ("C", urls[2], 4.5))]
    out, _, _ = _run(entries, urls)
    data = out["data"]
    assert data["category_median_per_unit"] == {"unit": "per drink", "median": 5.0,
                                                "range": [4.5, 6.0], "currency": "USD"}
    assert "per drink the median is USD 5," in out["narrative"]
    assert not any("not billed monthly" in u for u in out["uncertainties"])
    assert not any("divided by 12" in a for a in out["assumptions"])


# --------------------------------------------------------------------------- consistency
def test_true_conflict_is_detected_with_confidence():
    out = consistency.find_number_conflicts({
        "market_size": "The serviceable market for dog grooming salons is $1.2M in Austin.",
        "revenue": "We estimate dog grooming salons in Austin earn $900K combined.",
    })
    assert len(out) == 1
    c = out[0]
    assert (c["value_a"], c["value_b"]) == (1_200_000, 900_000)
    assert c["section_a"] == "market_size" and c["section_b"] == "revenue"
    assert c["topic"] == "austin grooming salons"  # "dog" is too short to be a token
    assert c["confidence"] == pytest.approx(0.8)
    assert "$1.2M" in c["context_a"] and c["type"] == "number"


def test_unit_class_mismatch_is_ignored():
    assert consistency.find_number_conflicts({
        "a": "Churn for subscription grooming plans is 40% yearly.",
        "b": "Churn for subscription grooming plans is 40 customers yearly.",
    }) == []


def test_small_difference_is_ignored():
    assert consistency.find_number_conflicts({
        "a": "Grooming salons in Austin number 100 locations.",
        "b": "Grooming salons in Austin number 103 locations.",
    }) == []


def test_years_and_list_indices_are_not_figures():
    assert consistency._numbers("In 2024 and 1999 grooming grew.") == []
    nums = consistency._numbers("1. Grooming salons\n2) Austin pets\nWe saw 3. Then 7.5 more.")
    assert [n["value"] for n in nums] == [3.0, 7.5]


def test_needs_two_shared_topic_tokens():
    assert consistency.find_number_conflicts({
        "a": "Grooming demand is 40 now.", "b": "Grooming supply is 90 now."}) == []
    found = consistency.find_number_conflicts({
        "a": "Grooming demand is 40 now.", "b": "Grooming demand is 90 now."})
    assert [c["topic"] for c in found] == ["demand grooming"]
    assert found[0]["confidence"] == pytest.approx(0.7)


def test_scale_words_and_digits_inside_words():
    nums = consistency._numbers("Revenue of 3 million, then $2.5 billion, 5k users, B2B, Q3.")
    assert [(n["value"], n["kind"]) for n in nums] == [
        (3e6, "count"), (2.5e9, "money"), (5e3, "headcount")]


def test_conflicts_are_deduplicated_by_pair_and_topic():
    out = consistency.find_number_conflicts({
        "a": "Austin grooming salons charge $50 per visit and $60 per visit.",
        "b": "Austin grooming salons charge $80 per visit.",
    })
    assert len(out) == 1


def test_figures_over_different_periods_are_not_compared():
    # Live 2026-10-30: "$2.00 to $3.00" per seat-hour against $123,188 a year was flagged,
    # and the repair wrote the comparison into the summary.
    assert consistency.find_number_conflicts({
        "a": "Laptop workers bring pastry revenue of about $3 per seat-hour at the counter.",
        "b": "Laptop workers bring pastry revenue of about $123,188 a year at the counter.",
    }) == []
    assert [n["rate"] for n in consistency._numbers(
        "$6 a drink, $28,000 monthly, $50 per visit, $2 per seat-hour, $9 total")] == [
        "drink", "month", "visit", "seat-hour", None]


def test_figures_far_apart_in_size_are_different_quantities():
    assert consistency.find_number_conflicts({
        "a": "The Austin grooming salon earns pastry revenue of $3 here.",
        "b": "The Austin grooming salon earns pastry revenue of $123,188 here.",
    }) == []


def test_repair_message_asks_for_a_silent_correction():
    strong = "The Austin dog grooming salon market totals {} annually."
    result = {"market_size": _section(strong.format("$1.2M")),
              "revenue": _section(strong.format("$900K"))}
    message = consistency.conflict_issues(result, model_written={"market_size", "revenue"})[0][
        "message"]
    assert "state why they differ" not in message
    assert "do not mention this check" in message


def _section(narrative, findings=()):
    return {"title": "t", "narrative": narrative, "findings": list(findings),
            "assumptions": [], "uncertainties": []}


def test_section_texts_reads_envelopes_only():
    result = {"a": _section("N", ["F1"]), "b": "not an envelope", "c": _section(" ")}
    assert consistency.section_texts(result, ["a", "b", "c", "missing"]) == {"a": "N\nF1"}


def test_conflict_issues_filters_by_writer_threshold_and_limit():
    strong = "The Austin dog grooming salon market totals {} annually."
    weak = "Grooming salons hold {} now."
    result = {
        "market_size": _section(strong.format("$1.2M"), [weak.format("$5")]),
        "revenue": _section(strong.format("$900K"), [weak.format("$9")]),
        "pricing": _section(strong.format("$3M")),
        "summary": _section(strong.format("$4M")),
    }
    issues = consistency.conflict_issues(result, model_written={"market_size", "revenue",
                                                                "summary"})
    assert issues and all(i["code"] == "number_conflict" for i in issues)
    assert all("pricing" not in i["repair"] for i in issues)
    assert len(issues) <= 3
    assert issues[0]["repair"] == ["market_size", "revenue"]
    assert "1,200,000" in issues[0]["message"] and "900,000" in issues[0]["message"]
    assert consistency.conflict_issues(result, model_written={"market_size", "revenue",
                                                              "summary"}, limit=1) == issues[:1]
    assert consistency.conflict_issues(result, model_written={"market_size", "revenue"},
                                       min_confidence=1.01) == []
    assert consistency.conflict_issues(result, model_written={"pricing"}) == []


def test_a_competitors_own_pricing_page_is_at_least_medium():
    from packs.market_research_next.analysis.pricing import _score

    chosen = {"name": "Jane App", "url": "https://jane.app", "kind": "direct"}
    own = _score(chosen, {"tiers": [], "source_urls": ["https://www.jane.app/pricing"],
                          "notes": None})
    other = _score(chosen, {"tiers": [], "source_urls": ["https://www.g2.com/products/jane"],
                            "notes": None})
    assert (own["confidence"], other["confidence"]) == ("medium", "low")


def test_places_levels_are_ignored_when_no_search_ran():
    from packs.market_research_next.analysis.pricing import _local_levels

    zeros = {str(n): 0 for n in range(5)}
    unavailable = {"competitor_count": {"data": {"available": False,
                                                 "price_level_counts": zeros}}}
    empty = {"competitor_count": {"data": {"available": True, "price_level_counts": zeros,
                                           "median_price_level": None}}}
    assert _local_levels(unavailable) is None and _local_levels(empty) is None
