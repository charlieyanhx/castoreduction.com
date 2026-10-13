"""Moved to treadle/core/section.py. This name stays so every existing import keeps working.

An ALIAS, not a copy: `core.section` and `treadle.core.section` are the same module object, so a
class has one identity (isinstance holds across both names) and a test that patches an
attribute through either name patches it for every caller.
"""
import sys

from treadle.core import section as _moved

sys.modules[__name__] = _moved
