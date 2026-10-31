"""report/section_provenance.py — section → producing script, for the debuggable report.

Every report section renders from exactly one result-key, and each result-key is produced
by exactly one skill (`@skill(produces=...)`) or one domain module (financials / economics
/ sizing math). This maps section → producer so the report's `?debug=1` overlay can badge
each block with the script that owns it, the evidence it consumed, and the character of
its data — turning "this sentence is wrong" into "open <module>".

Section-level and deterministic; a test fails if a section the report renders has no
producer entry here, or if a skill-produced section drifts from the live SKILL_REGISTRY.
The `module` points at where the CONTENT logic lives (differentiators.py, taste.py,
four_ps.py, …), not a thin skill wrapper. (The wrapper layer, skills/pipeline_steps.py,
was deleted as unused.)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

# data-character vocabulary — the honest label for how a section's numbers/prose arise
COMPUTED = "computed"      # deterministic arithmetic from upstream data
LLM = "llm"                # an LLM wrote this prose / made this judgement
FETCHED = "fetched"        # scraped / API-retrieved real-world data
SIMULATED = "simulated"    # LLM-simulated panels (WTP, personas, max-diff)
MIXED = "mixed"            # a blend (e.g. sizing: fetched anchors + LLM estimates)


@dataclass(frozen=True)
class SectionSource:
    """Where one report section's data actually comes from.

    This is the table the drift guard checks: `produced_by` must genuinely exist in
    `module`, or the report is attributing a section to a function that never ran.
    """
    section: str                    # human label ("Market Size")
    result_key: str                 # result dict key it renders from ("market_sizing")
    produced_by: str                # the skill or function that produces it
    module: str                     # dotted module path where the logic lives
    kind: str                       # "skill" | "module"
    origin: str                     # default data character (see vocabulary above)
    consumes: tuple[str, ...] = ()  # upstream result-keys it depends on
    #: WHERE THE DATA ACTUALLY LIVES, when that is not `result[result_key]`.
    #: MEASURED across 19 corpus reports: "pricing_benchmark" was present 0/19 times,
    #: because the benchmark table lives at result["pricing"]["benchmark"] and the
    #: renderer reads it from there. The section shipped on every page while its
    #: provenance entry silently matched nothing, so the one section a buyer is most
    #: likely to question carried no attribution at all. Defaults to (result_key,).
    path: tuple[str, ...] = ()
    #: True when the section is COMPUTED AT RENDER TIME rather than stored on the result.
    #: "integrity" is build_integrity_summary(result), called by render_html; it is never
    #: a result key, so checking for one could only ever fail. Same 0/19, same cause.
    derived: bool = False


# The authoritative section → producer table. Ordered roughly as the report reads.
#
# DRIFT IS THE FAILURE MODE HERE. Three entries in this table named functions that did not
# exist anywhere in the repo (`reddit_customer_voice`, `run_verifier`, `research_crew`) and
# one named the wrong module (`rank_segments` lives in segment_scoring.py, not plan.py) —
# the table LOOKED authoritative while pointing a debugger at files that would not contain
# what it promised. The skill-kind entries were guarded by a test against SKILL_REGISTRY;
# the module-kind ones were not, so nothing caught it. They are now: see
# test_section_provenance's module-resolution test. Prefer the RUN's own record
# (report/trace.recorded_producers) over this table wherever one exists.
SECTION_SOURCES: tuple[SectionSource, ...] = (
    SectionSource("Company profile", "profile", "extract_company_profile",
                  "company_profile", "module", LLM),
    SectionSource("Differentiators", "differentiators", "extract_differentiators",
                  "differentiators", "module", LLM, ("competitor_pricing", "audiences")),
    SectionSource("Customer universe", "customer_universe", "build_customer_universe",
                  "customer_universe", "module", FETCHED),
    SectionSource("Segment prioritization", "segment_ranking", "rank_segments",
                  "segment_scoring", "module", COMPUTED, ("customer_universe",)),
    SectionSource("Competitive landscape", "discover", "discover",
                  "discover", "module", FETCHED),
    SectionSource("Competitor map", "clustering", "cluster_competitors",
                  "clustering", "module", COMPUTED, ("discover",)),
    SectionSource("Feature importance", "max_diff", "simulate_max_diff",
                  "pricing", "module", SIMULATED),
    SectionSource("Decoded audiences", "audiences", "decode_taste", "taste", "module",
                  FETCHED),
    SectionSource("Consumer research / WTP", "consumer_research", "consumer_research_skill",
                  "skills.perspective", "skill", SIMULATED, ("company_profile",)),
    SectionSource("Pricing (PSM)", "pricing", "simulate_van_westendorp", "pricing",
                  "module", SIMULATED),
    SectionSource("Pricing benchmark", "pricing_benchmark", "build_benchmark_table",
                  "pricing", "module", FETCHED, ("competitor_pricing",),
                  path=("pricing", "benchmark")),
    SectionSource("Unit economics", "economics", "retail_unit_economics",
                  "business_model", "module", COMPUTED, ("pricing", "market_sizing")),
    SectionSource("Market size", "market_sizing", "estimate_market_size",
                  "market_sizing", "module", MIXED),
    SectionSource("Validation", "validation", "validate_numbers",
                  "skills.sizing.validate", "skill", COMPUTED, ("market_sizing",)),
    SectionSource("3-year financials", "financials", "project_three_year",
                  "financials", "module", COMPUTED, ("market_sizing", "pricing")),
    SectionSource("4Ps", "four_ps", "assemble_4ps_split", "four_ps", "module", LLM,
                  ("personas", "max_diff", "pricing")),
    SectionSource("Viability", "viability", "score_viability", "four_ps", "module", LLM,
                  ("four_ps", "differentiators", "market_sizing")),
    SectionSource("Personas", "personas", "synthesize_personas", "personas",
                  "module", SIMULATED, ("audiences",)),
    SectionSource("Customer voice (Reddit)", "reddit_signal", "fetch_signal",
                  "reddit_signal", "module", FETCHED),
    SectionSource("Verification", "verification", "verify_report",
                  "report.verifier", "module", LLM),
    SectionSource("Research brief", "research_brief", "run_crew_step",
                  "orchestrator.steps.crew", "module", LLM),
    SectionSource("Integrity summary", "integrity", "build_integrity_summary",
                  "plan", "module", COMPUTED, derived=True),
)

_BY_KEY: dict[str, SectionSource] = {s.result_key: s for s in SECTION_SOURCES}


def _present(result: dict, key: str) -> bool:
    """A section renders when its data holds non-empty, non-errored content.

    Follows `path` when the data is nested, because a top-level key that never exists is
    indistinguishable from a section that never ran -- and two sections were in exactly
    that state, attributed to nothing on every report they appeared on.
    """
    src = _BY_KEY.get(key)
    if src is not None and src.derived:
        # Computed at render time from the whole result. It is present whenever there is
        # a result to compute it from; there is no key to look for.
        return bool(result)
    v: object = result
    for part in (src.path if src is not None and src.path else (key,)):
        if not isinstance(v, dict):
            return False
        v = v.get(part)
    if not v:
        return False
    if isinstance(v, dict) and v.get("error"):
        return False
    return True


def _refine_origin(key: str, data, default: str) -> str:
    """Sharpen the default data-character from the actual payload where we can — the
    honest 'llm vs fetched' distinction the integrity work already tracks per figure."""
    if key == "market_sizing" and isinstance(data, dict):
        origins = set()
        tam = data.get("tam") or {}
        for mk in ("method_top_down", "method_bottom_up", "method_analog"):
            o = (tam.get(mk) or {}).get("origin")
            if o:
                origins.add(o)
        if origins == {"llm"}:
            return LLM
        if origins and origins != {"llm"}:
            return MIXED
    return default


def build_section_provenance(result: dict) -> list[dict]:
    """For each report section PRESENT in `result`, return its producer descriptor with
    the data-character refined from the payload. Ordered as SECTION_SOURCES. This is the
    data the ?debug overlay renders as per-section badges.
    """
    # THE LEDGER BEATS THE MAP. This table is DECLARED intent, and declared intent drifts:
    # measured on run6..run11, the shipped "How each section was produced" row credited
    # Market size to estimate_market_size (the legacy national engine) while the run's own
    # ledger — and gate D52 — recorded size_hyperlocal at skills/sizing/hyperlocal.py as the
    # producer. One report, two contradictory claims about its most load-bearing section,
    # and the reader-facing one was the wrong one. A static map cannot drift-check itself;
    # the ledger records the module, file and line AT THE MOMENT OF PRODUCTION. Where a
    # recorded producer exists it wins, and an overridden declaration stays visible as
    # declared_producer rather than being silently papered over.
    try:
        from report.trace import recorded_producers
        recorded = recorded_producers(result)
    except Exception:                       # a trace failure must never break the report
        recorded = {}

    out: list[dict] = []
    for s in SECTION_SOURCES:
        if not _present(result, s.result_key):
            continue
        entry = asdict(s)
        entry["consumes"] = list(s.consumes)
        entry["origin"] = _refine_origin(s.result_key, result.get(s.result_key), s.origin)
        rec = recorded.get(s.result_key)
        if rec and rec.get("produced_by"):
            if rec["produced_by"] != s.produced_by:
                entry["declared_producer"] = s.produced_by
            entry["produced_by"] = rec["produced_by"]
            if rec.get("module"):
                entry["module"] = rec["module"]
            entry["attribution"] = "recorded"
        else:
            entry["attribution"] = "declared"
        out.append(entry)
    return out


def producer_for(result_key: str) -> dict | None:
    """The producer descriptor for one result-key (None if unmapped)."""
    s = _BY_KEY.get(result_key)
    return asdict(s) if s else None
