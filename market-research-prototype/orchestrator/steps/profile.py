"""orchestrator/steps/profile.py — Step 2: company profile (Wave 3, item 5).

First step out of run_plan. It was extracted now because Wave 3 already touched it
(item 4's resume guard), per the plan's rule that a file moves only in the wave that
touches it.
"""
from __future__ import annotations

from company_profile import extract_company_profile
from logger import get

from . import skip_step, step_done, step_scope

log = get("plan.steps.profile")

_UNKNOWN_GEO = ("", "unknown", "none", "n/a")


def run_profile_step(result: dict, description: str, geo: str) -> dict:
    """Extract the company profile, or reuse a resumed one.

    Returns the profile dict. On extraction failure the dict carries `error` and the
    step is NOT recorded complete — the caller aborts the run. (Recording a failed step
    as done would let a later resume skip it and ship a profile-shaped hole.)
    """
    with step_scope("profile"):
        if skip_step(result, "profile", "profile"):
            profile = result["profile"]
            log.info("[plan] Step 2: profile RESUMED from prior run (skipped)")
        else:
            log.info("[plan] Step 2: extracting company profile")
            profile = extract_company_profile(description)
            if profile.get("error"):
                return profile

        # Geography fallback: if the LLM said "unknown" but the request carried a geo,
        # use the request value — prevents an "Geography: unknown" cover page when we
        # actually do know.
        if (profile.get("geography") or "").strip().lower() in _UNKNOWN_GEO:
            profile["geography"] = geo
            profile["_geography_source"] = "request_default"

        result["profile"] = profile
        step_done(result, "profile")
        return profile
