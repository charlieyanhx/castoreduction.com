"""Treadle phase 5: the service. One set of calls, three surfaces, one event shape.

Offline throughout. The picnic pack (a derived survey) and two small packs written here
(a runner, and a sections pack that renders) run through the real engine; the market
research pack is exercised only through its form, with an injected executor, so no
provider is ever called.
"""
from __future__ import annotations

import io
import json
import pickle
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from threading import Event

import pytest

from test_treadle_survey_is_derived_from_the_plan import FOOD, PICNIC
from treadle.core.section import Section
from treadle.registry import REPORT_TYPES, load_pack
from treadle.run import Report
from treadle.service import (EVENT_PROTOCOL, FORM_PROTOCOL, EventLog, RunStore, Service,
                             ServiceError, to_event)
from treadle.service.cli import main as cli
from treadle.service.events import read_events
from treadle.service.http import MAX_BODY_BYTES, make_server
from treadle.service.mcp import SUPPORTED_VERSIONS, McpServer, serve_stdio
from treadle.spec import ReportType


def _runner(brief, progress=None):
    if progress is not None:
        progress({"_steps_completed": ["gather"], "draft": "half"})
    return {"answer": brief["question"].upper()}


def _check_question(brief):
    if not isinstance(brief.get("question"), str) or not brief["question"]:
        raise ValueError("a question is required")


RUNNER = ReportType(key="echo_for_service", label="Echo", runner=_runner,
                    validate=_check_question)
PAGE = ReportType(
    key="page_for_service", label="Page",
    sections=lambda brief: [Section("title", lambda ctx: f"<b>{ctx['brief']['name']}</b>",
                                    consumes=("brief",), label="Title")],
    render=lambda report: "<!doctype html><p>" + report.result["title"] + "</p>")


@pytest.fixture
def packs():
    for rt in (PICNIC, RUNNER, PAGE):
        REPORT_TYPES.register(rt.key, rt)
    yield
    for rt in (PICNIC, RUNNER, PAGE):
        REPORT_TYPES.pop(rt.key, None)


@pytest.fixture
def service(tmp_path, packs):
    svc = Service(tmp_path)
    yield svc
    svc.close()


def _answer_all(svc: Service, view: dict, person: dict) -> dict:
    while view["next_question"] is not None:
        q = view["next_question"]
        view = svc.answer_survey(view["survey_id"], {q["field"]: person[q["field"]]},
                                 {q["field"]: q["id"]})
    return view


def _ready_survey(svc: Service) -> str:
    return _answer_all(svc, svc.start_survey(PICNIC.key), FOOD)["survey_id"]


# --------------------------------------------------------------------------- events
@dataclass
class _Verdict:
    key: str
    status: str

    def as_dict(self):
        return {"key": self.key, "status": self.status, "reason": "", "label": "L"}


def test_a_section_and_a_checkpoint_share_one_envelope():
    section = to_event("r", _Verdict("spot", "ok"))
    checkpoint = to_event("r", {"_steps_completed": ["a"], "profile": {"x": 1}})
    for event in (section, checkpoint):
        assert set(event) == {"protocol", "run_id", "at", "type", "section", "status",
                              "detail"}
        assert event["protocol"] == EVENT_PROTOCOL
    assert (section["type"], section["section"], section["status"]) == ("section", "spot", "ok")
    assert section["detail"] == {"reason": "", "label": "L"}
    assert checkpoint["type"] == "checkpoint"
    # The partial result is named, never copied: it can be most of a report.
    assert checkpoint["detail"] == {"keys": ["_steps_completed", "profile"],
                                    "steps_completed": ["a"]}


def test_the_sink_pickles_and_a_half_written_line_waits(tmp_path):
    path = tmp_path / "events.jsonl"
    sink = pickle.loads(pickle.dumps(EventLog(str(path), "r1")))
    sink(_Verdict("a", "ok"))
    sink({"k": 1})
    with path.open("a") as handle:
        handle.write('{"protocol": "treadle.run/1"')
    events = read_events(path)
    assert [e["type"] for e in events] == ["section", "checkpoint"]
    assert read_events(path, after=1)[0]["type"] == "checkpoint"


