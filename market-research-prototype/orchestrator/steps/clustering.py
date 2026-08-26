"""orchestrator/steps/clustering.py — Step 3c: competitor clustering + whitespace.

Extracted from run_plan (god-function dismantling, wave 2). Pure move: same ≥4-roster
guard, same drop-recording on error, same non-fatal axis-labeling span. Deliberately NO
blanket try/except — the inline block let cluster_competitors/find_whitespace exceptions
propagate, and a pure move must not quietly widen an exception net.

(The module shares its name with top-level clustering.py; absolute imports keep them
distinct — this is orchestrator.steps.clustering, the algorithm lives in clustering.)
"""
from __future__ import annotations

from typing import Callable

from logger import get

from . import record_dropped_output, step_done, step_scope

log = get("plan.steps.clustering")

_MIN_ROSTER_TO_CLUSTER = 4


def run_clustering_step(result: dict, profile: dict, opps: list,
                        checkpoint: Callable[[], None] | None = None) -> None:
    """Cluster the competitor roster, detect whitespace, label the PCA axes.

    R4 rank 9: cluster the CANONICAL roster (the competitors the report displays),
    never the larger `signals` pool. Clustering national ventures on `signals`
    plotted ~20 dots for a roster of 9 — a third competitor count on a third
    surface. The roster entries carry the same scraped descriptions, so the map
    positions exactly the competitors the report lists, and clustering's
    n_input == len(roster) == competitor_density.
    """
    if len(opps) < _MIN_ROSTER_TO_CLUSTER:
        # C-class (report_audit): this early return disclosed NOTHING, so the
        # competitive map simply vanished and the reader had no way to know why —
        # measured on run ff89f905, where the roster reached 15 AFTER refinement but
        # held fewer than 4 when clustering ran. Absence must be explained.
        record_dropped_output(
            result, "clustering",
            f"only {len(opps)} competitor(s) were known when the map was built "
            f"(minimum {_MIN_ROSTER_TO_CLUSTER}); later discovery rounds added more, "
            f"so the roster below is larger than any map could have plotted")
        log.info("[plan] clustering skipped: roster of %d below the minimum %d",
                 len(opps), _MIN_ROSTER_TO_CLUSTER)
        return
    with step_scope("clustering"):
        log.info("[plan] Step 3c: clustering competitors + PCA whitespace detection")
        from clustering import cluster_competitors, find_whitespace
        cluster_input = opps
        clustering = cluster_competitors(cluster_input)
        # C-class (report_audit): the map plotted 4 of 22 — every direct RAG rival
        # dropped for having no description, leaving four IT-outsourcing shops to
        # define both axes. Clustering cannot invent positions, but the reader must
        # never take a 4-of-22 picture as the competitive landscape.
        if isinstance(clustering, dict) and not clustering.get("error"):
            _n_plotted = (clustering.get("n_competitors")
                          or len(clustering.get("coordinates") or {}) or 0)
            clustering["n_roster"] = len(opps)
            clustering["coverage_pct"] = (round(100.0 * _n_plotted / len(opps))
                                          if opps else None)
            _missing = [o.get("brand") for o in opps
                        if o.get("brand") not in (clustering.get("coordinates") or {})]
            clustering["not_plotted"] = [m for m in _missing if m][:20]
            if opps and _n_plotted < 0.6 * len(opps):
                clustering["coverage_warning"] = (
                    f"This map positions {_n_plotted} of {len(opps)} rostered "
                    f"competitors ({clustering['coverage_pct']}%). The rest carried no "
                    f"description long enough to place, so the axes below describe "
                    f"ONLY the plotted subset — not the market. Not plotted: "
                    + ", ".join(clustering["not_plotted"][:8])
                    + ("..." if len(clustering["not_plotted"]) > 8 else ""))
                log.warning("[plan] competitor map covers %d of %d — disclosed",
                            _n_plotted, len(opps))
        if clustering.get("error"):
            # Measured: on a real OSM roster this is "need at least 4 competitors with
            # descriptions, got 2" (n_input=30). Silence made the competitor map vanish
            # between two runs of the same venture with nothing to explain it.
            record_dropped_output(result, "clustering",
                                  f"cluster_competitors: {clustering.get('error')}")
        if not clustering.get("error"):
            whitespace = find_whitespace(clustering, profile)
            # Label PCA axes (user feedback #3a + spec step 3c)
            try:
                from clustering import label_pca_axes
                axis_labels = label_pca_axes(clustering, opps)
                if "error" not in axis_labels:
                    clustering["axis_labels"] = axis_labels
            except Exception as e:
                log.warning(f"[plan] PCA axis labeling failed (non-fatal): {e}")
            result["clustering"] = clustering
            result["whitespace"] = whitespace
            step_done(result, "clustering")
            if checkpoint:
                checkpoint()
