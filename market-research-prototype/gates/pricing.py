"""gates/pricing.py — price, willingness to pay, unit economics and the volumes sold against them.

14 of the 61 deterministic detectors. Each takes (result, html) and
returns a Finding; none of them share state, which is why they split cleanly.
"""
from __future__ import annotations

import json
import re
import statistics
from typing import Optional
from gates.common import Finding, not_applicable, _num


def d08_profit_coherent(r: dict, html: Optional[str]) -> Finding:
    """If the report claims profitability at SOM, the scenario table must agree.

    Abstains unless the claim is actually made: this detects CONTRADICTION between two
    sections, so with nothing claimed there is nothing to contradict.
    """
    econ = r.get("economics") or {}
    asv = econ.get("at_som_volume") or {}
    if asv.get("profitable_at_som") is not True:
        return Finding(None, "no profitable-at-SOM claim")
    scen = (r.get("financials") or {}).get("scenarios") or {}
    agg = scen.get("aggressive") or {}
    y3 = _num((agg.get("year_3") or {}).get("monthly_operating_profit_usd"))
    be = agg.get("break_even_year")
    if y3 is None and be is None:
        return Finding(None, "no scenario table")
    ok = (be is not None) or (y3 is not None and y3 > 0)
    return Finding(ok, f"profitable_at_som=True but aggressive Y3 profit={y3} break_even={be}")


def d24_withheld_profit_not_fabricated(r: dict, html: Optional[str]) -> Finding:
    """A WITHHELD profit must not be rendered as a number.

    business_model.py omits monthly_operating_profit_usd on purpose when SOM spans
    several sites but the cost stack is one site, recording why in
    profit_withheld_reason. The template formatted the absent value through
    SafeUndefined and printed "$0/mo operating profit" — a figure nobody computed,
    beside $1.5M/mo revenue, while the scenario table showed $999K/mo at the identical
    volume, and the reason appeared nowhere. Same class as D09: the code withholds,
    the renderer publishes anyway."""
    asv = ((r.get("economics") or {}).get("at_som_volume") or {})
    reason = str(asv.get("profit_withheld_reason") or "").strip()
    if not reason:
        return Finding(None, "nothing withheld")
    if html is None:
        return Finding(None, "no HTML in corpus")
    if "$0/mo operating profit" in html:
        return Finding(False, "profit was withheld but the report renders "
                              "'$0/mo operating profit' — a fabricated figure")
    # The reason must actually reach the reader; silence just looks incomplete.
    if reason[:30] not in html:
        return Finding(False, "profit withheld but the reason is rendered nowhere")
    return Finding(True, "withheld profit disclosed with its reason")


def d26_pnl_cost_side_honest(r: dict, html: Optional[str]) -> Finding:
    """The P&L may not claim profits its own cost side cannot support (R4 rank 2).

    Three checks over financials + economics:
      1. A withheld profit stays withheld: when assumptions.profit_withheld_reason is
         set, no scenario row may carry monthly_operating_profit_usd.
      2. Break-even feasibility vs the venture's OWN published CAC: a break-even year
         whose acquisition spend (that year's customers x typical_cac_usd) meets or
         exceeds that year's revenue is impossible — 4a755faa claimed break-even
         YEAR 1 beside a $4,500 CAC implying ~$4.3M spend against $160K revenue.
      3. Implied operating margin never exceeds the disclosed contribution margin —
         profit = revenue x margin - fixed can't beat the margin, so a row that does
         was not built from the formula.
    """
    fin = r.get("financials") or {}
    scen = fin.get("scenarios") or {}
    if not scen:
        return Finding(None, "no financials")
    assumptions = fin.get("assumptions") or {}
    problems: list[str] = []

    # The withhold decision may live on EITHER surface — financials' own assumptions,
    # or the economics at-SOM block. The stored de34e328 is exactly the cross-surface
    # case: economics withheld its verdict, and the scenario table on the same page
    # published $827.8K/mo at the identical multi-site volume anyway.
    _econ_reason = ((r.get("economics") or {}).get("at_som_volume") or {}).get("profit_withheld_reason")
    if assumptions.get("profit_withheld_reason") or _econ_reason:
        for label, sc in scen.items():
            if not isinstance(sc, dict):
                continue
            for yk in ("year_1", "year_2", "year_3"):
                if "monthly_operating_profit_usd" in (sc.get(yk) or {}):
                    problems.append(f"{label}.{yk} carries a profit despite the withhold")

    cac = _num(((r.get("economics") or {}).get("unit_economics") or {}).get("typical_cac_usd"))
    if cac and cac > 0:
        for label, sc in scen.items():
            if not isinstance(sc, dict):
                continue
            be = sc.get("break_even_year")
            yr = sc.get(f"year_{be}") if be else None
            n = _num((yr or {}).get("customers"))
            rev = _num((yr or {}).get("revenue_usd"))
            if be and n and rev and n * cac >= rev:
                problems.append(
                    f"{label}: break-even Y{be} but acquisition spend "
                    f"({n:,.0f} x ${cac:,.0f} = ${n * cac:,.0f}) >= Y{be} revenue ${rev:,.0f}")

    margin = _num(assumptions.get("contribution_margin_pct"))
    if margin:
        for label, sc in scen.items():
            if not isinstance(sc, dict):
                continue
            for yk in ("year_1", "year_2", "year_3"):
                y = sc.get(yk) or {}
                p_, rev = _num(y.get("monthly_operating_profit_usd")), _num(y.get("revenue_usd"))
                if p_ and rev and rev > 0 and (p_ * 12) / rev > margin / 100 + 0.001:
                    problems.append(f"{label}.{yk}: implied op margin exceeds the "
                                    f"disclosed {margin}% contribution margin")

    if problems:
        return Finding(False, "; ".join(problems[:3]))
    return Finding(True, "cost side consistent with its own claims")


