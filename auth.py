"""auth.py — accounts, password storage and signed sessions.

The identity half of the ownership model. #93 added owner_id, scoped every read path and
left api._current_owner() returning a constant on purpose: ownership had to exist in the
data model before identity, or a login screen proves who you are while the query still
returns everyone's rows. This supplies the identity; because the reads are already scoped,
wiring it in changes one function.

NO NEW DEPENDENCY. hashlib.scrypt (RFC 7914) is a memory-hard KDF in the standard library,
and hmac + secrets sign the session. passlib/bcrypt/itsdangerous would each add supply
chain to a project that audits its own, for something stdlib already does correctly.

WHAT THIS IS NOT: an identity provider. There is no email verification, password reset,
MFA, or OAuth here. Those are real requirements for a paid product and each one is a
decision about how much of the account lifecycle is worth owning — see #94. This is the
minimum that makes per-user isolation REAL rather than stubbed.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from pathlib import Path

from logger import get

log = get("auth")

# scrypt parameters. n=2**14 with r=8,p=1 is the interactive-login profile from RFC 7914 —
# ~100ms and ~16MB per verification here, which is a real cost to an attacker with a stolen
# database and an unnoticeable one to a person signing in.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1
_SALT_BYTES = 16
_MIN_PASSWORD = 12

SESSION_MAX_AGE_S = 30 * 24 * 3600     # 30 days
_SECRET_FILE = Path(__file__).parent / ".session_secret"


class PasswordTooWeak(ValueError):
    """Raised at the door rather than stored — a weak password is a permanent liability."""


# ---------------------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    """scrypt with a fresh random salt, encoded as scrypt$n$r$p$salt$hash.

    A per-user salt is what stops one cracked hash from cracking every reuse of that
    password, and stops equal hashes from announcing that two accounts share one.
    """
    if not isinstance(password, str) or len(password) < _MIN_PASSWORD:
        raise PasswordTooWeak(f"password must be at least {_MIN_PASSWORD} characters")
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R,
                        p=_SCRYPT_P, dklen=32)
    return "$".join(["scrypt", str(_SCRYPT_N), str(_SCRYPT_R), str(_SCRYPT_P),
                     base64.b64encode(salt).decode(), base64.b64encode(dk).decode()])


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time verification. Any malformed stored value fails CLOSED rather than
    raising — a corrupt row must not turn the login endpoint into a 500."""
    if not password or not isinstance(stored, str):
        return False
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64),
                            n=int(n), r=int(r), p=int(p), dklen=32)
    except Exception:
        return False
    return hmac.compare_digest(dk, base64.b64decode(hash_b64))


# ----------------------------------------------------------------------------- accounts
def _db() -> sqlite3.Connection:
    import jobs
    conn = sqlite3.connect(jobs._db_path() if hasattr(jobs, "_db_path")
                           else os.environ.get("JOBS_DB_PATH")
                           or str(Path(__file__).parent / ".jobs.sqlite"),
                           timeout=10, isolation_level=None)
    conn.execute("""CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL)""")
    # Google sign-in. Added by ALTER rather than by rebuilding the table: password_hash is
    # NOT NULL and existing rows depend on it, so an OAuth-only account stores the sentinel
    # below instead. verify_password fails closed on anything that is not a scrypt string,
    # so that value can never be logged into with a password, by anyone, ever.
    if "google_sub" not in {r[1] for r in conn.execute("PRAGMA table_info(accounts)")}:
        conn.execute("ALTER TABLE accounts ADD COLUMN google_sub TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_google_sub "
                     "ON accounts(google_sub) WHERE google_sub IS NOT NULL")
    return conn


#: Stored in password_hash for an account that has no password. Deliberately not a valid
#: scrypt string.
OAUTH_ONLY = "oauth-only-no-password"


