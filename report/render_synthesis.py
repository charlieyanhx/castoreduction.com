"""
THE SYNTHESIS IS THE FRONT HALF OF THE REPORT, AND EVERY NUMBER IN IT LINKS TO ITS SOURCE.

MEASURED 2026-09-12 on a real run: one frontier-model pass over the fact layer wrote a
1,820-word analyst report with 60 citations, every one a JSON key path in square brackets
like [market_sizing.som.mid], and surfaced four pipeline defects the narrative slots had
not. The fact layer is the product; this module turns the prose into a page without
letting the prose become markup.

Three things happen here, in order, and the order is the safety argument:

  1. ESCAPE FIRST. The report has no sanitiser for model text (the executive summary is
     rendered `| safe` and trusts the slot), so the markdown is escaped before the markdown
     package sees it: `&` and `<` become entities, which means no tag and no entity the
     model wrote can reach the page. `>` is left alone on purpose: it opens nothing in
     HTML and it is markdown's blockquote marker. Links, images and autolinks are also
     deregistered from the converter, so the model cannot write an href either. Every
     href on the page comes from step 3.
  2. MARKDOWN, tables enabled, headings demoted one level so the venture name stays the
     page's only h1 and the report's h2 numbering and the PDF's contents pick the
     synthesis title up as a section.
  3. CITATIONS. Every bracketed path becomes <a class="cite" href="#fact-<slug>"
     title="<path>">last segment</a>, and `cited_facts` builds the appendix those links
     land on: one row per cited path with the value resolved from the result, or the
     words "not in the evidence" when the path does not resolve. A link that lands on
     the value it names is the citation gate made visible; a link to nowhere would be a
     fabricated assurance.

The slug lowercases the path and turns every run of dots, brackets and underscores into
one hyphen, so `competitor_pricing.per_domain[0].median` becomes
`fact-competitor-pricing-per-domain-0-median`. Underscores go too, because gate D43 (no
dead in-page anchors) only recognises ids made of [a-z0-9-], and the cheapest way to have
the run's own verifier check these links is to write ids it can read.
"""
from __future__ import annotations

import html as html_mod
import json
import re
from typing import Callable, Optional

# A bracket is a citation when every comma-separated part is a key path: identifier
# segments joined by dots, with optional [n] indexes, and an optional ": suffix" the
# model sometimes appends (`[market_sizing.som.mid: "low"]`) that is dropped.
_BRACKET = re.compile(r"\[([^\[\]<>]*(?:\[\d+\][^\[\]<>]*)*)\]")
_PATH = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*|\[\d+\])*$")
_PART = re.compile(r"[A-Za-z_][A-Za-z_0-9]*|\[\d+\]")
_HEADING = re.compile(r"<(/?)h([1-5])\b", re.I)

# How much of a nested value the appendix shows before cutting it. The row is there so
# a reader can check the number the prose used; a whole competitor roster is not that.
_VALUE_CHARS = 300


def split_citation(body: str) -> Optional[list[str]]:
    """The paths inside one bracket, or None when the bracket is not a citation."""
    paths = []
    for raw in body.split(","):
        path = raw.split(":", 1)[0].strip()
        if not _PATH.match(path):
            return None
        paths.append(path)
    return paths or None


def slug(path: str) -> str:
    """The in-page id a citation of `path` links to."""
    return "fact-" + re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")


def resolve(path: str, facts: dict) -> tuple[bool, object]:
    """Walk `path` into the fact layer: (found, value). Missing anywhere means not found."""
    node: object = facts
    for part in _PART.findall(path):
        if part.startswith("["):
            index = int(part[1:-1])
            if not isinstance(node, list) or index >= len(node):
                return False, None
            node = node[index]
        else:
            if not isinstance(node, dict) or part not in node:
                return False, None
            node = node[part]
    return True, node


def citations(markdown_text: str) -> list[str]:
    """Every cited path in the prose, in order of first appearance, without repeats."""
    seen: list[str] = []
    for m in _BRACKET.finditer(markdown_text or ""):
        for path in split_citation(m.group(1)) or ():
            if path not in seen:
                seen.append(path)
    return seen


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;")


