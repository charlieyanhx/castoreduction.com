"""Read each competitor's own pricing page, one competitor at a time.

This replaces a planner model for this job. On a live run the planner read one page, ran
one search for the other four competitors and stopped, so prices it saw only in search
snippets could not be verified. Finding a page and reading it needs no judgement: the same
steps run for every competitor, and the writing model gets the pages, not a summary.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from treadle.core import Evidence
from treadle.execution_limits import charge_tool_call
from treadle.harness.agent import AgentResult

from ..open_tools import HREF as _HREF, MAX_PAGE_CHARS, plain
from . import base

#: Tool calls for one section: a search and a read for each of five competitors, and two
#: reads of a menu or plans page linked from a homepage. execution_limits leases a task
#: at least this many.
MAX_CALLS = 12
#: Companies whose name was taken buy these: usemotion.com, getbarista.ai.
_DOMAIN_PREFIX = re.compile(r"^(?:get|use|try|my|the|go)(?=.)")
_NOT_THE_NAME = frozenset({"the", "a", "an", "and", "of", "by", "co", "inc", "llc", "ltd"})
#: Words that mark a page holding prices, strongest first. A page matching one of the
#: first four needs no further link.
_PRICE_WORDS = ("pricing", "prices", "plans", "menu", "rates", "packages", "services")
_IS_PRICE_PAGE = 4
_PRICE = re.compile(r"[$€£]\s?\d|\d\s?(?:usd|cad|eur|gbp)\b", re.I)
#: A page's share of the writer's evidence: pricing cuts each item at 6,000 characters.
PAGE_CHARS = 5500
_BEFORE, _AFTER, _GAP = 120, 80, " ... "


def brand(name: str) -> str:
    """The word a competitor's own domain will hold: "jane" for Jane App."""
    words = [w for w in re.findall(r"[a-z0-9]+", name.casefold()) if w not in _NOT_THE_NAME]
    return words[0] if words else ""


def own_site(name: str, url: Any) -> bool:
    """The address is the competitor's own, not an article or a namesake.

    The domain must open with the brand: a word inside it proves nothing, and on a live
    run artlybaristabot.com passed as the Portland cafe called Barista.
    """
    key = brand(name)
    if not key or not isinstance(url, str):
        return False
    label = _DOMAIN_PREFIX.sub("", base.registered_domain(url).split(".")[0])
    return label.startswith(key)


def _rank(url: str, title: str = "") -> int:
    text = f"{urlparse(url).path} {title}".casefold()
    return next((i for i, word in enumerate(_PRICE_WORDS) if word in text), len(_PRICE_WORDS))


def best_hit(name: str, hits: Any) -> str | None:
    """The competitor's own page among the search results, its pricing page first."""
    own = [h for h in hits or [] if isinstance(h, dict) and own_site(name, h.get("url"))]
    return min(own, key=lambda h: _rank(h["url"], str(h.get("title") or "")))["url"] if own else None


def price_link(links: list[str]) -> str | None:
    """The site's own link most likely to hold prices."""
    priced = [u for u in links if _rank(u) < len(_PRICE_WORDS)]
    return min(priced, key=lambda u: (_rank(u), len(u))) if priced else None


def price_text(text: str, limit: int) -> str:
    """The page cut to `limit`, keeping the text around its prices.

    A pricing page often opens with thousands of characters of navigation and feature
    copy: one live page showed its first price at character 8,286, past the cut the
    writer used to get.
    """
    if len(text) <= limit:
        return text
    spots = [m.start() for m in _PRICE.finditer(text)]
    if not spots:
        return text[:limit]
    head = text[:limit // 4]
    wanted = max(1, (limit - len(head)) // (_BEFORE + _AFTER + len(_GAP)))
    stride = max(1, len(spots) // wanted)
    windows = [text[max(0, spot - _BEFORE):spot + _AFTER] for spot in spots[::stride][:wanted]]
    return _GAP.join([head, *windows])[:limit]


def gather(goal: str, *, context: str, registry: Any = None, **_: Any) -> AgentResult:
    """The pages for one pricing section, in the shape the agent loop returns.

    Only pages read count as evidence. Search results find a page and are dropped, so a
    price seen in a snippet can never be cited as confirmed.
    """
    if registry is None:
        from ..open_tools import research_tools
        registry = research_tools()
    result = AgentResult(goal=goal, answer="")
    calls = 0

    def call(tool: str, **args: Any) -> Any:
        nonlocal calls
        calls += 1
        charge_tool_call()
        evidence = registry[tool].fn(**args)
        return None if evidence.error else evidence.payload

    def read(name: str, url: str) -> list[str]:
        page = call("read_page", url=url, max_chars=MAX_PAGE_CHARS)
        if not isinstance(page, dict):
            return []
        result.evidence.append(Evidence(
            source="read_page", category="scrape", count=1,
            payload={"url": url, "competitor": name,
                     "content": price_text(page["content"], PAGE_CHARS)}))
        return page["links"]

    opened = []
    for competitor in json.loads(context)["competitors"]:
        name, url = competitor["name"], competitor.get("url")
        if not own_site(name, url):
            if calls + 2 > MAX_CALLS:
                break
            url = best_hit(name, call("web_search", query=f"{name} pricing"))
        if url and calls < MAX_CALLS:
            opened.append((name, url, read(name, url)))
    for name, url, links in opened:
        link = price_link(links) if _rank(url) >= _IS_PRICE_PAGE else None
        if link and base.norm_url(link) != base.norm_url(url) and calls < MAX_CALLS:
            read(name, link)
    result.completed, result.stop_reason = True, "done"
    return result
