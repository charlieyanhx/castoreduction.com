"""orchestrator/steps/economics_step.py — Step 10: model-aware economics (+ break-even,
benchmark table, and the non-priced fallback).

Extracted from run_plan (god-function dismantling, wave 9). Pure move: same cost
structure (category-estimated + disclosed, computed once and shared by break-even +
unit economics), same D13 upstream guard (a geo-sourced venture gets NO scraped price
benchmark — measured: a $21 'per drink' row scraped from a cafe page), same three-way
model routing (retail contribution margin / subscription CLV+CAC+EVC / honest labeled
objects for marketplace + ad-supported, audit M12/M6), same non-fatal spans.

The price-of-record resolution stays in run_plan — it is built from plan-local
extractors (stated/unit/device price) whose patch surface a dozen tests own.
"""
from __future__ import annotations

from typing import Callable

from logger import get

from . import record_dropped_output, step_done, step_scope

log = get("plan.steps.economics")


def _wants_subscription_break_even(biz_kind: str | None) -> bool:
    """Only a real subscription gets subscription break-even.

    MEASURED: the condition was `not is_transactional`, which is True for marketplace and
    ad_supported as well — so a marketplace published "break even at 90 bookings, $444
    margin each" when the PLATFORM's contribution is $48 per booking and the real answer is
    834 (9.3x off), and report/claim_support.py whitelists that figure as citable. A free
    ad-supported app was told it needs "10,345 paying customers". The full transaction value
    is not the platform's revenue, and a free product has no paying customer at all.
    """
    return (biz_kind or "") == "subscription"


