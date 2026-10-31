"""gates/sizing.py — TAM, SAM, SOM and the trade area: does the funnel hold, and is each figure the one its label claims.

16 of the 61 deterministic detectors. Each takes (result, html) and
returns a Finding; none of them share state, which is why they split cleanly.
"""
from __future__ import annotations

import math
import re
from typing import Optional
from gates.common import Finding, not_applicable, _num


def d03_single_som(r: dict, html: Optional[str]) -> Finding:
    """The SOM the sizing published is the SOM the financials spent.

    Two numbers for the same quantity is the dual-SOM defect: the funnel shows one figure
    and the revenue scenarios are built on another, so the arithmetic a reader checks by
    hand does not reconcile.
    """
    som = _num(((r.get("market_sizing") or {}).get("som") or {}).get("mid"))
    used = _num(((r.get("financials") or {}).get("assumptions") or {}).get("som_mid_used"))
    if som is None or used is None:
        return Finding(None, "SOM or financials absent")
    return Finding(abs(som - used) < 0.5, f"sizing SOM={som:,.0f} vs financials={used:,.0f}")


def d04_funnel_order(r: dict, html: Optional[str]) -> Finding:
    """TAM >= SAM >= SOM, at EVERY edge of the range, not only at the mid.

    Checking the mid alone passed a report whose SAM.high exceeded TAM.high, because the
    clamp scaled the mid and let the edges move independently. A funnel that inverts at
    its optimistic edge is still an inverted funnel.
    """
    # R4 rank 17: check every EDGE (low/mid/high), not just the mid — the mid clamp
    # scaled high independently and let SAM.high exceed TAM.high on ordered mids (3/16).
    ms = r.get("market_sizing") or {}
    blocks = {k: {e: _num((ms.get(k) or {}).get(e)) for e in ("low", "mid", "high")}
              for k in ("tam", "sam", "som")}
    tam, sam, som = blocks["tam"], blocks["sam"], blocks["som"]
    if tam["mid"] is None or sam["mid"] is None or som["mid"] is None:
        return Finding(None, "funnel incomplete")
    for edge in ("low", "mid", "high"):
        t, s, o = tam[edge], sam[edge], som[edge]
        if s is not None and t is not None and s > t:
            return Finding(False, f"SAM.{edge} {s:,.0f} > TAM.{edge} {t:,.0f}")
        if o is not None and s is not None and o > s:
            return Finding(False, f"SOM.{edge} {o:,.0f} > SAM.{edge} {s:,.0f}")
    return Finding(True, f"funnel ordered on all edges (mids {som['mid']:,.0f} <= "
                         f"{sam['mid']:,.0f} <= {tam['mid']:,.0f})")


def d23_at_som_matches_its_label(r: dict, html: Optional[str]) -> Finding:
    """`at_som_volume` must be the volume its own label claims.

    The dominant R12 integrity defect: Unit Economics computed profitability at
    som.HIGH while labelling it `som_capture_pct: 100.0` and titling the box "at the
    obtainable SOM volume" — the same volume the scenario table on the facing page
    calls "130% of SOM (aggressive)". A buyer read it as "profitable at our obtainable
    market". 12/16 corpus ventures. FAILS when the implied capture disagrees with the
    stated one; N/A when there is no at-SOM claim or no SOM to check against."""
    asv = ((r.get("economics") or {}).get("at_som_volume") or {})
    monthly = _num(asv.get("monthly_revenue_usd"))
    stated = _num(asv.get("som_capture_pct"))
    som_mid = _num(((r.get("market_sizing") or {}).get("som") or {}).get("mid"))
    if monthly is None or stated is None or not som_mid:
        return Finding(None, "no at-SOM claim or no SOM to check against")
    implied = (monthly * 12) / som_mid * 100
    ok = abs(implied - stated) <= max(2.0, stated * 0.02)
    return Finding(ok, f"at-SOM revenue implies {implied:.0f}% of SOM but is labelled "
                       f"{stated:.0f}%" if not ok else
                       f"at-SOM volume matches its {stated:.0f}% label")


_SOM_SHARE_RE = re.compile(r"(\d{2,4}(?:\.\d+)?)\s*% of SOM")