def d31_benchmark_prices_coherent(r: dict, html: Optional[str]) -> Finding:
    """A benchmark price must be a comparable per-unit price, not a mixed-SKU median
    (R4 rank 7, 7/16).

    scrape_brand_prices pooled every dollar amount off a page and medianed it —
    purpleair.shop's [9.99..349] became "$75.50", spread 31.7x. Two checks:
      1. any per-domain median whose prices_found span > 3x is incoherent;
      2. a category_median backed by fewer than 3 priced domains is not a category."""
    cp = r.get("competitor_pricing") or {}
    per_domain = cp.get("per_domain") or []
    if not per_domain and cp.get("category_median") is None:
        return Finding(None, "no competitor pricing")
    problems = []
    for d in per_domain:
        med = d.get("median")
        prices = [p for p in (d.get("prices_found") or [])
                  if isinstance(p, (int, float)) and p > 0]
        if med and prices:
            spread = max(prices) / min(prices) if min(prices) > 0 else 999
            if spread > 3.0:
                problems.append(f"{d.get('domain')}: median ${med} from a "
                                f"{spread:.0f}x price spread (mixed SKUs)")
    n_priced = sum(1 for d in per_domain if d.get("median"))
    if cp.get("category_median") is not None and n_priced < 3:
        problems.append(f"category median from only {n_priced} priced domain(s) "
                        "— not a defensible category")
    if problems:
        return Finding(False, "; ".join(problems[:3]))
    return Finding(True, "benchmark prices coherent and adequately sourced")


def d10_wtp_band_sane(r: dict, html: Optional[str]) -> Finding:
    """The willingness-to-pay band is ordered, and a range is really a range.

    Two failures. Unordered (low > median) is arithmetic nonsense. `low == high` printed as
    a band is worse: it is a single observation wearing the clothes of a measured spread,
    which is precisely the over-claim the report exists not to make. A genuine single point
    is fine when it SAYS it is one.
    """
    wtp = (((r.get("consumer_research") or {}).get("synthesis") or {})
           .get("willingness_to_pay") or {})
    lo, md, hi = _num(wtp.get("low")), _num(wtp.get("median")), _num(wtp.get("high"))
    if lo is None or md is None or hi is None:
        return Finding(None, "no band (single point or absent)")
    if not (lo <= md <= hi):
        return Finding(False, f"band unordered: {lo}/{md}/{hi}")
    if lo == hi and not wtp.get("single_point") and (wtp.get("n_would_pay") or 0) >= 2:
        return Finding(False, f"degenerate band {lo}=={hi} presented as a range")
    return Finding(True, f"band {lo}/{md}/{hi}")


