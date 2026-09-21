# Documentation

Start with the current implementation and distinguish it from planned changes.

| Document | Purpose |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Current execution flow and module responsibilities |
| [RESEARCH_REVIEW_DESIGN.md](RESEARCH_REVIEW_DESIGN.md) | Proposed adaptive planning, evidence review, and targeted repair |
| [CLEANUP_REVIEW.md](CLEANUP_REVIEW.md) | Repository cleanup, findings, verification, and remaining limitations |
| [REPORT_SPEC.md](REPORT_SPEC.md) | Report requirements |
| [REPORT_METHODOLOGY.md](REPORT_METHODOLOGY.md) | Methodology and evidence expectations |
| [SIZING.md](SIZING.md), [TRIANGULATION.md](TRIANGULATION.md) | Sizing methodology and triangulation |
| [AUTHORING_A_METHOD.md](AUTHORING_A_METHOD.md) | Extending sizing methods |
| [archive/](archive/) | Historical designs and iteration logs |

`CODE_MAP.md`, `HARNESS_STATE.md`, audit results, and dated plans are snapshots of the
revisions they describe. Consult current code and tests before treating their counts or
implementation-status statements as current. The old `method/` and `process/` directories
are under `archive/`.

Run the current offline suite with `../test_all.sh -q` from here, or `./test_all.sh -q`
from the project root. Use `bench.sh` at the root to inspect or exercise a capability.
