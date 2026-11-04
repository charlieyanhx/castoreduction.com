"""The pack's repairs to the host tools, each from a live failure found 2026-11-03."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from packs.market_research_next import open_tools
from packs.market_research_next.analysis import community_discovery
from treadle.core import Evidence

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><category term="askportland" label="r/askportland"/><id>t3_abc</id>
<content type="html">&lt;p&gt;Where is the &lt;b&gt;best&lt;/b&gt; cafe?&lt;/p&gt;</content>
<link href="https://www.reddit.com/r/askportland/comments/abc/"/>
<published>2026-10-01T00:00:00+00:00</published><title>Best cafe</title></entry>
<entry><id>t5_2v5pk</id><title>A subreddit, not a post</title></entry>
</feed>"""


def reply(status=200, text=FEED, headers=None):
    return SimpleNamespace(status_code=status, text=text, headers=headers or {})


class Session:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def request(self, method, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_the_feed_gives_posts_and_drops_subreddit_entries():
    posts = open_tools.reddit_search("cafe", session=Session(reply()))
    assert posts == [{"kind": "post", "title": "Best cafe", "text": "Where is the best cafe?",
                      "subreddit": "askportland",
                      "url": "https://www.reddit.com/r/askportland/comments/abc/",
                      "created": "2026-10-01T00:00:00+00:00"}]


def test_one_rate_limit_is_waited_out(monkeypatch):
    waits = []
    monkeypatch.setattr(open_tools.time, "sleep", waits.append)
    session = Session(reply(429, "", {"x-ratelimit-reset": "4"}), reply())
    assert len(open_tools.reddit_search("cafe", session=session)) == 1
    assert waits == [5.0]


def test_a_long_rate_limit_wait_is_capped(monkeypatch):
    waits = []
    monkeypatch.setattr(open_tools.time, "sleep", waits.append)
    open_tools.reddit_search("cafe", session=Session(
        reply(429, "", {"x-ratelimit-reset": "600"}), reply()))
    assert waits == [60.0]


@pytest.mark.parametrize("replies", [
    (reply(403, "<html>"),), (reply(200, "<html><broken"),), (ConnectionError("down"),),
    (reply(429, ""), reply(429, "")),
])
def test_a_refusal_is_a_failure_never_an_empty_result(replies, monkeypatch):
    monkeypatch.setattr(open_tools.time, "sleep", lambda seconds: None)
    with pytest.raises(open_tools.RedditUnavailable):
        open_tools.reddit_search("cafe", session=Session(*replies))


def test_an_xml_bomb_is_refused():
    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><feed>&a;</feed>'
    with pytest.raises(open_tools.RedditUnavailable, match="unreadable"):
        open_tools.reddit_search("cafe", session=Session(reply(200, bomb)))


def test_the_tool_reports_a_refusal_as_a_failed_source(monkeypatch):
    def refused(query, limit):
        raise open_tools.RedditUnavailable("Reddit search answered 403")

    monkeypatch.setattr(open_tools, "reddit_search", refused)
    evidence = open_tools.reddit_mentions("cafe")
    assert evidence.count == 0 and evidence.skeleton
    assert "not an empty result" in evidence.error


def food_away(category=None, series_id=None):
    return Evidence(source="bls_cex_spend", category="econ", count=1,
                    payload={"annual_usd": 4142.0, "series_id": "CXUFOODAWAYLB0101M"})


def test_a_stand_in_series_says_what_it_covers():
    tool = open_tools._with_series_coverage(food_away)
    payload = tool(category="coffee").payload
    assert payload["annual_usd"] == 4142.0
    assert payload["covers"].startswith("all food away from home")
    assert "never call it coffee spending" in payload["limit"]


def test_a_failed_series_lookup_is_left_alone():
    failed = Evidence(source="bls_cex_spend", category="econ", count=0, skeleton=True,
                      error="BLS API unavailable")
    assert open_tools._with_series_coverage(lambda **kw: failed)(category="coffee") is failed


def test_the_answering_overpass_mirror_goes_first_and_each_is_tried_once(monkeypatch):
    from tools import geo

    tried = []

    def http(method, url, data, timeout):
        tried.append((url, timeout))
        return None

    monkeypatch.setattr(geo, "_http_json", http)
    assert open_tools._overpass_once("query") is None
    assert [url for url, _ in tried] == list(open_tools.OVERPASS_MIRRORS)
    assert all(timeout <= 25 for _, timeout in tried)
    assert "mail.ru" in tried[0][0]


def test_only_the_named_tools_are_replaced():
    from treadle.defaults import default_tool_registry

    host, repaired = default_tool_registry(), open_tools.research_tools()
    assert set(repaired) - set(host) == {"read_page"} and set(host) <= set(repaired)
    changed = {name for name in host if host[name].fn is not repaired[name].fn}
    assert changed == {"reddit_mentions", "osm_named_competitors", "poi_competition",
                       "bls_cex_spend"}
    assert repaired["reddit_mentions"].signature == host["reddit_mentions"].signature


def test_discovery_reads_the_live_feed_and_keeps_the_archive_as_backup():
    session = Session(reply())
    posts = community_discovery._live_posts("cafe", session)
    assert posts[0]["subreddit"] == "askportland"
    assert session.calls[0][0] == open_tools.REDDIT_RSS
    with pytest.raises(community_discovery.base.AnalysisError):
        community_discovery._live_posts("cafe", Session(reply(403, "")))


def test_gemini_calls_have_a_ceiling_below_the_task_deadline():
    import time
    from treadle import llm
    from treadle.execution_limits import activate_budget

    with activate_budget(model_calls=1, tool_calls=1, deadline=time.monotonic() + 480):
        assert llm._gemini_http_options()["http_options"]["timeout"] == 120_000
    with activate_budget(model_calls=1, tool_calls=1, deadline=time.monotonic() + 30):
        assert llm._gemini_http_options()["http_options"]["timeout"] <= 30_000


def test_a_stalled_gemini_model_falls_through_to_the_next(monkeypatch):
    from treadle import llm

    tried = []

    class Models:
        def generate_content(self, model, contents, config):
            tried.append(model)
            if len(tried) == 1:
                raise TimeoutError("The read operation timed out")
            usage = SimpleNamespace(prompt_token_count=1, candidates_token_count=1)
            return SimpleNamespace(text='{"ok": true}', usage_metadata=usage)

    monkeypatch.setattr(llm, "_gemini_client", lambda: SimpleNamespace(models=Models()))
    monkeypatch.setattr(llm, "_gemini_rate_gate", lambda model="": None)
    text, _, _ = llm._call_gemini("system", "user", 10, "gemini-flash-latest")
    assert text == '{"ok": true}'
    assert tried == ["gemini-flash-latest", llm.GEMINI_CHAIN[0]]


# --------------------------------------------------------------------------- read_page
class _Page:
    def __init__(self, text="", status=200, kind="text/html; charset=utf-8"):
        self.text, self.status_code, self.headers = text, status, {"Content-Type": kind}


def _served(monkeypatch, response):
    import scrape.http
    asked = []

    def request(method, url, **kwargs):
        asked.append((method, url))
        return response
    monkeypatch.setattr(scrape.http, "request", request)
    return asked


def test_read_page_returns_clean_text_and_its_own_links(monkeypatch):
    html = ('<html><script>steal()</script><h1>Plans</h1><p>Pro  $9</p>'
            '<a href="/menu">m</a><a href="https://other.com/x">o</a></html>')
    asked = _served(monkeypatch, _Page(html))
    out = open_tools.read_page("https://acme.com/start")
    assert asked == [("GET", "https://acme.com/start")] and out.count == 1
    assert out.payload["url"] == "https://acme.com/start"
    assert out.payload["content"] == "Plans Pro $9 m o"
    assert out.payload["links"] == ["https://acme.com/menu"]


def test_read_page_cuts_to_its_limit(monkeypatch):
    _served(monkeypatch, _Page("x" * 9000))
    assert len(open_tools.read_page("https://a.com/").payload["content"]) == open_tools.PAGE_CHARS
    assert len(open_tools.read_page("https://a.com/", max_chars=8000).payload["content"]) == 8000


@pytest.mark.parametrize("response", [None, _Page("no", 403), _Page("%PDF", kind="application/pdf"),
                                      _Page("<script>x</script>")])
def test_read_page_reports_a_page_it_could_not_read(monkeypatch, response):
    _served(monkeypatch, response)
    out = open_tools.read_page("https://a.com/")
    assert out.error and out.count == 0 and not out.payload


def test_read_page_never_passes_a_failure_as_an_exception(monkeypatch):
    import scrape.http

    def boom(method, url, **kwargs):
        raise RuntimeError("secret detail")
    monkeypatch.setattr(scrape.http, "request", boom)
    out = open_tools.read_page("https://a.com/")
    assert out.error and "secret" not in out.error


def test_the_pack_registry_offers_read_page_to_planners():
    tool = open_tools.research_tools()["read_page"]
    assert tool.name == "read_page" and tool.category == "scrape"
    assert "web page" in tool.docstring and "url" in tool.signature


@pytest.mark.parametrize("category,series", [
    ("specialty coffee shop boutique", open_tools.FOOD_AWAY_SERIES), ("cafe", open_tools.FOOD_AWAY_SERIES),
    ("meal delivery", open_tools.FOOD_AWAY_SERIES), ("clothing", None)])
def test_food_and_drink_never_reach_the_keyword_lookup(category, series):
    # Cafe, 2026-11-03: "boutique" in the category read as apparel spending.
    seen = {}

    def host(category=None, series_id=None):
        seen.update(category=category, series_id=series_id)
        return Evidence(source="bls_cex_spend", category="econ", count=1, payload={"value": 1})
    open_tools._with_series_coverage(host)(category=category)
    assert seen["series_id"] == series