def d32_wtp_aggregation_honest(r: dict, html: Optional[str]) -> Finding:
    """R4 rank 8: the WTP band must be honest arithmetic over the interviews.

    Three defects the corpus carried: the 'median' was the UPPER-middle order
    statistic (overstated for even n); a $0 'would not buy' counted as a payer,
    inflating n_would_pay; and a low/median/high band was minted from as few as 2
    answers. FAIL when the reported median disagrees with statistics.median of the
    strictly-positive interview WTPs, when n_would_pay counts a non-positive answer,
    or when a median band rests on fewer than 3 named prices."""
    cr = r.get("consumer_research") or {}
    syn = cr.get("synthesis") or {}
    wtp = syn.get("willingness_to_pay") or {}
    if not wtp:
        return Finding(None, "no WTP band")
    interviews = cr.get("interviews") or []
    pos = [float(w) for iv in interviews
           if isinstance((w := iv.get("willingness_to_pay_usd")), (int, float))
           and not isinstance(w, bool) and w > 0]

    n = wtp.get("n_would_pay")
    if interviews and n is not None and n != len(pos):
        return Finding(False, f"n_would_pay={n} but {len(pos)} strictly-positive WTPs "
                              "(a $0/non-buyer counted as a payer)")

    md = wtp.get("median")
    if md is not None and not wtp.get("single_point"):
        if (wtp.get("n_would_pay") or 0) < 3:
            return Finding(False, f"median band from only {wtp.get('n_would_pay')} "
                                  "named prices — a median needs >= 3")
        if pos:
            true_med = statistics.median(pos)
            if abs(float(md) - true_med) > 0.01:
                return Finding(False, f"reported median {md} != statistics.median "
                                      f"{true_med} of the interview WTPs")
    return Finding(True, "WTP aggregation honest")


def d13_benchmark_not_fabricated(r: dict, html: Optional[str]) -> Finding:
    """A geo-sourced roster must not carry scraped price benchmark rows.

    Competitors found on a map are venues, not storefronts with published price pages, so
    benchmark rows against them cannot have been scraped from anywhere. Rows present here
    mean the table was invented.
    """
    if not (r.get("discover") or {}).get("geo_sourced"):
        return Finding(None, "web-sourced competitors (benchmark legitimate)")
    rows = ((r.get("pricing") or {}).get("benchmark") or {}).get("rows") or []
    return Finding(not rows,
                   f"geo-sourced venture has {len(rows)} scraped benchmark rows" if rows else "no scraped benchmark (correct)")


def d37_viability_anchored_to_real_margin(r: dict, html: Optional[str]) -> Finding:
    """R4 rank 14: a per-unit venture with a computed contribution margin must anchor
    viability to THAT margin, not invent one. The surfacing was gated on
    model=='transactional', so hybrid/services/ecommerce got nothing (28d0ec61 computed
    65.5% but viability called it 'thin on unit-level contribution margins', score 40).
    FAIL when economics is a per-unit kind with a contribution_margin_pct but viability
    carries no unit_economics_anchor, or the anchor disagrees with the computed margin."""
    from business_model import is_per_unit
    econ = r.get("economics") or {}
    cm = econ.get("contribution_margin_pct")
    if not is_per_unit(econ.get("model")):
        return not_applicable(f"economics model {econ.get('model')!r} is not per-unit")
    if not _num(cm):
        # In scope and unanswerable: a per-unit venture that never computed a contribution
        # margin has a hole, and D55 should feel it. Only the line above is a shape fact.
        return Finding(None, "per-unit venture but no contribution margin was computed")
    anchor = (r.get("viability") or {}).get("unit_economics_anchor")
    if not anchor:
        return Finding(False, f"economics computed a {cm}% per-unit margin but viability "
                              "records no unit_economics_anchor — margin not surfaced")
    a = _num(anchor.get("contribution_margin_pct"))
    if a is None or abs(a - float(cm)) > 0.01:
        return Finding(False, f"viability anchor margin {a} != computed {cm}")
    return Finding(True, f"viability anchored to the computed {cm}% margin")