def d27_som_share_claims_possible(r: dict, html: Optional[str]) -> Finding:
    """No rendered share of SOM may exceed 100%, and the scenario construction must
    reach the reader (R4 rank 3, 16/16).

    The old label divided each scenario's Y3 ceiling by som_mid and printed the
    ratio as "% of SOM by Y3" — but the ceilings ARE the SOM band, so aggressive
    printed 120-200% of SOM: more than the obtainable market, by definition
    impossible. And `assumptions.scenario_basis`, the one sentence explaining the
    construction, was emitted in JSON and rendered nowhere."""
    fin = r.get("financials") or {}
    if not fin.get("scenarios") or html is None:
        return Finding(None, "no financials or no HTML")
    impossible = sorted({m for m in _SOM_SHARE_RE.findall(html) if float(m) > 100})
    if impossible:
        return Finding(False, "impossible share of SOM rendered: "
                              + ", ".join(f"{v}%" for v in impossible[:4]))
    basis = str((fin.get("assumptions") or {}).get("scenario_basis") or "")
    if basis:
        # Compare against UNESCAPED html: Jinja autoescape turns the basis text's
        # apostrophe into &#39;, and a raw substring check reported a rendered
        # sentence as missing (caught live on the first re-render).
        import html as _html_mod
        first_clause = basis.split(":")[0].strip()
        if first_clause and first_clause not in _html_mod.unescape(html):
            return Finding(False, "scenario_basis is in the JSON but its first "
                                  "clause is rendered nowhere")
    return Finding(True, "share claims possible; scenario basis rendered")


_TAM_FIG_RE = re.compile(r"TAM[^$]{0,12}\$?\s*([\d.]+)\s*([BMK])\b", re.I)


def _tam_figures(text: str) -> list[float]:
    """Dollar magnitudes explicitly labeled 'TAM $X' in a free-text string."""
    out = []
    for m in _TAM_FIG_RE.finditer(text or ""):
        try:
            out.append(float(m.group(1)) * {"B": 1e9, "M": 1e6, "K": 1e3}[m.group(2).upper()])
        except (ValueError, KeyError):
            pass
    return out


def d15_tam_coherent_across_sections(r: dict, html: Optional[str]) -> Finding:
    """C1 single-value coherence (the audit's cross-cutting invariant, ported to a
    deterministic detector): every 'TAM $X' figure cited in the SAM derivation must
    match the headline tam.mid. The R4 audit's dominant CRITICAL cluster (9/16 reports)
    was triangulation rewriting tam.mid to the median while sam.calculation /
    serviceability_waterfall kept citing the pre-triangulation TAM — two different TAMs
    in one section. N/A when the SAM derivation is a bottom-up build with no TAM anchor."""
    ms = r.get("market_sizing") or {}
    tam_mid = _num((ms.get("tam") or {}).get("mid"))
    if not tam_mid:
        return Finding(None, "no headline TAM")
    sam = ms.get("sam") or {}
    figs = _tam_figures(sam.get("calculation") or "") + _tam_figures(sam.get("serviceability_waterfall") or "")
    if not figs:
        return Finding(None, "SAM derivation cites no TAM figure (bottom-up)")
    off = [f for f in figs if abs(f - tam_mid) / tam_mid > 0.10]
    return Finding(not off,
                   f"SAM cites TAM {[round(f/1e6) for f in off]}M but headline is {round(tam_mid/1e6)}M"
                   if off else f"SAM-derivation TAM matches headline ({round(tam_mid/1e6)}M)")


