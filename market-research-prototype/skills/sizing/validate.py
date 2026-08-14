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
# R4 rank 24: the magnitude suffix used to allow whitespace before a bare k/m/b/t —
# so "300 mid-size" read the 'm' of "mid" as Mega (a phantom 1e6× blow-up that raised a
# false hard block and withheld correct sizings). A space-separated suffix letter must
# now be STANDALONE — `[kmbt]` not followed by another letter — while attached suffixes
# ("100k", "130M") and "%" still resolve.
_FORMULA_TOKEN = re.compile(
    r"(?P<num>\$?\s*\d[\d,]*\.?\d*(?:\s*(?:[kmbt](?![a-z])|%))?)|(?P<op>[*/](?=\s*\$?\s*\d))"
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


_ARITHMETIC_PAREN = re.compile(r"^[\s\d.,+\-*/×÷%kmbtKMBT$]+$")


def _strip_prose_parentheses(formula: str) -> str:
    """Drop citation parentheticals, KEEP arithmetic ones.

    MEASURED BUG. Stripping every "(...)" was added so "(IBISWorld 2023)" could not inject 2023
    as a phantom factor. But it cannot tell a citation from a divisor, so

        "SAM x 1/(103+1) fair-share x 60% ramp"
          -> "SAM x 1/  fair-share x 60% ramp"      the divisor is GONE
          -> evaluates to 0.6

    against a published 25,217 — a 2.4e-05x "mismatch" reported as a BLOCK on run5, run6 and
    run7, on a figure whose arithmetic is exact. A fix for one false positive created another.

    So: a parenthetical made only of digits and operators is arithmetic and stays (its content
    is unwrapped into the expression); anything containing letters is prose or a citation and
    goes. "(2,142 households)" contains letters, so it is dropped — which is correct, since the
    2,142 is already stated outside it in every formula this pipeline writes.
    """
    def _repl(m: "re.Match[str]") -> str:
        inner = m.group(1)
        return f"({inner})" if _ARITHMETIC_PAREN.match(inner) else " "

    return re.sub(r"\(([^)]*)\)", _repl, formula)


def _eval_arithmetic_parens(norm: str) -> str:
    """Replace a purely-arithmetic (a+b) group with its value, so the tokenizer sees one number.

    Only + and - inside, which is all this pipeline writes (competitors+1). Left to right; any
    surprise leaves the group untouched so the caller's own guards decide what to do.
    """
    def _repl(m: "re.Match[str]") -> str:
        inner = m.group(1).replace(",", "").strip()
        if not re.fullmatch(r"\d+(?:\.\d+)?(?:\s*[+\-]\s*\d+(?:\.\d+)?)*", inner):
            return m.group(0)
        tokens = re.findall(r"[+\-]|\d+(?:\.\d+)?", inner)
        try:
            acc = float(tokens[0])
            i = 1
            while i + 1 < len(tokens) + 1 and i < len(tokens):
                op, nxt = tokens[i], float(tokens[i + 1])
                acc = acc + nxt if op == "+" else acc - nxt
                i += 2
            return f" {acc} "
        except (ValueError, IndexError):
            return m.group(0)

    return re.sub(r"\(([^)]*)\)", _repl, norm)


_MIN_MAX = re.compile(r"^\s*(min|max)\s*\((.*)\)\s*$", re.S)


def _eval_min_max(norm: str, refs=None):
    """Evaluate a whole-formula min(a, b) / max(a, b), or None.

    Each side is reconciled by the ordinary machinery, so "min($320,000 x 60% ramp,
    $18,056,068 SAM)" resolves to min(192000, 18056068). BOTH sides must reconcile: if one
    is prose or an unresolved symbol the answer is unknown, and half a computation reported
    as a whole one is how a verifier starts lying. A side that is a lone number is fine here
    — inside min() it is an operand being compared, not a claim standing in for its own
    proof.

    Only the outermost call, and only a two-argument one. That is the entire shape this
    pipeline writes; anything richer returns None and the caller's guards decide.
    """
    m = _MIN_MAX.match(norm.strip())
    if not m:
        return None
    fn, inner = m.group(1), m.group(2)
    if "(" in inner or ")" in inner:
        return None                      # nested calls: out of scope, stay honest
    # Split on the ARGUMENT comma only. A plain inner.split(",") tears "$680,000" into
    # "$680" and "000" — every figure this pipeline writes uses thousands separators, so
    # the naive split turns a two-argument min() into five and returns None on all of them.
    parts = [p.strip() for p in re.split(r",(?!\d)", inner) if p.strip()]
    if len(parts) != 2:
        return None
    vals = []
    for part in parts:
        v = safe_eval_formula(part, refs=refs)
        if v is None:
            # A bare operand is legitimate INSIDE min(); parse it directly rather than
            # through safe_eval_formula, which rejects lone numbers on purpose.
            v = _lone_number(part, refs)
        if v is None:
            return None
        vals.append(v)
    return min(vals) if fn == "min" else max(vals)


def _lone_number(text: str, refs=None):
    """The single numeric token in `text`, or None if there is not exactly one.

    Deliberately strict: "$52,622,389 SAM" is one number with a trailing label and resolves;
    "TAM x 35%" is not, and must not silently become 35.
    """
    # AN OPERAND HAS NO OPERATOR. Without this, "tam * 35%" finds exactly one numeric token
    # (35%) and returns 0.35 — the unresolved symbol silently dropped, which is precisely
    # how "SAM x 1/104 x 60%" once collapsed to a bare 0.6 and blocked a correct figure.
    # Anything with arithmetic in it belongs to safe_eval_formula, which knows to abort on
    # a symbol it was given no value for.
    if re.search(r"[*/](?=\s*\$?\s*\d)", text):
        return None
    # THE LITERAL WINS WHEN IT IS ALREADY THERE. This pipeline writes "$52,622,389 SAM" —
    # the value AND its label. Substituting refs first turns that into two numbers and the
    # side stops resolving, which is what kept every SOM figure unverified even after min()
    # was understood. Symbol resolution is the FALLBACK, for a bare "SAM".
    toks = [t for t in _FORMULA_TOKEN.finditer(text) if t.group("num") is not None]
    if len(toks) == 1:
        return _coerce_token(toks[0].group("num"))
    if toks:
        return None                      # more than one number: not a lone operand
    for name, val in (refs or {}).items():
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        if re.fullmatch(rf"\s*{re.escape(str(name).lower())}\s*", text, flags=re.I):
            return float(val)
    return None


def safe_eval_formula(formula: str, refs: Optional[dict] = None) -> Optional[float]:
    """Recompute a sizing formula's value from its numeric tokens.

    Handles products and divisions of numbers with k/m/b/t and % suffixes, in
    left-to-right order (× and ÷ share precedence). Returns None when the formula
    has no clear arithmetic (e.g. an average or pure prose) so callers don't
    false-flag. Pure, no eval() of arbitrary code.
    """
    if not formula:
        return None
    # Strip parenthetical citations BEFORE tokenizing — "(IBISWorld 2023)" injected the
    # year 2023 as a phantom factor, which (with the "= $X" result tail) unbalanced
    # nums-vs-ops and made this return None on the exact headline it exists to check
    # (R2). Then keep only the LHS of "=": the RHS is the CLAIMED result being
    # reconciled against, not part of the computation.
    # min()/max() BEFORE anything else touches the string, and the order is the whole fix.
    # _strip_prose_parentheses drops any "(...)" containing letters, so it deletes the
    # min(...) group itself; and the `split("=")` below truncates run18's
    # "min(= $643,243: ...)" to "min(". Both run before the tokenizer, so a min() formula
    # arrived pre-destroyed no matter what the tokenizer could parse.
    #
    # MEASURED across the corpus plus run17/run18: of 24 figures carrying a value, 8 did not
    # reconcile, and all 8 were the same figure — SOM_obtainable, on every report that has
    # one. The SOM is min(supply, demand) BY CONSTRUCTION, that being what a
    # binding-constraint model means, so the reconciler was structurally blind to exactly
    # one figure and it happened to be the headline.
    raw_norm = formula.lower().replace("×", "*").replace("÷", "/").replace("·", "*")
    picked = _eval_min_max(raw_norm, refs)
    if picked is not None:
        return picked

    stripped = _strip_prose_parentheses(formula)
    stripped = stripped.split("=", 1)[0]
    norm = stripped.lower().replace("×", "*").replace("÷", "/").replace("·", "*")
    norm = _eval_arithmetic_parens(norm)
    # Resolve SYMBOLIC references to sibling figures (SAM, TAM). Without this,
    # "TAM x 35% serviceable" tokenized to a single number, returned None, and
    # report/verifier.py's `if computed is None ... continue` SKIPPED the figure entirely —
    # measured: TAM_local and SAM_local went unchecked in run5, run6 AND run7. "Could not
    # parse" was reading as "fine", inside a verifier.
    #
    # An UNRESOLVED symbol must abort rather than be dropped: dropping is exactly how
    # "SAM x 1/104 x 60%" collapsed to a bare 0.6 and blocked a correct figure.
    if refs:
        for _name, _val in refs.items():
            if isinstance(_val, (int, float)) and not isinstance(_val, bool):
                norm = re.sub(rf"\b{re.escape(str(_name).lower())}\b", repr(float(_val)), norm)
    if re.search(r"\b(?:tam|sam|som)\b", norm):
        return None          # a sizing symbol we were given no value for
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

    # NOTHING TO CHECK IS NOT A PASS.
    #
    # Every check below is guarded by `_num(...)` — deliberately, so a partial sizing is
    # judged on what it actually produced. But that makes an EMPTY sizing satisfy all of
    # them vacuously: measured on a live run, `tam: {}, sam: {}, som: {}` came back
    # `passed: true, blocks: [], warns: []` and therefore `publishable: True`, so a report
    # shipped with no market size and no signal that anything had gone wrong. The loud
    # unpublishable banner and every downstream refusal are keyed on this verdict, and none
    # of them fire when emptiness reads as health.
    #
    # A sizing that produced NO NUMBER AT ALL has not been validated, it has been SKIPPED.
    #
    # "Any number" means any numeric *_usd value on the sizing (som_demand_usd and
    # som_supply_usd count — they drive the triangulation warn) OR any figure carrying a
    # value_usd. A payload of figures with no headline mids has very much produced numbers,
    # and the provenance / formula-reconciliation / external-grounding checks all run on
    # exactly that shape. Scoping this to tam/sam/som alone broke five of them.
    #
    # And it appends rather than returning early: suppressing the remaining checks would
    # replace one silent pass with a narrower one.
    #
    # 0.0 is a number — a market genuinely sized at zero is a finding, not an absence — so
    # the test is `_num`, not truthiness.
    _values = [v for k, v in sizing.items() if k.endswith("_usd")]
    _values += [f.get("value_usd") for f in (sizing.get("figures") or [])
                if isinstance(f, dict)]
    if not any(_num(v) for v in _values):
        blocks.append({"check": "no_numbers",
                       "msg": "sizing produced no numeric figure at all — nothing was "
                              "validated, so this cannot be published as validated"})

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
        # A formula reconciles to its OWN stated result, so a large gap is a dropped
        # factor, not rounding (the R2 case: 30.6B×0.15×0.15=$688.5M printed as $4.59B,
        # ratio 0.15 — a 6.7x self-contradiction that the old 10x/0.1x block waved
        # through). Block at >2.5x either way; the 1.25/0.8 warn band still absorbs
        # legitimate rounding.
        if ratio > 2.5 or ratio < 0.4:
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
    # Grounding is a PROVENANCE fact, read from the origin field a fetch path wrote —
    # never inferred from substrings of the LLM-authored `source` prose. The substring
    # version classified a model-recalled figure captioned "US Census Bureau SUSB" as
    # grounded and then "externally" cross-checked one model number against another.
    # A figure with no origin field is MODELED: absence of provenance is not evidence
    # of a fetch. And anything self-labelled UNSOURCED can never be grounded, whatever
    # its origin field claims — belt and braces against mislabelled plumbing.
    _grounded_origins = ("census", "cbp", "bls", "acs")
    grounded, modeled = [], []
    for fig in (sizing.get("figures") or []):
        if not isinstance(fig, dict) or not _num(fig.get("value_usd")) or fig["value_usd"] <= 0:
            continue
        origin = str(fig.get("origin") or fig.get("data_origin") or "").strip().lower()
        is_grounded = (origin in _grounded_origins
                       and "unsourced" not in str(fig.get("source") or "").lower())
        (grounded if is_grounded else modeled).append(float(fig["value_usd"]))
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

    Every sizing engine already runs this gate internally — call it standalone
    only on hand-assembled or externally-supplied sizing payloads. Do NOT use to
    reconcile multiple estimates into one point value; that is triangulate_skill.
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