def d39_price_reconcile_unit_honest(r: dict, html: Optional[str]) -> Finding:
    """R4 rank 16: a per-unit venture's price reconciliation must render in the
    venture's OWN unit, not a hardcoded '/mo' — an $18,500-per-project consultancy
    read '$18,500/mo'. FAIL when a per-unit venture's price_reconciliation note carries
    '/mo'. N/A for subscriptions (where /mo is correct) or no reconciliation."""
    from business_model import is_per_unit
    recon = r.get("price_reconciliation") or {}
    note = recon.get("note")
    if not note:
        return Finding(None, "no price reconciliation")
    econ = r.get("economics") or {}
    kind = econ.get("model") or r.get("business_model_kind")
    # NOT `is_per_unit` — that answers "is revenue price x volume?" and is False for
    # marketplace and ad_supported, which are ALSO not monthly. MEASURED: a marketplace
    # reconciliation read "you stated $29/mo, WTP suggests $450/mo (+1452%)" where $450 is
    # one homeowner's job value, and this gate declared it not-applicable — so the only
    # check on that sentence excused the exact venture it was wrong for. "/mo" is correct
    # for a true subscription and for nothing else.
    from plan import _pricing_is_recurring
    if _pricing_is_recurring(kind):
        return Finding(None, "recurring venture (/mo is correct)")
    if "/mo" in note:
        return Finding(False, f"a {kind or 'non-recurring'} venture's price reconciliation "
                              f"is priced '/mo' instead of the venture's own unit")
    return Finding(True, "price reconciliation uses the venture's unit")


def d41_no_empty_price_per_customer(r: dict, html: Optional[str]) -> Finding:
    """R4 rank 20: a non-priced model (ad-supported / marketplace) or
    a $0 PSM price fell through the subscription assumptions block and rendered an empty
    "Annual price per customer: $ (%/mo churn assumed)". FAIL when the HTML shows a
    price-per-customer line with no number."""
    if html is None:
        return Finding(None, "no html to check")
    import re
    if re.search(r"price per customer:\s*\$\s*\(", html):
        return Finding(False, "empty 'Annual price per customer: $ (%/mo churn)' — a "
                              "non-priced model fell through the subscription block")
    return Finding(True, "no empty per-customer price rendered")


# The band D18's docstring has always specified: outside 0.1x-10x in EITHER
# direction is a disagreement the report must disclose.
_WTP_GAP_FOLD = 10.0


def d18_wtp_price_reconciled(r: dict, html: Optional[str]) -> Finding:
    """B3: a large gap between the consumer-research WTP synthesis and the PSM-
    recommended price must be disclosed (plan.reconcile_wtp_with_price), never
    rendered side by side with no comment. Real R4 shape: WTP $150-1,500/unit vs a
    $125,000/unit recommendation, 83-100x apart, unflagged (800c261b, e55db08e,
    4a755faa). FAIL when the ratio is outside 0.1x-10x and no wtp_price_mismatch
    flag is present. N/A when either number is missing or they already agree."""
    syn = ((r.get("consumer_research") or {}).get("synthesis") or {})
    wtp = syn.get("willingness_to_pay") or {}
    center = wtp.get("median") if wtp.get("median") is not None else wtp.get("point")
    ceiling = wtp.get("high") if wtp.get("high") is not None else center
    floor = wtp.get("low") if wtp.get("low") is not None else center
    recommended = (r.get("pricing") or {}).get("psm", {}).get("optimal_price_point")
    ceiling_n, floor_n, rec_n = _num(ceiling), _num(floor), _num(recommended)
    if not ceiling_n or not rec_n:
        return Finding(None, "WTP ceiling or recommended price missing")
    flagged = "wtp_price_mismatch" in syn
    # R4 rank 11: a price ABOVE the top of the WTP range is the mismatch that misleads a
    # buyer, not a ratio-to-median inside a wide deadband.
    if rec_n > ceiling_n:
        return Finding(flagged, f"recommended {rec_n} above WTP ceiling {ceiling_n} — "
                       + ("disclosed" if flagged else "UNFLAGGED"))
    # ...and the other side, which this gate documented ("outside 0.1x-10x") and then stopped
    # checking. Measured on job d62bc04f: WTP $150,000/mo against a $1,450/mo recommendation,
    # 103x apart, rendered side by side with no comment while the EVC section urged a 20-40%
    # rise — an answer neither number supports. "Someone would pay it" is true and is not the
    # question; what this gate enforces is that two of the report's own numbers cannot
    # disagree by an order of magnitude in silence. Measured across all 24 stored artifacts
    # before widening: every healthy report prices at 0.1x-1.1x of its own WTP floor, so
    # restoring the documented band costs nothing and catches only the pathological case.
    if floor_n and rec_n * _WTP_GAP_FOLD < floor_n:
        return Finding(flagged, f"recommended {rec_n} is {floor_n / rec_n:,.0f}x BELOW the "
                       f"stated WTP floor {floor_n} — "
                       + ("disclosed" if flagged else "UNFLAGGED"))
    return Finding(None, f"recommended {rec_n} within WTP range "
                         f"(floor {floor_n}, ceiling {ceiling_n})")