def d35_tam_method_divergence_disclosed(r: dict, html: Optional[str]) -> Finding:
    """R4 rank 12: when the three TAM methods (top-down/bottom-up/analog) diverge by
    more than 3x, that divergence must be disclosed — not collapsed to a single point
    with spread 0.0. Every method shares the 'llm' origin, so triangulate reduces them
    to one median and reports a cross-origin spread of 0.0 that reads as "converged"
    above tables actually spanning 8-28x (800c261b 27.8x). FAIL when methods diverge
    >3x but the triangulation reports converged/spread 0 or carries no raw_spread."""
    tam = ((r.get("market_sizing") or {}).get("tam") or {})
    vals = [float(tam[k]["value_usd"]) for k in
            ("method_top_down", "method_bottom_up", "method_analog")
            if isinstance(tam.get(k), dict) and _num(tam[k].get("value_usd"))]
    if len(vals) < 2 or min(vals) <= 0:
        return Finding(None, "fewer than 2 numeric TAM methods")
    span = max(vals) / min(vals)
    if span <= 3.0:
        return Finding(True, f"TAM methods span {span:.1f}x — coherent")
    tri = tam.get("triangulation") or {}
    spread, raw = tri.get("spread"), tri.get("raw_spread")
    if tri.get("converged") is True or (spread is not None and spread == 0):
        return Finding(False, f"TAM methods span {span:.1f}x but triangulation reports "
                              f"spread={spread}/converged={tri.get('converged')} — "
                              "the divergence is hidden behind one median")
    if raw is None:
        return Finding(False, f"TAM methods span {span:.1f}x but no raw_spread is "
                              "disclosed — the divergence is invisible")
    # raw_spread = (max-min)/mid SATURATES: with mid the median it approaches max/mid and
    # stops, so $8M/$1.568B/$2.5B reads 159% and would still read 159% if the bottom-up
    # method returned one cent. Accepting it as the disclosure let this gate emit "spans
    # 312.3x, disclosed (raw_spread=1.589)" — its own two halves disagreeing. Once the
    # methods genuinely diverge, the unbounded measure has to be there too.
    fold = _num(tri.get("raw_fold"))
    if fold:
        return Finding(True, f"TAM methods span {span:.1f}x, disclosed "
                             f"(raw_fold={fold:.1f}x, raw_spread={raw})")
    # No raw_fold: judge the disclosure by whether it is COMMENSURATE with the span, not by
    # whether it exists. Measured across all 40 stored artifacts, failing on absence alone
    # newly withheld two that disclose honestly — run1 carries raw_spread=64.0 against a
    # 173.6x span, which understates but plainly conveys "these disagree enormously". What
    # must fail is a figure that CANNOT convey it: raw_spread/span collapses to about
    # min/mid, so c98_subscription discloses 2.9 for 102.1x and d62bc04f 1.589 for 312.3x.
    # Within an order of magnitude of the truth still informs a reader; two orders does not.
    if _num(raw) and float(raw) * 10.0 >= span:
        return Finding(True, f"TAM methods span {span:.1f}x, disclosed "
                             f"(raw_spread={raw} — no raw_fold, but commensurate)")
    return Finding(False, f"TAM methods span {span:.1f}x but the only disclosed divergence "
                          f"is raw_spread={raw}, which saturates near max/median "
                          f"({float(raw or 0) * 100:.0f}% for a {span:.0f}x disagreement) and "
                          "cannot express a span this wide")


def d38_sam_slice_authoritative(r: dict, html: Optional[str]) -> Finding:
    """R4 rank 15: the serviceable slice a report stands behind is sam.mid / tam.mid.
    The LLM's key_assumption prose stated a different % (174ae091: SAM is 90% of TAM
    but the assumption said '15%') and was rendered nowhere. FAIL when a SAM lacks the
    computed `serviceable_slice_pct` or that figure disagrees with sam.mid/tam.mid."""
    ms = r.get("market_sizing") or {}
    tam_mid = _num((ms.get("tam") or {}).get("mid"))
    sam = ms.get("sam") or {}
    sam_mid = _num(sam.get("mid"))
    if not tam_mid or not sam_mid:
        return Finding(None, "no SAM/TAM mids")
    computed = sam_mid / tam_mid * 100.0
    slice_pct = _num(sam.get("serviceable_slice_pct"))
    if slice_pct is None:
        return Finding(False, f"SAM has no serviceable_slice_pct (computed slice is "
                              f"{computed:.1f}% of TAM — the authoritative figure)")
    if abs(slice_pct - computed) > 0.5:
        return Finding(False, f"serviceable_slice_pct {slice_pct}% != computed "
                              f"{computed:.1f}% (sam.mid/tam.mid)")
    return Finding(True, f"serviceable slice {slice_pct}% matches sam.mid/tam.mid")


def d40_hyperlocal_som_basis_honest(r: dict, html: Optional[str]) -> Finding:
    """R4 rank 18: a hyperlocal SOM that rests on an UNSOURCED single-unit revenue
    estimate must not be described as 'capacity-based' — only a real seats × turns
    model is capacity-based. 4/6 hyperlocal reports claimed capacity while the SOM was
    an LLM guess (no seat data). FAIL when the notes say the SOM is 'capacity-based'
    without any seat/capacity evidence."""
    ms = r.get("market_sizing") or {}
    scale = (ms.get("scale_decision") or {}).get("scale")
    if scale != "hyperlocal":
        return not_applicable("not a hyperlocal venture")
    notes = " ".join(ms.get("notes") or [])
    if not notes:
        return Finding(None, "no sizing notes")
    if "capacity-based" in notes and "seat" not in notes.lower():
        return Finding(False, "SOM described as 'capacity-based' with no seat/capacity "
                              "model — it is an unsourced single-unit revenue estimate")
    return Finding(True, "hyperlocal SOM basis described honestly")