# --------------------------------------------------------------------------- surveys and runs
def test_a_derived_survey_runs_once_it_is_ready(service):
    view = service.start_survey(PICNIC.key)
    assert view["protocol"] == "treadle.survey/1" and view["ready"] is False
    with pytest.raises(ServiceError) as refused:
        service.start_run(survey_id=view["survey_id"])
    assert refused.value.code == "not_ready"
    assert "place" in refused.value.detail["open"]
    survey_id = _answer_all(service, view, FOOD)["survey_id"]
    started = service.start_run(survey_id=survey_id)
    status = service.runs.wait(started["run_id"])
    assert (status["status"], status["ok"], status["report_type"]) == (
        "completed", True, PICNIC.key)
    report = service.report(started["run_id"])
    assert report["type"] == PICNIC.key and report["open_inputs"] == []
    events = service.run_events(started["run_id"])
    assert events["next"] == len(events["events"]) > 0
    assert {e["type"] for e in events["events"]} == {"section"}
    assert service.run_events(started["run_id"], after=events["next"])["events"] == []


def test_a_survey_refusal_keeps_its_code(service):
    view = service.start_survey(PICNIC.key)
    q = view["next_question"]
    with pytest.raises(ServiceError) as bad:
        service.answer_survey(view["survey_id"], {"guests": "lots"})
    assert bad.value.code == "invalid_answer" and bad.value.http_status == 422
    service.answer_survey(view["survey_id"], {q["field"]: "Hyde Park"}, {q["field"]: q["id"]})
    with pytest.raises(ServiceError) as stale:
        service.answer_survey(view["survey_id"], {"place": "Regent's Park"},
                              {"place": q["id"]})
    assert stale.value.code == "stale_question" and stale.value.http_status == 409


def test_a_survey_is_kept_by_the_service_not_the_client(service, tmp_path):
    view = service.start_survey(PICNIC.key)
    assert "survey" not in view and "entries" not in json.dumps(view)
    assert (tmp_path / "surveys" / f"{view['survey_id']}.json").is_file()
    assert Service(tmp_path).survey(view["survey_id"])["questions"] == view["questions"]
    for bad in ("../etc", "0" * 32):
        with pytest.raises(ServiceError) as missing:
            service.survey(bad)
        assert missing.value.code == "not_found"


def test_a_runner_pack_reports_checkpoints_in_the_same_shape(service):
    started = service.start_run(report_type=RUNNER.key, brief={"question": "why"})
    assert service.runs.wait(started["run_id"])["status"] == "completed"
    assert service.report(started["run_id"])["result"] == {"answer": "WHY"}
    (event,) = service.run_events(started["run_id"])["events"]
    assert (event["protocol"], event["type"]) == (EVENT_PROTOCOL, "checkpoint")
    assert event["detail"]["steps_completed"] == ["gather"]
    with pytest.raises(ServiceError) as no_survey:
        service.start_survey(RUNNER.key)
    assert no_survey.value.code == "no_survey"


def test_every_refusal_happens_before_anything_is_written(service, tmp_path):
    cases = [
        (dict(report_type="nope", brief={}), "unknown_report"),
        (dict(report_type=RUNNER.key, brief={}), "invalid_brief"),
        (dict(report_type=RUNNER.key, brief={"question": float("nan")}), "bad_request"),
        (dict(report_type=RUNNER.key, brief={"question": "a"}, answers={}), "bad_request"),
        (dict(report_type=RUNNER.key), "bad_request"),
        (dict(report_type=RUNNER.key, answers={"q": "a"}), "no_form"),
        (dict(report_type=RUNNER.key, brief={"question": "a"}, run_id="../x"), "bad_request"),
    ]
    for kwargs, code in cases:
        with pytest.raises(ServiceError) as refused:
            service.start_run(**kwargs)
        assert refused.value.code == code, kwargs
    assert [p.name for p in tmp_path.iterdir()] == ["surveys"]


def test_a_survey_runs_only_as_its_own_report_type(service):
    survey_id = _ready_survey(service)
    with pytest.raises(ServiceError) as refused:
        service.start_run(survey_id=survey_id, report_type=RUNNER.key)
    assert refused.value.code == "bad_request"


