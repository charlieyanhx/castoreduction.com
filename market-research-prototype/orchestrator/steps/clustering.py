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
        return
    with step_scope("clustering"):
        log.info("[plan] Step 3c: clustering competitors + PCA whitespace detection")
        from clustering import cluster_competitors, find_whitespace
        cluster_input = opps
        clustering = cluster_competitors(cluster_input)
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
