"""Moved to treadle/entry/hooks.py. This name stays so every existing import keeps working.

An ALIAS, not a copy: `entry.hooks` and `treadle.entry.hooks` are the same module object,
so module state is shared and a patch through either name reaches every caller.
"""
import sys

from treadle.entry import hooks as _moved

sys.modules[__name__] = _moved
