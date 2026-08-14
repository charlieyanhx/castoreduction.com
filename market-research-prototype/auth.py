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
    return conn


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
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": account_id, "iat": int(time.time())}).encode()
    ).decode().rstrip("=")
    return f"{payload}.{_sign(payload)}"


def read_session_token(token: str | None) -> str | None:
    """The account id a valid, unexpired, correctly-signed token names — else None."""
    if not token or not isinstance(token, str) or token.count(".") != 1:
        return None
    payload_b64, sig = token.split(".")
    if not hmac.compare_digest(_sign(payload_b64), sig):
        return None
    try:
        pad = "=" * (-len(payload_b64) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload_b64 + pad))
        if time.time() - float(data["iat"]) > SESSION_MAX_AGE_S:
            return None
        return str(data["sub"]) or None
    except Exception:
        return None
