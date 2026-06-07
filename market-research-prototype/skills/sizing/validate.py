"""
skills/sizing/validate.py — the mandatory numbers validation gate.

Every sizing payload passes through here before it ships. Impossible numbers are
flagged; hard violations set Evidence.error so the report renderer refuses to
publish. This is invariant #5 from SIZING.md: validate loud, never silent.

A sizing payload is expected to expose (any subset; missing keys are skipped):
  tam_usd, sam_usd, som_usd            — headline figures
  figures: [{value_usd, label, source, formula}, ...]   — provenance records
  trade_area_spend_usd                 — hyperlocal catchment ceiling (optional)

Checks (severity):
  ordering        SOM ≤ SAM ≤ TAM                         block
  share_ceiling   SOM / TAM ≤ max_share                   block
  provenance      every *_usd figure has a source         block
  trade_area_cap  SOM ≤ trade_area_spend (hyperlocal)     block
  triangulation   two SOM estimates within tolerance      warn
"""
from __future__ import annotations

import re
from typing import Optional

from skills.registry import skill
from tools import Evidence

DEFAULT_MAX_SHARE = 0.40  # SOM may not exceed 40% of TAM without explicit override

# C7 (Manus-parity): formula reconciliation. Catch figures whose stated formula
# does not compute to the stated value (e.g. "166k × $50 = $845M" — actually $8.3M).
_UNIT_MULT = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}
# A numeric token: optional $, digits/commas/decimal, optional k/m/b/t or %.
# An operator (* or /) only counts when immediately followed by another number —
# so "/hh/yr" or "/location" (per-unit prose) is ignored, but "/ 3%" is division.
_FORMULA_TOKEN = re.compile(
    r"(?P<num>\$?\s*\d[\d,]*\.?\d*\s*(?:k|m|b|t|%)?)|(?P<op>[*/](?=\s*\$?\s*\d))"
)


def _coerce_token(tok: str) -> Optional[float]:
    t = tok.replace("$", "").replace(",", "").replace(" ", "").lower()
    if not t:
        return None
    mult = 1.0
    if t.endswith("%"):
        t, mult = t[:-1], 0.01
    elif t and t[-1] in _UNIT_MULT:
        mult = _UNIT_MULT[t[-1]]
        t = t[:-1]
    try:
        return float(t) * mult
    except ValueError:
        return None


def safe_eval_formula(formula: str) -> Optional[float]:
    """Recompute a sizing formula's value from its numeric tokens.

    Handles products and divisions of numbers with k/m/b/t and % suffixes, in
    left-to-right order (× and ÷ share precedence). Returns None when the formula
    has no clear arithmetic (e.g. an average or pure prose) so callers don't
    false-flag. Pure, no eval() of arbitrary code.
    """
    if not formula:
        return None
    norm = formula.lower().replace("×", "*").replace("÷", "/").replace("·", "*")
    nums: list[float] = []
    ops: list[str] = []
    expect_num = True
    for m in _FORMULA_TOKEN.finditer(norm):
        if m.group("num") is not None:
            v = _coerce_token(m.group("num"))
            if v is None:
                continue
            nums.append(v)
            expect_num = False
        elif not expect_num:  # operator only valid between two numbers
            ops.append(m.group("op"))
            expect_num = True
    # Need at least two factors and one operator to reconcile a computation.
    if len(nums) < 2 or len(ops) < 1 or len(ops) != len(nums) - 1:
        return None
    acc = nums[0]
    for op, n in zip(ops, nums[1:]):
        if op == "/":
            if n == 0:
                return None
            acc /= n
        else:
            acc *= n
    return acc


