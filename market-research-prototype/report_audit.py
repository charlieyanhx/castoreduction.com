"""report_audit.py — the deterministic answer to "is it actually fixed?".

WHY THIS EXISTS
---------------
Three consecutive line-by-line audits of shipped reports found 42, then 73, then 147
defects. Between each round the fixes were proven by unit tests that passed while the
next report broke anyway, because a unit test pins the ONE field the audit named and
the defect reappears one field over. 37 of the third round's findings were literal
recurrences of the second round's.

The missing instrument is this one: an executable check that reads a REAL artifact
(and, where relevant, its rendered HTML) and answers the same questions a human
auditor asks, in the same language, every run. It is the scoreboard — a fix is not
"done" because a unit test is green, it is done when the count of violations on a
regenerated report goes down and stays down.

DESIGN
------
Checks are CLASSES of defect, not instances: the audits showed every instance is a
member of a small family, and patching instances is what produced the recurrences.

  A. UNMEASURED   a value the run did not measure reaches a scoring prompt or the
                  page AS A NUMBER (`or 0` coercions, skeleton outputs, failed
                  fetches read as zero).
  B. UNITS        a comparison or ratio mixes per-seat with per-customer figures.
  C. COUNTS       the same population is described by different numbers on different
                  surfaces (roster vs clustering vs density vs prose).
  D. SURFACE      the rendered page asserts something the artifact contradicts, or a
                  section's data never reaches the template at all.
  E. PROVENANCE   a citation, source label, or origin claim the ledger cannot back.

USAGE
    .venv/bin/python report_audit.py <job_id> [--html path] [--json]
    from report_audit import audit_result; audit_result(result, html) -> [Violation]

Exit code is the number of violations (0 = clean), so it can gate a run.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class Violation:
    check: str          # e.g. "A.unmeasured_reaches_prompt"
    cls: str            # A | B | C | D | E
    severity: str       # P1 | P2 | P3
    detail: str
    evidence: str = ""
    where: str = ""     # artifact key or template surface

    def line(self) -> str:
        return f"[{self.severity}] {self.check}: {self.detail}" + (
            f"  ({self.evidence})" if self.evidence else "")


# ---------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------
def _num(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _seats_per_account(r: dict) -> float:
    """The founder's own seats-per-customer answer, or 1.0 when not stated."""
    for src in (((r.get("intake") or {}).get("facts") or {}).get("seats_per_account"),
                ((r.get("economics") or {}).get("_facts") or {}).get("seats_per_account")):
        m = re.search(r"(\d[\d,.]*)", str(src or ""))
        if m:
            try:
                return max(1.0, float(m.group(1).replace(",", "")))
            except ValueError:
                pass
    return 1.0


def _is_skeleton(v) -> bool:
    if not isinstance(v, dict):
        return False
    if v.get("_skeleton") or v.get("_skeleton_reason"):
        return True
    inner = v.get("icp_details")
    return bool(isinstance(inner, dict) and inner.get("_skeleton"))


