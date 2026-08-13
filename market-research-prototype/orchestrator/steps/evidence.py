"""orchestrator/steps/evidence.py — the parallel evidence phase (Steps 6/6c/6d/6e/10b/11).

Extracted from run_plan (god-function dismantling, wave 4) — at ~200 lines the biggest
single block in the function, and measured at 43% of a steady-state run's wall clock.
Pure move: same fan-out, same per-future timeouts, same persist semantics (reddit/HN/
multi-source signals, audience backwards-compat, cannot_decode kept apart), same
industry-aware queried-map. The bundle it returns is what run_plan's later steps used to
read from shared locals — now they read a named contract instead.

Deliberately NOT here: the competitor_pricing persist (it lands after consumer research
in run_plan, and a move must not reorder result-key insertion), and any step_scope label
(this is a phase of several recorded steps, not one step).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Callable

from logger import get

from . import record_dropped_output, step_done

log = get("plan.steps.evidence")

_TECH_KW = (
    "saas", "software", "developer", "dev tool", "devtool", " api", "sdk", "devops",
    "cloud", "machine learning", "data platform", "analytics platform", "cybersecurity",
    "infosec", "b2b saas", "open source", "programming", "engineering tool", "ai platform",
    "ml platform", "data pipeline", "observability", "infrastructure software",
)


def _is_tech_venture(profile: dict | None) -> bool:
    """True if dev/tech forums (Stack Exchange / DEV.to / Lobsters) are a relevant customer-voice
    source — i.e. a software/dev/SaaS venture. Deterministic keyword check; a cafe/salon/clinic
    returns False so it isn't searched against (and judged by) tech forums."""
    p = profile or {}
    blob = f"{p.get('business_model','')} {p.get('category','')} {p.get('summary','')}".lower()
    return any(k in blob for k in _TECH_KW)