def run_economics_step(result: dict, profile: dict, *, psm_result: dict, biz_kind: str,
                       opt: float | None, price_per_unit, is_transactional: bool,
                       unit_noun: str, benchmark_recurring: bool, segment_summary: str,
                       competitor_pricing_data: dict, opps: list,
                       checkpoint: Callable[[], None] | None = None) -> None:
    """The priced-venture block: cost structure, subscription break-even, competitor
    benchmark (or its recorded skip), and the model-aware economics object."""
    with step_scope("economics"):
        # cycle36/37: cost structure is category-estimated + disclosed (not a hidden
        # $5000/$2), computed ONCE here and shared by break-even + unit economics.
        # P6 (operator rule, deddcd0f): a founder-entered monthly cost is THE anchor,
        # sourced and labeled; the model's figure survives as the benchmark beside it.
        from pricing import estimate_cost_structure, founder_cost_anchor
        _f_cost, _f_rent = founder_cost_anchor(result)
        _cost = estimate_cost_structure(
            profile.get("category", ""), opt,
            market_scale=(result.get("market_scale") or {}).get("scale"),
            founder_monthly_cost=_f_cost, founder_rent=_f_rent,
        ) if opt else None

        # --- Break-even (subscription only — retail break-even lives in unit economics) ---
        # Not `not is_transactional` — that is True for marketplace and ad_supported
        # too, and neither has a monthly per-customer fee to break even on.
        if _cost and opt and _wants_subscription_break_even(biz_kind):
            try:
                from pricing import compute_break_even
                result["pricing"]["break_even"] = compute_break_even(
                    opt, monthly_fixed_cost=_cost["monthly_fixed_cost"],
                    variable_cost_per_customer=_cost["variable_cost_per_customer"],
                    cost_source=_cost["source"])
            except (TypeError, ValueError):
                pass

        # --- Per-unit pricing + competitor benchmark table (user feedback #3b) ---
        # D13's invariant, enforced UPSTREAM: a geo-sourced venture (local competitors from
        # OSM) gets NO scraped price benchmark. Their "pricing pages" are cafe/salon websites
        # where the scraper grabs whatever number it finds — MEASURED on run7, a $21 figure
        # (a bean bag or gift card) shipped as "Noe Cafe: $21 per drink, 4.0x our price".
        # Before this guard the pipeline BUILT the bad table and relied on the gate to block
        # the whole report; now it never builds one, and the reason is recorded instead of
        # the table silently missing.
        if (result.get("discover") or {}).get("geo_sourced"):
            record_dropped_output(
                result, "pricing_benchmark",
                "scraped price benchmarks are skipped for geo-sourced local ventures — "
                "venue websites rarely publish a clean per-unit price, and D13 blocks any "
                "report that ships one (measured: a $21 'per drink' row scraped from a "
                "cafe page)")
        else:
            try:
                from pricing import build_benchmark_table
                # unit_noun == unit_for_model(biz_kind, ...) — already correct for every
                # kind (seat/account for subscription, booking for marketplace, the real
                # per-unit noun for transactional/etc). The old branch hand-rolled a
                # SEPARATE seat/account guess for "not is_transactional" that happened to
                # diverge from it for marketplace ("$450 per account" SaaS framing on a
                # per-booking price — R4 catch, 174ae091).
                bench = build_benchmark_table(
                    our_tiers=psm_result.get("recommended_tiers", []),
                    competitor_pricing=competitor_pricing_data,
                    pricing_unit=unit_noun,
                    competitor_brands=opps[:8],
                    recurring=benchmark_recurring,  # D06: only true subscriptions
                )
                if "error" not in bench:
                    result["pricing"]["benchmark"] = bench
            except Exception as e:
                log.warning(f"[plan] pricing benchmark failed (non-fatal): {e}")

        # --- Step 10: economics — MODEL-AWARE (cycle37) ---
        # Transactional retail → contribution margin + break-even covers/day (no CLV/churn/SaaS).
        # Subscription → the original CLV + CAC + EVC decomposition.
        try:
            if is_transactional and _cost and price_per_unit:
                from business_model import retail_unit_economics
                log.info("[plan] Step 10: retail unit economics (transactional, $%.2f/%s)",
                         price_per_unit, unit_noun)
                econ = retail_unit_economics(
                    price_per_unit=float(price_per_unit),
                    variable_cost_per_unit=_cost["variable_cost_per_customer"],
                    monthly_fixed_cost=_cost["monthly_fixed_cost"],
                    unit=unit_noun,
                    cost_source=_cost["source"],
                    category=profile.get("category", ""),
                    business_model=profile.get("business_model", ""),
                    kind=biz_kind,  # R6: model = the real kind, not hardcoded
                )
                # P6: the founder-vs-benchmark divergence reaches the reader.
                if _cost.get("benchmark_note"):
                    econ["cost_benchmark_note"] = _cost["benchmark_note"]
                if _cost.get("sourced"):
                    econ["cost_sourced"] = True
            elif biz_kind == "subscription":
                from economics import full_economics
                log.info("[plan] Step 10: CLV + CAC + EVC economics (subscription)")
                comp_prices = None
                if competitor_pricing_data and competitor_pricing_data.get("per_domain"):
                    comp_prices = [d["median"] for d in competitor_pricing_data["per_domain"] if d.get("median")]
                # `unit_noun` — the SAME resolver every other site uses. This was a
                # hand-rolled copy, three lines below the comment explaining that exactly
                # such a duplicate had been deleted for diverging, and it diverged: it read
                # the profile's `business_model` FIELD alone while unit_for_model reads
                # summary + business_model + category. MEASURED on realistic profiles, 1 in
                # 4 disagreed — a venture whose summary says "$29 per seat per month" but
                # whose business_model field says "subscription" was priced per seat and
                # had its CLV computed per account.
                econ = full_economics(
                    segment_summary=segment_summary,
                    product_summary=profile.get("summary", ""),
                    optimal_price_monthly=opt,
                    pricing_unit=unit_noun,
                    competitor_prices=comp_prices,
                    # R8 (88b416f6): the founder's status quo anchors the EVC
                    # reference — never a pricier alternative of the model's choosing.
                    status_quo=str(((result.get("intake") or {}).get("facts") or {})
                                   .get("status_quo") or "") or None,
                )
                # C5: carry the founder's seats-per-account so downstream consumers
                # can restate per-seat figures per customer instead of comparing
                # unlike units (the 3.7x CAC "breach" was a unit mismatch).
                _seats_fact = (((result.get("intake") or {}).get("facts") or {})
                               .get("seats_per_account"))
                if isinstance(econ, dict) and _seats_fact:
                    econ["_facts"] = {"seats_per_account": _seats_fact}
            else:
                # cycle38: marketplace (take-rate on GMV) and ad-supported (eCPM on users) have a
                # different revenue basis than per-unit OR subscription. Rather than fabricate a
                # SaaS CLV:CAC (audit M12/M6 criticals), emit an HONEST labeled economics object
                # that names the right basis and the operator inputs it needs.
                log.info("[plan] Step 10: economics — %s (non per-unit, non-subscription)", biz_kind)
                if biz_kind == "marketplace":
                    econ = {"model": "marketplace",
                            "revenue_basis": "take-rate on third-party GMV (platform revenue = GMV × take-rate, NOT full GMV)",
                            "needs_operator_input": ["take-rate %", "avg transaction value", "transactions/period", "buyer & seller CAC"],
                            "note": "Per-subscriber CLV:CAC does not apply. Size revenue from GMV × take-rate; "
                                    "model two-sided unit economics (CAC for both sides) once the take-rate is set."}
                elif biz_kind == "ad_supported":
                    econ = {"model": "ad_supported",
                            "revenue_basis": "advertising (revenue = active users × sessions × impressions × eCPM × fill-rate)",
                            "needs_operator_input": ["eCPM", "fill rate", "sessions/MAU", "impressions/session", "content + ad-serving cost/user"],
                            "note": "Free to the user — there is no subscriber price, so subscriber CLV:CAC does not apply. "
                                    "Unit economics are ad-revenue-per-active-user minus cost-to-serve."}
                else:
                    econ = {"model": biz_kind,
                            "revenue_basis": "model-specific",
                            "note": f"Economics for '{biz_kind}' require operator-provided revenue inputs; "
                                    "subscriber CLV:CAC does not apply."}
            result["economics"] = econ
            if "error" not in econ:
                step_done(result, "economics")
                if checkpoint:
                    checkpoint()
        except Exception as e:
            log.warning(f"[plan] economics computation failed (non-fatal): {e}")


