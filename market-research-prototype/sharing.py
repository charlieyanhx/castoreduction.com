"""The shared library, and the coupon a founder earns by putting a report in it.

TWO THINGS THAT ONLY MAKE SENSE TOGETHER. A library of finished reports is the best sales
asset the product has: a stranger deciding whether $29 is worth it wants to read real
work, not a landing page. But a report is the founder's own venture: their costs, their
site, their break-even. Nothing here is public by default and nothing becomes public
because it looked good. The owner publishes it, deliberately, under a name they choose,
and can take it back down at any time.

The $10 coupon is what makes that trade honest. They are giving us a sales asset; they get
paid for it in the only currency this product has.

WHAT IS PUBLISHED IS NOT WHAT THEY SEE. A shared report renders with annotate=0, so the
raw intake answers, the refine controls and the reader's private notes stay out of the
page entirely, the same treatment /sample already gets and for the same reason.
"""
from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import time
from pathlib import Path

log = logging.getLogger("mrp.sharing")

#: What a share is worth. One coupon per report, ever: re-sharing after a withdrawal does
#: not mint a second one, or the reward becomes a tap.
REWARD_USD = 10.0

#: How long a founder's name for their own report can be. It is shown to strangers.
MAX_TITLE = 120


def _db_path() -> Path:
    return Path(os.environ.get("JOBS_DB_PATH")
                or (Path(__file__).parent / ".jobs.sqlite"))


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=10, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS shared_reports (
            job_id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            title TEXT NOT NULL,
            shared_at INTEGER NOT NULL,
            withdrawn_at INTEGER)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shared_live "
                 "ON shared_reports(shared_at) WHERE withdrawn_at IS NULL")
    conn.execute("""CREATE TABLE IF NOT EXISTS coupons (
            code TEXT PRIMARY KEY,
            value_usd REAL NOT NULL,
            account_id TEXT,
            email TEXT,
            job_id TEXT,
            created_at INTEGER NOT NULL,
            redeemed_at INTEGER,
            stripe_promo_id TEXT)""")
    # ONE REWARD PER REPORT. The share endpoint is idempotent and a founder can withdraw
    # and re-share; without this, either of those mints money.
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_coupon_job "
                 "ON coupons(job_id) WHERE job_id IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_coupon_owner ON coupons(account_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_coupon_email "
                 "ON coupons(email) WHERE email IS NOT NULL")
    return conn


# -------------------------------------------------------------------------- the library --
def is_shared(job_id: str) -> bool:
    c = _db()
    try:
        row = c.execute("SELECT 1 FROM shared_reports WHERE job_id = ? "
                        "AND withdrawn_at IS NULL", (job_id,)).fetchone()
        return bool(row)
    finally:
        c.close()


def entry(job_id: str) -> dict | None:
    """The live listing for one report, or None if it is not published."""
    c = _db()
    try:
        row = c.execute("SELECT job_id, owner_id, title, shared_at FROM shared_reports "
                        "WHERE job_id = ? AND withdrawn_at IS NULL", (job_id,)).fetchone()
    finally:
        c.close()
    if not row:
        return None
    return {"job_id": row[0], "owner_id": row[1], "title": row[2], "shared_at": row[3]}


def listing(limit: int = 60, offset: int = 0) -> list[dict]:
    """Everything currently published, newest first. Owner ids never leave this module."""
    c = _db()
    try:
        rows = c.execute(
            "SELECT job_id, title, shared_at FROM shared_reports "
            "WHERE withdrawn_at IS NULL ORDER BY shared_at DESC LIMIT ? OFFSET ?",
            (max(1, min(int(limit), 200)), max(0, int(offset)))).fetchall()
    finally:
        c.close()
    return [{"job_id": r[0], "title": r[1], "shared_at": r[2]} for r in rows]


def mine(owner_id: str) -> list[dict]:
    c = _db()
    try:
        rows = c.execute(
            "SELECT job_id, title, shared_at FROM shared_reports "
            "WHERE owner_id = ? AND withdrawn_at IS NULL ORDER BY shared_at DESC",
            (owner_id,)).fetchall()
    finally:
        c.close()
    return [{"job_id": r[0], "title": r[1], "shared_at": r[2]} for r in rows]


def publish(job_id: str, owner_id: str, title: str) -> dict:
    """Put a report in the library under a name the owner chose.

    THE CALLER PROVES OWNERSHIP. This module never sees a request and cannot check a
    cookie, so it takes the owner id it is given. Routes must pass one that came from
    _owned_job, never one from the request body.

    Idempotent: re-publishing an entry already live only renames it.
    """
    title = (title or "").strip()[:MAX_TITLE] or "Untitled venture"
    now = int(time.time())
    c = _db()
    try:
        existing = c.execute("SELECT owner_id FROM shared_reports WHERE job_id = ?",
                             (job_id,)).fetchone()
        if existing and existing[0] != owner_id:
            # Should be unreachable, since the route checks ownership first, but a library
            # entry silently changing hands is not a thing to leave to one guard.
            raise PermissionError("that report is published by someone else")
        c.execute("INSERT INTO shared_reports (job_id, owner_id, title, shared_at, "
                  "withdrawn_at) VALUES (?, ?, ?, ?, NULL) "
                  "ON CONFLICT(job_id) DO UPDATE SET title = excluded.title, "
                  "withdrawn_at = NULL", (job_id, owner_id, title, now))
    finally:
        c.close()
    log.info("[sharing] %s published %s as %r", owner_id[:12], job_id[:8], title)
    return {"job_id": job_id, "title": title, "shared_at": now}


