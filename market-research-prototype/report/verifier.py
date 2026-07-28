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
def _check_formula_reconciliation(r: dict, html: Optional[str]):
    """Does each sizing figure's stated formula compute to its stated value?

    The R2 case: "$30.6B * 15% * 15% = $4.59B" — the arithmetic gives $688.5M, a
    6.7x self-contradiction printed as a headline.
    """
    from skills.sizing.validate import safe_eval_formula
    out = []
    for fig in ((r.get("market_sizing") or {}).get("figures") or []):
        if not isinstance(fig, dict):
            continue
        val = fig.get("value_usd")
        if not isinstance(val, (int, float)) or isinstance(val, bool) or not val:
            continue
        computed = safe_eval_formula(str(fig.get("formula") or ""))
        if computed is None or computed == 0:
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


_DETERMINISTIC: list[tuple[str, Callable]] = [
    ("formula_reconciliation", _check_formula_reconciliation),
    ("uncited_claims", _check_uncited_claims),
    ("dangling_citations", _check_dangling_citations),
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
