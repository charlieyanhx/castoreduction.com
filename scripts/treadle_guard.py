"""scripts/treadle_guard.py — Treadle may touch the frame and nothing else.

The rule the project runs under: carve the engine out, leave the product alone. The golden
baseline (scripts/treadle_baseline.py) catches a product change by its OUTPUT. This catches
it by its FILES, which is the cheaper and earlier signal: a diff that edits plan.py has
broken the rule even if every render happens to come out the same.

Everything changed since the Treadle base commit must be one of:
  * a frame module Treadle is allowed to move or edit (FRAME below), or
  * a new Treadle path (treadle/, packs/, test_treadle_*, scripts/treadle_*, ...).

FRAME IS NARROWER THAN THE FRAME TEST'S LIST, ON PURPOSE. test_the_frame_does_not_import_
the_soul.py counts billing, auth, quota, feedback, history, iteration, the routes, api and
cli as frame because they import no market research. They are still Castor the PRODUCT:
what a paying founder logs into, is charged by, and clicks. Treadle gets its own entry
points instead of rewiring those.

The base commit lives in docs/treadle/BASE. Delete that file when Treadle is done and the
guard (and its test) stand down, so ordinary product work is not blocked by it.

    .venv/bin/python scripts/treadle_guard.py              # committed changes only
    .venv/bin/python scripts/treadle_guard.py --worktree   # plus staged and unstaged
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE_FILE = ROOT / "docs" / "treadle" / "BASE"

FRAME_FILES = frozenset({
    "llm.py", "logger.py", "cache.py", "net.py", "errors.py", "jobs.py", "url_guard.py",
    "provenance.py", "paths.py", "rendering.py",
})
FRAME_PREFIXES = (
    "core/", "capabilities/", "harness/", "persistence/", "entry/", "context/", "model/",
    "tools/registry", "skills/registry", "agents/registry",
    "report/citation", "report/verifier", "report/trace", "report/section_provenance",
    "report/render_html", "report/render_md", "report/render_synthesis",
    "gates/common", "gates/runner",
)
TREADLE_PREFIXES = (
    "treadle/", "packs/", "test_treadle_", "scripts/treadle_", "docs/treadle/",
    "tests/treadle/",
)


def allowed(path: str) -> bool:
    return (path in FRAME_FILES
            or path.startswith(FRAME_PREFIXES)
            or path.startswith(TREADLE_PREFIXES))


def base() -> str | None:
    return BASE_FILE.read_text().strip() if BASE_FILE.exists() else None


def _git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                         check=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def changed(since: str, worktree: bool = False) -> set[str]:
    """Every path touched since `since`. A rename counts as BOTH its paths: moving a
    product file into treadle/ removes it from the product, which is a product change."""
    paths: set[str] = set()
    ranges = [[f"{since}..HEAD"]]
    if worktree:
        ranges += [["--cached"], []]
    for extra in ranges:
        for line in _git("diff", "--name-status", "-M", *extra):
            paths.update(line.split("\t")[1:])
    return paths


def violations(paths: set[str]) -> list[str]:
    return sorted(p for p in paths if not allowed(p))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worktree", action="store_true")
    args = ap.parse_args()
    since = base()
    if since is None:
        print("no docs/treadle/BASE: the guard is off")
        return 0
    bad = violations(changed(since, worktree=args.worktree))
    for p in bad:
        print(f"product file touched: {p}")
    print(f"{len(bad)} product files touched since {since[:8]}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
