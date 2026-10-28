"""The three computed market sections: Places count, Reddit discovery and sizing.

Fully offline: every outside call goes to a fake session, and the numbers are checked
against values worked out by hand from docs/treadle/AGENT_REVAMP.md's formulas.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from packs.market_research_next.analysis import community_discovery as cd
from packs.market_research_next.analysis import competitor_count as cc
from packs.market_research_next.analysis import sizing
from packs.market_research_next.contracts import validate_section_output
from packs.market_research_next.prep import VENTURE_FIELDS

NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)
FRESH, STALE = "2026-09-01", "2026-01-01"
KEY_ENV = {"GOOGLE_MAPS_API_KEY": "test-key"}


class Resp:
    def __init__(self, payload=None, status=200):
        self.status_code, self.payload = status, payload

    def json(self):
        return self.payload


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, headers=None, params=None, json=None, timeout=None,
                data=None, auth=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "params": params,
                           "json": json, "timeout": timeout, "data": data, "auth": auth})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


REDDIT_ENV = {"REDDIT_CLIENT_ID": "id", "REDDIT_CLIENT_SECRET": "secret"}


def signed(*responses):
    """A Reddit session that first grants an app-only token, then answers searches."""
    return Session(Resp({"access_token": "tok"}), *responses)


def context(attrs=None, facts=None, description="A place.", **upstream):
    attributes = {name: {"value": None, "basis": "unknown", "source_fields": []}
                  for name in VENTURE_FIELDS}
    for name, value in (attrs or {}).items():
        attributes[name] = {"value": value, "basis": "customer_stated",
                            "source_fields": [f"intake.facts.{name}"]}
    prep = {"venture": {"attributes": attributes, "customer_inputs": {
        "description": description, "intake": {"facts": facts or {}, "unknowns": []}}}}
    return {"prep": prep, **upstream}


def checked(module, section):
    validate_section_output(section)
    module.validate_data(section["data"])
    return section


CAFE = context({"product": "A specialty coffee shop on NW 23rd Avenue in Portland",
                "geography": "NW 23rd Avenue, Portland, Oregon. People walk 10 minutes."},
               {"delivery_mode": "In person."})


def place(name, **extra):
    return {"id": name, "displayName": {"text": name}, **extra}


# --------------------------------------------------------------------------- competitor_count
def test_category_and_location_rules():
    assert cc.category_phrase(CAFE) == "specialty coffee shop"
    assert cc.location_phrase(CAFE) == "NW 23rd Avenue, Portland, Oregon"


@pytest.mark.parametrize("product,category", [
    ("Single-site, in-person specialty coffee shop on NW 23rd Avenue, Portland. It serves.",
     "specialty coffee shop"),
    ("An app where dog owners book local dog walkers. Bookings are online.", "dog walkers"),
    ("A meal-planning service for busy parents.", "meal-planning service"),
    ("An online scheduling tool for small physiotherapy clinics.", "scheduling tool")])
def test_category_rule_on_prep_shaped_products(product, category):
    assert cc.category_phrase(context({"product": product})) == category


def test_reddit_terms_split_hyphens_and_possessives():
    ctx = context({"product": "A meal-planning service for busy parents.",
                   "geography": "Only Chicago's Lincoln Park neighbourhood"})
    assert cd.build_queries(ctx) == ["meal planning", "meal planning chicago lincoln"]


def test_places_request_parsing_ranking_and_price_levels():
    payload = {"places": [
        place("Coava", rating=4.6, userRatingCount=900, priceLevel="PRICE_LEVEL_MODERATE",
              businessStatus="OPERATIONAL", formattedAddress="1 Main St"),
        place("Gone", businessStatus="CLOSED_PERMANENTLY", priceLevel="PRICE_LEVEL_EXPENSIVE"),
        place("Barista", rating=4.4, userRatingCount=300, priceLevel="PRICE_LEVEL_INEXPENSIVE",
              businessStatus="OPERATIONAL"),
        place("Free Tea", rating=4.0, priceLevel="PRICE_LEVEL_FREE"),
        place("Odd", priceLevel="PRICE_LEVEL_UNSPECIFIED", businessStatus="CLOSED_TEMPORARILY"),
    ]}
    session = Session(Resp(payload))
    section = checked(cc, cc.produce(CAFE, session=session, env=KEY_ENV))
    call = session.calls[0]
    assert call["method"] == "POST" and call["url"] == cc.URL
    assert call["headers"]["X-Goog-Api-Key"] == "test-key"
    assert call["headers"]["X-Goog-FieldMask"] == (
        "places.id,places.displayName,places.formattedAddress,places.rating,"
        "places.userRatingCount,places.priceLevel,places.businessStatus,places.primaryType,"
        "places.websiteUri")
    assert call["json"] == {"textQuery": "specialty coffee shop in NW 23rd Avenue, Portland, Oregon",
                            "maxResultCount": 20, "languageCode": "en"}
    data = section["data"]
    assert [(c["rank"], c["name"]) for c in data["competitors"]] == [
        (1, "Coava"), (2, "Barista"), (3, "Free Tea"), (4, "Odd")]
    assert [c["price_level"] for c in data["competitors"]] == [2, 1, 0, None]
    assert data["count"] == 4 and data["median_rating"] == 4.4
    assert data["median_price_level"] == 1.0
    assert data["price_level_counts"] == {"0": 1, "1": 1, "2": 1, "3": 0, "4": 0}
    assert section["research_status"] == "completed"


def test_twenty_results_disclose_the_cap():
    payload = {"places": [place(f"Cafe {n}") for n in range(20)]}
    section = cc.produce(CAFE, session=Session(Resp(payload)), env=KEY_ENV)
    assert any("at most 20" in u for u in section["uncertainties"])


def test_no_key_is_partial_and_makes_no_call():
    session = Session()
    section = checked(cc, cc.produce(CAFE, session=session, env={}))
    assert session.calls == [] and section["research_status"] == "partial"
    assert section["data"]["available"] is False
    assert any("Google Places key is not configured" in u for u in section["uncertainties"])


def test_a_failed_places_call_is_partial_not_a_failure():
    section = checked(cc, cc.produce(CAFE, session=Session(Resp({"error": "secret body"},
                                                                 status=403)), env=KEY_ENV))
    assert section["research_status"] == "partial"
    assert section["data"]["available"] is False
    assert "403" in section["uncertainties"][0] and "secret" not in str(section)


@pytest.mark.parametrize("mode,applies", [
    ("Online.", False), ("Delivered online through our web app", False),
    ("A mix: booked online, walked in person.", True), ("In person.", True)])
def test_inapplicable_online_only(mode, applies):
    decided = context({"geography": "Toronto"}, {"delivery_mode": mode})
    assert (cc.inapplicable(decided) is None) is applies


def test_inapplicable_without_any_geography():
    assert "no location" in cc.inapplicable(context({"product": "Bakery"}))


def test_competitor_render_escapes_and_validate_rejects():
    data = {"available": True, "query": "q", "location": "x", "count": 1,
            "competitors": [{"rank": 1, "name": "<script>x</script>", "address": None,
                             "rating": None, "reviews": None, "price_level": None,
                             "status": None, "category": None, "website": None}],
            "median_rating": None, "median_price_level": None,
            "price_level_counts": {str(n): 0 for n in range(5)}}
    assert "<script>" not in cc.render_data(data) and "&lt;script&gt;" in cc.render_data(data)
    data["competitors"][0]["rank"] = 2
    with pytest.raises(ValueError):
        cc.validate_data(data)


# --------------------------------------------------------------------------- community_discovery
def sub(name, subscribers, title="", description="", over18=False):
    return {"data": {"display_name": name, "title": title, "public_description": description,
                     "subscribers": subscribers, "over18": over18, "url": f"/r/{name}/"}}


def test_reddit_confidence_rules():
    terms = ["coffee", "portland"]
    assert cd.confidence(terms, "PortlandCoffee", "", "", 50_000) == 1.0
    assert cd.confidence(terms, "pics", "coffee in portland", "", 50_000) == 0.3
    assert cd.confidence(["pics"], "pics", "", "", 50_000) == 1.0  # the query names it
    assert cd.confidence(terms, "Coffee", "", "", 2_000_000) == 0.55
    assert cd.confidence(terms, "PortlandCoffee", "", "", 2_000_000) == 1.0  # capped
    assert cd.confidence(terms, "PortlandCoffee", "", "", 9_999) == 0.9


@pytest.mark.parametrize("score,name", [(0.8, "exact"), (0.79, "semantic"), (0.6, "semantic"),
                                        (0.35, "adjacent"), (0.34, "peripheral")])
def test_tiers(score, name):
    assert cd.tier(score) == name


def test_quality_bands():
    def subs(*scores):
        return [{"confidence": s} for s in scores]
    assert cd.quality(subs(0.71, 0.8, 0.9, 1.0, 0.75)) == "good"
    assert cd.quality(subs(0.7, 0.8, 0.9, 1.0, 0.75)) == "moderate"
    assert cd.quality(subs(0.5, 0.5, 0.5)) == "moderate"
    assert cd.quality(subs(0.5, 0.49, 0.9)) == "poor"


def test_queries_never_carry_the_description():
    ctx = context({"product": "A specialty coffee shop on NW 23rd Avenue",
                   "geography": "NW 23rd Avenue, Portland, Oregon"},
                  description="Zebra quantum flamingo espresso bar for laptops.")
    assert cd.build_queries(ctx) == ["specialty coffee shop", "specialty coffee shop portland"]
    session = signed(Resp({"data": {"children": []}}), Resp({"data": {"children": []}}))
    cd.produce(ctx, session=session, env=REDDIT_ENV)
    grant, searches = session.calls[0], session.calls[1:]
    assert grant["url"] == cd.TOKEN_URL and grant["auth"] == ("id", "secret")
    assert grant["data"] == {"grant_type": "client_credentials"}
    sent = " ".join(c["params"]["q"] for c in searches).lower()
    assert not {"zebra", "quantum", "flamingo", "laptops"} & set(sent.split())
    assert all(c["headers"]["User-Agent"] == cd.USER_AGENT for c in session.calls)
    assert all(c["url"] == cd.URL and c["headers"]["Authorization"] == "bearer tok"
               for c in searches)
    assert searches[0]["params"]["limit"] == 25
    assert searches[0]["params"]["include_over_18"] == "false"


def test_ranking_merges_drops_nsfw_and_sorts():
    first = {"data": {"children": [
        sub("pics", 30_000_000, "coffee shop pics"),
        sub("specialtycoffee", 5_000, "Specialty coffee"),
        sub("CoffeeShopNSFW", 90_000, "specialty coffee shop", over18=True),
        sub("Coffee", 2_000_000, "Coffee lovers"),
    ]}}
    second = {"data": {"children": [sub("specialtycoffee", 5_000, "Specialty coffee"),
                                    sub("askportland", 80_000, "Portland questions")]}}
    section = checked(cd, cd.produce(CAFE, session=signed(Resp(first), Resp(second)),
                                     env=REDDIT_ENV))
    data = section["data"]
    names = [s["name"] for s in data["subreddits"]]
    assert "CoffeeShopNSFW" not in names and names.count("specialtycoffee") == 1
    scores = {s["name"]: s["confidence"] for s in data["subreddits"]}
    # specialty coffee shop: 2 of 3 terms, <10k members x0.9.
    assert scores["specialtycoffee"] == round(2 / 3 * 0.9, 3)
    # pics: 2 of 3 x0.3 x1.1 for a million members.
    assert scores["pics"] == round(2 / 3 * 0.3 * 1.1, 3)
    assert scores["Coffee"] == round(1 / 3 * 1.1, 3)
    # askportland: 1 of 4 terms in the city query.
    assert scores["askportland"] == 0.25
    assert names == ["specialtycoffee", "Coffee", "askportland", "pics"]
    assert sum(data["tier_distribution"].values()) == len(names)
    assert data["subreddits"][0]["url"] == "https://www.reddit.com/r/specialtycoffee/"


def test_ties_sort_by_subscribers_and_keep_ten():
    children = [sub(f"coffee{n}", 1_000 * (n + 10), "specialty coffee shop") for n in range(14)]
    section = cd.produce(CAFE, session=signed(Resp({"data": {"children": children}}),
                                              Resp({"data": {"children": []}})), env=REDDIT_ENV)
    subs = section["data"]["subreddits"]
    assert len(subs) == 10 and subs[0]["name"] == "coffee13"
    assert [s["subscribers"] for s in subs] == sorted((s["subscribers"] for s in subs), reverse=True)


def test_network_failure_is_partial_not_an_error():
    session = Session(ConnectionError("down"))
    section = checked(cd, cd.produce(CAFE, session=session, env=REDDIT_ENV))
    assert section["research_status"] == "partial" and section["data"]["subreddits"] == []
    assert any("failed" in u for u in section["uncertainties"])
    assert len(session.calls) == 1       # a failed sign-in makes no searches


def test_without_credentials_reddit_is_not_called():
    session = Session()
    section = checked(cd, cd.produce(CAFE, session=session, env={}))
    assert session.calls == [] and section["research_status"] == "partial"
    assert cd.NO_CREDENTIALS in section["uncertainties"]


def test_nothing_matched_is_partial_with_empty_list():
    session = signed(Resp({"data": {"children": []}}), Resp({"data": {"children": []}}))
    section = checked(cd, cd.produce(CAFE, session=session, env=REDDIT_ENV))
    assert section["research_status"] == "partial" and section["data"]["quality"] == "poor"


def test_community_render_escapes_and_validate_rejects():
    data = {"queries": ["q"], "quality": "poor", "tier_distribution": {"exact": 1},
            "subreddits": [{"name": "x", "subscribers": 1, "confidence": 0.9,
                            "match_tier": "exact", "url": "u", "title": "<img src=x>"}]}
    assert "<img" not in cd.render_data(data)
    data["subreddits"][0]["match_tier"] = "adjacent"
    with pytest.raises(ValueError):
        cd.validate_data(data)


# --------------------------------------------------------------------------- sizing
def fig(kind, value, url, unit="USD", retrieved=FRESH, **extra):
    return {"name": f"{kind} from {url}", "kind": kind, "value": value, "unit": unit,
            "geography": "US", "year": 2025, "source_url": url, "source_title": url,
            "retrieved_at": retrieved, **extra}


def market(*figures):
    return {"title": "Market size", "narrative": "n", "findings": [], "assumptions": [],
            "uncertainties": [], "research_status": "completed",
            "metrics": {"figures": list(figures)}}


def places(count):
    return {"data": {"available": True, "count": count}}


FIGURES = (fig("market_value", 4e9, "https://www.statista.com/a"),
           fig("market_value", 6e9, "https://ibisworld.com/b"),
           fig("customer_count", 10_000, "https://census.gov/c", unit="clinics"),
           fig("price", 40, "https://a.com/p", unit="USD/month"),
           fig("price", 60, "https://b.co.uk/p", unit="USD per month"))


def size(*figures, facts=None, attrs=None, **upstream):
    ctx = context(attrs or {}, facts if facts is not None else
                  {"business_model": "Subscription: $150 per month."},
                  market_size=market(*figures), **upstream)
    return checked(sizing, sizing.produce(ctx, now=NOW))


def test_three_methods_against_hand_numbers():
    data = size(*FIGURES, competitor_count=places(10))["data"]
    m = data["methods"]
    # som share: pre_launch 0.02 x 1/(1 + 10/10) x 1.0 = 0.01
    assert data["som_share"]["value"] == 0.01
    # top-down: median(4e9, 6e9) = 5e9; SAM 30% = 1.5e9; SOM 1% = 1.5e7
    assert [m["top_down"][k]["value"] for k in ("tam", "sam", "som")] == [5e9, 1.5e9, 1.5e7]
    # bottom-up: 10,000 x ($150 x 12 = $1,800) = 18e6; SAM 5.4e6; SOM 54,000
    assert [m["bottom_up"][k]["value"] for k in ("tam", "sam", "som")] == [18e6, 5.4e6, 54_000]
    # value theory: comparable median($480, $720) = $600; factor 1800/600 = 3 -> 2.0
    vt = m["value_theory"]["tam"]
    assert vt["inputs"]["comparable_price"] == 600 and vt["inputs"]["positioning_factor"] == 2.0
    assert vt["value"] == 12e6
    assert data["anchor_method"] == "bottom_up"
    assert sizing.DEFAULT_SHARE_WARNING in data["warnings"]


def test_sourced_share_and_no_founder_price():
    share = fig("serviceable_share", 25, "https://share.org/s", unit="%")
    data = size(*FIGURES, share, facts={}, competitor_count=places(10))["data"]
    bu = data["methods"]["bottom_up"]
    # ARPU falls back to the $600 comparable: 10,000 x 600 = 6e6; SAM 25% = 1.5e6
    assert bu["tam"]["value"] == 6e6 and bu["sam"]["value"] == 1.5e6
    assert data["methods"]["value_theory"]["tam"]["inputs"]["positioning_factor"] == 1.0
    assert sizing.DEFAULT_SHARE_WARNING not in data["warnings"]


def test_som_share_clamps_both_ends():
    # 0.02 / (1 + 20/10) x 0.6 = 0.004 -> 0.005
    assert sizing.estimate_som_share(20, "pre_launch", "high") == 0.005
    # The largest reachable share with the spec's constants is 0.10 x 1.25 = 0.125, so the
    # 0.25 ceiling only shows on the clamp itself.
    assert sizing.estimate_som_share(0, "scaling", "low") == 0.125
    assert sizing.clamp(0.4, 0.005, 0.25) == 0.25
    with pytest.raises(ValueError):
        sizing.estimate_som_share(-1, "scaling", "low")


@pytest.mark.parametrize("text,stage", [("Pre-launch idea", "pre_launch"),
                                        ("Piloting with three clinics", "early_customers"),
                                        ("Scaling to a second site", "scaling"), (None, "pre_launch")])
def test_stage_keywords(text, stage):
    assert sizing.stage_of(text) == stage


def web(url, when=FRESH):
    return {"url": url, "title": "t", "retrieved_at": when, "source_type": "web"}


def test_sizing_confidence_rules():
    founder = {"url": "", "title": "f", "retrieved_at": None, "source_type": "founder"}
    assert sizing.judge(1.0, [web("https://a.com"), web("https://news.b.co.uk")], NOW) == ("high", True)
    assert sizing.judge(1.0, [web("https://a.com"), web("https://www.a.com/x")], NOW) == ("medium", True)
    assert sizing.judge(1.0, [web("https://a.com"), web("https://b.com", STALE)], NOW) == ("medium", True)
    assert sizing.judge(1.0, [web("https://a.com"), web("https://b.com", None)], NOW) == ("medium", True)
    assert sizing.judge(1.0, [founder], NOW) == ("low", False)
    assert sizing.judge(1.0, [web("https://a.com"), founder, web("https://b.com")], NOW) == ("high", True)
    assert sizing.judge(1.0, [], NOW) == ("low", False)
    assert sizing.judge(None, [], NOW) == (None, False)


def test_section_confidence_and_stale_citation():
    data = size(*FIGURES, competitor_count=places(0))["data"]
    assert data["methods"]["top_down"]["tam"]["confidence"] == "high"
    stale = (fig("market_value", 4e9, "https://statista.com/a", retrieved=STALE),) + FIGURES[1:]
    assert size(*stale)["data"]["methods"]["top_down"]["tam"]["confidence"] == "medium"


def test_missing_inputs_are_named_and_anchor_falls_back():
    data = size(FIGURES[0], facts={})["data"]
    bu = data["methods"]["bottom_up"]["tam"]
    assert bu["value"] is None
    assert bu["note"] == ("not computed: customer_count, annual price per customer not found "
                          "in any source")
    assert data["anchor_method"] == "top_down"
    assert any("competitor count" in w for w in data["warnings"])


def test_nothing_computable_is_partial():
    section = size()
    assert section["research_status"] == "partial" and section["data"]["anchor_method"] is None
    assert section["narrative"].startswith("No market size could be computed")
    assert any("market_value" in u for u in section["uncertainties"])


def test_bad_figures_are_skipped_with_warnings():
    bad = [fig("market_value", -1, "https://a.com"), fig("market_value", 5, "not a url"),
           fig("market_value", 9e9, "https://eu.com", unit="EUR"),
           fig("price", 30, "https://p.com", unit="USD"), {"kind": "mystery"}, "junk"]
    data = size(*bad)["data"]
    assert data["anchor_method"] is None and len(data["warnings"]) >= 6


def test_unit_scale_and_pricing_section_price():
    pricing = {"data": {"category_median_monthly": 50.0, "currency": "USD"}}
    data = size(fig("market_value", 2, "https://a.com", unit="USD billion"),
                fig("customer_count", 1, "https://b.com", unit="thousand clinics"),
                facts={}, pricing=pricing)["data"]
    assert data["methods"]["top_down"]["tam"]["value"] == 2e9
    # 1,000 customers x ($50 x 12) from the pricing section
    bu = data["methods"]["bottom_up"]["tam"]
    assert bu["value"] == 600_000
    assert any(c["source_type"] == "section" for c in bu["citations"])


def test_inferred_price_and_unit_economics_are_not_the_founders_price():
    ctx = context({"pricing": "$20 per month"}, {"unit_economics": "Costs $3 per user per month."})
    ctx["prep"]["venture"]["attributes"]["pricing"]["basis"] = "inferred"
    assert sizing.founder_price(ctx)[0] is None
    slot = context({"pricing": {"value": 10.0, "period": "month", "kind": "price",
                                "currency": "USD", "unit": "seat", "source": "form"}})
    assert sizing.founder_price(slot)[0] == 120.0
    assert sizing.founder_price(context({}, {"pricing": "$5 or $8 per month"}))[0] is None


def test_money_format_and_narrative():
    assert sizing.money(1_234_567) == "$1.2M" and sizing.money(450_000) == "$450K"
    assert sizing.money(2e9) == "$2B" and sizing.money(950) == "$950"
    narrative = size(*FIGURES, competitor_count=places(10))["narrative"]
    assert "bottom-up" in narrative and "$18M" in narrative and "TAM = customers x ARPU" in narrative


def test_sizing_validate_rejects_and_render_escapes():
    data = size(*FIGURES)["data"]
    html = sizing.render_data({**data, "warnings": ["<b>bold</b>"]})
    assert "<b>bold" not in html and "&lt;b&gt;" in html
    broken = {**data, "methods": {k: v for k, v in data["methods"].items() if k != "top_down"}}
    with pytest.raises(ValueError):
        sizing.validate_data(broken)
    bad_conf = {**data["methods"]["top_down"], "tam": {**data["methods"]["top_down"]["tam"],
                                                        "confidence": "certain"}}
    with pytest.raises(ValueError):
        sizing.validate_data({**data, "methods": {**data["methods"], "top_down": bad_conf}})


def test_no_competitor_count_leaves_som_uncomputed_rather_than_assuming_none():
    section = size(*FIGURES)
    data = section["data"]
    assert data["som_share"]["value"] is None
    anchored = data["methods"][data["anchor_method"]]
    assert anchored["tam"]["value"] is not None and anchored["sam"]["value"] is not None
    assert anchored["som"]["value"] is None
    assert "competitor count" in anchored["som"]["note"]
    assert "SOM not computed" in section["narrative"]
    counted = size(*FIGURES, competitor_count=places(4))["data"]
    assert counted["som_share"]["value"] is not None


@pytest.mark.parametrize("text,annual", [
    ("$5 per latte, about 300 a week", None), ("$40 per class, 2 classes a week", None),
    ("$39 per practitioner per month", None), ("$29/month", 348.0), ("$29 a month", 348.0),
    ("$49 monthly", 588.0), ("$1,200 per year", 1200.0)])
def test_a_founder_price_is_annualised_only_by_its_own_period(text, annual):
    assert sizing._price_from(text)[0] == annual


def test_an_empty_competitor_list_is_no_count():
    listed = {"metrics": {"competitors": []}}
    assert sizing.competitor_input(context(competitive_landscape=listed))[0] is None


def test_a_share_given_as_a_fraction_is_never_divided_again():
    figure, _ = sizing.read_figure(fig("serviceable_share", 0.35, "https://a.com/x",
                                       unit="percent"))
    assert figure["value"] == 0.35
    figure, _ = sizing.read_figure(fig("serviceable_share", 35, "https://a.com/x", unit="%"))
    assert figure["value"] == 0.35
