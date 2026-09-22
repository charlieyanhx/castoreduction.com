"""Five real reports end to end, in a browser, timed.

    .venv/bin/python scripts/timing_runs.py [base_url] [--runs 5]

Signs in as the testing account, makes sure it holds a report credit per run (an
operator grant on this machine's database, so Stripe's page, which refuses an automated
browser, is not in the loop), and for each of N comparable briefs: fills the survey,
lets the report run for real (the research, the Opus analyst report, the gates), opens
it, asks the analyst two questions and an explanation, and on the first run leaves a note
and rewrites. Every phase is timed from the browser's side, and every run's job record,
events and workshop are saved under out/timing/<stamp>/ with a summary.json that carries
the averages. The point is the diagnostic: where the minutes go, what each run costs,
what the gates said, what broke.

Real money: about a dollar a run in Opus plus whatever the metered tools bill.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE = (_args[0] if _args else "http://127.0.0.1:8765").rstrip("/")
RUNS = int(sys.argv[sys.argv.index("--runs") + 1]) if "--runs" in sys.argv else 5
_ATTACH_ARG = sys.argv[sys.argv.index("--attach") + 1] if "--attach" in sys.argv else None
sys.argv = [sys.argv[0], BASE] + (["--attach", _ATTACH_ARG] if _ATTACH_ARG else [])   # live_smoke reads argv at import
sys.path.insert(0, str(ROOT / "scripts"))
import live_smoke as smoke                          # noqa: E402  (its helpers, its account)

STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
OUT = ROOT / "out" / "timing" / STAMP
OUT.mkdir(parents=True)

BRIEFS = [
    ("mission-sf", "An independent specialty coffee shop with a small roastery, opening on a corner "
     "site in the Mission District of San Francisco, about 1,200 square feet with 28 seats, "
     "pour-over and espresso around $5.50 a drink, and wholesale roasted beans to local cafes."),
    ("sellwood-pdx", "A specialty coffee shop with a small roastery on Milwaukie Avenue in Sellwood, "
     "Portland, Oregon, about 1,100 square feet with 24 seats, espresso and pour-over around $5 "
     "a drink, and wholesale roasted beans to nearby cafes."),
    ("east-6th-austin", "A specialty coffee shop and micro-roastery on East 6th Street in Austin, "
     "Texas, about 1,400 square feet with 32 seats, drinks around $5.25, plus wholesale beans "
     "to local restaurants."),
    ("logan-square-chi", "A neighbourhood specialty coffee shop with a small roaster in Logan Square, "
     "Chicago, about 1,000 square feet with 22 seats, espresso and filter coffee around $5 a "
     "drink, and wholesale beans to a handful of cafes."),
    ("ballard-sea", "A specialty coffee shop with an in-house roastery on Ballard Avenue in Seattle, "
     "about 1,300 square feet with 30 seats, drinks around $5.50, with wholesale roasted beans "
     "to local grocers and cafes."),
]
QUESTIONS = ["What should I validate first, and how, before signing the lease?",
             "Which number in this report is the least certain, and what would move it?"]


def ensure_credits(page, n: int) -> int:
    """The account holds a report credit per run: an operator grant on this machine's
    database, logged as such, so the run starts without Stripe's page in the loop."""
    import billing
    me = smoke.api(page, "GET", "/auth/me")["body"] or {}
    owner = me.get("owner")
    have = billing.balance(owner, "report")
    if have < n:
        billing._record(owner, "report", n - have, None, None)
        smoke.log("report credits granted for the timing runs", added=n - have, owner=(owner or "")[:8])
    return billing.balance(owner, "report")


def step_durations(events: dict) -> dict:
    """Per-step seconds from the run's own event feed: the step events carry duration_s,
    and the tool and llm events under them say what the step spent it on."""
    out: dict = {}
    for e in (events or {}).get("events") or []:
        if e.get("layer") == "step" and e.get("duration_s") is not None:
            out[e.get("name") or e.get("step")] = round(float(e["duration_s"]), 1)
    return out


def counts(events: dict) -> dict:
    ev = (events or {}).get("events") or []
    tools = [e for e in ev if e.get("layer") == "tool"]
    llm = [e for e in ev if e.get("layer") == "llm"]
    return {"tool_calls": len(tools), "tool_failures": sum(1 for e in tools if e.get("ok") is False),
            "tool_seconds": round(sum(float(e.get("duration_s") or 0) for e in tools), 1),
            "llm_calls": len(llm), "llm_cached": sum(1 for e in llm if e.get("cached")),
            "llm_seconds": round(sum(float(e.get("duration_s") or 0) for e in llm), 1)}


ATTACH = sys.argv[sys.argv.index("--attach") + 1] if "--attach" in sys.argv else None


