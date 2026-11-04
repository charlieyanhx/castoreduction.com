"""The three computed market sections: Places count, Reddit discovery and sizing.

Fully offline: every outside call goes to a fake session, and the numbers are checked
against values worked out by hand from docs/treadle/AGENT_REVAMP.md's formulas.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from packs.market_research_next.analysis import community_discovery as cd
from packs.market_research_next.analysis import competitor_count as cc
from packs.market_research_next.analysis import sizing
from packs.market_research_next.contracts import validate_section_output
from packs.market_research_next.open_tools import REDDIT_RSS
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


def KEEP_ALL(system, user, **kwargs):
    """A relevance screen that keeps every candidate."""
    import json
    return {"keep": [s["name"] for s in json.loads(user)["subreddits"]]}


def discover(*args, **kwargs):
    return cd.produce(*args, **{"model": KEEP_ALL, **kwargs})


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
    ("A meal-planning service for busy parents.", "meal planning service"),
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
        "places.websiteUri,places.location,nextPageToken")
    assert call["json"] == {"textQuery": "specialty coffee shop in NW 23rd Avenue, Portland, Oregon",
                            "pageSize": 20, "languageCode": "en"}
    data = section["data"]
    assert [(c["rank"], c["name"]) for c in data["competitors"]] == [
        (1, "Coava"), (2, "Barista"), (3, "Free Tea"), (4, "Odd")]
    assert [c["price_level"] for c in data["competitors"]] == [2, 1, 0, None]
    assert data["count"] == 4 and data["median_rating"] == 4.4
    assert data["median_price_level"] == 1.0
    assert data["price_level_counts"] == {"0": 1, "1": 1, "2": 1, "3": 0, "4": 0}
    assert section["research_status"] == "completed"


def test_pages_are_followed_and_a_stopped_search_is_a_floor():
    full = {"places": [place(f"Cafe {n}") for n in range(20)], "nextPageToken": "more"}
    last = {"places": [place(f"Late {n}") for n in range(5)]}
    session = Session(Resp(full), Resp(last))
    section = checked(cc, cc.produce(CAFE, session=session, env=KEY_ENV))
    assert [c["json"].get("pageToken") for c in session.calls] == [None, "more"]
    assert section["data"]["count"] == 25 and section["data"]["count_is_floor"] is False
    assert not any("at least" in u for u in section["uncertainties"])
    # Google still had more after the last page we may read: the count is a lower bound.
    session = Session(*[Resp(full) for _ in range(cc.MAX_PAGES)])
    section = checked(cc, cc.produce(CAFE, session=session, env=KEY_ENV))
    assert len(session.calls) == cc.MAX_PAGES
    data = section["data"]
    assert data["count_is_floor"] is True and section["metrics"]["count_is_floor"] is True
    assert f'at least {data["count"]}' in section["narrative"]


def test_only_places_of_the_same_trade_are_counted():
    payload = {"places": [
        place("Barista", primaryType="cafe", userRatingCount=700),
        place("Case Study", primaryType="coffee_shop", userRatingCount=185),
        place("The Meadow", primaryType="chocolate_shop", userRatingCount=122),
        place("Papa Haydn", primaryType="restaurant", userRatingCount=2298),
        place("St. Honore", primaryType="bakery", userRatingCount=1664),
        place("Sterling", primaryType="coffee_shop", userRatingCount=835)]}
    section = checked(cc, cc.produce(CAFE, session=Session(Resp(payload)), env=KEY_ENV))
    data = section["data"]
    assert [c["name"] for c in data["competitors"]] == ["Barista", "Case Study", "Sterling"]
    assert [c["rank"] for c in data["competitors"]] == [1, 2, 3]
    assert data["count"] == 3 and section["metrics"]["count"] == 3
    assert sorted(o["name"] for o in data["other_places"]) == ["Papa Haydn", "St. Honore",
                                                               "The Meadow"]
    assert "3 other places" in section["narrative"]
    assert "bakery" in section["narrative"] and "restaurant" in section["narrative"]
    # The findings keep Google's order among real competitors; review counts do not pick them.
    assert section["findings"][0].startswith("Barista")
    assert not any("Papa Haydn" in f for f in section["findings"])


def test_a_generic_store_type_is_not_the_trade_and_repeats_count_once():
    payload = {"places": [
        place("Barista", primaryType="cafe", formattedAddress="823 NW 23rd"),
        place("New Renaissance Bookshop", primaryType="store"),
        place("Barista", primaryType="cafe", formattedAddress="823 NW 23rd")]}
    data = cc.produce(CAFE, session=Session(Resp(payload)), env=KEY_ENV)["data"]
    assert [c["name"] for c in data["competitors"]] == ["Barista"]
    assert [o["name"] for o in data["other_places"]] == ["New Renaissance Bookshop"]


def test_places_far_from_the_search_area_are_not_counted():
    near = {"latitude": 45.5289, "longitude": -122.6985}       # NW 23rd and Glisan
    payload = {"places": [
        place("Barista", primaryType="cafe", location=near),
        place("Case Study", primaryType="coffee_shop",
              location={"latitude": 45.5335, "longitude": -122.6990}),   # ~0.5 km
        place("PDX Coffee Club - Hillsdale", primaryType="coffee_shop",
              location={"latitude": 45.4797, "longitude": -122.6912}),   # ~5.5 km
        place("No location", primaryType="coffee_shop")]}
    section = checked(cc, cc.produce(CAFE, session=Session(Resp(payload)), env=KEY_ENV))
    data = section["data"]
    assert [c["name"] for c in data["competitors"]] == ["Barista", "Case Study", "No location"]
    assert data["farther_away"] == 1
    assert "1 more" in section["narrative"] and "1.6 km" in section["narrative"]


def test_when_no_type_matches_the_category_every_place_counts():
    ctx = context({"product": "A dog grooming salon", "geography": "Austin, Texas"})
    payload = {"places": [place("Wags", primaryType="pet_care"),
                          place("Muddy Paws", primaryType="pet_store")]}
    section = checked(cc, cc.produce(ctx, session=Session(Resp(payload)), env=KEY_ENV))
    assert section["data"]["count"] == 2 and section["data"]["other_places"] == []


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
    data = {"available": True, "query": "q", "location": "x", "count": 1, "count_is_floor": False,
            "other_places": [],
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
    assert cd.build_queries(ctx) == ["coffee", "coffee portland"]
    session = signed(Resp({"data": {"children": []}}), Resp({"data": {"children": []}}))
    discover(ctx, session=session, env=REDDIT_ENV)
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
    section = checked(cd, discover(CAFE, session=signed(Resp(first), Resp(second)),
                                     env=REDDIT_ENV))
    data = section["data"]
    names = [s["name"] for s in data["subreddits"]]
    assert "CoffeeShopNSFW" not in names and names.count("specialtycoffee") == 1
    scores = {s["name"]: s["confidence"] for s in data["subreddits"]}
    # The query is "coffee": "specialty" and "shop" are generic retail words, not terms.
    # specialtycoffee: 1 of 1, <10k members x0.9.
    assert scores["specialtycoffee"] == 0.9
    # pics: 1 of 1 x0.3 generic x1.1 for a million members.
    assert scores["pics"] == round(0.3 * 1.1, 3)
    assert scores["Coffee"] == 1.0          # 1 of 1 x1.1, capped at 1
    # askportland: 1 of 2 terms in the city query.
    assert scores["askportland"] == 0.5
    assert names == ["Coffee", "specialtycoffee", "askportland", "pics"]
    assert sum(data["tier_distribution"].values()) == len(names)
    assert data["subreddits"][0]["url"] == "https://www.reddit.com/r/Coffee/"


def test_a_model_screen_drops_lookalike_communities():
    first = {"data": {"children": [
        sub("Coffee", 2_000_000, "Coffee lovers"),
        sub("coffeemeetsbagel", 22_000, "Discussion and tips related to CMB",
            "coffee meets bagel dating app"),
        sub("shoptitans", 27_000, "Shop Titans", "a coffee shop simulation game")]}}
    seen = []

    def screen(system, user, **kwargs):
        import json
        seen.append(json.loads(user))
        return {"keep": ["Coffee", "NotACandidate"]}

    section = checked(cd, cd.produce(CAFE, session=signed(Resp(first),
                                                          Resp({"data": {"children": []}})),
                                     env=REDDIT_ENV, model=screen))
    assert [s["name"] for s in section["data"]["subreddits"]] == ["Coffee"]
    assert section["data"]["screened"] is True
    assert seen[0]["venture"]["category"] == "specialty coffee shop"
    assert {s["name"] for s in seen[0]["subreddits"]} == {"Coffee", "coffeemeetsbagel",
                                                          "shoptitans"}


def test_a_failed_screen_keeps_the_ranked_list_and_says_so():
    def broken(system, user, **kwargs):
        raise RuntimeError("provider down")

    first = {"data": {"children": [sub("Coffee", 2_000_000, "Coffee lovers")]}}
    section = checked(cd, cd.produce(CAFE, session=signed(Resp(first),
                                                          Resp({"data": {"children": []}})),
                                     env=REDDIT_ENV, model=broken))
    assert [s["name"] for s in section["data"]["subreddits"]] == ["Coffee"]
    assert section["data"]["screened"] is False
    assert any("Relevance could not be checked" in u for u in section["uncertainties"])


def test_ties_sort_by_subscribers_and_keep_ten():
    children = [sub(f"coffee{n}", 1_000 * (n + 10), "specialty coffee shop") for n in range(14)]
    section = discover(CAFE, session=signed(Resp({"data": {"children": children}}),
                                              Resp({"data": {"children": []}})), env=REDDIT_ENV)
    subs = section["data"]["subreddits"]
    assert len(subs) == 10 and subs[0]["name"] == "coffee13"
    assert [s["subscribers"] for s in subs] == sorted((s["subscribers"] for s in subs), reverse=True)


def test_network_failure_is_partial_not_an_error():
    session = Session(ConnectionError("down"))
    section = checked(cd, discover(CAFE, session=session, env=REDDIT_ENV))
    assert section["research_status"] == "partial" and section["data"]["subreddits"] == []
    assert any("failed" in u for u in section["uncertainties"])
    assert len(session.calls) == 1       # a failed sign-in makes no searches


class Archives:
    """The two open archives, answered by URL and the parameter each call varies."""

    def __init__(self, prefixes=None, posts=None, exact=None, fail=False):
        self.prefixes, self.posts, self.exact = prefixes or {}, posts or {}, exact or {}
        self.fail, self.calls = fail, []

    def request(self, method, url, headers=None, params=None, json=None, timeout=None,
                data=None, auth=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "params": params})
        if self.fail:
            raise ConnectionError("down")
        if url == cd.ARCHIVE_POSTS:
            names = self.posts.get(params["q"], [])
            return Resp({"data": [{"subreddit": n, "title": "a post"} for n in names]})
        if "subreddit_prefix" in params:
            return Resp({"data": self.prefixes.get(params["subreddit_prefix"], [])})
        return Resp({"data": self.exact.get(params["subreddit"], [])})


def meta(name, subscribers, title="", description="", over18=False):
    return sub(name, subscribers, title, description, over18)["data"]


def test_without_credentials_the_open_archives_answer():
    ctx = context({"product": "A specialty coffee shop on NW 23rd Avenue",
                   "geography": "NW 23rd Avenue, Portland, Oregon"},
                  description="Zebra quantum flamingo espresso bar for laptops.")
    archives = Archives(
        prefixes={"coffee": [meta("Coffee", 2_000_000, "Coffee lovers"),
                             meta("CoffeeNSFW", 90_000, "coffee", over18=True)],
                  "portland": [meta("Portland", 600_000, "Portland, Oregon")]},
        posts={"coffee portland": ["askportland", "askportland", "nottheonion"]},
        exact={"askportland": [meta("askportland", 80_000, "Portland questions",
                                    "coffee shop and neighbourhood advice")]})
    section = checked(cd, discover(ctx, session=archives, env={}))
    urls = {c["url"] for c in archives.calls}
    assert cd.TOKEN_URL not in urls and cd.URL not in urls
    # The live feed is tried first; this fake cannot answer it, so the archive backs it up.
    assert urls == {REDDIT_RSS, cd.ARCHIVE_SUBREDDITS, cd.ARCHIVE_POSTS}
    sent = " ".join(str(v) for c in archives.calls for v in c["params"].values()).lower()
    assert not {"zebra", "quantum", "flamingo", "laptops"} & set(sent.split())
    assert all(c["headers"]["User-Agent"] == cd.USER_AGENT for c in archives.calls)
    data = section["data"]
    names = [s["name"] for s in data["subreddits"]]
    assert "CoffeeNSFW" not in names
    assert "askportland" in names            # seen in two matching posts, then looked up
    assert "nottheonion" not in names        # one stray post is noise, never looked up
    assert not any(c["params"].get("subreddit") == "nottheonion" for c in archives.calls)
    assert data["source"] == "open_archive" and section["research_status"] == "completed"
    assert "archive" in section["narrative"].lower()


def test_the_live_feed_answers_and_the_post_archive_is_not_called():
    ctx = context({"product": "A specialty coffee shop on NW 23rd Avenue",
                   "geography": "NW 23rd Avenue, Portland, Oregon"})

    class Feed(Archives):
        def request(self, method, url, **kwargs):
            if url == REDDIT_RSS:
                self.calls.append({"method": method, "url": url, **kwargs})
                entries = "".join(
                    f'<entry><id>t3_{i}</id><category term="askportland"/><title>t</title></entry>'
                    for i in range(3))
                return SimpleNamespace(status_code=200, headers={}, text=(
                    f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'))
            return super().request(method, url, **kwargs)

    feed = Feed(exact={"askportland": [meta("askportland", 80_000, "Portland questions",
                                            "coffee shop and neighbourhood advice")]})
    section = checked(cd, discover(ctx, session=feed, env={}))
    assert cd.ARCHIVE_POSTS not in {c["url"] for c in feed.calls}
    assert "askportland" in [s["name"] for s in section["data"]["subreddits"]]


def test_archive_keeps_real_matches_only():
    ctx = context({"product": "A dog grooming salon", "geography": "Austin, Texas"})
    archives = Archives(prefixes={"dog": [
        meta("doggrooming", 77_000, "Dog Grooming", "for dog groomers"),
        meta("dogecoin", 2_500_000, "Dogecoin", "the cryptocurrency"),
        meta("DogGroomersItaly", 180, "dog grooming", "dog groomers in Italy")]})
    names = [s["name"] for s in discover(ctx, session=archives, env={})["data"]["subreddits"]]
    assert names == ["doggrooming"]   # a shared prefix is not a match; 180 members is too few


def test_archive_search_fits_one_section_tool_budget():
    # Live 2026-10-30: ten archive calls against a per-section allowance of eight stopped
    # the whole run with call_budget. The worst case must stay under the allowance.
    from treadle.execution_limits import ExecutionLimits

    ctx = context({"product": "Artisan sourdough bakery cafe with wine bar",
                   "geography": "Hawthorne Boulevard, Portland, Oregon"})
    many = [f"sub{n}" for n in range(12) for _ in range(3)]
    archives = Archives(posts={q: many for q in cd.build_queries(ctx)},
                        exact={f"sub{n}": [meta(f"sub{n}", 50_000, "bakery")] for n in range(12)})
    discover(ctx, session=archives, env={})
    assert len(archives.calls) <= cd.ARCHIVE_CALLS < ExecutionLimits().task_tool_calls
    assert any(c["url"] == cd.ARCHIVE_POSTS for c in archives.calls)


def test_each_word_is_looked_up_once_across_queries():
    archives = Archives()
    discover(CAFE, session=archives, env={})
    words = [c["params"]["subreddit_prefix"] for c in archives.calls
             if "subreddit_prefix" in (c["params"] or {})]
    assert sorted(words) == sorted(set(words))
    assert len([c for c in archives.calls if c["url"] == cd.ARCHIVE_POSTS]) == 2


def test_archive_failure_is_partial_not_an_error():
    archives = Archives(fail=True)
    section = checked(cd, discover(CAFE, session=archives, env={}))
    assert section["research_status"] == "partial" and section["data"]["subreddits"] == []
    assert any("archive" in u.lower() for u in section["uncertainties"])


def test_with_credentials_the_archives_are_not_used():
    session = signed(Resp({"data": {"children": [sub("Coffee", 2_000_000, "coffee")]}}),
                     Resp({"data": {"children": []}}))
    section = discover(CAFE, session=session, env=REDDIT_ENV)
    assert {c["url"] for c in session.calls} == {cd.TOKEN_URL, cd.URL}
    assert section["data"]["source"] == "reddit"


def test_nothing_matched_is_partial_with_empty_list():
    session = signed(Resp({"data": {"children": []}}), Resp({"data": {"children": []}}))
    section = checked(cd, discover(CAFE, session=session, env=REDDIT_ENV))
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
    assert bu["note"] == ("Not computed: no source gave a count of potential customers or a "
                          "yearly spend per customer in this category.")
    assert data["anchor_method"] == "top_down"
    assert any("competitor count" in w for w in data["warnings"])


def test_nothing_computable_is_partial():
    section = size()
    assert section["research_status"] == "partial" and section["data"]["anchor_method"] is None
    assert section["narrative"].startswith("No market size is computed")
    assert any("total yearly spend in this market" in u for u in section["uncertainties"])
    assert not any("market_value" in u or "not found in any source" in u
                   for u in section["uncertainties"])


def test_what_was_found_is_said_when_nothing_can_be_computed():
    households = fig("customer_count", 14533, "https://data.census.gov/", unit="households")
    section = size(households, facts={})
    assert section["data"]["anchor_method"] is None
    text = section["narrative"]
    assert "14,533" in text and "yearly spend per customer in this category" in text
    assert "rather than estimated" in text


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


@pytest.mark.parametrize("product,category", [
    ("A platform connecting dog owners with local dog walkers, featuring online booking.",
     "dog walkers"),
    ("An app that matches parents with nearby babysitters.", "babysitters"),
    ("A specialty coffee shop on NW 23rd Avenue.", "specialty coffee shop")])
def test_the_search_names_the_trade_not_the_app(product, category):
    assert cc.category_phrase(context({"product": product})) == category


@pytest.mark.parametrize("text,place", [
    ("Launching in the Annex and Little Italy neighbourhoods of Toronto",
     "the Annex and Little Italy neighbourhoods of Toronto"),
    ("Only Chicago's Lincoln Park neighbourhood.", "Chicago's Lincoln Park neighbourhood"),
    ("Based in Portland, Oregon", "Portland, Oregon"),
    ("Initially serving Austin; later Dallas", "Austin"),
    ("Portland, Oregon", "Portland, Oregon")])
def test_the_search_names_the_place_not_the_sentence(text, place):
    assert cc.location_phrase(context({"geography": text})) == place


def test_a_park_or_the_place_itself_is_not_a_competitor():
    places = [{"name": "City Town Canine", "category": "pet_care"},
              {"name": "Grace - College Parkette", "category": "park"},
              {"name": "Little Italy", "category": None}]
    kept = cc.businesses(places, "the Annex and Little Italy of Toronto")
    assert [p["name"] for p in kept] == ["City Town Canine"]


def test_a_kind_copied_with_its_explanation_is_still_that_kind():
    # Vague, 2026-11-03: the writer copied the contract's whole description into "kind".
    copied = fig("market_value (total yearly spend in this market)", 5e9, "https://a.com/x")
    data = size(copied, facts={}, competitor_count=places(10))["data"]
    assert data["methods"]["top_down"]["tam"]["value"] == 5e9


def test_competing_businesses_are_not_customers():
    # Cafe, 2026-11-03: 76 competing establishments outranked 15,062 households.
    rivals = fig("customer_count", 76, "https://osm.org/x", unit="establishments",
                 name="Competing food establishments")
    homes = fig("customer_count", 15062, "https://census.gov/h", unit="households")
    data = size(rivals, homes, facts={"business_model": "$10 per month"},
                competitor_count=places(10))["data"]
    assert data["methods"]["bottom_up"]["tam"]["inputs"]["customers"] == 15062


def test_a_rate_is_never_reported_as_zero():
    # Vague, 2026-11-03: 0.141 printed as "0", and five forces read that as no growth.
    growth = fig("growth_rate", 0.141, "https://a.com/g", unit="fraction", name="Market growth")
    homes = fig("customer_count", 100, "https://census.gov/h", unit="households")
    section = size(growth, homes, facts={})
    assert "Market growth: 0 " not in section["narrative"]
    assert "Market growth: 14.1%" in section["narrative"]


@pytest.mark.parametrize("product,category", [
    ("Meal-planning service offered via a $20/month app subscription.", "Meal planning service"),
    ("Tutoring delivered through local libraries.", "Tutoring")])
def test_the_search_stops_before_how_the_service_is_sold(product, category):
    assert cc.category_phrase(context({"product": product})) == category


@pytest.mark.parametrize("product", [
    "An app where dog owners book local walkers. We take a cut.",
    "A platform/app for booking local dog walkers online for in-person walks",
    "A platform connecting dog owners with local dog walkers, featuring online booking."])
def test_rewordings_of_one_business_search_for_the_same_trade(product):
    # PREP rewords the product each run, and the query moved with it: 7 places, then 1.
    assert cc.category_phrase(context({"product": product})) == "dog walkers"


def test_a_figure_from_the_trade_area_outranks_the_writers_choice_of_place():
    county = fig("customer_count", 343_370, "https://census.gov/c", unit="households",
                 geography="Multnomah County")
    area = fig("customer_count", 15_062, "https://data.census.gov/", unit="households",
               geography="1.5 km around NW 23rd Avenue", trade_area=True)
    data = size(county, area, facts={"business_model": "$10 per month"},
                competitor_count=places(10))["data"]
    assert data["methods"]["bottom_up"]["tam"]["inputs"]["customers"] == 15_062
    assert not any("different places" in w for w in data["warnings"])


def test_trade_area_spend_gives_the_ceiling():
    area = fig("customer_count", 100, "https://data.census.gov/", unit="households", trade_area=True)
    spend = fig("broader_spend", 4000, "https://www.bls.gov/cex/", unit="USD per household per year",
                name="All food away from home", trade_area=True)
    other = fig("broader_spend", 2000, "https://www.bls.gov/cex/", unit="USD per household per year",
                name="Apparel")
    data = size(area, spend, other, facts={})["data"]
    assert data["ceiling"]["value"] == 400_000 and data["ceiling"]["category"] == "All food away from home"


def test_a_count_left_as_other_is_still_a_count():
    # Cafe, 2026-11-03: households were labelled "other", so sizing saw no customers.
    other = fig("other", 2448, "https://census.gov/h", unit="households")
    income = fig("other", 69524, "https://census.gov/i", unit="USD per household")
    data = size(other, income, facts={"business_model": "$10 per month"},
                competitor_count=places(10))["data"]
    assert data["methods"]["bottom_up"]["tam"]["inputs"]["customers"] == 2448


@pytest.mark.parametrize("name,year", [("Projected global market size", 2025),
                                       ("Global market forecast", 2025), ("Global market", 2031)])
def test_a_forecast_is_not_todays_market(name, year):
    # Vague brief, 2026-11-03: the median of today's and 2030's market gave a $110.5B TAM.
    now = fig("market_value", 60e9, "https://a.com/x", name="Global market size")
    later = fig("market_value", 150e9, "https://b.com/x", name=name, year=year)
    data = size(now, later, facts={}, competitor_count=places(10))["data"]
    assert data["methods"]["top_down"]["tam"]["value"] == 60e9
    assert any("forecast" in w for w in data["warnings"])


def test_a_top_down_share_is_never_presented_as_a_forecast():
    section = size(FIGURES[0], facts={}, competitor_count=places(10))
    assert any("not a revenue forecast" in w for w in section["data"]["warnings"])
    assert "not a revenue forecast" in section["narrative"]


def test_bls_spend_per_consumer_unit_matches_a_household_count():
    spend = fig("broader_spend", 4142, "https://www.bls.gov/cex/", unit="USD per year",
                name="All food away from home spending per consumer unit")
    data = size(fig("customer_count", 100, "https://census.gov/h", unit="households"),
                spend, facts={})["data"]
    assert data["ceiling"]["value"] == 414_200


def test_the_search_ignores_a_slash_between_container_words():
    product = "A platform/app connecting dog owners with local dog walkers, featuring booking."
    assert cc.category_phrase(context({"product": product})) == "dog walkers"


def test_a_number_returned_as_text_is_still_the_number():
    # B2B, 2026-11-03: "50883" was skipped and the report said no source gave a count.
    data = size(fig("customer_count", "50,883", "https://census.gov/c", unit="establishments"),
                facts={"business_model": "$10 per month"}, competitor_count=places(10))["data"]
    assert data["methods"]["bottom_up"]["tam"]["value"] == 50_883 * 120


@pytest.mark.parametrize("text", ["about 5k", "many", "", "1e400"])
def test_text_that_is_not_a_plain_number_is_still_skipped(text):
    data = size(fig("customer_count", text, "https://census.gov/c"))["data"]
    assert any("no positive number" in w for w in data["warnings"])


def test_counts_in_different_units_are_never_averaged():
    # Cafe, 2026-11-03: the median of 2,448 households and 4,108 residents counts nothing.
    data = size(fig("customer_count", 2448, "https://census.gov/h", unit="households"),
                fig("customer_count", 4108, "https://census.gov/r", unit="residents"),
                facts={"business_model": "$10 per month"}, competitor_count=places(10))["data"]
    assert data["methods"]["bottom_up"]["tam"]["inputs"]["customers"] == 2448


def test_counts_for_different_places_use_the_widest_and_say_so():
    tract = fig("customer_count", 1438, "https://census.gov/t", unit="households",
                geography="Census Tract 8325")
    county = fig("customer_count", 2_066_248, "https://census.gov/c", unit="households",
                 geography="Cook County")
    data = size(tract, county, facts={"business_model": "$10 per month"},
                competitor_count=places(10))["data"]
    bu = data["methods"]["bottom_up"]["tam"]
    assert bu["inputs"]["customers"] == 2_066_248
    assert [c["url"] for c in bu["citations"] if c["source_type"] == "web"] == ["https://census.gov/c"]
    assert any("Census Tract 8325" in w and "Cook County" in w for w in data["warnings"])


def test_a_count_of_businesses_is_preferred_over_people_for_a_business_price():
    people = fig("customer_count", 9_000_000, "https://census.gov/p", unit="residents")
    clinics = fig("customer_count", 50_883, "https://census.gov/c", unit="establishments")
    data = size(people, clinics, facts={"business_model": "$10 per clinic per month"},
                competitor_count=places(10))["data"]
    assert data["methods"]["bottom_up"]["tam"]["inputs"]["customers"] == 50_883


@pytest.mark.parametrize("text,annual", [("$39 per practitioner per month", 468),
                                         ("$39 a seat a month", 468), ("$120 per clinic per year", 120)])
def test_a_price_per_unit_per_period_is_annualized(text, annual):
    assert sizing.founder_price(context({}, {"pricing": text}))[0] == annual


def test_a_per_item_price_and_a_volume_are_still_not_a_yearly_price():
    assert sizing.founder_price(context({}, {"pricing": "$5 per latte, about 300 a week"}))[0] is None


def test_a_count_and_price_in_different_units_are_a_labelled_floor():
    # 50,883 clinics at a per-practitioner price is one practitioner per clinic: a floor.
    data = size(fig("customer_count", 50_883, "https://census.gov/c", unit="establishments"),
                facts={"pricing": "$39 per practitioner per month"},
                competitor_count=places(10))["data"]
    assert data["methods"]["bottom_up"]["tam"]["value"] == 50_883 * 468
    assert any("per practitioner" in w and "floor" in w and "establishments" in w
               for w in data["warnings"])


def test_matching_units_carry_no_floor_warning():
    data = size(fig("customer_count", 500, "https://census.gov/c", unit="clinics"),
                facts={"pricing": "$39 per clinic per month"}, competitor_count=places(10))["data"]
    assert not any("floor" in w for w in data["warnings"])


def test_tam_says_it_counts_everyone_not_only_buyers():
    section = size(fig("customer_count", 2448, "https://census.gov/h", unit="households"),
                   facts={"business_model": "$10 per month"}, competitor_count=places(10))
    assert any("every household" in a for a in section["assumptions"])


def _spend(value=3699, **extra):
    return fig("other", value, "https://www.bls.gov/cex/", unit="USD per household per year",
               **{"name": "Household food away from home spend", **extra})


def test_spend_on_a_wider_category_gives_a_ceiling_and_never_a_tam():
    # Cafe, 2026-11-03: households and restaurant spend were both found, labelled "other".
    section = size(fig("customer_count", 2448, "https://census.gov/h", unit="households",
                       geography="Census Tract 47"), _spend(), facts={})
    data = section["data"]
    assert data["anchor_method"] is None and section["metrics"]["tam"] is None
    assert data["ceiling"]["value"] == 2448 * 3699
    assert section["metrics"]["ceiling"] == 2448 * 3699
    assert section["research_status"] == "partial"
    text = section["narrative"]
    assert text.startswith("No market size is computed")
    assert "$9.1M" in text and "upper limit" in text and "food away from home" in text
    assert "$9.1M" in sizing.render_data(data)


def test_a_ceiling_needs_matching_units():
    data = size(fig("customer_count", 4108, "https://census.gov/r", unit="residents"),
                _spend(), facts={})["data"]
    assert data["ceiling"] is None


def test_no_ceiling_when_a_market_size_exists():
    data = size(*FIGURES, _spend(), competitor_count=places(10))["data"]
    assert data["anchor_method"] and data["ceiling"] is None


@pytest.mark.parametrize("name,unit", [("Median household income", "USD per year"),
                                       ("Household spend", "USD"),
                                       ("Tract income per household", "USD per household per year")])
def test_other_figures_that_are_not_spend_stay_out_of_the_ceiling(name, unit):
    other = fig("other", 69524, "https://census.gov/i", unit=unit, name=name)
    data = size(fig("customer_count", 2448, "https://census.gov/h", unit="households"),
                other, facts={})["data"]
    assert data["ceiling"] is None


def test_a_labelled_wider_spend_figure_counts():
    wider = fig("broader_spend", 3000, "https://www.bls.gov/cex/",
                unit="USD per household per year", name="Food away spend")
    data = size(fig("customer_count", 100, "https://census.gov/h", unit="households"),
                wider, facts={})["data"]
    assert data["ceiling"]["value"] == 300_000


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
    ("$39 per practitioner per month", 468.0), ("$29/month", 348.0), ("$29 a month", 348.0),
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


# --- the same brief twice: figures that must not move with the model's wording
def _market_figure(value, year, url, kind="market_value", name="Market", unit="USD per year"):
    return {"kind": kind, "name": name, "value": value, "unit": unit, "year": year,
            "geography": "Global", "source_url": url, "source_title": "Analyst"}


def _sized(figures):
    market = {"metrics": {"figures": figures}, "findings": [], "narrative": ""}
    return sizing.produce({"market_size": market, "prep": {"venture": {"attributes": {}}}})


def test_a_spend_named_after_restaurants_is_still_a_household_spend():
    spend = {"kind": "broader_spend", "name": "All food away from home (restaurants, cafes)",
             "value": 4142.0, "unit": "USD per household per year", "geography": "United States",
             "citation": {}, "trade_area": True}
    homes = {"kind": "customer_count", "name": "Households", "value": 27995.0,
             "unit": "households", "geography": "1.5 km", "citation": {}, "trade_area": True}
    assert sizing.ceiling([spend, homes], [homes])["value"] == round(27995.0 * 4142.0, 2)


def test_a_whole_market_filed_as_broader_spend_is_a_market_value():
    figure, _ = sizing.read_figure(_market_figure(59.9e9, 2023, "https://a.example/r",
                                                  kind="broader_spend"))
    assert figure["kind"] == "market_value"
    per_clinic, _ = sizing.read_figure(_market_figure(9000, 2023, "https://a.example/r",
                                                      kind="broader_spend",
                                                      unit="USD per clinic per year"))
    assert per_clinic["kind"] == "broader_spend"


def test_one_source_counts_once_at_its_latest_year():
    data = _sized([_market_figure(59.9e9, 2023, "https://a.example/r"),
                   _market_figure(89.1e9, 2026, "https://a.example/r")])["data"]
    assert data["methods"]["top_down"]["tam"]["value"] == 89.1e9


def test_sources_that_disagree_are_called_a_range():
    data = _sized([_market_figure(9.6e9, 2024, "https://a.example/r"),
                   _market_figure(59.9e9, 2024, "https://b.example/r")])["data"]
    assert any("$9.6B" in w and "$59.9B" in w for w in data["warnings"])
    close = _sized([_market_figure(50e9, 2024, "https://a.example/r"),
                    _market_figure(60e9, 2024, "https://b.example/r")])["data"]
    assert not any("disagree" in w for w in close["warnings"])


def test_a_hyphen_does_not_change_the_search():
    assert (cc.category_phrase(context({"product": "meal-planning service"}))
            == cc.category_phrase(context({"product": "meal planning service"})))


def test_the_founder_first_market_is_counted_before_a_later_one():
    # B2B, 2026-11-04: Canadian practitioners beat US clinics because the price is per
    # practitioner, though the founder said the United States comes first.
    us = {"kind": "customer_count", "name": "Clinics", "value": 50883.0,
          "unit": "establishments", "geography": "United States", "citation": {},
          "trade_area": False}
    canada = {**us, "name": "Physios", "value": 32267.0, "unit": "physiotherapists",
              "geography": "Canada"}
    counted, _ = sizing.customer_base([us, canada], "seats", "United States first, then Canada.")
    assert counted == [us]
    assert sizing.customer_base([us, canada], "seats", None)[0] == [canada]


@pytest.mark.parametrize("name, unit", [
    ("Average annual household spend on coffee", "USD per year"),
    ("Spend per daycare child", "USD per year"),
    ("Coffee market spend per-household", "USD per year"),
])
def test_one_customers_spend_stays_a_spend(name, unit):
    figure, _ = sizing.read_figure(_market_figure(4000, 2024, "https://a.example/r",
                                                  kind="broader_spend", name=name, unit=unit))
    assert figure["kind"] == "broader_spend"


def test_one_source_keeps_a_figure_for_each_place():
    us = {**_market_figure(10e9, 2023, "https://a.example/r"), "geography": "United States"}
    world = {**_market_figure(40e9, 2024, "https://a.example/r"), "geography": "Global"}
    data = _sized([us, world])["data"]
    assert data["methods"]["top_down"]["tam"]["value"] == 25e9


def test_a_short_place_name_does_not_match_inside_another():
    count = {"kind": "customer_count", "name": "Clinics", "value": 10.0, "unit": "establishments",
             "geography": "US", "citation": {}, "trade_area": False}
    other = {**count, "value": 5.0, "geography": "Houston"}
    assert sizing._first_market([count, other], "Houston first") == [other]
