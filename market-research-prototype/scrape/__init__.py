"""
Iter 38 — `scrape/` subpackage. Centralized HTTP + extraction stack.

Modules:
  http        — cached requests session (requests-cache), per-host throttling,
                consistent UA + retry policy. Use this instead of bare requests.
  structured  — extruct + price-parser wrappers. Pulls JSON-LD, OpenGraph,
                microdata, prices, social links from arbitrary HTML in one call.
  search      — Brave (if key) → SearXNG public → ddgs cascade. Falls through
                until something returns ≥1 hit.
  wayback     — `waybackpy` fallback for sources that timed out / 4xx'd.
  crawl       — thin async wrapper over crawl4ai with a singleton browser pool,
                markdown output, robots.txt respect, rate limit. For pages where
                we need JS execution.

Importing this package installs requests-cache globally (24h SQLite-backed)
so EVERY `requests.get` call in the rest of the app gets cached for free.
"""
from __future__ import annotations
from .http import install_cache, request, get_session

# Auto-install on first import — every module that does `import scrape` (or any
# submodule) gets cached HTTP for free.
install_cache()

__all__ = ["install_cache", "request", "get_session"]
