"""Treadle: the report engine. Survey, agents, gated writing; any report type.

The engine knows how to build a report. A pack (packs/<name>) knows what one particular
kind of report is made of. See docs/treadle/PLAN.md.

    import treadle
    treadle.load_pack("travel_guide")
    report = treadle.run_report("travel_guide", {"city": "Lisbon", "days": 3,
                                                 "interests": ["food"], "diet": "none",
                                                 "currency": "EUR"})
"""
# .inputs imports only treadle.core.section (standard library underneath), so it may load
# first; .registry pulls in .spec, which reads Input from it. .survey reads the registry
# and the spec, so it loads after both.
from .inputs import ASK, ASSUME, DECLINE, FIND, Input
from .registry import REPORT_TYPES, get, load_pack, register
from .run import Report, run_report
from .spec import ReportType
from .survey import Survey, SurveyError

__all__ = ["ASK", "ASSUME", "DECLINE", "FIND", "Input", "REPORT_TYPES", "Report",
           "ReportType", "Survey", "SurveyError", "get", "load_pack", "register",
           "run_report"]
