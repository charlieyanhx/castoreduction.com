"""Moved to treadle/capabilities/. This package stays so every existing import keeps working.

A real package rather than an alias, for the reason given in core/__init__.py: each
submodule aliases its counterpart, so nothing is ever loaded twice under two names.
"""
from treadle.capabilities import *  # noqa: F401,F403
