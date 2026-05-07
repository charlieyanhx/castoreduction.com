"""
tools/domain.py — registered domain-validation utilities.

Used to filter out parked domains, confirm a domain resolves to a real site,
estimate domain age, and probe URL patterns when a brand-name guess is needed.
"""
from __future__ import annotations
from .registry import tool, Evidence


@tool(category="domain", returns="bool")
def is_parked_domain(domain: str) -> Evidence:
    """True if domain is parked / for-sale / GoDaddy / Sedo placeholder."""
    from sources import is_parked_domain as _impl
    parked = bool(_impl(domain))
    return Evidence(
        source="is_parked_domain", category="domain",
        count=1, payload=parked, cost_meta={"domain": domain},
    )


@tool(category="domain", returns="bool")
def validate_domain(domain: str) -> Evidence:
    """Verify the domain resolves and serves real content (not parked, not 404)."""
    from sources import validate_domain as _impl
    valid = bool(_impl(domain))
    return Evidence(
        source="validate_domain", category="domain",
        count=1, payload=valid, cost_meta={"domain": domain},
    )


@tool(category="domain", returns="list[str URLs]")
def probe_domain_patterns(brand: str, max_candidates: int = 5) -> Evidence:
    """Generate plausible domains for a brand (.com, .io, .ai, etc) and check liveness."""
    from sources import probe_domain_patterns as _impl
    candidates = _impl(brand, max_candidates=max_candidates) or []
    return Evidence(
        source="probe_domain_patterns", category="domain",
        count=len(candidates), payload=candidates,
    )


@tool(category="domain", returns="str domain or None")
def resolve_brand_domain(brand: str) -> Evidence:
    """Best-effort: resolve a brand name to its primary domain."""
    from sources import resolve_brand_domain as _impl
    domain = _impl(brand)
    return Evidence(
        source="resolve_brand_domain", category="domain",
        count=1 if domain else 0, payload=domain,
    )


@tool(category="domain", returns="int days or None")
def estimate_domain_age_days(domain: str) -> Evidence:
    """RDAP-based domain age in days — proxy for company age when firmographics fails."""
    from sources import estimate_domain_age_days as _impl
    age = _impl(domain)
    return Evidence(
        source="estimate_domain_age_days", category="domain",
        count=1 if age is not None else 0,
        payload=age,
        cost_meta={"domain": domain, "age_years": round(age / 365, 1) if age else None},
    )
