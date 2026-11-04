"""Moved to treadle/provenance.py. This name stays so every existing import keeps working.

An ALIAS, not a copy: `provenance` and `treadle.provenance` are the same module object, so module
state is shared and a patch through either name reaches every caller.
"""
import sys

from treadle import provenance as _moved

sys.modules[__name__] = _moved
