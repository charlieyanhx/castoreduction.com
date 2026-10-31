"""Moved to treadle/capabilities/scheduler.py. This name stays so every existing import keeps working.

An ALIAS, not a copy: `capabilities.scheduler` and `treadle.capabilities.scheduler` are the same module object,
so module state is shared and a patch through either name reaches every caller.
"""
import sys

from treadle.capabilities import scheduler as _moved

sys.modules[__name__] = _moved
