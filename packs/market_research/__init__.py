"""packs/market_research — Castor's market research, behind Treadle's door.

An adapter, not a port. plan.run_plan is called exactly as the product calls it, and is
imported only when a run actually starts: registering this pack, or listing which reports
exist, must not pay for loading the whole market-research domain.

The brief is run_plan's keyword arguments, by name. Anything else is refused before the
run, because a misspelt field ("efort") silently dropped is a paid run at the wrong depth.
"""
from __future__ import annotations

from treadle import ReportType, register

#: Every argument run_plan takes besides `progress`, which the engine supplies.
#: test_treadle_one_door_for_every_report checks this against run_plan's signature.
BRIEF_FIELDS = ("description", "geo", "max_candidates", "operator_weights", "refine",
                "resume_from", "effort", "intake", "report_style")


def validate(brief: dict) -> dict:
    unknown = sorted(set(brief) - set(BRIEF_FIELDS))
    if unknown:
        raise ValueError(f"market_research brief has unknown field(s): {', '.join(unknown)} "
                         f"(accepted: {', '.join(BRIEF_FIELDS)})")
    if not str(brief.get("description") or "").strip():
        raise ValueError("market_research brief needs a non-empty `description`")
    return brief


def run(brief: dict, progress=None) -> dict:
    from plan import run_plan          # the domain, loaded only when a run starts
    kwargs = {k: v for k, v in brief.items() if k != "description"}
    if progress is not None:
        kwargs["progress"] = progress
    return run_plan(brief["description"], **kwargs)


REPORT_TYPE = register(ReportType(
    key="market_research",
    label="Market research",
    description="Castor's venture report: market, competitors, pricing, sizing, viability.",
    runner=run,
    validate=validate,
))
