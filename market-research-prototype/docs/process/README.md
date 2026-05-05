# Process

This branch records **how we got here** — the chronology of development, the decisions we made, and the bugs we found along the way. It's a living engineering log, not a tidy retrospective.

| File | Topic |
|---|---|
| [`01-cycle-log.md`](./01-cycle-log.md) | Cycle-by-cycle iteration history. Each cycle = one full benchmark run + analysis + fix(es). |
| [`02-decisions.md`](./02-decisions.md) | Architectural and rubric decisions with rationale. Why we did X instead of Y. |
| [`03-bugs-surfaced.md`](./03-bugs-surfaced.md) | Real bugs the benchmark caught — many would have shipped silently without it. |

## Reading order

If you want to understand the full arc: `01-cycle-log.md`.

If you're reviewing an architectural choice: `02-decisions.md`.

If you're convincing someone the benchmark is worth the effort: `03-bugs-surfaced.md`.

## Big picture

Late-spring 2026 the pipeline had been iterating on prompt engineering for ~30 cycles ("cycle" = one full pipeline run plus analysis plus fix). At cycle 28 we shifted from "make the prompts better" to "is the system actually right?" — that's when the benchmark was born.

The benchmark itself went through five versions in two days:

- **v1** — 7-dimension rubric, single case, parallel runs (rate-limit collisions)
- **v2** — 8-dimension rubric (added prose judge), still single case
- **v3** — 16-dimension rubric, 3 cases, 3 fixes shipped (viability retry, citation scorer, validation gate)
- **v4** — 6 fixes total (added place prompt, TAM 3-method split, orchestrator timeout)
- **v5** — 7 fixes total (added value_usd coerce); steady-state baseline

Each version exposed new bugs that previous versions hid. The honest take: **the benchmark made the pipeline visibly worse before it made it better**, because every new rubric dimension caught something we hadn't been measuring.
