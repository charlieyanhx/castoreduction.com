"""url_guard.py: may this process fetch that URL?

THE HOLE THIS CLOSES. `POST /taste` accepts a `domain` string from the client and the
pipeline fetches `https://{domain}` (taste.scrape_homepage_testimonials), extracts up to
5000 characters of the response, and stores it on the job, which the caller then reads
back from `GET /jobs/{id}`. Neither `net.request` nor `scrape.http.request` inspected the
URL, so that is a full-read SSRF: any internal service that speaks https was reachable,
and an http-only target (cloud metadata at 169.254.169.254, an admin port on loopback)
was reachable in one hop because `requests` follows cross-scheme redirects by default.

TWO RULES, and the second is the one that is usually missed:

  1. The URL must name a PUBLIC address. Scheme in {http, https}, hostname resolves, and
     no resolved address is loopback / private / link-local / reserved / multicast.

  2. EVERY REDIRECT HOP IS A NEW URL and gets rule 1 applied again, BEFORE the connection
     is made. Handing `allow_redirects=True` to requests and validating only the first
     URL checks the one address an attacker does not control. So the guard drives the
     redirect chain itself and each hop is a fresh decision.

A host that resolves to BOTH public and private addresses is refused rather than filtered
down to its public answers: that shape is DNS rebinding's calling card, and no legitimate
site we fetch needs it.

RESIDUAL RISK, stated rather than papered over: this resolves the name and then requests
connects, which re-resolves. A record that changes between those two moments (classic DNS
rebinding) is not closed by this module. Closing it means pinning the checked IP into the
socket via a custom transport adapter. That is the right next step if this ever guards
something more sensitive than an outbound scrape; it is deliberately not built today.

ESCAPE HATCH: CASTOR_ALLOW_PRIVATE_FETCH=1 disables rule 1 for local development against
a service on loopback. It is OFF by default, so production fails closed without anyone
remembering to configure it.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urljoin, urlparse

ALLOWED_SCHEMES = {"http", "https"}
MAX_REDIRECT_HOPS = 5


class BlockedAddress(ValueError):
    """The URL names an address this process may not reach.

    A ValueError subclass on purpose: callers that already treat a malformed URL as a
    permanent (non-retryable) failure get the right behaviour without a new except.
    """


def _private_reason(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Why this address is off limits, or None if it is a fine public address."""
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link-local (cloud metadata lives here)"
    if ip.is_private:
        return "private (RFC1918/ULA)"
    if ip.is_reserved:
        return "reserved"
    if ip.is_multicast:
        return "multicast"
    if ip.is_unspecified:
        return "unspecified"
    return None