_AVG_ORDER_RE = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*average\s+(?:order|job|booking|transaction)\b", re.I)


def _avg_order_figures(text: str) -> list[float]:
    """Dollar magnitudes labeled '$X average order/job/booking/transaction' in prose."""
    out = []
    for m in _AVG_ORDER_RE.finditer(text or ""):
        try:
            out.append(float(m.group(1).replace(",", "")))
        except ValueError:
            pass
    return out


def _canonical_arpu(r: dict) -> Optional[float]:
    """Mirrors four_ps.price_anchor_directive's resolution: per-unit models use the
    REAL unit price (economics.price_per_unit); others use the PSM optimal point."""
    kind = str(r.get("business_model_kind") or "").lower()
    econ = r.get("economics") or {}
    if kind in ("transactional", "ecommerce", "services", "hybrid"):
        return _num(econ.get("price_per_unit"))
    return _num((r.get("pricing") or {}).get("psm", {}).get("optimal_price_point"))


def d21_arpu_coherent_across_sections(r: dict, html: Optional[str]) -> Finding:
    """C2: Place/Product/Promotion get NO pricing context in their own prompts (only
    Price does), so an "average order/job/booking" dollar figure appearing in their
    prose is genuinely invented, not miscomputed. Real R4 critical (a marketplace):
    Price used $450 (correct), Place said $200 "average job size", Product said $100
    "average order" — three numbers for one concept. FAILs when any section's figure
    disagrees with the canonical ARPU (economics.price_per_unit for per-unit models,
    else the PSM optimal point). N/A when no section names an average-order figure at
    all, or no canonical ARPU is available to check against."""
    canon = _canonical_arpu(r)
    fp = r.get("four_ps") or {}
    all_figs: dict[str, list[float]] = {}
    for section in ("product", "price", "place", "promotion"):
        nar = str((fp.get(section) or {}).get("narrative") or "")
        figs = _avg_order_figures(nar)
        if figs:
            all_figs[section] = figs
    if not all_figs:
        return Finding(None, "no section names an average order/job/booking figure")
    if not canon:
        return Finding(None, "no canonical ARPU available to check against")
    bad = {s: fs for s, fs in all_figs.items()
          if any(abs(f - canon) / canon > 0.02 for f in fs)}
    return Finding(not bad, f"sections disagree with canonical ${canon:,.0f}: {bad}"
                   if bad else f"all sections agree with canonical ${canon:,.0f}")


def d58_psm_tiers_disclose_their_own_range(r: dict, html: str | None) -> Finding:
    """A recommended tier outside the PSM's own acceptable range must say so.

    MEASURED on runs 12-15, identically every time: acceptable range $4.25-$6.75, then
    tiers at Value $3.85 (below the floor, which is also the point of marginal cheapness)
    and Premium $9.50 (above the ceiling, above the too-expensive MEDIAN of $8.25, above
    that band's q3 of $9.00 — so appreciably more than half the simulated panel rejects
    it). Both shipped with flat "PSM PRICING OUTPUT" citations, in a report whose kill
    criterion elsewhere treats the $4.25 floor as meaningful.

    This gate does NOT require tiers to be in range. An out-of-range tier can be sound
    strategy — a loss-leader, a halo SKU — and clamping one would destroy a real
    recommendation. It requires only that the report SAY the instrument disagrees, so a
    reader can tell a deliberate halo SKU from a number the model drifted into. That is
    the same disclosure-not-obedience shape as D09.

    ok=None when there is no PSM or no usable range: unchecked, which D55 counts against
    coverage, rather than a silent pass."""
    psm = (r.get("pricing") or {}).get("psm") or {}
    tiers = psm.get("recommended_tiers")
    rng = psm.get("acceptable_range")
    if not isinstance(tiers, list) or not tiers:
        return Finding(None, "no PSM tiers to check")
    if not isinstance(rng, (list, tuple)) or len(rng) != 2:
        return Finding(None, "PSM published tiers but no acceptable range to check "
                             "them against — the disclosure check could not run")
    try:
        lo, hi = float(rng[0]), float(rng[1])
    except (TypeError, ValueError):
        return Finding(None, "PSM acceptable range is not numeric")
    if lo > hi:
        return Finding(None, "PSM acceptable range is inverted — the instrument is at "
                             "fault, not the tiers")

    naked = []
    outside = 0
    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        try:
            p = float(tier.get("price"))
        except (TypeError, ValueError):
            continue
        if lo <= p <= hi:
            continue
        outside += 1
        if not str(tier.get("range_note") or "").strip():
            naked.append(f"{tier.get('name') or '?'} ${p:g}")
    if naked:
        return Finding(False,
                       f"tier(s) outside the PSM's own ${lo:g}-${hi:g} acceptable range "
                       f"carry no qualification: {', '.join(naked)} — a reader cannot "
                       f"tell a deliberate halo/loss-leader from a drifted number")
    if outside:
        return Finding(True, f"{outside} tier(s) outside the ${lo:g}-${hi:g} range, each "
                             f"disclosed as such")
    return Finding(True, f"all tiers within the ${lo:g}-${hi:g} acceptable range")


