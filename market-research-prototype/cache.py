"""
Tiny SQLite cache for HTTP/scraper results. Keeps repeated runs free.
Default TTL: 7 days.
"""
from __future__ import annotations
import json
import os
import sqlite3
import time
from pathlib import Path

# cycle32 deploy: allow env override so a Docker volume at /data is usable.
DB = Path(os.environ.get("LLM_CACHE_PATH") or (Path(__file__).parent / ".cache.sqlite"))
DB.parent.mkdir(parents=True, exist_ok=True)
TTL_SECONDS = 7 * 24 * 3600


def _conn():
    c = sqlite3.connect(DB)
    c.execute(
        "CREATE TABLE IF NOT EXISTS cache "
        "(k TEXT PRIMARY KEY, v TEXT, ts INTEGER)"
    )
    return c


def get(key: str):
    """Cached value for `key`, or None if absent or older than TTL_SECONDS.

    Expiry is checked on READ rather than swept: nothing here runs on a timer, so a
    stale row that is never asked for costs nothing but disk.
    """
    c = _conn()
    row = c.execute("SELECT v, ts FROM cache WHERE k = ?", (key,)).fetchone()
    c.close()
    if not row:
        return None
    v, ts = row
    if time.time() - ts > TTL_SECONDS:
        return None
    try:
        return json.loads(v)
    except Exception:
        return None


def put(key: str, value):
    """Store `value` under `key`, replacing any existing entry and resetting its age."""
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO cache (k, v, ts) VALUES (?, ?, ?)",
        (key, json.dumps(value, default=str), int(time.time())),
    )
    c.commit()
    c.close()


def cached(namespace: str):
    """Decorator that caches a function's result by namespace + args."""
    def deco(fn):
        """Wrap fn so identical arguments are answered from the cache."""
        def wrapper(*args, **kwargs):
            """Return the cached result for these arguments, computing it only on a miss.

            The key is the namespace plus the arguments as sorted JSON, so call order and dict
            ordering cannot produce two keys for one call.
            """
            key = f"{namespace}:{json.dumps([args, kwargs], default=str, sort_keys=True)}"
            hit = get(key)
            if hit is not None:
                return hit
            result = fn(*args, **kwargs)
            put(key, result)
            return result
        wrapper.__name__ = fn.__name__
        return wrapper
    return deco
