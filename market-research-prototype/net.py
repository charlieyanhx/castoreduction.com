"""
Retry-wrapped HTTP helpers. Used by sources.py instead of requests.* directly.

Uses tenacity (verified OSS, 6.7k+ stars, Apache 2.0) for retry semantics —
exponential backoff, jitter, retry on specific conditions.

Retries on transient failures (connection errors, timeouts, 408/425/429/5xx).
Does NOT retry on permanent failures (4xx except 408/425/429).
"""
from __future__ import annotations
import requests
from tenacity import (
    retry, stop_after_attempt, wait_exponential, wait_random,
    retry_if_exception_type, retry_if_result, before_sleep_log,
    RetryError,
)
import logging

from logger import log


RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
DEFAULT_MAX_RETRIES = 3
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
DEFAULT_HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}


def _should_retry_response(r: requests.Response) -> bool:
    """True if response status code is in the transient-error set."""
    return r.status_code in RETRY_STATUS


def request(
    method: str,
    url: str,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff: float = 2.0,
    timeout: int = 20,
    headers: dict | None = None,
    **kwargs,
) -> requests.Response:
    """
    requests.request with tenacity-powered retry on transient failures.
    Returns the final response (even if still failing) after retries exhausted.
    Raises for connection/timeout errors after exhausting retries.
    """
    merged_headers = dict(DEFAULT_HEADERS)
    if headers:
        merged_headers.update(headers)

    # Build a retry decorator with the caller's config
    # stop: after (max_retries + 1) attempts total
    # wait: exponential with jitter — 2s, 4s, 8s + random(0, 1)
    # retry: on connection/timeout exceptions OR on retryable status codes
    retry_decorator = retry(
        stop=stop_after_attempt(max_retries + 1),
        wait=wait_exponential(multiplier=backoff, min=backoff, max=60) + wait_random(0, 1),
        retry=(
            retry_if_exception_type((requests.Timeout, requests.ConnectionError))
            | retry_if_result(_should_retry_response)
        ),
        before_sleep=before_sleep_log(logging.getLogger("mrp"), logging.WARNING),
        reraise=True,
    )

    @retry_decorator
    def _do_request():
        return requests.request(
            method, url, timeout=timeout, headers=merged_headers, **kwargs
        )

    try:
        return _do_request()
    except RetryError as e:
        # Last attempt returned a retryable status code — extract and return it
        # so callers can inspect the final status rather than catching an exception.
        if e.last_attempt and not e.last_attempt.failed:
            last = e.last_attempt.result()
            log.debug(
                "http %s %s → %s (retries exhausted)",
                method, _short(url), getattr(last, "status_code", "?")
            )
            return last
        # Last attempt raised — re-raise the original exception
        raise


def get(url: str, **kwargs) -> requests.Response:
    return request("GET", url, **kwargs)


def post(url: str, **kwargs) -> requests.Response:
    return request("POST", url, **kwargs)


def head(url: str, **kwargs) -> requests.Response:
    return request("HEAD", url, **kwargs)


def _short(url: str, max_len: int = 80) -> str:
    return url if len(url) <= max_len else url[: max_len - 3] + "..."
