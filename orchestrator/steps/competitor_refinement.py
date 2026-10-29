"""orchestrator/steps/competitor_refinement.py — Round-2+ competitor search seeded by
differentiator gaps.

WHY THIS EXISTS
---------------
The initial discover() step finds competitors through web search + trends + LLM recall.
It doesn't know what the venture's specific market gaps are — it can't, because
differentiators.extract_differentiators() runs *after* discovery. This step closes the
loop: it reads the gaps that differentiators identified, runs targeted searches for
"who is already solving that gap?", and unions any new rivals into the roster.

READS:  result["differentiators"]["gaps"]              — [{need, why_unmet}, ...]
        result["discover"]["synthesis"]["ranked_opportunities"] — existing roster

WRITES: result["discover"]["synthesis"]["ranked_opportunities"] — enriched in-place
        result["discover"]["_refinement_rounds"]       — how many rounds ran
        result["_refinement_added_competitors"]        — True if anything was added
            (plan.py reads this to decide whether to re-run differentiators)

STOPPING CONDITION
------------------
If a round surfaces fewer than MIN_NEW_TO_CONTINUE real competitors, we stop.
MAX_ROUNDS is a hard ceiling regardless of novelty.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from logger import get

from . import step_done, step_scope

log = get("plan.steps.competitor_refinement")

MIN_NEW_TO_CONTINUE = 3   # fewer new competitors than this in a round → stop
MAX_ROUNDS = 2            # hard ceiling to prevent runaway cost


def _gap_queries(gap_need: str, category: str, geo: str = "",
                 channel: str = "") -> list[str]:
    """Targeted query angles for one gap.

    Geo and channel are injected so physical local ventures get local results instead
    of globally dominant online platforms. For a Venice Beach boutique, "luxury vintage
    clothing Venice Beach" surfaces Wasteland and local consignment shops; without geo
    the same search returns Vestiaire Collective and 1stDibs.

    channel: "physical" | "online" | "hybrid" | "" (unknown)
    """
    queries = [
        f"{category} {gap_need}",
        f"alternatives that offer {gap_need} {category}",
    ]
    if geo and geo.lower() not in ("us", "global", "unknown", ""):
        # Physical/hybrid: add geo-specific angles that surface local rivals
        queries.append(f"{category} {geo}")
        queries.append(f"{gap_need} {category} near {geo}")
    else:
        # No specific geo or online-only: review-site angles surface broader set
        queries.append(f"best solution for {gap_need} {category}")
        queries.append(f"site:g2.com OR site:capterra.com {category} {gap_need}")

    if channel == "physical":
        # Explicitly exclude online-only platforms from physical search angles
        queries.append(f"in-person {category} {geo}".strip())

    return queries


def _search_for_gap(gap_need: str, category: str, known: set[str],
                    geo: str = "", channel: str = "") -> list[dict]:
    """Run targeted web searches for one gap and extract real competitor names.
    Returns candidates not already in known. Best-effort — returns [] on any failure."""
    try:
        from tools.scrape import web_search, filter_aggregator_domains
        from llm import call_json

        hits: list[dict] = []
        for q in _gap_queries(gap_need, category, geo=geo, channel=channel):
            ev = web_search(q, max_results=6)
            rows = (filter_aggregator_domains(ev.payload or []).payload) or []
            hits.extend(r for r in rows if isinstance(r, dict))

        if not hits:
            return []

        lines = [
            f"- {h.get('title', '')} | {(h.get('snippet') or '')[:140]}"
            for h in hits[:30]
        ]

        # Channel filter instruction: for physical ventures, online-only platforms
        # compete for the same customer but are not direct local rivals. Flag them
        # as adjacent so they don't crowd out local competitors in the roster.
        channel_instruction = ""
        if channel == "physical":
            channel_instruction = (
                "\nCHANNEL NOTE: This is a physical in-person venture. "
                "Online-only platforms (e.g. global marketplaces, e-commerce sites) "
                "compete for the same buyer but are NOT local rivals — mark these "
                "with channel='online' in your output so they can be classified "
                "as adjacent rather than direct. Prioritize local/regional businesses."
            )

        raw = call_json(
            system=(
                "You extract real competitor COMPANY/PRODUCT names from web-search results. "
                "Return only companies that genuinely compete in the stated category and "
                "specifically address the stated gap. Exclude review publishers (G2, Capterra, "
                "Forbes, PCMag), generic phrases, and companies already in the known list. "
                "Return ONLY JSON: {\"companies\": [{\"name\": str, \"domain\": str|null, "
                "\"channel\": \"physical\"|\"online\"|\"unknown\"}]}."
                + channel_instruction
            ),
            user=(
                f"CATEGORY: {category}\n"
                f"GAP: {gap_need}\n"
                f"GEO: {geo or 'unspecified'}\n"
                f"ALREADY KNOWN: {', '.join(sorted(known)[:20])}\n\n"
                f"SEARCH RESULTS:\n" + "\n".join(lines)
            ),
            max_tokens=500,
        ) or {}
    except Exception as e:
        log.warning("[refinement] gap search failed for %r (non-fatal): %s", gap_need, e)
        return []

    out = []
    for c in raw.get("companies") or []:
        if not isinstance(c, dict):
            continue
        name = (c.get("name") or "").strip()
        if name and name.lower() not in known:
            out.append({
                "name": name,
                "domain": c.get("domain"),
                "_gap_seed": gap_need,
                "_channel": c.get("channel", "unknown"),
            })
    return out


def _enrich_candidate(brand: dict, category: str, geo: str) -> dict:
    """Gather signals for one gap-sourced candidate using the same pipeline as discover().
    Import is lazy to avoid a circular import (discover imports from orchestrator indirectly
    through plan.py, but not through this step)."""
    try:
        from discover import _gather_signals
        sig = _gather_signals(brand, category=category, geo=geo)
        # Carry forward the gap that surfaced this competitor
        sig["_gap_seed"] = brand.get("_gap_seed")
        return sig
    except Exception as e:
        return {
            "brand": brand.get("name"),
            "error": str(e),
            "_gap_seed": brand.get("_gap_seed"),
        }


def run_competitor_refinement_step(
    result: dict,
    profile: dict,
    opps: list,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> None:
    """Gap-seeded round-2+ competitor search.

    Reads differentiator gaps, runs targeted searches per gap, gathers signals for any
    new competitors found, and unions them into the existing ranked roster. Re-ranks
    by opportunity_score afterwards so the display order stays consistent.

    Non-fatal: any failure in search or enrichment leaves the existing roster untouched.
    """
    with step_scope("competitor_refinement"):
        gaps = (result.get("differentiators") or {}).get("gaps") or []
        if not gaps:
            log.info("[refinement] no differentiator gaps to seed from — skipping")
            return

        category = profile.get("category", "")
        geo = profile.get("geography", profile.get("geo", "US"))
        channel = profile.get("channel", "")  # "physical" | "online" | "hybrid" | ""

        # Build known-name AND known-domain sets from the full roster (including
        # reference cases) so round-2 can't re-add a competitor that was already
        # found and partitioned out (e.g. 1stDibs appearing in both lists).
        disc_syn = (result.get("discover") or {}).get("synthesis") or {}
        full_roster = (
            (disc_syn.get("ranked_opportunities") or [])
            + (disc_syn.get("reference_cases") or [])
        )
        known: set[str] = {
            (o.get("brand") or o.get("name") or "").strip().lower()
            for o in full_roster
            if (o.get("brand") or o.get("name"))
        }
        known_domains: set[str] = {
            (o.get("domain") or "").strip().lower()
            for o in full_roster
            if o.get("domain")
        }

        all_added: list[dict] = []

        for round_num in range(1, MAX_ROUNDS + 1):
            log.info("[refinement] round %d / %d — seeding from %d gap(s)",
                     round_num, MAX_ROUNDS, min(len(gaps), 3))

            round_candidates: list[dict] = []

            # Run gap searches in parallel — each gap is independent
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = {
                    pool.submit(
                        _search_for_gap,
                        gap.get("need", ""), category, set(known),
                        geo=geo, channel=channel,
                    ): gap
                    for gap in gaps[:3]   # top 3 gaps per round
                    if gap.get("need")
                }
                for fut in as_completed(futures):
                    try:
                        new = fut.result(timeout=60)
                        for c in new:
                            name_lower = c["name"].lower()
                            if name_lower not in known:
                                round_candidates.append(c)
                                known.add(name_lower)
                    except Exception as e:
                        log.warning("[refinement] gap search future failed: %s", e)

            # Novelty judge — is this round worth continuing?
            if len(round_candidates) < MIN_NEW_TO_CONTINUE:
                log.info(
                    "[refinement] round %d: %d new competitor(s) found, "
                    "below threshold (%d) — stopping",
                    round_num, len(round_candidates), MIN_NEW_TO_CONTINUE,
                )
                break

            log.info("[refinement] round %d: %d new competitor(s) — gathering signals",
                     round_num, len(round_candidates))

            # Enrich new candidates (signals) in parallel
            enriched_round: list[dict] = [None] * len(round_candidates)  # type: ignore
            with ThreadPoolExecutor(max_workers=4) as pool:
                future_to_idx = {
                    pool.submit(_enrich_candidate, c, category, geo): i
                    for i, c in enumerate(round_candidates)
                }
                for fut in as_completed(future_to_idx):
                    i = future_to_idx[fut]
                    try:
                        enriched_round[i] = fut.result(timeout=120)
                    except Exception as e:
                        enriched_round[i] = {
                            "brand": round_candidates[i].get("name"),
                            "error": f"timeout: {e}",
                        }

            enriched_round = [e or {} for e in enriched_round]
            all_added.extend(enriched_round)

            log.info("[refinement] round %d complete — %d total new competitor(s) accumulated",
                     round_num, len(all_added))

            # On a multi-round run, refresh gaps from updated differentiators if available.
            # For now the same gap list is reused; a future enhancement can re-run
            # differentiators mid-loop when the roster grows substantially.

        result["discover"]["_refinement_rounds"] = round_num

        if not all_added:
            log.info("[refinement] no new competitors found across all rounds")
            return

        # --- Union into the existing ranked roster ---
        disc = result.get("discover") or {}
        syn = disc.get("synthesis") or {}
        existing_roster = syn.get("ranked_opportunities") or []

        # Convert enriched signal dicts into the ranked_opportunity shape the rest of
        # the pipeline expects.  We do NOT call the synthesis LLM again — that would be
        # expensive and slow.  Instead we build minimal records that carry the Python-
        # computed score, signals, and provenance, and sort them into the existing list.
        from discover import _signal_score  # already computed in _enrich_candidate
        # C1 (9201627d audit): a record with NO gathered signal is a keyword mention,
        # not a measured competitor. 13 of this run's 35 printed 0.0 with every signal
        # null, and "35 entrenched competitors" then drove the viability score, the
        # differentiation verdict and every 4Ps section. Those records are disclosed
        # separately (unverified_mentions) instead of ranked.
        _SIGNAL_KEYS = ("trend_slope", "trustpilot_reviews", "trustpilot_avg_stars",
                        "ig_followers", "wayback_avg_per_month", "domain_age_days",
                        "reddit_mentions", "hn_mentions")

        def _has_signal(rec: dict) -> bool:
            return any(rec.get(k) is not None for k in _SIGNAL_KEYS)

        # C1: the founder's OWN declared stack is not a rival. This run listed Mem0 —
        # the framework named in intake.differentiation ("built on frameworks like
        # mem0") — as a direct competitor.
        _own_stack = set()
        _facts = ((result.get("intake") or {}).get("facts") or {})
        for _f in ("differentiation", "key_features", "product"):
            for _w in str(_facts.get(_f) or "").replace(",", " ").split():
                _w = _w.strip(".;:()").lower()
                if len(_w) >= 3 and _w not in ("the", "and", "like", "for", "with",
                                               "versus", "built", "frameworks",
                                               "framework", "generic", "standard"):
                    _own_stack.add(_w)

        new_entries, unverified = [], []
        for e in all_added:
            if not e.get("brand"):
                continue
            score = e.get("_score") or _signal_score(e)
            _b = str(e.get("brand") or "").lower().strip()
            if _b and _b in _own_stack:
                log.info("[refine] %r is the founder's own stack — not a competitor",
                         e.get("brand"))
                unverified.append({"brand": e.get("brand"), "domain": e.get("domain"),
                                   "reason": "named in the founder's own stack",
                                   "_gap_seed": e.get("_gap_seed")})
                continue
            if not _has_signal(e):
                unverified.append({"brand": e.get("brand"), "domain": e.get("domain"),
                                   "reason": "surfaced by keyword search; no public "
                                             "signal could be gathered",
                                   "_gap_seed": e.get("_gap_seed")})
                continue
            # C4 (9201627d audit): a gap-search domain is a SEARCH RESULT, not a
            # verified identity — Credal AI printed toolmage.com and MaxKB printed
            # wz-it.com as their own sites, and the customer-voice verdict then
            # described the wrong company. Mark it unverified; the report prints the
            # brand without asserting the domain.
            # C2: dedup by domain — catches entries already in reference_cases
            # (e.g. 1stDibs partitioned from round-1, re-found in round-2)
            _entry_domain = (e.get("domain") or "").strip().lower()
            if _entry_domain and _entry_domain in known_domains:
                log.info("[refinement] skipping %s — domain already in roster",
                         e.get("brand"))
                continue
            if _entry_domain:
                known_domains.add(_entry_domain)

            # C1: respect off_category from validate_domain — was hardcoded "direct"
            # which let Foundry.com (a tech company) rank as a direct vintage competitor.
            # C5: online-only platform competing with a physical venture → adjacent.
            is_off = bool(e.get("off_category"))
            is_channel_mismatch = (channel == "physical" and e.get("_channel") == "online")
            if is_off:
                _relevance, _is_competitor = "reference", False
            elif is_channel_mismatch:
                _relevance, _is_competitor = "adjacent", True
            else:
                _relevance, _is_competitor = "direct", True

            _thesis = f"Surfaced in round-2 gap search for: {e.get('_gap_seed', 'unknown gap')}."
            if is_channel_mismatch:
                _thesis += " Online platform — adjacent competitor (same buyer, different channel)."
            if is_off:
                _thesis += " Off-category domain — retained as reference only."

            new_entries.append({
                "brand": e.get("brand"),
                "domain": e.get("domain"),
                "domain_verified": bool(e.get("firmographics")
                                        and (e["firmographics"].get("sources") or [])),
                "opportunity_score": score,
                "relevance": _relevance,
                "is_competitor": _is_competitor,
                "thesis": _thesis,
                "suggested_next_step": "decode_taste" if score > 20 else "investigate_further",
                "signals": {
                    k: e.get(k) for k in (
                        "trend_slope", "trustpilot_reviews", "trustpilot_avg_stars",
                        "ig_followers", "wayback_avg_per_month", "domain_age_days",
                    )
                    if e.get(k) is not None
                },
                "_gap_seed": e.get("_gap_seed"),
                "_channel": e.get("_channel"),
                "_refinement_sourced": True,
            })

        existing_roster.extend(new_entries)

        # C1: every displayed score needs its Python counterpart STORED, or D46 reads
        # it as a number nothing computed (measured: 23 of 35 records this run). The
        # enrichment already ran — it just was not persisted into the signal pool.
        _steps = disc.setdefault("steps", {})
        _pool = _steps.setdefault("signals", [])
        _seen = {(s_.get("brand"), s_.get("domain")) for s_ in _pool}
        for e in all_added:
            if e.get("brand") and (e.get("brand"), e.get("domain")) not in _seen:
                rec = dict(e)
                rec.setdefault("_score", e.get("_score") or _signal_score(e))
                _pool.append(rec)
        if unverified:
            syn["unverified_mentions"] = unverified
            log.info("[refine] %d keyword mention(s) held back from the roster",
                     len(unverified))

        # Re-rank: sort by score descending; scoreless entries go last.
        existing_roster.sort(
            key=lambda o: (o.get("opportunity_score") is None,
                           -(o.get("opportunity_score") or 0.0))
        )
        for i, op in enumerate(existing_roster, 1):
            if "rank" in op:
                op["rank"] = i

        syn["ranked_opportunities"] = existing_roster
        disc["synthesis"] = syn
        result["discover"] = disc

        # Update canonical density to reflect the enriched set
        from discover import _set_canonical_density
        _set_canonical_density(result["discover"])

        # Signal to plan.py that differentiators should be re-run on the enriched set
        result["_refinement_added_competitors"] = True

        step_done(result, "competitor_refinement")
        log.info("[refinement] complete — added %d new competitor(s) to roster", len(new_entries))

        if checkpoint:
            checkpoint()