# ---------------------------------------------------------------------------------
# A. an unmeasured value must never reach a prompt or the page as a number
# ---------------------------------------------------------------------------------
def check_unmeasured(r: dict, html: str = "") -> list[Violation]:
    out: list[Violation] = []
    via = r.get("viability") or {}
    scores = via.get("scores") or {}
    reasoning = " ".join(str((s or {}).get("reasoning") or "")
                         for s in scores.values() if isinstance(s, dict))
    reasoning += " " + str(via.get("summary") or "") + " " + str(via.get("headline") or "")

    # A1. a step that produced a skeleton must not be scored as a measured zero
    for key in ("customer_universe", "clustering", "market_sizing", "economics",
                "personas", "consumer_research", "differentiators"):
        if _is_skeleton(r.get(key)):
            if re.search(rf"\b0\b[^.]*{key.split('_')[0]}", reasoning, re.I):
                out.append(Violation(
                    "A.skeleton_scored_as_zero", "A", "P1",
                    f"{key} produced a skeleton (no measurement) but the viability "
                    f"reasoning cites it as a zero",
                    str((r.get(key) or {}).get("_skeleton_reason"))[:90], key))

    # A2. an UNAVAILABLE source must not read as measured absence
    flags = " ".join((r.get("validation") or {}).get("flags") or [])
    unavailable = "unavailable (fetch failed)" in flags or bool(
        ((r.get("multi_source_signal") or {}).get("unavailable")))
    reddit_403 = "403" in json.dumps(r.get("audiences_undecodable") or [])
    if (unavailable or reddit_403) and re.search(
            r"\bzero\b[^.]*\b(audience|confidence|voice|signal)", reasoning, re.I):
        out.append(Violation(
            "A.outage_scored_as_zero", "A", "P1",
            "a source the run recorded as UNAVAILABLE is described as a measured zero "
            "in the viability reasoning",
            re.search(r"[^.]*\bzero\b[^.]*", reasoning, re.I).group(0)[:120]
            if re.search(r"\bzero\b", reasoning, re.I) else "", "viability.scores"))

    # A3. no decoded audience at all, yet a confidence number is asserted
    if not (r.get("audiences") or r.get("audience")) and re.search(
            r"audience confidence[^.]*\b0(\.0+)?\b", reasoning, re.I):
        out.append(Violation(
            "A.absent_audience_scored", "A", "P2",
            "no brand decoded (audiences empty) but a 0 audience-confidence is scored "
            "as a finding rather than reported as not measured",
            "", "viability"))
    return out


# ---------------------------------------------------------------------------------
# B. per-seat and per-customer figures must never be compared
# ---------------------------------------------------------------------------------
def check_units(r: dict, html: str = "") -> list[Violation]:
    out: list[Violation] = []
    econ = r.get("economics") or {}
    if str(econ.get("pricing_unit") or "").lower() != "seat":
        return out                     # the mismatch class only exists for per-seat models
    seats = _seats_per_account(r)
    if seats <= 1.0:
        return out                     # one seat per customer: the units coincide
    clv = _num((econ.get("clv") or {}).get("clv_usd"))
    cac = _num((econ.get("unit_economics") or {}).get("typical_cac_usd"))
    if clv is None or cac is None:
        return out

    via = r.get("viability") or {}
    blob = " ".join([str(via.get("summary") or ""), str(via.get("headline") or "")] +
                    [str((s or {}).get("reasoning") or "")
                     for s in (via.get("scores") or {}).values() if isinstance(s, dict)] +
                    [json.dumps(r.get("four_ps") or {})])

    # B1. prose that puts the per-seat CLV against the per-customer CAC
    clv_pat = rf"{int(round(clv)):,}|{clv:.2f}|{int(round(clv))}"
    cac_pat = rf"{int(round(cac)):,}|{cac:.2f}|{int(round(cac))}"
    for m in re.finditer(r"[^.]*\b(?:cac|acquisition cost)\b[^.]*\.", blob, re.I):
        sent = m.group(0)
        if re.search(clv_pat, sent) and re.search(cac_pat, sent):
            out.append(Violation(
                "B.seat_clv_vs_customer_cac", "B", "P1",
                f"a per-SEAT CLV (${clv:,.0f}) is compared with a per-CUSTOMER CAC "
                f"(${cac:,.0f}) in one sentence; restated per customer the CLV is "
                f"${clv * seats:,.0f} ({seats:g} seats)",
                sent.strip()[:160], "viability/four_ps"))
            break

    # B2. the artifact itself must carry the restated figure so prose can quote it
    if not (econ.get("clv") or {}).get("clv_per_customer_usd"):
        out.append(Violation(
            "B.no_per_customer_clv_stored", "B", "P2",
            "economics stores CLV per seat only; nothing downstream can compare it "
            "with the per-customer CAC without recomputing (the recurrence path)",
            f"seats_per_account={seats:g}", "economics.clv"))
    return out


