"""gates/model.py — business-model bleed: a per-unit venture must never be shown subscription mechanics.

3 of the 61 deterministic detectors. Each takes (result, html) and
returns a Finding; none of them share state, which is why they split cleanly.
"""
from __future__ import annotations

from typing import Optional
from gates.common import Finding, not_applicable


PER_UNIT_KINDS = {"transactional", "ecommerce", "services", "hybrid"}


MONTHLY_UNITS = {"mo", "month", "monthly", "account", "seat"}


def d05_unit_no_monthly(r: dict, html: Optional[str]) -> Finding:
    """A per-unit venture is never described in monthly units.

    Model bleed: a bakery priced per loaf acquires subscription language somewhere in the
    chain and the report starts reasoning about churn it does not have. Checked across
    economics, financials and willingness-to-pay because the leak can enter at any of them.
    """
    if r.get("business_model_kind") not in PER_UNIT_KINDS:
        return not_applicable("not a per-unit model")
    units = {
        "economics": str((r.get("economics") or {}).get("unit") or ""),
        "financials": str(((r.get("financials") or {}).get("assumptions") or {}).get("unit") or ""),
        "wtp": str((((r.get("consumer_research") or {}).get("synthesis") or {})
                    .get("willingness_to_pay") or {}).get("unit") or "").lstrip("/"),
    }
    bad = {k: u for k, u in units.items() if u.lower() in MONTHLY_UNITS}
    return Finding(not bad, f"units={units}" + (f" MONTHLY BLEED: {bad}" if bad else ""))


def d06_html_no_saas_bleed(r: dict, html: Optional[str]) -> Finding:
    """The same model bleed, hunted in the RENDERED PAGE rather than the data.

    D05 reads the unit fields; this reads what the buyer actually sees, because a tier card
    can render "$350/mo per account" from data whose unit field was innocent.
    """
    # C3/D06-extend: marketplace ventures (take-rate per transaction) are also
    # never-recurring — same subscription-phrase leak class as per-unit models. Real
    # R4 catch: a marketplace's per-booking price rendered "$350/mo per account".
    # Kept separate from PER_UNIT_KINDS (shared with D05, where marketplace genuinely
    # isn't "per-unit" in the unit-noun sense) rather than widening that set.
    kind = r.get("business_model_kind")
    if kind not in PER_UNIT_KINDS and kind != "marketplace":
        return not_applicable("not a per-unit or marketplace model")
    if html is None:
        return Finding(None, "no HTML in corpus")
    # "/mo per " matters as much as "/month per ": the tier cards and optimal-price
    # line render the ABBREVIATED form, so a list carrying only "/month per " reported
    # "clean" on a marketplace that printed "$185.0/mo per booking" four times (caught
    # by the Wave-4-entry R4 panel, R5). A gate's phrase list has to match what the
    # renderer actually writes, not a near-miss of it.
    hits = [p for p in ("/month per ", "/mo per ", "B2B SaaS benchmark", "per account")
            if p in html]
    return Finding(not hits, f"SaaS phrases in rendered report: {hits}" if hits else "clean")


def d17_per_unit_not_on_subscription_fallback(r: dict, html: Optional[str]) -> Finding:
    """B2 + C3: a venture whose business model is NOT a true subscription
    (transactional/ecommerce/services/hybrid, OR marketplace) must NOT have
    financials on the subscription shape (a 'customers' key / 'annual_price_per_
    customer' assumption in the scenario table — that's churn-annualized revenue,
    wrong for both a one-time-sale business AND a take-rate marketplace).

    Two real R4 chains this catches: (1) 8add1fa2 (hybrid) — a device price was
    mis-extracted as its $5/mo app fee, margin went negative, economics errored, and
    financials silently fell back to subscription math. (2) 174ae091 (marketplace) —
    the average booking value was treated as a monthly seat fee ("$5400/yr, 5%
    monthly churn") for a venture whose own differentiator claims "zero subscription
    fees". N/A when the venture is a true subscription, or financials are absent."""
    kind = r.get("business_model_kind")
    year3 = (((r.get("financials") or {}).get("scenarios") or {}).get("base") or {}).get("year_3") or {}
    if kind in PER_UNIT_KINDS:
        econ = r.get("economics") or {}
        # NOT `!= "transactional"`. business_model.py:319 writes `"model": kind`, and
        # economics_step passes the real kind, so econ["model"] is literally "ecommerce" /
        # "services" / "hybrid" for three of the four per-unit kinds — MEASURED, this gate
        # returned not-applicable on all three. It is the ONLY gate that inspects
        # `customers` / `annual_price_per_customer` on a per-unit venture, and those are
        # exactly the kinds where the subscription fallback lands, so it was dead in the
        # place it was needed. The outer `kind in PER_UNIT_KINDS` already established
        # applicability; this inner check only needs to confirm economics agrees.
        if econ.get("model") not in PER_UNIT_KINDS:
            return not_applicable(f"economics model {econ.get('model')!r} is not per-unit")
        if not year3:
            return Finding(None, "no financials scenario table")
        bad = "customers" in year3
        return Finding(not bad, "financials year_3 carries 'customers' (subscription shape) "
                       "on a transactional venture" if bad else "financials use the unit shape")
    if kind == "marketplace":
        if not year3:
            return Finding(None, "no financials scenario table")
        assumptions = (r.get("financials") or {}).get("assumptions") or {}
        bad = "customers" in year3 or "annual_price_per_customer" in assumptions
        return Finding(not bad, "financials carry a subscription shape ('customers' or "
                       "annual_price_per_customer) on a marketplace venture" if bad
                       else "financials use the revenue-only shape")
    return not_applicable("not a per-unit or marketplace model")