def _check(sizing: dict, max_share: float) -> tuple[list[dict], list[dict]]:
    """Return (blocks, warns). Pure — no I/O, fully deterministic."""
    blocks: list[dict] = []
    warns: list[dict] = []

    tam = sizing.get("tam_usd")
    sam = sizing.get("sam_usd")
    som = sizing.get("som_usd")

    def _num(x):
        return isinstance(x, (int, float)) and not isinstance(x, bool)

    # Ordering: SOM ≤ SAM ≤ TAM (only compare the pairs we actually have).
    if _num(sam) and _num(tam) and sam > tam:
        blocks.append({"check": "ordering", "msg": f"SAM {sam:,.0f} > TAM {tam:,.0f}"})
    if _num(som) and _num(sam) and som > sam:
        blocks.append({"check": "ordering", "msg": f"SOM {som:,.0f} > SAM {sam:,.0f}"})
    if _num(som) and _num(tam) and som > tam:
        blocks.append({"check": "ordering", "msg": f"SOM {som:,.0f} > TAM {tam:,.0f}"})

    # Share ceiling.
    if _num(som) and _num(tam) and tam > 0:
        share = som / tam
        if share > max_share:
            blocks.append({"check": "share_ceiling",
                           "msg": f"SOM is {share:.0%} of TAM (max {max_share:.0%})"})

    # Provenance: every figure with a *_usd value must cite a source.
    for fig in (sizing.get("figures") or []):
        if not isinstance(fig, dict):
            continue
        if _num(fig.get("value_usd")) and not str(fig.get("source") or "").strip():
            blocks.append({"check": "provenance",
                           "msg": f"figure {fig.get('label', '?')!r} has no source"})

    # Hyperlocal catchment cap.
    cap = sizing.get("trade_area_spend_usd")
    if _num(som) and _num(cap) and som > cap:
        blocks.append({"check": "trade_area_cap",
                       "msg": f"SOM {som:,.0f} exceeds trade-area spend {cap:,.0f}"})

    # Triangulation (warn): two SOM estimates should converge.
    s1 = sizing.get("som_demand_usd")
    s2 = sizing.get("som_supply_usd")
    if _num(s1) and _num(s2) and max(s1, s2) > 0:
        spread = abs(s1 - s2) / max(s1, s2)
        if spread > 0.5:
            warns.append({"check": "triangulation",
                          "msg": f"SOM estimates diverge {spread:.0%} "
                                 f"(demand {s1:,.0f} vs supply {s2:,.0f})"})

    # C7: formula reconciliation — does each figure's stated formula compute to
    # its stated value? A gross mismatch (>10×) blocks; a moderate one (>25%) warns.
    for fig in (sizing.get("figures") or []):
        if not isinstance(fig, dict):
            continue
        val = fig.get("value_usd")
        if not _num(val) or val == 0:
            continue
        computed = safe_eval_formula(str(fig.get("formula") or ""))
        if computed is None or computed == 0:
            continue
        ratio = computed / val
        label = fig.get("label", "?")
        if ratio > 10 or ratio < 0.1:
            blocks.append({"check": "formula_reconciliation",
                           "msg": f"{label}: formula computes {computed:,.0f} but value is "
                                  f"{val:,.0f} ({ratio:.2g}× off)"})
        elif ratio > 1.25 or ratio < 0.8:
            warns.append({"check": "formula_reconciliation",
                          "msg": f"{label}: formula computes {computed:,.0f} vs value "
                                 f"{val:,.0f} ({ratio:.2g}×)"})

    # C7: segmentation must sum to its labeled parent (±2%). Castor's "TAM
    # segmentation" summed to SAM — a labeling/base error.
    segs = sizing.get("segmentation") or []
    seg_vals = [s.get("tam_usd") for s in segs if isinstance(s, dict)]
    seg_sum = sum(v for v in seg_vals if _num(v))
    parent = tam if _num(tam) else None
    if seg_sum > 0 and _num(parent) and parent > 0:
        seg_ratio = seg_sum / parent
        if seg_ratio < 0.5 or seg_ratio > 1.5:
            blocks.append({"check": "segmentation_sum",
                           "msg": f"segments sum to {seg_sum:,.0f} but labeled-parent TAM is "
                                  f"{parent:,.0f} ({seg_ratio:.2g}×)"})
        elif abs(seg_ratio - 1.0) > 0.02:
            warns.append({"check": "segmentation_sum",
                          "msg": f"segments sum to {seg_ratio:.0%} of TAM (expected ~100%)"})

    # F6: EXTERNAL cross-check — a GROUNDED (Census/BLS/ACS) figure vs a MODELED (LLM)
    # figure for TAM. Unlike the formula/segmentation checks (which compare LLM output
    # to itself and pass by construction), this can genuinely FAIL: real data and the
    # model disagreeing is signal, not noise.
    _grounded_src = ("census", "cbp", "bls", "acs")
    grounded, modeled = [], []
    for fig in (sizing.get("figures") or []):
        if not isinstance(fig, dict) or not _num(fig.get("value_usd")) or fig["value_usd"] <= 0:
            continue
        src = str(fig.get("source") or "").lower()
        (grounded if any(t in src for t in _grounded_src) else modeled).append(float(fig["value_usd"]))
    if grounded and modeled:
        def _med(xs):
            s = sorted(xs)
            return s[len(s) // 2]
        g, m = _med(grounded), _med(modeled)
        if g > 0 and m > 0:
            ratio = max(g, m) / min(g, m)
            if ratio > 5:
                warns.append({"check": "external_grounding_divergence", "kind": "external",
                              "msg": f"grounded estimate {g:,.0f} and modeled estimate "
                                     f"{m:,.0f} diverge {ratio:.1f}× — at least one is wrong"})

    # F6: tag the circular/by-construction checks honestly as internal-consistency
    # (they verify the model against itself), distinct from rule + external checks.
    _internal = {"formula_reconciliation", "segmentation_sum"}
    for item in blocks + warns:
        item.setdefault("kind", "internal" if item["check"] in _internal else "rule")

    return blocks, warns


@skill(produces="validation", consumes=["market_sizing"])
def validate_numbers(sizing: dict, max_share: float = DEFAULT_MAX_SHARE) -> Evidence:
    """Gate a sizing payload. Blocks (hard) set Evidence.error; warns are advisory.

    Returns Evidence(produces="validation") with payload:
      {passed: bool, blocks: [...], warns: [...]}
    `passed` is False iff there are hard blocks; in that case Evidence.error is set
    so downstream renderers refuse to publish.
    """
    blocks, warns = _check(sizing or {}, max_share)
    passed = not blocks
    return Evidence(
        source="validate_numbers",
        category="skill_output",
        count=len(blocks) + len(warns),
        payload={"passed": passed, "blocks": blocks, "warns": warns},
        error=None if passed else "; ".join(b["msg"] for b in blocks)[:500],
        cost_meta={"passed": passed, "n_blocks": len(blocks), "n_warns": len(warns)},
    )
