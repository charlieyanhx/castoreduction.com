"""
benchmarks/run_manus_bench.py — Castor vs. Manus head-to-head harness.

Runs Castor's differentiator path (scale classification → fitting sizing method →
validation gate, plus multi-perspective consumer research) on a fixed set of
queries and prints a structured report + a blank scorecard for each. You then run
the same query in Manus, and score both on the rubric in `manus_comparison.md`.

Usage (needs network + an LLM key — Census/OSM/Gemini are sandboxed in CI):
    python -m benchmarks.run_manus_bench
    python -m benchmarks.run_manus_bench --query Q1
    python -m benchmarks.run_manus_bench --json > castor_side.json

Design notes:
  - Each query declares the inputs its scale needs (address for hyperlocal,
    addresses/representative for regional, profile for digital).
  - Every step degrades gracefully — a sandboxed data source yields a skeleton
    with an explicit error, never a crash, so the harness always emits a report.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any, Callable

try:  # make the LLM key in .env available when run as a script
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


@dataclass
class BenchQuery:
    qid: str
    prompt: str
    scale_hint: str
    run: Callable[[], dict]  # returns Castor's structured side for this query


def _scale(description: str, geo: str) -> dict:
    from skills.sizing.classify import classify_market_scale
    ev = classify_market_scale(description, geo)
    return ev.payload or {"error": ev.error}


def _consumer(description: str, geo: str) -> dict:
    from skills.perspective import consumer_research_skill
    ev = consumer_research_skill(description=description, geo=geo, n_perspectives=4)
    return (ev.payload or {}).get("synthesis") if not ev.skeleton else {"error": ev.error}


def _q1() -> dict:
    """Hyperlocal: a restaurant at a real LA address."""
    from skills.sizing.hyperlocal import size_hyperlocal
    desc = "A farm-to-table restaurant in Silver Lake, Los Angeles."
    addr = "2700 Sunset Blvd, Los Angeles, CA 90026"
    sizing = size_hyperlocal(address=addr, category="food_away_from_home",
                             osm_value="restaurant", supply_seats=60)
    return {
        "scale": _scale(desc, "Los Angeles, CA"),
        "sizing": sizing.payload,
        "sizing_error": sizing.error,
        "consumer_research": _consumer(desc, "Los Angeles, CA"),
    }


def _q2() -> dict:
    """Regional: 8-studio fitness chain in Austin."""
    from skills.sizing.regional import size_regional
    desc = "A regional chain of 8 boutique fitness studios across Austin, TX."
    sizing = size_regional(representative_address="600 Congress Ave, Austin, TX 78701",
                           planned_locations=8, category="fitness", osm_value="gym",
                           phasing=[0.25, 0.6, 1.0])
    return {
        "scale": _scale(desc, "Austin, TX"),
        "sizing": sizing.payload,
        "sizing_error": sizing.error,
        "consumer_research": _consumer(desc, "Austin, TX"),
    }


def _q3() -> dict:
    """National digital: B2B SaaS (uses the gated legacy engine)."""
    from skills.sizing.national_digital import size_national_digital
    desc = "A B2B SaaS for restaurant inventory management, US market."
    sizing = size_national_digital(profile={"summary": desc, "name": "InventoryCo"})
    return {
        "scale": _scale(desc, "US"),
        "sizing": sizing.payload,
        "sizing_error": sizing.error,
        "consumer_research": _consumer(desc, "US"),
    }


def _q4() -> dict:
    """Live-web competitor recency — Manus's strength; Castor's deepened discovery.

    Uses multi_strategy_discovery (GPT-Researcher fan-out: several parallel search
    strategies → dedupe by cross-strategy agreement → direct/indirect classification)
    to close the breadth gap against a general web agent.
    """
    from skills.discovery_multi import multi_strategy_discovery
    desc = "AI customer-support software."
    ev = multi_strategy_discovery(description=desc, max_candidates=15)
    return {"discovery": ev.payload,
            "note": "Manus leads on raw recency; Castor adds direct/indirect classification + provenance."}


QUERIES = [
    BenchQuery("Q1", "Farm-to-table restaurant, Silver Lake LA — size/competitors/WTP/viability", "hyperlocal", _q1),
    BenchQuery("Q2", "Regional chain of 8 boutique fitness studios, Austin TX", "regional", _q2),
    BenchQuery("Q3", "B2B SaaS for restaurant inventory mgmt, US — TAM/SAM/SOM", "national_digital", _q3),
    BenchQuery("Q4", "Top 15 competitors in AI customer-support software + funding", "web-recency", _q4),
]

_RUBRIC = [
    "numeric specificity", "provenance", "method fit", "triangulation", "validation",
    "competitor coverage", "consumer insight", "defensibility",
    "web recency/breadth", "reproducibility",
]


def _scorecard(qid: str) -> str:
    lines = [f"\nSCORECARD — {qid}    Castor: ___/100   Manus: ___/100", "",
             f"{'dimension':<24}{'Castor':<8}{'Manus':<8}notes"]
    for dim in _RUBRIC:
        lines.append(f"{dim:<24}{'_':<8}{'_':<8}")
    return "\n".join(lines)


def _print_human(qid: str, prompt: str, side: dict) -> None:
    print("=" * 78)
    print(f"{qid}: {prompt}")
    print("=" * 78)
    sizing = side.get("sizing") or {}
    if "scale" in side:
        sc = side["scale"]
        print(f"  scale        : {sc.get('scale')} → {sc.get('sizing_skill')}")
    if sizing:
        print(f"  TAM          : {sizing.get('tam_usd')}")
        print(f"  SAM          : {sizing.get('sam_usd')}")
        print(f"  SOM          : {sizing.get('som_usd')}")
        figs = sizing.get("figures") or []
        print(f"  provenance   : {len(figs)} sourced figure(s)")
        for f in figs[:4]:
            print(f"     - {f.get('label')}: {f.get('value_usd')}  [{f.get('source')}]")
        val = sizing.get("validation") or {}
        print(f"  validation   : passed={val.get('passed')} blocks={len(val.get('blocks') or [])}")
    if side.get("sizing_error"):
        print(f"  sizing_error : {side['sizing_error']}")
    cr = side.get("consumer_research") or {}
    if cr and "error" not in cr:
        wtp = cr.get("willingness_to_pay") or {}
        print(f"  consumer     : {cr.get('n_segments')} segments | WTP median={wtp.get('median')} "
              f"| shared needs={cr.get('shared_needs')}")
    if side.get("discovery"):
        disc = side["discovery"] or {}
        comps = disc.get("competitors") or []
        print(f"  competitors  : {len(comps)} found via {disc.get('n_strategies', '?')} strategies "
              f"({disc.get('n_direct', 0)} direct, {disc.get('n_indirect', 0)} indirect)")
        for c in comps[:5]:
            print(f"     - {c.get('name', '?')} [{c.get('relationship', '?')}] "
                  f"via {', '.join(c.get('sources', []))}")
    print(_scorecard(qid))
    print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", help="run only this query id (Q1..Q4)")
    ap.add_argument("--json", action="store_true", help="emit raw JSON instead of a report")
    args = ap.parse_args()

    selected = [q for q in QUERIES if not args.query or q.qid == args.query]
    results: dict[str, Any] = {}
    for q in selected:
        try:
            results[q.qid] = q.run()
        except Exception as e:  # harness never dies on one query
            results[q.qid] = {"error": f"{type(e).__name__}: {e}"}

    if args.json:
        print(json.dumps(results, indent=2, default=str))
        return
    for q in selected:
        _print_human(q.qid, q.prompt, results[q.qid])


if __name__ == "__main__":
    main()
