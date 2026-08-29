"""source_tiers.py — what KIND of source is this, and what is it entitled to do?

Castor already records WHERE every figure came from (persistence/ledger.py, the
provenance panel, report/citation.py). Nothing recorded whether that origin carries any
AUTHORITY. `Method.source` (report/forecast.py) is free text, and `triangulate` counts
independent origins without asking what each origin IS — so a syndicated storefront that
republishes another storefront can sit in the headline arithmetic wearing the same
authority as a federal filing.

The roster below is harvested, not invented: it is the measured citation universe of the
two competitors' full public corpora (31 reports, 5,151 citations, 1,247 domains,
2026-08-19/20) plus the government and filing hosts our own tools already fetch.

The tiers, and the one question each answers:

  PRIMARY       a filing, a regulator, a statistics agency, an issuer's own IR page.
  RESEARCH      a named research house. Usable as ONE triangulation path, never as proof.
  PRESS         reported journalism. Corroborates events; does not size markets.
  CONTENT_MILL  a long-tail market-research storefront. Resolves, but proves nothing.
  PADDING       vendor and framework documentation. Never evidence about a market.
  COMMUNITY     forums, review sites, social. Real customer VOICE, not market structure.
  UNKNOWN       unrecognised. Stays unknown; promotion-by-default is the bug this prevents.

This module DECLARES the tier. It does not enforce anything — what a caller does with a
CONTENT_MILL anchor is that caller's decision, made explicitly at its own call site, so
the rule stays visible where it bites instead of hiding in a fetch-time filter.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Optional
from urllib.parse import urlsplit


class Tier(Enum):
    PRIMARY = "primary"
    RESEARCH = "research"
    PRESS = "press"
    CONTENT_MILL = "content_mill"
    PADDING = "padding"
    COMMUNITY = "community"
    UNKNOWN = "unknown"


# Registrable domains, harvested from the competitor corpus census + our own fetchers.
# Matched by exact host or proper suffix — never by substring (see
# test_a_substring_is_not_an_identity for why that distinction is load-bearing).
_PRIMARY = {
    "sec.gov", "crunchbase.com", "pitchbook.com", "tracxn.com",
    "markets.businessinsider.com", "osm.org", "openstreetmap.org",
}
_RESEARCH = {
    "grandviewresearch.com", "mordorintelligence.com", "statista.com",
    "researchandmarkets.com", "marketsandmarkets.com", "gartner.com",
    "forrester.com", "idc.com", "nielsen.com", "mckinsey.com", "ibisworld.com",
    "euromonitor.com", "cbinsights.com", "pwc.com", "deloitte.com",
}
_PRESS = {
    "techcrunch.com", "cnbc.com", "reuters.com", "bloomberg.com", "forbes.com",
    "fortune.com", "wsj.com", "ft.com", "axios.com", "venturebeat.com",
    "businessinsider.com", "fastcompany.com", "theinformation.com", "finextra.com",
    "siliconangle.com", "inc.com",
}
# The measured failure surface: these supplied the headline TAM in most competitor
# reports. They resolve, so a citation-frequency audit scores them a hit.
_CONTENT_MILL = {
    "deepmarketinsights.com", "dataintelo.com", "emergenresearch.com",
    "verifiedmarketresearch.com", "wantstats.com", "mmrstatistics.com",
    "growthmarketreports.com", "imarcgroup.com", "precedenceresearch.com",
    "coherentmarketinsights.com", "htfmarketinsights.com", "htfmarketintelligence.com",
    "6wresearch.com", "apnaresearchplus.com", "market.us", "futuremarketinsights.com",
    "thebusinessresearchcompany.com", "fortunebusinessinsights.com",
    "marketdataforecast.com", "marketresearch.com", "globenewswire.com",
    "prnewswire.com", "businesswire.com",
}
# 32.4% of one competitor's 4,087 citations. figma.com appeared in 17 of 17 reports.
_PADDING = {
    "figma.com", "aws.amazon.com", "amazon.com", "stripe.com", "sentry.io",
    "datadoghq.com", "launchdarkly.com", "amplitude.com", "nextjs.org",
    "reactnative.dev", "react.dev", "postgresql.org", "docker.com", "nodejs.org",
    "redis.io", "tailwindcss.com", "mapbox.com", "twilio.com", "typescriptlang.org",
    "opentelemetry.io", "terraform.io", "kotlinlang.org", "go.dev", "vercel.com",
    "supabase.com", "mongodb.com", "postgis.net",
}
_COMMUNITY = {
    "reddit.com", "ycombinator.com", "indiehackers.com", "trustpilot.com",
    "x.com", "twitter.com", "facebook.com", "youtube.com", "quora.com",
    "producthunt.com", "g2.com", "capterra.com", "github.com", "medium.com",
    "stackoverflow.com", "glassdoor.com", "linkedin.com", "instagram.com",
}

_BY_TIER: tuple[tuple[frozenset, Tier], ...] = (
    (frozenset(_PRIMARY), Tier.PRIMARY),
    (frozenset(_CONTENT_MILL), Tier.CONTENT_MILL),
    (frozenset(_PADDING), Tier.PADDING),
    (frozenset(_COMMUNITY), Tier.COMMUNITY),
    (frozenset(_RESEARCH), Tier.RESEARCH),
    (frozenset(_PRESS), Tier.PRESS),
)

# `Method.source` is free text ("Gartner Digital Wellness 2024"), so a named house has to
# be recognisable without a URL. Whole-word only: a bare "IDC" is the report author, but
# "idc" inside another word is not.
_NAME_TIERS: tuple[tuple[re.Pattern, Tier], ...] = (
    (re.compile(r"\b(gartner|forrester|idc|nielsen|mckinsey|statista|ibisworld|"
                r"euromonitor|mordor|grand\s+view|cb\s*insights|"
                r"marketsandmarkets|euromonitor)\b"), Tier.RESEARCH),
    (re.compile(r"\b(sec\s+filing|edgar|10-k|10-q|s-1|census|bls|"
                r"bea|qcew|susb|eurostat)\b"), Tier.PRIMARY),
)


def _host(source: str) -> Optional[str]:
    """The lowercased hostname, whether `source` is a full URL or a bare domain."""
    s = (source or "").strip()
    if not s:
        return None
    if "://" not in s:
        # A bare domain has no spaces and at least one dot; anything else is prose.
        if " " in s or "." not in s:
            return None
        s = "//" + s
    try:
        host = (urlsplit(s).hostname or "").lower().strip(".")
    except ValueError:
        return None
    return host or None


def _matches(host: str, domains: frozenset) -> bool:
    """Exact host or a proper subdomain of one — never a substring.

    `notsec.gov.phishing.example` must not read as sec.gov, and `mystripe.com.example`
    must not read as stripe.com.
    """
    return any(host == d or host.endswith("." + d) for d in domains)


def classify(source: Optional[str]) -> Tier:
    """The tier of a source, given a URL, a bare domain, or a free-text citation."""
    text = (source or "").strip()
    if not text:
        return Tier.UNKNOWN

    host = _host(text)
    if host:
        # A government or issuer-relations host is authoritative by construction, and
        # neither list can practically be enumerated.
        if host.endswith(".gov") or host.startswith("ir."):
            return Tier.PRIMARY
        for domains, tier in _BY_TIER:
            if _matches(host, domains):
                return tier
        return Tier.UNKNOWN

    lowered = text.lower()
    for pattern, tier in _NAME_TIERS:
        if pattern.search(lowered):
            return tier
    return Tier.UNKNOWN


def can_anchor_sizing(tier: Tier) -> bool:
    """May a market figure REST on this source alone?

    Only a filing/agency or a named research house. Press corroborates events but does
    not size markets; a content mill resolves without proving anything.
    """
    return tier in (Tier.PRIMARY, Tier.RESEARCH)


def is_market_evidence(tier: Tier) -> bool:
    """Does this say anything about a MARKET — its size, structure, or competition?

    Community sources are real evidence about customer VOICE and deliberately fail this
    test; vendor documentation says nothing about any market at all.
    """
    return tier in (Tier.PRIMARY, Tier.RESEARCH, Tier.PRESS, Tier.CONTENT_MILL)
