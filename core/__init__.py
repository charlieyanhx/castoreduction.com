"""core/ — the frame's contracts, importable without the domain.

Everything in here is true of any report this harness could produce: an Evidence envelope
around one I/O call, and a Registry of named capabilities. Nothing here knows what a
market, a competitor or a TAM is.

THE RULE: core imports the standard library and nothing else in this repo. The soul may
depend on the frame; the frame may never depend on the soul. That direction is enforced by
test_the_frame_does_not_import_the_soul.py rather than left to good intentions.
"""
from __future__ import annotations

from core.evidence import Evidence
from core.registry import DuplicateRegistration, Registry

__all__ = ["Evidence", "Registry", "DuplicateRegistration"]