def d49_trade_area_matches_its_radius(r: dict, html: Optional[str]) -> Finding:
    """Audit high #4: a hyperlocal trade-area household count must be consistent with the
    radius it claims, not with the county the address happens to sit in.

    `size_hyperlocal` sizes ONE premise inside `radius_m`. The count used to come straight
    from `acs_demographics` for the whole COUNTY, with the radius ignored and the result
    labelled confidence="high" / "US Census ACS". The overstatement is exactly
    county_land_km2 / catchment_km2; measured against live TIGERweb land areas for a 3km
    catchment: Los Angeles 372x, Gallatin MT 239x, Harris TX 156x, Cook IL 87x.

    The check is a density ceiling, not an equality: households / catchment_km2 must be a
    residentially plausible density. Manhattan, the densest US county, is ~13,500
    households/km², so 20,000 is a generous ceiling that no real catchment reaches and that
    every county-scale figure blows through (LA County as a 3km trade area implies ~117,000
    households/km²).

    APPLICABILITY IS THE DATA, NOT THE LABEL. This used to decline on any scale outside
    ("hyperlocal", "trade_area", ""), which excused it from every `regional` report — and
    those are exactly the reports where a single catchment is standing in for a multi-site
    footprint, so an implausible density is most diagnostic there, not least. A report that
    publishes a radius and a trade-area household count has made a checkable claim whatever
    it calls its scale; one that publishes neither has nothing to check."""
    ms = r.get("market_sizing") or {}
    households = ms.get("trade_area_households")
    radius_m = ms.get("radius_m")
    if households is None or not radius_m:
        # Distinguish "this venture has no trade area to disclose" from "it should have one
        # and did not". Keyed on the METHOD, not the scale label, per the note above: a
        # `regional` report that sized by catchment stays in scope and must still answer.
        if "trade_area" not in str(ms.get("method") or "").lower():
            return not_applicable(
                f"sizing method {ms.get('method') or 'unrecorded'!r} is not a trade-area "
                "catchment, so there is no radius or household count to check")
        return Finding(None, "no trade area disclosed (no radius or household count)")
    area = math.pi * (_num(radius_m) / 1000.0) ** 2
    if area <= 0:
        return Finding(None, "non-positive catchment")
    density = _num(households) / area
    MAX_PLAUSIBLE = 20_000.0        # Manhattan, the densest US county, is ~13,500 hh/km²
    if density > MAX_PLAUSIBLE:
        return Finding(False, f"{_num(households):,.0f} households in a "
                              f"{_num(radius_m) / 1000:.1f} km catchment "
                              f"({area:,.1f} km²) implies {density:,.0f} households/km² — "
                              "denser than Manhattan, so this is a county-scale count "
                              "presented as a trade area")
    return Finding(True, f"{_num(households):,.0f} households over {area:,.1f} km² "
                         f"= {density:,.0f} households/km², a plausible catchment")


def d50_no_publishable_sizing_without_numbers(r: dict, html: Optional[str]) -> Finding:
    """A sizing with no numbers must never claim to be publishable.

    Measured on a live run: size_hyperlocal returned tam/sam/som all empty, with its own
    note "households or spend unavailable — TAM not computed", and the gate reported
    passed=true / publishable=True. Every check in validate._check is guarded on the value
    being numeric, so absent numbers satisfy all of them vacuously — "nothing to check" was
    indistinguishable from "checked and fine". The report's unpublishable banner and every
    downstream refusal are keyed on that verdict, so none of them fired.

    Producing no numbers is ALLOWED — a run can legitimately fail to size a market. Saying
    those absent numbers are publishable is not. N/A when there is no sizing at all."""
    ms = r.get("market_sizing") or {}
    if not ms:
        return Finding(None, "no market_sizing on this report")
    mids = [(ms.get(k) or {}).get("mid") for k in ("tam", "sam", "som")]
    has_number = any(isinstance(v, (int, float)) and not isinstance(v, bool) for v in mids)
    if has_number:
        return Finding(True, "sizing produced at least one figure")
    if ms.get("publishable"):
        return Finding(False, "sizing produced no TAM/SAM/SOM value yet is marked "
                              "publishable — an empty sizing passed the gate vacuously, so "
                              "the report ships with no market size and no warning")
    return Finding(True, "sizing produced no figures and honestly says so "
                         "(publishable=False)")


_SAM_FIG_RE = re.compile(r"SAM[^$]{0,12}\$?\s*([\d.]+)\s*([BMK])\b", re.I)


def _sam_figures(text: str) -> list[float]:
    """Dollar magnitudes explicitly labeled 'SAM $X' in a free-text string."""
    out = []
    for m in _SAM_FIG_RE.finditer(text or ""):
        try:
            out.append(float(m.group(1)) * {"B": 1e9, "M": 1e6, "K": 1e3}[m.group(2).upper()])
        except (ValueError, KeyError):
            pass
    return out


