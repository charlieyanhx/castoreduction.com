"""Treadle: the report engine. Survey, agents, gated writing; any report type.

The engine knows how to build a report. A pack (packs/<name>) knows what one particular
kind of report is made of. See docs/treadle/PLAN.md.

    import treadle
    treadle.load_pack("travel_guide")
    report = treadle.run_report("travel_guide", {"city": "Lisbon", "days": 3,
                                                 "interests": ["food"]})
"""
from .registry import REPORT_TYPES, get, load_pack, register
from .run import Report, run_report
from .spec import ReportType

__all__ = ["REPORT_TYPES", "Report", "ReportType", "get", "load_pack", "register",
           "run_report"]
