"""report/verifier.py — the pre-publication verification pass (Wave 6, item 1).

gates.py runs the 22 deterministic invariants as a CI-style sweep across a CORPUS: it
tells a developer whether a batch of reports is healthy. It has never run inside a
single run, before that run's report reaches a buyer.

This does. One entry point over one report, returning findings ranked by severity,
each naming the invariant that fired and what the buyer would otherwise have read.

Three layers, cheapest first:

  1. the deterministic invariants (reused from gates.py — one definition, not two);
  2. the checks that live closer to the prose: formula reconciliation and the
     citation audit;
  3. an OPTIONAL LLM review, off by default. The deterministic floor must hold with
     no model available at all — a verifier that silently degrades to "nothing found"
     when the API is down is worse than no verifier, because it reads as a pass.

Design rules:
  * A detector that raises becomes a FINDING, never an exception. The verifier's job
    is to report problems; crashing on one is the loudest possible way to hide the
    other twenty-one.
  * BLOCK findings make the report unpublishable; ADVISORY ones annotate it. An
    uncited claim is worth telling the reader about; it is not worth withholding a
    paid report over.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from logger import get

log = get("verifier")


class Severity:
    BLOCK = "block"        # would ship a wrong number or an unsupported claim
    ADVISORY = "advisory"  # worth disclosing, not worth withholding over
    INFO = "info"

    _RANK = {BLOCK: 0, ADVISORY: 1, INFO: 2}

    @classmethod
    def rank(cls, s: str) -> int:
        return cls._RANK.get(s, 9)


@dataclass
class Finding:
    invariant: str
    severity: str
    detail: str
    audit_class: str = ""


@dataclass
class Coverage:
    """How much of the rulebook could actually answer on this report.

    A Finding is only recorded when an invariant returns False, so a detector that DECLINED
    to answer produced output identical to one that passed: nothing. The pass then reported
    zero blocking issues and stamped the report publishable. Measured: run_plan verified with
    html=None, and 10 fail-severity invariants can only answer with a rendered page — so a
    tenth of the rulebook was silently absent from every verdict.

    "Nothing was found" and "nothing could be checked" must not look the same."""
    answered: int = 0
    not_applicable: int = 0
    blind_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"answered": self.answered, "not_applicable": self.not_applicable,
                "blind_ids": sorted(self.blind_ids)}


@dataclass
class VerificationResult:
    findings: list[Finding] = field(default_factory=list)
    coverage: Coverage = field(default_factory=Coverage)

    @property
    def publishable(self) -> bool:
        return not any(f.severity == Severity.BLOCK for f in self.findings)

    def summary(self) -> dict:
        out = {Severity.BLOCK: 0, Severity.ADVISORY: 0, Severity.INFO: 0}
        for f in self.findings:
            out[f.severity] = out.get(f.severity, 0) + 1
        out["publishable"] = self.publishable
        out["coverage"] = self.coverage.as_dict()
        return out


# --------------------------------------------------------------------------
# Layer 2 — checks that live closer to the prose than gates.py reaches.
# --------------------------------------------------------------------------
def _figure_refs(figures: list) -> dict:
    """TAM/SAM/SOM values from the figure list, for resolving symbolic formula references.

    A hyperlocal SAM's formula is literally "TAM x 35% serviceable" — the reference is the whole
    point of the formula, and without resolving it the figure cannot be checked at all.
    """
    refs: dict = {}
    for fig in figures or []:
        if not isinstance(fig, dict):
            continue
        val = fig.get("value_usd")
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        label = str(fig.get("label") or "")
        stem = label.split("_", 1)[0].upper()          # TAM_local -> TAM, SOM_demand -> SOM
        if stem in ("TAM", "SAM", "SOM") and stem not in refs:
            refs[stem] = float(val)
    return refs


def _reconcilable_figures(figures: list) -> list:
    """Labels of figures whose formula CANNOT be reconciled, i.e. silently skipped today.

    MEASURED: TAM_local and SAM_local returned None — and were therefore never checked — in
    run5, run6 AND run7. Two of the three figures in every hyperlocal report went unverified
    because "could not parse" was treated as "fine", which is the repo's dominant bug class
    living inside a verifier. Exposed as a function so a test can assert real reports are fully
    reconcilable rather than trusting the silence.
    """
    from skills.sizing.validate import safe_eval_formula
    refs = _figure_refs(figures)
    unreconcilable = []
    for fig in figures or []:
        if not isinstance(fig, dict):
            continue
        val = fig.get("value_usd")
        if not isinstance(val, (int, float)) or isinstance(val, bool) or not val:
            continue
        if _figure_computed(fig, refs) is None:
            unreconcilable.append(fig.get("label") or "?")
    return unreconcilable


def _figure_computed(fig: dict, refs: dict):
    """Recompute a figure, preferring its machine-readable `calc` over the human `formula`.

    WHY BOTH. `formula` is prose for a reader — "2,142 households within 1.5 km (7.1 km2
    catchment) x $3,945/hh/yr" — and contains numbers that are NOT factors (the 1.5 km radius),
    so a token-product parser cannot reconcile it and returned None. Measured: TAM_local was
    unreconciled, and therefore unchecked, in run5, run6 and run7 — the headline number of every
    hyperlocal report. Rather than make the reader-facing string machine-shaped, producers now
    also emit `calc`, the pure arithmetic. `formula` stays the sentence a human reads.
    """
    from skills.sizing.validate import safe_eval_formula
    calc = fig.get("calc")
    if calc:
        got = safe_eval_formula(str(calc), refs=refs)
        if got is not None:
            return got
    return safe_eval_formula(str(fig.get("formula") or ""), refs=refs)


def _check_formula_reconciliation(r: dict, html: Optional[str]):
    """Does each sizing figure's stated formula compute to its stated value?

    The R2 case: "$30.6B * 15% * 15% = $4.59B" — the arithmetic gives $688.5M, a
    6.7x self-contradiction printed as a headline.

    Symbolic references (TAM/SAM/SOM) are resolved from the sibling figures. Before that, a
    hyperlocal SAM ("TAM x 35% serviceable") was unparseable and skipped, while a correct
    SOM_demand was BLOCKED because the (competitors+1) divisor had been stripped as if it were
    a citation. The ratio band is unchanged on purpose — it was never the problem, and widening
    it would have hidden the 6.7x case this check exists for.
    """
    from skills.sizing.validate import safe_eval_formula
    figures = (r.get("market_sizing") or {}).get("figures") or []
    refs = _figure_refs(figures)
    out = []
    for fig in figures:
        if not isinstance(fig, dict):
            continue
        val = fig.get("value_usd")
        if not isinstance(val, (int, float)) or isinstance(val, bool) or not val:
            continue
        computed = _figure_computed(fig, refs)
        if computed is None or computed == 0:
            # Not a pass — an unreconcilable formula means the report states arithmetic nobody
            # verified. ADVISORY rather than BLOCK so a prose formula cannot stop a run, but it
            # is no longer invisible.
            out.append((Severity.ADVISORY,
                        f"{fig.get('label', '?')}: formula could not be reconciled "
                        f"({str(fig.get('formula') or '')[:70]!r}) — the figure is unverified"))
            continue
        ratio = computed / val
        if ratio > 2.5 or ratio < 0.4:
            out.append((Severity.BLOCK,
                        f"{fig.get('label', '?')}: formula computes {computed:,.0f} "
                        f"but the report prints {val:,.0f} ({ratio:.2g}x off)"))
    return out


def _check_uncited_claims(r: dict, html: Optional[str]):
    from report.citation import audit_sections
    fp = r.get("four_ps") or {}
    sections = {p: fp.get(p) for p in ("product", "price", "place", "promotion")
                if isinstance(fp.get(p), dict)}
    if not sections:
        return []
    audit = audit_sections(sections, fp.get("citations") or [])
    out = []
    for name, a in audit.items():
        for u in (a.get("uncited") or []) if name != "_totals" else []:
            out.append((Severity.ADVISORY,
                        f"{name}: unattributed claim — {u['sentence'][:120]}"))
    return out


def _check_dangling_citations(r: dict, html: Optional[str]):
    from report.citation import audit_sections
    fp = r.get("four_ps") or {}
    sections = {p: fp.get(p) for p in ("product", "price", "place", "promotion")
                if isinstance(fp.get(p), dict)}
    if not sections:
        return []
    audit = audit_sections(sections, fp.get("citations") or [])
    out = []
    for name, a in audit.items():
        if name == "_totals":
            continue
        for cid in (a.get("dangling") or []):
            out.append((Severity.BLOCK,
                        f"{name}: citation marker {cid} resolves to nothing — "
                        "the footnote looks sourced and is not"))
    return out


def _check_unsupported_footnotes(r: dict, html: Optional[str]):
    """A resolving marker on a number nothing measured — one level below dangling.

    _check_dangling_citations asks whether the marker RESOLVES; this asks whether the
    thing it resolves to supports the number standing next to it. MEASURED across runs
    12-15: 28 of 190 footnoted 4Ps sentences (15%) carried a figure absent from every
    deterministic input the section was handed — "150 drinks/day", "500 local workers",
    "$0.45 per click" — each sitting beside a real computed number whose authority it
    borrowed.

    ADVISORY, unlike dangling. This one reads prose with a regex, which is unsound in
    both directions: it cannot see a fabricated non-numeric attribute, and it will
    occasionally flag honest derived arithmetic. A check that can be wrong must annotate
    a report, never block one.
    """
    from report.claim_support import unsupported_citations
    out = []
    for row in unsupported_citations(r):
        out.append((Severity.ADVISORY,
                    f"{row['section']}: {row['number']:g} carries citation "
                    f"{row['citation_ids']} but appears in no source or computed input — "
                    f"\"{row['sentence'][:120]}\""))
    return out


_DETERMINISTIC: list[tuple[str, Callable]] = [
    ("formula_reconciliation", _check_formula_reconciliation),
    ("uncited_claims", _check_uncited_claims),
    ("dangling_citations", _check_dangling_citations),
    ("unsupported_footnotes", _check_unsupported_footnotes),
]


# --------------------------------------------------------------------------
# Layer 3 — optional LLM review. Never runs unless explicitly asked for.
# --------------------------------------------------------------------------
_REVIEW_SYSTEM = """You review a finished market-research report for an institutional
buyer. Report only defects you can point at IN THE TEXT: a claim contradicted by
another claim, a number that cannot follow from the numbers given, a recommendation
that does not follow from the evidence stated.

