"""Run the connected Treadle pipeline through a real Playwright browser.

This starts exactly one report, using the server's real configured model and research
providers. It never retries a report automatically. --resume-run inspects an existing
run without dispatching more model work. Browser artifacts include a trace, screenshots,
the report JSON, a machine-readable summary, JUnit XML and a small HTML test report.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from html import escape
import json
import math
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
TERMINAL = {"completed", "incomplete", "failed", "interrupted"}
REPORT_SECTIONS = (
    "summary", "target_personas", "multi_perspective", "market_size", "revenue",
    "differentiators", "competitive_landscape", "customer_voice",
)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False),
                    encoding="utf-8")


def _clean_message(value: str) -> str:
    """Avoid retaining recognizable credentials in browser diagnostics."""
    value = re.sub(r"\b(?:sk|gsk|AIza)[-_A-Za-z0-9]{16,}\b", "[REDACTED]", value)
    return re.sub(r"(?i)(bearer\s+)[^\s\"']+", r"\1[REDACTED]", value)[:2000]


def _check(checks: list[dict], name: str, passed: bool, detail: object = "") -> None:
    checks.append({"name": name, "passed": bool(passed), "detail": detail,
                   "category": "browser"})


def inspect_report(report: dict, brief: dict, state: dict) -> list[dict]:
    """Check observable pipeline contracts without claiming research truth."""
    checks: list[dict] = []
    result = report.get("result") or {}
    prep = result.get("prep") or {}
    execution = report.get("execution") or {}
    business = prep.get("business_context") or {}
    _check(checks, "pipeline_completed", state.get("status") == "completed"
           and state.get("ok") is True, {
               "status": state.get("status"), "stop_reason": execution.get("stop_reason"),
               "issues": execution.get("issues", []),
           })
    _check(checks, "recursive_prep_v2", prep.get("schema_version") == "market_research.prep/2")
    _check(checks, "customer_input_preserved",
           prep.get("venture", {}).get("customer_inputs") == brief)
    _check(checks, "business_object_present", isinstance(business.get("object"), dict)
           and bool(business["object"]))
    _check(checks, "source_tracing_disabled", prep.get("source_tracing_enabled") is False)
    _check(checks, "eight_research_specifications",
           set(prep.get("research_spec", {})) == set(REPORT_SECTIONS))
    missing = [key for key in REPORT_SECTIONS if not isinstance(result.get(key), dict)]
    _check(checks, "eight_produced_sections", not missing, {"missing": missing})
    rounds = execution.get("correction_rounds")
    _check(checks, "global_correction_cap", type(rounds) is int and 0 <= rounds <= 2,
           {"correction_rounds": rounds})
    _check(checks, "no_stale_consumers", report.get("stale") == [], report.get("stale"))
    limits, usage = execution.get("limits") or {}, execution.get("usage") or {}
    for kind in ("model_calls", "tool_calls"):
        charged = usage.get("charged", {}).get(kind)
        maximum = limits.get("max_" + kind)
        actual = usage.get("actual", {}).get(kind)
        valid = (type(charged) is int and type(maximum) is int and type(actual) is int
                 and 0 <= actual <= charged <= maximum)
        _check(checks, kind + "_within_global_budget", valid,
               {"actual": actual, "charged": charged, "maximum": maximum})
    attempts = execution.get("attempts")
    _check(checks, "isolated_attempt_records", isinstance(attempts, list)
           and bool(attempts) and all(
               isinstance(item, dict) and isinstance(item.get("section"), str)
               and type(item.get("round")) is int and 0 <= item["round"] <= 2
               and type(item.get("pid")) is int and item["pid"] > 0
               and isinstance(item.get("usage_valid"), bool)
               for item in attempts
           ))
    for item in checks:
        item["category"] = "pipeline"
    return checks


def write_test_reports(out: Path, summary: dict) -> None:
    _write_json(out / "summary.json", summary)
    checks = summary["checks"]
    suite = ET.Element("testsuite", name="Treadle browser pipeline",
                       tests=str(len(checks)),
                       failures=str(sum(not c["passed"] for c in checks)),
                       time=str(summary["elapsed_s"]))
    for item in checks:
        case = ET.SubElement(suite, "testcase", name=item["name"],
                             classname="treadle.browser_pipeline")
        if not item["passed"]:
            ET.SubElement(case, "failure", message=item["name"]).text = json.dumps(
                item.get("detail"), ensure_ascii=False)
    ET.ElementTree(suite).write(out / "junit.xml", encoding="utf-8", xml_declaration=True)
    rows = "".join(
        "<tr><td>" + escape(item["name"]) + "</td><td>"
        + ("PASS" if item["passed"] else "FAIL") + "</td><td><pre>"
        + escape(json.dumps(item.get("detail"), ensure_ascii=False, indent=2))
        + "</pre></td></tr>"
        for item in checks
    )
    links = "".join(
        f'<li><a href="{escape(path.name)}">{escape(path.name)}</a></li>'
        for path in sorted(out.iterdir())
        if path.is_file() and path.name != "index.html"
    )
    (out / "index.html").write_text(
        "<!doctype html><html lang='en'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Treadle browser test</title><style>"
        "body{max-width:1100px;margin:32px auto;padding:0 20px;font:16px/1.5 system-ui}"
        "table{width:100%;border-collapse:collapse}td{padding:8px;border:1px solid #ccd}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere}td:first-child{overflow-wrap:anywhere}"
        "</style><h1>Treadle browser test</h1><p>"
        + ("Passed" if summary["passed"] else "Failed")
        + ". Execution and structural checks do not establish research accuracy.</p>"
        + f"<p>Run: {escape(str(summary.get('run_id', 'not started')))}</p>"
        + "<table><thead><tr><th>Check</th><th>Result</th><th>Detail</th></tr></thead><tbody>"
        + rows + "</tbody></table><h2>Artifacts</h2><ul>" + links + "</ul></html>",
        encoding="utf-8",
    )


async def _wait_for_run(context, base: str, run_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last_signature, last_log = None, 0.0
    while True:
        response = await context.request.get(f"{base}/runs/{run_id}")
        if not response.ok:
            raise RuntimeError(f"Run status returned HTTP {response.status}")
        state = await response.json()
        signature = json.dumps({
            key: state.get(key) for key in ("status", "current_step", "progress", "stop_reason")
        }, sort_keys=True)
        now = time.monotonic()
        if signature != last_signature or now - last_log >= 30:
            print(json.dumps({"event": "run_progress", "run_id": run_id,
                              "state": json.loads(signature)}), flush=True)
            last_signature, last_log = signature, now
        if state.get("status") in TERMINAL:
            return state
        if now >= deadline:
            raise TimeoutError("Browser observation deadline reached; resume the saved run ID "
                               "to inspect it without starting another report")
        # This is bounded API condition polling, not a timing assumption about UI readiness.
        await asyncio.sleep(min(2.0, deadline - now))


async def _overflow(page) -> dict:
    return await page.evaluate("""() => ({
        viewport: document.documentElement.clientWidth,
        content: document.documentElement.scrollWidth,
        overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1
    })""")


async def run_browser(args: argparse.Namespace) -> int:
    from playwright.async_api import async_playwright, expect

    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    brief = json.loads(args.brief.read_text(encoding="utf-8"))
    if not isinstance(brief, dict):
        raise ValueError("brief must be a JSON object")
    checks: list[dict] = []
    logs: dict[str, list] = {"console_errors": [], "page_errors": [], "failed_requests": []}
    started = time.monotonic()
    summary = {"run_id": args.resume_run, "resumed": bool(args.resume_run),
               "checks": checks, "artifacts": str(out)}
    browser = context = page = None
    base = args.base_url.rstrip("/")
    try:
        async with async_playwright() as playwright:
            launch = {"headless": not args.headed}
            if args.chrome.exists():
                launch["executable_path"] = str(args.chrome)
            browser = await playwright.chromium.launch(**launch)
            context = await browser.new_context(viewport={"width": 1440, "height": 1000},
                                                device_scale_factor=1)
            context.set_default_timeout(15000)
            await context.tracing.start(screenshots=True, snapshots=True, sources=True)
            page = await context.new_page()
            page.on("console", lambda msg: logs["console_errors"].append(
                _clean_message(msg.text)) if msg.type == "error" else None)
            page.on("pageerror", lambda error: logs["page_errors"].append(
                _clean_message(str(error))))
            page.on("requestfailed", lambda request: logs["failed_requests"].append({
                "path": urlsplit(request.url).path,
                "failure": _clean_message(request.failure or "unknown"),
            }) if urlsplit(request.url).netloc == urlsplit(base).netloc else None)
            try:
                health = await context.request.get(base + "/health")
                if not health.ok:
                    raise RuntimeError(f"Health check returned HTTP {health.status}")
                health_state = await health.json()
                live = (health_state.get("live_providers") is True
                        and health_state.get("pipeline") == "market_research_next")
                _check(checks, "live_connected_pipeline_selected", live, health_state)
                if not live:
                    raise RuntimeError("Server must select market_research_next with live providers")
                await page.goto(base + "/", wait_until="domcontentloaded")
                await expect(page.get_by_label("Business brief (JSON)")).to_be_visible()
                await expect(page.get_by_role("button", name="Start report", exact=True)).to_be_visible()
                _check(checks, "browser_form_loaded", True)
                await page.screenshot(path=str(out / "form-desktop.png"), full_page=True)
                run_id = args.resume_run
                if not run_id:
                    await page.get_by_label("Business brief (JSON)").fill(json.dumps(
                        brief, ensure_ascii=False, indent=2))
                    async with page.expect_response(
                        lambda response: urlsplit(response.url).path == "/runs"
                        and response.request.method == "POST"
                    ) as dispatched:
                        await page.get_by_role("button", name="Start report", exact=True).click()
                    response = await dispatched.value
                    if response.status not in (200, 201, 202):
                        raise RuntimeError(f"Report dispatch returned HTTP {response.status}")
                    created = await response.json()
                    run_id = created.get("run_id")
                    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
                        raise RuntimeError("Server returned an invalid run ID")
                    summary["run_id"] = run_id
                    _write_json(out / "run.json", {"run_id": run_id, "base_url": base})
                    print(json.dumps({"event": "report_started", "run_id": run_id}), flush=True)
                    _check(checks, "browser_dispatched_one_report", True)
                else:
                    # The harness reads this query parameter and resumes status display only.
                    await page.goto(base + "/?run=" + run_id, wait_until="domcontentloaded")
                    _check(checks, "resumed_without_dispatch", True)
                state = await _wait_for_run(context, base, run_id, args.timeout)
                summary["terminal_state"] = state
                _write_json(out / "terminal-state.json", state)
                # Verify the same terminal state is visible, rather than relying only on API data.
                await expect(page.locator("#state")).to_have_attribute("data-status", state["status"])
                _check(checks, "terminal_status_visible", True, state["status"])
                await page.screenshot(path=str(out / "status-desktop.png"), full_page=True)
                if state["status"] in {"failed", "interrupted"}:
                    checks.append({"name": "pipeline_completed", "passed": False,
                                   "category": "pipeline", "detail": state})
                    _check(checks, "terminal_failure_disclosed", True)
                else:
                    json_response = await context.request.get(f"{base}/runs/{run_id}/report.json")
                    if not json_response.ok:
                        raise RuntimeError(f"Report JSON returned HTTP {json_response.status}")
                    report = await json_response.json()
                    _write_json(out / "report.json", report)
                    checks.extend(inspect_report(report, brief, state))
                    await expect(page.get_by_role("link", name="Open report", exact=True)).to_be_visible()
                    await page.get_by_role("link", name="Open report", exact=True).click()
                    await expect(page.get_by_role("heading", name="Venture research report",
                                                  exact=True)).to_be_visible()
                    section_ids = await page.locator("section > h2[id]").evaluate_all(
                        "elements => elements.map(element => element.id)")
                    _check(checks, "fixed_report_sections_rendered",
                           section_ids == list(REPORT_SECTIONS), section_ids)
                    (out / "report.html").write_text(await page.content(), encoding="utf-8")
                    desktop = await _overflow(page)
                    _check(checks, "desktop_report_fits_viewport", not desktop["overflow"], desktop)
                    await page.screenshot(path=str(out / "report-desktop.png"), full_page=True)
                    await page.set_viewport_size({"width": 390, "height": 844})
                    await expect(page.locator("h1")).to_be_visible()
                    mobile = await _overflow(page)
                    _check(checks, "mobile_report_fits_viewport", not mobile["overflow"], mobile)
                    await page.screenshot(path=str(out / "report-mobile.png"), full_page=True)
                    if state.get("status") != "completed":
                        await expect(page.get_by_text(
                            "This report contains unresolved findings or", exact=False)).to_be_visible()
                        _check(checks, "incomplete_report_disclosed", True)
            except Exception as exc:
                _check(checks, "browser_journey_exception", False,
                       {"type": type(exc).__name__, "message": _clean_message(str(exc))})
                if page is not None:
                    try:
                        await page.screenshot(path=str(out / "failure.png"), full_page=True)
                    except Exception:
                        pass
            finally:
                await context.tracing.stop(path=str(out / "trace.zip"))
                await context.close()
                await browser.close()
    except Exception as exc:
        _check(checks, "browser_startup_or_cleanup", False,
               {"type": type(exc).__name__, "message": _clean_message(str(exc))})
    for name, entries in logs.items():
        _check(checks, "no_" + name, not entries, entries)
    _write_json(out / "browser-log.json", logs)
    summary.update(passed=bool(checks) and all(c["passed"] for c in checks),
                   browser_passed=all(c["passed"] for c in checks if c["category"] == "browser"),
                   pipeline_passed=any(c["category"] == "pipeline" for c in checks)
                   and all(c["passed"] for c in checks if c["category"] == "pipeline"),
                   elapsed_s=round(time.monotonic() - started, 2))
    write_test_reports(out, summary)
    print(json.dumps({"event": "browser_test_finished", "run_id": summary["run_id"],
                      "passed": summary["passed"], "artifacts": str(out),
                      "failed_checks": [c["name"] for c in checks if not c["passed"]]}), flush=True)
    return 0 if summary["passed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8779")
    parser.add_argument("--brief", type=Path, default=ROOT / "docs/treadle/example_hybrid_venture.json")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "out/treadle-browser-e2e" /
                        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    parser.add_argument("--timeout", type=float, default=1000)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--chrome", type=Path, default=CHROME)
    parser.add_argument("--resume-run", help="inspect this run without starting another report")
    args = parser.parse_args(argv)
    parsed = urlsplit(args.base_url)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/")):
        parser.error("--base-url must be a plain localhost HTTP origin")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    if args.resume_run and not re.fullmatch(r"[A-Za-z0-9_-]+", args.resume_run):
        parser.error("--resume-run contains an invalid run ID")
    try:
        return asyncio.run(run_browser(args))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
