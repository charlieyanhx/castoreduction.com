#!/usr/bin/env python3
"""
Daily automated check — tests both functionality and analysis quality.

Run manually:  .venv/bin/python daily_check.py
Or via cron/scheduler for hands-off monitoring.

Two phases:
  1. FUNCTIONALITY — runs all 68 unit/integration/API tests
  2. QUALITY — runs a real discover+taste pipeline on a rotating category,
     then scores the output for analysis depth and signal coverage

Outputs a JSON report to out/daily_check_YYYYMMDD.json
Exits with code 0 if all checks pass, 1 if any fail.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure we're running from the right directory
os.chdir(Path(__file__).parent)

# Load env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

OUT_DIR = Path("out")
OUT_DIR.mkdir(exist_ok=True)

# Rotating categories — one per day of week
CATEGORIES = [
    "protein bars",
    "skincare",
    "wireless earbuds",
    "standing desk",
    "meal prep containers",
    "sleep supplements",
    "pet camera",
]


def run_tests() -> dict:
    """Run all 3 test suites, return pass/fail summary."""
    results = {}
    for suite in ["test_infra.py", "test_integration.py", "test_api.py"]:
        t0 = time.time()
        r = subprocess.run(
            [".venv/bin/python", suite],
            capture_output=True, text=True, timeout=60,
        )
        dur = round(time.time() - t0, 2)
        passed = r.returncode == 0
        # Extract test count from output
        count_line = [l for l in r.stderr.split("\n") if "Ran " in l]
        count = count_line[0].strip() if count_line else "?"
        results[suite] = {
            "passed": passed,
            "count": count,
            "duration_s": dur,
            "returncode": r.returncode,
            "stderr_tail": r.stderr[-500:] if not passed else "",
        }
    return results


def run_quality_check(category: str) -> dict:
    """
    Run discover on a real category, then score the output for quality.
    Quality metrics:
      - Did brand extraction produce ≥2 candidates?
      - Did ≥1 candidate get a domain resolved?
      - Did ≥1 candidate have a trend slope?
      - Did ≥1 candidate have trustpilot data?
      - Did ≥1 candidate have IG data?
      - Did synthesis produce a category_read?
      - Did synthesis produce ranked_opportunities with theses?
      - Total runtime under 5 minutes?
    """
    from discover import discover
    from llm import get_usage, reset_usage

    reset_usage()
    t0 = time.time()
    try:
        result = discover(category, geo="US", max_candidates=5)
    except Exception as e:
        return {
            "category": category,
            "error": str(e),
            "quality_score": 0,
            "runtime_s": round(time.time() - t0, 1),
        }
    runtime = round(time.time() - t0, 1)
    usage = get_usage().summary()

    signals = result.get("steps", {}).get("signals", [])
    synth = result.get("synthesis", {})
    opps = synth.get("ranked_opportunities", [])

    checks = {
        "has_rising_queries": bool(result.get("steps", {}).get("trends", {}).get("rising_queries")),
        "brands_extracted_gte_2": len(signals) >= 2,
        "brand_extraction_method": result.get("brand_extraction_method", "unknown"),
        "any_domain_resolved": any(s.get("domain") for s in signals),
        "any_trend_slope": any(s.get("trend_slope") is not None for s in signals),
        "any_trustpilot": any(s.get("trustpilot_reviews") for s in signals),
        "any_instagram": any(s.get("ig_followers") for s in signals),
        "any_wayback": any(s.get("wayback_snapshots_total") for s in signals),
        "synthesis_has_category_read": bool(synth.get("category_read")),
        "synthesis_has_opportunities": len(opps) > 0,
        "opportunities_have_theses": all(o.get("thesis") for o in opps) if opps else False,
        "runtime_under_5min": runtime < 300,
    }

    # Quality score: each check is 1 point, max 12 (excluding brand_extraction_method which is informational)
    score_checks = {k: v for k, v in checks.items() if isinstance(v, bool)}
    quality_score = sum(1 for v in score_checks.values() if v)
    quality_max = len(score_checks)

    return {
        "category": category,
        "quality_score": quality_score,
        "quality_max": quality_max,
        "quality_pct": round(quality_score / quality_max * 100, 1) if quality_max else 0,
        "checks": checks,
        "num_candidates": len(signals),
        "num_opportunities": len(opps),
        "runtime_s": runtime,
        "llm_usage": usage,
        "top_opportunity": opps[0] if opps else None,
        "error": result.get("error"),
    }


def run_taste_check(brand: str, domain: str) -> dict:
    """Quick taste decode quality check."""
    from taste import decode_taste

    t0 = time.time()
    try:
        profile = decode_taste(brand, domain)
    except Exception as e:
        return {"brand": brand, "error": str(e), "quality_score": 0}
    runtime = round(time.time() - t0, 1)

    checks = {
        "has_motivation": bool(profile.get("purchase_motivation")),
        "has_hooks": len(profile.get("hook_angles_that_would_work", [])) >= 2,
        "has_emotional_triggers": bool(profile.get("emotional_triggers")),
        "has_life_context": bool(profile.get("life_context")),
        "confidence_above_0.5": (profile.get("confidence") or 0) > 0.5,
    }
    score = sum(1 for v in checks.values() if v)

    return {
        "brand": brand,
        "domain": domain,
        "quality_score": score,
        "quality_max": len(checks),
        "quality_pct": round(score / len(checks) * 100, 1),
        "checks": checks,
        "runtime_s": runtime,
    }


def main():
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    category = CATEGORIES[int(today[-1]) % len(CATEGORIES)]
    report: dict = {
        "date": today,
        "category": category,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    all_pass = True

    # Phase 1: functionality tests
    print(f"{'='*60}")
    print(f"DAILY CHECK — {today} — category: {category}")
    print(f"{'='*60}")
    print()
    print("Phase 1: Functionality tests")
    test_results = run_tests()
    report["tests"] = test_results
    for suite, r in test_results.items():
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  {status}  {suite}  ({r['count']}, {r['duration_s']}s)")
        if not r["passed"]:
            all_pass = False
            print(f"         {r['stderr_tail'][:200]}")
    print()

    # Phase 2: discover quality
    print("Phase 2: Discover quality check")
    discover_result = run_quality_check(category)
    report["discover"] = discover_result
    print(f"  Category: {category}")
    print(f"  Quality: {discover_result['quality_pct']}% ({discover_result['quality_score']}/{discover_result['quality_max']})")
    print(f"  Candidates: {discover_result['num_candidates']}")
    print(f"  Runtime: {discover_result['runtime_s']}s")
    if discover_result.get("error"):
        print(f"  Error: {discover_result['error'][:200]}")
    if discover_result.get("top_opportunity"):
        top = discover_result["top_opportunity"]
        print(f"  Top: {top.get('brand')} (score {top.get('opportunity_score')})")
        print(f"        {top.get('thesis', '')[:150]}")
    for k, v in discover_result.get("checks", {}).items():
        if isinstance(v, bool) and not v:
            all_pass = False
            print(f"  ✗ FAILED: {k}")
    print()

    # Phase 3: taste quality (use top discover result if available)
    top_opp = discover_result.get("top_opportunity")
    if top_opp and top_opp.get("domain"):
        print("Phase 3: Taste decode quality check")
        taste_result = run_taste_check(top_opp["brand"], top_opp["domain"])
        report["taste"] = taste_result
        print(f"  Brand: {taste_result.get('brand')}")
        print(f"  Quality: {taste_result['quality_pct']}% ({taste_result['quality_score']}/{taste_result['quality_max']})")
        print(f"  Runtime: {taste_result['runtime_s']}s")
        for k, v in taste_result.get("checks", {}).items():
            if not v:
                print(f"  ✗ FAILED: {k}")
    else:
        print("Phase 3: Taste decode SKIPPED (no top opportunity with domain)")
        report["taste"] = {"skipped": True}

    # Save report
    report_path = OUT_DIR / f"daily_check_{today}.json"
    report_path.write_text(json.dumps(report, indent=2, default=str))
    print()
    print(f"Report saved → {report_path}")
    print(f"Overall: {'ALL PASS' if all_pass else 'ISSUES FOUND'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