def _as_ip(host: str):
    """`host` as an ip address object, or None if it is a name.

    Strips the brackets RFC 3986 puts around an IPv6 literal in a URL. safe_domain used
    to call ipaddress.ip_address() on the raw string, so "[::1]" raised ValueError, fell
    through to the hostname branch and was ACCEPTED as an ordinary domain. urlparse
    already strips them, which is why the fetch-time check caught it and the door did not.
    One parser, used by both, so they cannot disagree again.
    """
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def _resolve(host: str) -> list[str]:
    """Every address this hostname answers with. Raises BlockedAddress if it answers
    with none, because a name we cannot resolve is a fetch we cannot vet."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise BlockedAddress(f"{host!r} does not resolve: {e}") from e
    return sorted({i[4][0] for i in infos})


def assert_public_url(url: str) -> None:
    """Raise BlockedAddress unless `url` names a public http(s) address.

    Silent on success so it reads as an assertion at the call site.
    """
    if os.environ.get("CASTOR_ALLOW_PRIVATE_FETCH") == "1":
        return

    parts = urlparse(url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise BlockedAddress(f"scheme {parts.scheme!r} is not fetchable (http/https only)")

    host = parts.hostname
    if not host:
        raise BlockedAddress(f"no host in {url!r}")

    # A literal IP skips resolution; a name gets every address it answers with.
    literal = _as_ip(host)
    addrs = [str(literal)] if literal is not None else _resolve(host)

    blocked = []
    for a in addrs:
        reason = _private_reason(ipaddress.ip_address(a))
        if reason:
            blocked.append(f"{a} ({reason})")
    if blocked:
        raise BlockedAddress(
            f"{host!r} resolves to a non-public address: {', '.join(blocked)}")


def fetch_guarded(send, method: str, url: str, *, allow_redirects: bool = True,
                  max_hops: int = MAX_REDIRECT_HOPS, **kwargs):
    """Drive `send` through the redirect chain, vetting every hop.

    `send` performs ONE request and must not follow redirects itself; it is called as
    send(method, url, **kwargs) and returns a requests.Response (or None, which
    scrape.http does on failure).

    When allow_redirects is False this is just "vet, then send once": the caller wanted
    the 3xx itself.
    """
    assert_public_url(url)
    resp = send(method, url, **kwargs)
    if not allow_redirects:
        return resp

    hops = 0
    while resp is not None and hops < max_hops:
        # `is True` and isinstance, not truthiness. requests sets is_redirect to a real
        # bool and Location to a real str; a test double sets neither, and a Mock is
        # truthy, so duck-typing here walked the redirect branch on fake data and blew up
        # inside urljoin. Anything that is not unmistakably a redirect is the final
        # response.
        if getattr(resp, "is_redirect", False) is not True:
            break
        headers = getattr(resp, "headers", None)
        location = headers.get("location") if hasattr(headers, "get") else None
        if not isinstance(location, str) or not location:
            break
        base = getattr(resp, "url", None)
        nxt = urljoin(base, location) if isinstance(base, str) else location
        # THE POINT OF THIS MODULE: the new URL is vetted before it is opened.
        assert_public_url(nxt)
        # 303, and 302/301 on a POST, become GET, mirroring requests' own behaviour so
        # following the chain by hand does not change what the server sees.
        if resp.status_code in (301, 302, 303) and method.upper() not in ("GET", "HEAD"):
            method = "GET"
            kwargs.pop("data", None)
            kwargs.pop("json", None)
        resp = send(method, nxt, **kwargs)
        hops += 1
    return resp


def safe_domain(value: str) -> str:
    """Normalise a client-supplied domain, or raise BlockedAddress.

    The `domain` field on /taste arrives as free text with only a length check. This
    strips what a person naturally pastes (a scheme, a trailing slash, a path) and then
    insists the remainder is a bare public hostname, so the SSRF is refused at the door
    with a 400 as well as at the socket.
    """
    d = (value or "").strip()
    if not d:
        raise BlockedAddress("empty domain")
    if "://" in d:
        d = urlparse(d).netloc or d.split("://", 1)[1]
    d = d.split("/")[0].split("?")[0].strip().rstrip(".").lower()
    if not d or " " in d:
        raise BlockedAddress(f"{value!r} is not a hostname")

    # SYNTAX ONLY. This deliberately does NOT resolve the name.
    #
    # It used to, and that was wrong twice over: it put a blocking DNS lookup inside
    # request parsing, and it turned "does not resolve right this second" into a refusal,
    # which rejected the perfectly ordinary reserved-TLD host a test posts and would
    # equally reject a real customer's domain during a DNS blip. Neither is an attack.
    #
    # The SSRF is closed at fetch time by assert_public_url, which is the only place the
    # answer can be authoritative anyway, because the address is what matters and it is
    # read at connect time. What is worth catching here is the shape a person types when
    # they are aiming at us: a bare private/loopback literal, or localhost by name.
    # Strip a :port before asking whether the rest is an address. Only for a single
    # colon: more than one means an IPv6 literal, which _as_ip handles via its brackets.
    host_only = (d.rsplit(":", 1)[0]
                 if d.count(":") == 1 and d.rsplit(":", 1)[1].isdigit() else d)
    ip = _as_ip(host_only)
    if ip is None:
        if host_only == "localhost" or host_only.endswith(".localhost"):
            raise BlockedAddress(f"{value!r} names the local machine")
        return d
    reason = _private_reason(ip)
    if reason:
        raise BlockedAddress(f"{value!r} is a non-public address ({reason})")
    return d
