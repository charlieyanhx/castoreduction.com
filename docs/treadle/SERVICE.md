# Treadle service

Phase 5. Treadle as something another program calls: one set of calls
(`treadle/service/core.py`) behind three surfaces. Standard library only, so the engine
still installs on its own.

```
python -m treadle --pack NAME types | describe | survey | show | answer | run
python -m treadle --pack NAME http [--port 8780]      JSON over HTTP, loopback only
python -m treadle --pack NAME mcp                     MCP server on stdio
```

`--pack` loads a pack by name (`travel_guide`, `market_research_next`) or dotted path, and
repeats. Surveys and runs are kept under `--out-dir` (default `$TREADLE_OUT_DIR`, then
`./treadle-out`), so a survey started by one command is answered by the next. Installed
with pip, the same command line is `treadle`.

## How a report is asked for

`describe` says which of three ways a report type takes:

| `survey` | What the caller does |
|---|---|
| `derived` | `survey` starts a session; answer `next_question` until `ready`; `run --survey ID`. The questions are derived from the plan, so only what the plan needs is asked. |
| `form` | `survey` returns a fixed questionnaire (`treadle.form/1`); `run --answers` with the filled form. Market research uses this: the founder-approved survey, served unchanged. |
| `none` | `run --brief` with the brief the type validates. |

Surveys are kept by the service, not handed to the client. A survey's JSON is unsigned,
so a client holding it could rewrite an answer's tier; the client holds only its id.
One answer at a time keeps its precise refusal code (`stale_question`, `open_dispute`);
a batch is all or nothing and reports each refused field under `invalid_answer`.

## Runs

A run is a directory under the out dir: `brief.json`, `survey.json` (survey runs),
`status.json`, `events.jsonl`, `report.json`, and `report.html` when the pack declares
`render`.

- **Never twice.** A caller may name the run id (`Idempotency-Key` over HTTP, `run_id`
  over MCP, `--run-id` on the command line). The same request returns the existing run; a
  different request under that id is a `conflict`.
- **Never resumed.** After a restart, a receipt still saying `running` reads as
  `interrupted`. The work behind it may already have been paid for.
- **One at a time** by default (`busy` names the running run). `http --max-running N`
  raises it.
- **No provider text.** A failed run records the exception's class only, and so does a
  failed section: its reason and any raising detector's finding are cut to the class in
  the events, `report.json` and the rendered page.

Statuses: `running`, `completed`, `incomplete` (produced, with a failed section or an
unresolved review issue), `failed`, `interrupted`.

## One event shape: `treadle.run/1`

```json
{"protocol": "treadle.run/1", "run_id": "...", "at": 1792694310.79,
 "type": "section", "section": "spot", "status": "ok",
 "detail": {"reason": "", "findings": [], "reads": {"place": "d8ae..."}, "label": "Where to sit"}}
```

A sections pack writes a `section` event per section verdict as that section finishes
(the bounded workflow delivers it from its own thread, so a watcher never waits for the
round), and again on each correction round. A runner pack's partial results become `checkpoint` events with
`detail: {"keys": [...], "steps_completed": [...]}`. Partial results are named, never
copied. Events are appended to a file because a pack may run sections in isolated
processes, which receive the sink pickled. Read them with `after=N`; the response's
`next` is the index to ask from next time.

## HTTP

```
GET  /health  /types  /types/{key}
POST /surveys                    {"report_type", "brief"?}
GET  /surveys/{id}
POST /surveys/{id}/answers       {"answers", "reply_to"?}
POST /runs                       {"report_type"?, one of "brief" | "answers" | "survey_id"}
GET  /runs/{id}  /runs/{id}/events?after=N  /runs/{id}/report.json  /runs/{id}/report
```

Binds to loopback and refuses any other `Host`. A POST must be JSON (64 KiB at most)
with `X-Treadle-Run: 1`, which a browser cannot send cross-site without a preflight this
server never answers. Refusals are `{"error": {"code", "message", "detail"}}`. The codes
are listed once in `treadle/service/errors.py`, survey codes included.

## MCP

Tools: `list_report_types`, `describe_report_type`, `start_survey`, `get_survey`,
`answer_survey`, `start_run`, `get_run`, `get_run_events`, `get_report`. Protocol
versions 2025-06-18, 2025-03-26 and 2024-11-05. A refusal is a tool result with
`isError` and the error dict, so the calling model can correct itself. An unknown tool
or malformed message is a JSON-RPC error. While serving, `sys.stdout` points at stderr,
so a pack that prints cannot corrupt the stream.

To register it with an MCP client, run it with the repo's interpreter and put the repo
on `PYTHONPATH`. Clients start servers from an arbitrary directory, and the repo is not
installed into its own `.venv`:

```json
{"command": "/path/to/castor-advisories/.venv/bin/python",
 "args": ["-m", "treadle", "--pack", "market_research_next", "--out-dir", "/path/to/runs", "mcp"],
 "env": {"PYTHONPATH": "/path/to/castor-advisories"}}
```

Bare pack names (`travel_guide`, `market_research_next`) resolve only inside a repo
checkout: the `treadle` distribution packages the engine, not `packs/`. A pip-installed
`treadle` loads a pack by its own module path (`--pack mypackage.mypack`).

`start_run` on `market_research_next` makes live, paid model and research calls. The
pack reads `TREADLE_PREP_MODEL` and the Anthropic settings from the environment; the
service does not read `.env`.

## What a pack may declare for the service

`ReportType.render` (Report to HTML) and, for a type whose questions are written rather
than derived, `form` with `from_form` (answers to brief). A type has a derived survey or a
form, never both. Market research declares all three in
`packs/market_research_next/__init__.py` and `form.py`.

## Still open

- **The caller is not authenticated.** An answer is "stated" because someone called
  `answer`; the boundary is that only this machine can call. A served deployment needs
  callers to authenticate before STATED can mean a person.
- **No finder loop.** `status.to_find` lists what an agent may look up, but no call
  submits a finding (`Survey.found`).
- **Verified offline only.** The HTTP and MCP surfaces drive the picnic pack from survey
  to report in tests, and market research is exercised through its form with an injected
  executor. A live market-research run through the service has not been made.
