"""gates/common.py — the verdict types every detector returns.

Split out of gates.py so the six detector modules share one definition of a Finding
rather than importing each other.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import statistics
import sys
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class Finding:
    """One detector's verdict on one report.

    `ok` is three-valued: True holds, False violated, None could not be decided. The
    comment on `out_of_scope` below explains why None alone was not enough.
    """
    ok: Optional[bool]  # True pass / False fail / None not-applicable
    detail: str = ""
    # WHY this abstained, when it did. `None` alone conflates two opposite meanings, and D55
    # was adding them together: "this check cannot apply to this KIND of venture" (a fact
    # about the business, no reflection on the report) versus "this would apply and the data
    # is not there" (exactly the incompleteness D55 exists to catch). Only the detector knows
    # which it means, so the detector says it — see `not_applicable` below.
    out_of_scope: bool = False


@dataclass
class Invariant:
    """A named detector, and what its verdict is worth.

    `audit_class` records WHICH historical failure this exists to catch, so a check can
    never become a rule nobody remembers the reason for. `severity` separates "block the
    gate" from "say it and move on", which is what lets the sweep stay honest without
    refusing every report that has one soft flaw.
    """
    id: str
    name: str
    audit_class: str          # which historical failure mode this detects
    severity: str             # "fail" blocks a gate; "warn" is reported only
    check: Callable[[dict, Optional[str]], Finding] = field(repr=False, default=None)


def not_applicable(detail: str) -> Finding:
    """Abstain because this check does not apply to this venture's SHAPE.

    Use for facts about the business that no amount of report quality can change — a
    subscription is not per-unit, a national_digital venture has no trade area, a US venture
    has no currency conversion to disclose. These leave D55's denominator entirely.

    Do NOT use for an absent section, an empty list, or a claim the report simply did not
    make. A hollow report makes no claims at all, so every claim-guard abstains together —
    that is the signal D55 reads, and marking those out-of-scope would retire the gate.
    Plain `Finding(None, ...)` stays the default for exactly that reason.
    """
    return Finding(None, detail, out_of_scope=True)


def _num(v) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