def d20_sam_self_consistent(r: dict, html: Optional[str]) -> Finding:
    """C1: every 'SAM $X' figure cited in sam.calculation / sam.serviceability_waterfall
    must match the headline sam.mid — a SEPARATE defect from D15 (which checks the TAM
    figure in those same strings). Root cause: sam.mid can be moved by triangulation OR
    by the funnel-ordering clamp (_enforce_sizing_ordering), and the narrative strings
    used to only get re-synced once, early, before either of those could run. Real R4
    critical (174ae091): mid=$195.8M vs strings say $202.5M. N/A when the SAM narrative
    cites no SAM figure at all (a bottom-up-only calculation).

    Tolerance note: comparing raw sam_mid to a figure parsed back out of a 1-decimal-
    place display string (e.g. "$1.4B") is NOT apples-to-apples — format_currency's own
    rounding can introduce a ~3-4% artifact (1.35B displays as "1.4B", parses back as
    1.40B) that is the SAME magnitude as the real bug this detects. So sam_mid is passed
    through the identical formatter before comparing — both sides see the same rounding,
    and only a genuinely different underlying number trips the (now tight) threshold."""
    ms = r.get("market_sizing") or {}
    sam = ms.get("sam") or {}
    sam_mid = _num(sam.get("mid"))
    if not sam_mid:
        return Finding(None, "no headline SAM")
    figs = _sam_figures(sam.get("calculation") or "") + _sam_figures(sam.get("serviceability_waterfall") or "")
    if not figs:
        return Finding(None, "SAM narrative cites no SAM figure")
    from market_sizing import format_currency
    canon = _sam_figures(f"SAM {format_currency(sam_mid)}")
    baseline = canon[0] if canon else sam_mid
    off = [f for f in figs if abs(f - baseline) / baseline > 0.005]
    return Finding(not off,
                   f"SAM narrative cites {[round(f/1e6) for f in off]}M but headline is {round(sam_mid/1e6)}M"
                   if off else f"SAM narrative matches headline ({round(sam_mid/1e6)}M)")


def d52_chosen_sizing_skill_actually_ran(r: dict, html: str | None) -> Finding:
    """The sizing skill the classifier NAMED must be the one that produced the numbers.

    Measured on a real end-to-end run (out/live/run1.*): the classifier returned
    {"scale": "hyperlocal", "sizing_skill": "size_hyperlocal"} and size_hyperlocal never ran
    -- no sizing step appears in _steps_completed at all -- yet market_sizing carried a TAM,
    three figures and publishable=True. Every figure was model-narrated, and the bottom-up
    one cited "Census ACS Mission District demographics & BLS QCEW NAICS 722515" while zero
    Census/BLS calls were made and data_origin was None.

    A trade-area model leaves a footprint: a radius, a catchment, a household count. LLM
    sizing leaves none. So the presence of that footprint is the check.

    N/A for national/digital ventures, which legitimately have no trade area, and when no
    scale decision was recorded."""
    scale_dec = r.get("market_scale") or (r.get("market_sizing") or {}).get("scale_decision")
    if not scale_dec:
        return Finding(None, "no scale decision recorded")
    scale = (scale_dec.get("scale") or "").lower()
    if scale not in ("hyperlocal", "regional", "national_physical"):
        return not_applicable(f"scale={scale or '?'} has no trade area to measure")

    ms = r.get("market_sizing") or {}
    skill = scale_dec.get("sizing_skill") or "the trade-area model"
    footprint = {k: ms.get(k) for k in ("radius_m", "catchment_km2", "trade_area_households")
                 if ms.get(k) is not None}

    # WHICH skill, not merely whether SOME trade-area model ran. The footprint test alone
    # cannot tell a substitution from a success: `size_by_scale` routes hyperlocal AND
    # regional into `size_hyperlocal`, which leaves the same radius/catchment/household
    # trio either way, so a 3-location chain sized as one 3 km catchment read here as
    # "size_regional ran" — the gate confirming a measurement while missing that it
    # measured the wrong thing.
    ran = ms.get("sizing_skill_ran")
    if ran and ran != skill:
        # Wave B: a DISCLOSED downgrade is not a substitution. When the geocoder said
        # the location is city-grade, the router runs the city scan instead of drawing
        # a 1.5 km ring around an arbitrary pin, and stamps why. The figures then
        # describe what they claim to describe (method=city_scan, no trade-area
        # footprint), which is the exact property this gate protects. A reroute
        # WITHOUT the stamp stays a failure — silence is still substitution.
        if (ran == "size_citywide" and ms.get("downgrade_reason")
                and ms.get("method") == "city_scan"):
            return Finding(True,
                           f"{skill} was rerouted to {ran} and disclosed: "
                           f"{ms['downgrade_reason']}")
        return Finding(False,
                       f"classifier chose {skill} for this {scale} venture and "
                       f"{ran} produced the numbers instead — the published figures "
                       f"describe what {ran} measures ({footprint or 'no footprint'}), "
                       f"not what {skill} would have. A multi-site venture sized this way "
                       f"publishes one trade area as its whole market")
    if footprint:
        # Un-stamped artifacts predate the key and fall back to the original check: a gate
        # that fails every archived report for lacking a field invented today is a gate
        # people learn to ignore.
        return Finding(True, f"{ran or skill} ran: {footprint}")

    tam = ((ms.get("tam") or {}).get("mid"))
    return Finding(False,
                   f"classifier chose {skill} for this {scale} venture and it did not run -- "
                   f"no radius, catchment or trade-area household count is present, so the "
                   f"published TAM ({tam}) is model-narrated rather than measured"
                   + ("" if ms.get("publishable") is False
                      else " AND is still marked publishable"))


