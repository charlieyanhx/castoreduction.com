"""
Error taxonomy. Every module raises one of these so callers can branch
cleanly on "retry vs skip vs abort".

Hierarchy:
  MRPError
  ├── TransientError   — worth retrying (network, rate limit)
  ├── PermanentError   — will not succeed on retry (4xx, parse failure)
  │   ├── DataError    — structured data was wrong/missing
  │   └── AuthError    — missing/invalid credentials
  └── ConfigError      — setup problem (missing env var, bad file)

Catch `MRPError` at top-level for user-facing messages;
catch specific subclasses in retry loops.
"""
from __future__ import annotations


class MRPError(Exception):
    """Base for all market-research-prototype errors."""

    def to_dict(self) -> dict:
        return {
            "error_class": self.__class__.__name__,
            "message": str(self),
        }


class TransientError(MRPError):
    """Network blip, rate limit, temporary 5xx. Retry with backoff."""


class PermanentError(MRPError):
    """4xx, parse failure, or logic error. Don't retry — fix the cause."""


class DataError(PermanentError):
    """Structured data was malformed, missing a required field, or empty."""


class AuthError(PermanentError):
    """Missing/invalid API key or token."""


class ConfigError(MRPError):
    """Setup problem — missing env var, bad file path, unsupported version."""
