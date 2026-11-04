"""scripts/treadle_standalone_check.py — does Treadle actually run on its own?

Phase 2's finish line, measured rather than asserted. In a brand-new virtualenv outside
the repo:

  1. `pip install <repo>` installs treadle and nothing of Castor's.
  2. A report type defined OUTSIDE the repo (a copy of the travel guide, renamed so it
     cannot be confused with the repo's pack) registers and runs through run_report.
  3. Every treadle submodule imports.
  4. No Castor module is loaded at any point, and the repo is not on sys.path.

    .venv/bin/python scripts/treadle_standalone_check.py [--keep]

Needs network access for pip to fetch the four small dependencies. Exits non-zero, naming
the step, on any failure.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Castor modules that must never appear in a standalone process.
CASTOR = ("plan", "four_ps", "discover", "intake", "intake_tree", "orchestrator", "api",
          "billing", "auth", "quota", "tools", "skills", "agents", "gates", "report",
          "routes", "packs", "config", "paths", "charts", "market_sizing")

PROBE = textwrap.dedent('''
    import importlib, pkgutil, sys
    assert not any("castor-advisories" in p for p in sys.path), sys.path
    import treadle
    assert "castor-advisories" not in treadle.__file__, treadle.__file__
    treadle.load_pack("guide_pack")
    report = treadle.run_report("standalone_guide",
                                {"city": "Porto", "days": 2, "interests": ["food"],
                                 "diet": "none", "currency": "EUR"})
    assert report.ok, report.sections
    statuses = {s["key"]: s["status"] for s in report.sections["sections"]}
    assert set(statuses.values()) == {"ok"}, statuses
    for m in pkgutil.walk_packages(treadle.__path__, "treadle."):
        importlib.import_module(m.name)
    leaked = sorted(m for m in sys.modules if m.split(".")[0] in CASTOR)
    assert not leaked, f"Castor modules loaded: {leaked}"
    print("sections:", statuses)
    print("modules:", len([m for m in sys.modules if m.startswith("treadle")]))
''')


def _run(cmd: list[str], cwd: pathlib.Path, step: str) -> str:
    out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        print(f"FAILED at {step}\n{out.stdout[-3000:]}\n{out.stderr[-3000:]}")
        raise SystemExit(1)
    return out.stdout


def main() -> int:
    keep = "--keep" in sys.argv
    work = pathlib.Path(tempfile.mkdtemp(prefix="treadle-standalone-"))
    try:
        _run([sys.executable, "-m", "venv", str(work / "venv")], work, "create venv")
        py = work / "venv" / "bin" / "python"
        # Build from a copy holding only what the package is made of. Installing from the
        # repo itself leaves build/ and treadle.egg-info/ behind in the working tree.
        src = work / "src"
        shutil.copytree(ROOT / "treadle", src / "treadle",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(ROOT / "pyproject.toml", src / "pyproject.toml")
        _run([str(py), "-m", "pip", "install", "-q", str(src)], work, "pip install")

        pack = (ROOT / "packs" / "travel_guide" / "__init__.py").read_text()
        pack = pack.replace('key="travel_guide"', 'key="standalone_guide"')
        (work / "guide_pack.py").write_text(pack)

        probe = f"CASTOR = {CASTOR!r}\n" + PROBE
        print(_run([str(py), "-I", "-c", probe.replace(
            "import importlib, pkgutil, sys",
            "import importlib, pkgutil, sys; sys.path.insert(0, '.')")], work, "probe"))
        print(f"standalone: ok ({work})" if keep else "standalone: ok")
        return 0
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
