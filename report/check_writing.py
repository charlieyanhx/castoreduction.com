"""report/check_writing.py: the two synthesis gates, run on the writing after it is written.

THE VERIFIER RUNS BEFORE THE WRITER, BY DESIGN: the analyst report reads the findings and
says what they mean. So when verify_report ran, there was no synthesis for D62 and D63 to
read, and they answered "not applicable". MEASURED on the first real run with the writer
on (diag02, 2026-09-14): D62 sat in blind_ids, the gate that exists to catch an invented
number never saw the prose, and the gate's own tests could not tell, because they hand it
a result that already carries a synthesis.

So the writing gets checked here, after it is written, and TWO CALLERS need the same
check: plan, after the first write, and the workshop's rewrite, after every rewrite. It
lives in report/ rather than plan.py because a rewrite module importing plan would pull
the whole pipeline in to run one gate, and plan already reaches into report/ at call time
for the verifier and the renderer.

A D62 FAILURE WITHHOLDS THE WRITING, NOT THE REPORT: the facts passed their gates, and a
fifty-cent paragraph that failed its check is not a reason to withhold twenty-two
sections of research. The markdown moves aside, the page falls back to the narrative it
rendered before the synthesis existed, the drop is disclosed the way every other dropped
section is, and the verification record carries the finding as advisory with D62 and D63
moved from blind to answered. Nothing here raises: a gate that crashes leaves the writing
in place and says so.

RUN TWICE, IT DESCRIBES THE SECOND WRITING. A rewrite checks a result whose verification
record already answered D62 and D63 for the previous draft. The previous draft's findings
are dropped before the new ones are recorded, and `answered` counts each gate once, so a
report rewritten three times does not carry three D63 warnings about prose that is no
longer on the page.
"""
from __future__ import annotations

from logger import get

log = get("report.check_writing")

#: The gates that read the writing. Everything else in the rulebook read the facts.
WRITING_GATES: tuple[str, ...] = ("D62", "D63")


def _record(result: dict, verdicts: list[tuple[str, object]]) -> None:
    """Move the writing gates from blind to answered and replace their findings."""
    ver = result.get("verification")
    if not isinstance(ver, dict):
        return
    findings = ver.setdefault("findings", [])
    summary = ver.setdefault("summary", {})
    cov = summary.setdefault("coverage", {})
    blind = list(cov.get("blind_ids") or [])
    answered = int(cov.get("answered") or 0)
    stale = [f for f in findings
             if f.get("invariant") in WRITING_GATES and f.get("audit_class") == "synthesis"]
    for f in stale:
        findings.remove(f)
        if f.get("severity") == "advisory":
            summary["advisory"] = max(0, int(summary.get("advisory") or 0) - 1)
    for gid, f in verdicts:
        if f.ok is None:
            continue
        if gid in blind:
            blind.remove(gid)
            answered += 1
        if f.ok is False:
            findings.append({"invariant": gid, "severity": "advisory",
                             "detail": f.detail, "audit_class": "synthesis"})
            summary["advisory"] = int(summary.get("advisory") or 0) + 1
    cov["blind_ids"] = blind
    cov["answered"] = answered


def check_the_writing(result: dict) -> str | None:
    """Run D62 and D63 on result["synthesis"] and withhold the writing on a D62 fail.

    Returns the reason the writing was withheld, or None when it stands. Mutates the
    result the way plan always did: the markdown moves to `withheld_markdown`, the reason
    to `withheld_reason`, the drop is recorded under _dropped_outputs, and the
    verification record is updated. A caller that must not lose the previous writing
    hands in a copy (report.rewrite does).
    """
    syn = result.get("synthesis")
    if not isinstance(syn, dict) or not (syn.get("markdown") or "").strip():
        return None
    try:
        from gates.synthesis import (d62_synthesis_numbers_are_in_the_evidence_it_cites as d62,
                                     d63_synthesis_citations_resolve as d63)
        f62, f63 = d62(result, None), d63(result, None)
    except Exception as e:                                   # noqa: BLE001 - never block a run
        log.error("[check_writing] the writing could not be checked: %s: %s",
                  type(e).__name__, e)
        return None
    _record(result, [("D62", f62), ("D63", f63)])
    if f62.ok is not False:
        return None
    from orchestrator.steps import record_dropped_output
    syn["withheld_markdown"] = syn.pop("markdown")
    syn["withheld_reason"] = f62.detail
    record_dropped_output(result, "synthesis",
                          "the written analysis did not pass its citation check and is "
                          f"withheld; the evidence below stands on its own ({f62.detail})")
    log.warning("[check_writing] analyst report withheld by D62: %s", f62.detail[:200])
    return f62.detail