def run_evidence_step(result: dict, profile: dict, opps: list,
                      checkpoint: Callable[[], None] | None = None) -> dict:
    """Fan out every independent scrape after discover; persist the signal sections;
    return the bundle downstream steps consume.

    Decode taste for TOP-3 brands (not just top-1) to enable persona synthesis.
    Plus place + pricing scrapes — all independent.
    """
    checkpoint = checkpoint or (lambda: None)
    competitor_domains = [o["domain"] for o in opps if o.get("domain")][:8]
    top_3_comps = [o for o in opps if o.get("domain")][:3]
    # A roster with no domains silently skips FOUR sections -- audiences, personas, channels
    # and competitor prices -- because every one of them needs something to fetch. Measured:
    # run1 had 8/8 competitors with a domain and produced 3 taste decodes; run2 had 0/30
    # (OSM venues carried names only) and produced nothing, not even the honest
    # "cannot_decode" record the taste step writes when it tries and finds no voice. The
    # absence of a refusal is what made this hard to see: it looked like the step was never
    # meant to run.
    if opps and not top_3_comps:
        record_dropped_output(
            result, "audiences",
            f"no competitor carries a domain ({len(opps)} in the roster), so there was "
            "nothing to fetch customer voice from; audiences, personas, channel analysis "
            "and competitor pricing all depend on it")
    channel_data = {}

    def _taste_task_for(comp):
        from taste import decode_taste
        log.info(f"[plan] Step 6: decoding audience for {comp['brand']}")
        return decode_taste(comp["brand"], comp["domain"])

    def _channels_task():
        if not competitor_domains:
            return {}
        from place import analyze_competitor_channels
        log.info(f"[plan] Step 11: scraping channels across {len(competitor_domains)} competitors")
        return analyze_competitor_channels(competitor_domains)

    def _prices_task():
        """Scrape competitor product prices for PSM anchoring."""
        if not competitor_domains:
            return {}
        from competitor_pricing import gather_competitor_prices
        log.info(f"[plan] Step 10b: scraping prices across {len(competitor_domains)} competitors")
        # W2-5: category relevance gate — off-category pages never anchor the median.
        return gather_competitor_prices(competitor_domains[:6],
                                        category=profile.get("category", ""))

    def _reddit_task():
        """Pull Reddit customer voice for the top competitor (or category if none)."""
        from reddit_signal import fetch_signal
        target = (top_3_comps[0]["brand"] if top_3_comps else profile.get("category", ""))
        if not target:
            return {}
        log.info(f"[plan] Step 6c: pulling Reddit signal for '{target}'")
        return fetch_signal(target, max_threads=10, days_back=180)

    def _hn_task():
        """cycle25 (issue 6/7): also pull HackerNews mentions as customer voice."""
        from sources import hackernews_mentions
        target = (top_3_comps[0]["brand"] if top_3_comps else profile.get("category", ""))
        if not target:
            return []
        log.info(f"[plan] Step 6d: pulling HackerNews mentions for '{target}'")
        try:
            return hackernews_mentions(target, limit=20)
        except Exception as e:
            log.warning(f"[plan] HN fetch failed (non-fatal): {e}")
            return []

    def _multisrc_task():
        """Pull customer-voice sources in parallel — INDUSTRY-AWARE (cycle38). The dev forums
        (Stack Exchange / DEV.to / Lobsters) are only relevant to tech/dev/SaaS ventures; for a
        cafe or salon they return nothing but noise. They now run ONLY for tech ventures; every
        venture gets vertical_publication_mentions (industry trade press). All free, no key."""
        from sources import stackexchange_mentions, devto_mentions, lobsters_mentions, vertical_publication_mentions
        target = (top_3_comps[0]["brand"] if top_3_comps else profile.get("category", ""))
        if not target:
            return {}
        category = profile.get("category", "")
        is_tech = _is_tech_venture(profile)
        log.info("[plan] Step 6e: customer-voice sources for '%s' (tech_forums=%s)", target, is_tech)
        out = {"stackoverflow": [], "devto": [], "lobsters": [], "vertical_pubs": [], "_tech": is_tech}
        with ThreadPoolExecutor(max_workers=4) as p:
            futs = {"vertical_pubs": p.submit(vertical_publication_mentions, target, category, 10)}
            if is_tech:  # dev forums only help tech/dev/SaaS ventures
                futs["stackoverflow"] = p.submit(stackexchange_mentions, target, 12)
                futs["devto"] = p.submit(devto_mentions, target, 10)
                futs["lobsters"] = p.submit(lobsters_mentions, target, 10)
            for name, fut in futs.items():
                try:
                    out[name] = fut.result(timeout=25) or []
                except Exception as e:
                    log.warning(f"[plan] {name} fetch failed (non-fatal): {e}")
                    out[name] = []
        return out

    taste_results: list[dict] = []
    competitor_pricing_data = {}
    reddit_data = {}
    hn_data: list[dict] = []
    multisrc_data: dict = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        # Submit one taste decode per top brand (1, 2, or 3 in parallel)
        taste_futs = [pool.submit(_taste_task_for, c) for c in top_3_comps]
        channels_fut = pool.submit(_channels_task)
        prices_fut = pool.submit(_prices_task)
        reddit_fut = pool.submit(_reddit_task)
        hn_fut = pool.submit(_hn_task)
        multisrc_fut = pool.submit(_multisrc_task)

        # Gather taste results — iter 40: also collect cannot_decode entries
        # so the report can show "we tried but no signal" honestly.
        cannot_decode_results: list[dict] = []
        for fut in taste_futs:
            try:
                t = fut.result(timeout=120) or {}
                if t and not t.get("error"):
                    if t.get("cannot_decode"):
                        cannot_decode_results.append(t)
                    else:
                        taste_results.append(t)
            except FutureTimeoutError:
                log.warning("[plan] one taste decode timed out after 120s")

        try:
            channel_data = channels_fut.result(timeout=60) or {}
        except FutureTimeoutError:
            log.warning("[plan] channel scraping timed out after 60s")
            channel_data = {}
        try:
            competitor_pricing_data = prices_fut.result(timeout=80) or {}
        except FutureTimeoutError:
            log.warning("[plan] competitor pricing scrape timed out after 80s")
            competitor_pricing_data = {}
        try:
            reddit_data = reddit_fut.result(timeout=120) or {}
        except FutureTimeoutError:
            log.warning("[plan] reddit signal timed out after 120s")
            reddit_data = {}
        except Exception as e:
            log.warning(f"[plan] reddit signal failed (non-fatal): {e}")
            reddit_data = {}
        try:
            hn_data = hn_fut.result(timeout=30) or []
        except Exception as e:
            log.warning(f"[plan] HN signal failed (non-fatal): {e}")
            hn_data = []
        try:
            multisrc_data = multisrc_fut.result(timeout=60) or {}
        except Exception as e:
            log.warning(f"[plan] multi-source fetch failed (non-fatal): {e}")
            multisrc_data = {}

    # Persist Reddit signal even if downstream skips it
    if reddit_data:
        result["reddit_signal"] = reddit_data
        if reddit_data.get("threads_found", 0) > 0:
            step_done(result, "reddit_signal")
        checkpoint()

    # cycle25: persist HN customer voice as its own signal
    target_for_voice = top_3_comps[0]["brand"] if top_3_comps else profile.get("category", "")
    if hn_data:
        result["hn_signal"] = {
            "query": target_for_voice,
            "hits_found": len(hn_data),
            "hits": hn_data[:15],
        }
        step_done(result, "hn_signal")
        checkpoint()

    # cycle27: persist Stack Exchange + DEV.to + Lobsters
    # cycle31-r2: + vertical_pubs for non-tech verticals
    if multisrc_data:
        _is_tech_run = bool(multisrc_data.get("_tech"))
        result["multi_source_signal"] = {
            "query": target_for_voice,
            "stackoverflow": (multisrc_data.get("stackoverflow") or [])[:8],
            "devto": (multisrc_data.get("devto") or [])[:6],
            "lobsters": (multisrc_data.get("lobsters") or [])[:6],
            "vertical_pubs": (multisrc_data.get("vertical_pubs") or [])[:8],
            "counts": {
                "stackoverflow": len(multisrc_data.get("stackoverflow") or []),
                "devto": len(multisrc_data.get("devto") or []),
                "lobsters": len(multisrc_data.get("lobsters") or []),
                "vertical_pubs": len(multisrc_data.get("vertical_pubs") or []),
            },
            # Which sources were actually ASKED. _multisrc_task deliberately skips the dev
            # forums for non-tech ventures (cycle38) and recorded that as _tech — which this
            # allowlist then DROPPED, so a cafe's report showed "devto: 0, lobsters: 0,
            # stackoverflow: 0" for sources never queried, and the thin-signal flag below
            # docked its confidence for having no Stack Overflow presence. "Skipped as
            # irrelevant" and "asked and found nothing" must never be the same value —
            # the third instance of exactly this allowlist bug in this file.
            "queried": {
                "stackoverflow": _is_tech_run,
                "devto": _is_tech_run,
                "lobsters": _is_tech_run,
                "vertical_pubs": True,
            },
        }
        step_done(result, "multi_source_signal")
        checkpoint()

    # Backwards-compat: keep `top_audience` as the first decoded profile
    top_audience = taste_results[0] if taste_results else {}
    if top_audience:
        result["audience"] = top_audience
        result["audiences"] = taste_results  # full set for transparency
        step_done(result, "audience")
    # Iter 40 (#3c): surface the cannot_decode brands so the report can show
    # "we tried but no consumer signal exists for these enterprise B2B brands"
    if cannot_decode_results:
        result["audiences_undecodable"] = cannot_decode_results
        checkpoint()

    return {
        "taste_results": taste_results,
        "cannot_decode": cannot_decode_results,
        "channel_data": channel_data,
        "competitor_pricing": competitor_pricing_data,
        "reddit": reddit_data,
        "hn": hn_data,
        "multisrc": multisrc_data,
        "top_audience": top_audience,
    }
