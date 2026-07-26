"""
Historical tracking — when the same plan is run twice, link them and compute deltas.

We hash the input description (normalized — lowercased + whitespace-collapsed) to identify
"same plan". When a /plan request matches a prior completed plan's hash:
  - We don't dedup (the operator wants a refresh)
  - But we DO record `previous_job_id` so the report can show deltas

Deltas computed:
  - Viability score change (with direction)
  - Competitor list (added / removed / score changed)
  - Persona list (added / removed)
  - TAM/SAM/SOM mid (% change)
  - Recommended next steps (changed?)
"""
from __future__ import annotations
import hashlib
import re
import json
import sqlite3
import threading
from pathlib import Path

from logger import get

log = get("history")

DB = Path(__file__).parent / ".jobs.sqlite"
_lock = threading.Lock()


def hash_description(description: str) -> str:
    """Normalize and hash a startup description so 'same idea' matches."""
    normalized = re.sub(r"\s+", " ", (description or "").strip().lower())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def find_previous_plan(description: str, exclude_job_id: str = "") -> str | None:
    """Return the most recent completed plan job_id matching this description, or None."""
    h = hash_description(description)
    with _lock:
        conn = sqlite3.connect(DB, timeout=10)
        # We store description in params_json — query that
        rows = conn.execute(
            "SELECT id, params_json, created_at FROM jobs "
            "WHERE kind = 'plan' AND state = 'complete' "
            "ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        conn.close()
    for jid, params_json, _ts in rows:
        if jid == exclude_job_id:
            continue
        try:
            params = json.loads(params_json or "{}")
            if hash_description(params.get("description", "")) == h:
                return jid
        except Exception:
            continue
    return None


def compute_deltas(current: dict, previous: dict) -> dict:
    """
    Compute meaningful deltas between two plan results.
    `current` and `previous` are both `result` dicts from completed plan jobs.
    """
    cur_v = (current.get("viability") or {}).get("viability_score") or 0
    prev_v = (previous.get("viability") or {}).get("viability_score") or 0
    viability_delta = cur_v - prev_v

    cur_opps = (current.get("discover", {}).get("synthesis", {}) or {}).get("ranked_opportunities", []) or []
    prev_opps = (previous.get("discover", {}).get("synthesis", {}) or {}).get("ranked_opportunities", []) or []

    # A ranked record can legitimately carry NO score — a geo-sourced neighbour, or a brand
    # the momentum enrichment never measured (discover._restore_computed_numbers drops an
    # unbacked number rather than printing one). `.get(key, 0)` does NOT protect against
    # that: the key exists holding None, so the default never fires and the subtraction
    # below raises TypeError. Membership is the roster; scores are only the numeric ones.
    cur_brands = {o.get("brand"): o.get("opportunity_score") for o in cur_opps if o.get("brand")}
    prev_brands = {o.get("brand"): o.get("opportunity_score") for o in prev_opps if o.get("brand")}

    new_competitors = [b for b in cur_brands if b not in prev_brands]
    dropped_competitors = [b for b in prev_brands if b not in cur_brands]
    score_changes = []
    for b, score in cur_brands.items():
        # Both sides must be numbers to have moved. `is None` rather than a falsy test —
        # 0.0 is a real score (a rival with no public footprint), not a missing one.
        prev = prev_brands.get(b)
        if score is None or prev is None:
            continue
        delta = score - prev
        if abs(delta) >= 5:  # only flag significant moves
            score_changes.append({"brand": b, "previous": prev, "current": score,
                                  "delta": round(delta, 1)})

    cur_personas = {p.get("name") for p in (current.get("personas", {}) or {}).get("personas", []) if p.get("name")}
    prev_personas = {p.get("name") for p in (previous.get("personas", {}) or {}).get("personas", []) if p.get("name")}
    new_personas = list(cur_personas - prev_personas)
    dropped_personas = list(prev_personas - cur_personas)

    def _ms_mid(d, layer):
        return ((d.get("market_sizing") or {}).get(layer) or {}).get("mid")

    cur_tam = _ms_mid(current, "tam")
    prev_tam = _ms_mid(previous, "tam")
    tam_change_pct = None
    if cur_tam and prev_tam and prev_tam > 0:
        tam_change_pct = round((cur_tam - prev_tam) / prev_tam * 100, 1)

    return {
        "viability_delta": round(viability_delta, 1),
        "viability_direction": "up" if viability_delta > 2 else "down" if viability_delta < -2 else "flat",
        "new_competitors": new_competitors,
        "dropped_competitors": dropped_competitors,
        "score_changes": sorted(score_changes, key=lambda x: -abs(x["delta"]))[:5],
        "new_personas": new_personas,
        "dropped_personas": dropped_personas,
        "tam_change_pct": tam_change_pct,
        "previous_score": prev_v,
        "current_score": cur_v,
    }
