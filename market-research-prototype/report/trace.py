"""report/trace.py — sentence-level provenance: which script produced THIS sentence.

`section_provenance.py` answers "which module owns this block". For debugging that is one
step too coarse: seeing a sentence that reads wrong, you want the exact result path behind
it so you can go read the code that wrote that field.

DERIVED, NOT ANNOTATED. The report's prose IS the result values, so walking the result dict
gives {text -> JSON path} for free, and the first path segment is the result key that
SECTION_SOURCES already maps to a producing module. No template markup, so the mapping
cannot drift away from the template the way hand-placed tags do.

Three honest outcomes per block, because not all prose is model output:

  result    matched a result value — carries the exact path (`four_ps.price.narrative`,
            `differentiators.differentiators[4].why_unique`) and its producing module.
  template  static prose written in templates/report.html: the report's own framing, a
            caveat, a legend. "This sentence is not from a model" is a real debugging
            answer, and the section-level overlay could not give it.
  unmatched the template transformed the value before printing it (truncation like
            `[:120]`, currency formatting, interpolation into a sentence), so no exact run
            survives. Counted and reported — never quietly relabelled as template prose.

Annotation is tag-safe by construction: the HTML is split into tag and text runs and only
text runs are rewritten, so a value that also appears inside an attribute cannot corrupt the
markup. Debug-only — this changes the bytes of the page, never what it says.
"""
from __future__ import annotations

import html as _html
import re
from typing import Any, Optional

# Below this length a value is a label or an enum ("high", "llm", "direct", "seat") and
# would match all over the page, attributing prose to whatever field happened to share a
# word with it.
_MIN_VALUE_CHARS = 25
# A block shorter than this is a heading, a number or a table cell — not a sentence anyone
# needs to trace.
_MIN_BLOCK_CHARS = 40

# Run-bookkeeping keys, not report content. `_trace` in particular holds the run's own event
# log, so indexing it attributes report prose to the provenance panel's own text.
_SKIP_KEYS = ("_trace", "_cogs", "_steps_completed", "_elapsed_seconds", "_verification")


def index_values(result: dict) -> list[tuple[str, str]]:
    """Every substantial string in `result` paired with its JSON path, LONGEST FIRST.

    Longest-first matters: a short value that is a substring of a longer one must not claim
    the longer one's text. `differentiators.items[0].why` and a summary quoting it would
    otherwise race, and the shorter (less specific) path could win.
    """
    found: dict[str, str] = {}

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in _SKIP_KEYS:
                    continue
                walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")
        elif isinstance(node, str):
            text = node.strip()
            if len(text) >= _MIN_VALUE_CHARS and text not in found:
                found[text] = path

    walk(result or {}, "")
    return sorted(found.items(), key=lambda kv: -len(kv[0]))


def _root_key(path: str) -> str:
    """The result key a path hangs off — `personas[2].name` -> `personas`."""
    return re.split(r"[.\[]", path, maxsplit=1)[0]


def producer_for_path(path: str) -> Optional[dict]:
    """The producing script for a result path, via its root key.

    Falls back through a sibling key before giving up: side-channel results hang off a
    variant of a mapped key (`audiences_undecodable` beside `audiences`,
    `pricing_benchmark` beside `pricing`), and answering "?" for those would send a
    debugger looking for a module that does not exist. Returns None only when even the
    stem is unmapped, and the caller then reports the root key itself — which is still a
    better lead than a question mark."""
    from report.section_provenance import producer_for
    root = _root_key(path)
    hit = producer_for(root)
    if hit:
        return hit
    # `audiences_undecodable` -> `audiences`; `segment_ranking_debug` -> `segment_ranking`.
    parts = root.split("_")
    while len(parts) > 1:
        parts.pop()
        hit = producer_for("_".join(parts))
        if hit:
            return {**hit, "via": root}
    return None


_TAG_SPLIT = re.compile(r"(<[^>]+>)")
_BLOCK = re.compile(r"<(p|li|td|h3|blockquote)\b[^>]*>(.*?)</\1>", re.S | re.I)


