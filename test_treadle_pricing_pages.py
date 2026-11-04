"""Reading each competitor's own pricing page, with no planner deciding when to stop."""
from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import urljoin, urlparse

import pytest

from packs.market_research_next.analysis import pricing, pricing_pages as pages
from treadle.harness.agent import Evidence


class Tools(dict):
    """A registry of fake tools that records each call."""

    def __init__(self, hits=None, html=None):
        super().__init__()
        self.calls: list[tuple[str, dict]] = []
        self.hits, self.html = hits or {}, html or {}
        self["web_search"] = SimpleNamespace(fn=self._search)
        self["read_page"] = SimpleNamespace(fn=self._read)

    def _search(self, query):
        self.calls.append(("web_search", {"query": query}))
        found = next((v for k, v in self.hits.items() if k in query), [])
        return Evidence(source="web_search", category="scrape", count=len(found), payload=found)

    def _read(self, url, max_chars):
        self.calls.append(("read_page", {"url": url}))
        html = self.html.get(url, "")
        links = [urljoin(url, h) for h in pages._HREF.findall(html)
                 if urlparse(urljoin(url, h)).netloc == urlparse(url).netloc]
        payload = {"url": url, "content": pages.plain(html), "links": links}
        return Evidence(source="read_page", category="scrape", count=1 if html else 0,
                        payload=payload if html else None)


def gather(tools, *competitors):
    context = json.dumps({"competitors": [
        {"name": n, "url": u, "kind": "direct"} for n, u in competitors]})
    return pages.gather("goal", context=context, registry=tools)


def reads(tools):
    return [args["url"] for tool, args in tools.calls if tool == "read_page"]


def test_every_competitor_gets_its_own_search_and_read():
    tools = Tools(hits={
        "Todoist": [{"title": "Pricing | Todoist", "url": "https://www.todoist.com/pricing"}],
        "Motion": [{"title": "Pricing", "url": "https://www.usemotion.com/pricing"}]},
        html={"https://www.todoist.com/pricing": "<p>$5</p>"})
    gather(tools, ("Todoist", None), ("Motion", None))
    assert [q["query"] for t, q in tools.calls if t == "web_search"] == ["Todoist pricing",
                                                                         "Motion pricing"]
    assert reads(tools) == ["https://www.todoist.com/pricing", "https://www.usemotion.com/pricing"]


def test_an_article_that_mentions_a_competitor_is_not_its_website():
    # The landscape gave Todoist a blog post on reclaim.ai as its address.
    tools = Tools(hits={"Todoist": [{"title": "Pricing", "url": "https://todoist.com/pricing"}]})
    gather(tools, ("Todoist", "https://reclaim.ai/blog/productivity-apps"))
    assert reads(tools) == ["https://todoist.com/pricing"]


def test_a_known_site_is_read_without_searching():
    tools = Tools()
    gather(tools, ("Sterling Coffee Roasters", "https://sterling.coffee/"))
    assert tools.calls == [("read_page", {"url": "https://sterling.coffee/"})]


def test_the_pricing_page_is_chosen_over_other_pages_of_the_site():
    tools = Tools(hits={"Motion": [
        {"title": "Motion blog", "url": "https://usemotion.com/blog/x"},
        {"title": "Review", "url": "https://other.com/motion-pricing"},
        {"title": "Plans", "url": "https://usemotion.com/pricing"}]})
    gather(tools, ("Motion", None))
    assert reads(tools) == ["https://usemotion.com/pricing"]


def test_a_search_with_no_page_of_the_competitor_reads_nothing():
    tools = Tools(hits={"Acme": [{"title": "Best apps", "url": "https://review.com/acme"}]})
    result = gather(tools, ("Acme", None))
    assert reads(tools) == [] and result.evidence == []


def test_a_homepage_link_to_the_menu_is_followed():
    tools = Tools(html={"https://haven.coffee/": '<a href="/about">a</a><a href="/menu/">m</a>',
                        "https://haven.coffee/menu/": "<p>Latte $5</p>"})
    gather(tools, ("Haven Coffee", "https://haven.coffee/"))
    assert reads(tools) == ["https://haven.coffee/", "https://haven.coffee/menu/"]


def test_a_link_to_another_site_is_not_followed():
    tools = Tools(html={"https://haven.coffee/": '<a href="https://square.site/menu">m</a>'})
    gather(tools, ("Haven Coffee", "https://haven.coffee/"))
    assert reads(tools) == ["https://haven.coffee/"]


def test_a_page_already_about_prices_follows_no_link():
    tools = Tools(html={"https://acme.com/pricing": '<a href="/menu">m</a>'})
    gather(tools, ("Acme", "https://acme.com/pricing"))
    assert reads(tools) == ["https://acme.com/pricing"]


def test_the_section_never_passes_its_call_limit():
    tools = Tools(hits={c: [{"title": "x", "url": f"https://{c}.com/"}] for c in "abcdefgh"})
    gather(tools, *[(f"{c}", None) for c in "abcdefgh"])
    assert len(tools.calls) <= pages.MAX_CALLS


def test_a_page_says_where_it_came_from_and_whose_it_is():
    tools = Tools(html={"https://acme.com/pricing": "<h1>Plans</h1><p>Pro $9</p>"})
    result = gather(tools, ("Acme", "https://acme.com/pricing"))
    page = result.evidence[-1]
    assert page.payload["url"] == "https://acme.com/pricing"
    assert page.payload["competitor"] == "Acme"
    assert "Pro $9" in page.payload["content"] and "<" not in page.payload["content"]


def test_search_snippets_are_not_evidence_of_a_price():
    tools = Tools(hits={"Motion": [{"title": "t", "url": "https://motion.com/p",
                                    "snippet": "from $19 a month"}]})
    result = gather(tools, ("Motion", None))
    assert all(e.source == "read_page" for e in result.evidence)


def test_a_price_far_down_a_long_page_still_reaches_the_writer():
    text = "words " * 2000 + "Pro plan $19 per month " + "more " * 2000
    window = pages.price_text(text, 1000)
    assert "Pro plan $19 per month" in window and len(window) <= 1000
    assert pages.price_text("short $5", 1000) == "short $5"


def test_prices_spread_down_a_page_all_reach_the_writer():
    text = ("x" * 3000 + " Basic $9 ") + ("y" * 3000 + " Pro $29 ")
    window = pages.price_text(text, 1200)
    assert "$9" in window and "$29" in window


@pytest.mark.parametrize("name,url,own", [
    ("Jane App", "https://jane.app/pricing", True),
    ("The Greenhouse Chicago", "https://greenhouse-chi.com/", True),
    ("Todoist", "https://reclaim.ai/blog/x", False), ("Todoist", None, False),
    ("Rover", "https://www.rover.com/", True), ("", "https://a.com", False),
    # Cafe, 2026-11-03: a robot barista maker was priced as the cafe called Barista.
    ("Barista", "https://www.artlybaristabot.com/pricing", False),
    ("Barista", "https://baristapdx.com/", True), ("Barista", "https://getbarista.ai/", True),
    ("Motion", "https://usemotion.com/pricing", True), ("Wag", "https://wagwalking.com/", True)])
def test_own_site(name, url, own):
    assert pages.own_site(name, url) is own


def test_pricing_runs_on_the_page_reader_by_default():
    assert pricing._default_agent() is pages.gather