def _converter():
    import markdown
    md = markdown.Markdown(extensions=["tables"])
    # No href the model wrote reaches the page: the only links are the citations.
    for name in ("link", "image_link", "reference", "image_reference",
                 "short_reference", "short_image_ref", "autolink", "automail", "html"):
        md.inlinePatterns.deregister(name, strict=False)
    md.parser.blockprocessors.deregister("reference", strict=False)
    # Redundant with the escape above, kept so the escape is not the only thing standing.
    md.preprocessors.deregister("html_block", strict=False)
    return md


def _demote(html: str) -> str:
    return _HEADING.sub(lambda m: f"<{m.group(1)}h{int(m.group(2)) + 1}", html)


def _link(m: re.Match) -> str:
    paths = split_citation(m.group(1))
    if not paths:
        return m.group(0)
    return ", ".join(
        f'<a class="cite" href="#{slug(p)}" title="{html_mod.escape(p, quote=True)}">'
        f"{html_mod.escape(p.rsplit('.', 1)[-1])}</a>"
        for p in paths)


def render_synthesis_html(markdown_text: str) -> str:
    """Escaped, converted, demoted, cited. Safe to mark `safe` in the template."""
    html = _converter().convert(_escape(markdown_text or ""))
    return _BRACKET.sub(_link, _demote(html))


def _value_text(found: bool, value: object) -> str:
    if not found:
        return "not in the evidence"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if isinstance(value, int):
        return str(value)
    text = (value if isinstance(value, str)
            else json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str))
    return text if len(text) <= _VALUE_CHARS else text[:_VALUE_CHARS] + " ..."


def cited_facts(markdown_text: str, result: dict,
                label: Optional[Callable[[str], str]] = None) -> list[dict]:
    """The appendix the citation links land on, grouped by top-level section.

    Each group carries the section's own slug, and each fact its path's slug; a bare
    citation of a section (`[audiences_undecodable]`) shares the group's slug, and the
    template gives such a row no id of its own so the page never carries two.
    """
    label = label or (lambda key: key.replace("_", " ").title())
    groups: dict[str, dict] = {}
    for path in citations(markdown_text):
        key = _PART.match(path).group(0)
        group = groups.setdefault(key, {"key": key, "label": label(key),
                                        "slug": slug(key), "facts": []})
        found, value = resolve(path, result)
        group["facts"].append({"path": path, "slug": slug(path), "found": found,
                               "text": _value_text(found, value)})
    return list(groups.values())


def provenance(synthesis: dict) -> dict:
    """What the strip under the prose says: model, style, dollars, seconds.

    The producing section owns these names; this reads the ledger's spelling (`usd`)
    first and the spelled-out `cost_usd` second, and prints nothing for a field the
    section did not record rather than a placeholder that reads like a measurement.
    """
    usd = synthesis.get("usd", synthesis.get("cost_usd"))
    seconds = synthesis.get("seconds", synthesis.get("duration_seconds"))
    return {
        "model": str(synthesis.get("model") or ""),
        "style": str(synthesis.get("style") or ""),
        "usd": (f"{float(usd):.2f}" if isinstance(usd, (int, float)) else ""),
        "seconds": (f"{float(seconds):.0f}" if isinstance(seconds, (int, float)) else ""),
    }


def synthesis_view(result: dict,
                   label: Optional[Callable[[str], str]] = None) -> Optional[dict]:
    """Everything the template needs, or None when the run carries no synthesis prose.

    None is the whole fallback: the template renders exactly what it rendered before the
    synthesis existed, and a test pins that.
    """
    synthesis = (result or {}).get("synthesis")
    if not isinstance(synthesis, dict):
        return None
    markdown_text = synthesis.get("markdown")
    if not isinstance(markdown_text, str) or not markdown_text.strip():
        return None
    paths = citations(markdown_text)
    return {
        "html": render_synthesis_html(markdown_text),
        "provenance": provenance(synthesis),
        "facts": cited_facts(markdown_text, result, label),
        "n_facts": len(paths),
    }