# Volume phrasings the sections actually write. Built from the two measured runs:
# "targeting 250 drinks per day", "150 drinks/day", "reach 150 daily drinks",
# "120.4 drinks per day", "targeting 150 daily transactions" — plus, since #100 taught the
# ladder to plan in months, "690 seats per month" and "57 bookings/mo".
#
# The noun list below is every cafe-and-shop word someone happened to think of. It is a
# FLOOR, not the list: the venture's own unit noun is spliced in per call, because a
# consultancy selling projects and a platform selling bookings were invisible to all of it.
_GENERIC_UNIT_NOUNS = ("drink", "unit", "transaction", "customer",
                       "cover", "order", "visit", "sale", "booking", "seat")


_PER_DAY = r"per\s+day|/\s*day|a\s+day|daily"


_PER_MONTH = r"per\s+month|/\s*months?\b|/\s*mo\b|a\s+month|monthly"


def _singular(noun: str) -> str:
    n = (noun or "").strip().lower()
    return n[:-1] if n.endswith("s") and not n.endswith("ss") else n


def _volume_claim_re(unit_noun: str | None) -> re.Pattern:
    """The phrasing matcher, widened by the venture's own noun and its own period.

    The optional word between number and noun is CAPTURED (f1), because it decides what
    kind of claim this is — see _stated_volumes."""
    nouns = sorted({_singular(n) for n in (*_GENERIC_UNIT_NOUNS, unit_noun or "") if n},
                   key=len, reverse=True)
    alt = "|".join(re.escape(n) + "s?" for n in nouns)
    return re.compile(
        rf"(?P<n1>\d[\d,]*(?:\.\d+)?)\s*(?:(?P<f1>\w+)\s+)?(?:{alt})\s*"
        rf"(?:(?P<d1>{_PER_DAY})|(?P<m1>{_PER_MONTH}))"
        rf"|(?:reach|target(?:ing)?|hit)\s+(?P<n2>\d[\d,]*(?:\.\d+)?)\s+"
        rf"(?:(?P<d2>daily)|(?P<m2>monthly))",
        re.I)


def _stated_volumes(four_ps: dict, unit_noun: str | None = None
                    ) -> list[tuple[str, float, str, str | None]]:
    """(section, number, period, qualifier) for every volume figure the 4Ps prose states.

    The PERIOD is captured rather than assumed. A daily figure inside a monthly business is
    not a missing match — it is a claim, and one worth checking, because a section that
    writes "23 bookings per day" against a 57/month plan is off by 12x and used to read as
    "no section states a daily volume".

    The QUALIFIER is the non-demand modifier between number and noun, when one exists.
    MEASURED (job 6d1e27a2, 2026-08-21): place wrote 'Set a target of 5 creator visits per
    month' — influencer visits, a marketing cadence — and the report was WITHHELD because
    5/month is no rung. '15 referral bookings monthly' and '120 digital transactions daily'
    are the same shape: slices and cadences, not the total the ladder prices. A modifier
    that is itself a demand noun ('100 drink sales per day') does NOT qualify the claim —
    both words say demand, and that figure is the operating volume.
    """
    pattern = _volume_claim_re(unit_noun)
    nouns = {_singular(n) for n in (*_GENERIC_UNIT_NOUNS, unit_noun or "") if n}
    out: list[tuple[str, float, str, str | None]] = []
    for section in ("product", "price", "place", "promotion"):
        body = four_ps.get(section)
        if body is None:
            continue
        text = body if isinstance(body, str) else json.dumps(body)
        for m in pattern.finditer(text):
            raw = m.group("n1") or m.group("n2")
            period = "month" if (m.group("m1") or m.group("m2")) else "day"
            filler = m.group("f1") if m.group("n1") else None
            qualifier = filler if (filler and _singular(filler) not in nouns) else None
            # A CURRENCY-LED figure is a PRICE, not a volume. MEASURED (run
            # ff89f905): "$65 per seat per month" and "$95 per seat per month" were
            # read as "65 seats/month" and "95 seats/month", and the stock check
            # (R4) withheld a report whose prose was correct. A volume never carries
            # a currency symbol immediately before its number.
            _lead = text[max(0, m.start() - 12):m.start()]
            if re.search(r"[$€£]\s*$|\b(?:usd|eur|gbp)\s*$", _lead, re.I):
                continue
            try:
                out.append((section, float(str(raw).replace(",", "")), period, qualifier))
            except (TypeError, ValueError):
                continue
    return out


