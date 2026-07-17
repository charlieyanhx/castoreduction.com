"""report/ — the numbers a buyer reads, each owned by exactly one model (Wave 4).

  forecast.py  the canonical sizing/forecast model: ONE derivation per number, and
               prose GENERATED from the rule actually used.

The wave exists because the old shape had five writers and no owner: TAM mid/low/high
was recomputed at five sites under three different formulas, each overwriting the last
while a hardcoded sentence kept describing the first one.
"""

# The pre-existing top-level report.py (markdown renderers) moved to render_md.py when
# this package claimed the `report` name — re-exported so `import report;
# report.render_discover(...)` (api.py) keeps working unchanged.
from report.render_md import (  # noqa: E402,F401
    render_discover, render_taste, render_match, render_full,
)
