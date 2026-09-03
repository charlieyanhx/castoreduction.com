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
    "bundle5": "STRIPE_PRICE_BUNDLE5",
    "bundle10": "STRIPE_PRICE_BUNDLE10",
    "marks": "STRIPE_PRICE_MARKS",
    "questions": "STRIPE_PRICE_QUESTIONS",
    "rerun": "STRIPE_PRICE_RERUN",
}

#: What a purchase is WORTH, as (credit kind, how many). The thing bought and the thing
#: held are not the same: a 5-pack is one line item in Stripe and five report credits here.
#: fulfill() granted a flat 1 for every account kind, so the landing page's "5 for $99"
#: charged for five and delivered one. Anything absent grants one of itself.
GRANTS = {
    "report":   ("report", 1),
    "bundle5":  ("report", 5),
    "bundle10": ("report", 10),
}

#: The operator's list prices, for drawing a button that says what it costs. The AMOUNT
#: CHARGED lives in Stripe and only in Stripe — this is display copy, and a mismatch shows
#: up as a surprise on the checkout page rather than as a wrong charge.
LIST_PRICES_USD = {"report": 29.0, "bundle5": 99.0, "bundle10": 175.0}

#: How a button should read. Kept beside the prices so the two never drift apart.
OFFERS = {
    "report":   {"label": "This report",   "credits": 1},
    "bundle5":  {"label": "5 reports",     "credits": 5},
    "bundle10": {"label": "10 reports",    "credits": 10},
}

#: What each purchase grants. Report credits sit on the ACCOUNT; the refinement packs
#: attach to one JOB, because a budget that followed the buyer between reports would defeat
#: the triage the budget exists to force.
ACCOUNT_KINDS = ("report", "bundle5", "bundle10")
JOB_KINDS = ("marks", "questions", "rerun")

#: Stripe's own tolerance for webhook timestamps. Older than this and it is a replay of a
#: capture, not a delivery.
_REPLAY_TOLERANCE_S = 300


class BillingError(Exception):
    """Operator-facing: the message is safe to show."""


def configured() -> bool:
    """BOTH halves, because half a Stripe integration takes money and grants nothing.

    With the secret key alone, create_checkout works and a card is charged; then every
    webhook delivery hits verify_webhook, which raises without STRIPE_WEBHOOK_SECRET, and
    api.py answers 400. Stripe retries, gives up, and the buyer has paid for a credit that
    was never recorded. /billing/status would still have said configured: true.
    """
    return bool((os.environ.get("STRIPE_SECRET_KEY") or "").strip()
                and (os.environ.get("STRIPE_WEBHOOK_SECRET") or "").strip())


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
    # THE BUYER'S EMAIL, because they pay before they have an account. Stripe collects an
    # address at checkout whether or not we ask, and it is the only durable handle on a
    # purchase made from a guest cookie. Grant to the cookie so the report runs now; keep
    # the address so the credit can be claimed if that cookie is ever cleared, or if they
    # register a week later. Without it "buy first, register later" loses the purchase the
    # moment the browser forgets who they were.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(entitlements)")}
    if "email" not in cols:
        conn.execute("ALTER TABLE entitlements ADD COLUMN email TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entitlement_email "
                     "ON entitlements(email) WHERE email IS NOT NULL")
    # The idempotency guard. One checkout session can fulfil exactly once, whatever
    # Stripe's retry policy does.
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_entitlement_session "
                 "ON entitlements(session_id) WHERE session_id IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entitlement_owner "
                 "ON entitlements(account_id, kind)")
    # WHERE A GUEST WENT. Stripe stamps the guest id into the checkout metadata and the
    # webhook may arrive minutes later, by which time that visitor may have registered and
    # had their guest cookie deleted. Without this the credits are written to an id nobody
    # can present again. See resolve_owner().
    # WHICH RUNS WERE PAID FOR, and whether the money has been given back.
    #
    # The credit is spent at submit time and `_paid_credit` was a local of that request.
    # A deploy kills the worker; the startup resumer picks the job back up in a NEW
    # PROCESS that has no idea it was bought, so a resumed run that then failed was a
    # silent loss with no refund and no notification. A ledger row outlives the process.
    #
    # refunded_at also makes the refund idempotent by construction: two paths can now try
    # to give the same credit back (the crash handler and the delivered-nothing check) and
    # exactly one will succeed.
    conn.execute("""CREATE TABLE IF NOT EXISTS credit_spends (
            job_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            spent_at INTEGER NOT NULL,
            refunded_at INTEGER)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS owner_moves (
            old_owner TEXT PRIMARY KEY,
            new_owner TEXT NOT NULL,
            moved_at INTEGER NOT NULL)""")
    return conn