def test_a_rendering_pack_serves_html_and_one_that_does_not_says_so(service):
    page = service.start_run(report_type=PAGE.key, brief={"name": "Lisbon"})
    assert service.runs.wait(page["run_id"])["html"] is True
    assert service.report_html(page["run_id"]) == "<!doctype html><p><b>Lisbon</b></p>"
    plain = service.start_run(report_type=RUNNER.key, brief={"question": "q"})
    service.runs.wait(plain["run_id"])
    with pytest.raises(ServiceError) as refused:
        service.report_html(plain["run_id"])
    assert refused.value.code == "unavailable"


def test_a_run_id_never_runs_twice_and_a_restart_never_resumes(tmp_path, packs):
    calls, release, entered = [], Event(), Event()

    def execute(key, brief, survey, directory):
        calls.append(key)
        entered.set()
        assert release.wait(3)
        return Report(key, {"answer": "x"}, None)

    svc = Service(tmp_path, runs=RunStore(tmp_path, execute=execute))
    run_id = "a" * 32
    try:
        first = svc.start_run(report_type=RUNNER.key, brief={"question": "q"}, run_id=run_id)
        assert first == {"run_id": run_id, "status": "running"}
        assert entered.wait(1)
        assert svc.start_run(report_type=RUNNER.key, brief={"question": "q"},
                             run_id=run_id) == first
        for other in ({"question": "different"},):
            with pytest.raises(ServiceError) as conflict:
                svc.start_run(report_type=RUNNER.key, brief=other, run_id=run_id)
            assert conflict.value.code == "conflict"
        with pytest.raises(ServiceError) as busy:
            svc.start_run(report_type=RUNNER.key, brief={"question": "q2"})
        assert (busy.value.code, busy.value.detail) == ("busy", {"run_id": run_id})
    finally:
        release.set()
    assert svc.runs.wait(run_id)["status"] == "completed"
    svc.close()
    assert calls == [RUNNER.key]

    # A receipt still saying `running` after a restart reads as interrupted, read-only.
    stuck = tmp_path / ("b" * 32)
    stuck.mkdir()
    (stuck / "status.json").write_text(json.dumps(
        {"run_id": "b" * 32, "report_type": RUNNER.key, "status": "running",
         "started_at": time.time()}))
    (stuck / "brief.json").write_text(json.dumps({"question": "q"}))
    later = Service(tmp_path, runs=RunStore(tmp_path, execute=lambda *_: pytest.fail("resumed")))
    assert later.run("b" * 32)["status"] == "interrupted"
    assert later.start_run(report_type=RUNNER.key, brief={"question": "q"},
                           run_id="b" * 32) == {"run_id": "b" * 32, "status": "interrupted"}
    later.close()


def test_a_failed_run_keeps_the_provider_message_out(tmp_path, packs):
    def execute(*_):
        raise RuntimeError("sk-secret and the private brief")

    svc = Service(tmp_path, runs=RunStore(tmp_path, execute=execute))
    started = svc.start_run(report_type=RUNNER.key, brief={"question": "q"})
    status = svc.runs.wait(started["run_id"])
    svc.close()
    assert (status["status"], status["error_type"]) == ("failed", "RuntimeError")
    assert "secret" not in json.dumps(status)
    assert "secret" not in (tmp_path / started["run_id"] / "status.json").read_text()


# --------------------------------------------------------------------------- the market research form
def test_market_research_is_asked_through_its_approved_form(tmp_path):
    import survey_questions as sq

    load_pack("market_research_next")
    seen = []

    def execute(key, brief, survey, directory):
        seen.append(brief)
        return Report(key, {"brief": brief}, None)

    svc = Service(tmp_path, runs=RunStore(tmp_path, execute=execute, render=lambda r: None))
    view = svc.start_survey("market_research_next")
    assert (view["kind"], view["protocol"]) == ("form", FORM_PROTOCOL)
    form = view["form"]
    assert form["opening"]["question"] == sq.OPENING_QUESTION
    assert [q["field"] for q in form["questions"]] == list(sq.FIELDS)
    assert [q["question"] for q in form["questions"]] == [q["question"] for q in sq.QUESTIONS]
    started = svc.start_run(report_type="market_research_next", answers={
        "description": "An AI receptionist for dental clinics", "geography": "Toronto"})
    svc.runs.wait(started["run_id"])
    svc.close()
    (brief,) = seen
    assert brief["description"] == "An AI receptionist for dental clinics"
    assert brief["intake"]["facts"] == {"geography": "Toronto"}
    assert "status_quo" in brief["intake"]["unknowns"]
    with pytest.raises(ServiceError) as refused:
        Service(tmp_path).start_run(report_type="market_research_next",
                                    answers={"geography": "Toronto"})
    assert refused.value.code == "invalid_brief"