def find_or_create_google_account(sub: str, email: str, email_verified: bool) -> str:
    """The account id for a Google identity, creating or linking as needed.

    THE LINKING RULE, and it is the whole security of this function: an existing
    password account is linked to a Google identity ONLY when Google says it has verified
    that address. Without that check, anyone able to make Google assert an unverified
    address could claim the matching account here and inherit its reports. Google verifies
    gmail and Workspace addresses; it does not always verify others, and the flag is the
    only way to tell from here.

    Three cases: known Google subject, an existing account with the same verified email
    (linked), or a new account with no password at all.
    """
    sub = (sub or "").strip()
    e = _norm_email(email)
    if not sub or "@" not in e:
        raise ValueError("google returned no usable identity")
    if not email_verified:
        raise ValueError("google has not verified that email address")

    c = _db()
    row = c.execute("SELECT id FROM accounts WHERE google_sub = ?", (sub,)).fetchone()
    if row:
        c.close()
        return row[0]

    existing = c.execute("SELECT id, google_sub FROM accounts WHERE email = ?",
                         (e,)).fetchone()
    if existing:
        if existing[1] and existing[1] != sub:
            # The address is already bound to a DIFFERENT Google subject. Rebinding it
            # would hand one person's account to another; refusing is the only safe move.
            c.close()
            raise ValueError("that email is already linked to another Google account")
        c.execute("UPDATE accounts SET google_sub = ? WHERE id = ?", (sub, existing[0]))
        c.close()
        log.info("account %s linked to google", existing[0][:8])
        return existing[0]

    acct_id = str(uuid.uuid4())
    try:
        c.execute("INSERT INTO accounts (id, email, password_hash, created_at, google_sub) "
                  "VALUES (?, ?, ?, ?, ?)",
                  (acct_id, e, OAUTH_ONLY, int(time.time()), sub))
    except sqlite3.IntegrityError:
        c.close()
        raise ValueError("could not create that account")
    c.close()
    log.info("account created via google %s", acct_id[:8])
    return acct_id


def _norm_email(email: str) -> str:
    return (email or "").strip().lower()


def _find_account(email: str) -> dict | None:
    c = _db()
    row = c.execute("SELECT id, email, password_hash FROM accounts WHERE email = ?",
                    (_norm_email(email),)).fetchone()
    c.close()
    return {"id": row[0], "email": row[1], "password_hash": row[2]} if row else None


def create_account(email: str, password: str) -> str:
    """Returns the new account id. Raises PasswordTooWeak or ValueError on a duplicate."""
    e = _norm_email(email)
    if "@" not in e:
        raise ValueError("a valid email is required")
    ph = hash_password(password)          # raises before anything is written
    acct_id = str(uuid.uuid4())
    c = _db()
    try:
        c.execute("INSERT INTO accounts (id, email, password_hash, created_at) "
                  "VALUES (?, ?, ?, ?)", (acct_id, e, ph, int(time.time())))
    except sqlite3.IntegrityError:
        c.close()
        raise ValueError("account already exists")
    c.close()
    log.info("account created %s", acct_id[:8])
    return acct_id


def authenticate(email: str, password: str) -> str | None:
    """The account id, or None.

    None for BOTH an unknown email and a wrong password, deliberately and with the same
    work done in each case: a caller that could tell them apart would turn the login form
    into a way to ask which addresses have accounts. The dummy verify on the miss keeps
    the timing from answering the same question.
    """
    acct = _find_account(email)
    if acct is None:
        verify_password(password, _DUMMY_HASH)      # equalise timing, discard result
        return None
    return acct["id"] if verify_password(password, acct["password_hash"]) else None


# A real hash of a random value, computed once, so the unknown-email path performs the
# same scrypt work as the wrong-password path.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(24))


# ----------------------------------------------------------------------------- sessions
#: One-shot tokens for the two flows that must reach an address we have not yet proven we
#: can reach. 1h for a reset, 24h for a confirmation.
RESET_TTL_S = 3600
VERIFY_TTL_S = 24 * 3600


