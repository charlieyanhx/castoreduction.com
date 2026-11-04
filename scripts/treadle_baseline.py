"""scripts/treadle_baseline.py — the golden baseline Treadle's frame work is held to.

Treadle carves the report engine out of Castor without touching the product. "Without
touching" has to be measurable, so this records what the product produces TODAY from the
19-report corpus: the rendered HTML and the verifier's verdict for each, as sha256. Any
Treadle commit that changes either has changed the product, whatever its diff looks like.

Rendering and verification are pure functions of a stored result except for the clock:
the page and the verdict each stamp datetime.now(). With the clock pinned (FROZEN_NOW),
renders are byte-identical across processes and minutes, so the baseline needs no LLM,
no network, and no tolerance.

    .venv/bin/python scripts/treadle_baseline.py --write   # record (phase 0 only)
    .venv/bin/python scripts/treadle_baseline.py --check   # what every phase must pass

The corpus lives in out/wave4_corpus, which is gitignored: on a fresh clone there is
nothing to check and the companion test skips rather than passing vacuously.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CORPUS = ROOT / "out" / "wave4_corpus"
MANIFEST = ROOT / "tests" / "treadle" / "golden.json"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


#: The instant every baseline render believes it is. The page stamps "Generated: <minute>"
#: (report/render_html.py) and the verdict stamps verified_at (report/verifier.py), both
#: from datetime.now(). Unpinned, two renders a minute apart differ, and a baseline that
#: fails on the clock teaches people to ignore it. Pinned here, never in the product.
FROZEN_NOW = _dt.datetime(2026, 1, 1, 12, 0, 0)


class _FrozenDatetime(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN_NOW.replace(tzinfo=tz) if tz else FROZEN_NOW


@contextlib.contextmanager
def _frozen_clock():
    """Both readers resolve `datetime.datetime` at call time (render_html imports it inside
    the function, the verifier reaches it through the module), so swapping the module
    attribute for the duration reaches both without editing either."""
    real = _dt.datetime
    _dt.datetime = _FrozenDatetime
    try:
        yield
    finally:
        _dt.datetime = real


def fingerprint(result: dict) -> dict:
    """The two things a reader receives from a stored result: the page, and its verdict."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from report.render_html import render_report_html
    from report.verifier import verify_report

    # Each call gets its own copy: a renderer that mutated its input would otherwise make
    # the verdict depend on the render, and the baseline would hide exactly that defect.
    with _frozen_clock():
        html = render_report_html(json.loads(json.dumps(result)), job_id="golden")
        verdict = verify_report(html, json.loads(json.dumps(result)))
    return {"html": _sha(html),
            "verdict": _sha(json.dumps(verdict, sort_keys=True, default=str))}


def corpus() -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text())["result"]
            for p in sorted(CORPUS.glob("*.json"))}


def current() -> dict[str, dict]:
    return {name: fingerprint(result) for name, result in corpus().items()}


def drift(recorded: dict, now: dict) -> list[str]:
    """Every way `now` differs from `recorded`, one line each. Empty means unchanged."""
    out = []
    for name in sorted(set(recorded) | set(now)):
        if name not in now:
            out.append(f"{name}: in the baseline but not in the corpus")
        elif name not in recorded:
            out.append(f"{name}: in the corpus but not in the baseline")
        else:
            out += [f"{name}: {part} changed" for part in ("html", "verdict")
                    if recorded[name][part] != now[name][part]]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = ap.parse_args()

    if not CORPUS.is_dir() or not any(CORPUS.glob("*.json")):
        print(f"no corpus at {CORPUS}", file=sys.stderr)
        return 2
    now = current()
    if args.write:
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps(now, indent=2, sort_keys=True) + "\n")
        print(f"recorded {len(now)} reports to {MANIFEST.relative_to(ROOT)}")
        return 0
    problems = drift(json.loads(MANIFEST.read_text()), now)
    for line in problems:
        print(line)
    print(f"{len(now)} reports, {len(problems)} differences")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