def _variants(text: str) -> list[str]:
    """The forms a result value can take once Jinja has rendered it."""
    escaped = _html.escape(text, quote=False)
    out = [text] if text == escaped else [escaped, text]
    # Jinja's autoescape turns ' into &#39; in some paths and leaves it in others.
    alt = escaped.replace("'", "&#39;")
    if alt not in out:
        out.append(alt)
    return out


def annotate(html: str, result: dict) -> tuple[str, dict]:
    """Wrap every result-derived run of text in a span naming its path and producer.

    Returns (annotated_html, stats). Only text runs are rewritten — never the inside of a
    tag — so a value that is also an attribute value cannot break the markup.
    """
    values = index_values(result)
    if not values:
        return html, {"matched": 0, "blocks": len(_BLOCK.findall(html)), "unmatched": 0}
    activity = step_activity(result)

    segments = _TAG_SPLIT.split(html)
    matched_paths: set[str] = set()
    for i, segment in enumerate(segments):
        if not segment or segment.startswith("<") or len(segment.strip()) < _MIN_VALUE_CHARS:
            continue
        # Collect NON-OVERLAPPING matches against the pristine text first, then splice once.
        # Inserting as we go would let a later (shorter) value match inside a span already
        # written — the attribute text becomes matchable content, and the output is
        # `data-src="…" data-by="…"` printed inside the report. Found live.
        claims: list[tuple[int, int, str, str]] = []   # (start, end, matched_form, path)
        for text, path in values:
            if path in matched_paths:
                continue          # one span per path — the first rendering owns it
            for form in _variants(text):
                if not form:
                    continue
                at = segment.find(form)
                while at != -1:
                    end = at + len(form)
                    if not any(at < c_end and c_start < end
                               for c_start, c_end, _f, _p in claims):
                        claims.append((at, end, form, path))
                        matched_paths.add(path)
                        break
                    at = segment.find(form, at + 1)
                if path in matched_paths:
                    break
        if not claims:
            continue
        out_parts, cursor = [], 0
        for start, end, form, path in sorted(claims):
            producer = producer_for_path(path) or {}
            chain = chain_for_path(path, result, activity)
            out_parts.append(segment[cursor:start])
            out_parts.append(
                f'<span class="tr" data-src="{_html.escape(path, quote=True)}"'
                f' data-by="{_html.escape(str(producer.get("module") or _root_key(path)), quote=True)}"'
                f' data-origin="{_html.escape(str(producer.get("origin") or "?"), quote=True)}"'
                f' data-gen="{_html.escape(str(chain["generated_by"]), quote=True)}"'
                f' data-step="{_html.escape(str(chain["step"] or "?"), quote=True)}"'
                f' data-fn="{_html.escape(str(producer.get("produced_by") or "?"), quote=True)}"'
                f'>{form}</span>')
            cursor = end
        out_parts.append(segment[cursor:])
        segments[i] = "".join(out_parts)
    out = "".join(segments)
    blocks = len(_BLOCK.findall(html))
    matched = out.count('<span class="tr"')
    return out, {"matched": matched, "blocks": blocks,
                 "unmatched": max(blocks - matched, 0)}


def trace_report(html: str, result: dict) -> list[dict]:
    """One row per substantial block: what produced it, and where to go look.

    Every block gets an answer. A block whose text matches no result value is the report's
    own prose — attributed to the template, which is exactly the fact a debugger needs when
    a sentence reads oddly but no model wrote it.
    """
    values = index_values(result)
    rows: list[dict] = []
    for _tag, inner in _BLOCK.findall(html):
        text = _html.unescape(re.sub(r"<[^>]+>", "", inner)).strip()
        if len(text) < _MIN_BLOCK_CHARS:
            continue
        hit = next(((v, p) for v, p in values
                    if v[:60] in text or (len(text) >= 60 and text[:60] in v)), None)
        if hit:
            producer = producer_for_path(hit[1]) or {}
            rows.append({"kind": "result", "path": hit[1],
                         "module": producer.get("module") or _root_key(hit[1]),
                         "produced_by": producer.get("produced_by") or "?",
                         "origin": producer.get("origin") or "?",
                         "text": text[:120]})
        else:
            rows.append({"kind": "template", "path": None,
                         "module": "templates/report.html",
                         "produced_by": "report template",
                         "origin": "authored", "text": text[:120]})
    return rows


