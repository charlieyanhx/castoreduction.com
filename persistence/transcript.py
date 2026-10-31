"""Moved to treadle/persistence/transcript.py. This name stays so every existing import keeps working.

An ALIAS, not a copy: `persistence.transcript` and `treadle.persistence.transcript` are the same module
object, so the process-wide ledger is one ledger and a patch through either name reaches
every caller.
"""
import sys

from treadle.persistence import transcript as _moved

sys.modules[__name__] = _moved
