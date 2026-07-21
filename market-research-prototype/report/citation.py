"""report/citation.py — claim→source store + post-draft citation audit (Wave 4, item 2).

The pipeline already asks the LLM for `citations: [{id, source, claim}]` and ¹²³ markers
in the narrative prose. Nothing ever checked the prose HONOURED them, so two failures
shipped silently:

  * a sentence asserting a checkable fact ("grew 23% in 2024") with no marker at all —
    an uncited claim wearing the same authority as a sourced one;
  * a marker pointing at a citation id that was never emitted (a dangling ⁷) — a
    footnote that looks rigorous and resolves to nothing.

This module is the deterministic check: no LLM, no judgement about whether a claim is
TRUE — only whether it is ATTRIBUTED. A "claim" is a sentence containing something
falsifiable-looking: a year, a currency figure, or a percentage. Everything else is
positioning prose and is deliberately not policed (flagging "buyers value trust" would
train people to ignore the checker).
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

# Superscript digits used as citation markers in the narrative.
_SUP = "⁰¹²³⁴⁵⁶⁷⁸⁹"
_SUP_TO_DIGIT = {c: str(i) for i, c in enumerate(_SUP)}
_SUP_RUN = re.compile(f"[{_SUP}]+")

# What makes a sentence a checkable CLAIM.
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_MONEY = re.compile(r"\$\s?\d")
_PCT = re.compile(r"\d\s?%")

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _marker_ids(text: str) -> list[int]:
    """Citation ids referenced by superscript markers in `text`.

    A RUN of superscripts is one id (¹² = 12), the standard typographic convention —
    treating them as separate ids would make every two-digit footnote look dangling.
    """
    out = []
    for m in _SUP_RUN.finditer(text or ""):
        digits = "".join(_SUP_TO_DIGIT[c] for c in m.group())
        try:
            out.append(int(digits))
        except ValueError:
            continue
    return out


def is_claim(sentence: str) -> bool:
    """Does this sentence assert something checkable (a year, $ figure, or %)?"""
    s = sentence or ""
    return bool(_YEAR.search(s) or _MONEY.search(s) or _PCT.search(s))


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text or "") if s.strip()]


class CitationStore:
    """claim→source registry. Dedupes on (source, claim) so the same evidence cited
    twice keeps ONE id — otherwise a report grows a footnote list longer than its
    evidence base, which reads as rigour it doesn't have."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], int] = {}
        self._entries: list[dict] = []

    def register(self, source: str, claim: str = "") -> int:
        key = ((source or "").strip(), (claim or "").strip())
        if key in self._by_key:
            return self._by_key[key]
        cid = len(self._entries) + 1
        self._by_key[key] = cid
        self._entries.append({"id": cid, "source": key[0], "claim": key[1]})
        return cid

    def entries(self) -> list[dict]:
        return [dict(e) for e in self._entries]

    def source_for(self, cid: int) -> Optional[str]:
        for e in self._entries:
            if e["id"] == cid:
                return e["source"]
        return None

    @classmethod
    def from_list(cls, citations: Iterable[dict]) -> "CitationStore":
        """Adopt the LLM's emitted citation list, preserving its ids."""
        s = cls()
        for c in citations or []:
            if not isinstance(c, dict):
                continue
            try:
                cid = int(c.get("id"))
            except (TypeError, ValueError):
                continue
            src, clm = str(c.get("source") or ""), str(c.get("claim") or "")
            s._by_key[(src, clm)] = cid
            s._entries.append({"id": cid, "source": src, "claim": clm})
        return s


def _known_ids(citations: Iterable[dict]) -> set[int]:
    out = set()
    for c in citations or []:
        if isinstance(c, dict):
            try:
                out.add(int(c.get("id")))
            except (TypeError, ValueError):
                pass
    return out


def audit_narrative(text: str, citations: Iterable[dict] | None = None) -> dict:
    """Audit one narrative. Returns uncited claims, dangling markers, and counts."""
    known = _known_ids(citations)
    uncited, dangling, n_claims, n_cited = [], [], 0, 0
    for sent in sentences(text):
        ids = _marker_ids(sent)
        for i in ids:
            if i not in known and i not in dangling:
                dangling.append(i)
        if not is_claim(sent):
            continue
        n_claims += 1
        # Cited = carries a marker that actually resolves.
        if any(i in known for i in ids):
            n_cited += 1
        else:
            uncited.append({"sentence": sent, "markers": ids})
    return {"uncited": uncited, "dangling": dangling,
            "n_claims": n_claims, "n_cited": n_cited,
            "cited_pct": round(n_cited / n_claims * 100, 1) if n_claims else 0.0}


def fact_density(text: str, citations: Iterable[dict] | None = None) -> dict:
    """How many checkable claims does this prose make, and how many are attributed?"""
    a = audit_narrative(text, citations)
    return {k: a[k] for k in ("n_claims", "n_cited", "cited_pct")}


def audit_sections(sections: dict, citations: Iterable[dict] | None = None) -> dict:
    """Audit every 4Ps-style {section: {narrative}} block, plus a `_totals` roll-up.

    A section's OWN `citations` win over the pooled list. In the split-synthesis path
    each section is a separate LLM call numbering its footnotes from 1, so the pooled
    list holds four colliding id spaces — resolving a section's ⁷ against the pool
    would match some other section's ⁷ and call an unsourced claim sourced.
    """
    out: dict = {}
    tot_claims = tot_cited = 0
    for name, blk in (sections or {}).items():
        if not isinstance(blk, dict):
            continue
        own = blk.get("citations")
        a = audit_narrative(str(blk.get("narrative") or ""),
                            own if isinstance(own, list) and own else citations)
        out[name] = a
        tot_claims += a["n_claims"]
        tot_cited += a["n_cited"]
    out["_totals"] = {
        "n_claims": tot_claims, "n_cited": tot_cited,
        "cited_pct": round(tot_cited / tot_claims * 100, 1) if tot_claims else 0.0,
        "n_uncited": sum(len(v["uncited"]) for k, v in out.items() if k != "_totals"),
    }
    return out