def d56_local_spend_is_grounded_or_says_it_is_not(r: dict, html: str | None) -> Finding:
    """A trade-area TAM must say whether its per-household spend is LOCAL.

    THE BUG. TAM_local = trade_area_households x $3,945, where $3,945 (BLS CEX
    CXUFOODAWAYLB0101M) is the *national* all-consumer-units average. Every neighbourhood was
    priced identically -- a $32k-median tract and a $250k-median tract got the same spend --
    while acs_demographics had been returning median_hh_income on every run and nothing read it.
    Measured on the real Mission District tract, grounding it in the local income distribution
    moves spend +15.0%, and across plausible tracts the multiplier spans 0.64x to 2.23x.

    WHAT THIS ENFORCES IS DISCLOSURE, NOT ADJUSTMENT. Local income is genuinely unavailable
    sometimes -- no Census FIPS, a non-US address (ACS and BLS CEX are US-only), an ACS outage.
    Measured: all 6 stored corpus reports predate the Census key and had no local income at all.
    Demanding an adjustment would fail them for an honest limitation. What must never happen is
    a report presenting the national average with NOTHING said about it, leaving a reader to
    assume the number is local. So: adjusted and disclosed, or unadjusted and disclosed.

    Also catches the self-refuting-number class this pipeline keeps producing: a record that
    claims a multiplier whose arithmetic does not reconcile with the two figures beside it.

    ok=None only when there is no local TAM to ground. That set cannot swallow the failure --
    an absent disclosure on a PUBLISHED trade-area TAM is exactly what returns False."""
    ms = r.get("market_sizing") or {}
    if (ms.get("method") or "") != "trade_area_catchment":
        return not_applicable("not a trade-area (hyperlocal) sizing")
    tam = ms.get("tam_usd") or (ms.get("tam") or {}).get("mid")
    if not tam:
        return Finding(None, "trade-area sizing published no TAM to ground")

    adj = ms.get("spend_income_adjustment")
    if not isinstance(adj, dict) or not adj:
        return Finding(False,
                       "a trade-area TAM was published with no record of whether its "
                       "per-household spend was grounded in local income — a reader cannot "
                       "tell the national average from a local one")
    if adj.get("applied"):
        mult, nat = adj.get("multiplier"), adj.get("national_spend")
        got = adj.get("adjusted_spend")
        if not isinstance(mult, (int, float)) or mult <= 0:
            return Finding(False, f"income adjustment claims to have been applied with an "
                                  f"unusable multiplier {mult!r}")
        if not adj.get("geography"):
            return Finding(False, "income adjustment applied without naming the geography "
                                  "whose income was used")
        if isinstance(nat, (int, float)) and isinstance(got, (int, float)):
            if abs(got - nat * mult) > max(1.0, 0.01 * abs(got)):
                return Finding(False,
                               f"income-adjusted spend ${got:,.0f} does not reconcile with "
                               f"${nat:,.0f} x {mult:.4f} = ${nat * mult:,.0f}")
        return Finding(True, f"spend grounded in {adj.get('geography')} income "
                             f"(x{mult:.3f})")
    reason = (adj.get("reason") or "").strip()
    if len(reason) < 10:
        return Finding(False, "income adjustment was skipped without a usable reason "
                              f"({reason!r})")
    return Finding(True, f"national spend used and disclosed: {reason[:80]}")