def test_a_type_declares_a_derived_survey_or_a_form_never_both():
    with pytest.raises(ValueError):
        ReportType(key="half_form", label="x", runner=_runner, form=lambda: {})
    with pytest.raises(ValueError):
        ReportType(key="both", label="x", sections=PICNIC.sections, inputs=PICNIC.inputs,
                   form=lambda: {}, from_form=lambda a: a)


# --------------------------------------------------------------------------- HTTP
@pytest.fixture
def http(service):
    server = make_server(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _call(host, method, path, body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    sent = {"Host": host}
    if method == "POST":
        sent.update({"Content-Type": "application/json", "X-Treadle-Run": "1"})
    sent.update(headers or {})
    request = urllib.request.Request(f"http://{host}{path}", data=data, method=method,
                                     headers={k: v for k, v in sent.items() if v is not None})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.headers, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read().decode()


def test_http_drives_a_survey_to_a_report(http):
    status, headers, body = _call(http, "GET", "/health")
    assert status == 200 and json.loads(body)["ok"] is True
    assert headers["Cache-Control"] == "no-store"
    status, _, body = _call(http, "POST", "/surveys", {"report_type": PICNIC.key})
    view = json.loads(body)
    assert status == 201
    while view["next_question"]:
        q = view["next_question"]
        _, _, body = _call(http, "POST", f"/surveys/{view['survey_id']}/answers",
                           {"answers": {q["field"]: FOOD[q["field"]]},
                            "reply_to": {q["field"]: q["id"]}})
        view = json.loads(body)
    status, _, body = _call(http, "POST", "/runs", {"survey_id": view["survey_id"]},
                            headers={"Idempotency-Key": "c" * 32})
    assert (status, json.loads(body)["run_id"]) == (202, "c" * 32)
    for _ in range(200):
        _, _, body = _call(http, "GET", f"/runs/{'c' * 32}")
        if json.loads(body)["status"] != "running":
            break
        time.sleep(0.02)
    assert json.loads(body)["status"] == "completed"
    _, _, body = _call(http, "GET", f"/runs/{'c' * 32}/events?after=1")
    assert json.loads(body)["events"][0]["protocol"] == EVENT_PROTOCOL
    status, _, body = _call(http, "GET", f"/runs/{'c' * 32}/report.json")
    assert status == 200 and json.loads(body)["type"] == PICNIC.key
    status, _, body = _call(http, "GET", f"/runs/{'c' * 32}/report")
    assert status == 409 and json.loads(body)["error"]["code"] == "unavailable"


def test_http_serves_a_pack_page_under_a_strict_policy(http):
    _, _, body = _call(http, "POST", "/runs", {"report_type": PAGE.key,
                                               "brief": {"name": "Porto"}})
    run_id = json.loads(body)["run_id"]
    for _ in range(200):
        _, _, body = _call(http, "GET", f"/runs/{run_id}")
        if json.loads(body)["status"] != "running":
            break
        time.sleep(0.02)
    status, headers, page = _call(http, "GET", f"/runs/{run_id}/report")
    assert status == 200 and "<b>Porto</b>" in page
    assert headers["Content-Type"].startswith("text/html")
    assert "script-src" not in headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


@pytest.mark.parametrize("headers,raw,status,code", [
    ({"X-Treadle-Run": None}, None, 403, "forbidden"),
    ({"Origin": "https://attacker.example"}, None, 403, "forbidden"),
    ({"Sec-Fetch-Site": "cross-site"}, None, 403, "forbidden"),
    ({"Content-Type": "text/plain"}, None, 415, "bad_request"),
    ({}, b'{"report_type": "echo_for_service", "brief": {"question": NaN}}', 400, "bad_request"),
    ({}, b'{"report_type": "echo_for_service", "brief": {"question": "q"}, "x": 1}', 400,
     "bad_request"),
    ({}, b"[1]", 400, "bad_request"),
    ({}, b"x" * (MAX_BODY_BYTES + 1), 413, "too_large"),
])
def test_http_refuses_before_any_run_starts(http, tmp_path, headers, raw, status, code):
    body = {"report_type": RUNNER.key, "brief": {"question": "q"}}
    got, _, text = _call(http, "POST", "/runs", body, headers=headers, raw=raw)
    assert (got, json.loads(text)["error"]["code"]) == (status, code)
    assert [p.name for p in tmp_path.iterdir()] == ["surveys"]


def test_http_answers_only_to_this_machine(http):
    status, _, body = _call(http, "GET", "/types", headers={"Host": "attacker.example"})
    assert status == 403 and json.loads(body)["error"]["code"] == "forbidden"
    assert _call(http, "GET", "/nowhere")[0] == 404
    assert _call(http, "DELETE", "/types")[0] == 405
    with pytest.raises(ValueError):
        make_server(Service.__new__(Service), host="0.0.0.0", port=0)


# --------------------------------------------------------------------------- MCP
def _rpc(server, method, params=None, msg_id=1):
    return server.handle({"jsonrpc": "2.0", "id": msg_id, "method": method,
                          "params": params or {}})


def test_mcp_negotiates_and_lists_one_tool_per_call(service):
    server = McpServer(service)
    init = _rpc(server, "initialize", {"protocolVersion": "2025-03-26"})["result"]
    assert init["protocolVersion"] == "2025-03-26"
    assert init["capabilities"] == {"tools": {"listChanged": False}}
    unknown = _rpc(server, "initialize", {"protocolVersion": "1999-01-01"})["result"]
    assert unknown["protocolVersion"] == SUPPORTED_VERSIONS[0]
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = [t["name"] for t in _rpc(server, "tools/list")["result"]["tools"]]
    assert names == ["list_report_types", "describe_report_type", "start_survey",
                     "get_survey", "answer_survey", "start_run", "get_run",
                     "get_run_events", "get_report"]
    assert _rpc(server, "ping")["result"] == {}
    assert _rpc(server, "resources/list")["error"]["code"] == -32601


def test_mcp_drives_a_survey_to_a_report(service):
    server = McpServer(service)

    def call(name, **arguments):
        result = _rpc(server, "tools/call", {"name": name, "arguments": arguments})["result"]
        assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
        return result

    view = call("start_survey", report_type=PICNIC.key)["structuredContent"]
    refused = call("start_run", survey_id=view["survey_id"])
    assert refused["isError"] and refused["structuredContent"]["error"]["code"] == "not_ready"
    while view["next_question"]:
        q = view["next_question"]
        view = call("answer_survey", survey_id=view["survey_id"],
                    answers={q["field"]: FOOD[q["field"]]},
                    reply_to={q["field"]: q["id"]})["structuredContent"]
    run_id = call("start_run", survey_id=view["survey_id"])["structuredContent"]["run_id"]
    service.runs.wait(run_id)
    assert call("get_run", run_id=run_id)["structuredContent"]["status"] == "completed"
    assert call("get_run_events", run_id=run_id, after=0)["structuredContent"]["next"] > 0
    assert call("get_report", run_id=run_id)["structuredContent"]["type"] == PICNIC.key


def test_mcp_tells_a_bad_call_from_a_refusal(service):
    server = McpServer(service)
    assert _rpc(server, "tools/call", {"name": "nope"})["error"]["code"] == -32602
    extra = _rpc(server, "tools/call", {"name": "get_run",
                                        "arguments": {"run_id": "a" * 32, "x": 1}})["result"]
    assert extra["isError"] and extra["structuredContent"]["error"]["code"] == "bad_request"
    missing = _rpc(server, "tools/call", {"name": "get_run", "arguments": {}})["result"]
    assert missing["isError"]
    assert server.handle(["batch"])["error"]["code"] == -32600


def test_mcp_keeps_stdout_for_the_protocol(service, monkeypatch):
    stdin = io.StringIO('not json\n\n{"jsonrpc": "2.0", "id": 1, "method": "ping"}\n')
    stdout, stderr = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(McpServer, "handle", lambda self, m: (print("a pack talking"),
                                                             {"jsonrpc": "2.0", "id": 1,
                                                              "result": {}})[1])
    serve_stdio(service)
    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert lines[0]["error"]["code"] == -32700
    assert lines[1] == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert "a pack talking" in stderr.getvalue()
    assert sys.stdout is stdout


# --------------------------------------------------------------------------- CLI
def test_the_cli_steps_a_survey_across_commands_and_runs_it(tmp_path, packs, capsys):
    out = ["--out-dir", str(tmp_path / "store")]
    assert cli([*out, "types"]) == 0
    assert PICNIC.key in capsys.readouterr().out
    assert cli([*out, "survey", PICNIC.key]) == 0
    view = json.loads(capsys.readouterr().out)
    while view["next_question"]:
        q = view["next_question"]
        answers = tmp_path / "answers.json"
        answers.write_text(json.dumps({q["field"]: FOOD[q["field"]]}))
        assert cli([*out, "answer", view["survey_id"], "--answers", str(answers)]) == 0
        view = json.loads(capsys.readouterr().out)
    assert cli([*out, "run", "--survey", view["survey_id"], "--out",
                str(tmp_path / "picnic")]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "completed"
    assert json.loads((tmp_path / "picnic.json").read_text())["type"] == PICNIC.key
    brief = tmp_path / "page.json"
    brief.write_text(json.dumps({"name": "Faro"}))
    assert cli([*out, "run", PAGE.key, "--brief", str(brief), "--out",
                str(tmp_path / "page")]) == 0
    assert "<b>Faro</b>" in (tmp_path / "page.html").read_text()


def test_the_cli_exits_2_on_a_refusal_with_the_code(tmp_path, packs, capsys):
    brief = tmp_path / "b.json"
    brief.write_text("{}")
    assert cli(["--out-dir", str(tmp_path), "run", "nope", "--brief", str(brief)]) == 2
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "unknown_report"


# --------------------------------------------------------------------------- review fixes
def _broken(ctx):
    raise RuntimeError("sk-ant-secret in a provider response body")


BROKEN = ReportType(
    key="broken_for_service", label="Broken",
    sections=lambda brief: [Section("fine", lambda ctx: "ok", consumes=("brief",)),
                            Section("bad", _broken, consumes=("brief",))],
    render=lambda report: json.dumps(report.sections))


def test_a_failed_section_keeps_only_its_exception_class(tmp_path):
    REPORT_TYPES.register(BROKEN.key, BROKEN)
    svc = Service(tmp_path)
    try:
        started = svc.start_run(report_type=BROKEN.key, brief={"x": 1})
        status = svc.runs.wait(started["run_id"])
        assert status["status"] == "incomplete"
        events = svc.run_events(started["run_id"])["events"]
        failed = [e for e in events if e["status"] == "failed"]
        assert failed and failed[0]["detail"]["reason"] == "RuntimeError"
        stored = (tmp_path / started["run_id"]).iterdir()
        for path in stored:
            assert "secret" not in path.read_text(), path.name
        assert "secret" not in json.dumps(svc.report(started["run_id"]))
        assert "secret" not in svc.report_html(started["run_id"])
    finally:
        svc.close()
        REPORT_TYPES.pop(BROKEN.key, None)


def test_a_raising_detector_finding_is_cut_to_its_class():
    from treadle.service.events import redact_verdict

    verdict = {"key": "k", "status": "flagged", "reason": "1 invariant(s) failed",
               "findings": ["no_negative: detector raised KeyError: 'api_key=sk-1'",
                            "plain finding: value is 3"]}
    assert redact_verdict(verdict)["findings"] == ["no_negative: detector raised KeyError",
                                                   "plain finding: value is 3"]
    assert redact_verdict(verdict)["reason"] == "1 invariant(s) failed"


def test_malformed_numbers_and_replies_are_bad_requests_not_crashes(http, service):
    got, _, text = _call(http, "GET", f"/runs/{'a' * 32}/events?after=%C2%B2")
    assert (got, json.loads(text)["error"]["code"]) == (400, "bad_request")
    got, _, text = _call(http, "POST", "/runs", {"report_type": RUNNER.key},
                         headers={"Content-Length": "²"})
    assert got == 400
    view = service.start_survey(PICNIC.key)
    for reply_to in (3, [{"a": 1}], {"place": 7}):
        with pytest.raises(ServiceError) as refused:
            service.answer_survey(view["survey_id"], {"place": "Hyde Park"}, reply_to)
        assert refused.value.code == "bad_request"
