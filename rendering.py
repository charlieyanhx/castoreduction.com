"""rendering.py — the two rendering helpers both the report and the routes need.

Neither belongs to routing, and both used to live under routes/. report/render_html.py
had to reach up into `api` at call time to borrow them, which is the one upward dependency
the layer graph had left. Moved down here, the same import runs at module scope in both
directions and nothing is working around a cycle.
"""
from __future__ import annotations

import jinja2


class SafeUndefined(jinja2.ChainableUndefined):
    """A missing template field must NEVER 500 the whole report (M2-class hardening). The default
    Undefined raises on `'{:,.0f}'.format(missing)`, on `missing > 0` comparisons, and on
    arithmetic — any one of which blanks the entire page. This renders/behaves NULLISH instead, so
    one absent value degrades to a blank cell. ChainableUndefined base also lets `a.b.c` chains
    resolve to undefined rather than raising. The degradation banner + validation flags still
    surface genuinely missing data, so we lose nothing by failing soft here."""
    __slots__ = ()
    def __format__(self, spec): return ""
    def __bool__(self): return False
    def __lt__(self, other): return False
    def __le__(self, other): return False
    def __gt__(self, other): return False
    def __ge__(self, other): return False
    def __int__(self): return 0
    def __float__(self): return 0.0
    def __add__(self, other): return other
    def __radd__(self, other): return other
    def __sub__(self, other): return 0
    def __mul__(self, other): return 0
    __rmul__ = __mul__
    def __truediv__(self, other): return 0
    def __round__(self, n=0): return 0


def display_title(profile: dict) -> str:
    """The venture name a human should see.

    The LLM often extracts name="Unknown" from a description-only brief. Printing that on
    a paid deliverable (or a PDF cover) is worse than naming what the report is ABOUT, so
    fall back to category, then to the first sentence of the summary.
    """
    profile = profile or {}
    name = str(profile.get("name") or "").strip()
    if name.lower() not in ("", "unknown", "untitled", "n/a", "none", "null"):
        return name
    derived = (profile.get("category") or "").strip()
    if derived:
        return derived
    summ = str(profile.get("summary") or "").strip()
    return summ.split(".")[0][:60] if summ else "Market Research"