def _tokens_db() -> sqlite3.Connection:
    conn = _db()
    conn.execute("""CREATE TABLE IF NOT EXISTS auth_tokens (
            token_hash TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            expires_at INTEGER NOT NULL,
            used_at INTEGER)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_auth_tokens_acct "
                 "ON auth_tokens(account_id, kind)")
    return conn


def issue_token(account_id: str, kind: str, ttl_s: int) -> str:
    """Mint a single-use token and return the SECRET half.

    STORED HASHED, like a password. The row is what an attacker reaches if they get the
    database, and a reset table full of usable links is a full account takeover of every
    pending request. Only the caller ever holds the plaintext, and only long enough to put
    it in an email.

    Issuing invalidates this account's earlier tokens of the same kind: two live reset
    links means an old email, forwarded or leaked, still works after the person asked
    again.
    """
    import secrets as _secrets
    token = _secrets.token_urlsafe(32)
    now = int(time.time())
    c = _tokens_db()
    try:
        c.execute("DELETE FROM auth_tokens WHERE account_id = ? AND kind = ?",
                  (account_id, kind))
        c.execute("INSERT INTO auth_tokens (token_hash, account_id, kind, expires_at) "
                  "VALUES (?, ?, ?, ?)",
                  (_hash_token(token), account_id, kind, now + int(ttl_s)))
        c.execute("DELETE FROM auth_tokens WHERE expires_at < ?", (now - 86400,))
    finally:
        c.close()
    return token


def spend_token(token: str, kind: str) -> str | None:
    """The account a valid unused token names, marking it used. None otherwise.

    Single use is enforced by the UPDATE's own WHERE clause, not by a read followed by a
    write: two tabs submitting the same reset link must not both succeed.
    """
    if not token:
        return None
    now = int(time.time())
    th = _hash_token(token)
    c = _tokens_db()
    try:
        row = c.execute("SELECT account_id FROM auth_tokens WHERE token_hash = ? "
                        "AND kind = ? AND used_at IS NULL AND expires_at > ?",
                        (th, kind, now)).fetchone()
        if not row:
            return None
        spent = c.execute("UPDATE auth_tokens SET used_at = ? WHERE token_hash = ? "
                          "AND used_at IS NULL", (now, th)).rowcount
        return row[0] if spent == 1 else None
    finally:
        c.close()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def set_password(account_id: str, new: str) -> None:
    """Set a password WITHOUT the old one. Reached only after spend_token has proved the
    holder controls the address, which is the other way of proving the account."""
    hashed = hash_password(new)
    c = _db()
    try:
        c.execute("UPDATE accounts SET password_hash = ? WHERE id = ?",
                  (hashed, account_id))
    finally:
        c.close()
    invalidate_sessions(account_id)


def account_by_email(email: str) -> dict | None:
    return _find_account(email)


def account_email(account_id: str) -> str | None:
    c = _db()
    try:
        row = c.execute("SELECT email FROM accounts WHERE id = ?", (account_id,)).fetchone()
    finally:
        c.close()
    return row[0] if row else None


def mark_email_verified(account_id: str) -> None:
    c = _db()
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(accounts)")}
        if "email_verified_at" not in cols:
            c.execute("ALTER TABLE accounts ADD COLUMN email_verified_at INTEGER")
        c.execute("UPDATE accounts SET email_verified_at = ? WHERE id = ?",
                  (int(time.time()), account_id))
    finally:
        c.close()


def email_is_verified(account_id: str) -> bool:
    c = _db()
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(accounts)")}
        if "email_verified_at" not in cols:
            return False
        row = c.execute("SELECT email_verified_at FROM accounts WHERE id = ?",
                        (account_id,)).fetchone()
    finally:
        c.close()
    return bool(row and row[0])


def invalidate_sessions(account_id: str) -> None:
    """Cut every session this account has already issued.

    THE REASON PEOPLE RESET A PASSWORD IS THAT SOMEBODY ELSE HAS ACCESS. Without this the
    intruder's cookie kept working for up to thirty days after the reset — including
    DELETE /auth/account — so the recovery flow completed without recovering anything.

    Implemented as a floor on issue time rather than a session table: these tokens are
    stateless by design, and one indexed lookup per request is a smaller price than a
    server-side session store. Anything minted before the floor is refused.
    """
    c = _db()
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(accounts)")}
        if "sessions_valid_from" not in cols:
            c.execute("ALTER TABLE accounts ADD COLUMN sessions_valid_from INTEGER")
        c.execute("UPDATE accounts SET sessions_valid_from = ? WHERE id = ?",
                  (int(time.time()), account_id))
    finally:
        c.close()


def _sessions_valid_from(account_id: str) -> int:
    c = _db()
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(accounts)")}
        if "sessions_valid_from" not in cols:
            return 0
        row = c.execute("SELECT sessions_valid_from FROM accounts WHERE id = ?",
                        (account_id,)).fetchone()
    except sqlite3.Error:
        return 0
    finally:
        c.close()
    return int(row[0]) if row and row[0] else 0


def has_password(account_id: str) -> bool:
    """False for an account created through Google.

    create_account is the only path that sets a real hash; find_or_create_google_account
    stores OAUTH_ONLY, a deliberately invalid scrypt string. Callers that ask a user to
    prove themselves WITH a password must ask this first, or they present a form that can
    only ever answer "wrong".
    """
    h = password_hash_of(account_id)
    return bool(h) and not str(h).startswith("oauth-only")


def password_hash_of(account_id: str) -> str | None:
    """The stored hash, for a caller that must re-prove a password before something
    destructive. Returns None for an unknown account, which verify_password refuses."""
    c = _db()
    try:
        row = c.execute("SELECT password_hash FROM accounts WHERE id = ?",
                        (account_id,)).fetchone()
    finally:
        c.close()
    return row[0] if row else None


def change_password(account_id: str, current: str, new: str) -> None:
    """Set a new password, proving the old one first.

    THE CURRENT PASSWORD IS THE PROOF, not the session. A 30-day cookie on a shared or
    borrowed machine is exactly the case where "change my password" must not be a
    one-click account takeover, and this system has no second factor to fall back on.

    An OAuth-only account has no current password to prove (create_account was never the
    path that made it), so it cannot use this at all: `password_hash` is the deliberate
    sentinel and verify_password refuses it.
    """
    c = _db()
    try:
        row = c.execute("SELECT password_hash FROM accounts WHERE id = ?",
                        (account_id,)).fetchone()
    finally:
        c.close()
    if not row:
        raise ValueError("no such account")
    if not verify_password(current, row[0]):
        raise ValueError("current password is wrong")
    hashed = hash_password(new)          # raises PasswordTooWeak, same rule as signup
    c = _db()
    try:
        c.execute("UPDATE accounts SET password_hash = ? WHERE id = ?",
                  (hashed, account_id))
    finally:
        c.close()
    # Changing a password is the other half of "somebody else has access". Same floor.
    invalidate_sessions(account_id)


def delete_account(account_id: str) -> dict:
    """Erase an account and everything attached to it. Returns what went.

    EVERYTHING MEANS EVERYTHING, and the order matters: the account row goes LAST, so a
    failure part-way leaves an account that still owns its data rather than orphaned rows
    nobody can reach or delete. Reports, the refinement layer on each, unspent credits,
    quota history and unfinished drafts all name the owner, and a deletion that left any
    of them behind would be a deletion in the copy only.

    The caller proves identity. This function does not: it is reached from one route that
    has already re-checked the password.
    """
    gone = {}
    conn = _db()                     # same path resolution as every other reader here
    try:
        for table, col in (("jobs", "owner_id"), ("entitlements", "account_id"),
                           ("run_slots", "owner_id"), ("run_ledger", "owner_id"),
                           ("intake_sessions", "owner_id")):
            try:
                cur = conn.execute(f"DELETE FROM {table} WHERE {col} = ?", (account_id,))
                gone[table] = cur.rowcount or 0
            except sqlite3.OperationalError:
                gone[table] = 0                  # table not created on this instance yet
        cur = conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        gone["accounts"] = cur.rowcount or 0
    finally:
        conn.close()
    log.info("[auth] deleted account %s: %s", account_id[:8], gone)
    return gone


def _session_secret() -> str:
    """The signing key.

    Production must supply SESSION_SECRET. A shipped default would mean anyone who can
    read the source can mint a session for any account, which is not a weaker system —
    it is no system. Local development gets a generated key persisted beside the database
    so restarting the server does not log you out.
    """
    env = os.environ.get("SESSION_SECRET", "").strip()
    if env:
        return env
    if os.environ.get("CASTOR_ENV", "").lower() == "production":
        raise RuntimeError(
            "SESSION_SECRET is required when CASTOR_ENV=production — refusing to sign "
            "sessions with a generated local key")
    try:
        if _SECRET_FILE.exists():
            return _SECRET_FILE.read_text().strip()
        s = secrets.token_urlsafe(48)
        _SECRET_FILE.write_text(s)
        _SECRET_FILE.chmod(0o600)
        log.warning("generated a local dev session secret at %s — set SESSION_SECRET in "
                    "production", _SECRET_FILE.name)
        return s
    except OSError:
        return secrets.token_urlsafe(48)      # ephemeral; sessions die on restart


def _sign(payload_b64: str) -> str:
    return base64.urlsafe_b64encode(
        hmac.new(_session_secret().encode(), payload_b64.encode(),
                 hashlib.sha256).digest()).decode().rstrip("=")


def make_session_token(account_id: str) -> str:
    """A signed session token for this account: base64(payload).hmac.

    Carries only the account id and issue time. Nothing secret is inside, so the token is
    readable by anyone holding it; the signature is what makes it unforgeable, and `iat`
    is what lets read_session_token expire it.
    """
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": account_id, "iat": int(time.time())}).encode()
    ).decode().rstrip("=")
    return f"{payload}.{_sign(payload)}"


def read_session_token(token: str | None) -> str | None:
    """The account id a valid, unexpired, correctly-signed token names — else None.

    A GUEST TOKEN IS NOT A SESSION. Both are signed with the same key, so without the
    `typ` check below a guest cookie pasted into the session cookie's slot would verify
    and name its guest id as an account. Guest ids are uuid4 and account ids are uuid4,
    so nothing real would be reached — but "nothing real would be reached" is an argument
    about today's id format, not a guarantee, and this is the function that decides who
    someone is. It checks the type.
    """
    data = _read_token(token, want="session")
    if not data:
        return None
    sub = str(data["sub"]) or None
    if not sub:
        return None
    # THE FLOOR. A token minted before the account's last password change is refused,
    # which is what makes a reset actually evict whoever else was signed in.
    try:
        if float(data.get("iat") or 0) < _sessions_valid_from(sub):
            return None
    except Exception:                                        # noqa: BLE001
        return None
    return sub


def make_guest_token(guest_id: str) -> str:
    """A signed token for a visitor who has not registered.

    THE POINT IS ISOLATION, NOT SECURITY. It carries no privilege: it names one anonymous
    library so two strangers on the same instance do not share a workspace, which is what
    the shared LEGACY_OWNER fallback did. Signed rather than a bare uuid so a visitor
    cannot type someone else's guest id into their own cookie and read their reports.
    """
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": guest_id, "iat": int(time.time()), "typ": "guest"}).encode()
    ).decode().rstrip("=")
    return f"{payload}.{_sign(payload)}"


def read_guest_token(token: str | None) -> str | None:
    """The guest id a valid, unexpired, correctly-signed guest token names — else None."""
    data = _read_token(token, want="guest")
    return (str(data["sub"]) or None) if data else None


def _read_token(token: str | None, *, want: str) -> dict | None:
    """Verify signature, expiry and type. The one place tokens are opened."""
    if not token or not isinstance(token, str) or token.count(".") != 1:
        return None
    payload_b64, sig = token.split(".")
    if not hmac.compare_digest(_sign(payload_b64), sig):
        return None
    try:
        pad = "=" * (-len(payload_b64) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload_b64 + pad))
        # Session tokens predate `typ` and carry none; absent means session.
        if (data.get("typ") or "session") != want:
            return None
        if time.time() - float(data["iat"]) > SESSION_MAX_AGE_S:
            return None
        return data if data.get("sub") else None
    except Exception:
        return None