# The floor is deliberately far below any real revenue-per-venue figure: a venue's revenue is
# a SLICE of the addressable spend around it, so the true bar is much higher. This only
# catches order-of-magnitude nonsense, never a tight-but-real market — measured: an honest
# hard market at $400K/venue passes; run9's $122K/venue fails.
_MIN_TAM_PER_COMPETITOR_USD = 250_000.0


def d57_market_supports_its_competitors(r: dict, html: str | None) -> Finding:
    """A trade-area market must be able to feed the competitors it says already exist.

    THE MEASUREMENT. run9 published TAM $12.5M for a trade area it also said contains 102
    operating cafes — $122,433 of total food-away spend per existing cafe, below SF rent for
    the storefront alone. Every gate passed, because every gate checks internal CONSISTENCY
    and the arithmetic downstream of a wrong input was exact (the input was the trade-area
    cap inversion: households 25x low). The wrongness was only visible from OUTSIDE the
    model: 102 real businesses were demonstrably surviving on a market the report said could
    not sustain one.

    So this is the pipeline's first EXTERNAL-plausibility invariant: if a trade-area TAM and
    a geo competitor count are both published, TAM / competitors must clear a survival floor.
    It is cause-agnostic on purpose — whatever upstream defect next produces an absurd
    sizing (bad geocode, bad land area, bad spend figure), the ratio catches it, because the
    competitor roster is measured independently of every one of those inputs.

    ok=None only when there is no trade-area TAM or no competitor count — and the missing-
    count case NAMES what was missing, so D55's coverage accounting shows a hole rather than
    this reading as fine."""
    ms = r.get("market_sizing") or {}
    if (ms.get("method") or "") != "trade_area_catchment":
        return not_applicable("not a trade-area (hyperlocal) sizing")
    tam = (ms.get("tam") or {}).get("mid") or ms.get("tam_usd")
    if not tam:
        return Finding(None, "no trade-area TAM published")
    comp = ms.get("competitors")
    if not isinstance(comp, (int, float)) or isinstance(comp, bool) or comp < 0:
        return Finding(None, "trade-area TAM published but no competitor count to check it "
                             "against — the plausibility check could not run")
    if comp == 0:
        return Finding(True, "no competitors in the trade area — nothing to divide by, and "
                             "an empty market may legitimately be small")
    per = tam / comp
    if per < _MIN_TAM_PER_COMPETITOR_USD:
        return Finding(False,
                       f"TAM ${tam:,.0f} across {comp:,.0f} existing competitors is "
                       f"${per:,.0f} each — real venues are already surviving here, so the "
                       f"market is mis-sized, not tiny (floor: "
                       f"${_MIN_TAM_PER_COMPETITOR_USD:,.0f})")
    return Finding(True, f"${per:,.0f} of addressable market per existing competitor")


def d59_som_anchor_discloses_its_method(r: dict, html: str | None) -> Finding:
    """A hyperlocal SOM must say how it was anchored, and an unsourced anchor must show
    the other method's figure.

    THE MEASUREMENT. Same venture, same trade area, same competitor census: run14 SOM
    $390,000, run15 SOM $650,000. A 67% swing in the number every downstream verdict
    hangs off, driven entirely by _estimate_unit_revenue — an explicitly UNSOURCED LLM
    estimate of one premise's annual revenue. size_hyperlocal computes an independent
    second estimate beside it (fair share of SAM across the census); the size_by_scale
    mapping dropped both, so the report published one confident figure and no way to
    inspect it.

    This gate does NOT require the anchor to be sourced — until operator seat/turn inputs
    or published per-store benchmarks exist, an estimate is the only anchor available, and
    refusing to size would serve nobody. It requires the report to SAY the anchor is an
    estimate and to publish the alternative, so a reader can see two defensible methods
    disagreeing rather than one number that looks measured. Disclosure, not obedience —
    the D09 shape again.

    ok=None for non-hyperlocal sizings and for reports with no SOM: unchecked, which D55
    counts against coverage, rather than a silent pass."""
    ms = r.get("market_sizing") or {}
    if (ms.get("method") or "") != "trade_area_catchment":
        return not_applicable("not a trade-area (hyperlocal) sizing")
    som = (ms.get("som") or {}).get("mid") or ms.get("som_usd")
    if not som:
        return Finding(None, "no SOM published")
    anchor = ms.get("som_anchor")
    if not isinstance(anchor, dict) or not anchor.get("method"):
        return Finding(False,
                       "the report publishes a SOM with no statement of how it was "
                       "anchored — a reader cannot tell a capacity model from an "
                       "unsourced single-unit revenue guess")
    if anchor.get("sourced") and anchor.get("method") != "area_receipts_benchmark":
        # A measured seats x turns capacity model IS about this site, so a fair-share
        # alternative beside it adds little. That was the only sourced anchor when this
        # branch was written. An AREA AVERAGE is sourced and wide — a mean across every
        # establishment in a county — so the spread against fair share is exactly the
        # finding, and short-circuiting here would drop the requirement at the moment it
        # started to matter. It falls through to the alternative check below.
        return Finding(True, f"SOM anchored on {anchor.get('method')} (sourced)")
    if anchor.get("method") == "fair_share_of_sam":
        return Finding(True, "SOM is the fair-share fallback, and says so")
    if not anchor.get("alternative_usd"):
        return Finding(False,
                       f"SOM is anchored on {anchor.get('method')} (unsourced) and the "
                       f"report shows no alternative estimate beside it — the "
                       f"disagreement between the two methods is the honest uncertainty "
                       f"and it is being hidden")
    return Finding(True,
                   f"{'sourced' if anchor.get('sourced') else 'unsourced'} "
                   f"{anchor.get('method')} anchor, disclosed, with the "
                   f"{anchor.get('alternative_method')} alternative "
                   f"(${anchor.get('alternative_usd'):,.0f}) published beside it")


