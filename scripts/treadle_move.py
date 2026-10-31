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


_MODULE_ALIAS = '''"""Moved to treadle/{mod}.py. This name stays so every existing import keeps working.

An ALIAS, not a copy: `{mod}` and `treadle.{mod}` are the same module object, so module
state is shared and a patch through either name reaches every caller.
"""
import sys

from treadle import {mod} as _moved

sys.modules[__name__] = _moved
'''


def moved_packages() -> list[str]:
    """Every name that now lives under treadle/ AND left an alias at the repo root."""
    dirs = [p.name for p in TREADLE.iterdir()
            if p.is_dir() and (p / "__init__.py").exists() and (ROOT / p.name).is_dir()]
    mods = [p.stem for p in TREADLE.glob("*.py")
            if (ROOT / p.name).exists()
            and "Moved to treadle/" in (ROOT / p.name).read_text()]
    return sorted(dirs + mods)


def retarget(source: str, packages: list[str]) -> str:
    """Point imports of every moved name at its treadle name, keeping what each binds.

    `from X... import` keeps its shape. `import X` must NOT become `import treadle.X`,
    which binds `treadle` and leaves every later `X.attr` a NameError; it becomes
    `from treadle import X`, which binds the same name to the same module.
    """
    if not packages:
        return source
    names = "|".join(re.escape(p) for p in packages)
    source = re.sub(rf"^(\s*)from ({names})(\.|\s)",
                    lambda m: f"{m[1]}from treadle.{m[2]}{m[3]}", source, flags=re.MULTILINE)

    def plain_import(m: re.Match) -> str:
        indent, name, sub, alias = m[1], m[2], m[3] or "", m[4] or ""
        if sub and not alias:
            raise SystemExit(f"cannot retarget `import {name}{sub}` safely; edit it by hand")
        if sub:
            return f"{indent}import treadle.{name}{sub}{alias}"
        return f"{indent}from treadle import {name}{alias}"

    return re.sub(rf"^(\s*)import ({names})((?:\.\w+)+)?(\s+as\s+\w+)?[ \t]*$",
                  plain_import, source, flags=re.MULTILINE)


def move_module(mod: str) -> None:
    """A top-level module: treadle/<mod>.py, and an alias at <mod>.py."""
    src, dst = ROOT / f"{mod}.py", TREADLE / f"{mod}.py"
    if dst.exists():
        raise SystemExit(f"treadle/{mod}.py already exists")
    _git("mv", src.name, str(dst.relative_to(ROOT)))
    src.write_text(_MODULE_ALIAS.format(mod=mod))


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
        if (ROOT / pkg).is_dir():
            print(f"{pkg}: moved {', '.join(move(pkg)) or '(package only)'}")
        else:
            move_module(pkg)
            print(f"{pkg}.py: moved")
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
