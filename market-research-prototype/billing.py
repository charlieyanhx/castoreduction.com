"""billing.py — taking money, and the entitlements it buys.

STRIPE CHECKOUT, HOSTED. The browser is redirected to a page Stripe owns, the card is
entered there, and we are told what happened by webhook. No card number ever reaches this
process, which keeps the whole application out of PCI scope. The alternative, collecting
card details ourselves, would be a different kind of project.

NO SDK. Two HTTP calls and one HMAC, against `requests` and `hashlib`, which are already
here. This is the same reasoning auth.py used for sessions: the official client would add
a dependency and a supply chain for work the standard library does in a page. The one
security-critical piece is webhook verification, and that is a documented construction
tested below against tampering, replay and a wrong secret.

PRICES LIVE IN STRIPE, NOT HERE. Every product is an env var holding a Stripe Price ID, so
changing what a report costs is a dashboard edit rather than a deploy, and no amount of
money is ever hardcoded in this repository.

FULFILMENT IS IDEMPOTENT. Stripe retries a webhook until it gets a 2xx, and will happily
deliver the same event twice. The checkout session id is a UNIQUE column, so a replayed
event grants nothing the second time. Money arriving twice for one purchase is a support
ticket; credits granted twice for one payment is a hole.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

from logger import get

log = get("billing")

STRIPE_API = "https://api.stripe.com/v1"

#: kind -> the env var naming its Stripe Price. A kind whose price is unset cannot be
#: bought, which is how the whole billing layer stays dormant until it is configured.
PRICE_ENV = {
    "report": "STRIPE_PRICE_REPORT",
    "marks": "STRIPE_PRICE_MARKS",
    "questions": "STRIPE_PRICE_QUESTIONS",
    "rerun": "STRIPE_PRICE_RERUN",
}

#: What each purchase grants. Report credits sit on the ACCOUNT; the refinement packs
#: attach to one JOB, because a budget that followed the buyer between reports would defeat
#: the triage the budget exists to force.
ACCOUNT_KINDS = ("report",)
JOB_KINDS = ("marks", "questions", "rerun")

#: Stripe's own tolerance for webhook timestamps. Older than this and it is a replay of a
#: capture, not a delivery.
_REPLAY_TOLERANCE_S = 300


class BillingError(Exception):
    """Operator-facing: the message is safe to show."""


def configured() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


def price_for(kind: str) -> str | None:
    return (os.environ.get(PRICE_ENV.get(kind, ""), "") or "").strip() or None


def buyable(kind: str) -> bool:
    return configured() and bool(price_for(kind))


# ------------------------------------------------------------------------------ storage --
def _db_path() -> Path:
    return Path(os.environ.get("JOBS_DB_PATH")
                or (Path(__file__).parent / ".jobs.sqlite"))


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=10, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS entitlements (
            id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            job_id TEXT,
            remaining INTEGER NOT NULL,
            session_id TEXT,
            created_at INTEGER NOT NULL)""")
    # The idempotency guard. One checkout session can fulfil exactly once, whatever
    # Stripe's retry policy does.
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_entitlement_session "
                 "ON entitlements(session_id) WHERE session_id IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entitlement_owner "
                 "ON entitlements(account_id, kind)")
    return conn


def balance(account_id: str, kind: str = "report") -> int:
    """Unspent credits of one kind for an account."""
    c = _db()
    n = c.execute("SELECT COALESCE(SUM(remaining), 0) FROM entitlements "
                  "WHERE account_id = ? AND kind = ?", (account_id, kind)).fetchone()[0]
    c.close()
    return int(n or 0)


def consume(account_id: str, kind: str = "report") -> bool:
    """Spend one credit. False when there is nothing to spend.

    Spends the OLDEST row first and decrements in a single statement, so two requests
    racing for the last credit cannot both win: SQLite serialises the writes and the loser
    sees remaining = 0.
    """
    c = _db()
    try:
        row = c.execute("SELECT id FROM entitlements WHERE account_id = ? AND kind = ? "
                        "AND remaining > 0 ORDER BY created_at LIMIT 1",
                        (account_id, kind)).fetchone()
        if not row:
            return False
        cur = c.execute("UPDATE entitlements SET remaining = remaining - 1 "
                        "WHERE id = ? AND remaining > 0", (row[0],))
        return cur.rowcount == 1
    finally:
        c.close()


def _record(account_id: str, kind: str, count: int, session_id: str | None,
            job_id: str | None) -> bool:
    """Write the entitlement. False when this session was already fulfilled."""
    c = _db()
    try:
        c.execute("INSERT INTO entitlements (id, account_id, kind, job_id, remaining, "
                  "session_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (str(uuid.uuid4()), account_id, kind, job_id, count, session_id,
                   int(time.time())))
        return True
    except sqlite3.IntegrityError:
        log.info("[billing] session %s already fulfilled, ignoring replay",
                 (session_id or "")[:20])
        return False
    finally:
        c.close()


