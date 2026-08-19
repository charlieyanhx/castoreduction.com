"""report/pdf.py — print-grade PDF export (Wave 4, item 3).

The old `/jobs/{id}/report.pdf` fed the SCREEN html straight to Chromium's print():
what came out was a printout of a web page — no cover, no contents, unlabelled
charts, headings orphaned at the foot of pages. An institutional buyer forwards this
to an investment committee; it has to read as a document.

Three layers, all pure-string so they're testable without a renderer:

  number_figures()    — labels every chart in document order ("Figure 3 — Pricing"),
                        so body prose and reviewers can refer to one.
  toc_entries()       — the report's own h2 anchors, in order. Headings that never
                        had an id get a stable slug, otherwise the TOC link is dead.
  build_print_html()  — cover + contents + paged-media CSS around the body, minus
                        the screen-only furniture (feedback widget, nav).

render_pdf() then hands that to WeasyPrint, which is the only engine that resolves
`target-counter()` — i.e. that can print REAL page numbers next to TOC entries.
Chromium is the fallback: same document, TOC without page numbers, because a wrong
page number is worse than none.
"""
from __future__ import annotations

import html as html_mod
import logging
import os
import re
import sys
from typing import Optional

log = logging.getLogger(__name__)

_H2 = re.compile(r"<h2\b([^>]*)>(.*?)</h2>", re.I | re.S)
_ID_ATTR = re.compile(r'\bid\s*=\s*"([^"]*)"', re.I)
_SVG_BLOCK = re.compile(r"<svg\b", re.I)
_TAGS = re.compile(r"<[^>]+>")
_NO_PRINT_OPEN = re.compile(
    r'<(div|section|nav|aside|header|footer)\b[^>]*class="[^"]*\bno-print\b[^"]*"[^>]*>',
    re.I)


def _strip_no_print(html: str) -> str:
    """Remove `.no-print` containers INCLUDING their nested children.

    A non-greedy `<div ...>.*?</div>` closes on the first `</div>` it meets, which for
    the report's nested toolbar meant the wrapper vanished while its buttons survived —
    orphaned, no longer carrying the no-print class, and printed on page 3 of the PDF.
    So: match the real closing tag by depth.
    """
    out, i = [], 0
    while True:
        m = _NO_PRINT_OPEN.search(html, i)
        if not m:
            out.append(html[i:])
            return "".join(out)
        out.append(html[i:m.start()])
        tag = m.group(1).lower()
        depth, j = 1, m.end()
        tagre = re.compile(rf"</?{tag}\b[^>]*>", re.I)
        while depth and (tm := tagre.search(html, j)):
            depth += -1 if tm.group().startswith("</") else 1
            j = tm.end()
        i = j if not depth else len(html)   # unbalanced: drop to end rather than leak
_BODY_TAG = re.compile(r"<body\b[^>]*>(.*)</body>", re.I | re.S)
_STYLE_TAG = re.compile(r"<style\b[^>]*>(.*?)</style>", re.I | re.S)
_SCRIPT_TAG = re.compile(r"<script\b[^>]*>.*?</script>", re.I | re.S)


def _split_document(source: str) -> tuple[str, str]:
    """Split a report into (body fragment, screen CSS) — and defuse the screen CSS.

    The rendered report is a COMPLETE html document with its own `@page` rule. Nesting
    it whole inside the print shell let the inner rule win on cascade order: the cover
    kept the running footer, and the report's screen `body` box (max-width, 56px
    padding, paper background) stacked on top of the page margins. So: lift the body
    out, keep the visual CSS (tables, colours — the report has to still look like
    itself), and strip the `@page` blocks that belong to the print layer alone.
    """
    m = _BODY_TAG.search(source)
    if not m:
        return source, ""            # already a fragment
    styles = "\n".join(_STYLE_TAG.findall(source))
    # Drop @page blocks (incl. their nested @bottom-*/@top-* margin boxes).
    out, i = [], 0
    while True:
        j = styles.find("@page", i)
        if j < 0:
            out.append(styles[i:])
            break
        out.append(styles[i:j])
        depth, k = 0, styles.find("{", j)
        if k < 0:
            break
        for k in range(k, len(styles)):
            if styles[k] == "{":
                depth += 1
            elif styles[k] == "}":
                depth -= 1
                if depth == 0:
                    break
        i = k + 1
    return m.group(1), "".join(out)


def _text(fragment: str) -> str:
    return html_mod.unescape(_TAGS.sub("", fragment)).strip()


def _slug(title: str, n: int) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return f"sec-{s[:40]}" if s else f"sec-{n}"


