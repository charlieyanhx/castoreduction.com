"""treadle/service/cli.py: the service from a shell.

    python -m treadle --pack travel_guide types
    python -m treadle --pack travel_guide survey travel_guide
    python -m treadle --pack travel_guide answer <survey_id> --answers answers.json
    python -m treadle --pack travel_guide run travel_guide --survey <survey_id> --out out/lisbon
    python -m treadle --pack market_research_next run market_research_next --answers form.json
    python -m treadle --pack travel_guide http --port 8780
    python -m treadle --pack travel_guide mcp

Every command prints JSON. Surveys and runs live under --out-dir (or TREADLE_OUT_DIR), so
a survey started by one command is answered by the next. `run` waits for the report and
exits 0 when it is ok, 1 when it is incomplete or failed, 2 when it was refused.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from treadle.registry import load_pack

from .core import Service
from .errors import ServiceError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="treadle", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pack", action="append", default=[],
                        help="load a pack by name or dotted module path (repeatable)")
    parser.add_argument("--out-dir", type=Path,
                        default=Path(os.environ.get("TREADLE_OUT_DIR") or "treadle-out"),
                        help="where surveys and runs are kept (default: $TREADLE_OUT_DIR "
                             "or ./treadle-out)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("types", help="list the loaded report types")
    describe = commands.add_parser("describe", help="describe one report type")
    describe.add_argument("report_type")
    survey = commands.add_parser("survey", help="start a survey (or show a type's form)")
    survey.add_argument("report_type")
    survey.add_argument("--brief", type=Path, help="JSON brief answering some inputs")
    show = commands.add_parser("show", help="a survey's status and questions")
    show.add_argument("survey_id")
    answer = commands.add_parser("answer", help="answer survey questions")
    answer.add_argument("survey_id")
    answer.add_argument("--answers", type=Path, required=True, help="JSON {field: answer}")
    answer.add_argument("--reply-to", type=Path, help="JSON {field: question id}")
    run = commands.add_parser("run", help="run a report and wait for it")
    run.add_argument("report_type", nargs="?")
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument("--brief", type=Path, help="JSON brief")
    source.add_argument("--answers", type=Path, help="JSON answers to the type's form")
    source.add_argument("--survey", help="id of a ready survey")
    run.add_argument("--run-id", help="32 hex characters; the same id never runs twice")
    run.add_argument("--out", type=Path, help="also write <out>.json and <out>.html")
    http = commands.add_parser("http", help="serve JSON over HTTP on loopback")
    http.add_argument("--host", default="127.0.0.1")
    http.add_argument("--port", type=int, default=8780)
    http.add_argument("--max-running", type=int, default=1)
    commands.add_parser("mcp", help="serve MCP on stdio")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    for pack in args.pack:
        load_pack(pack)
    service = Service(args.out_dir, max_running=getattr(args, "max_running", 1))
    try:
        if args.command == "http":
            return _serve_http(service, args.host, args.port)
        if args.command == "mcp":
            from .mcp import serve_stdio
            serve_stdio(service)
            return 0
        if args.command == "run":
            return _run(service, args)
        _print(_command(service, args))
        return 0
    except ServiceError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": {"code": "bad_request", "message": str(exc),
                                    "detail": {}}}), file=sys.stderr)
        return 2
    finally:
        service.close()


def _command(service: Service, args: argparse.Namespace) -> dict:
    if args.command == "types":
        return service.report_types()
    if args.command == "describe":
        return service.describe(args.report_type)
    if args.command == "survey":
        return service.start_survey(args.report_type, _json(args.brief))
    if args.command == "show":
        return service.survey(args.survey_id)
    return service.answer_survey(args.survey_id, _json(args.answers), _json(args.reply_to))


def _run(service: Service, args: argparse.Namespace) -> int:
    started = service.start_run(report_type=args.report_type, brief=_json(args.brief),
                                answers=_json(args.answers), survey_id=args.survey,
                                run_id=args.run_id)
    status = service.runs.wait(started["run_id"])
    if args.out is not None and status["status"] in ("completed", "incomplete"):
        args.out.parent.mkdir(parents=True, exist_ok=True)
        Path(f"{args.out}.json").write_text(json.dumps(service.report(started["run_id"]),
                                                       ensure_ascii=False, indent=2),
                                            encoding="utf-8")
        if status.get("html"):
            Path(f"{args.out}.html").write_text(service.report_html(started["run_id"]),
                                                encoding="utf-8")
    _print(status)
    return 0 if status.get("ok") else 1


def _serve_http(service: Service, host: str, port: int) -> int:
    from .http import make_server

    server = make_server(service, host=host, port=port)
    print(json.dumps({"listening": f"http://{host}:{server.server_address[1]}"}),
          file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _json(path: Optional[Path]):
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _print(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))
