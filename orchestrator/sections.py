"""orchestrator/sections.py — report sections declared as data, assembled one at a time.

THE FIRST REAL MIGRATION ONTO core/section.py. The assembler was built, hardened and
tested against nothing: plan.py called its steps literally, so every guarantee it offers
(declared order, bounded reads, isolated writes, per-section verdicts, named skip reasons)
applied to no report anybody could buy. This module is where one section stops being a
literal call and becomes a declaration.

WHY VIABILITY WENT FIRST. It reads the most and writes the least: seven result keys in,
one key out, no downstream consumer inside the run. That makes it the cheapest place to be
wrong. It also carries the distinction the assembler had no way to express -- five inputs
it genuinely needs and two it deliberately narrates the absence of.

WHAT THE MIGRATION CHANGED IN THE FRAME. Declaring viability's reads honestly was
impossible with one tier: `consumes` skips a section whose input is missing, and MEASURED
across the 19-report corpus, declaring all seven would have SKIPPED viability on 16 of the
19 that ship it. `optional` is that second tier, and it exists because a real section
needed it, not because the shape looked tidy.

WHAT IT DID NOT CHANGE. The scorer receives byte-identical kwargs on all 19 corpus
reports, verified by capture-and-diff before and after the split. A migration that
changes the report is not a migration, it is a rewrite with a nicer name.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from core.section import (FAILED, FLAGGED, NOT_APPLICABLE, OK, SKIPPED, Section,
                          SectionResult, assemble, stale_reads, with_operator_note)
from logger import get

from .steps import record_dropped_output, step_done

log = get("plan.sections")

#: Tolerance on the model's own rounding. Contributions are published to one decimal, so
#: five of them can drift about half a point from the headline without anyone lying.
_ROUNDING_SLACK = 1.0


def _has_data(payload: Any) -> bool:
    """Is this payload a real section, or an honest empty?

    THIS IS Evidence.__bool__, APPLIED ONE LAYER UP. The frame already defines when a
    result has data: `count > 0 and error is None`, with `skeleton` and `error` kept apart
    so "we could not look" never reads as "we looked and found nothing". Section payloads
    want the same rule.

    Most sections have no `count`, so for them this is exactly the "no error key" test that
    viability and segment_ranking already used. customer_universe DOES carry one, and its
    step deliberately let an empty universe land in the result while leaving the step
    unrecorded -- so a resume recomputes it instead of skipping past a hole. Reading the
    count is how that survives the migration without a per-section exception, which is what
    a lookup table of special cases would have been.
    """
    if not isinstance(payload, dict):
        return payload is not None
    if payload.get("error"):
        return False
    if "count" in payload:
        return (payload.get("count") or 0) > 0
    return True


def _composition(payload: Any) -> Optional[list]:
    """The per-dimension breakdown, or None when there is nothing to check.

    ABSTAINS rather than failing on an errored or empty payload. "Could not look" is not
    "looked and found nothing" -- the same distinction Evidence draws between a skeleton
    and an error, and the one gate abstention exists for.
    """
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    comp = payload.get("score_composition")
    return comp if isinstance(comp, list) and comp else None


def score_reconciles_with_its_parts(payload: Any) -> Optional[str]:
    """The headline score must equal the contributions it is composed of.

    THE DEFECT SHAPE THIS GUARDS IS MEASURED, IN ANOTHER SECTION. D46 found 8 of 8
    displayed segment scores were the model's numbers rather than Python's -- a headline
    that had quietly stopped being a function of its parts. Viability publishes both the
    parts and the total, so the same substitution is checkable here from the payload alone.
    The corpus is 19/19 clean today: this is a guard against a known failure mode reaching
    a new section, not a repair of one that already has.
    """
    comp = _composition(payload)
    if comp is None:
        return None
    total = payload.get("viability_score")
    if total is None:
        return None
    summed = sum(float(d.get("contribution") or 0) for d in comp if isinstance(d, dict))
    if abs(summed - float(total)) > _ROUNDING_SLACK:
        return (f"headline score is {total} but its {len(comp)} contributions sum to "
                f"{summed:.1f}; the published total is not the total of the published parts")
    return None


def each_dimension_states_one_score(payload: Any) -> Optional[str]:
    """A dimension's score appears twice; both copies must be the same number.

    `scores[dim].score` and `score_composition[].raw` are two independent statements of one
    value, both written by the model. Two places to say a number is two places to say
    different ones, and a reader comparing the dimension panel to the composition table is
    exactly who would find it.
    """
    comp = _composition(payload)
    if comp is None:
        return None
    scores = payload.get("scores") or {}
    bad = []
    for d in comp:
        if not isinstance(d, dict):
            continue
        dim, raw = d.get("dimension"), d.get("raw")
        stated = (scores.get(dim) or {}).get("score")
        if stated is not None and raw is not None and stated != raw:
            bad.append(f"{dim} is {stated} in the dimension panel and {raw} in the "
                       f"composition table")
    return "; ".join(bad) or None


def viability_section(profile: dict, *, biz_kind: str) -> Section:
    """Step 14, declared rather than called.

    `consumes` is what the scorer cannot run without. `optional` is what it knows how to
    say "not measured" about -- written after a Reddit outage became "zero target audience
    confidence" and docked the score, and after a skeleton customer universe was read as
    "0 candidate entities harvested" rather than as an outage.

    `profile` and `biz_kind` are run-scoped arguments, not sections, so they are bound here
    instead of declared: nothing in the assembly produces them and nothing waits on them.
    """
    def produce(ctx: dict) -> dict:
        from .steps.viability import score_viability
        return score_viability(ctx, profile, biz_kind=biz_kind)

    return Section(
        key="viability",
        produce=produce,
        consumes=("four_ps", "discover", "differentiators", "economics", "market_sizing"),
        optional=("customer_universe", "audience"),
        label="Viability",
        origin="llm",
        invariants=(("score_reconciles_with_its_parts", score_reconciles_with_its_parts),
                    ("each_dimension_states_one_score", each_dimension_states_one_score)),
    )


def apply(sections, result: dict, *, checkpoint: Optional[Callable[[], None]] = None,
          ) -> list[SectionResult]:
    """Assemble sections into `result` and do the run's bookkeeping around them.

    core/section.py deliberately knows nothing about `_steps_completed`, checkpoints or
    dropped outputs -- it is frame, and those are this pipeline's conventions. This is the
    seam that connects them, and it is the only place that has to know both.

    A SKIPPED OR FAILED SECTION RECORDS WHY. The assembler already produces the reason
    ("declared input(s) absent or empty: economics"); routing it into _dropped_outputs is
    what puts it on the page, since an absent section that does not explain itself reads
    exactly like one that was never part of the report.
    """
    results = assemble(sections, result)
    for sr in results:
        payload = result.get(sr.key)
        if sr.status == NOT_APPLICABLE:
            # A SEPARATE CHANNEL ON PURPOSE. "We could not produce this" and "this report
            # was never going to have this" are different facts, and the corpus is 14 of 19
            # on the second one. Filing both under _dropped_outputs would hand a DTC
            # founder a list of things that look broken.
            result.setdefault("_inapplicable_sections", {})[sr.key] = sr.reason
            log.info("[plan] section %s does not apply: %s", sr.key, sr.reason[:160])
            continue
        if sr.status in (SKIPPED, FAILED):
            record_dropped_output(result, sr.key, sr.reason)
            log.warning("[plan] section %s %s: %s", sr.key, sr.status, sr.reason[:160])
            continue
        if sr.status == FLAGGED:
            log.warning("[plan] section %s produced but flagged: %s",
                        sr.key, "; ".join(sr.findings)[:300])
        if not _has_data(payload):
            # Produced, but not something to credit as a completed step. Recorded, not
            # promoted: all three pre-migration steps wrote the payload and withheld
            # step_done, and a migration that quietly started crediting it would be a
            # behaviour change -- on the cover page's "N steps completed" line, and on
            # resume, which recomputes a step that was never recorded rather than skipping
            # past a hole. The checkpoint still fires: it only persists partial state for
            # the progress UI, and segment_ranking already called it on this path, so
            # withholding it here would be the behaviour change instead.
            log.warning("[plan] section %s produced nothing to credit: %s", sr.key,
                        str(payload)[:160])
        else:
            step_done(result, sr.key)
        if checkpoint:
            checkpoint()
    result.setdefault("_section_results", []).extend(r.as_dict() for r in results)
    return results


def _is_b2b(profile: dict) -> bool:
    """Does this venture have customer COMPANIES to enumerate?

    Step 5 has always tested the business model string for b2b or saas, and returned
    immediately otherwise, because a direct-to-consumer venture has no company universe to
    build. MEASURED: 14 of 19 corpus reports have no customer_universe and ALL 14 are
    non-B2B.
    """
    model = (profile.get("business_model") or "").lower()
    return "b2b" in model or "saas" in model


def _not_b2b_because(profile: dict, what_it_needs: str) -> Optional[str]:
    """Why a B2B-only section does not apply here, in that section's own words.

    Each section says what IT was going to do and why this venture is not the audience for
    it. A shared sentence would have been shorter and wrong: the reason under "Customer
    universe" must not read as an explanation of segment prioritization, which is exactly
    the mistake this function's first version shipped.
    """
    if _is_b2b(profile):
        return None
    shown = (profile.get("business_model") or "not stated").strip()
    return f"{what_it_needs}, and this venture's business model is {shown}"


def segment_ranking_section(profile: dict, opps: list) -> Section:
    """Steps 7-8, declared rather than called. The second section on the assembler.

    THE SILENT EXIT THIS REPLACES. The step opened with a bare `return` when
    customer_universe carried no segments, and MEASURED across the corpus that is 14 of 19
    reports: segment_ranking absent, with no reason recorded on a single one. The mapping
    is exact -- customer_universe present, .segments present and segment_ranking present
    agree on all 19 -- so declaring it as the one required input reproduces the gate
    precisely and turns 14 silent absences into 14 that state their cause. That disclosure
    is not new code here; it falls out of `apply` routing the assembler's own skip reason.

    `consumer_research` and `operator_weights` are optional for reasons the body already
    encodes: the objection cross-check is wrapped in its own try/except, and weights fall
    back to DEFAULT_WEIGHTS. Declaring them as required would skip the section whenever an
    enrichment was missing, which is exactly the regression the `optional` tier exists for.
    """
    def produce(ctx: dict) -> dict:
        from .steps.segments import rank_customer_segments
        return rank_customer_segments(ctx, profile, opps)

    return Section(
        key="segment_ranking",
        produce=produce,
        consumes=("customer_universe",),
        optional=("consumer_research", "operator_weights"),
        label="Segment ranking",
        origin="llm",
        inapplicable=lambda: _not_b2b_because(
            profile, "segment prioritization ranks the B2B customer universe into "
                     "buyer segments"),
    )


def customer_universe_section(profile: dict, opps: list) -> Section:
    """Step 5, declared rather than called. The third section, and the root of a chain.

    THE SECTION WITH NO INPUTS. It reads nothing from the result: profile and the
    competitor roster are run-scoped arguments, so `consumes` is empty and applicability is
    the ONLY gate. That makes it the cleanest statement of what `inapplicable` is for --
    there is no missing input here to mistake for a failure, only a venture this section
    was never about.

    IT CLOSES THE CHAIN. segment_ranking now reports "declared input absent:
    customer_universe" when it is skipped, which pointed the reader at a section that
    explained nothing about itself. Both now answer with the same fact -- this venture is
    not B2B -- so the explanation terminates instead of forwarding.

    The count-gated credit stays: an empty universe is an honest finding and lands in the
    result, but leaves the step unrecorded so a resume recomputes it rather than skipping
    past a hole. `_has_data` carries that, which is why it reads `count` instead of the
    bare error check the other two sections needed.
    """
    def produce(ctx: dict) -> dict:
        from .steps.customer_universe import build_universe
        return build_universe(ctx, profile, opps)

    return Section(
        key="customer_universe",
        produce=produce,
        label="Customer universe",
        origin="fetched",
        inapplicable=lambda: _not_b2b_because(
            profile, "this section enumerates the real companies that could buy a B2B "
                     "product"),
    )


def check_reads_are_still_current(result: dict) -> list[str]:
    """Ask, once the run is over, whether any section was assembled from stale data.

    RUN THIS LAST. `consumes` guarantees an input exists when a section runs; it cannot
    guarantee the input is final, because a step may rewrite a key it does not own.
    run_financials_step rewrites result["economics"] in place, viability consumes
    economics, and MEASURED across the corpus, 14 of 19 reports would send viability a
    materially different prompt in the other order -- with `economics` present either way,
    so nothing in the declaration could have caught it. The order is correct today; this
    is what notices if it stops being.

    The finding is attached to the section it belongs to, so it renders on the line that
    already exists for "this section was produced and something is wrong with it" rather
    than needing a surface of its own. A produced section stays produced: the reader is
    told what is wrong with it, not handed a hole where it used to be.
    """
    records = [SectionResult(key=d.get("key", ""), status=d.get("status", ""),
                             reason=d.get("reason", ""),
                             findings=list(d.get("findings") or []),
                             reads=dict(d.get("reads") or {}))
               for d in (result.get("_section_results") or [])]
    messages = stale_reads(records, result)
    if not messages:
        return []
    by_key = {}
    for m in messages:
        by_key.setdefault(m.split(" ", 1)[0], []).append(m)
    for entry in (result.get("_section_results") or []):
        found = by_key.get(entry.get("key"))
        if not found:
            continue
        entry["status"] = FLAGGED
        entry["findings"] = list(entry.get("findings") or []) + found
        log.warning("[plan] %s", found[0][:200])
    return messages


def brief_rests_on_a_contributing_agent(payload: Any) -> Optional[str]:
    """A brief with no surviving worker behind it is prose, not research.

    run_research_crew sets `error` to the synthesis error ONLY when nothing contributed
    (`error=synth.error if not contributing else None`). So a lead that synthesises
    successfully while every specialist died returns error=None, a populated `brief`, and
    contributing_agents=[]. Nothing downstream distinguishes that from a real brief.

    This is the codebase's oldest defect shape in the agent layer: an absence read as an
    answer. The crew already records exactly what is needed to catch it, and nobody asked.
    """
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    brief = payload.get("brief")
    if not brief:
        return None
    if not (payload.get("contributing_agents") or []):
        return (f"the brief is {len(str(brief))} characters long and no specialist agent "
                f"contributed to it; the lead synthesised from nothing")
    return None


def every_dispatched_worker_is_accounted_for(payload: Any) -> Optional[str]:
    """A worker that died must be visible in the brief's own bookkeeping.

    The crew isolates worker failures on purpose -- "a dead worker doesn't sink the crew"
    -- and stores None for it. That is the right call and the right record. This checks the
    record survives: a dispatched worker is either contributing or explicitly empty, never
    quietly missing from both.
    """
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    workers = payload.get("workers")
    if not isinstance(workers, dict) or not workers:
        return None
    contributing = set(payload.get("contributing_agents") or [])
    dead = sorted(n for n, v in workers.items() if not v and n not in contributing)
    live = sorted(n for n, v in workers.items() if v)
    missing = sorted(contributing - set(workers))
    if missing:
        return (f"contributing_agents names {', '.join(missing)}, which never appears in "
                f"the worker roster; the brief credits an agent that did not run")
    if dead and not live:
        return (f"every dispatched specialist failed ({', '.join(dead)}) yet the brief "
                f"was still assembled")
    return None


def research_brief_section(description: str, geo: str = "US",
                           effort_levers: Optional[dict] = None,
                           address: Optional[str] = None) -> Section:
    """The research crew, declared. The fourth section, and the first that is an AGENT.

    WHAT THIS TESTS THAT THE OTHER THREE DO NOT. viability, segment_ranking and
    customer_universe are deterministic or single-call producers. This one fans four
    specialist agents out over their own harness contexts and has a lead synthesise them.
    If the frame is a frame, an agent crew is just another producer behind the same
    contract -- same bounded context, same verdict, same disclosure. That claim was
    untested: MEASURED, 0 of 19 corpus reports ran the crew.

    THE ABSENCE IT CLOSES IS THE LAST 19/19. run_crew_step's own docstring insists the
    distinction matters -- "None means 'not attempted' and is distinct from an error; a
    reader must be able to tell a run that did not buy the crew from one where it was
    bought and failed" -- and then plan.py did `if _brief is not None` and moved on, so a
    standard-effort report said nothing at all. Not attempted is exactly what
    `inapplicable` is for, and now the report says which tier would have produced it.

    `consumes` is empty because the crew researches from the DESCRIPTION, not the result.
    It reads no section and therefore waits on none.
    """
    levers = effort_levers or {}

    def produce(ctx: dict) -> dict:
        from .steps.crew import run_crew_step
        brief = run_crew_step({}, description, geo, effort_levers=levers, address=address)
        if brief is None:
            # Unreachable while `inapplicable` gates the lever, and worth a loud failure
            # rather than a None written into the result if the two ever disagree.
            raise RuntimeError("the crew declined to run despite the effort lever being on")
        return brief

    return Section(
        key="research_brief",
        produce=produce,
        label="Research brief",
        origin="llm",
        inapplicable=lambda: None if levers.get("research_crew") else (
            "the research crew is a deep-effort stage: four specialist agents plus a lead "
            "synthesis, and this run was not bought at that tier"),
        invariants=(("brief_rests_on_a_contributing_agent",
                     brief_rests_on_a_contributing_agent),
                    ("every_dispatched_worker_is_accounted_for",
                     every_dispatched_worker_is_accounted_for)),
    )


#: What the writer cannot write without. Sizing, economics, financials and the score are
#: the spine of every decision the report is asked to make; a venture missing one of them
#: has no report to synthesise, only sections to list.
SYNTHESIS_CONSUMES = ("market_sizing", "economics", "financials", "viability")


def synthesis_section(result: dict, description: str) -> Section:
    """The analyst report, declared. The fifth section, and the one that reads everything.

    IT CONSUMES THE OUTPUT OF EVERY OTHER SECTION, so it runs last, and that is derived,
    not arranged: `optional` names every top-level key the fact layer carries, which
    makes each of them an ordering edge in core.section.plan. A section added tomorrow is
    read by the writer the moment it lands in the result, because the roster is the union
    of the declared FACT_KEYS and what this run actually holds.

    THREE REASONS IT DOES NOT APPLY, each stated in its own words. A stubbed run is a
    clone of another report, so there is nothing about this venture to write from. No
    ANTHROPIC_API_KEY means no writer. And the writer is a paid Opus call by design
    (MEASURED: a smaller model invented two figures the citation gate then caught), so
    it waits for the same consent the JSON chain waits for: LLM_ALLOW_PAID=1 or
    LLM_BACKEND=anthropic.

    EACH REASON IS WRITTEN FOR TWO READERS. All three are facts about the deployment, not
    the venture, and a founder reading the page needs only the first half: the report is
    not written here. The operator who has to set the variable needs its name, so the
    name rides the same string as an operator note (core.section.with_operator_note),
    which the log and the stored result keep and the page drops. MEASURED before this:
    "Synthesis (the report is written by claude-opus-5 and ANTHROPIC_API_KEY is not set)"
    on every buyer's page of a deployment that had not opted in. The reasons read as the
    clause after the section's name, the way the research brief's does.

    A WRITE THAT FAILS IS A FAILED SECTION, NEVER A FAILED RUN. The assembler already
    records a raising producer as FAILED with the exception's class and message, and
    `apply` routes that to the page. Twenty-one sections of paid research do not become
    worthless because the twenty-second could not be written.

    `result` is closed over for applicability only. The producer reads its declared
    context, and the style it writes in rides that context on intake.report_style.
    """
    from report.synthesis import (DEFAULT_STYLE, DROPPED_KEY, FACT_KEYS, INAPPLICABLE_KEY,
                                  MODEL, the_writing_ran_to_its_end, write_synthesis)

    carried = {k for k in (result or {}) if not str(k).startswith("_")}
    optional = (tuple(sorted((set(FACT_KEYS) | carried) - set(SYNTHESIS_CONSUMES)))
                + (DROPPED_KEY, INAPPLICABLE_KEY))

    def produce(ctx: dict) -> dict:
        style = ((ctx.get("intake") or {}).get("report_style")) or DEFAULT_STYLE
        return write_synthesis(ctx, description, style)

    def why_not() -> Optional[str]:
        from capabilities.effort import effort_config
        from llm import backend_configured, paid_backend_allowed
        if (result or {}).get("_stub"):
            return with_operator_note(
                "this run is a clone of another report, not research into this venture; "
                "there is nothing to write from",
                "CASTOR_STUB_REPORT names the report it was cloned from")
        # THE PURCHASE DECIDES BEFORE THE DEPLOYMENT DOES. A quick run is a fast look at
        # the numbers, and the written report is an Opus call it was not bought at; that
        # reason belongs to the founder, so it comes before the deployment's, and it
        # carries no operator note because there is no variable to set.
        if not effort_config((result or {}).get("_effort")).get("analyst_report"):
            return ("the written report starts at standard effort, and this run was "
                    "bought at quick")
        if not backend_configured("anthropic"):
            return with_operator_note(
                f"written by {MODEL}, and this deployment has not enabled it",
                "ANTHROPIC_API_KEY is not set")
        if not paid_backend_allowed("anthropic"):
            return with_operator_note(
                f"written by {MODEL}, a paid model, and this deployment has not opted "
                f"into paid backends",
                "set LLM_ALLOW_PAID=1 or LLM_BACKEND=anthropic")
        return None

    return Section(
        key="synthesis",
        produce=produce,
        consumes=SYNTHESIS_CONSUMES,
        optional=optional,
        label="Analyst report",
        origin="llm",
        inapplicable=why_not,
        invariants=(("the_writing_ran_to_its_end", the_writing_ran_to_its_end),),
    )