def number_figures(body: str) -> tuple[str, list[dict]]:
    """Insert "Figure N — <nearest heading>" captions above each chart.

    Idempotent: a body that already carries castor figure captions is returned with
    the same numbering, so re-rendering never produces "Figure 1 Figure 1".
    """
    if 'class="fig-caption"' in body:
        caps = re.findall(r'<figcaption class="fig-caption">(.*?)</figcaption>', body, re.S)
        return body, [{"n": i + 1, "caption": _text(c)} for i, c in enumerate(caps)]

    # Heading positions, so each figure can borrow the section it sits under.
    heads = [(m.start(), _text(m.group(2))) for m in _H2.finditer(body)]
    figures: list[dict] = []
    out, cursor = [], 0
    for i, m in enumerate(_SVG_BLOCK.finditer(body)):
        pos = m.start()
        section = ""
        for hpos, htitle in heads:
            if hpos < pos:
                section = htitle
            else:
                break
        caption = f"Figure {i + 1} — {section}" if section else f"Figure {i + 1}"
        figures.append({"n": i + 1, "caption": caption})
        out.append(body[cursor:pos])
        out.append(f'<figcaption class="fig-caption">{html_mod.escape(caption)}</figcaption>')
        cursor = pos
    out.append(body[cursor:])
    return "".join(out), figures


def toc_entries(body: str) -> list[dict]:
    """Every h2 in document order as {id, title}. ids are read, never invented here —
    `build_print_html` is what stamps missing ones, so the entry and the anchor in the
    emitted document always agree."""
    entries = []
    for i, m in enumerate(_H2.finditer(body)):
        idm = _ID_ATTR.search(m.group(1) or "")
        title = _text(m.group(2))
        if not title:
            continue
        entries.append({"id": idm.group(1) if idm else "", "title": title})
    return entries


def _ensure_heading_ids(body: str) -> str:
    """Give every h2 an id so its TOC link resolves."""
    counter = {"n": 0}

    def _sub(m):
        counter["n"] += 1
        attrs, inner = m.group(1) or "", m.group(2)
        if _ID_ATTR.search(attrs):
            return m.group(0)
        return f'<h2{attrs} id="{_slug(_text(inner), counter["n"])}">{inner}</h2>'

    return _H2.sub(_sub, body)


_PRINT_CSS = """
@page {
  size: Letter;
  margin: 0.75in 0.7in 0.85in 0.7in;
  @bottom-center { content: counter(page); font: 9pt Georgia, serif; color: #6b7280; }
  @top-right { content: string(section); font: 8pt Georgia, serif; color: #9ca3af; }
}
@page :first { margin: 0; @bottom-center { content: none; } @top-right { content: none; } }

/* Neutralise the report's SCREEN body box — its max-width/padding/background are
   page-margin duplicates on paper. Declared here so the later cascade wins. */
body { font: 10.5pt/1.55 Georgia, "Times New Roman", serif; color: #1f2937;
       max-width: none; margin: 0; padding: 0; background: #fff; }

/* Cover owns page 1 alone; contents owns page 2. */
.pdf-cover { page-break-after: always; break-after: page;
  height: 9.6in; padding: 1.6in 1.1in 0; box-sizing: border-box; }
.pdf-cover .rule { width: 62px; height: 3px; background: #1f2937; margin: 0 0 30px; }
.pdf-cover .kicker { font: 600 9pt/1 Georgia, serif; letter-spacing: .18em;
  text-transform: uppercase; color: #6b7280; margin: 0 0 14px; }
.pdf-cover h1 { font-size: 30pt; line-height: 1.15; margin: 0 0 18px; font-weight: 600;
  color: #111827; border: none; padding: 0; }
.pdf-cover .sub { font-size: 12pt; color: #4b5563; margin: 0; }
.pdf-cover .meta { position: absolute; font-size: 9pt; color: #6b7280; margin-top: 3.2in; }
.pdf-cover .meta div { margin: 3px 0; }

.pdf-toc { page-break-after: always; break-after: page; }
/* The TOC is furniture, not a section: it must not consume a section number.
   (Measured: the first restyled PDF numbered CONTENTS "01".) */
.pdf-toc h2 { counter-increment: none; font-size: 13pt; letter-spacing: .1em; text-transform: uppercase;
  color: #111827; border-bottom: 1px solid #d1d5db; padding-bottom: 8px; }
.pdf-toc h2::before { content: none; }
.pdf-toc ol { list-style: none; padding: 0; margin: 18px 0 0; }
.pdf-toc li { margin: 0 0 9px; font-size: 10.5pt; }
.pdf-toc a { text-decoration: none; color: #1f2937; }
/* Leader dots + real page number. WeasyPrint resolves target-counter(); Chromium
   silently drops it, which is why the dots are on the ::after too. */
.pdf-toc a::after { content: " " leader('.') " " target-counter(attr(href), page);
  color: #9ca3af; }

/* Page-break discipline: a heading must never be the last thing on a page. */
h1, h2, h3, h4 { break-after: avoid; page-break-after: avoid;
                 break-inside: avoid; page-break-inside: avoid; }
h2 { string-set: section content(); }
/* ...but the TOC's own h2 must not become the running header for every page of
   front matter that follows it ("Contents" printed atop the viability score). */
.pdf-toc h2 { string-set: section ""; }
table, figure, svg, .fig-caption + * { break-inside: avoid; page-break-inside: avoid; }
tr, li, p { break-inside: avoid; page-break-inside: avoid; }
thead { display: table-header-group; }

.fig-caption { font: 600 8.5pt/1.4 Georgia, serif; letter-spacing: .06em;
  text-transform: uppercase; color: #6b7280; margin: 18px 0 6px; break-after: avoid; }
svg { max-width: 100%; height: auto; }
a { color: inherit; }
.no-print { display: none !important; }
"""



