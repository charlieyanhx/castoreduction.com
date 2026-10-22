"""Run the selectable PREP workflow through the Treadle service; save JSON, HTML, optionally PDF.

The pack-agnostic command line is `python -m treadle` (treadle/service/cli.py); this
script keeps the market-research tuning flags.

    .venv/bin/python scripts/treadle_market_research.py --brief venture.json --out out/venture --pdf

Live execution uses configured model and research providers. --describe makes no calls.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brief", type=Path, help="JSON with description and survey/intake answers")
    parser.add_argument("--out", type=Path, help="output file prefix (without extension)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--correction-rounds", type=int, choices=(0, 1, 2), default=2)
    parser.add_argument("--run-timeout", type=float, default=900,
                        help="maximum workflow seconds, including corrections")
    parser.add_argument("--task-timeout", type=float, default=300,
                        help="maximum seconds per task attempt, including recursive PREP")
    parser.add_argument("--review-timeout", type=float, default=90,
                        help="maximum seconds per consistency review")
    parser.add_argument("--model-calls", type=int, default=120,
                        help="model-attempt budget shared across the whole run")
    parser.add_argument("--tool-calls", type=int, default=60,
                        help="agent tool-dispatch budget shared across the whole run")
    parser.add_argument("--task-model-calls", type=int, default=12)
    parser.add_argument("--task-tool-calls", type=int, default=8)
    parser.add_argument("--review-model-calls", type=int, default=3)
    parser.add_argument("--business-calls", type=int, choices=range(2, 11), default=6,
                        help="recursive object model calls, including its support review")
    parser.add_argument("--business-depth", type=int, choices=range(1, 13), default=6,
                        help="maximum recursive business graph depth")
    parser.add_argument("--pdf", action="store_true", help="also render PDF using the existing exporter")
    parser.add_argument("--describe", action="store_true", help="print the workflow without model calls")
    parser.add_argument("--runs-dir", type=Path,
                        help="where the service keeps the run (default: <out dir>/treadle-runs)")
    args = parser.parse_args(argv)
    from treadle.execution_limits import ExecutionLimits
    from packs.market_research_next import DEPENDENCIES, REPORT_ORDER, build_report_type

    try:
        limits = ExecutionLimits(
            run_timeout_s=args.run_timeout, task_timeout_s=args.task_timeout,
            review_timeout_s=args.review_timeout, max_model_calls=args.model_calls,
            max_tool_calls=args.tool_calls, task_model_calls=args.task_model_calls,
            task_tool_calls=args.task_tool_calls, review_model_calls=args.review_model_calls,
        )
        if args.workers < 1:
            raise ValueError("--workers must be positive")
    except (ValueError, TypeError) as exc:
        parser.error(str(exc))
    if args.describe:
        print(json.dumps({"report_order": REPORT_ORDER, "research_dependencies": DEPENDENCIES,
                          "all_research_requires": "prep", "summary_waits_for": REPORT_ORDER[1:],
                          "max_correction_rounds": args.correction_rounds,
                          "workers": args.workers, "execution_limits": asdict(limits),
                          "execution_mode": "isolated_processes",
                          "preparation": {"schema_version": "market_research.prep/2",
                                          "business_object_enabled": True,
                                          "max_calls": args.business_calls,
                                          "max_depth": args.business_depth},
                          "source_tracing_enabled": False}, indent=2))
        return 0
    if args.brief is None or args.out is None:
        parser.error("--brief and --out are required unless --describe is used")
    from treadle import register
    from treadle.service import Service, ServiceError

    try:
        brief = json.loads(args.brief.read_text(encoding="utf-8"))
        if not isinstance(brief, dict):
            raise ValueError("brief must be a JSON object")
        # A separate key avoids mutating the default pack's shared configuration.
        rt = build_report_type(key="market_research_selected", max_workers=args.workers,
                               max_correction_rounds=args.correction_rounds, limits=limits,
                               business_max_calls=args.business_calls,
                               business_max_depth=args.business_depth)
        register(rt)
        # The run goes through the Treadle service, so it is recorded like any other:
        # status, treadle.run/1 events and the report under --runs-dir.
        service = Service(args.runs_dir or args.out.parent / "treadle-runs")
        try:
            run_id = service.start_run(report_type=rt.key, brief=brief)["run_id"]
            status = service.runs.wait(run_id)
            if status["status"] not in ("completed", "incomplete"):
                raise RuntimeError(f"the run failed ({status.get('error_type')}); "
                                   f"see run {run_id}")
            args.out.parent.mkdir(parents=True, exist_ok=True)
            prefix = str(args.out)
            Path(prefix + ".json").write_text(json.dumps(service.report(run_id),
                                                         ensure_ascii=False,
                                                         allow_nan=False, indent=2),
                                               encoding="utf-8")
            html = service.report_html(run_id)
        finally:
            service.close()
        Path(prefix + ".html").write_text(html, encoding="utf-8")
        if args.pdf:
            from report.pdf import render_pdf

            Path(prefix + ".pdf").write_bytes(render_pdf(html, meta={"title": "Venture research report"}))
        print(json.dumps({"ok": status["ok"], "output_prefix": prefix, "run_id": run_id,
                          "correction_rounds": status.get("correction_rounds"),
                          "stop_reason": status.get("stop_reason"),
                          "usage": status.get("usage")}))
        return 0 if status["ok"] else 1
    except ServiceError as exc:
        print(f"Treadle: {exc}", file=sys.stderr)
        return 2
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"Treadle: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
