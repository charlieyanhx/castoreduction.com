# Castor vs. Manus — Head-to-Head Market-Research Benchmark

A reproducible protocol to compare Castor against a general autonomous agent
(Manus) on market-research queries. The point is not "we beat a general agent at
everything" — it's to **isolate where Castor's moat actually is** (defensible,
sourced, triangulated numbers) versus where a broad web agent is genuinely
stronger (live browsing breadth). An honest benchmark shows both.

## How to run

1. On a machine with network + `GEMINI_API_KEY` (or Groq/Anthropic), run:
   ```bash
   python -m benchmarks.run_manus_bench            # all queries
   python -m benchmarks.run_manus_bench --query Q1 # one query
   ```
   This prints Castor's structured output per query + a blank scorecard.
2. Paste each query verbatim into Manus, save its answer.
3. Score both sides on the rubric below (1–5 per dimension). Fill the scorecard.

## The benchmark queries

Chosen to span scales and to expose hand-waving. Q1/Q2 are where Castor's
numbers-right engine should separate; Q4 is where a live web agent should lead.

| ID | Query | Scale | Tests |
|----|-------|-------|-------|
| **Q1** | "I want to open a farm-to-table restaurant at 2700 Sunset Blvd, Silver Lake, Los Angeles. Market size, competitors, what customers would pay, and is it viable?" | hyperlocal | trade-area sizing, provenance |
| **Q2** | "Sizing for a regional chain of 8 boutique fitness studios across Austin, TX." | regional | per-location rollout, ceiling |
| **Q3** | "TAM/SAM/SOM for a B2B SaaS for restaurant inventory management, US." | national_digital | top-down÷bottom-up, triangulation |
| **Q4** | "Who are the top 15 competitors in AI customer-support software and what are their latest funding rounds?" | — | live web recency (Manus's strength) |

## The rubric (1–5 each; weights reflect Castor's thesis)

| # | Dimension | What 5 looks like | Weight | Expected edge |
|---|-----------|-------------------|--------|---------------|
| 1 | **Numeric specificity** | Concrete TAM/SAM/SOM figures, not "large and growing" | 1.0 | tie |
| 2 | **Provenance** | Every number cites source + formula | 1.5 | **Castor** |
| 3 | **Method fit** | Local biz sized by trade area, not national÷players | 1.5 | **Castor** |
| 4 | **Triangulation** | Headline numbers cross-checked ≥2 independent ways | 1.5 | **Castor** |
| 5 | **Validation** | Impossible numbers (SOM>SAM, share>100%) caught | 1.0 | **Castor** |
| 6 | **Competitor coverage** | Named, classified direct/indirect competitors | 1.0 | tie / Manus |
| 7 | **Consumer insight** | Segment-level needs, objections, willingness-to-pay | 1.0 | **Castor** |
| 8 | **Defensibility** | Would an SBA officer / lender accept the numbers? | 1.5 | **Castor** |
| 9 | **Web recency/breadth** | Fresh, broad, current web facts (funding, news) | 1.0 | **Manus** |
| 10 | **Reproducibility** | Same input → same structured output, benchmarked | 0.5 | **Castor** |

**Weighted score** = Σ(dimension × weight) / Σ(weights) × (100/5).

## Scorecard (fill per query)

```
Query: ____   Castor: ____ / 100   Manus: ____ / 100

dim                     Castor  Manus  notes
1 numeric specificity     _       _
2 provenance              _       _
3 method fit              _       _
4 triangulation           _       _
5 validation              _       _
6 competitor coverage     _       _
7 consumer insight        _       _
8 defensibility           _       _
9 web recency/breadth     _       _
10 reproducibility        _       _
```

## Reading the result (the strategic point)

- If **Castor wins 2,3,4,5,8** and **Manus wins 9**: this is the expected, healthy
  outcome. It confirms the thesis — Castor's moat is the *numbers-right engine*
  (the un-open-sourced, lender-grade layer), while a general web agent leads on
  raw browsing recency. The product strategy is to **own the numbers layer** and
  treat broad web research as a commodity input (which our agentic limbs already
  wrap via the GPT-Researcher pattern).
- If **Manus wins 2,3,4,8**: that's a real signal our numbers engine isn't yet
  defensible enough — a bug to fix, captured by this benchmark.
- If **Castor loses 6,9 badly**: deepen the discovery limb (live search/scrape +
  the planned BERTopic/embeddings from `STACK.md`).

This benchmark is meant to be re-run every cycle so the gap on each dimension is
tracked over time, the same way `benchmarks/run_all.py` tracks the 16-dimension
rubric across the 17 internal cases.
