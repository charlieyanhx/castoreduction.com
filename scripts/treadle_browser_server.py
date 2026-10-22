"""Local browser surface for the real selectable Treadle pipeline.

    .venv/bin/python scripts/treadle_browser_server.py --prep-model MODEL

The server binds to 127.0.0.1 only. Starting a report uses configured live providers.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import asdict
import html
import json
from pathlib import Path
import re
import sys
from threading import Lock
import time
from typing import Callable
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

MAX_BODY_BYTES = 65_536
RUN_ID = re.compile(r"^[0-9a-f]{32}$")


def _write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2),
                         encoding="utf-8")
    temporary.replace(path)


def _append_progress(path: str, verdict) -> None:
    """File-backed callback: bounded callbacks execute outside the server process."""
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"time": time.time(), **verdict.as_dict()},
                                ensure_ascii=False, allow_nan=False) + "\n")


def _live_runner(prep_model: str | None) -> Callable:
    from functools import partial
    from packs.market_research_next import build_report_type
    from packs.market_research_next.models import ExplicitPrepModel
    from treadle import register, run_report
    from treadle.execution_limits import ExecutionLimits

    report_type = register(build_report_type(
        key="market_research_browser_" + uuid4().hex,
        prep_model=ExplicitPrepModel(model=prep_model),
        business_model=ExplicitPrepModel(model=prep_model, max_tokens=12000),
        max_workers=4, max_correction_rounds=2,
        business_max_calls=6, business_max_depth=6,
        limits=ExecutionLimits(run_timeout_s=900, task_timeout_s=300, review_timeout_s=90),
    ))

    def execute(brief: dict, directory: Path):
        return run_report(report_type.key, brief,
                          progress=partial(_append_progress, str(directory / "events.jsonl")))

    return execute


def _page(example: dict) -> str:
    # User/model strings enter HTML only via escaping or DOM textContent.
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Treadle local pipeline test</title>
<link rel="icon" href="data:,">
<style>
body{font:16px system-ui;margin:2rem auto;max-width:900px;padding:0 1rem;line-height:1.5}
textarea{box-sizing:border-box;width:100%;height:26rem;font:14px monospace}
button{padding:.7rem 1rem;margin:1rem 0} pre{white-space:pre-wrap;overflow-wrap:anywhere}
a{margin-right:1rem} #state{font-weight:600}
</style></head><body>
<h1>Treadle local pipeline test</h1>
<p>Runs the connected business-object PREP, research, bounded review, and report
pipeline using configured live models. This is a local test surface.</p>
<p>Limits: 900 seconds overall; 300 seconds per task; 90 seconds per review;
120 model attempts; 60 tool dispatches; at most two correction rounds.
Source tracing is disabled.</p>
<form id="run-form"><label for="brief">Business brief (JSON)</label>
<textarea id="brief" required>""" + html.escape(json.dumps(example, indent=2)) + """</textarea>
<button id="start" type="submit">Start report</button></form>
<div role="status" aria-live="polite" id="state" data-status="idle">Ready</div>
<p id="elapsed"></p><p id="run-id"></p>
<p id="links" hidden><a id="html-link">Open report</a><a id="json-link">Report JSON</a></p>
<pre id="details"></pre><h2>Completed steps</h2><pre id="events"></pre>
<script>
const state = document.getElementById('state');
const details = document.getElementById('details');
const start = document.getElementById('start');
let polling = false;
async function poll(id) {
  if (polling) return;
  polling = true;
  start.disabled = true;
  document.getElementById('run-id').textContent = 'Run: ' + id;
  try {
    while (true) {
      const response = await fetch('/runs/' + id);
      if (!response.ok) throw new Error('Could not read run status.');
      const data = await response.json();
      state.dataset.status = data.status;
      state.textContent = data.status === 'running' ? 'Running full pipeline' :
        data.status === 'completed' ? 'Completed' :
        data.status === 'incomplete' ? 'Incomplete — review the report findings' :
        data.status === 'interrupted' ? 'Interrupted — server restarted; this run was not resumed' : 'Failed';
      document.getElementById('elapsed').textContent =
        'Elapsed: ' + Math.round(data.elapsed_seconds) + ' seconds';
      details.textContent = JSON.stringify(data, null, 2);
      const eventsResponse = await fetch('/runs/' + id + '/events');
      if (eventsResponse.ok) {
        const events = await eventsResponse.json();
        document.getElementById('events').textContent = events.map(event =>
          event.key + ': ' + event.status).join('\\n');
      }
      if (data.status !== 'running') {
        if (data.status === 'completed' || data.status === 'incomplete') {
          document.getElementById('html-link').href = '/runs/' + id + '/report';
          document.getElementById('json-link').href = '/runs/' + id + '/report.json';
          document.getElementById('links').hidden = false;
        }
        break;
      }
      await new Promise(resolve => setTimeout(resolve, 1500));
    }
  } catch (error) {
    state.textContent = error.message;
    state.dataset.status = 'disconnected';
  } finally {
    polling = false;
    // A lost connection must not invite a second paid run. Reload to resume by URL.
    start.disabled = state.dataset.status === 'disconnected';
  }
}
document.getElementById('run-form').addEventListener('submit', async event => {
  event.preventDefault();
  start.disabled = true;
  document.getElementById('links').hidden = true;
  let submitted = false;
  let requestId;
  try {
    const brief = JSON.parse(document.getElementById('brief').value);
    requestId = crypto.randomUUID().replaceAll('-', '');
    history.replaceState(null, '', '/?run=' + requestId);
    submitted = true;
    const response = await fetch('/runs', {method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Treadle-Run': '1',
        'Idempotency-Key': requestId},
      body: JSON.stringify({brief})});
    const data = await response.json();
    if (response.status === 409 && data.run_id) {
      history.replaceState(null, '', '/?run=' + data.run_id);
      await poll(data.run_id);
      return;
    }
    if (!response.ok) {
      // A parsed client-error response confirms the request was rejected. Server
      // errors remain uncertain: the job might have started before the failure.
      if (response.status >= 400 && response.status < 500) submitted = false;
      throw new Error(data.detail || 'Could not start report.');
    }
    history.replaceState(null, '', '/?run=' + data.run_id);
    await poll(data.run_id);
  } catch (error) {
    if (submitted) {
      // This is a read-only recovery using the ID saved before dispatch. Neither
      // a lost response nor a page reload creates another paid run.
      await poll(requestId);
      return;
    }
    state.textContent = error.message;
    state.dataset.status = 'error';
    start.disabled = false;
  }
});
const resumed = new URLSearchParams(location.search).get('run');
if (resumed && /^[0-9a-f]{32}$/.test(resumed)) poll(resumed);
</script></body></html>"""


