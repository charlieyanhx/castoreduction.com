# Triangulation — Castor's central engine

> Not a feature of market sizing. The organizing principle of the whole product.
> It is the concrete mechanism behind the two things that get *scarcer* as agents
> get cheaper (FORESIGHT P2/P3): reproducible truth and verifiable provenance.

---

## The idea in one sentence

**Never trust a single derivation. Compute every load-bearing number ≥2
methodologically-independent ways, require them to converge, and treat divergence as
a published signal — not a hidden error you average away.**

---

## Why this is the big idea (not hyperbole)

1. **It is the antidote to LLM variance — the #1 enterprise objection.** We *measured*
   Manus swing TAM 47% and SOM 27× on the identical prompt. Why? One method per run,
   no cross-check. A single LLM number is one stochastic draw. Triangulation converts a
   stochastic generator into a *bounded, defensible* estimate: if three independent
   paths agree, the number is trustworthy regardless of model temperature; if they
   disagree, you *say so*. As generation commoditizes, this is the thing that doesn't.

2. **It generalizes far beyond TAM.** Triangulation is a universal verification
   primitive. Everything good we shipped is secretly the same idea:
   - 3-method TAM (top-down ÷ bottom-up ÷ analog)
   - SOM demand-side vs supply-side cross-check
   - `validate_numbers` formula reconciliation (stated value vs recomputed value)
   - the independent judge vs the deterministic gate (two evaluators)
   - segmentation-sum vs parent (two ways to the same total)
   **One principle, many instances.** That means it's an *engine*, not a step.

3. **It is the seed of the attestation layer (FORESIGHT P8).** "Triangulated across N
   independent sources, converged within X%" is exactly what a "Castor-verified" mark
   would certify. No model lab or horizontal agent does this; it's a category.

---

## The honest part: most of our triangulation is currently FAKE

True triangulation requires the paths to be **methodologically independent**. Ours
often aren't:

- The 3 TAM methods are **three prompts to the same LLM**. Three draws from one model
  are *correlated*, not independent — they can **falsely converge** (the model is
  confidently wrong the same way three times). Convergence then means nothing.
- The drivers (ARPU, penetration, serviceable fraction) are single LLM guesses with no
  second path at all (audit M2).

**Fake triangulation is worse than none** — it manufactures false confidence. So the
work is not "add more methods," it's "make the paths *independent* by anchoring each to
a *different data source or mechanism*."

### Real vs. fake
| | Fake (what we partly have) | Real (what to build) |
|---|---|---|
| Top-down | LLM "industry report says…" | A *named, fetched* analyst/industry figure |
| Bottom-up | LLM "150k restaurants × $50" | **Census count × BLS spend** (live, sourced) |
| Analog | LLM "comparable ARR ÷ penetration" | A comparable's *real filing/disclosure* number |
| Convergence | average the three | **report spread; flag if paths diverge >X%** |
| Independence | same model, 3 prompts | **3 different data origins** |

---

## The convergence math (make it a first-class score)

For any figure with independent estimates e₁…eₙ:
- **point** = median(eᵢ) (robust to one bad path), not mean
- **spread** = (max−min)/median
- **confidence** = high if spread ≤ 0.25, medium ≤ 0.6, else **low + a visible flag**
- **provenance** = the source of *each* path, shown, not just the headline
- **n_independent** = count of *distinct data origins* (not distinct prompts) — a number
  triangulated by 1 origin is labeled single-source, however many prompts produced it.

This turns every headline number into `{point, spread, confidence, paths:[{value,
source, method}], n_independent}` — a defensible object, not a scalar.

---

## What to build to make triangulation the moat

1. **A generic `triangulate(quantity, methods=[...])` engine** (promote it out of
   market_sizing into a cross-cutting skill). Each method is an independent producer
   returning `(value, source)`. The engine computes point/spread/confidence and refuses
   to claim convergence across non-independent paths.
2. **Independence accounting.** Tag each method with its *data origin*; convergence only
   counts when origins differ. Same-model prompts collapse to n_independent=1.
3. **Triangulate the drivers, not just the totals** (closes audit M2): ARPU from BLS *and*
   from competitor pricing scrape; penetration from analog adoption *and* from a
   bottom-up funnel. The softest input gets a second path.
4. **Surface it in the report** as a first-class "Triangulation" section: every headline
   number with its paths, spread, and a convergence badge — this *is* the audit-grade,
   reproducible artifact the foresight thesis sells.
5. **Make it the attestation export** — a signed "triangulation record" per figure becomes
   the "Castor-verified" trail.

---

## The reframed positioning (tighter than FORESIGHT)

Castor isn't "AI market research." **Castor is a triangulation engine for business
decisions** — it produces numbers that are *cross-verified across independent sources,
with convergence stated and divergence flagged.* That single capability is:
- the antidote to agent variance (P2),
- the substance of audit-grade provenance (P3),
- the seed of attestation (P8),
- and the thing a lender/investor actually needs to act.

Everything else (the harness, the UI, the agents) is delivery. **Triangulation is the
product.**