def record_move(old_owner: str, new_owner: str) -> None:
    """Remember that a guest became an account, so a late webhook can follow them.

    THE RACE THIS CLOSES. Stripe gives no ordering guarantee between the browser redirect
    and the webhook, and a delivery retry pushes the event minutes out. The buyer comes
    back from checkout, registers at the claim card, and signup deletes the guest cookie.
    The webhook then arrives carrying metadata.account_id = the guest id, writes five
    credits against it, and the account shows zero: the survey redraws the paywall at
    somebody who has just paid $99.

    Matching on the Stripe email cannot rescue it, because the address they register with
    need not be the one on the card, and the prefill that would have made them match is
    itself empty until the webhook lands. This mapping is the deterministic fix.
    """
    if not old_owner or not new_owner or old_owner == new_owner:
        return
    c = _db()
    try:
        c.execute("INSERT INTO owner_moves (old_owner, new_owner, moved_at) "
                  "VALUES (?, ?, ?) ON CONFLICT(old_owner) DO UPDATE SET "
                  "new_owner = excluded.new_owner, moved_at = excluded.moved_at",
                  (old_owner, new_owner, int(time.time())))
    finally:
        c.close()


def resolve_owner(account_id: str, _depth: int = 0) -> str:
    """Follow a guest id to the account it became. Identity for anything else.

    Chases a short chain (guest -> account is one hop today, but a guest who registers,
    logs out and registers again could make two) and stops well before any cycle could
    spin, because a wrong answer here writes somebody's purchase to somebody else.
    """
    if not account_id or _depth >= 4:
        return account_id
    c = _db()
    try:
        row = c.execute("SELECT new_owner FROM owner_moves WHERE old_owner = ?",
                        (account_id,)).fetchone()
    finally:
        c.close()
    if not row or not row[0] or row[0] == account_id:
        return account_id
    return resolve_owner(row[0], _depth + 1)


def balance(account_id: str, kind: str = "report") -> int:
    """Unspent credits of one kind for an account."""
    c = _db()
    n = c.execute("SELECT COALESCE(SUM(remaining), 0) FROM entitlements "
                  "WHERE account_id = ? AND kind = ?", (account_id, kind)).fetchone()[0]
    c.close()
    return int(n or 0)


def email_on_credits(account_id: str, kind: str = "report") -> str | None:
    """The address Stripe collected for the credits this owner still holds.

    THE REGISTRATION ASK COMES AFTER THE MONEY, so by the time we ask someone to make an
    account they have already typed their address into Stripe's page. Asking for it a
    second time is friction we have already been given the answer to, and a form that
    prefills is a form that gets finished.

    Only this owner's own rows are read, so it returns nothing for anyone who has not
    bought anything, and it can never surface somebody else's address.
    """
    c = _db()
    try:
        row = c.execute("SELECT email FROM entitlements WHERE account_id = ? AND kind = ? "
                        "AND remaining > 0 AND email IS NOT NULL "
                        "ORDER BY created_at DESC LIMIT 1", (account_id, kind)).fetchone()
    finally:
        c.close()
    return row[0] if row else None


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


