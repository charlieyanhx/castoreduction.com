"""bench/__main__.py -- the command line.

    ./bench.sh                                  what you can run
    ./bench.sh hackernews_mentions              one capability, on its own
    ./bench.sh reddit_mentions query="pour over" limit=3
    ./bench.sh smoke                            all 63, about 80s, no tokens
    ./bench.sh smoke --kind tool                just the tool surface
    ./bench.sh list --match census              what is registered
    ./bench.sh doctor                           what bench cannot call

`smoke` is the one to reach for after a change: it exercises everything the filters
select, prints one line each, and diffs the verdicts against the previous run. It exits
non-zero only when something in the CODE is wrong -- error, refused, no-fixture,
timeout. A tool that found nothing today exits zero and says `empty`, because that is
what happened, and a bench that failed the build over it would be turned off inside a
week.

Environment. This loads .env like the pipeline does, so tools reach the same APIs with
the same keys. `--llm off` is the mode that needs no credentials at all.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

import dotenv  # noqa: E402

dotenv.load_dotenv(PROJ / ".env")

from bench import report                                      # noqa: E402
from bench.capability import KINDS, by_name, discover, select  # noqa: E402
from bench.fixtures import args_for                            # noqa: E402
from bench.keys import KeyRefused, supplied_key                 # noqa: E402
from bench.llm_gate import MODES, llm_mode                     # noqa: E402
from bench.prose import render as render_prose                 # noqa: E402
from bench.runner import DEFAULT_TIMEOUT_S, run_many, run_one  # noqa: E402


def _parse_value(raw: str):
    """`limit=3` is an int, `query=pour over` is a string, `estimates=[...]` is JSON.

    JSON first, bare string as the fallback -- so structured fixtures can be overridden
    from the shell without a second flag, and ordinary prose still needs no quoting
    beyond what the shell already wants.
    """
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _kv(pairs: list[str]) -> dict:
    """`k=v k=v` from the command line into kwargs."""
    out = {}
    for p in pairs:
        if "=" not in p:
            raise SystemExit(f"arguments are k=v; {p!r} has no '='")
        k, v = p.split("=", 1)
        out[k] = _parse_value(v)
    return out


def cmd_list(args, caps) -> int:
    """What is registered, with the first line of each docstring."""
    chosen = select(caps, args.kind, args.label, args.match)
    for c in chosen:
        print(f"  {c.kind:5} {c.name:34} {c.label:22} {c.tier:7} {c.headline[:70]}")
    print(f"\n{len(chosen)} of {len(caps)} capabilities")
    return 0


def cmd_doctor(args, caps) -> int:
    """Which capabilities bench cannot call, and exactly which parameter is missing.

    This is the maintenance surface for bench/fixtures.py. It calls nothing.
    """
    gaps = []
    for c in caps:
        _, missing = args_for(c.name, c.fn)
        if missing:
            gaps.append((c, missing))
    for c, missing in gaps:
        print(f"  {c.kind:5} {c.name:34} needs a fixture for: {', '.join(missing)}")
    covered = len(caps) - len(gaps)
    print(f"\n{covered}/{len(caps)} capabilities are callable from fixtures alone")
    if gaps:
        print("Add the parameter name to BY_PARAM in bench/fixtures.py, or the "
              "capability to OVERRIDES.")
    return 1 if gaps else 0


def _prompt_parameter(cap) -> str | None:
    """The parameter a free-text prompt belongs in: the first required string.

    For every agent that is `description`, for local_market_agent it is `address`. Which
    one it picked is PRINTED rather than assumed, because a --prompt that silently went
    into the wrong slot would look like the agent ignoring you.
    """
    import inspect
    raw = getattr(cap.fn, "__wrapped_fn__", None) or cap.fn
    try:
        sig = inspect.signature(raw, eval_str=True)
    except (TypeError, ValueError, NameError):
        sig = inspect.signature(raw)
    for name, p in sig.parameters.items():
        if p.default is inspect.Parameter.empty and p.annotation in (str, "str"):
            return name
    return None


def cmd_call(args, caps) -> int:
    """One capability, with whatever arguments you gave, and what it wrote."""
    cap = by_name(caps, args.name)
    if cap is None:
        print(f"no capability named {args.name!r}. Try: ./bench.sh list --match "
              f"{args.name[:6]}")
        return 2

    extra = _kv(args.args)
    if args.prompt:
        slot = _prompt_parameter(cap)
        if slot is None:
            print(f"{cap.name} takes no free-text parameter for --prompt to fill; "
                  f"pass it by name instead, e.g. {cap.signature}")
            return 2
        extra.setdefault(slot, args.prompt)
        print(f"(--prompt went to {slot})")

    with supplied_key(args.key, args.allow_paid) as using:
        if using:
            print(f"(model key: {using}, backend pinned)")
        with llm_mode(args.llm):
            result = run_one(cap, extra=extra)

    print(f"\n{cap.kind} {cap.name}  ({cap.label}, {cap.tier} tier)")
    print(f"  signature {cap.signature}")
    print(f"  called    {json.dumps(result.args, default=str)}")
    print(f"  verdict   {result.verdict}   count={result.count}  "
          f"{result.duration_s:.2f}s")
    print(f"  payload   {result.shape}")
    if result.llm_calls:
        print(f"  llm       {result.llm_calls} calls, {result.llm_tokens} tokens, "
              f"${result.llm_usd:.4f}")
    if result.spend_usd:
        print(f"  metered   ${result.spend_usd:.4f}")
    if result.note:
        print(f"  note      {result.note}")
    if result.error:
        print(f"  error     {result.error[:1500]}")

    written = render_prose(result.payload, limit=None if args.full else 1200)
    if written:
        print(f"\n{'-' * 72}")
        print(written)
        print("-" * 72)
    if args.full and not written:
        print(f"  full      {json.dumps(result.payload, indent=2, default=str)[:20000]}")
    return 1 if result.failed else 0


def cmd_smoke(args, caps) -> int:
    """Exercise the selected surface, print a line each, diff against the last run."""
    chosen = select(caps, args.kind, args.label, args.match)
    if not chosen:
        print("nothing selected")
        return 2

    print(f"exercising {len(chosen)} capabilities  "
          f"(llm={args.llm}, jobs={args.jobs}, "
          f"{'metered included' if args.metered else 'metered skipped'}"
          f"{', heavy included' if args.heavy else ''}"
          f"{', http cache off' if args.fresh_http else ''})\n")

    def emit(r):
        print(report.line(r), flush=True)

    with supplied_key(args.key, args.allow_paid) as using:
      if using:
        print(f"(model key: {using}, backend pinned)\n")
      with llm_mode(args.llm):
        with _http(args.fresh_http):
            results = run_many(chosen, jobs=args.jobs, timeout_s=args.timeout,
                               skip_metered=not args.metered,
                               skip_heavy=not args.heavy, recheck=args.recheck,
                               on_result=emit)

    redone = [r for r in results if r.rechecked]
    if redone:
        print("\nrechecked alone, because a wide sweep can throttle a source:")
        for r in redone:
            print(f"  {report.MARK.get(r.verdict, r.verdict):5} {r.name:34} {r.note}")

    previous = report.load()
    moved = report.changes(results, previous)
    if moved:
        print("\nsince the last run:")
        print("\n".join(moved))

    print(f"\n{report.summary(results)}")
    print(f"cost: {report.spend(results)}")
    saved = report.save(results, meta={"llm": args.llm, "kind": args.kind,
                                       "label": args.label, "match": args.match,
                                       "metered": args.metered})
    print(f"saved: {saved.relative_to(PROJ)}")
    if args.json:
        report.save(results, Path(args.json))
        print(f"wrote: {args.json}")

    failures = [r for r in results if r.failed]
    return 1 if failures else 0


class _http:
    """Optionally run with requests-cache switched off.

    scrape/__init__.py installs the cache at import, so a bench run reads sqlite by
    default and a sweep costs almost no requests. That is usually what you want. It is
    exactly what you do not want when the question is whether a SOURCE broke rather than
    whether the parser did, which is what --fresh-http is for.
    """

    def __init__(self, fresh: bool) -> None:
        self.fresh = fresh
        self.ctx = None

    def __enter__(self):
        if not self.fresh:
            return self
        import requests_cache
        self.ctx = requests_cache.disabled()
        self.ctx.__enter__()
        return self

    def __exit__(self, *exc):
        if self.ctx is not None:
            self.ctx.__exit__(*exc)
        return False


def _key_flags(p: argparse.ArgumentParser) -> None:
    """Supplying a model key is the same decision for `call` and for `smoke`."""
    p.add_argument("--key", metavar="PROVIDER[=VALUE]",
                   help="run against your own key: `--key gemini` asks for it without "
                        "echoing (nothing lands in shell history), `--key gemini=VALUE` "
                        "takes it inline. Pins the backend to that provider.")
    p.add_argument("--allow-paid", action="store_true",
                   help="required before a key for a billing provider is used")


def _filters(p: argparse.ArgumentParser) -> None:
    p.add_argument("--kind", choices=KINDS, help="tool, skill or agent")
    p.add_argument("--label", help="a tool category, or a skill/agent 'produces'")
    p.add_argument("--match", help="substring of the capability name")


SUBCOMMANDS = ("list", "doctor", "call", "smoke")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="bench", description="Exercise one registered capability, or all of them, "
                                  "without building a report.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="what is registered")
    _filters(pl)

    pd = sub.add_parser("doctor", help="which capabilities have no fixture arguments")

    pc = sub.add_parser("call", help="run one capability with explicit arguments")
    pc.add_argument("name")
    pc.add_argument("args", nargs="*", help="k=v pairs; values parse as JSON when they can")
    pc.add_argument("--llm", choices=MODES, default="real",
                    help="one deliberate call defaults to the real model")
    pc.add_argument("--prompt", help="free text for the capability's own first "
                                     "argument, so you do not have to know its name")
    pc.add_argument("--full", action="store_true",
                    help="print the whole payload, not a readable excerpt")
    _key_flags(pc)

    ps = sub.add_parser("smoke", help="exercise the selected capabilities")
    _filters(ps)
    ps.add_argument("--llm", choices=MODES, default="cached",
                    help="cached (default) serves the model from .cache.sqlite and "
                         "reports a miss as a miss")
    ps.add_argument("--metered", action="store_true",
                    help="include tools that cost money per call")
    ps.add_argument("--heavy", action="store_true",
                    help="include the whole-report capabilities (run_pipeline_skill, "
                         "run_research_crew) that a bench exists to avoid")
    ps.add_argument("--jobs", type=int, default=6,
                    help="parallel workers for parallel-safe tools (default 6)")
    ps.add_argument("--no-recheck", dest="recheck", action="store_false",
                    help="do not retry a non-clean result serially before reporting it")
    ps.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                    help=f"per-capability deadline in seconds (default {DEFAULT_TIMEOUT_S:.0f})")
    ps.add_argument("--fresh-http", action="store_true",
                    help="bypass the 24h requests-cache and hit the real sources")
    ps.add_argument("--json", help="also write the run to this path")
    _key_flags(ps)

    argv = sys.argv[1:]
    if not argv:
        # No arguments is a question, not a mistake. Answer it with the three lines
        # somebody actually needs rather than argparse's "the following arguments are
        # required: cmd".
        print(__doc__.strip().split("\n\n")[1])
        print("\n  ./bench.sh <name>        one capability, on its own"
              "\n  ./bench.sh smoke         all of them"
              "\n  ./bench.sh list          what is registered"
              "\n  ./bench.sh --help        every flag\n")
        return 0
    # A bare capability name means `call`, because that is the command you reach for
    # most and typing the verb adds nothing: `./bench.sh reddit_mentions query=coffee`.
    if argv[0] not in SUBCOMMANDS and not argv[0].startswith("-"):
        argv = ["call"] + argv

    args = parser.parse_args(argv)
    caps = discover()

    if args.cmd == "list":
        return cmd_list(args, caps)
    if args.cmd == "doctor":
        return cmd_doctor(args, caps)
    try:
        if args.cmd == "call":
            return cmd_call(args, caps)
        return cmd_smoke(args, caps)
    except KeyRefused as e:
        print(f"key refused: {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
