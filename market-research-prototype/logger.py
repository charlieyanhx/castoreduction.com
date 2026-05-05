"""
Central logging. Imported everywhere that needs to log.

Level is controlled by MRP_LOG_LEVEL env var. Defaults to INFO.
Set MRP_LOG_LEVEL=DEBUG for noisy output, WARNING to quiet.
"""
from __future__ import annotations
import logging
import os
import sys

_LEVEL = os.environ.get("MRP_LOG_LEVEL", "INFO").upper()
_FORMAT = "%(asctime)s %(levelname)-7s %(name)-12s %(message)s"
_DATEFMT = "%H:%M:%S"


def _configure() -> logging.Logger:
    root = logging.getLogger("mrp")
    if root.handlers:
        return root  # already configured
    root.setLevel(_LEVEL)
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    root.addHandler(h)
    root.propagate = False
    return root


_configure()
log = logging.getLogger("mrp")


def get(name: str) -> logging.Logger:
    """Get a namespaced child logger — e.g. get('discover') → mrp.discover."""
    return logging.getLogger(f"mrp.{name}")