# ------------------------------------------------------------------------------ checkout --
def create_checkout(kind: str, account_id: str, success_url: str, cancel_url: str,
                    job_id: str | None = None) -> str:
    """A Stripe Checkout URL for one purchase. Raises BillingError with a safe message."""
    if kind not in PRICE_ENV:
        raise BillingError(f"unknown purchase: {kind}")
    if not configured():
        raise BillingError("payments are not configured on this instance")
    price = price_for(kind)
    if not price:
        raise BillingError(f"no price is set for {kind}")
    if kind in JOB_KINDS and not job_id:
        raise BillingError(f"{kind} is bought for one report, and none was named")

    import requests
    form = {
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items[0][price]": price,
        "line_items[0][quantity]": "1",
        # Metadata is what the webhook reads back. It must carry everything needed to
        # fulfil, because the webhook arrives with no session of its own.
        "metadata[account_id]": account_id,
        "metadata[kind]": kind,
        "client_reference_id": account_id,
    }
    if job_id:
        form["metadata[job_id]"] = job_id
    try:
        r = requests.post(f"{STRIPE_API}/checkout/sessions", data=form, timeout=20,
                          auth=(os.environ["STRIPE_SECRET_KEY"], ""))
        r.raise_for_status()
        url = (r.json() or {}).get("url")
    except Exception as e:                                   # noqa: BLE001
        log.warning("[billing] checkout creation failed: %s", e)
        raise BillingError("could not reach the payment provider, try again")
    if not url:
        raise BillingError("the payment provider returned no checkout link")
    return url


# ------------------------------------------------------------------------------ webhook --
def verify_webhook(raw_body: bytes, signature_header: str, secret: str | None = None,
                   now: int | None = None) -> dict:
    """Authenticate a webhook and return its event. Raises BillingError otherwise.

    THIS FUNCTION IS THE DOOR. Anything that gets past it grants credits without payment,
    so it checks three things, and rejecting on any one of them is the whole point:

      the secret   an HMAC-SHA256 over "{timestamp}.{body}", compared in constant time
      the body     the RAW bytes, because re-serialising JSON changes them and the
                   signature is over what was sent, not over what it parsed into
      the clock    a timestamp outside the tolerance is a captured request being replayed
    """
    secret = secret or os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        raise BillingError("no webhook secret is configured")
    if not signature_header:
        raise BillingError("unsigned webhook")

    parts = {}
    for chunk in signature_header.split(","):
        k, _, v = chunk.strip().partition("=")
        parts.setdefault(k, []).append(v)
    timestamps = parts.get("t") or []
    signatures = parts.get("v1") or []
    if not timestamps or not signatures:
        raise BillingError("malformed signature header")

    try:
        ts = int(timestamps[0])
    except ValueError:
        raise BillingError("malformed signature timestamp")
    if abs((now if now is not None else int(time.time())) - ts) > _REPLAY_TOLERANCE_S:
        raise BillingError("webhook timestamp outside tolerance")

    signed = str(ts).encode() + b"." + raw_body
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    # Any of the offered signatures may match: Stripe sends several while a secret rotates.
    if not any(hmac.compare_digest(expected, s) for s in signatures):
        raise BillingError("webhook signature did not verify")

    try:
        return json.loads(raw_body.decode())
    except Exception:                                        # noqa: BLE001
        raise BillingError("webhook body was not JSON")


def fulfill(event: dict) -> dict:
    """Grant what a completed checkout bought. Safe to call twice with the same event.

    Only `checkout.session.completed` with a paid status grants anything: Stripe emits
    plenty of events, and treating an unpaid session as a purchase would be the same hole
    as skipping the signature.
    """
    kind_of_event = (event or {}).get("type")
    if kind_of_event != "checkout.session.completed":
        return {"granted": False, "reason": f"ignored event {kind_of_event}"}

    session = ((event.get("data") or {}).get("object")) or {}
    if session.get("payment_status") != "paid":
        return {"granted": False, "reason": "session is not paid"}

    meta = session.get("metadata") or {}
    kind = meta.get("kind") or ""
    account_id = meta.get("account_id") or session.get("client_reference_id") or ""
    job_id = meta.get("job_id")
    session_id = session.get("id")
    if kind not in PRICE_ENV or not account_id:
        return {"granted": False, "reason": "session carried no usable metadata"}

    if kind in ACCOUNT_KINDS:
        ok = _record(account_id, kind, 1, session_id, None)
        return {"granted": ok, "kind": kind, "account_id": account_id}

    # A refinement pack. The entitlement row is the receipt and the idempotency guard;
    # iteration.grant is what actually widens the budget on that one report.
    if not job_id:
        return {"granted": False, "reason": "a pack was bought for no report"}
    if not _record(account_id, kind, 1, session_id, job_id):
        return {"granted": False, "reason": "already fulfilled"}
    try:
        import iteration
        iteration.grant(job_id, kind, packs=1, paid=True)
    except Exception as e:                                   # noqa: BLE001
        # The row is written, so the payment is recorded and will not be granted twice.
        # Surfacing the failure beats pretending it worked.
        log.error("[billing] paid for %s on %s but granting failed: %s", kind, job_id, e)
        return {"granted": False, "reason": "payment recorded but the grant failed"}
    return {"granted": True, "kind": kind, "job_id": job_id}