def one_run(page, i: int, slug: str, brief: str, full_workshop: bool) -> dict:
    smoke.BRIEF = brief
    rec: dict = {"run": i + 1, "slug": slug, "brief": brief}
    t0 = time.time()
    if i == 0 and ATTACH:
        # a run already going (a harness that broke after the launch): follow it
        rec["survey_seconds"] = None
        t1 = time.time()
        job = smoke.follow_run(page, ATTACH)
    else:
        gate = smoke.survey(page)
        rec["survey_seconds"] = round(time.time() - t0, 1)
        if gate:
            raise SystemExit("the gate appeared: the account holds no report credit")
        t1 = time.time()
        job = smoke.follow_run(page)
    rec["job"] = job
    rec["run_wall_seconds"] = round(time.time() - t1, 1)
    j = smoke.api(page, "GET", f"/jobs/{job}")["body"] or {}
    ev = smoke.api(page, "GET", f"/jobs/{job}/events?since=0")["body"] or {}
    r = j.get("result") or {}
    syn = r.get("synthesis") or {}
    rec.update({
        "state": j.get("state"), "error": (j.get("error") or "")[:200],
        "pipeline_seconds": r.get("_duration_seconds"),
        "steps": len(r.get("_steps_completed") or []),
        "dropped": [d if isinstance(d, str) else (d.get("key") or d.get("section")) for d in (r.get("_dropped_outputs") or [])][:12],
        "cogs": r.get("_cogs"),
        "synthesis": {k: syn.get(k) for k in ("model", "style", "usd", "seconds", "in_tok", "out_tok", "stop_reason", "withheld_reason")},
        "synthesis_chars": len(syn.get("markdown") or syn.get("withheld_markdown") or ""),
        "verification": {"summary": (r.get("verification") or {}).get("summary"),
                         "findings": [(f.get("id"), f.get("severity"), (f.get("detail") or "")[:120])
                                      for f in ((r.get("verification") or {}).get("findings") or [])][:20]},
        "step_seconds": step_durations(ev), "events": counts(ev),
    })
    (OUT / f"run{i+1}-{slug}-job.json").write_text(json.dumps(j, indent=1))
    (OUT / f"run{i+1}-{slug}-events.json").write_text(json.dumps(ev, indent=1))
    if not page.locator("a.open").count():
        rec["workshop"] = "not reached: the run did not finish"
        return rec
    # the workshop, for real
    page.locator("a.open").click()
    page.wait_for_selector("#ws", timeout=60_000)
    page.wait_for_function('/^\\d+$/.test(document.getElementById("wsBalN").textContent)')
    turns = []
    for q in QUESTIONS:
        tq = time.time()
        page.fill("#wsInput", q); page.press("#wsInput", "Enter")
        page.wait_for_selector("#wsPending", state="detached", timeout=240_000)
        last = page.locator("#wsTurns .ws-analyst").last
        turns.append({"q": q, "seconds": round(time.time() - tq, 1),
                      "refused": "refused" in (last.get_attribute("class") or ""),
                      "cites": len(last.locator("a.cite").all()), "answer": last.inner_text()[:500]})
        smoke.log("turn", run=i + 1, seconds=turns[-1]["seconds"], refused=turns[-1]["refused"], cites=turns[-1]["cites"])
    p = page.locator("#synthesis p").nth(2)
    p.scroll_into_view_if_needed(); box = p.bounding_box()
    page.mouse.move(box["x"] + 1, box["y"] + 10); page.mouse.down()
    page.mouse.move(box["x"] + min(box["width"] - 2, 260), box["y"] + 10, steps=6); page.mouse.up()
    page.wait_for_selector("#wsSel:not([hidden])")
    page.locator('#wsSel button[data-verb="explain"]').click()
    tq = time.time()
    page.fill("#wsInput", "Explain this passage in plain words, and say how sure the evidence is.")
    page.press("#wsInput", "Enter")
    page.wait_for_selector("#wsPending", state="detached", timeout=240_000)
    last = page.locator("#wsTurns .ws-analyst").last
    turns.append({"q": "explain", "seconds": round(time.time() - tq, 1),
                  "refused": "refused" in (last.get_attribute("class") or ""),
                  "cites": len(last.locator("a.cite").all()), "answer": last.inner_text()[:500]})
    rec["turns"] = turns
    rec["balance_after_turns"] = int(page.locator("#wsBalN").inner_text())
    if full_workshop:
        p = page.locator("#synthesis p").nth(4)
        p.scroll_into_view_if_needed(); box = p.bounding_box()
        page.mouse.move(box["x"] + 1, box["y"] + 10); page.mouse.down()
        page.mouse.move(box["x"] + min(box["width"] - 2, 240), box["y"] + 10, steps=6); page.mouse.up()
        page.wait_for_selector("#wsSel:not([hidden])")
        page.locator('#wsSel button[data-verb="note"]').click()
        page.fill("#wsInput", "Lead with the rent risk: the lease is not signed and the broker's number is a quote.")
        page.press("#wsInput", "Enter")
        page.wait_for_selector("#wsNotes .ws-note", timeout=30_000)
        tr = time.time()
        page.locator("#wsRewrite").click()
        try:
            page.wait_for_function('document.getElementById("wsHistory") && !document.getElementById("wsHistory").hidden', timeout=10 * 60_000)
            page.wait_for_function('/^\\d+$/.test(document.getElementById("wsBalN").textContent)')
            rec["rewrite"] = {"seconds": round(time.time() - tr), "message": page.locator("#wsErr").inner_text()[:200],
                              "balance": int(page.locator("#wsBalN").inner_text())}
        except Exception as e:                                   # noqa: BLE001
            rec["rewrite"] = {"seconds": round(time.time() - tr), "error": str(e)[:160],
                              "message": page.locator("#wsErr").inner_text()[:200]}
        smoke.log("rewrite", run=i + 1, **{k: v for k, v in rec["rewrite"].items() if k != "message"})
        it = smoke.api(page, "GET", f"/jobs/{job}/iteration")["body"] or {}
        rec["rewrite_receipt"] = (it.get("rewrites") or [{}])[-1]
    rec["report_url"] = f"{BASE}/jobs/{job}/report.html"
    rec["total_wall_seconds"] = round(time.time() - t0, 1)
    return rec


