"""
Step 3c of spec: Cluster competitors + dimensionality reduction for whitespace detection.

Iter 36: upgraded embedding + clustering stack —
  Embeddings:   fastembed (BAAI/bge-small-en-v1.5, 384-dim, ONNX, no torch)  →  TF-IDF fallback
  Clustering:   HDBSCAN (density-based, no k, handles noise)                 →  K-Means fallback
  2D layout:    UMAP (local structure preserved)                             →  PCA fallback

Rationale: TF-IDF + K-Means + PCA worked but gave noisy clusters on small n
and produced uninterpretable 2D maps (because PCA is linear). The new stack:
  - semantic embeddings catch synonymy ("DTC analytics" ≈ "Shopify dashboard")
  - HDBSCAN doesn't force every competitor into a cluster (outliers = noise, label -1)
  - UMAP's 2D projection keeps similar brands physically close, enabling
    "whitespace" regions that actually reflect gaps in buyer mental models.
"""
from __future__ import annotations
import numpy as np
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import silhouette_score
from logger import get
from llm import call_json  # module-level for mock-patching in tests

log = get("clustering")

# Lazy singletons — models are expensive to load, so one per process
_fastembed_model = None
_hdbscan_available: bool | None = None
_umap_available: bool | None = None


def _get_fastembed():
    """Lazy-load fastembed once per process. Returns None if unavailable."""
    global _fastembed_model
    if _fastembed_model is False:
        return None
    if _fastembed_model is None:
        try:
            from fastembed import TextEmbedding
            _fastembed_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
            log.info("[clustering] fastembed BAAI/bge-small-en-v1.5 loaded (384-dim)")
        except Exception as e:
            log.warning("[clustering] fastembed unavailable, falling back to TF-IDF: %s", e)
            _fastembed_model = False
            return None
    return _fastembed_model


def _build_semantic_embeddings(texts: list[str]) -> np.ndarray | None:
    """Semantic embeddings via fastembed. Returns None if unavailable."""
    model = _get_fastembed()
    if model is None:
        return None
    try:
        embs = list(model.embed(texts))
        return np.array(embs)
    except Exception as e:
        log.warning("[clustering] fastembed.embed failed: %s — falling back to TF-IDF", e)
        return None


def _build_tfidf_embeddings(texts: list[str], max_features: int = 300) -> np.ndarray:
    """TF-IDF fallback — always available via sklearn."""
    vec = TfidfVectorizer(
        max_features=max_features,
        ngram_range=(1, 2),
        stop_words="english",
        min_df=1,
    )
    X = vec.fit_transform(texts).toarray()
    return X


def _build_embeddings(texts: list[str]) -> tuple[np.ndarray, str]:
    """Try semantic first, fall back to TF-IDF. Returns (X, method_tag)."""
    X = _build_semantic_embeddings(texts)
    if X is not None:
        return X, "fastembed-bge-small"
    return _build_tfidf_embeddings(texts), "tfidf"


def _cluster_hdbscan(X: np.ndarray, min_cluster_size: int = 2) -> tuple[np.ndarray | None, float]:
    """HDBSCAN density clustering. Returns (labels, quality_score) or (None, -1) if unavailable."""
    try:
        import hdbscan
    except ImportError:
        return None, -1.0
    try:
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=max(2, min_cluster_size),
            min_samples=1,
            metric="euclidean",
            cluster_selection_method="eom",
        )
        labels = clusterer.fit_predict(X)
        # Score: fraction of points that got clustered (not -1 noise)
        n_clustered = int((labels != -1).sum())
        if n_clustered < 4 or len(set(labels)) <= 1:
            return None, -1.0
        # Silhouette only on clustered points
        mask = labels != -1
        if mask.sum() >= 4 and len(set(labels[mask])) >= 2:
            score = float(silhouette_score(X[mask], labels[mask]))
        else:
            score = 0.0
        return labels, score
    except Exception as e:
        log.warning("[clustering] HDBSCAN failed: %s", e)
        return None, -1.0


def _project_umap(X: np.ndarray) -> np.ndarray | None:
    """UMAP 2D projection. Returns None if unavailable."""
    try:
        import umap
    except ImportError:
        return None
    try:
        # n_neighbors small because we usually have 5-15 competitors
        n = X.shape[0]
        reducer = umap.UMAP(
            n_components=2,
            n_neighbors=min(max(2, n - 1), 10),
            min_dist=0.3,
            metric="cosine",
            random_state=42,
        )
        return reducer.fit_transform(X)
    except Exception as e:
        log.warning("[clustering] UMAP failed: %s", e)
        return None


