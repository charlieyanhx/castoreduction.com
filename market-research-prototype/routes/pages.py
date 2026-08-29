"""routes/pages.py — the surfaces a person looks at, rather than calls.

The workspace and login shells, the static console pages, the rendered docs tree, and the
two generated dashboards (/architecture and /benchmarks, ~260 lines of HTML between them).
Also /healthz and /usage: not pages, but the same shape of endpoint, a read with no job and
no owner behind it.

WHY THIS GROUP CAN MOVE AND OTHERS CANNOT. Nothing here reads or writes a job, so none of
it touches the ownership choke point that routes/deps.py explains has to stay in api.py.
The single exception is `index`, which asks whether the visitor is signed in; it imports
that answer INSIDE the function, on purpose, so this module does not import api at import
time (which would be a cycle) and so `patch.object(api, "_session_owner", ...)` still
reaches it.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse)

from logger import get
from llm import get_usage
from routes.deps import APP_VERSION, DOCS_DIR, WEB_DIR, _NO_CACHE, _stamped_html

log = get("api")

router = APIRouter()

def _render_docs_index() -> str:
    """List all markdown files in docs/ as a clickable index."""
    if not DOCS_DIR.exists():
        return "<p>No docs directory found.</p>"
    items = []
    for md in sorted(DOCS_DIR.rglob("*.md")):
        rel = md.relative_to(DOCS_DIR).as_posix()
        depth = rel.count("/")
        indent = "  " * depth
        items.append(f'{indent}<li><a href="/docs/{rel}">{rel}</a></li>')
    body = "\n".join(items)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"/><title>Castor Research — Docs</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 760px; margin: 40px auto; padding: 0 20px; color: #1f2937; }}
  h1 {{ border-bottom: 1px solid #e5e7eb; padding-bottom: 8px; }}
  a {{ color: #2563eb; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  ul {{ list-style: none; padding: 0; }}
  li {{ padding: 6px 0; font-size: 11pt; font-family: ui-monospace, monospace; }}
  .nav {{ background: #f3f4f6; padding: 12px 16px; border-radius: 6px; margin: 16px 0; }}
</style>
</head><body>
<h1>Castor Research — Documentation</h1>
<div class="nav">
  Two branches: <strong>method/</strong> (how the system works) · <strong>process/</strong> (how we got here).<br/>
  Start with <a href="/docs/README.md">docs/README.md</a> for the reading order.
</div>
<ul>
{body}
</ul>
</body></html>
"""


@router.get("/")
def index():
    """The workspace. In production an unauthenticated visitor is sent to the login page
    instead, because a 401 from the workspace's first fetch is a dead end."""
    # Imported HERE, not at module scope: api imports this module, so importing api back
    # at the top would be a cycle. Resolving it per call also keeps the test seam, since
    # patch.object(api, "_session_owner", ...) is looked up at the moment it is used.
    from api import _session_owner
    # A 401 from the workspace's first fetch is a dead end for a real customer; send them
    # somewhere they can act. Local installs keep going straight in.
    if (os.environ.get("CASTOR_ENV", "").lower() == "production"
            and not _session_owner()):
        return RedirectResponse("/login", status_code=303)
    ws = WEB_DIR / "workspace.html"
    if ws.exists():
        return _stamped_html(ws)
    f = WEB_DIR / "index.html"
    if f.exists():
        return FileResponse(f, headers=_NO_CACHE)
    return JSONResponse({"ok": True, "hint": "no web/workspace.html found"})


@router.get("/login", response_class=HTMLResponse)
def login_page():
    """Sign in / sign up. #94 shipped the endpoints and no screen, which made the product
    usable only by someone holding the route list and a curl command."""
    f = WEB_DIR / "login.html"
    if not f.exists():
        raise HTTPException(status_code=404, detail="login page not built")
    return FileResponse(f, headers=_NO_CACHE)


@router.get("/home", response_class=HTMLResponse)
def home_landing():
    """The previous marketing/chat landing, kept available at /home."""
    f = WEB_DIR / "index.html"
    if not f.exists():
        raise HTTPException(status_code=404, detail="home not found")
    return FileResponse(f, headers=_NO_CACHE)