# ---------------------------------------------------------------------------------
# C. one population, one number
# ---------------------------------------------------------------------------------
def check_counts(r: dict, html: str = "") -> list[Violation]:
    out: list[Violation] = []
    disc = r.get("discover") or {}
    syn = disc.get("synthesis") or {}
    roster = syn.get("ranked_opportunities") or []
    n_roster = len(roster)
    density = disc.get("competitor_density")
    clustering = r.get("clustering") or {}
    n_cluster = (clustering.get("n_competitors")
                 or len(clustering.get("coordinates") or {}) or 0)

    if density is not None and n_roster and int(density) != n_roster:
        out.append(Violation(
            "C.density_vs_roster", "C", "P1",
            f"competitor_density={density} but the roster the report prints holds "
            f"{n_roster}", "", "discover"))

    # C1. the map must not describe a different population than the prose
    if n_roster and n_cluster and n_cluster < n_roster:
        frac = n_cluster / n_roster
        disclosed = bool(clustering.get("coverage_warning")) and (
            not html or "does not show" in html)
        if disclosed:
            out.append(Violation(
                "C.map_covers_a_disclosed_subset", "C", "P3",
                f"the map plots {n_cluster} of {n_roster} ({frac:.0%}) — disclosed to "
                f"the reader, so it misleads no one, but the coverage is thin",
                "", "clustering"))
        else:
            out.append(Violation(
                "C.map_covers_a_subset", "C", "P1" if frac < 0.5 else "P2",
                f"the competitive map plots {n_cluster} of {n_roster} competitors "
                f"({frac:.0%}) with NO disclosure — the picture describes a different "
                f"set than every count in the prose", "", "clustering"))

    # C2. every count stated in prose must exist as an artifact-derived number
    prose = json.dumps(r.get("four_ps") or {}) + " " + json.dumps(r.get("viability") or {})
    known = {n_roster, int(density) if density is not None else None, n_cluster,
             disc.get("active_signal_density"),
             len((disc.get("steps") or {}).get("signals") or [])}
    known = {k for k in known if isinstance(k, int) and k > 1}
    for m in re.finditer(r"\b(\d{1,3})\s+(?:direct\s+|active\s+)?competitors?\b", prose, re.I):
        n = int(m.group(1))
        if n > 1 and n not in known:
            out.append(Violation(
                "C.prose_count_unbacked", "C", "P2",
                f"prose states '{n} competitors' — no artifact figure equals it "
                f"(roster {n_roster}, density {density}, map {n_cluster})",
                m.group(0), "four_ps/viability"))
            break

    # C3. a signal count offered as data depth must describe the shown roster
    via_blob = json.dumps(r.get("viability") or {})
    pool = len((disc.get("steps") or {}).get("signals") or [])
    for m in re.finditer(r"\b(\d{1,3})\s+signals?\b", via_blob, re.I):
        n = int(m.group(1))
        if n and n != pool and n != n_roster:
            out.append(Violation(
                "C.signal_count_off_roster", "C", "P2",
                f"viability cites '{n} signals' — the pool holds {pool} records and "
                f"the roster {n_roster}; the number describes neither",
                m.group(0), "viability"))
            break
    return out


# ---------------------------------------------------------------------------------
# D. the page must not assert what the artifact denies
# ---------------------------------------------------------------------------------
_SECTION_KEYS = {
    "hn_signal": "HackerNews customer voice",
    "multi_source_signal": "dev-forum / trade-publication voice",
    "reddit_signal": "Reddit customer voice",
    "price_intel": "scraped price evidence",
    "whitespace": "whitespace map",
}