def create_app(*, out_dir: Path, prep_model: str | None = None, port: int = 8779,
               runner: Callable | None = None) -> FastAPI:
    """Construct the local test app; an injected runner enables offline HTTP tests."""
    from packs.market_research_next import validate
    from packs.market_research_next.render import render_html

    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    execute = runner if runner is not None else _live_runner(prep_model)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="treadle-browser")
    lock = Lock()
    jobs: dict[str, dict] = {}
    example_path = ROOT / "docs/treadle/example_hybrid_venture.json"
    example = json.loads(example_path.read_text(encoding="utf-8"))
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    @asynccontextmanager
    async def lifespan(_app):
        yield
        executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Treadle local pipeline test", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def local_request_boundary(request: Request, call_next):
        host = request.headers.get("host", "")
        if host not in allowed_hosts:
            return JSONResponse({"detail": "Local host required."}, status_code=403)
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin is not None and origin != "http://" + host:
                return JSONResponse({"detail": "Same origin required."}, status_code=403)
            if (request.headers.get("sec-fetch-site") not in (None, "same-origin")
                    or request.headers.get("x-treadle-run") != "1"):
                return JSONResponse({"detail": "Local run header required."}, status_code=403)
            if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
                return JSONResponse({"detail": "JSON content required."}, status_code=415)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    def public_status(job: dict) -> dict:
        status = {key: value for key, value in job.items() if key != "started_monotonic"}
        end = job.get("finished_at", time.time())
        status["elapsed_seconds"] = max(0.0, end - job["started_at"])
        return status

    def persisted_job(run_id: str) -> dict | None:
        """Read prior receipts only; never resubmit work after a server restart."""
        directory = out_dir / run_id
        status_path = directory / "status.json"
        if not status_path.is_file():
            return None
        try:
            job = json.loads(status_path.read_text(encoding="utf-8"))
            if (not isinstance(job, dict) or job.get("run_id") != run_id
                    or job.get("status") not in ("running", "completed", "incomplete", "failed")
                    or not isinstance(job.get("started_at"), (int, float))):
                return None
            if job["status"] == "running":
                job.update(status="interrupted", ok=False, error_type="ServerRestart",
                           finished_at=status_path.stat().st_mtime,
                           error="The server restarted. This run was interrupted and was not resumed.")
            elif job["status"] in ("completed", "incomplete") and not all(
                    (directory / name).is_file() for name in ("report.json", "report.html")):
                job.update(status="failed", ok=False, error_type="MissingArtifact",
                           error="Saved report artifacts are unavailable.")
            return job
        except (OSError, ValueError, TypeError):
            return None

    def finish(run_id: str, brief: dict, directory: Path) -> None:
        try:
            report = execute(brief, directory)
            report_dict = asdict(report)
            _write_json(directory / "report.json", report_dict)
            (directory / "report.html").write_text(render_html(report), encoding="utf-8")
            execution = report.execution or {}
            update = {
                "status": "completed" if report.ok else "incomplete", "ok": report.ok,
                "correction_rounds": execution.get("correction_rounds", 0),
                "stop_reason": execution.get("stop_reason"), "usage": execution.get("usage"),
            }
        except Exception as exc:
            # Provider exceptions may contain request text or credentials. Keep them
            # out of HTTP responses and persisted status, including exception messages.
            update = {"status": "failed", "ok": False, "error_type": type(exc).__name__,
                      "error": "Pipeline execution failed. No report is available."}
        with lock:
            jobs[run_id].update(update, finished_at=time.time())
            _write_json(directory / "status.json", public_status(jobs[run_id]))

    def lookup(run_id: str) -> tuple[dict, Path]:
        if not RUN_ID.fullmatch(run_id):
            raise HTTPException(404, "Run not found.")
        with lock:
            job = jobs.get(run_id) or persisted_job(run_id)
            if job is None:
                raise HTTPException(404, "Run not found.")
            return public_status(job), out_dir / run_id

    @app.get("/", response_class=HTMLResponse)
    def index():
        return HTMLResponse(_page(example))

    @app.get("/health")
    def health():
        return {"ok": True, "pipeline": "market_research_next", "live_providers": runner is None}

    @app.get("/favicon.ico")
    def favicon():
        return Response(status_code=204)

    @app.post("/runs")
    async def start_run(request: Request):
        request_id = request.headers.get("idempotency-key")
        if request_id is not None and not RUN_ID.fullmatch(request_id):
            raise HTTPException(422, "Idempotency-Key must be a 32-character hexadecimal run ID.")
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_BODY_BYTES:
                raise HTTPException(413, "Brief exceeds 64 KiB.")
            raw.extend(chunk)
        try:
            payload = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(payload, dict) or set(payload) != {"brief"}:
                raise ValueError()
            brief = payload["brief"]
            if not isinstance(brief, dict):
                raise ValueError()
            validate(brief)
        except (ValueError, TypeError, RecursionError, UnicodeError):
            raise HTTPException(422, "Provide a valid JSON business brief with a description.") from None
        with lock:
            if request_id:
                previous = jobs.get(request_id) or persisted_job(request_id)
                directory = out_dir / request_id
                if previous is not None:
                    try:
                        saved_brief = json.loads((directory / "brief.json").read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        raise HTTPException(409, "This run ID already exists; its brief is unavailable.") from None
                    if json.dumps(saved_brief, sort_keys=True) != json.dumps(brief, sort_keys=True):
                        raise HTTPException(409, "This run ID was already used for a different brief.")
                    return JSONResponse({"run_id": request_id, "status": previous["status"]},
                                        status_code=202)
                if directory.exists():
                    raise HTTPException(409, "This run ID already exists and cannot be restarted.")
            for current in jobs.values():
                if current["status"] == "running":
                    return JSONResponse({"detail": "A report is already running.",
                                         "run_id": current["run_id"]}, status_code=409)
            run_id = request_id or uuid4().hex
            directory = out_dir / run_id
            directory.mkdir()
            job = {"run_id": run_id, "status": "running", "started_at": time.time()}
            _write_json(directory / "brief.json", brief)
            _write_json(directory / "status.json", public_status(job))
            jobs[run_id] = job
            executor.submit(finish, run_id, brief, directory)
        return JSONResponse({"run_id": run_id, "status": "running"}, status_code=202)

    @app.get("/runs/{run_id}")
    def status(run_id: str):
        return lookup(run_id)[0]

    @app.get("/runs/{run_id}/events")
    def events(run_id: str):
        _, directory = lookup(run_id)
        path = directory / "events.jsonl"
        if not path.exists():
            return []
        result = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                result.append(json.loads(line))
            except ValueError:
                continue  # A concurrently appended final line may be incomplete.
        return result

    def artifact(run_id: str, name: str, media_type: str) -> Response:
        job, directory = lookup(run_id)
        if job["status"] not in ("completed", "incomplete"):
            raise HTTPException(409, "Report is not available.")
        return Response((directory / name).read_bytes(), media_type=media_type)

    @app.get("/runs/{run_id}/report")
    def report_html(run_id: str):
        return artifact(run_id, "report.html", "text/html")

    @app.get("/runs/{run_id}/report.json")
    def report_json(run_id: str):
        return artifact(run_id, "report.json", "application/json")

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8779)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "out/treadle-browser")
    parser.add_argument("--prep-model", help="operator-selected PREP model; otherwise TREADLE_PREP_MODEL")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    from dotenv import load_dotenv
    import uvicorn

    load_dotenv(ROOT / ".env")
    app = create_app(out_dir=args.out_dir, prep_model=args.prep_model, port=args.port)
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
