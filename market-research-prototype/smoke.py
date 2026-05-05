"""
Non-LLM smoke test. Runs google_trends_rising against a real category,
manually extracts brand names from the rising queries (bypassing the
Haiku call), then runs _gather_signals for each and prints the ranking.
This verifies every data source works end-to-end on real data.
"""
import json
import re
import time

from sources import google_trends_rising, probe_domain_patterns
from discover import _gather_signals
from logger import get

log = get("smoke")


STOP_WORDS = {
    "best", "review", "reviews", "healthy", "the", "are", "for", "vs",
    "and", "with", "new", "cheap", "top", "good", "bad", "buy", "online",
}


def brand_guess_from_query(q: str, category_words: set) -> dict | None:
    """
    Cheap brand extraction — keep the first ~2 tokens that aren't category
    or stop words. This is a smoke-test stand-in for the Haiku call.
    """
    words = [w.lower() for w in re.findall(r"[a-z]+", q.lower())]
    brand_words = [
        w for w in words
        if w not in category_words and w not in STOP_WORDS and len(w) > 2
    ]
    if not brand_words:
        return None
    # Take up to 2 brand words so "david protein" resolves better than "david"
    name = " ".join(brand_words[:2]).title()
    return {"name": name, "query_evidence": q}


def run(category: str, max_brands: int = 5):
    log.info(f"\n{'='*60}\nCATEGORY: {category}\n{'='*60}")

    log.info("\n[1] google_trends_rising")
    trends = google_trends_rising(category)
    slope = trends.get("slope_12m")
    log.info(f"  category slope_12m: {slope}")
    rising = trends.get("rising_queries", [])
    log.info(f"  {len(rising)} rising queries")
    for q in rising[:10]:
        log.info(f"    +{q.get('value')}%  {q.get('query')}")

    log.info(f"\n[2] extracting brand candidates (rule-based, no LLM)")
    cat_words = set(category.lower().split()) | {"best", "review", "reviews", "healthy", "the", "are"}
    seen = set()
    candidates = []
    for q in rising:
        g = brand_guess_from_query(q.get("query", ""), cat_words)
        if g and g["name"] not in seen:
            seen.add(g["name"])
            candidates.append(g)
        if len(candidates) >= max_brands:
            break
    log.info(f"  → {[c['name'] for c in candidates]}")

    log.info(f"\n[3] gathering signals for {len(candidates)} brands")
    enriched = []
    for i, b in enumerate(candidates, 1):
        log.info(f"  [{i}/{len(candidates)}] {b['name']}")
        t0 = time.time()
        sigs = _gather_signals(b, category=category, geo="US")
        dur = round(time.time() - t0, 1)
        print(
            f"     score={sigs.get('_score')} "
            f"trend={sigs.get('trend_slope')} "
            f"tp_reviews={sigs.get('trustpilot_reviews')} "
            f"tp_stars={sigs.get('trustpilot_avg_stars')} "
            f"reddit={sigs.get('reddit_mentions')} "
            f"age_days={sigs.get('domain_age_days')} "
            f"({dur}s)"
        )
        enriched.append(sigs)

    log.info(f"\n[4] ranked by composite score")
    ranked = sorted(enriched, key=lambda x: x.get("_score", 0), reverse=True)
    for i, r in enumerate(ranked, 1):
        log.info(f"  {i}. [{r.get('_score'):>5}] {r.get('brand')}  ({r.get('domain')})")

    log.info(f"\n[5] full signal dump")
    print(json.dumps(ranked, indent=2, default=str)[:3000])


# Hardcoded brand candidates for each smoke-test category, simulating what
# the real pipeline gets from Haiku. This isolates the "does the rest of the
# pipeline work on good input?" question from the "does the LLM extract
# decent brand names?" question.
SEEDED_BRANDS = {
    "protein bars": [
        {"name": "David Protein", "likely_domain": "davidprotein.com", "query_evidence": "david protein bars"},
        {"name": "Equip Foods", "likely_domain": "equipfoods.com", "query_evidence": "equip protein bars"},
        {"name": "Junkless Foods", "likely_domain": "junklessfoods.com", "query_evidence": "junkless protein bars"},
        {"name": "Mush", "likely_domain": "mush.com", "query_evidence": "mush protein bars"},
    ],
}


def run_seeded(category: str):
    log.info(f"\n{'='*60}\nSEEDED: {category}\n{'='*60}")
    candidates = SEEDED_BRANDS.get(category, [])
    if not candidates:
        log.info(f"  no seeded brands for {category!r}")
        return
    enriched = []
    for i, b in enumerate(candidates, 1):
        log.info(f"  [{i}/{len(candidates)}] {b['name']} (guess={b['likely_domain']})")
        t0 = time.time()
        sigs = _gather_signals(b, category=category, geo="US")
        dur = round(time.time() - t0, 1)
        print(
            f"     domain={sigs.get('domain')} [{sigs.get('domain_confidence')}] "
            f"score={sigs.get('_score')} "
            f"trend={sigs.get('trend_slope')} "
            f"tp_reviews={sigs.get('trustpilot_reviews')} "
            f"reddit={sigs.get('reddit_mentions')} "
            f"age={sigs.get('domain_age_days')} "
            f"({dur}s)"
        )
        enriched.append(sigs)

    log.info(f"\nranked:")
    for i, r in enumerate(sorted(enriched, key=lambda x: x.get("_score", 0), reverse=True), 1):
        log.info(f"  {i}. [{r.get('_score'):>5}] {r.get('brand')}  ({r.get('domain')}, {r.get('domain_confidence')})")

    log.info(f"\nfull dump:")
    print(json.dumps(enriched, indent=2, default=str)[:4000])


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "rising"
    cat = sys.argv[2] if len(sys.argv) > 2 else "protein bars"
    if mode == "seeded":
        run_seeded(cat)
    else:
        run(mode)  # backward-compat: first arg is category
