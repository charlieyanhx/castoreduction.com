"""Moved to treadle/core/registry.py. This name stays so every existing import keeps working.

An ALIAS, not a copy: `core.registry` and `treadle.core.registry` are the same module object, so a
class has one identity (isinstance holds across both names) and a test that patches an
attribute through either name patches it for every caller.
"""
import sys

from treadle.core import registry as _moved

sys.modules[__name__] = _moved
