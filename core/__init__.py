"""Moved to treadle/core/. This package stays so every existing import keeps working.

It is a real package rather than an alias of treadle.core: aliasing the PACKAGE would make
`import core.section` load treadle/core/section.py a second time under a second name, and
two copies of Section would no longer recognise each other. Each submodule here aliases
its counterpart instead (see core/section.py).
"""
from treadle.core import DuplicateRegistration, Evidence, Registry

__all__ = ["Evidence", "Registry", "DuplicateRegistration"]