def cluster_competitors(
    competitors: list[dict],
    k_range: tuple[int, int] = (3, 7),
) -> dict:
    """
    Cluster competitors by description embedding, find best k via silhouette score,
    and project to 2D via PCA for visualization.

    Input: list of {brand, domain, description, ...signals} from discover.
    Output: {
      "k": chosen cluster count,
      "silhouette_score": quality metric,
      "clusters": [{
        "id": int,
        "members": [brand names],
        "centroid_2d": [x, y],
        "top_terms": [words that characterize this cluster]
      }],
      "coordinates": {brand: [x, y]},
      "pca_explained_variance": float,
    }
    """
    # Build description texts for each competitor
    texts = []
    names = []
    for c in competitors:
        desc_parts = [
            c.get("description", ""),
            c.get("brand", ""),
            c.get("query_evidence", ""),
        ]
        # Add signal data as text features
        if c.get("reddit_top_subs"):
            desc_parts.append(" ".join(c["reddit_top_subs"]))
        text = " ".join(p for p in desc_parts if p)
        if len(text) < 20:
            continue
        texts.append(text)
        names.append(c.get("brand", f"unknown_{len(names)}"))

    n = len(texts)
    if n < 4:
        return {
            "error": f"Need at least 4 competitors with descriptions to cluster, got {n}",
            "n_competitors": n,
        }

    # Build embeddings (semantic first, TF-IDF fallback)
    X, embedding_method = _build_embeddings(texts)
    log.info(f"[clustering] embeddings: method={embedding_method}, shape={X.shape}")

    # --- Clustering: HDBSCAN first, K-Means fallback ---
    best_labels, best_score = _cluster_hdbscan(X, min_cluster_size=2)
    clustering_method = "hdbscan"
    if best_labels is None:
        clustering_method = "kmeans"
        max_k = min(k_range[1], n - 1)
        min_k = min(k_range[0], max_k)
        best_score = -1.0
        for k in range(min_k, max_k + 1):
            km = KMeans(n_clusters=k, random_state=42, n_init=10)
            labels = km.fit_predict(X)
            if len(set(labels)) < 2:
                continue
            try:
                score = silhouette_score(X, labels)
            except Exception:
                continue
            if score > best_score:
                best_score = score
                best_labels = labels

    if best_labels is None:
        return {"error": "Clustering failed — all solutions had <2 clusters", "n_competitors": n}

    # --- 2D projection: UMAP first, PCA fallback ---
    X_2d = _project_umap(X) if X.shape[0] >= 4 else None
    projection_method = "umap"
    explained_var = 0.0
    if X_2d is None:
        projection_method = "pca"
        pca = PCA(n_components=min(2, X.shape[1]))
        X_2d = pca.fit_transform(X)
        explained_var = float(sum(pca.explained_variance_ratio_))

    # Build clusters — HDBSCAN uses -1 for noise points; handle gracefully.
    unique_cluster_ids = sorted(set(int(l) for l in best_labels) - {-1})
    best_k = len(unique_cluster_ids)
    clusters = []
    for cluster_id in unique_cluster_ids:
        member_mask = best_labels == cluster_id
        members = [names[i] for i, m in enumerate(member_mask) if m]
        if not members:
            continue
        centroid = X_2d[member_mask].mean(axis=0).tolist()
        # Top TF-IDF terms for this cluster
        cluster_avg = X[member_mask].mean(axis=0)
        clusters.append({
            "id": int(cluster_id),
            "members": members,
            "centroid_2d": [float(centroid[0]), float(centroid[1])],
            "size": int(member_mask.sum()),
        })

    coordinates = {names[i]: [float(X_2d[i][0]), float(X_2d[i][1])] for i in range(n)}

    noise_count = int((best_labels == -1).sum()) if -1 in best_labels else 0

    return {
        "n_competitors": n,
        "k": best_k,
        "silhouette_score": round(float(best_score), 3),
        "clusters": clusters,
        "coordinates": coordinates,
        "pca_explained_variance": round(explained_var, 3),
        "method": f"{embedding_method} + {clustering_method} + {projection_method}",
        "embedding_method": embedding_method,
        "clustering_method": clustering_method,
        "projection_method": projection_method,
        "noise_count": noise_count,  # HDBSCAN outliers (competitors that don't fit any cluster)
    }


AXIS_LABEL_PROMPT = """You are interpreting the 2 principal axes of a competitive map.

Our TF-IDF embeddings of {n_competitors} competitors were reduced via PCA to 2D.
For each axis, here are the competitors at the extremes:

PC1 (x-axis):
  High-PC1 end: {pc1_high}
  Low-PC1 end: {pc1_low}

PC2 (y-axis):
  High-PC2 end: {pc2_high}
  Low-PC2 end: {pc2_low}

Competitor descriptions (for context):
{descriptions}

For EACH axis, look at what distinguishes the "high" set from the "low" set and give:
  - A short 2-4 word label that describes the axis of variation (e.g. "price-tier positioning", "channel maturity", "feature breadth vs depth")
  - A 1-sentence explanation of what the axis represents and what being "high" vs "low" means on it

Return JSON:
{{
  "pc1": {{
    "label": "short phrase",
    "high_meaning": "what being at the high end of PC1 means",
    "low_meaning": "what being at the low end means",
    "summary": "one sentence interpreting this axis"
  }},
  "pc2": {{
    "label": "short phrase",
    "high_meaning": "...",
    "low_meaning": "...",
    "summary": "..."
  }}
}}

Be concrete. Don't say "describes the product" — say what kind of product, what tier, what audience. If the axis is genuinely noisy/uninterpretable, say so in the summary and give a best-guess label."""


