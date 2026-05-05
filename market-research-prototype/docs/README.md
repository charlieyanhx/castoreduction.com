# Castor Advisories — Documentation

Two branches:

| Branch | What you'll find |
|---|---|
| **[`method/`](./method/)** | How the system works today — pipeline architecture, benchmark rubric, scoring formulas, test-case design. Read this first if you want to understand or audit the system. |
| **[`process/`](./process/)** | How we got here — chronological cycle log, fix-by-fix history, design decisions and their rationale, bugs the benchmark surfaced. Read this if you want to understand WHY the system is shaped the way it is. |

## Quick orientation

- The product: a 22-step market-research pipeline that takes a B2B SaaS venture description and emits a paid-grade report (TAM/SAM/SOM, 4Ps, personas, competitive landscape, viability score).
- The benchmark: a 16-dimension rubric scored against publicly-cited reference data, with an LLM-as-judge for prose quality. Three test cases (Sleep Loop, TraceFlow, Workhive).
- Code locations:
  - Pipeline: `plan.py` orchestrates step-by-step. Component modules: `taste.py`, `customer_universe.py`, `differentiators.py`, `personas.py`, `pricing.py`, `place.py`, `market_sizing.py`, `four_ps.py`, etc.
  - Benchmark: `benchmarks/{score.py, prose_judge.py, run_all.py, cases/*.json}`.
  - Tests: `test_infra.py`, `test_integration.py`, `test_api.py` (231 passing as of 2026-04-29).

## Reading order

If new to the project: `method/01-pipeline-overview.md` → `method/02-benchmark-rubric.md` → `process/01-cycle-log.md`.

If reviewing changes: `process/02-decisions.md` → `process/03-bugs-surfaced.md`.

If extending: `method/04-test-cases.md` (how to add a case) → `method/05-scoring-formula.md` (how to add a dimension).
