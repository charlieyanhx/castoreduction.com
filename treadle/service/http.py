"""treadle/service/http.py: the service as JSON over HTTP, on this machine only.

    GET  /health                       GET  /types            GET /types/{key}
    POST /surveys                      {"report_type", "brief"?}
    GET  /surveys/{id}
    POST /surveys/{id}/answers         {"answers", "reply_to"?}
    POST /runs                         {"report_type"?, one of "brief" | "answers" | "survey_id"}
                                       Idempotency-Key: <32 hex>, optional
    GET  /runs/{id}                    GET /runs/{id}/events?after=N
    GET  /runs/{id}/report.json        GET /runs/{id}/report   (the pack's HTML)

A RUN SPENDS MONEY, SO THE SERVER TRUSTS ONLY THIS MACHINE. It binds to loopback, refuses
a Host header naming anything else (a DNS-rebinding page names its own host), and takes a
POST only as JSON with the X-Treadle-Run header, which a browser cannot send cross-site
without a preflight this server never answers. Refusals carry treadle.service error codes.
Standard library only: the engine installs without a web framework.
"""
from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlsplit

from .core import Service
from .errors import ServiceError

MAX_BODY_BYTES = 65_536
LOOPBACK = ("127.0.0.1", "localhost")
_ID = r"([0-9a-f]{32})"
_KEY = r"([a-z][a-z0-9_]*)"
#: ASCII digits only: str.isdigit also takes "²", which int() then refuses.
_DIGITS = re.compile(r"[0-9]{1,12}")
SECURITY_HEADERS = {
    "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
}
#: A rendered report is the pack's own page: it may style itself, and still loads nothing.
REPORT_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
              "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


def _routes(service: Service) -> list[tuple[str, re.Pattern, tuple[str, ...], Callable]]:
    """(method, path, the body fields it takes, handler(match, body, query, headers))."""
    s = service

    def run_events(m, _b, q, _h):
        raw = q.get("after", ["0"])[-1]
        if not _DIGITS.fullmatch(raw):
            raise ServiceError("bad_request", "after is a non-negative event index")
        return s.run_events(m[1], int(raw))

    table = [
        ("GET", r"/health", (), lambda *_: {"ok": True, **s.report_types()}),
        ("GET", r"/types", (), lambda *_: s.report_types()),
        ("GET", rf"/types/{_KEY}", (), lambda m, *_: s.describe(m[1])),
        ("POST", r"/surveys", ("report_type", "brief"),
         lambda m, b, *_: (201, s.start_survey(_need(b, "report_type"), b.get("brief")))),
        ("GET", rf"/surveys/{_ID}", (), lambda m, *_: s.survey(m[1])),
        ("POST", rf"/surveys/{_ID}/answers", ("answers", "reply_to"),
         lambda m, b, *_: s.answer_survey(m[1], _need(b, "answers"), b.get("reply_to"))),
        ("POST", r"/runs", ("report_type", "brief", "answers", "survey_id"),
         lambda m, b, q, h: (202, s.start_run(
             report_type=b.get("report_type"), brief=b.get("brief"),
             answers=b.get("answers"), survey_id=b.get("survey_id"),
             run_id=h.get("Idempotency-Key")))),
        ("GET", rf"/runs/{_ID}", (), lambda m, *_: s.run(m[1])),
        ("GET", rf"/runs/{_ID}/events", (), run_events),
        ("GET", rf"/runs/{_ID}/report\.json", (), lambda m, *_: s.report(m[1])),
        ("GET", rf"/runs/{_ID}/report", (), lambda m, *_: _Html(s.report_html(m[1]))),
    ]
    return [(method, re.compile(path), fields, fn) for method, path, fields, fn in table]


class _Html(str):
    """A handler's HTML answer, as opposed to its JSON one."""


def _need(body: dict, name: str) -> Any:
    if body.get(name) is None:
        raise ServiceError("bad_request", f"{name} is required")
    return body[name]


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


