"""gates/provenance.py — where each figure came from, and whether the citation beside it is real.

6 of the 61 deterministic detectors. Each takes (result, html) and
returns a Finding; none of them share state, which is why they split cleanly.
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
from gates.common import Finding, not_applicable, _num


NON_US_MARKERS = (
    "portugal", "lisbon", "canada", "mexico", "brazil", "united kingdom", " uk", "london",
    "germany", "berlin", "france", "paris", "spain", "madrid", "italy", "japan", "tokyo",
    "china", "india", "australia", "singapore", "netherlands", "europe",
)


def d25_provenance_chip_not_fabricated(r: dict, html: Optional[str]) -> Finding:
    """The report may not claim SOURCING it does not have (R4 rank 1, 16/16).

    The integrity chip rendered green "Sourced: 3/3 — headline methods with a cited
    source" from the LLM-authored `source` strings, on reports whose every
    triangulation path was origin='llm' — model-recalled citations sold as fetched
    data, 7 of them naming Census/BLS for numbers no fetch produced. FAILS when no
    method is genuinely grounded (data_origin) but the html carries the fetched-data
    claim without the model-asserted disclosure."""
    ms = r.get("market_sizing") or {}
    tam = ms.get("tam") or {}
    methods = [tam.get(k) or {} for k in ("method_top_down", "method_bottom_up", "method_analog")]
    methods = [m for m in methods if isinstance(m.get("value_usd"), (int, float))]
    if not methods or html is None:
        return Finding(None, "no headline methods or no HTML")
    n_grounded = sum(1 for m in methods
                     if str(m.get("data_origin") or "").strip().lower() not in ("", "llm"))
    if n_grounded > 0:
        return Finding(True, f"{n_grounded}/{len(methods)} methods genuinely grounded")
    # All model-asserted: the old green claim must be gone and the disclosure present.
    if "with a cited source" in html:
        return Finding(False, "all origins are llm but the chip still claims "
                              "'headline methods with a cited source'")
    if "model-asserted" not in html:
        return Finding(False, "all origins are llm and the model-asserted disclosure "
                              "is rendered nowhere")
    return Finding(True, "model-asserted citations disclosed as such")


def d11_currency_sources(r: dict, html: Optional[str]) -> Finding:
    """A non-US venture must not be SOURCED to US-only data, nor advised to validate on it.

    MEASURED: this gate passed a Lisbon bakery whose TAM was built on the BLS Consumer
    Expenditure Survey national average and cited as "source: BLS Consumer Expenditure
    Survey", because it inspected `sources_to_validate` — the ADVICE strings — and those
    have always been right. `validation_sources_for()` correctly returns Eurostat/INE for a
    non-US location, so the one field the gate read was the one field that was never wrong,
    while the DATA half beside it carried a US federal citation.

    It now reads provenance as well as advice, and looks for the location in the places the
    pipeline actually stores it — `profile.geography` alone missed a hyperlocal run whose
    trade area lives on `market_sizing._hyperlocal_location`.
    """
    prof, ms = r.get("profile") or {}, r.get("market_sizing") or {}
    blob = " ".join(str(v or "") for v in (
        prof.get("geography"), prof.get("location"), prof.get("summary"),
        ms.get("location"), ms.get("_hyperlocal_location"), ms.get("density_geography"),
    )).lower()
    if not any(m in blob for m in NON_US_MARKERS):
        return not_applicable("US venture")

    bad = []
    srcs = " ".join(ms.get("sources_to_validate") or [])
    advice = [s for s in ("US Census", "BLS") if s in srcs]
    if advice:
        bad.append(f"recommends US-only sources to validate against: {advice}")
    # The half that shipped wrong. `bls`/`census` are the origins D53 treats as agency-
    # grounded; a non-US venture may carry `bls_national_us` (a labelled proxy) but never
    # an origin that asserts the agency surveys this market.
    origins = ms.get("data_origin") or {}
    claimed = sorted({k for k, v in origins.items() if v in ("bls", "census", "acs")})
    if claimed:
        bad.append(f"claims US federal provenance for {claimed}")
    # The reader-facing string, checked separately: an origin can be right while the
    # sentence beside it still says BLS.
    src_txt = str(ms.get("spend_per_hh_source") or "")
    if ("BLS" in src_txt or "Census" in src_txt) and "PROXY" not in src_txt.upper():
        bad.append(f"spend cited to a US agency without a proxy label: {src_txt[:70]}")
    return Finding(not bad, "non-US venture " + "; ".join(bad) if bad else "clean")


def d12_provenance(r: dict, html: Optional[str]) -> Finding:
    tr = r.get("_trace")
    n = len(tr) if isinstance(tr, (list, dict)) else 0
    return Finding(n > 0, f"{n} trace records" if n else "no _trace on result")


def d47_trace_belongs_to_one_run(r: dict, html: Optional[str]) -> Finding:
    """Audit criticals #2/#3: a report's `_trace` must be exactly ONE run's history.

    `step_done` appends to `_steps_completed` and records the ledger step event in the
    same call, idempotent per result dict, and every `_step_done` site in plan.py passes
    the same `result`. So within one run no step name can repeat and no step event can
    exist that `_steps_completed` never declared. Either means a concurrent run's events
    landed in this report — which is how the buyer-facing "Data Provenance" panel came to
    publish another run's work: on the pre-fix corpus 8/16 reports carry duplicate or
    foreign step events, and the contaminated ones report 84-107 LLM calls against a clean
    median of 37 (~2.8x). `_cogs` is derived from the same event list, so the disclosed
    cost of the report inherits the same inflation.

    Post-fix every event carries `run_id`, so more than one run id in a trace is direct
    proof; that clause is inert on pre-fix reports, which is why the structural checks
    carry the baseline. Steps DECLARED but absent from the trace are not failed here — a
    resume seed and plan.py's direct "refine" append both legitimately declare a step
    with no ledger event in this run."""
    tr = r.get("_trace")
    if not isinstance(tr, list) or not tr:
        return Finding(None, "no _trace (D12 covers presence)")
    declared = r.get("_steps_completed") or []
    comp = [e.get("name") for e in tr if isinstance(e, dict)
            and e.get("layer") == "step" and e.get("status") == "complete"]
    if not comp:
        return Finding(None, "no step-complete events in the trace")
    run_ids = {e.get("run_id") for e in tr if isinstance(e, dict) and e.get("run_id")}
    dupes = sorted({n for n in comp if comp.count(n) > 1})
    foreign = sorted(n for n in set(comp) - set(declared) if n)
    problems = []
    if len(run_ids) > 1:
        problems.append(f"{len(run_ids)} distinct run_ids in one trace")
    if dupes:
        problems.append(f"step(s) recorded complete more than once: {dupes[:4]}")
    if foreign:
        problems.append(f"step event(s) this run never declared: {foreign[:4]}")
    if problems:
        return Finding(False, "; ".join(problems))
    return Finding(True, f"{len(comp)} step event(s), one run's history")


def d48_shipped_report_attributes_its_sections(r: dict, html: Optional[str]) -> Finding:
    """Provenance a buyer cannot see is not provenance.

    report/section_provenance.py maps every section to the script that produced it, and
    build_section_provenance() runs on EVERY render — then the result was discarded unless
    someone hand-typed `?debug=1`. Measured: 0/16 shipped reports named any producing
    module, nothing in web/ or templates/ linked to the flag, and the PDF path calls the
    endpoint positionally so it could never carry the overlay.

    FAIL when a section the report renders carries no visible producer/origin attribution.
    N/A on a debug render — the subject is the shipped report, not the debug view."""
    if html is None:
        return Finding(None, "no html to check")
    if "prov-legend" in html:
        return Finding(None, "debug render — the gate judges the shipped report")
    from report.section_provenance import build_section_provenance
    prov = build_section_provenance(r)
    if not prov:
        return Finding(None, "no attributable sections in this result")
    missing = [p["result_key"] for p in prov
               if f'data-produced-by="{p["module"]}"' not in html
               or f'data-origin="{p["origin"]}"' not in html]
    if missing:
        return Finding(False, f"{len(missing)}/{len(prov)} rendered section(s) carry no "
                              f"producer/origin a reader can see: {', '.join(missing[:6])}")
    return Finding(True, f"all {len(prov)} rendered section(s) name their module and "
                         "declare computed/fetched/llm/simulated/mixed")


# Statistical agencies whose name in a source string is an authority claim a reader will
# trust without checking. Brand names of private research shops (Statista, Gartner) are
# deliberately absent: they are not verifiable through a tool we call, so demanding an
# origin for them would flag every honest secondary citation.
_AGENCIES = re.compile(
    r"\b(census|acs\b|cbp\b|susb|bls\b|qcew|cex\b|oes\b|eurostat|"
    r"office for national statistics|statcan|world bank|imf\b|oecd|fred\b)", re.I)


# Phrasings that DISCLOSE rather than assert. Six corpus figures already say
# "LLM estimate (UNSOURCED - validate vs US Census ACS)", which names the agency as
# something to check against and states plainly that the number is not sourced. Failing
# those would punish the disclosure and teach the pipeline to stop disclosing.
_DISCLOSED = re.compile(
    r"unsourced|llm estimate|llm-estimate|model(?:led|ed)?\s|estimate only|"
    r"validate\s+(?:vs|against)|compare\s+(?:to|vs|against)|to be validated|"
    r"not\s+(?:yet\s+)?sourced|placeholder", re.I)


_PROVEN_ORIGINS = {"census", "acs", "cbp", "susb", "bls", "qcew", "cex", "scrape",
                   "stated", "osm", "api", "fetched"}


def _origin_of(block: dict) -> str:
    for k in ("data_origin", "origin", "count_origin", "arpu_origin"):
        v = block.get(k)
        if v:
            return str(v).lower()
    return ""


def _shows_its_agency_operand(block: dict) -> bool:
    """True when the figure's formula names an agency AND a currency operand beside it.

    Deliberately strict about WHERE it looks: the formula is the string that reaches a
    reader (plan.py::_block keeps `calculation` and discards `source`), so a chain proved
    only in a field the pipeline throws away proves nothing. Mentioning the word "Census"
    is not showing your work — there has to be a number to check.
    """
    formula = str(block.get("formula") or block.get("calculation") or "")
    if not _AGENCIES.search(formula):
        return False
    return bool(re.search(r"\$\s?\d{1,3}(,\d{3})+", formula))


def d53_no_fabricated_agency_citation(r: dict, html: str | None) -> Finding:
    """A figure may not name a statistical agency that no tool actually called.

    The worst defect in this codebase: a wrong number can be checked, but a number wearing a
    real agency's name defeats checking. MEASURED across the 16-report corpus plus the live
    run -- 14 of 15 figures naming an agency carry no origin proving a call, and the live
    run's bottom-up TAM cites "Census ACS Mission District demographics & BLS QCEW NAICS
    722515" with data_origin=None, zero Census/BLS calls, no transcript and no API key. One
    figure is worse still: data_origin="llm" beside a source string claiming Census.

    Honest disclosure PASSES. "LLM estimate (UNSOURCED - validate vs US Census ACS)" names
    the agency as a check, not a source, and 6 corpus figures already phrase it that way.

    N/A when no figure names an agency at all."""
    ms = r.get("market_sizing") or {}
    blocks: list[tuple[str, dict]] = []
    for f in (ms.get("figures") or []):
        if isinstance(f, dict):
            blocks.append((str(f.get("label") or "figure"), f))
    for name in ("method_top_down", "method_bottom_up", "method_analog"):
        blk = (ms.get("tam") or {}).get(name)
        if isinstance(blk, dict):
            blocks.append((name, blk))

    claimed = [(lbl, b) for lbl, b in blocks
               if _AGENCIES.search(str(b.get("source") or ""))]
    if not claimed:
        return Finding(None, "no figure names a statistical agency")

    bad = []
    for lbl, b in claimed:
        src = str(b.get("source") or "")
        if _DISCLOSED.search(src):
            continue                        # says outright it is not sourced
        origin = _origin_of(b)
        if origin in _PROVEN_ORIGINS:
            continue
        if origin == "derived" and _shows_its_agency_operand(b):
            # THE THIRD CASE (#91). A figure can be derived arithmetic ON a genuine agency
            # fetch: the SOM anchor is $884,029 (Economic Census, really called) x 0.638 x
            # 1.141, and the product appears in no dataset. Both existing escapes would be
            # lies — "unsourced" throws away a real citation, and claiming `census` as the
            # origin is precisely the over-claiming this gate exists to stop.
            #
            # What makes it safe is not the label but the ARITHMETIC BEING VISIBLE: the
            # formula publishes the agency-attributed operand, so a reader opens the
            # citation, finds $884,029, and recomputes. That is the property D53 protects,
            # reached another way. A derived figure that names an agency and shows no
            # operand still fails below — the teeth are in _shows_its_agency_operand.
            continue
        agency = (_AGENCIES.search(src) or [""])[0]
        bad.append(f"{lbl} cites {agency!r} with origin="
                   f"{origin or 'NONE'}"
                   + (" (the pipeline recorded 'llm' and the prose claims the agency anyway)"
                      if origin == "llm" else ""))
    if bad:
        return Finding(False, "; ".join(bad[:3])
                       + (f" (+{len(bad)-3} more)" if len(bad) > 3 else ""))
    return Finding(True, f"{len(claimed)} agency citation(s), each with a proven origin "
                         "or an explicit unsourced disclosure")