@router.get("/workspace", response_class=HTMLResponse)
def workspace_page():
    """The Manus-parity 3-zone agentic workspace (cycle34)."""
    f = WEB_DIR / "workspace.html"
    if not f.exists():
        raise HTTPException(status_code=404, detail="workspace not built")
    return _stamped_html(f)


@router.get("/workspace.js")
def workspace_js():
    """The workspace bundle, served no-cache so a deploy is picked up on reload."""
    f = WEB_DIR / "workspace.js"
    if not f.exists():
        raise HTTPException(status_code=404, detail="workspace.js not found")
    return FileResponse(f, media_type="application/javascript",
                        headers=_NO_CACHE)


@router.get("/dashboard.html", response_class=HTMLResponse)
def dashboard_page():
    """The dashboard page, if this install has one built."""
    f = WEB_DIR / "dashboard.html"
    if not f.exists():
        raise HTTPException(status_code=404, detail="dashboard not built")
    return FileResponse(f, headers=_NO_CACHE)


@router.get("/progress.html", response_class=HTMLResponse)
def progress_page():
    """The run-progress page, if this install has one built."""
    f = WEB_DIR / "progress.html"
    if not f.exists():
        raise HTTPException(status_code=404, detail="progress page not built")
    return FileResponse(f, headers=_NO_CACHE)


@router.get("/survey", response_class=HTMLResponse)
def survey_page():
    """FORM MODE's surface: the same deterministic question plan the chat walks one turn
    at a time, rendered all at once as a survey.

    The JS is inlined deliberately. _stamped_html's version-stamping regex is hardcoded to
    workspace.js, so an external survey.js would be served unstamped and a browser holding
    half an old bundle is exactly the failure _asset_version exists to prevent."""
    f = WEB_DIR / "survey.html"
    if not f.exists():
        raise HTTPException(status_code=404, detail="survey not built")
    return FileResponse(f, headers=_NO_CACHE)


@router.get("/survey.js")
def survey_js():
    """Served from its own route rather than the /web static mount so it carries
    _NO_CACHE, the same reason workspace.js has one. _stamped_html's version-stamping
    regex is hardcoded to workspace.js and never sees this file, so the no-cache header
    is what stops a browser holding half an old bundle."""
    f = WEB_DIR / "survey.js"
    if not f.exists():
        raise HTTPException(status_code=404, detail="survey.js not found")
    return FileResponse(f, media_type="application/javascript", headers=_NO_CACHE)


@router.get("/healthz")
def healthz():
    """Liveness probe. Answers without touching the DB or any backend, so it stays
    truthful when those are the thing that is broken."""
    return {"ok": True, "version": APP_VERSION}


@router.get("/usage")
def usage():
    """Cumulative LLM token spend for this process, as the cost tracker sees it."""
    return get_usage().summary()


