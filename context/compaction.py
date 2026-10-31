"""Moved to treadle/context/compaction.py. This name stays so every existing import keeps working.

An ALIAS, not a copy: `context.compaction` and `treadle.context.compaction` are the same module object,
so module state is shared and a patch through either name reaches every caller.
"""
import sys

from treadle.context import compaction as _moved

sys.modules[__name__] = _moved