# ---------------------------------------------------------------------------------------
# The full chain, per sentence. Everything above is STATIC (a declared map); everything
# below joins it to what the RUN actually did, from the append-only ledger in `_trace`.
# That is the difference between "four_ps.py owns this" and "four_ps.py wrote this during
# the four_ps step, which made 3 gemini-flash-latest calls and fetched nothing".
# ---------------------------------------------------------------------------------------

def _run_events(result: dict) -> list[dict]:
    return [e for e in (result.get("_trace") or []) if isinstance(e, dict)]


def step_activity(result: dict) -> dict[str, dict]:
    """Per pipeline step, what actually happened during it — from the ledger, not a guess.

    Tool events whose `step` is None are counted separately rather than spread across
    steps: the step label is a ContextVar and does not cross into the pipeline's worker
    threads, so a large share of tool calls genuinely cannot be attributed to a step. That
    is a real gap in the ledger, and inventing an owner for them would be worse than
    reporting them as unattributed.
    """
    events = _run_events(result)
    # Only the 3 extracted orchestrator steps wrap themselves in `step_scope`, so most
    # llm/tool events carry no step label at all (measured on a live run: 34 of 42 llm
    # calls and every tool call). The ledger IS time-ordered though, and `step_done`
    # records a completion event, so an unlabelled event can be attributed to the next
    # step that completed after it. That is an INFERENCE from ordering, not a recorded
    # fact — parallel fan-out can straddle a boundary — so it is marked as one rather
    # than presented as ground truth.
    completions = sorted((e.get("t") or 0, e.get("name")) for e in events
                         if e.get("layer") == "step" and e.get("status") == "complete")

    def _infer(t) -> tuple[Optional[str], bool]:
        for done_t, name in completions:
            if (t or 0) <= done_t:
                return name, True
        return None, True

    out: dict[str, dict] = {}
    for e in _run_events(result):
        step, inferred = e.get("step"), False
        if not step:
            step, inferred = _infer(e.get("t"))
        step = step or "(unattributable)"
        slot = out.setdefault(step, {"llm_calls": 0, "models": set(), "in_tok": 0,
                                     "out_tok": 0, "cached": 0, "tools": {},
                                     "tools_failed": [], "inferred": 0, "labelled": 0})
        slot["inferred" if inferred else "labelled"] += 1
        layer = e.get("layer")
        if layer == "llm":
            slot["llm_calls"] += 1
            slot["models"].add(e.get("model") or "?")
            slot["in_tok"] += int(e.get("in_tok") or 0)
            slot["out_tok"] += int(e.get("out_tok") or 0)
            slot["cached"] += 1 if e.get("cached") else 0
        elif layer == "tool":
            name = e.get("name") or "?"
            t = slot["tools"].setdefault(name, {"calls": 0, "sourced": 0,
                                                "source": e.get("source") or name})
            t["calls"] += 1
            t["sourced"] += 1 if e.get("sourced") else 0
            if not e.get("ok"):
                slot["tools_failed"].append(f"{name}: {(e.get('error') or 'failed')[:60]}")
    for slot in out.values():
        slot["models"] = sorted(slot["models"])
    return out


def _step_for_key(root: str, result: dict) -> Optional[str]:
    """The pipeline step that produced a result key.

    Most keys are named after their step (profile, discover, pricing, four_ps, viability…),
    so an exact match against the steps this run actually recorded is both the simplest
    rule and a verifiable one — no hand-maintained table to drift. Returns None rather than
    guessing when the run recorded no step of that name.
    """
    recorded = {e.get("name") for e in _run_events(result)
                if e.get("layer") == "step" and e.get("status") == "complete"}
    if root in recorded:
        return root
    stem = root.split("_")[0]
    return next((s for s in sorted(recorded) if s == stem), None)



