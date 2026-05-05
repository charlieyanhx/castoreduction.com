"""Live probe — hit each free data source and report what works."""
import json, time, traceback
from sources import (
    google_trends_rising, trustpilot_reviews,
    reddit_mentions, estimate_domain_age_days,
)
from logger import get

log = get("probe")

TESTS = []
def test(name):
    def deco(fn):
        TESTS.append((name, fn)); return fn
    return deco

@test("google_trends")
def t1():
    return google_trends_rising("protein bars", geo="US")

@test("brand_trend_slope")
def t2():
    from sources import brand_trend_slope
    return brand_trend_slope("gymshark")

@test("trustpilot")
def t3():
    return {"count": len(trustpilot_reviews("gymshark.com", max_pages=1))}

@test("reddit")
def t4():
    return {"count": len(reddit_mentions('"gymshark"', limit=10))}

@test("rdap_domain_age")
def t5():
    return {"age_days": estimate_domain_age_days("gymshark.com")}

if __name__ == "__main__":
    results = {}
    for name, fn in TESTS:
        log.info(f"\n=== {name} ===")
        t0 = time.time()
        try:
            r = fn()
            dur = round(time.time() - t0, 2)
            snippet = json.dumps(r, default=str)[:500]
            log.info(f"OK ({dur}s): {snippet}")
            results[name] = {"ok": True, "dur_s": dur, "snippet": snippet}
        except Exception as e:
            dur = round(time.time() - t0, 2)
            log.info(f"FAIL ({dur}s): {e}")
            traceback.print_exc(limit=2)
            results[name] = {"ok": False, "dur_s": dur, "err": str(e)}
        time.sleep(1)
    log.info("\n\n=== SUMMARY ===")
    for n, r in results.items():
        log.info(f"  {'✓' if r['ok'] else '✗'} {n}  ({r['dur_s']}s)")