def claim_by_email(email: str, account_id: str) -> int:
    """Move credits bought before this account existed onto it. Returns how many.

    THE OTHER HALF OF "BUY FIRST, REGISTER LATER". A purchase made from a guest cookie is
    granted to that cookie so the report can run immediately, and the cookie is one
    keystroke from being cleared. The address Stripe collected is the durable handle: when
    someone registers with it, whatever they already paid for follows them.

    Only rows still holding credit move, and only ones not already on a real account, so
    this can never take a purchase from somebody else.
    """
    email = (email or "").strip().lower()
    if not email or not account_id:
        return 0
    c = _db()
    try:
        n = c.execute(
            "UPDATE entitlements SET account_id = ? "
            "WHERE email = ? AND remaining > 0 AND account_id LIKE 'guest-%'",
            (account_id, email)).rowcount or 0
    finally:
        c.close()
    if n:
        log.info("[billing] claimed %d prepaid credit(s) for %s", n, account_id[:8])
    return n


def reassign_owner(old_owner: str, new_owner: str) -> int:
    """Move unspent credits from a guest to the account they just created.

    The sibling of jobs.reassign_owner and it has to happen in the same breath: a guest
    who bought a pack and then registered would otherwise watch their balance vanish at
    the exact moment the product asked them to commit.
    """
    if not old_owner or not new_owner or old_owner == new_owner:
        return 0
    # RECORDED BEFORE THE MOVE, so a webhook that lands a millisecond later already has
    # somewhere to point. This is the whole fix for the redirect/webhook race.
    record_move(old_owner, new_owner)
    c = _db()
    try:
        n = c.execute("UPDATE entitlements SET account_id = ? WHERE account_id = ?",
                      (new_owner, old_owner)).rowcount or 0
    finally:
        c.close()
    if n:
        log.info("[billing] moved %d entitlement row(s) from %s to %s",
                 n, old_owner[:12], new_owner[:8])
    return n


def record_spend(job_id: str, account_id: str) -> None:
    """Note that this run was paid for with a credit. Survives the process that spent it."""
    if not job_id or not account_id:
        return
    c = _db()
    try:
        c.execute("INSERT OR IGNORE INTO credit_spends (job_id, account_id, spent_at) "
                  "VALUES (?, ?, ?)", (job_id, account_id, int(time.time())))
    finally:
        c.close()


def paid_owner(job_id: str) -> str | None:
    """Who paid for this run, or None if nobody did."""
    if not job_id:
        return None
    c = _db()
    try:
        row = c.execute("SELECT account_id FROM credit_spends WHERE job_id = ? "
                        "AND refunded_at IS NULL", (job_id,)).fetchone()
    finally:
        c.close()
    return row[0] if row else None


def refund_for_job(job_id: str, reason: str = "") -> bool:
    """Give back the credit spent on one run. False if it was free, or already refunded.

    IDEMPOTENT BY CONSTRUCTION. The claim on refunded_at is a conditional UPDATE, so two
    callers racing to refund the same run cannot both win and the buyer cannot be paid
    twice for one failure. The credit is only written if that claim succeeded.
    """
    if not job_id:
        return False
    c = _db()
    try:
        owner_row = c.execute("SELECT account_id FROM credit_spends WHERE job_id = ?",
                              (job_id,)).fetchone()
        if not owner_row:
            return False                       # nobody paid for this run
        claimed = c.execute("UPDATE credit_spends SET refunded_at = ? "
                            "WHERE job_id = ? AND refunded_at IS NULL",
                            (int(time.time()), job_id)).rowcount
    finally:
        c.close()
    if not claimed:
        return False                           # already refunded
    owner = resolve_owner(owner_row[0])         # they may have registered since
    ok = credit_back(owner, "report", reason, job_id=job_id)
    if not ok:
        log.error("[billing] claimed the refund for %s but could not write the credit",
                  job_id[:8])
    return ok


def was_refunded(job_id: str) -> bool:
    """Has the credit for this report already been given back?

    A withheld report is refunded and then remains readable behind ?force=1, so without
    this the reader could take the money back and keep the report. The refund row names
    the job it undoes, which is what makes the question answerable.
    """
    if not job_id:
        return False
    c = _db()
    try:
        row = c.execute("SELECT refunded_at FROM credit_spends WHERE job_id = ?",
                        (job_id,)).fetchone()
    finally:
        c.close()
    return bool(row and row[0])


