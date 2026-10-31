"""Moved to treadle/core/evidence.py. This name stays so every existing import keeps working.

An ALIAS, not a copy: `core.evidence` and `treadle.core.evidence` are the same module object, so a
class has one identity (isinstance holds across both names) and a test that patches an
attribute through either name patches it for every caller.
"""
import sys

from treadle.core import evidence as _moved

sys.modules[__name__] = _moved
