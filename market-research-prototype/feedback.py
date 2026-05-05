"""
Operator feedback loop — capture thumbs-up/down + comments on plan outputs.

Stored in the existing .jobs.sqlite (separate table). Used to:
  1. Track quality over time (% positive)
  2. Surface which categories/segments produce reliable outputs
  3. Eventually train better scoring weights / prompt tuning

Schema:
  feedback (id, job_id, rating, section, comment, created_at)
    rating: -1 (bad), 0 (neutral), +1 (good)
    section: 'overall' | 'product' | 'price' | 'place' | 'promotion' | 'viability' | 'audience' | 'competitors'
"""
from __future__ import annotations
import sqlite3
import time
import threading
from pathlib import Path

from logger import get

log = get("feedback")

DB = Path(__file__).parent / ".jobs.sqlite"  # share with jobs DB
_lock = threading.Lock()


def _conn():
    conn = sqlite3.connect(DB, timeout=10, isolation_level=None)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            rating INTEGER NOT NULL,
            section TEXT DEFAULT 'overall',
            comment TEXT,
            created_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_feedback_job ON feedback (job_id)"
    )
    return conn


def submit(job_id: str, rating: int, section: str = "overall", comment: str = "") -> int:
    """Insert a feedback row. Returns the row id."""
    if rating not in (-1, 0, 1):
        raise ValueError("rating must be -1, 0, or 1")
    now = int(time.time())
    with _lock:
        c = _conn()
        cur = c.execute(
            "INSERT INTO feedback (job_id, rating, section, comment, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (job_id, rating, section, comment[:1000], now),
        )
        rid = cur.lastrowid
        c.close()
    log.info("feedback %s job=%s rating=%d section=%s", rid, job_id[:8], rating, section)
    return rid


def get_for_job(job_id: str) -> list[dict]:
    """Return all feedback rows for a job."""
    with _lock:
        c = _conn()
        rows = c.execute(
            "SELECT id, rating, section, comment, created_at FROM feedback "
            "WHERE job_id = ? ORDER BY created_at DESC",
            (job_id,),
        ).fetchall()
        c.close()
    return [
        {"id": r[0], "rating": r[1], "section": r[2], "comment": r[3], "created_at": r[4]}
        for r in rows
    ]


def stats() -> dict:
    """Aggregate pipeline quality: % positive, by section, by recent N days."""
    now = int(time.time())
    with _lock:
        c = _conn()
        # Overall counts
        rows = c.execute(
            "SELECT section, rating, COUNT(*) FROM feedback GROUP BY section, rating"
        ).fetchall()
        # Recent 7 days
        cutoff = now - 7 * 86400
        recent = c.execute(
            "SELECT rating, COUNT(*) FROM feedback WHERE created_at > ? GROUP BY rating",
            (cutoff,),
        ).fetchall()
        # Top complaints (negative comments)
        complaints = c.execute(
            "SELECT job_id, section, comment FROM feedback "
            "WHERE rating < 0 AND comment IS NOT NULL AND comment != '' "
            "ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        # Total
        total = c.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        c.close()

    by_section: dict = {}
    for section, rating, count in rows:
        by_section.setdefault(section, {"positive": 0, "neutral": 0, "negative": 0})
        if rating > 0:
            by_section[section]["positive"] += count
        elif rating < 0:
            by_section[section]["negative"] += count
        else:
            by_section[section]["neutral"] += count

    recent_pos = sum(c for r, c in recent if r > 0)
    recent_neg = sum(c for r, c in recent if r < 0)
    recent_total = recent_pos + recent_neg

    overall_pos = sum(s["positive"] for s in by_section.values())
    overall_neg = sum(s["negative"] for s in by_section.values())
    overall_total = overall_pos + overall_neg

    return {
        "total_feedback": total,
        "overall_positive_pct": round(overall_pos / overall_total * 100, 1) if overall_total else None,
        "recent_7d_positive_pct": round(recent_pos / recent_total * 100, 1) if recent_total else None,
        "by_section": by_section,
        "recent_complaints": [
            {"job_id": r[0][:8], "section": r[1], "comment": r[2][:200]}
            for r in complaints
        ],
    }