def label_pca_axes(clustering_result: dict, competitors: list[dict]) -> dict:
    """
    Iter 35 step 4 (user feedback #3a + spec 3c): ask LLM what PC1/PC2 represent.

    Finds brands at extremes of each axis from clustering_result.coordinates,
    then calls the LLM once to interpret what each axis means.
    """
    coords = clustering_result.get("coordinates") or {}
    if not coords or len(coords) < 4:
        return {"error": "need at least 4 coordinates to label axes"}

    # Flatten to list of (brand, x, y)
    points = [(b, xy[0], xy[1]) for b, xy in coords.items() if isinstance(xy, list) and len(xy) >= 2]
    if len(points) < 4:
        return {"error": "insufficient coordinate data"}

    pc1_sorted = sorted(points, key=lambda p: p[1])
    pc2_sorted = sorted(points, key=lambda p: p[2])
    n_extreme = min(3, len(points) // 2)
    pc1_low = [p[0] for p in pc1_sorted[:n_extreme]]
    pc1_high = [p[0] for p in pc1_sorted[-n_extreme:]]
    pc2_low = [p[0] for p in pc2_sorted[:n_extreme]]
    pc2_high = [p[0] for p in pc2_sorted[-n_extreme:]]

    # Build brand→description lookup, then a compact list for the prompt
    desc_map = {c.get("brand"): (c.get("description") or c.get("thesis") or "")[:140] for c in (competitors or [])}
    all_extreme_brands = set(pc1_low + pc1_high + pc2_low + pc2_high)
    descriptions = "\n".join(
        f"  - {b}: {desc_map.get(b, '(no description)')}"
        for b in sorted(all_extreme_brands)
    )[:2000]

    try:
        result = call_json(
            system="You interpret PCA axes of competitive maps. Return only JSON.",
            user=AXIS_LABEL_PROMPT.format(
                n_competitors=len(points),
                pc1_high=", ".join(pc1_high),
                pc1_low=", ".join(pc1_low),
                pc2_high=", ".join(pc2_high),
                pc2_low=", ".join(pc2_low),
                descriptions=descriptions,
            ),
            max_tokens=500,
        )
        if "_parse_error" in result:
            return {"error": "axis labeling returned malformed JSON", "_raw": result.get("_raw", "")[:200]}
        # Attach the extreme-brand lists so the chart legend can show them
        if "pc1" in result:
            result["pc1"]["high_brands"] = pc1_high
            result["pc1"]["low_brands"] = pc1_low
        if "pc2" in result:
            result["pc2"]["high_brands"] = pc2_high
            result["pc2"]["low_brands"] = pc2_low
        return result
    except Exception as e:
        log.warning("PCA axis labeling failed: %s", e)
        return {"error": str(e)}


def find_whitespace(clustering_result: dict, company_profile: dict) -> dict:
    """
    Given the clustered competitor map, identify regions of the 2D space
    that are underpopulated — those are whitespace opportunities.

    Simple heuristic: find the largest empty cell in a 4x4 grid over
    the bounding box of competitor coordinates.
    """
    coords = clustering_result.get("coordinates", {})
    if len(coords) < 4:
        return {"whitespace_found": False, "reason": "not enough competitors"}

    xs = [c[0] for c in coords.values()]
    ys = [c[1] for c in coords.values()]
    if not xs:
        return {"whitespace_found": False}

    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    if x_max == x_min or y_max == y_min:
        return {"whitespace_found": False, "reason": "coordinates collapsed"}

    # 4x4 grid
    grid = np.zeros((4, 4), dtype=int)
    for x, y in zip(xs, ys):
        col = min(3, int((x - x_min) / (x_max - x_min) * 4))
        row = min(3, int((y - y_min) / (y_max - y_min) * 4))
        grid[row][col] += 1

    # Find empty cells
    empty = [(r, c) for r in range(4) for c in range(4) if grid[r][c] == 0]
    if not empty:
        return {"whitespace_found": False, "reason": "market fully covered"}

    # Pick the empty cell farthest from any populated cell (most "blank")
    populated = [(r, c) for r in range(4) for c in range(4) if grid[r][c] > 0]
    def dist_to_pop(cell):
        return min(abs(cell[0]-p[0]) + abs(cell[1]-p[1]) for p in populated)
    best = max(empty, key=dist_to_pop)

    return {
        "whitespace_found": True,
        "grid_shape": [4, 4],
        "empty_cells": len(empty),
        "populated_cells": len(populated),
        "largest_gap_location": list(best),
        "grid_population": grid.tolist(),
    }
