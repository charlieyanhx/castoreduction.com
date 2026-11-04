"""treadle/service/mcp.py: the service as an MCP server, so an agent can order a report.

JSON-RPC 2.0 over stdio, one message per line: initialize, ping, tools/list, tools/call.
Each tool is one core.Service call. Written against the protocol rather than an SDK, so
the engine installs with no new dependency; the surface is small enough to hold whole.

A tool refusal is a tool RESULT with isError and the service's error dict, so the calling
model reads the code and can correct itself; a malformed call (unknown tool, bad JSON) is
a JSON-RPC error, which is the client's bug, not the model's.

STDOUT IS THE PROTOCOL. A pack that prints would corrupt the stream, so while serving,
sys.stdout points at stderr and only this module writes to the real one.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Callable, Optional, TextIO

from .core import Service
from .errors import ServiceError

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "treadle", "version": "0.1.0"}
INSTRUCTIONS = (
    "Treadle writes reports. list_report_types says what can be ordered. For a type with "
    "survey 'derived', start_survey and answer next_question until ready, then start_run "
    "with the survey_id. For survey 'form', fill the form and start_run with answers. "
    "Otherwise start_run with a brief. A run takes minutes: poll get_run until it is no "
    "longer running, read get_run_events for progress, then get_report. start_run may "
    "spend money on model and research providers.")

_ID = {"type": "string", "pattern": "^[0-9a-f]{32}$"}
_OBJECT = {"type": "object"}


def _tool(name: str, description: str, properties: dict, required: tuple[str, ...],
          call: Callable[[Service, dict], dict]) -> dict:
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties,
                            "required": list(required), "additionalProperties": False},
            "call": call}


TOOLS = (
    _tool("list_report_types", "The report types this server can write.", {}, (),
          lambda s, a: s.report_types()),
    _tool("describe_report_type", "One report type: its shape and how it is asked for.",
          {"report_type": {"type": "string"}}, ("report_type",),
          lambda s, a: s.describe(a["report_type"])),
    _tool("start_survey", "Start the survey for a report type (a derived survey's first "
                          "questions, or a fixed form).",
          {"report_type": {"type": "string"}, "brief": _OBJECT}, ("report_type",),
          lambda s, a: s.start_survey(a["report_type"], a.get("brief"))),
    _tool("get_survey", "A survey's status and open questions.", {"survey_id": _ID},
          ("survey_id",), lambda s, a: s.survey(a["survey_id"])),
    _tool("answer_survey", "Answer questions by field. Pass reply_to {field: question id} "
                           "so an answer to a question that has since changed is refused.",
          {"survey_id": _ID, "answers": _OBJECT, "reply_to": _OBJECT},
          ("survey_id", "answers"),
          lambda s, a: s.answer_survey(a["survey_id"], a["answers"], a.get("reply_to"))),
    _tool("start_run", "Start a report from exactly one of brief, answers (a form) or "
                       "survey_id. May spend money. run_id makes the call idempotent.",
          {"report_type": {"type": "string"}, "brief": _OBJECT, "answers": _OBJECT,
           "survey_id": _ID, "run_id": _ID}, (),
          lambda s, a: s.start_run(**a)),
    _tool("get_run", "A run's status: running, completed, incomplete, failed or "
                     "interrupted.", {"run_id": _ID}, ("run_id",),
          lambda s, a: s.run(a["run_id"])),
    _tool("get_run_events", "A run's treadle.run/1 events from index `after`; `next` is "
                            "the index to ask from next time.",
          {"run_id": _ID, "after": {"type": "integer", "minimum": 0}}, ("run_id",),
          lambda s, a: s.run_events(a["run_id"], a.get("after", 0))),
    _tool("get_report", "A finished run's report as JSON.", {"run_id": _ID}, ("run_id",),
          lambda s, a: s.report(a["run_id"])),
)
_BY_NAME = {t["name"]: t for t in TOOLS}


class McpServer:
    def __init__(self, service: Service):
        self.service = service

    def handle(self, message: Any) -> Optional[dict]:
        """One incoming message -> the response to send, or None for a notification."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _failure(None, -32600, "Invalid request")
        msg_id, method = message.get("id"), message.get("method")
        if "id" not in message:
            return None                    # a notification, such as initialized
        if not isinstance(method, str):
            return _failure(msg_id, -32600, "Invalid request")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return _failure(msg_id, -32602, "params must be an object")
        if method == "initialize":
            asked = params.get("protocolVersion")
            version = asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
            return _success(msg_id, {"protocolVersion": version,
                                     "capabilities": {"tools": {"listChanged": False}},
                                     "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS})
        if method == "ping":
            return _success(msg_id, {})
        if method == "tools/list":
            return _success(msg_id, {"tools": [{k: v for k, v in t.items() if k != "call"}
                                               for t in TOOLS]})
        if method == "tools/call":
            return self._call(msg_id, params)
        return _failure(msg_id, -32601, f"Method not found: {method}")

    def _call(self, msg_id: Any, params: dict) -> dict:
        tool = _BY_NAME.get(params.get("name"))
        if tool is None:
            return _failure(msg_id, -32602, f"Unknown tool: {params.get('name')}")
        args = params.get("arguments") or {}
        try:
            if not isinstance(args, dict):
                raise ServiceError("bad_request", "arguments must be an object")
            schema = tool["inputSchema"]
            extra = sorted(set(args) - set(schema["properties"]))
            missing = [n for n in schema["required"] if args.get(n) is None]
            if extra or missing:
                raise ServiceError("bad_request", "; ".join(
                    ([f"unexpected: {', '.join(extra)}"] if extra else [])
                    + ([f"required: {', '.join(missing)}"] if missing else [])))
            return _success(msg_id, _content(tool["call"](self.service, args), False))
        except ServiceError as exc:
            return _success(msg_id, _content(exc.as_dict(), True))
        except Exception:                                          # noqa: BLE001
            return _success(msg_id, _content(
                {"error": {"code": "internal", "message": "Internal error.", "detail": {}}},
                True))

    def serve(self, reader: TextIO, writer: TextIO) -> None:
        for line in reader:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except ValueError:
                response = _failure(None, -32700, "Parse error")
            else:
                response = self.handle(message)
            if response is not None:
                writer.write(json.dumps(response, ensure_ascii=False) + "\n")
                writer.flush()


def serve_stdio(service: Service) -> None:
    protocol_out = sys.stdout
    sys.stdout = sys.stderr
    try:
        McpServer(service).serve(sys.stdin, protocol_out)
    finally:
        sys.stdout = protocol_out


def _content(payload: dict, is_error: bool) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "structuredContent": payload, "isError": is_error}


def _success(msg_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _failure(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