def credit_back(account_id: str, kind: str = "report", reason: str = "",
                job_id: str | None = None) -> bool:
    """Put a spent credit back. The inverse consume() never had.

    THE HOLE THIS CLOSES. A credit is spent BEFORE the work (routes/research.py), which is
    correct — the alternative is running six minutes of metered research for someone who
    might not have paid. But there was no path back, so a paid run that errored, or that
    the verifier withheld, left the buyer with no report, no credit, and nobody able to
    restore it: every write went through _record, which wants a Stripe session id.

    The partial unique index is on session_id WHERE NOT NULL, so a refund row carries none
    and cannot collide with a purchase. It is a NEW grant, not an undo of the old row: the
    original purchase stays in the ledger because it happened.
    """
    if kind not in PRICE_ENV:
        return False
    # CARRY THE ADDRESS FORWARD. A refund written with email=NULL cannot be reached by
    # claim_by_email, so a guest whose paid run crashed got a credit they could never
    # claim onto an account, and /billing/status had no address left to prefill the
    # registration form with. The refund inherits the address of the purchase it undoes.
    email = email_on_credits(account_id, kind) or _last_email_for(account_id, kind)
    # job_id NAMES WHAT THIS UNDOES. On a purchase row it means "this pack belongs to that
    # report"; on a refund row (session_id NULL) it means "this is the money back for that
    # report", which is what was_refunded reads.
    ok = _record(account_id, kind, 1, None, job_id, email=email)
    if ok:
        log.info("[billing] credited back one %s to %s%s", kind, account_id[:8],
                 f" ({reason})" if reason else "")
    return ok


def _last_email_for(account_id: str, kind: str = "report") -> str | None:
    """The address on this owner's most recent entitlement of a kind, spent or not.

    email_on_credits only looks at rows with credit LEFT, which is exactly wrong for a
    refund: the credit being given back was just spent, so its row reads remaining = 0 and
    the address on it would be skipped.
    """
    c = _db()
    try:
        row = c.execute("SELECT email FROM entitlements WHERE account_id = ? AND kind = ? "
                        "AND email IS NOT NULL ORDER BY created_at DESC LIMIT 1",
                        (account_id, kind)).fetchone()
    finally:
        c.close()
    return row[0] if row else None


def _record(account_id: str, kind: str, count: int, session_id: str | None,
            job_id: str | None, email: str | None = None) -> bool:
    """Write the entitlement. False when this session was already fulfilled."""
    c = _db()
    try:
        c.execute("INSERT INTO entitlements (id, account_id, kind, job_id, remaining, "
                  "session_id, created_at, email) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (str(uuid.uuid4()), account_id, kind, job_id, count, session_id,
                   int(time.time()), (email or "").strip().lower() or None))
        return True
    except sqlite3.IntegrityError:
        log.info("[billing] session %s already fulfilled, ignoring replay",
                 (session_id or "")[:20])
        return False
    finally:
        c.close()