# ---------------------------------------------------------------------------
# Benchmark dashboard — heatmap of all cases × dimensions
# Added cycle31-r3. Reads the most-recent /tmp/bench_*.json files and renders
# a single-page scannable view. No LLM calls; pure HTML.
# ---------------------------------------------------------------------------
@router.get("/architecture", response_class=HTMLResponse)
def architecture_dashboard():
    """cycle32 Phase 6: live dashboard of registered tools, skills, and active config.
    Lets agent/UI/operator see the full architecture at a glance — no code reading required."""
    import tools as tools_mod
    import skills as skills_mod
    import config as config_mod

    tools_by_cat = {}
    for t in tools_mod.list_tools():
        tools_by_cat.setdefault(t.category, []).append(t)

    skills_by_produces = {}
    for s in skills_mod.list_skills():
        skills_by_produces.setdefault(s.produces, []).append(s)

    profile = config_mod.profile_name()
    profiles = config_mod.available_profiles()
    cfg = config_mod.get_all()

    def _esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # Render tools by category
    tool_blocks = []
    for cat in sorted(tools_by_cat):
        rows = []
        for t in sorted(tools_by_cat[cat], key=lambda x: x.name):
            rows.append(
                f'<tr><td><code>{_esc(t.name)}</code></td>'
                f'<td><code style="font-size:9pt;color:#6b7280">{_esc(t.signature)}</code></td>'
                f'<td style="font-size:9pt;color:#4b5563">{_esc(t.docstring.split(chr(10))[0])}</td></tr>'
            )
        tool_blocks.append(
            f'<h3>{_esc(cat)} <span style="font-size:9pt;color:#9ca3af">({len(rows)} tools)</span></h3>'
            f'<table style="width:100%;border-collapse:collapse;font-size:10pt;margin-bottom:18px">'
            f'<thead style="background:#f9fafb"><tr><th style="text-align:left;padding:6px 10px;border:1px solid #e5e7eb">Name</th><th style="text-align:left;padding:6px 10px;border:1px solid #e5e7eb">Signature</th><th style="text-align:left;padding:6px 10px;border:1px solid #e5e7eb">Description</th></tr></thead>'
            f'<tbody>' + "".join(f'<tr style="border-bottom:1px solid #e5e7eb">{r[4:-5]}' for r in rows) + '</tbody></table>'
        )

    # Render skills by produces
    skill_blocks = []
    for prod in sorted(skills_by_produces):
        rows = []
        for s in sorted(skills_by_produces[prod], key=lambda x: x.name):
            consumes_str = ", ".join(s.consumes) if s.consumes else "—"
            rows.append(
                f'<tr style="border-bottom:1px solid #e5e7eb">'
                f'<td style="padding:6px 10px"><code>{_esc(s.name)}</code></td>'
                f'<td style="padding:6px 10px;font-size:9pt;color:#6b7280"><code>{_esc(s.signature)}</code></td>'
                f'<td style="padding:6px 10px;font-size:9pt;color:#7c3aed">{_esc(consumes_str)}</td>'
                f'<td style="padding:6px 10px;font-size:9pt;color:#4b5563">{_esc(s.docstring.split(chr(10))[0])}</td>'
                f'</tr>'
            )
        skill_blocks.append(
            f'<h3>produces: <code style="background:#dbeafe;padding:2px 8px;border-radius:3px">{_esc(prod)}</code> '
            f'<span style="font-size:9pt;color:#9ca3af">({len(rows)} skill{"s" if len(rows)!=1 else ""})</span></h3>'
            f'<table style="width:100%;border-collapse:collapse;font-size:10pt;margin-bottom:18px">'
            f'<thead style="background:#f9fafb"><tr>'
            f'<th style="text-align:left;padding:6px 10px;border:1px solid #e5e7eb">Name</th>'
            f'<th style="text-align:left;padding:6px 10px;border:1px solid #e5e7eb">Signature</th>'
            f'<th style="text-align:left;padding:6px 10px;border:1px solid #e5e7eb">Consumes</th>'
            f'<th style="text-align:left;padding:6px 10px;border:1px solid #e5e7eb">Description</th>'
            f'</tr></thead><tbody>' + "".join(rows) + '</tbody></table>'
        )

    # Render config (top-level keys + values)
    cfg_rows = []
    for k in sorted(cfg.keys()):
        v = cfg[k]
        if isinstance(v, dict):
            inner = "<br/>".join(f"<span style='color:#6b7280'>{_esc(kk)}:</span> <code>{_esc(str(vv))}</code>" for kk, vv in v.items())
            cfg_rows.append(f'<tr><td style="padding:6px 10px;font-weight:600;vertical-align:top"><code>{_esc(k)}</code></td><td style="padding:6px 10px;font-size:9pt">{inner}</td></tr>')
        else:
            cfg_rows.append(f'<tr><td style="padding:6px 10px;font-weight:600"><code>{_esc(k)}</code></td><td style="padding:6px 10px"><code>{_esc(str(v))}</code></td></tr>')

    profile_links = " · ".join(
        f'<code style="background:{"#dbeafe" if p == profile else "#f3f4f6"};padding:2px 8px;border-radius:3px">{_esc(p)}</code>'
        for p in profiles
    )

    return HTMLResponse(f"""<!doctype html>
<html><head><meta charset="utf-8"/><title>Castor Architecture — cycle32</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 1200px; margin: 30px auto; padding: 0 24px; color: #1f2937; line-height: 1.5; }}
  h1 {{ border-bottom: 1px solid #e5e7eb; padding-bottom: 8px; }}
  h2 {{ margin-top: 36px; padding-top: 12px; border-top: 1px solid #e5e7eb; }}
  h3 {{ margin-top: 18px; }}
  table {{ border-collapse: collapse; }}
  table th, table td {{ border: 1px solid #e5e7eb; }}
  code {{ font-size: 90%; }}
  .summary-box {{ background: #f9fafb; border: 1px solid #e5e7eb; padding: 14px 18px; border-radius: 6px; margin: 14px 0; }}
  .nav {{ font-size: 9pt; color: #6b7280; margin-bottom: 24px; }}
  a {{ color: #2563eb; text-decoration: none; }}
</style></head><body>
<div class="nav"><a href="/">app home</a> · <a href="/benchmarks">benchmark dashboard</a> · <a href="/docs">docs</a> · <a href="/api/tools">/api/tools (json)</a> · <a href="/api/skills">/api/skills (json)</a></div>

<h1>Architecture (cycle32 — registry pattern)</h1>

<div class="summary-box">
  <strong>{len(tools_mod.TOOL_REGISTRY)} tools</strong> across {len(tools_by_cat)} categories ·
  <strong>{len(skills_mod.SKILL_REGISTRY)} skills</strong> producing {len(skills_by_produces)} report sections ·
  <strong>active profile:</strong> {profile_links}
  <br/>
  <span style="font-size:9pt;color:#6b7280;margin-top:6px;display:inline-block">
    Adding a new tool/skill is now strictly additive — 1 file, no modification of orchestrator code.
  </span>
</div>

<h2>Tools <span style="font-size:11pt;color:#6b7280;font-weight:400">— atomic capability primitives, return Evidence envelopes</span></h2>
{"".join(tool_blocks)}

<h2>Skills <span style="font-size:11pt;color:#6b7280;font-weight:400">— compose tools to produce a report section</span></h2>
{"".join(skill_blocks)}

<h2>Active config <span style="font-size:11pt;color:#6b7280;font-weight:400">— profile: <code>{_esc(profile)}</code></span></h2>
<p style="font-size:10pt;color:#6b7280">Switch profile via <code>PIPELINE_PROFILE=quick</code> env var. Available: {profile_links}</p>
<table style="width:100%;border-collapse:collapse;font-size:10pt">
<thead style="background:#f9fafb"><tr><th style="text-align:left;padding:6px 10px;border:1px solid #e5e7eb">Namespace</th><th style="text-align:left;padding:6px 10px;border:1px solid #e5e7eb">Settings</th></tr></thead>
<tbody>{"".join(cfg_rows)}</tbody>
</table>
</body></html>
""")


