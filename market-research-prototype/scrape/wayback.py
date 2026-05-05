"""
Iter 38: Wayback Machine fallback.

When a live source times out / 4xx's / 5xx's, try the most recent cached snapshot.
Free, no key, well-rate-limited (we keep it under 1/sec).

Two entrypoints:
  latest_snapshot_url(url) -> str | None    # gives the archive URL
  fetch_via_wayback(url)   -> str | None    # returns the HTML body of the snapshot
"""
from __future__ import annotations
from .http import request, USER_AGENT
from logger import get

log = get("scrape.wayback")


def latest_snapshot_url(url: str, timeout: float = 8.0) -> str | None:
    """
    Return the URL of the most recent Wayback snapshot, or None.

    Iter 43: Replaced waybackpy (which doesn't honor socket timeouts) with a
    direct CDX-API call through our cached/throttled `scrape.http.request`.
    Was hanging the entire pipeline minutes-to-indefinitely on Crunchbase URLs.
    """
    if not url:
        return None
    try:
        # CDX API: returns list-of-lists, first row is column headers
        r = request(
            "GET",
            "http://web.archive.org/cdx/search/cdx",
            params={"url": url, "limit": "-1", "output": "json", "filter": "statuscode:200"},
            timeout=timeout,
        )
        if r is None or not r.ok:
            return None
        rows = r.json()
        if not rows or len(rows) < 2:
            return None
        # rows[0] = headers, rows[-1] = newest. Format: [urlkey, timestamp, original, ...]
        last = rows[-1]
        if len(last) < 3:
            return None
        timestamp = last[1]
        original = last[2]
        return f"http://web.archive.org/web/{timestamp}/{original}"
    except Exception as e:
        log.debug("wayback CDX lookup failed for %s: %s", url, e)
        return None


def fetch_via_wayback(url: str, timeout: float = 10.0) -> str | None:
    """Resolve to the latest snapshot then GET its body. Returns HTML or None."""
    snap = latest_snapshot_url(url)
    if not snap:
        return None
    r = request("GET", snap, timeout=timeout)
    if r is None or not r.ok:
        return None
    return r.text
