"""gates/__main__.py — keeps `python -m gates ...` working now that gates is a package.

`gates.py` was a script as well as a module, and its docstring documents the CLI:

    python -m gates --corpus DIR --gate core --out docs/baselines/M3.json

A package cannot be executed through its `__init__`, so without this file that command
dies with "gates is a package and cannot be directly executed" — a split that keeps every
import working and silently breaks the entry point.
"""
from __future__ import annotations

import sys

from gates.runner import main

if __name__ == "__main__":
    sys.exit(main())
