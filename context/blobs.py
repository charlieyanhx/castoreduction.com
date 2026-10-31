"""Moved to treadle/context/blobs.py. This name stays so every existing import keeps working.

An ALIAS, not a copy: `context.blobs` and `treadle.context.blobs` are the same module object,
so module state is shared and a patch through either name reaches every caller.
"""
import sys

from treadle.context import blobs as _moved

sys.modules[__name__] = _moved
