"""Moved to treadle/harness/agent.py. This name stays so every existing import keeps working.

An ALIAS, not a copy: `harness.agent` and `treadle.harness.agent` are the same module object,
so module state is shared and a patch through either name reaches every caller.
"""
import sys

from treadle.harness import agent as _moved

sys.modules[__name__] = _moved
