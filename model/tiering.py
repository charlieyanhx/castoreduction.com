"""Moved to treadle/model/tiering.py. This name stays so every existing import keeps working.

An ALIAS, not a copy: `model.tiering` and `treadle.model.tiering` are the same module object,
so module state is shared and a patch through either name reaches every caller.
"""
import sys

from treadle.model import tiering as _moved

sys.modules[__name__] = _moved
