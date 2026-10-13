"""Moved to treadle/cache.py. This name stays so every existing import keeps working.

An ALIAS, not a copy: `cache` and `treadle.cache` are the same module object, so module
state is shared and a patch through either name reaches every caller.
"""
import sys

from treadle import cache as _moved

sys.modules[__name__] = _moved