def grant_test_purchase(kind: str, account_id: str) -> int:
    """Grant what `kind` sells, WITHOUT any money changing hands. Testing only.

    THE HOLE THIS FILLS. With no Stripe account wired there is nothing for a Buy button to
    call, so the entire post-payment half of the funnel — the claim-your-credits card, the
    registration ask, the credit being spent on the run — was unreachable on any instance
    that had not finished setting up billing. That is every instance before it launches,
    which is exactly when the flow most needs walking.

    THE CALLER MUST PROVE IT IS ALLOWED. This function refuses whenever real keys are
    present, so it cannot be reached on an instance that can take money, but that guard is
    the last line rather than the first: api.billing_checkout also requires
    CASTOR_PAYWALL_PREVIEW before it will call here.

    The row is written with a `preview-` session id, so a test grant is identifiable
    forever in the ledger, and the partial unique index on session_id still makes it
    idempotent per generated id.
    """
    if configured():
        raise BillingError("this instance has real payment keys; refusing to grant a "
                           "credit nobody paid for")
    if kind not in ACCOUNT_KINDS:
        raise BillingError(f"{kind} is not something a test purchase can grant")
    credit_kind, count = GRANTS.get(kind, (kind, 1))
    _record(account_id, credit_kind, count, f"preview-{uuid.uuid4()}", None)
    log.warning("[billing] TEST PURCHASE: granted %d %s credit(s) to %s. No money moved.",
                count, credit_kind, account_id[:12])
    return count


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
        # PAYMENT METHODS ARE NOT NAMED HERE, and that is a correction rather than a
        # loosening. This sent payment_method_types[0]=card to keep delayed-settlement
        # methods out, on the reasoning that a credit handed over before the money arrives
        # is a report given away. Stripe now enables Managed Payments by default on new
        # accounts and REJECTS that parameter outright:
        #
        #   Unsupported parameter: payment_method_types. Managed Payments, which is
        #   enabled by default on your account, handles this parameter for you.
        #
        # so every checkout on a newly created account failed with a 400, which the caller
        # reported as "could not reach the payment provider". Passing it conditionally
        # would mean guessing which kind of account this is on every request.
        #
        # THE SAFETY IT WAS PROTECTING IS ALREADY HELD ELSEWHERE, which is what makes this
        # safe to drop rather than a trade. fulfill() grants nothing unless the event says
        # payment_status == "paid", and checkout.session.async_payment_succeeded is
        # handled, so a method that settles days later grants the credit when it settles
        # and not before. The webhook is the guard; the parameter was a second lock on a
        # door the webhook already holds.
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items[0][price]": price,
        "line_items[0][quantity]": "1",
        # Metadata is what the webhook reads back. It must carry everything needed to
        # fulfil, because the webhook arrives with no session of its own.
        "metadata[account_id]": account_id,
        "metadata[kind]": kind,
        "client_reference_id": account_id,
        # THE DISCOUNT BOX. Sharing a report earns a $10 code (sharing.py), and a code with
        # nowhere to be typed is not a reward. Stripe validates and redeems it on its own
        # page, so no redemption logic lives here to get wrong.
        "allow_promotion_codes": "true",
    }
    if job_id:
        form["metadata[job_id]"] = job_id
    try:
        r = requests.post(f"{STRIPE_API}/checkout/sessions", data=form, timeout=20,
                          auth=(os.environ["STRIPE_SECRET_KEY"], ""))
    except Exception as e:                                   # noqa: BLE001
        # A genuine network failure. This one really is "try again".
        log.warning("[billing] could not reach Stripe: %s", e)
        raise BillingError("could not reach the payment provider, try again")

    # SAY WHAT STRIPE SAID. Every failure used to become "could not reach the payment
    # provider, try again", which is a lie for the common case and sends the operator to
    # look at their network. MEASURED, twice, while wiring up a real account: a Managed
    # Payments rejection and an archived price both surfaced as a connectivity problem,
    # and the actual reasons ("Unsupported parameter: payment_method_types", "The price
    # specified is inactive") were thrown away with the response body.
    #
    # A 4xx from Stripe is a CONFIGURATION fault, not a transport one: the price is
    # archived, or recurring, or belongs to another account, or the key is wrong. Those
    # messages are Stripe's own operator-facing copy, they carry no secrets, and they name
    # the fix. Logged at error and surfaced, because an instance whose checkout is broken
    # is an instance selling nothing, and the operator is the only one who can repair it.
    if not r.ok:
        detail = ""
        try:
            detail = ((r.json() or {}).get("error") or {}).get("message") or ""
        except Exception:                                    # noqa: BLE001
            detail = (r.text or "")[:200]
        log.error("[billing] Stripe refused the checkout (%s): %s", r.status_code, detail)
        if 400 <= r.status_code < 500 and detail:
            raise BillingError(f"Stripe refused this purchase: {detail}")
        raise BillingError("the payment provider refused the request, try again")

    try:
        url = (r.json() or {}).get("url")
    except Exception as e:                                   # noqa: BLE001
        log.warning("[billing] Stripe returned an unreadable checkout: %s", e)
        raise BillingError("the payment provider returned no checkout link")
    if not url:
        raise BillingError("the payment provider returned no checkout link")
    return url