@router.get("/benchmarks", response_class=HTMLResponse)
def benchmarks_dashboard():
    """Scan /tmp/bench_*.json files, build a heatmap view of all known cases."""
    import glob
    import json as _json

    # Load every bench dashboard file we know about
    rows_by_case: dict = {}
    for path in sorted(glob.glob("/tmp/bench_*.json")):
        if path.endswith(".samples"):
            continue
        try:
            data = _json.loads(Path(path).read_text())
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for row in data:
            case = row.get("case")
            if not case:
                continue
            # Extract score
            grade_obj = row.get("grade") or {}
            score = grade_obj.get("final_score") or row.get("mean_score")
            stdev = row.get("stdev_score")
            n = row.get("n_samples", 1)
            dims = grade_obj.get("dimensions") or row.get("dimensions_aggregated") or {}
            # Keep most-recent or highest-sample-count entry
            existing = rows_by_case.get(case)
            if existing and existing.get("n_samples", 1) >= n and not stdev:
                continue
            if score is None:
                continue
            rows_by_case[case] = {
                "case": case, "score": score, "stdev": stdev, "n_samples": n,
                "dims": dims, "source_file": Path(path).name,
            }

    if not rows_by_case:
        return HTMLResponse("<h1>No bench dashboards found in /tmp/bench_*.json</h1>")

    # Tier classification based on filename heuristics
    TIER = {
        "sleep_loop": 0, "devtools_apm": 0, "hr_smb": 0,
        "cyber_soc": 1, "restaurant_pos": 1, "sales_engagement": 1,
        "healthcare_ehr": 1, "construction_tech": 1,
        "fintech_b2b": 2, "edtech_corporate": 2, "insurance_smb": 2,
    }
    for c in rows_by_case:
        if c.startswith("tier3_"):
            TIER[c] = 3

    cases_sorted = sorted(rows_by_case.values(), key=lambda r: (TIER.get(r["case"], 9), r["case"]))
    # All dimension keys
    DIM_KEYS = ["coverage", "tam_accuracy", "cagr_accuracy", "competitor_recall",
                "icp_alignment", "method_depth", "source_breadth", "differentiators",
                "personas", "pricing_psm", "unit_economics", "segment_authenticity",
                "citation_grounding", "validation_honesty", "growth_scenarios", "prose_quality"]

    def cell_color(score) -> str:
        """Background colour for a score cell: green good, red bad, grey unknown."""
        if score is None: return "#f3f4f6"
        if score >= 90: return "#bbf7d0"
        if score >= 75: return "#fde68a"
        if score >= 50: return "#fed7aa"
        return "#fecaca"

    def cell_score_only(d) -> str:
        if not isinstance(d, dict): return "—"
        return str(d.get("score", "—"))

    head_dims = "".join(f'<th style="padding:4px 6px;font-size:9pt;writing-mode:vertical-rl;border:1px solid #e5e7eb">{d[:14]}</th>' for d in DIM_KEYS)
    rows_html = []
    for r in cases_sorted:
        case = r["case"]
        tier = TIER.get(case, "?")
        score = r["score"]
        stdev = r.get("stdev")
        n = r.get("n_samples", 1)
        score_cell = f'<strong>{score:.1f}</strong>' + (f' <span style="font-size:8pt;color:#6b7280">±{stdev}</span>' if stdev else "") + f' <span style="font-size:8pt;color:#9ca3af">({n}×)</span>'
        dim_cells = ""
        for dk in DIM_KEYS:
            d = (r["dims"] or {}).get(dk) or {}
            s = d.get("score") if isinstance(d, dict) else None
            color = cell_color(s)
            dim_cells += f'<td style="background:{color};text-align:center;font-size:9pt;padding:3px 4px;border:1px solid #e5e7eb">{cell_score_only(d)}</td>'
        rows_html.append(
            f'<tr><td style="padding:4px 8px;font-size:9pt;color:#6b7280;border:1px solid #e5e7eb">T{tier}</td>'
            f'<td style="padding:4px 8px;font-size:10pt;font-weight:600;border:1px solid #e5e7eb">{case}</td>'
            f'<td style="padding:4px 8px;font-size:10pt;border:1px solid #e5e7eb;white-space:nowrap">{score_cell}</td>'
            f'{dim_cells}</tr>'
        )
    body = "\n".join(rows_html)

    # Tier averages
    from statistics import mean as _mean
    tier_summaries = []
    for tier in (0, 1, 2, 3):
        rows = [r for r in cases_sorted if TIER.get(r["case"]) == tier]
        if rows:
            tier_summaries.append(f"<strong>Tier {tier}</strong> n={len(rows)} mean={_mean([r['score'] for r in rows]):.1f}")
    summary_line = " · ".join(tier_summaries)

    return HTMLResponse(f"""<!doctype html>
<html><head><meta charset="utf-8"/><title>Castor Bench Dashboard</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 1500px; margin: 24px auto; padding: 0 20px; color: #1f2937; }}
  h1 {{ margin-bottom: 4px; }}
  .summary {{ font-size: 11pt; color: #4b5563; margin-bottom: 18px; }}
  table {{ border-collapse: collapse; }}
  th {{ background: #f9fafb; }}
  .legend {{ font-size: 10pt; color: #6b7280; margin-top: 14px; }}
  .swatch {{ display: inline-block; width: 14px; height: 14px; vertical-align: middle; margin-right: 4px; border: 1px solid #e5e7eb; }}
</style></head><body>
<h1>Castor Pipeline Benchmark — All Cases</h1>
<div class="summary">{summary_line} · <a href="/docs">docs</a> · <a href="/">app home</a></div>

<table>
<thead><tr>
  <th style="padding:4px 8px;font-size:9pt;border:1px solid #e5e7eb">Tier</th>
  <th style="padding:4px 8px;font-size:9pt;border:1px solid #e5e7eb">Case</th>
  <th style="padding:4px 8px;font-size:9pt;border:1px solid #e5e7eb">Score</th>
  {head_dims}
</tr></thead>
<tbody>
{body}
</tbody></table>

<div class="legend">
  <span class="swatch" style="background:#bbf7d0"></span>≥90 (A)
  <span class="swatch" style="background:#fde68a;margin-left:12px"></span>75-89 (B/C)
  <span class="swatch" style="background:#fed7aa;margin-left:12px"></span>50-74 (D)
  <span class="swatch" style="background:#fecaca;margin-left:12px"></span>&lt;50 (F)
  · Numbers = dimension score 0-100. Empty cells = case-source missing that dimension.
</div>
</body></html>
""")


