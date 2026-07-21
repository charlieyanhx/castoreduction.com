"""
F1 — the validation gate must WITHHOLD the headline numbers, not just banner them.

Failure-first: these tests assert the rendered Market Size section hides TAM/SAM/SOM
figures when validation.passed is False. They MUST fail on the pre-fix template (which
renders the numbers regardless) and pass after the fix.
"""
from __future__ import annotations

import re
import unittest

from jinja2 import Environment, FileSystemLoader

_env = Environment(loader=FileSystemLoader("templates"), autoescape=True)
_SRC = _env.loader.get_source(_env, "report.html")[0]


def _market_size_section() -> str:
    # Slice the FULL balanced market-sizing block: from its opening comment/if
    # through to (but not including) the next top-level block (financials).
    start = _SRC.index("<!-- MARKET SIZING (TAM/SAM/SOM) -->")
    end = _SRC.index("{% if financials and not financials.error %}", start)
    return _SRC[start:end]


def _render(market_sizing: dict) -> str:
    from market_sizing import format_currency
    return _env.from_string(_market_size_section()).render(
        market_sizing=market_sizing, format_currency=format_currency)


_PASS = {
    "tam": {"mid": 5_000_000_000, "low": 4e9, "high": 6e9, "label": "TAM"},
    "sam": {"mid": 1_000_000_000, "low": 8e8, "high": 1.2e9, "label": "SAM"},
    "som": {"mid": 50_000_000, "low": 4e7, "high": 6e7, "label": "SOM"},
    "validation": {"passed": True, "blocks": []},
}


def _blocked():
    d = {k: dict(v) if isinstance(v, dict) else v for k, v in _PASS.items()}
    d["validation"] = {"passed": False, "blocks": [{"msg": "SOM 9B > SAM 1B"}]}
    return d


class TestGateWithholdsNumbers(unittest.TestCase):
    def test_blocked_sizing_hides_headline_numbers(self):
        out = _render(_blocked())
        # The 24pt TAM currency must NOT appear when the gate failed.
        self.assertNotIn("$5.0B", out, "blocked sizing still rendered the TAM figure")
        self.assertNotIn("$1.0B", out, "blocked sizing still rendered the SAM figure")
        # The failure notice + the specific block must be shown instead.
        self.assertIn("failed validation", out.lower())
        self.assertIn("SOM 9B", out)   # the specific block reason is shown (HTML-escaped)
        self.assertIn("SAM 1B", out)

    def test_passing_sizing_shows_numbers(self):
        out = _render(_PASS)
        self.assertIn("$5.0B", out)   # TAM rendered normally when it passed
        self.assertIn("$1.0B", out)   # SAM rendered

    def test_no_convergence_theatre_for_single_source(self):
        # F4: when the engine says single_source/not-triangulated, the report must NOT
        # also print the LLM "3-method triangulation … unweighted average" theatre.
        ms = {
            "tam": {"mid": 5e9, "low": 4e9, "high": 6e9, "label": "TAM",
                    "method_top_down": {"value_usd": 5e9, "calculation": "a", "source": "LLM"},
                    "method_bottom_up": {"value_usd": 5e9, "calculation": "b", "source": "LLM"},
                    "reconciliation": "3-method triangulation: headline mid is the unweighted average. converged.",
                    "triangulation": {"confidence": "single_source", "n_independent": 1,
                                      "point": 5e9, "converged": False, "spread": 0,
                                      "flag": "only 1 independent origin (llm) — not triangulated",
                                      "cross_origin": [{"origin": "llm", "value": 5e9}]}},
            "sam": {"mid": 1e9, "low": 8e8, "high": 1.2e9},
            "som": {"mid": 5e7, "low": 4e7, "high": 6e7},
            "validation": {"passed": True, "blocks": []},
        }
        out = _render(ms)
        self.assertNotIn("unweighted average", out)   # theatre gone
        self.assertNotIn("Reconciliation:", out)
        self.assertIn("not triangulated", out)        # honest badge remains

    def test_no_validation_key_still_shows_numbers(self):
        # Backward-compat: legacy reports without a validation block still render.
        d = {k: dict(v) for k, v in _PASS.items() if k != "validation"}
        out = _render(d)
        self.assertIn("$5.0B", out)


if __name__ == "__main__":
    unittest.main()


class TestChartHonesty(unittest.TestCase):
    """W4 item 4: the competitor map must not assert statistics it doesn't have.

    (a) UMAP has no explained-variance concept — clustering.py hardcodes
        explained_var=0.0 on the umap path, which the title rendered verbatim as
        "0% variance explained": a real-looking statistic that is actually 'we did
        not compute this'.
    (b) A silhouette <= 0 means points sit closer to OTHER clusters than their own —
        the clustering is noise, so a "whitespace" gap drawn on that projection is an
        artifact, not an opportunity. Don't draw it.
    """

    def _clustering(self, method="bge + hdbscan + umap", var=0.0, sil=0.42):
        return {"k": 3, "method": method, "pca_explained_variance": var,
                "silhouette_score": sil,
                "coordinates": {"A": [0.1, 0.2], "B": [0.8, 0.7], "C": [0.5, 0.4]},
                "clusters": [{"id": 0, "members": ["A"], "size": 1},
                             {"id": 1, "members": ["B"], "size": 1},
                             {"id": 2, "members": ["C"], "size": 1}],
                "axis_labels": {}}

    def test_umap_projection_omits_the_bogus_variance_claim(self):
        from charts import competitor_map_svg
        svg = competitor_map_svg(self._clustering())
        self.assertNotIn("0% variance", svg)
        self.assertNotIn("variance explained", svg)

    def test_pca_projection_keeps_a_real_variance_claim(self):
        from charts import competitor_map_svg
        svg = competitor_map_svg(self._clustering(method="tfidf + kmeans + pca", var=0.63))
        self.assertIn("63% variance explained", svg)

    def test_whitespace_suppressed_when_silhouette_is_not_positive(self):
        from charts import competitor_map_svg
        ws = {"whitespace_found": True, "largest_gap_location": [1, 2]}
        for bad in (0.0, -0.13):
            svg = competitor_map_svg(self._clustering(sil=bad), whitespace=ws)
            self.assertNotIn("whitespace", svg, f"silhouette={bad} must suppress callout")

    def test_whitespace_shown_when_clustering_is_meaningful(self):
        from charts import competitor_map_svg
        ws = {"whitespace_found": True, "largest_gap_location": [1, 2]}
        svg = competitor_map_svg(self._clustering(sil=0.42), whitespace=ws)
        self.assertIn("whitespace", svg)
