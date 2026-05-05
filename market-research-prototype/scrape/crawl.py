"""
Iter 38: thin wrapper over crawl4ai.

Lazy-loaded singleton crawler — first call pays a ~3s browser-launch cost,
subsequent calls reuse the pool. Use this when:
  - you need JS execution (SPA / React app)
  - you want clean markdown output for LLM consumption
  - you want robots.txt + per-host rate limit out of the box

For static HTML, prefer `scrape.http.request()` (faster, no browser overhead).

Public API:
  fetch_page(url, *, timeout=20) -> {url, status, html, markdown, structured}
"""
from __future__ import annotations
import asyncio
import threading
from typing import Any

from logger import get

log = get("scrape.crawl")

_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_crawler = None  # AsyncWebCrawler
_loop_thread: threading.Thread | None = None


def _ensure_loop():
    """Create a dedicated background event loop for crawl4ai (it's async-only)."""
    global _loop, _loop_thread
    with _lock:
        if _loop and _loop.is_running():
            return _loop
        _loop = asyncio.new_event_loop()

        def runner():
            asyncio.set_event_loop(_loop)
            _loop.run_forever()

        _loop_thread = threading.Thread(target=runner, daemon=True, name="crawl4ai-loop")
        _loop_thread.start()
        return _loop


def _ensure_crawler():
    """Lazy-init the crawler. Returns the AsyncWebCrawler or None on failure."""
    global _crawler
    if _crawler is not None:
        return _crawler
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig
        _crawler = AsyncWebCrawler(config=BrowserConfig(
            headless=True,
            verbose=False,
            user_agent="MarketResearchPrototype/0.2",
        ))
        # Warm it up so the first real call doesn't pay launch cost
        loop = _ensure_loop()
        async def warm():
            await _crawler.start()
        fut = asyncio.run_coroutine_threadsafe(warm(), loop)
        fut.result(timeout=20)
        log.info("crawl4ai pool warmed")
        return _crawler
    except Exception as e:
        log.warning("crawl4ai unavailable: %s", e)
        _crawler = False  # sentinel — don't retry
        return None


def fetch_page(url: str, timeout: float = 20.0) -> dict | None:
    """
    Fetch a URL through crawl4ai's headless browser. Returns:
      {url, status, html, markdown, success}
    or None if crawl4ai is unavailable (caller should fall back to plain HTTP).
    """
    crawler = _ensure_crawler()
    if not crawler:
        return None

    loop = _ensure_loop()

    async def _go():
        result = await crawler.arun(url=url)
        return result

    try:
        fut = asyncio.run_coroutine_threadsafe(_go(), loop)
        result = fut.result(timeout=timeout)
    except Exception as e:
        log.debug("crawl4ai.arun failed for %s: %s", url, e)
        return None

    if not result:
        return None

    # crawl4ai's CrawlResult has .html, .markdown, .success, .status_code
    return {
        "url": url,
        "status": getattr(result, "status_code", None),
        "html": getattr(result, "html", "") or getattr(result, "cleaned_html", "") or "",
        "markdown": str(getattr(result, "markdown", "") or ""),
        "success": bool(getattr(result, "success", True)),
    }
