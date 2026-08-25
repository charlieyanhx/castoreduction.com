"""
One renderer, two callers.

The report HTML was built inline inside the `/report.html` route, which made it reachable
only by an HTTP request. `run_plan` therefore verified each report with `html=None`, and
MEASURED, 10 of the invariants -- every one of them fail-severity -- can only answer when
they can read the rendered page:

    D02 report renders (>1KB HTML)          D36 validation warns surfaced
    D06 rendered report free of SaaS        D41 no empty per-customer price
    D24 withheld profit never a number      D43 no dead in-page nav anchors
    D25 provenance chip never overclaims    D45 cannot-decode notice not self-refuting
    D27 no impossible share-of-SOM claim    D48 shipped report attributes its sections

So the in-run verification pass -- the one whose whole purpose is "what would have gone out
wrong" -- was structurally blind to the entire class of defects that only exist once the
report is a page. It reported those 10 as not-applicable and called the rest a verdict.

Extracting the render makes the page available before it ships, which is the only way those
detectors can run at the moment they matter.
"""
from __future__ import annotations

import logging
from datetime import datetime

import charts
from report.section_provenance import SECTION_SOURCES  # noqa: F401  (template may read it)

log = logging.getLogger("mrp.report.render")


def _ladder_period(r: dict) -> str:
    """The period this venture plans in — asked of `financials`, never decided here.

    A second place deciding a period is exactly the defect C6 spent a day consolidating
    away. Falls back to "day", which is what the template hardcoded before, so a result too
    thin to judge renders as it always did.
    """
    try:
        from financials import ladder_inputs
        return ladder_inputs(r.get("economics"), r.get("market_sizing"),
                             (r.get("business_model") or {}).get("kind"))["period"]
    except Exception:                                        # noqa: BLE001
        return "day"