def d60_area_average_is_labelled(r: dict, html: Optional[str]) -> Finding:
    """An area average must never reach a reader dressed as this venture's revenue.

    #91 anchors the headline SOM on Economic Census receipts per establishment — a mean
    across every establishment in a county (525 of them for the measured venture). That is
    a defensible anchor and a dangerous string: rendered under the previous label,
    "single-unit revenue", a buyer reads it as this one unit, now with a Census citation
    attached. An adversarial review of the design called that out as strictly worse than
    the unsourced guess it replaces, because a guess at least looks like one.

    WHY THIS FIELD. plan.py::_block keeps `calculation` and DISCARDS `source`, and
    market_sizing.figures[] never reaches the template. A disclosure written into
    figures[].source is one the pipeline throws away, and a gate asserting on it would pass
    while the page said nothing — the shape this repo has already shipped three times. So
    this reads market_sizing.som.calculation, the string a reader actually gets, and the
    rendered HTML when it is available.

    Four things must be present, because each alone is insufficient: the word AVERAGE (what
    kind of number), the GEOGRAPHY (average over where), the ESTABLISHMENT COUNT (average
    over how many — two and 525 are different claims), and MEAN (over a right-skewed
    distribution, and the Census does not publish the median).
    """
    ms = r.get("market_sizing") or {}
    if (ms.get("method") or "") != "trade_area_catchment":
        return not_applicable("not a trade-area (hyperlocal) sizing")
    anchor = ms.get("som_anchor") or {}
    if anchor.get("method") != "area_receipts_benchmark":
        return Finding(None, "SOM is not anchored on an area receipts benchmark")

    calc = ((ms.get("som") or {}).get("calculation") or "")
    if not calc.strip():
        return Finding(False,
                       "the SOM is an area average and the report publishes no "
                       "calculation for it — the reader gets the number with none of "
                       "the qualification that makes it honest")

    def _missing(text: str) -> list[str]:
        low = (text or "").lower()
        out = []
        if "average" not in low:
            out.append("the word 'average'")
        if "mean" not in low:
            out.append("'mean' (the median is lower and unpublished)")
        # Any digit run counts — MEASURED (bb08c5c3): the producer writes the count bare
        # ("9482 establishments") and this regex demanded thousands separators, so the
        # gate withheld a report for missing a disclosure that sat in the very string
        # its finding quoted. A formatting choice must never decide a withholding.
        if not re.search(r"\b\d[\d,]*\s+establishments\b", low):
            out.append("the establishment count it averages over")
        if not re.search(r"\b(county|parish|borough|state|nation|united states)\b", low):
            out.append("the geography it averages over")
        return out

    gaps = _missing(calc)
    if gaps:
        return Finding(False,
                       f"the SOM calculation presents an area average without "
                       f"{', '.join(gaps)} — a reader cannot tell it is not this site's "
                       f"revenue: {calc[:160]}")
    if html:
        html_gaps = _missing(html)
        if html_gaps:
            return Finding(False,
                           f"the area-average qualification reaches the JSON but not the "
                           f"rendered page, which is missing {', '.join(html_gaps)}")
    return Finding(True, "the SOM is disclosed as an area average, with its geography, "
                         "its establishment count and its statistic")