def check_surface(r: dict, html: str = "") -> list[Violation]:
    out: list[Violation] = []
    if not html:
        return out

    # D1. a produced section whose data the renderer never passes (silent discard)
    try:
        src = open("report/render_html.py").read()
    except OSError:
        src = ""
    if src:
        for key, label in _SECTION_KEYS.items():
            produced = bool(r.get(key))
            if produced and f"{key}=" not in src:
                out.append(Violation(
                    "D.section_never_reaches_template", "D", "P1",
                    f"{label} was produced ({key} present in the artifact) but the "
                    f"renderer never passes it — the section silently vanishes",
                    "", f"render_html/{key}"))

    # D2. "withheld" language with nothing withheld
    if re.search(r"figures withheld", html, re.I):
        sizing_ok = ((r.get("market_sizing") or {}).get("validation") or {}).get("passed")
        if sizing_ok is not False and (r.get("market_sizing") or {}).get("tam"):
            out.append(Violation(
                "D.withheld_claim_without_withhold", "D", "P2",
                "the page says figures are withheld while the sizing figures render",
                "", "templates/report.html"))

    # D3. a credential-dependent claim with no credential
    if re.search(r"with your Reddit OAuth app", html) and \
            (r.get("reddit_signal") or {}).get("tier") != "praw":
        out.append(Violation(
            "D.claims_a_credential_it_lacks", "D", "P2",
            "the page credits PRAW/OAuth while the run used the anonymous tier",
            str((r.get("reddit_signal") or {}).get("tier")), "templates/report.html"))

    # D4. raw internals reaching the reader
    for pat, what in ((r"cluster\d\(", "raw cluster ids"),
                      (r"\bwtp_x_market_size\b", "internal weight keys"),
                      (r"\$\\\\?ge\b|\\\\%", "raw LaTeX"),
                      (r"\bMiroFish\b", "internal jargon"),
                      (r"_skeleton", "internal skeleton markers")):
        if re.search(pat, html):
            out.append(Violation(
                "D.internal_leaks_to_reader", "D", "P3",
                f"{what} render in the deliverable", pat, "templates/report.html"))
    return out


# ---------------------------------------------------------------------------------
# E. a citation the ledger cannot back
# ---------------------------------------------------------------------------------
def check_provenance(r: dict, html: str = "") -> list[Violation]:
    out: list[Violation] = []
    tam = (r.get("market_sizing") or {}).get("tam") or {}
    for key in ("method_top_down", "method_bottom_up", "method_analog"):
        m = tam.get(key) or {}
        src = str(m.get("source") or "")
        origin = str(m.get("data_origin") or "")
        if src and origin == "llm" and re.search(
                r"\b(IDC|Gartner|Forrester|TechCrunch|PitchBook|CB Insights|IBISWorld)\b",
                src, re.I):
            if html and "model-asserted" not in html:
                out.append(Violation(
                    "E.named_house_on_llm_origin", "E", "P2",
                    f"{key} cites a named research house while its recorded origin is "
                    f"'llm', and the page carries no model-asserted label",
                    src[:90], f"market_sizing.tam.{key}"))
    return out


ALL_CHECKS = (check_unmeasured, check_units, check_counts, check_surface,
              check_provenance)


def audit_result(result: dict, html: str = "") -> list[Violation]:
    """Every class check against one artifact (+ its rendered page when available)."""
    out: list[Violation] = []
    for fn in ALL_CHECKS:
        try:
            out.extend(fn(result, html))
        except Exception as e:                                   # noqa: BLE001
            out.append(Violation(f"{fn.__name__}.CRASHED", "?", "P3",
                                 f"check crashed: {e}"))
    order = {"P1": 0, "P2": 1, "P3": 2}
    out.sort(key=lambda v: (order.get(v.severity, 3), v.cls))
    return out


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    job_id = argv[0]
    html = ""
    if "--html" in argv:
        html = open(argv[argv.index("--html") + 1]).read()
    import jobs
    j = jobs.get(job_id)
    if not j:
        print(f"no such job: {job_id}")
        return 1
    vs = audit_result(j.get("result") or {}, html)
    if "--json" in argv:
        print(json.dumps([asdict(v) for v in vs], indent=1))
    else:
        from collections import Counter
        c = Counter(v.severity for v in vs)
        print(f"{len(vs)} violation(s)  P1={c['P1']} P2={c['P2']} P3={c['P3']}")
        for v in vs:
            print("  " + v.line())
    return len(vs)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
