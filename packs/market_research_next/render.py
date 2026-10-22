"""Assemble the fixed report from accepted section results, without another model call."""
from __future__ import annotations

from html import escape
import json

from treadle.run import Report

from . import REPORT_ORDER
from .prep import REPORT_SECTIONS


def render_html(report: Report) -> str:
    """Escape all model/customer content and preserve missing sections visibly."""
    verdicts = {item["key"]: item for item in (report.sections or {}).get("sections", [])}
    parts = ["<!doctype html><html lang='en'><head><meta charset='utf-8'>",
             "<meta name='viewport' content='width=device-width,initial-scale=1'>",
             "<title>Venture research report</title><style>",
             "body{max-width:850px;margin:48px auto;padding:0 24px;color:#17242b;"
             "font:16px/1.6 system-ui,sans-serif}h1,h2{line-height:1.2}"
             "h2{margin-top:40px;border-top:1px solid #cbd5db;padding-top:24px}"
             ".notice{padding:16px;background:#f7f1df;border-left:3px solid #8b661d}"
             "p{white-space:pre-wrap}li{margin:6px 0}small{color:#52616b}"
             "@media print{body{margin:0;max-width:none}h2{break-after:avoid}}",
             "</style></head><body><h1>Venture research report</h1>"]
    if not report.ok:
        parts.append("<p class='notice'>This report contains unresolved findings or "
                     "incomplete research. Review the limitations before using its conclusions.</p>")
    prep = report.result.get("prep", {})
    attrs = prep.get("venture", {}).get("attributes", {})
    unknowns = [name.replace("_", " ") for name, item in attrs.items()
                if item.get("basis") == "unknown"]
    if unknowns:
        parts.append("<p><strong>Business details still unknown:</strong> "
                     + escape(", ".join(unknowns)) + ".</p>")
    business = prep.get("business_context")
    if isinstance(business, dict):
        limitations = _unique_text(business.get("limitations", []))
        if limitations:
            parts.append("<aside class='notice'><strong>Business description limitations</strong><ul>")
            parts.extend(f"<li>{escape(item)}</li>" for item in limitations)
            parts.append("</ul></aside>")
        readiness = business.get("readiness", {}).get("sections", {})
        questions = _unique_text([
            item.get("question")
            for key in REPORT_ORDER
            for item in readiness.get(key, {}).get("missing_requirements", [])
            if isinstance(item, dict)
        ])
        if questions:
            parts.append("<details><summary>Questions unanswered in the starting information</summary>"
                         "<p>Section findings may address these questions through research; "
                         "the starting business description did not establish their answers.</p><ul>")
            parts.extend(f"<li>{escape(question)}</li>" for question in questions)
            parts.append("</ul></details>")
    for key in REPORT_ORDER:
        parts.append(f'<section><h2 id="{key}">{escape(REPORT_SECTIONS[key]["label"])}</h2>')
        block = report.result.get(key)
        if not isinstance(block, dict):
            reason = verdicts.get(key, {}).get("reason") or "This section was not produced."
            parts.append(f"<p class='notice'>{escape(reason)}</p></section>")
            continue
        if block.get("research_note"):
            parts.append(f"<p><em>{escape(str(block['research_note']))}</em></p>")
        parts.append(f"<p>{escape(str(block.get('narrative', '')))}</p>")
        if block.get("metrics"):
            parts.append("<h3>Calculations and figures</h3><table><tbody>")
            for label, value in _metric_rows(block["metrics"]):
                parts.append(f"<tr><th scope='row'>{escape(label)}</th>"
                             f"<td>{escape(value)}</td></tr>")
            parts.append("</tbody></table>")
        for field, label in (("findings", "Findings"), ("assumptions", "Assumptions"),
                             ("uncertainties", "Uncertainties")):
            values = block.get(field, [])
            if values:
                parts.append(f"<h3>{label}</h3><ul>")
                parts.extend(f"<li>{escape(str(value))}</li>" for value in values)
                parts.append("</ul>")
        parts.append("</section>")
    issues = (report.execution or {}).get("issues", [])
    if issues:
        parts.append("<section><h2>Unresolved review findings</h2><ul>")
        parts.extend(f"<li>{escape(item['message'])}</li>" for item in issues)
        parts.append("</ul></section>")
    parts.append("</body></html>")
    return "".join(parts)


def _unique_text(values):
    return list(dict.fromkeys(value.strip() for value in values
                              if isinstance(value, str) and value.strip()))


def _metric_rows(metrics: dict, prefix: str = ""):
    for name, value in metrics.items():
        label = (prefix + " / " if prefix else "") + str(name).replace("_", " ")
        if isinstance(value, dict) and value:
            yield from _metric_rows(value, label)
        else:
            yield label, value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