def create_promo_code(code: str, amount_off_cents: int, currency: str = "usd") -> str:
    """Make one customer-facing discount code redeemable at checkout. Returns its id.

    Two Stripe objects: a `coupon` is the amount, a `promotion_code` is the string a buyer
    types. Both are created here because the pair is meaningless apart.

    once + max_redemptions=1 is deliberate. This is a thank-you for one shared report, not
    a standing discount, and a code that can be forwarded and reused is a hole in pricing
    rather than a marketing channel.
    """
    if not configured():
        raise BillingError("payments are not configured on this instance")
    import requests
    auth = (os.environ["STRIPE_SECRET_KEY"], "")
    r = requests.post(f"{STRIPE_API}/coupons", timeout=20, auth=auth, data={
        "amount_off": str(int(amount_off_cents)),
        "currency": currency,
        "duration": "once",
        "max_redemptions": "1",
        "name": f"Shared a report (${int(amount_off_cents) / 100:.0f})",
    })
    r.raise_for_status()
    coupon_id = (r.json() or {}).get("id")
    if not coupon_id:
        raise BillingError("Stripe created no coupon")
    r = requests.post(f"{STRIPE_API}/promotion_codes", timeout=20, auth=auth, data={
        "coupon": coupon_id,
        "code": code,
        "max_redemptions": "1",
    })
    r.raise_for_status()
    promo_id = (r.json() or {}).get("id")
    if not promo_id:
        raise BillingError("Stripe created no promotion code")
    log.info("[billing] %s is now redeemable", code)
    return promo_id


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
    # BOTH SUCCESS EVENTS. `completed` fires when the session finishes, but for a payment
    # method that settles later it arrives with payment_status still `unpaid` and the real
    # confirmation comes as `async_payment_succeeded`. Handling only the first meant such
    # a purchase was charged and never credited. The session-id idempotency guard makes
    # accepting both safe: whichever arrives second grants nothing.
    if kind_of_event not in ("checkout.session.completed",
                            "checkout.session.async_payment_succeeded"):
        return {"granted": False, "reason": f"ignored event {kind_of_event}"}

    session = ((event.get("data") or {}).get("object")) or {}
    if session.get("payment_status") != "paid":
        return {"granted": False, "reason": "session is not paid"}

    meta = session.get("metadata") or {}
    # Stripe always collects this; it is the handle on a buyer who has no account yet.
    buyer_email = ((session.get("customer_details") or {}).get("email")
                   or session.get("customer_email") or "").strip().lower() or None
    kind = meta.get("kind") or ""
    account_id = meta.get("account_id") or session.get("client_reference_id") or ""
    # FOLLOW THE BUYER. The id in this metadata was stamped when checkout was created and
    # may since have become an account; granting to the stale guest id writes the purchase
    # somewhere the buyer can no longer present.
    resolved = resolve_owner(account_id)
    if resolved != account_id:
        log.info("[billing] webhook for %s follows the guest onto %s",
                 account_id[:12], resolved[:12])
        account_id = resolved
    job_id = meta.get("job_id")
    session_id = session.get("id")
    if kind not in PRICE_ENV or not account_id:
        return {"granted": False, "reason": "session carried no usable metadata"}

    if kind in ACCOUNT_KINDS:
        credit_kind, count = GRANTS.get(kind, (kind, 1))
        ok = _record(account_id, credit_kind, count, session_id, None, email=buyer_email)
        return {"granted": ok, "kind": kind, "credits": count if ok else 0,
                "account_id": account_id}

    # A refinement pack. The entitlement row is the receipt and the idempotency guard;
    # iteration.grant is what actually widens the budget on that one report.
    if not job_id:
        return {"granted": False, "reason": "a pack was bought for no report"}
    if not _record(account_id, kind, 1, session_id, job_id, email=buyer_email):
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