Do NOT report style, tone, or things you merely think are unlikely. If you cannot
quote the contradicting text, it is not a finding.

Return JSON: {"findings": [{"severity": "block"|"advisory", "detail": "<what a buyer
would read, and why it is wrong>"}]}"""


def _llm_review(r: dict, html: Optional[str]) -> list[Finding]:
    """Ask a model for defects the deterministic layer structurally cannot see.

    The 22 invariants read STRUCTURE; they cannot notice that a recommendation does
    not follow from its own evidence. This is where that lives — and why it is opt-in
    and never load-bearing.
    """
    from llm import call_json
    fp = r.get("four_ps") or {}
    prose = "\n\n".join(
        f"[{p}] {(fp.get(p) or {}).get('narrative', '')}"
        for p in ("product", "price", "place", "promotion") if isinstance(fp.get(p), dict))
    sizing = (r.get("market_sizing") or {})
    body = (f"SIZING: TAM {sizing.get('tam_usd')}, SAM {sizing.get('sam_usd')}, "
            f"SOM {sizing.get('som_usd')}\nMODEL: {r.get('business_model_kind')}\n\n{prose}")
    resp = call_json(system=_REVIEW_SYSTEM, user=body[:12000], max_tokens=1200) or {}
    out = []
    for f in (resp.get("findings") or []):
        if not isinstance(f, dict) or not f.get("detail"):
            continue
        sev = Severity.BLOCK if str(f.get("severity")) == Severity.BLOCK else Severity.ADVISORY
        out.append(Finding(invariant="llm_review", severity=sev,
                           detail=str(f["detail"])[:400], audit_class="llm"))
    return out


# --------------------------------------------------------------------------
def verify_report(result: dict, html: Optional[str] = None,
                  use_llm: bool = False) -> VerificationResult:
    """Verify ONE report before it ships. Never raises."""
    result = result or {}
    findings: list[Finding] = []

    # Layer 1 — the shared invariants. Imported from gates.py so there is one
    # definition of each rule, not a drifting copy.
    try:
        from gates import INVARIANTS
    except Exception as e:                       # pragma: no cover — import guard
        log.warning("[verifier] cannot load invariants: %s", e)
        INVARIANTS = []
    coverage = Coverage()
    for inv in INVARIANTS:
        try:
            f = inv.check(result, html)
        except Exception as e:
            findings.append(Finding(inv.id, Severity.ADVISORY,
                                    f"detector failed to run: {type(e).__name__}: {e}",
                                    inv.audit_class))
            coverage.not_applicable += 1
            coverage.blind_ids.append(inv.id)
            continue
        if f.ok is None:
            coverage.not_applicable += 1
            coverage.blind_ids.append(inv.id)
        else:
            coverage.answered += 1
        if f.ok is False:
            findings.append(Finding(
                inv.id,
                Severity.BLOCK if inv.severity == "fail" else Severity.ADVISORY,
                f.detail, inv.audit_class))

    # Layer 2 — prose-adjacent checks.
    for name, check in _DETERMINISTIC:
        try:
            for severity, detail in check(result, html) or []:
                findings.append(Finding(name, severity, detail, "wave4"))
        except Exception as e:
            # A crashing detector must not hide the other twenty-one.
            findings.append(Finding(name, Severity.ADVISORY,
                                    f"check failed to run: {type(e).__name__}: {e}",
                                    "verifier"))

    if use_llm:
        try:
            findings.extend(_llm_review(result, html))
        except Exception as e:
            log.warning("[verifier] llm review failed: %s", e)

    findings.sort(key=lambda f: (Severity.rank(f.severity), f.invariant))
    if html is None:
        log.info("[verifier] no rendered page supplied: %d/%d invariants could not answer",
                 coverage.not_applicable, coverage.answered + coverage.not_applicable)
    return VerificationResult(findings=findings, coverage=coverage)
