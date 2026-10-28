"""Local browser surface for the real selectable Treadle pipeline.

    .venv/bin/python scripts/treadle_browser_server.py --prep-model MODEL

The server binds to 127.0.0.1 only. Starting a report uses configured live providers.
"""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import html
import json
from pathlib import Path
import re
import sys
from typing import Callable
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

MAX_BODY_BYTES = 65_536
RUN_ID = re.compile(r"^[0-9a-f]{32}$")


def _live_runner(prep_model: str | None) -> Callable:
    from packs.market_research_next import build_report_type
    from packs.market_research_next.models import ExplicitPrepModel
    from treadle import register, run_report
    from treadle.execution_limits import ExecutionLimits
    from treadle.service import EventLog

    report_type = register(build_report_type(
        key="market_research_browser_" + uuid4().hex,
        prep_model=ExplicitPrepModel(model=prep_model),
        business_model=ExplicitPrepModel(model=prep_model, max_tokens=12000),
        max_workers=4, max_correction_rounds=2,
        business_max_calls=6, business_max_depth=6,
        limits=ExecutionLimits(run_timeout_s=1500, task_timeout_s=300, review_timeout_s=90),
    ))

    def execute(brief: dict, directory: Path):
        return run_report(report_type.key, brief,
                          progress=EventLog(str(directory / "events.jsonl"), directory.name))

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
<p>Limits: 1500 seconds overall; 300 seconds per task; 90 seconds per review;
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
          (event.section || event.key) + ': ' + event.status).join('\\n');
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

    from treadle.service import RunStore, ServiceError

    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    execute = runner if runner is not None else _live_runner(prep_model)
    # The store's report type is a stable label: the live runner registers a fresh key
    # per process, and a run id reused after a restart must still match its first request.
    store = RunStore(out_dir, execute=lambda _key, brief, _survey, directory: execute(
        brief, directory), render=render_html)
    example_path = ROOT / "docs/treadle/example_hybrid_venture.json"
    example = json.loads(example_path.read_text(encoding="utf-8"))
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    @asynccontextmanager
    async def lifespan(_app):
        yield
        store.close()

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

    def guarded(call, *args):
        """The store's refusals as this page's HTTP errors."""
        try:
            return call(*args)
        except ServiceError as exc:
            raise HTTPException(404 if exc.code == "not_found" else 409, str(exc)) from None

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
        try:
            started, _created = store.start("market_research_next", brief, run_id=request_id)
        except ServiceError as exc:
            if exc.code == "busy":
                return JSONResponse({"detail": str(exc), "run_id": exc.detail["run_id"]},
                                    status_code=409)
            raise HTTPException(409, str(exc)) from None
        return JSONResponse(started, status_code=202)

    @app.get("/runs/{run_id}")
    def status(run_id: str):
        return guarded(store.status, run_id)

    @app.get("/runs/{run_id}/events")
    def events(run_id: str):
        return guarded(store.events, run_id)

    @app.get("/runs/{run_id}/report")
    def report_html(run_id: str):
        return Response(guarded(store.html, run_id), media_type="text/html")

    @app.get("/runs/{run_id}/report.json")
    def report_json(run_id: str):
        return JSONResponse(guarded(store.report, run_id))

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