@router.get("/docs", response_class=HTMLResponse)
@router.get("/docs/", response_class=HTMLResponse)
def docs_index():
    """A clickable index of every markdown file under docs/."""
    return HTMLResponse(_render_docs_index())


@router.get("/docs/{path:path}", response_class=HTMLResponse)
def docs_render(path: str):
    """Render a markdown file as HTML."""
    target = (DOCS_DIR / path).resolve()
    # Path traversal guard. is_relative_to, not startswith: a STRING prefix says yes to a
    # sibling whose name merely begins with the same characters, so ../docs_leak/x.md
    # passed a check on ".../docs". Nothing named docs* sits beside it today, which is the
    # only reason that was latent rather than live.
    if not target.is_relative_to(DOCS_DIR.resolve()):
        raise HTTPException(status_code=400, detail="invalid path")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"docs file not found: {path}")
    if target.suffix != ".md":
        return FileResponse(target)
    import markdown as _md
    md_text = target.read_text(encoding="utf-8")
    html_body = _md.markdown(md_text, extensions=["tables", "fenced_code", "toc"])
    parent = "/".join(path.split("/")[:-1])
    parent_link = f'<a href="/docs/{parent}">../{parent}/</a>' if parent else '<a href="/docs">docs/</a>'
    return HTMLResponse(f"""<!doctype html>
<html><head><meta charset="utf-8"/><title>{path} — Castor Docs</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 860px; margin: 30px auto; padding: 0 24px; color: #1f2937; line-height: 1.55; }}
  h1, h2, h3, h4 {{ color: #111827; }}
  h1 {{ border-bottom: 1px solid #e5e7eb; padding-bottom: 8px; }}
  h2 {{ margin-top: 32px; border-bottom: 1px solid #f3f4f6; padding-bottom: 6px; }}
  pre {{ background: #f3f4f6; padding: 12px 14px; border-radius: 4px; overflow-x: auto; font-size: 10pt; }}
  code {{ background: #f3f4f6; padding: 1px 5px; border-radius: 3px; font-size: 90%; }}
  pre code {{ padding: 0; background: transparent; }}
  table {{ border-collapse: collapse; margin: 14px 0; font-size: 10pt; width: 100%; }}
  table th, table td {{ border: 1px solid #e5e7eb; padding: 6px 10px; text-align: left; }}
  table th {{ background: #f9fafb; font-weight: 700; }}
  a {{ color: #2563eb; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  blockquote {{ border-left: 3px solid #e5e7eb; padding-left: 14px; color: #6b7280; }}
  .nav {{ font-size: 9pt; color: #6b7280; margin-bottom: 24px; }}
</style>
</head><body>
<div class="nav"><a href="/docs">← all docs</a> · {parent_link} · <a href="/">app home</a></div>
{html_body}
</body></html>
""")
