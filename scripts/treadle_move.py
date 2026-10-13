"""scripts/treadle_move.py — move one frame package into treadle/, leaving aliases behind.

The same four steps every time, so they are done the same way every time:

  1. `git mv pkg/*.py treadle/pkg/` so history follows the code.
  2. Inside the moved files, imports of any package already under treadle/ are rewritten
     to their treadle name, so the engine never reaches itself through an alias that a
     standalone install will not have.
  3. Each old module becomes a sys.modules alias of its new one (see core/section.py for
     why an alias and not a copy).
  4. The old package __init__ stays a real package, re-exporting the new one's public
     names (see core/__init__.py for why the package itself is not aliased).

    .venv/bin/python scripts/treadle_move.py capabilities entry context model
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TREADLE = ROOT / "treadle"

_ALIAS = '''"""Moved to treadle/{pkg}/{mod}.py. This name stays so every existing import keeps working.

An ALIAS, not a copy: `{pkg}.{mod}` and `treadle.{pkg}.{mod}` are the same module object,
so module state is shared and a patch through either name reaches every caller.
"""
import sys

from treadle.{pkg} import {mod} as _moved

sys.modules[__name__] = _moved
'''

_PACKAGE = '''"""Moved to treadle/{pkg}/. This package stays so every existing import keeps working.

A real package rather than an alias, for the reason given in core/__init__.py: each
submodule aliases its counterpart, so nothing is ever loaded twice under two names.
"""
from treadle.{pkg} import *  # noqa: F401,F403
'''


def moved_packages() -> list[str]:
    return sorted(p.name for p in TREADLE.iterdir()
                  if p.is_dir() and (p / "__init__.py").exists())


def retarget(source: str, packages: list[str]) -> str:
    """Point `from pkg...`/`import pkg...` at treadle.pkg for every moved package."""
    names = "|".join(re.escape(p) for p in packages)
    pattern = re.compile(rf"^(\s*)(from|import) ({names})(\.|\s)", re.MULTILINE)
    return pattern.sub(lambda m: f"{m[1]}{m[2]} treadle.{m[3]}{m[4]}", source)


def _git(*args: str) -> None:
    subprocess.run(["git", *args], cwd=ROOT, check=True)


def move(pkg: str) -> list[str]:
    src, dst = ROOT / pkg, TREADLE / pkg
    if dst.exists():
        raise SystemExit(f"treadle/{pkg} already exists")
    modules = sorted(p.stem for p in src.glob("*.py") if p.name != "__init__.py")
    dst.mkdir(parents=True)
    for p in sorted(src.glob("*.py")):
        _git("mv", str(p.relative_to(ROOT)), str((dst / p.name).relative_to(ROOT)))
    for mod in modules:
        (src / f"{mod}.py").write_text(_ALIAS.format(pkg=pkg, mod=mod))
    (src / "__init__.py").write_text(_PACKAGE.format(pkg=pkg))
    return modules


def main(pkgs: list[str]) -> int:
    for pkg in pkgs:
        print(f"{pkg}: moved {', '.join(move(pkg)) or '(package only)'}")
    moved = moved_packages()
    for f in sorted(TREADLE.rglob("*.py")):
        text = f.read_text()
        new = retarget(text, moved)
        if new != text:
            f.write_text(new)
            print(f"retargeted imports in {f.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