def withdraw(job_id: str, owner_id: str) -> bool:
    """Take it back down. The coupon already earned is NOT clawed back."""
    c = _db()
    try:
        n = c.execute("UPDATE shared_reports SET withdrawn_at = ? "
                      "WHERE job_id = ? AND owner_id = ? AND withdrawn_at IS NULL",
                      (int(time.time()), job_id, owner_id)).rowcount or 0
    finally:
        c.close()
    if n:
        log.info("[sharing] %s withdrew %s", owner_id[:12], job_id[:8])
    return bool(n)


def reassign_owner(old_owner: str, new_owner: str) -> int:
    """A guest who published and then registered keeps their entries and their coupons."""
    if not old_owner or not new_owner or old_owner == new_owner:
        return 0
    c = _db()
    try:
        n = c.execute("UPDATE shared_reports SET owner_id = ? WHERE owner_id = ?",
                      (new_owner, old_owner)).rowcount or 0
        n += c.execute("UPDATE coupons SET account_id = ? WHERE account_id = ?",
                       (new_owner, old_owner)).rowcount or 0
    finally:
        c.close()
    return n


# -------------------------------------------------------------------------- the coupons --
def _new_code() -> str:
    """Unambiguous by construction: no O/0, no I/1/L, so a code read off a screen and
    typed into Stripe's box lands on the first try."""
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    body = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"CASTOR-{body[:4]}-{body[4:]}"


def coupon_for_job(job_id: str) -> dict | None:
    c = _db()
    try:
        row = c.execute("SELECT code, value_usd, account_id, email, redeemed_at "
                        "FROM coupons WHERE job_id = ?", (job_id,)).fetchone()
    finally:
        c.close()
    if not row:
        return None
    return {"code": row[0], "value_usd": row[1], "account_id": row[2],
            "email": row[3], "redeemed": bool(row[4])}


def mint(job_id: str, account_id: str | None, email: str | None,
         value_usd: float = REWARD_USD) -> dict:
    """Create the reward for sharing one report, or hand back the one already created.

    Returns the coupon either way, so the endpoint is safe to call twice: a double-click
    on Share must not be worth $20.
    """
    already = coupon_for_job(job_id)
    if already:
        return already
    code = _new_code()
    email = (email or "").strip().lower() or None
    c = _db()
    try:
        c.execute("INSERT INTO coupons (code, value_usd, account_id, email, job_id, "
                  "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                  (code, float(value_usd), account_id, email, job_id, int(time.time())))
    except sqlite3.IntegrityError:
        # Lost a race on the job_id index. The winner's coupon is the real one.
        c.close()
        return coupon_for_job(job_id) or {}
    finally:
        try:
            c.close()
        except Exception:                                        # noqa: BLE001
            pass
    _sync_to_stripe(code, value_usd)
    log.info("[sharing] minted a $%.0f coupon for sharing %s", value_usd, job_id[:8])
    return {"code": code, "value_usd": float(value_usd), "account_id": account_id,
            "email": email, "redeemed": False}


def _sync_to_stripe(code: str, value_usd: float) -> None:
    """Make the code redeemable at checkout. Dormant until Stripe is configured.

    A code that exists here and not in Stripe is typed into the discount box and rejected,
    which is worse than not offering one. Rather than fail the share, the row keeps
    stripe_promo_id NULL and pending() lists it, so the operator can push the backlog the
    moment keys land.
    """
    import billing
    if not billing.configured():
        return
    try:
        promo_id = billing.create_promo_code(code, int(round(value_usd * 100)))
    except Exception as e:                                       # noqa: BLE001
        log.warning("[sharing] coupon %s is not redeemable yet: %s", code, e)
        return
    c = _db()
    try:
        c.execute("UPDATE coupons SET stripe_promo_id = ? WHERE code = ?", (promo_id, code))
    finally:
        c.close()


def pending() -> list[str]:
    """Codes promised to a founder that Stripe does not know about yet."""
    c = _db()
    try:
        return [r[0] for r in c.execute(
            "SELECT code FROM coupons WHERE stripe_promo_id IS NULL AND redeemed_at IS NULL")]
    finally:
        c.close()


def sync_pending() -> int:
    """Push the backlog. Call after Stripe keys are added to an instance that ran without."""
    n = 0
    for code in pending():
        c = _db()
        try:
            row = c.execute("SELECT value_usd FROM coupons WHERE code = ?", (code,)).fetchone()
        finally:
            c.close()
        if not row:
            continue
        _sync_to_stripe(code, row[0])
        n += 1
    return n


def held_by(account_id: str) -> list[dict]:
    c = _db()
    try:
        rows = c.execute("SELECT code, value_usd, created_at, redeemed_at FROM coupons "
                         "WHERE account_id = ? ORDER BY created_at DESC",
                         (account_id,)).fetchall()
    finally:
        c.close()
    return [{"code": r[0], "value_usd": r[1], "created_at": r[2],
             "redeemed": bool(r[3])} for r in rows]


def claim_by_email(email: str, account_id: str) -> int:
    """The coupon half of "register later".

    Someone shared a report as a guest, left an address, and got the code by mail. When
    they register with that address the coupon moves onto the account, exactly as
    billing.claim_by_email does for prepaid credits.
    """
    email = (email or "").strip().lower()
    if not email or not account_id:
        return 0
    c = _db()
    try:
        n = c.execute("UPDATE coupons SET account_id = ? WHERE email = ? "
                      "AND (account_id IS NULL OR account_id LIKE 'guest-%') "
                      "AND redeemed_at IS NULL", (account_id, email)).rowcount or 0
    finally:
        c.close()
    if n:
        log.info("[sharing] moved %d coupon(s) onto %s", n, account_id[:8])
    return n