def summarize(runs: list[dict]) -> dict:
    done = [r for r in runs if r.get("state") == "complete" and r.get("pipeline_seconds")]
    def avg(key, sub=None):
        vals = []
        for r in done:
            v = r.get(key) if sub is None else (r.get(key) or {}).get(sub)
            if isinstance(v, (int, float)):
                vals.append(float(v))
        return round(statistics.mean(vals), 1) if vals else None
    turn_secs = [t["seconds"] for r in done for t in (r.get("turns") or [])]
    return {
        "runs": len(runs), "completed": len(done),
        "avg_survey_seconds": avg("survey_seconds"),
        "avg_run_wall_seconds": avg("run_wall_seconds"),
        "avg_pipeline_seconds": avg("pipeline_seconds"),
        "avg_synthesis_seconds": avg("synthesis", "seconds"),
        "avg_cost_usd": avg("cogs", "usd"),
        "avg_synthesis_usd": avg("synthesis", "usd"),
        "avg_turn_seconds": round(statistics.mean(turn_secs), 1) if turn_secs else None,
        "avg_total_wall_seconds": avg("total_wall_seconds"),
        "refused_turns": sum(1 for r in done for t in (r.get("turns") or []) if t.get("refused")),
        "withheld_writings": sum(1 for r in done if (r.get("synthesis") or {}).get("withheld_reason")),
        "min_pipeline_seconds": min((r["pipeline_seconds"] for r in done), default=None),
        "max_pipeline_seconds": max((r["pipeline_seconds"] for r in done), default=None),
    }


def main():
    from playwright.sync_api import sync_playwright
    acct = smoke.account()
    runs: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.set_default_timeout(60_000)
        page.goto(BASE + "/healthz")
        me = smoke.sign_in(page, acct)
        smoke.mark_verified_if_needed(page, me)
        credits = ensure_credits(page, RUNS)
        smoke.log("timing runs start", base=BASE, runs=RUNS, report_credits=credits, out=str(OUT))
        for i, (slug, brief) in enumerate(BRIEFS[:RUNS]):
            smoke.log("run begins", run=i + 1, slug=slug)
            try:
                rec = one_run(page, i, slug, brief, full_workshop=(i == 0))
            except Exception as e:                               # noqa: BLE001
                rec = {"run": i + 1, "slug": slug, "error": f"{type(e).__name__}: {str(e)[:300]}"}
                smoke.log("run broke", run=i + 1, error=rec["error"][:160])
                try:
                    page.screenshot(path=str(OUT / f"run{i+1}-{slug}-broke.png"))
                except Exception:                                # noqa: BLE001
                    pass
            runs.append(rec)
            (OUT / "runs.json").write_text(json.dumps(runs, indent=1))
            smoke.log("run done", run=i + 1, state=rec.get("state"), pipeline_s=rec.get("pipeline_seconds"),
                      usd=(rec.get("cogs") or {}).get("usd"), steps=rec.get("steps"))
        summary = summarize(runs)
        (OUT / "summary.json").write_text(json.dumps({"summary": summary, "runs": runs}, indent=1))
        smoke.log("timing runs done", **{k: v for k, v in summary.items() if v is not None})
        browser.close()


if __name__ == "__main__":
    main()