class _Handler(BaseHTTPRequestHandler):
    server_version = "treadle"
    sys_version = ""
    routes: list = []
    allowed_hosts: frozenset = frozenset()

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def log_message(self, *_args):
        """Quiet: a request line can carry an id, and the operator did not ask for it."""

    def _dispatch(self, method: str) -> None:
        try:
            refusal = self._boundary(method)
            if refusal is not None:
                return self._send(refusal[0], _error(refusal[1], refusal[2]))
            url = urlsplit(self.path)
            matched = [(m, fields, fn) for verb, pattern, fields, fn in self.routes
                       for m in [pattern.fullmatch(url.path)] if m and verb == method]
            if not matched:
                allowed = any(p.fullmatch(url.path) for _v, p, _f, _fn in self.routes)
                return self._send(405 if allowed else 404, _error(
                    "bad_request" if allowed else "not_found",
                    "Method not allowed." if allowed else "No such route."))
            match, fields, fn = matched[0]
            body = self._body(fields) if method == "POST" else {}
            answer = fn(match, body, parse_qs(url.query), self.headers)
            status, payload = answer if isinstance(answer, tuple) else (200, answer)
            self._send(status, payload)
        except ServiceError as exc:
            self._send(exc.http_status, exc.as_dict())
        except Exception:                                          # noqa: BLE001
            self._send(500, _error("internal", "Internal error."))

    def _boundary(self, method: str) -> Optional[tuple[int, str, str]]:
        host = self.headers.get("Host", "")
        if host not in self.allowed_hosts:
            return 403, "forbidden", "Local host required."
        if method != "POST":
            return None
        origin = self.headers.get("Origin")
        if origin is not None and origin != "http://" + host:
            return 403, "forbidden", "Same origin required."
        if (self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none")
                or self.headers.get("X-Treadle-Run") != "1"):
            return 403, "forbidden", "The X-Treadle-Run: 1 header is required."
        media = self.headers.get("Content-Type", "").split(";")[0].strip()
        if media != "application/json":
            return 415, "bad_request", "JSON content required."
        return None

    def _body(self, fields: tuple[str, ...]) -> dict:
        length = self.headers.get("Content-Length")
        if length is None or not _DIGITS.fullmatch(length):
            raise ServiceError("bad_request", "Content-Length is required.")
        if int(length) > MAX_BODY_BYTES:
            raise ServiceError("too_large", f"The body exceeds {MAX_BODY_BYTES // 1024} KiB.")
        raw = self.rfile.read(int(length))
        try:
            body = json.loads(raw, parse_constant=_reject_constant)
        except (ValueError, RecursionError, UnicodeError):
            raise ServiceError("bad_request", "The body is not valid JSON.") from None
        if not isinstance(body, dict):
            raise ServiceError("bad_request", "The body is a JSON object.")
        extra = sorted(set(body) - set(fields))
        if extra:
            raise ServiceError("bad_request", f"Unexpected field(s): {', '.join(extra)}.",
                               detail={"allowed": list(fields)})
        return body

    def _send(self, status: int, payload: Any) -> None:
        html = isinstance(payload, _Html)
        data = (str(payload) if html else json.dumps(payload, ensure_ascii=False,
                                                     allow_nan=False)).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8" if html
                         else "application/json")
        self.send_header("Content-Length", str(len(data)))
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, REPORT_CSP if html and name == "Content-Security-Policy"
                             else value)
        self.end_headers()
        self.wfile.write(data)


def _error(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message, "detail": {}}}


def make_server(service: Service, *, host: str = "127.0.0.1", port: int = 8780
                ) -> ThreadingHTTPServer:
    """A server bound to loopback; port 0 picks a free one (server.server_address)."""
    if host not in LOOPBACK:
        raise ValueError("the Treadle service binds to loopback only")
    handler = type("TreadleHandler", (_Handler,), {"routes": _routes(service)})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    bound = server.server_address[1]
    handler.allowed_hosts = frozenset({f"127.0.0.1:{bound}", f"localhost:{bound}"})
    return server