def _cover_date(generated_at) -> str:
    """A date a human reads. The cover printed "Prepared: 1786991389" — the HTML report
    formats the same value correctly, so only the PDF path was handing an epoch straight to
    the page. Anything already formatted is passed through untouched."""
    raw = str(generated_at or "").strip()
    if not raw:
        return ""
    try:
        import datetime as _dt
        return _dt.datetime.fromtimestamp(float(raw)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return raw


def build_print_html(body: str, meta: Optional[dict] = None) -> str:
    """Wrap a report body in a print document: cover, contents, paged-media CSS."""
    meta = meta or {}
    body, screen_css = _split_document(body)
    body = _SCRIPT_TAG.sub("", _strip_no_print(body))
    body = _ensure_heading_ids(body)
    body, _figs = number_figures(body)

    title = meta.get("title") or "Market Research Report"
    job_id = str(meta.get("job_id") or "")
    generated = _cover_date(meta.get("generated_at"))

    toc_html = "".join(
        f'<li><a href="#{e["id"]}">{html_mod.escape(e["title"])}</a></li>'
        for e in toc_entries(body) if e["id"])

    meta_rows = "".join(
        f"<div>{html_mod.escape(k)}: {html_mod.escape(v)}</div>"
        for k, v in (("Prepared", generated), ("Run", job_id[:8])) if v)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{html_mod.escape(title)}</title>
<style>{screen_css}</style>
<style>{_PRINT_CSS}</style></head>
<body>
<section class="pdf-cover">
  <div class="rule"></div>
  <p class="kicker">Market Research</p>
  <h1>{html_mod.escape(title)}</h1>
  <p class="sub">Category sizing, competitive landscape, pricing, and viability.</p>
  <div class="meta">{meta_rows}</div>
</section>
<nav class="pdf-toc"><h2 id="contents">Contents</h2><ol>{toc_html}</ol></nav>
{body}
</body></html>"""


def _weasyprint():
    """Import WeasyPrint, teaching it where Homebrew keeps pango/cairo on macOS.

    WeasyPrint loads those through ctypes at IMPORT time, and the venv doesn't
    inherit brew's lib path — so the import raises unless DYLD_FALLBACK_LIBRARY_PATH
    is set before it happens, not after.
    """
    if sys.platform == "darwin":
        brew = "/opt/homebrew/lib"
        cur = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        if os.path.isdir(brew) and brew not in cur.split(":"):
            os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = f"{cur}:{brew}".lstrip(":")
    import weasyprint  # noqa: F401  (raises OSError when system libs are missing)
    return weasyprint


def available_engine() -> Optional[str]:
    """Which renderer this machine can actually use: 'weasyprint', 'chromium', None."""
    try:
        _weasyprint()
        return "weasyprint"
    except Exception:
        pass
    try:
        import playwright.sync_api  # noqa: F401
        return "chromium"
    except Exception:
        return None


def render_pdf(body: str, meta: Optional[dict] = None,
               engine: Optional[str] = None) -> bytes:
    """Render a report body to PDF bytes. Raises RuntimeError if no engine exists."""
    doc = build_print_html(body, meta)
    engine = engine or available_engine()

    if engine == "weasyprint":
        try:
            wp = _weasyprint()
            return wp.HTML(string=doc, base_url=os.getcwd()).write_pdf()
        except Exception as e:
            # Fall through rather than fail: a Chromium PDF without TOC page numbers
            # still beats a 500 on the buyer's download.
            log.warning("weasyprint render failed (%s); falling back to chromium", e)
            engine = "chromium"

    if engine == "chromium":
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content(doc, wait_until="networkidle")
                return page.pdf(format="Letter", print_background=True,
                                margin={"top": "0.75in", "bottom": "0.85in",
                                        "left": "0.7in", "right": "0.7in"})
            finally:
                browser.close()

    raise RuntimeError("no PDF engine available (install weasyprint or playwright)")
