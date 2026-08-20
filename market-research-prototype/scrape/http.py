"""
Iter 38: cached HTTP + per-host throttle + consistent UA.

`requests-cache` monkey-patches `requests` globally — once installed, every
`requests.get` in the entire app reads from a SQLite cache (24h TTL) before
hitting the network. Cuts re-runs 5-20×. Disable per-call with `expire_after=0`.

Per-host throttle: a tiny in-memory rate-limit so we don't hammer one host
with 10 concurrent requests (which gets us banned). Default 2/sec per host.
"""
from __future__ import annotations
import os
import threading
import time
from collections import defaultdict
from pathlib import Path

import requests  # module-level so tests can patch scrape.http.requests.request

# cycle32 deploy: env override for Docker volume / non-default cache location
CACHE_PATH = Path(os.environ.get("HTTP_CACHE_PATH") or (Path(__file__).parent.parent / ".http_cache.sqlite"))
CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
DEFAULT_TTL = 24 * 3600  # 24h
USER_AGENT = "MarketResearchPrototype/0.2 (+https://github.com/anthropics/claude-code)"

_installed = False
_install_lock = threading.Lock()

# One warning per (host, exception type) per process — a broken serializer fires on
# every request, and a thousand identical stack lines would bury the one that matters.
_logged_failures: set[tuple[str, str]] = set()


def _log_request_failure(url: str, exc: Exception) -> None:
    from urllib.parse import urlparse
    key = (urlparse(url).netloc, type(exc).__name__)
    if key in _logged_failures:
        return
    _logged_failures.add(key)
    try:
        from logger import get
        get("scrape.http").warning(
            "[http] request to %s failed with %s: %s — returning None (further "
            "identical failures for this host suppressed)",
            key[0], type(exc).__name__, exc)
    except Exception:
        pass

_host_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
_host_last_request: dict[str, float] = defaultdict(lambda: 0.0)
PER_HOST_MIN_INTERVAL = 0.5  # seconds — 2 req/sec/host


def install_cache(force_reinstall: bool = False) -> None:
    """Idempotent — install requests-cache globally with SQLite backend."""
    global _installed
    with _install_lock:
        if _installed and not force_reinstall:
            return
        try:
            import requests_cache
            requests_cache.install_cache(
                cache_name=str(CACHE_PATH).replace(".sqlite", ""),
                backend="sqlite",
                expire_after=DEFAULT_TTL,
                allowable_methods=("GET", "HEAD"),
                allowable_codes=(200, 203, 300, 301, 302, 308, 404),
                stale_if_error=True,  # serve stale cache when network fails
            )
            _installed = True
        except ImportError:
            # If requests-cache isn't installed for some reason, silently skip.
            pass


def get_session():
    """Get a properly-configured requests session (cached UA, retries baked in)."""
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    return s


def _throttle(host: str) -> None:
    """Sleep just enough to honor the per-host rate limit."""
    with _host_locks[host]:
        elapsed = time.time() - _host_last_request[host]
        wait = PER_HOST_MIN_INTERVAL - elapsed
        if wait > 0:
            time.sleep(wait)
        _host_last_request[host] = time.time()


def request(method: str, url: str, *, timeout: float = 10, **kwargs):
    """
    Cached, UA-stamped, per-host throttled request. Returns the requests.Response
    or None on failure (instead of raising — caller checks `.ok`).
    """
    install_cache()
    from urllib.parse import urlparse

    host = urlparse(url).netloc
    if host:
        _throttle(host)

    headers = kwargs.pop("headers", None) or {}
    headers.setdefault("User-Agent", USER_AGENT)
    headers.setdefault("Accept-Language", "en-US,en;q=0.9")
    try:
        return requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
    except Exception as e:
        # MEASURED (2026-08-20): requests-cache 1.3.1 + attrs 26 raised NameError
        # while SAVING every fresh response, and this bare except converted that
        # environment bug into None — which callers read as "host had no data".
        # Weeks of geocode misses were attributed to Nominatim throttling. Never
        # swallow silently: name the exception so infrastructure failure and
        # empty-result stay distinguishable in the logs.
        _log_request_failure(url, e)
        return None