def ensure_nonpriced_economics(result: dict, biz_kind: str) -> None:
    """cycle38: non-priced models (ad-supported, marketplace) often have no PSM optimal
    price, so the priced block is skipped — but we still owe the reader an HONEST
    economics object naming the real revenue basis (never a fabricated SaaS CLV:CAC,
    and never silently blank)."""
    if result.get("economics") or biz_kind not in ("ad_supported", "marketplace"):
        return
    if biz_kind == "ad_supported":
        result["economics"] = {"model": "ad_supported",
            "revenue_basis": "advertising (revenue = active users × sessions × impressions × eCPM × fill-rate)",
            "needs_operator_input": ["eCPM", "fill rate", "sessions/MAU", "impressions/session", "cost-to-serve/user"],
            "note": "Free to the user — no subscriber price, so subscriber CLV:CAC does not apply. "
                    "Unit economics = ad revenue per active user minus cost-to-serve."}
    else:
        result["economics"] = {"model": "marketplace",
            "revenue_basis": "take-rate on third-party GMV (platform revenue = GMV × take-rate, not full GMV)",
            "needs_operator_input": ["take-rate %", "avg transaction value", "transactions/period", "buyer & seller CAC"],
            "note": "Size revenue from GMV × take-rate; model two-sided CAC. Subscriber CLV:CAC does not apply."}
    step_done(result, "economics")
