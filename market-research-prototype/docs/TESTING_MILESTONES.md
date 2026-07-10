# Moved — unified into the Harness v2 plan

This document has been **merged into [CC_HARNESS_PLAN.md](CC_HARNESS_PLAN.md)** (single source
of truth for Harness v2): §3 carries the testing program (five rings, per-file test map,
milestone gates M0–M8, regression protocol, rules of evidence), §4 the wave order.

Gate runners are unchanged: `python gates.py --corpus <dir> --gate all` (report quality,
D01–D14) and `python harness_gates.py --gate M<k>` (harness build, H01–H20).
Baselines live in `docs/baselines/`.
