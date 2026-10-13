"""Moved to treadle/capabilities/effort.py. This name stays so every existing import keeps working.

An ALIAS, not a copy: `capabilities.effort` and `treadle.capabilities.effort` are the same module object,
so module state is shared and a patch through either name reaches every caller.
"""
import sys

from treadle.capabilities import effort as _moved

sys.modules[__name__] = _moved