def render_report_html(result: dict, job_id: str = "", debug: int = 0,
                       annotate: int = 0) -> str:
    """Render one report to HTML from its result dict. Pure: no DB, no request."""
    from api import SafeUndefined, display_title   # local: api imports plan, plan imports us
    j = {"result": result or {}}
    from jinja2 import Environment, FileSystemLoader
    from datetime import datetime

    from api import TEMPLATES_DIR       # module-relative; see api.py
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True,
                      undefined=SafeUndefined)
    tpl = env.get_template("report.html")

    r = j["result"] or {}
    profile = r.get("profile", {})
    profile = {**profile, "name": display_title(profile)}
    four_ps = r.get("four_ps", {})
    viability = r.get("viability", {})
    validation = r.get("validation", {})
    psm = (r.get("pricing", {}) or {}).get("psm", {})
    competitors = (r.get("discover", {}).get("synthesis", {}) or {}).get("ranked_opportunities", [])

    # Color for viability score
    score = viability.get("viability_score") or 0
    if score >= 70:
        viability_color = "#10b981"
    elif score >= 40:
        viability_color = "#f59e0b"
    else:
        viability_color = "#ef4444"

    # Render competitor map SVG if clustering data exists
    from charts import competitor_map_svg
    import charts
    from report.section_provenance import build_section_provenance
    clustering = r.get("clustering")
    whitespace = r.get("whitespace")
    competitor_chart = competitor_map_svg(clustering, whitespace) if clustering else ""

    # Build a transparent "data sources used" summary for the report
    sigs = (r.get("discover", {}).get("steps", {}) or {}).get("signals", [])
    audience = r.get("audience", {})
    cp = r.get("competitor_pricing", {})
    _geo_sourced = bool((r.get("discover", {}) or {}).get("geo_sourced"))
    _census_src = str((r.get("market_sizing") or {}).get("competitors_source") or "").lower()
    _aud_ev = audience.get("_evidence", {}) or {}
    sources_used = {
        "google_trends": any(s.get("trend_slope") is not None for s in sigs),
        "trustpilot": any(s.get("trustpilot_reviews") is not None for s in sigs) or _aud_ev.get("trustpilot_review_count", 0) > 0,
        "google_reviews": _aud_ev.get("google_review_count", 0) > 0
                          or "google maps" in _census_src,
        "overture_maps": "overture" in _census_src,
        "reddit": any((s.get("reddit_mentions") or 0) > 0 for s in sigs) or _aud_ev.get("reddit_post_count", 0) > 0,
        "wayback_machine": any(s.get("wayback_avg_per_month") is not None for s in sigs),
        "instagram": any(s.get("ig_followers") is not None for s in sigs),
        "domain_age_rdap": any(s.get("domain_age_days") is not None for s in sigs),
        "review_articles": _aud_ev.get("article_count", 0) > 0,
        "competitor_homepage_scrape": len(sigs) > 0,
        "competitor_pricing": (cp.get("competitors_with_prices", 0) or 0) > 0,
        "clustering": clustering is not None and not clustering.get("error"),
        "market_sizing": (r.get("market_sizing") is not None) and not (r.get("market_sizing") or {}).get("error"),
    }
    # P3: the chip row lists what the venture's SHAPE could use, not every instrument
    # the codebase owns — a struck-through chip reads as a pipeline failure. Web-brand
    # instruments are omitted on geo-sourced rosters (venues rarely own a web presence);
    # the geo instruments are omitted on web-brand rosters. Fired chips always stay.
    _WEB_BRAND_ONLY = ("google_trends", "wayback_machine", "instagram",
                       "domain_age_rdap")
    _GEO_ONLY = ("overture_maps", "google_reviews")
    _inapplicable = _WEB_BRAND_ONLY if _geo_sourced else _GEO_ONLY
    sources_used = {k: v for k, v in sources_used.items()
                    if v or k not in _inapplicable}

    from market_sizing import format_currency

    # The refinement layer (iteration.py): reader annotations, Q&A, revision stamp. Loaded
    # here so BOTH the HTML route and the PDF derive the revised page from artifact + layer
    # — the original result JSON is never touched.
    try:
        import iteration as _iteration
        _iter_state = _iteration.get_state(job_id) if job_id else None
        if _iter_state is not None and not _iteration.has_content(_iter_state):
            _iter_state = None
    except Exception:                                # noqa: BLE001 - the layer is optional
        _iter_state = None

    html = tpl.render(
        iteration=_iter_state,
        annotate=bool(annotate),
        job_id=job_id,
        profile=profile,
        market_sizing=r.get("market_sizing"),
        financials=r.get("financials"),
        personas=r.get("personas"),
        audiences=r.get("audiences"),
        audiences_undecodable=r.get("audiences_undecodable"),
        deltas=r.get("_deltas_vs_previous"),
        previous_job_id=r.get("_previous_job_id"),
        format_currency=format_currency,
        four_ps=four_ps,
        viability=viability,
        viability_color=viability_color,
        validation=validation,
        # W6-1: what the pre-publication verifier found on THIS report.
        verification=r.get("verification"),
        # W6: the research crew's integrated brief (deep effort only).
        research_brief=r.get("research_brief"),
        psm=psm,
        competitors=competitors,
        # R4 rank 10: reference/off-category entries partitioned out of the competitor
        # roster — shown separately so they don't count as competitors.
        reference_cases=(r.get("discover", {}).get("synthesis", {}) or {}).get("reference_cases", []),
        not_shown_candidates=(r.get("discover", {}).get("synthesis", {}) or {}).get("not_shown", []),
        # Debuggable report: section→script provenance + the ?debug=1 toggle.
        section_provenance=build_section_provenance(r),
        debug=bool(debug),
        competitor_chart=competitor_chart,
        clustering=clustering,
        whitespace=whitespace,
        steps_completed=r.get("_steps_completed", []),
        duration_seconds=r.get("_duration_seconds"),
        generated_date=datetime.now().strftime("%Y-%m-%d %H:%M"),
        sources_used=sources_used,
        competitor_pricing=cp,
        reddit_signal=r.get("reddit_signal"),
        economics=r.get("economics"),
        pricing_benchmark=(r.get("pricing") or {}).get("benchmark"),
        # WHICH PERIOD THIS VENTURE PLANS IN, from the one function that decides it. The
        # break-even tiles hardcoded "/day", so a consultancy read "0.3 projects/day" —
        # arithmetically right (30x12 = 360 = _DAYS_PER_YEAR) and useless. Passing the
        # ladder's period rather than adding a second rule here is the whole point: the
        # computation stays in business_model, the CHOICE stays in financials.
        ladder_period=_ladder_period(r),
        # The price of record has been computed since R4 rank 16 and rendered nowhere, so
        # its provenance — including "we read no price at all" — existed only for whoever
        # opened the artifact. Same shape as #83's som_anchor: a disclosure that never
        # reaches the page is not a disclosure.
        price_of_record=(r.get("pricing") or {}).get("price_of_record"),
        differentiators=r.get("differentiators"),
        customer_universe=r.get("customer_universe"),
        segment_ranking=r.get("segment_ranking"),
        segment_radar=(charts.segment_radar_svg((r.get("segment_ranking") or {}).get("top_pick", {}))
                      if (r.get("segment_ranking") or {}).get("top_pick") else ""),
        # Iter 41: max_diff was being computed (11 features in cycle 5) but never passed to template
        max_diff=r.get("max_diff"),
        # cycle33: STORM-style multi-perspective consumer research
        consumer_research=r.get("consumer_research"),
        market_scale=r.get("market_scale"),
        # cycle33 C5: stated-vs-recommended price reconciliation (no silent re-pricing)
        price_reconciliation=r.get("price_reconciliation"),
        # cycle33: generator-evaluator-refine audit (present only when refine=True)
        refine_audit=r.get("_refine"),
        # cycle35: surface backend rigor to the UX (no dark capabilities)
        integrity=__import__("plan").build_integrity_summary(r),
        # cycle36: flag a run crippled by transient LLM/network failures (never present
        # $0 TAM / failed sections as real findings — tell the reader to regenerate).
        run_health=__import__("plan").assess_run_health(r),
        # cycle37: business model (transactional retail vs subscription) → model-aware
        # pricing / unit-economics / financials rendering.
        business_model_kind=r.get("business_model_kind"),
        # The kind AND whether the brief actually said so. A disclosure that reaches
        # the JSON and not the template is not a disclosure — this codebase has
        # shipped that shape before (the SOM anchor block, rendered nowhere).
        business_model=r.get("business_model"),
        # DEBUG-ONLY: the raw per-run call log (every tool invocation, including failures).
        # It was passed unconditionally, so the "🔍 Data Provenance (debug)" table — raw
        # HTTPSConnectionPool errors, garbage discovery domains and all — shipped inside the
        # buyer's report on every default render (measured on run9.html). The template's own
        # comment marks it DEBUG and the sentence-annotator below already honours the flag;
        # the wiring here just ignored it. The reader-facing "How each section was produced"
        # table is separate and stays.
        provenance=__import__("plan").build_provenance_summary(r) if debug else None,
    )
    if debug:
        # Sentence-level provenance: mark every result-derived run of text with the exact
        # result path behind it, so a sentence that reads wrong names the field — and the
        # script — to go and read. Debug-only: this changes the page's bytes (never its
        # words), and it must not reach the buyer's report or the PDF.
        from report.trace import annotate as _annotate
        html, _trace_stats = _annotate(html, r)
        log.info("[report] sentence trace: %d/%d blocks attributed to a result path",
                 _trace_stats["matched"], _trace_stats["blocks"])
    return html