def d61_volume_targets_match_the_ladder(r: dict, html: Optional[str]) -> Finding:
    """Every daily volume in the 4Ps must be a rung of the ladder, not a section's invention.

    MEASURED, same venture, two runs, the volume_ladder reminder confirmed fired on both:

      run17  price "targeting 250 drinks per day"; place and promotion "150 drinks per day"
             -- 67% apart, in one report, and BOTH inside the range the rule demanded
      run18  every figure in all four sections is 120.4 (break-even) or 320 (the ceiling)
             -- no operating target stated at all

    The old rule pinned a RANGE ("between break-even and the obtainable ceiling"), so a
    section could obey it and still contradict its neighbour. #76 fixed "targets outside the
    model"; this fixes "different targets inside it". The ladder now carries a third rung --
    the base-case year-1 volume, computed in financials.py beside the ramp it depends on --
    and this gate enforces that the prose quotes a rung rather than picking a number.

    Tolerance is 3%: prose reasonably writes 195 for 194.9, and a gate that cries wolf on
    good writing is a gate somebody switches off. It is NOT a range check -- 200/day sits
    comfortably between break-even and the ceiling and still fails, which is the whole point.
    """
    fp = r.get("four_ps") or {}
    # ONE reader, shared with the four_ps prompt that wrote the ladder. This gate used to
    # rebuild the rungs from `economics["price_per_unit"]` and `/365`, which meant it was a
    # SECOND owner of the number it exists to police -- and it disagreed with the first on
    # every non-retail shape (no price found at all) and by 1.4% on retail (the model runs
    # on 360 open days, this ran on 365).
    from financials import ladder_inputs
    ms = r.get("market_sizing") or {}
    lad = ladder_inputs(r.get("economics"), ms,
                        (r.get("business_model") or {}).get("kind"))
    rungs = dict(lad["rungs"])
    ladder_period, unit_noun = lad["period"], lad["unit"]

    # THE LADDER THE SECTIONS WERE ACTUALLY SHOWN, when the artifact recorded it. Prose is
    # graded against the rungs it was written from, never against what today's arithmetic
    # would produce -- otherwise every change to the model retroactively fails reports that
    # obeyed it, and the gate is back to being a second owner of the number.
    shown = fp.get("_volume_ladder")
    stamped_ladder = False
    stamped_stock = False
    if isinstance(shown, dict) and isinstance(shown.get("rungs"), dict):
        stamped_rungs = {k: float(v) for k, v in shown["rungs"].items()
                         if isinstance(v, (int, float)) and not isinstance(v, bool)}
        if stamped_rungs:
            rungs, stamped_ladder = stamped_rungs, True
            ladder_period = shown.get("period") or ladder_period
            unit_noun = shown.get("unit") or unit_noun
            # R4 (88b416f6): present ONLY on post-fix artifacts, whose prompt taught
            # the stock/flow distinction. Legacy stamps lack the key and keep their
            # old grading — a report is graded against the ladder it was written from.
            stamped_stock = bool(shown.get("is_stock"))
    if not stamped_ladder and ladder_period == "day" and "obtainable ceiling" in rungs:
        # An artifact from before the ladder was stamped had its ceiling written as
        # som/price/365; the model divides by 360 open days. Un-stamped reports are graded
        # against both calendars rather than failed for quoting the one they were given.
        # Self-expiring: every run since stamps its ladder and takes the branch above.
        rungs["obtainable ceiling (365-day calendar)"] = (
            rungs["obtainable ceiling"] * 360.0 / 365.0)

    # The target the sections were ACTUALLY handed, when the artifact recorded it. Prose is
    # checked against what the prompt said, not against what today's code would say -- a
    # gate that re-derives is a gate that grades a report against a model it never saw. A
    # bare float is still accepted: older artifacts stored one.
    stamped = fp.get("_volume_target") or fp.get("_volume_target_units_per_day")
    if isinstance(stamped, dict):
        if stamped.get("measure") == "units" and stamped.get("value"):
            rungs["planning target"] = float(stamped["value"])
            ladder_period = stamped.get("period") or ladder_period
    elif isinstance(stamped, (int, float)) and not isinstance(stamped, bool) and stamped > 0:
        rungs["planning target"] = float(stamped)
        ladder_period = "day"          # the pre-#100 key was units *per day* by definition

    stated = _stated_volumes(fp, unit_noun)
    if not stated:
        return Finding(None, "no section states an operating volume")
    if not rungs:
        return Finding(None, "no ladder available to check the stated volumes against")

    per_year = {"day": 360.0, "month": 12.0}

    def _is_rung(value: float, rung: float) -> bool:
        """Prose may ROUND a rung; it may not re-estimate it.

        A flat percentage tolerance does not express that. 3% of 194.9 is +/-5.8, which
        admits 200 — a number a section chose for itself, and exactly what this gate exists
        to catch. The allowance is instead "rounds to the same figure": half a unit, widened
        to 0.5% so a four-digit volume can be written to three significant figures.
        """
        return abs(value - rung) <= max(0.51, 0.005 * rung)

    bad = []
    ceiling_bound = max(rungs.values())
    if stamped_stock and stated:
        # R4: on a stock ladder every per-period phrasing is wrong BY FORM, rung value
        # or not — "320 seats/month" states a 12x-larger acquisition flow than the
        # 320-active-seat stock the model planned. (Only per-period claims reach this
        # gate at all; "320 active seats" carries no period marker and never matches.)
        flow_claims = [f"{s_} states {v:g} {unit_noun or 'unit'}s/{p_}"
                       for s_, v, p_, _q in stated]
        return Finding(False,
                       "; ".join(flow_claims[:4]) + " — but this model's ladder counts "
                       f"a STOCK ({unit_noun or 'unit'}s held at once), and phrasing a "
                       "stock as a per-period flow overstates the acquisition rate "
                       "(320 active seats is not 320 seats acquired every month)")
    for section, value, period, qualifier in stated:
        # Restate the prose's figure in the ladder's period before comparing. "23 bookings
        # per day" beside a 57/month plan is a claim of 690/month, and comparing 23 to 57
        # would have called it merely low rather than 12x the plan.
        as_ladder = value * (per_year[period] / per_year[ladder_period])
        if qualifier is not None:
            # A qualified claim ('5 creator visits/month', '15 referral bookings monthly')
            # is a slice or cadence the ladder cannot demand rung-equality of — but demand
            # arithmetic still bounds it: a segment of demand cannot exceed all demand
            # (run6 claimed 100 commuter drinks/day against a 13.5/day ceiling).
            if as_ladder > ceiling_bound * 1.005:
                bad.append(f"{section} states {value:g} {qualifier} "
                           f"{unit_noun or 'units'}/{period}, which would exceed the "
                           f"obtainable ceiling ({ceiling_bound:,.1f}/{ladder_period}) — "
                           f"a segment of demand cannot be bigger than all demand")
            continue
        if not any(_is_rung(as_ladder, v) for v in rungs.values()):
            bad.append(f"{section} states {value:g}/{period}")
    if bad:
        rung_txt = ", ".join(f"{k} {v:,.1f}/{ladder_period}" for k, v in rungs.items())
        return Finding(False,
                       f"{'; '.join(bad[:4])} — none of which is a rung of the ladder "
                       f"({rung_txt}). A volume a section chose for itself is how one "
                       f"report came to recommend 150/day and 250/day at the same time")
    return Finding(True, f"{len(stated)} stated volume(s), every one a ladder rung")