def _generation_source(origin: Optional[str], act: dict, produced_by: str) -> str:
    """WHAT actually generated this text, named as concretely as the run allows.

    The origin says what KIND of thing produced it; this says which one. For model-written
    prose that is the model id from the run's own llm events — the single most useful fact
    when a sentence reads wrong, because it tells you whether to go argue with a prompt or
    with arithmetic. Deterministic output names the function instead, since no model was
    involved at all and looking for a prompt would be a dead end.
    """
    models = act.get("models") or []
    tools = sorted((act.get("tools") or {}).keys())
    tokens = (act.get("in_tok") or 0) + (act.get("out_tok") or 0)
    model_str = ", ".join(models) if models else "model not recorded for this step"
    if origin in ("llm", "simulated"):
        detail = f" ({tokens:,} tok)" if tokens else ""
        panel = " — an LLM-simulated panel, not real respondents" if origin == "simulated" else ""
        return f"{model_str}{detail}{panel}"
    if origin == "computed":
        return f"Python — {produced_by}() — no model involved"
    if origin == "fetched":
        return (f"fetched via {', '.join(tools)}" if tools
                else "fetched — the fetching tool was not recorded for this step")
    if origin == "mixed":
        bits = [b for b in (model_str if models else "", ", ".join(tools)) if b]
        return " + ".join(bits) or "mixed sources, none recorded"
    return f"{produced_by} (origin unrecorded)"


def chain_for_path(path: str, result: dict,
                   activity: Optional[dict] = None) -> dict:
    """The whole provenance chain for one result path — static map joined to the live run."""
    producer = producer_for_path(path) or {}
    root = _root_key(path)
    step = _step_for_key(root, result)
    acts = activity if activity is not None else step_activity(result)
    act = acts.get(step or "", {})
    # A completed step with no recorded llm/tool events is a LEDGER gap, not a step that
    # did nothing: `set_step` is a ContextVar and is not set on every call path, so those
    # events land in the "(unattributed)" pool. Saying so beats printing a bare None and
    # letting the reader conclude the step was idle.
    attributed = bool(act)
    return {
        "path": path,
        "result_key": root,
        "section": producer.get("section"),
        "module": producer.get("module") or root,
        "produced_by": producer.get("produced_by"),
        "origin": producer.get("origin"),
        "consumes": producer.get("consumes") or [],
        "via": producer.get("via"),
        "step": step,
        "step_llm_calls": act.get("llm_calls"),
        "step_models": act.get("models") or [],
        "step_tokens": ((act.get("in_tok") or 0) + (act.get("out_tok") or 0)) or None,
        "step_tools": sorted((act.get("tools") or {}).keys()),
        "step_tools_failed": act.get("tools_failed") or [],
        "generated_by": _generation_source(producer.get("origin"), act,
                                           producer.get("produced_by") or root),
        "step_activity_known": attributed,
        "step_note": ("" if attributed else
                      "this step recorded no llm/tool events against its own name — they "
                      "are in the unattributed pool (set_step is not called on every path)"),
    }


def full_trace(html: str, result: dict) -> list[dict]:
    """Every substantial block with its complete chain — the detailed debugging view.

    Ordered as the report reads, so a block's row is where the eye already is.
    """
    activity = step_activity(result)
    rows = []
    for row in trace_report(html, result):
        if row["kind"] == "result":
            rows.append({**chain_for_path(row["path"], result, activity),
                         "kind": "result", "text": row["text"]})
        else:
            rows.append({**row, "generated_by": "static text in templates/report.html "
                         "— no model and no data source wrote this sentence",
                         "step": None, "step_models": [], "step_tools": [],
                         "step_tools_failed": [], "result_key": None,
                         "consumes": [], "via": None, "section": None,
                         "step_llm_calls": None, "step_tokens": None})
    return rows
