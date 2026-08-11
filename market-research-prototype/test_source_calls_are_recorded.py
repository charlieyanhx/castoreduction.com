"""
The registry advertised 39 tools; the ledger only ever saw 13 of them.

MEASURED across 22 stored artifacts (5 live runs + 16 corpus) before this change:

    13  tools recorded in the ledger on a real run
    18  tools provably called in production but NEVER recorded
     4  registered tools with no call path to the wrapper at all

PROOF THE 18 REALLY RAN: out/live/run6.json carries hn_signal with hits_found=20 and 20
hydrated Hacker News stories, so hackernews_mentions unquestionably executed — and run6's
_trace records ZERO calls to it. Same for reddit_mentions (reddit_signal present),
trustpilot_reviews, validate_domain, resolve_brand_domain, google_trends_rising,
meta_ad_library and the rest.

THE CAUSE IS ONE FACT, NOT THREE BUGS. Every function in tools/*.py is a thin @tool wrapper
that delegates to an implementation re-exported through sources.py:

    @tool(category="customer_voice", ...)
    def hackernews_mentions(query, limit=20) -> Evidence:
        from sources import hackernews_mentions as _impl      # <- the real work
        items = _impl(query, limit=limit) or []
        return Evidence(...)

`@tool` records, and points TOOL_REGISTRY[name].fn at the recording wrapper, so
get_tool("x").fn(...) is traced. But production does `from sources import hackernews_mentions`
and calls the bare implementation, which the wrapper never sees. That one fact explains all 18
blind spots AND 3 of the 4 "dead" tools (their implementations run — firmographics.
enrich_competitors from plan.py:1739, scrape.structured.extract from five sites — only their
wrappers are unreached).

WHY IT MATTERS BEYOND A COUNT: it is the ceiling on report provenance. Of run6's 139 traced
blocks only 9 are attribution="recorded"; 80 fall back to a static declared map precisely
because the ledger holds no record for them. Raising recording coverage raises the ceiling on
honest attribution — the same problem wearing two hats.

THE FIX: record at the implementation layer, which is the single choke point BOTH paths
traverse, and guard against double-counting when the @tool wrapper is the caller. Return shapes
are untouched — the recorder only observes — so none of the 18 call sites change.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import persistence.ledger as ledger


class _LedgerCapture:
    """Runs a callable with a fresh ledger and returns the tool events it recorded."""

    def __enter__(self):
        ledger.reset("test-recording")
        return self

    def __exit__(self, *exc):
        return False

    @staticmethod
    def tool_events(name=None):
        evs = [e for e in ledger.LEDGER.events() if e.get("layer") == "tool"]
        return [e for e in evs if name is None or e.get("name") == name]


class TestADirectImplementationCallIsRecorded(unittest.TestCase):
    """The blind spot itself: `from sources import X; X(...)` must reach the ledger."""

    def test_hackernews_mentions_called_directly_is_recorded(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"hits": [
            {"title": "a cafe thread", "points": 5, "num_comments": 2,
             "objectID": "1", "created_at": "2025-01-01T00:00:00Z"}]}
        with _LedgerCapture() as cap:
            from sources import hackernews_mentions
            with patch("tools.sources.forums.mrp_http.get", return_value=resp):
                out = hackernews_mentions("Noe Cafe", limit=5)
            self.assertEqual(len(out), 1, "the implementation stopped returning its list")
            evs = cap.tool_events("hackernews_mentions")
        self.assertEqual(len(evs), 1,
                         f"a direct implementation call recorded {len(evs)} events, expected 1")
        self.assertTrue(evs[0].get("ok"))

    def test_the_return_shape_is_unchanged_by_recording(self):
        """18 call sites consume plain lists/dicts. If recording changed the return type into
        Evidence the whole pipeline would break — the recorder must only observe."""
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"hits": []}
        with _LedgerCapture():
            from sources import hackernews_mentions
            with patch("tools.sources.forums.mrp_http.get", return_value=resp):
                out = hackernews_mentions("x", limit=3)
        self.assertIsInstance(out, list)

    def test_a_failed_fetch_at_least_leaves_a_trace_marked_empty(self):
        """HONEST ABOUT ITS OWN LIMIT. A network failure inside hackernews_mentions is caught by
        the implementation itself, which returns [] — so from outside, "the network died" and
        "nobody mentioned this brand" are the SAME value. The instrumentation cannot invent the
        difference; it records ok=True, skeleton=True either way.

        What it does buy: the call is no longer INVISIBLE. Before this, a failed fetch left no
        record at all, which is strictly worse — the run looked like it never asked.

        The deeper fix is the swallowed exception in each implementation (the same class as
        Reddit's 403 reading as "0 posts"), and it is NOT done here."""
        with _LedgerCapture() as cap:
            from sources import hackernews_mentions
            with patch("tools.sources.forums.mrp_http.get",
                       side_effect=RuntimeError("network down")):
                out = hackernews_mentions("x", limit=3)
            evs = cap.tool_events("hackernews_mentions")
        self.assertEqual(out, [], "the implementation's own error handling changed")
        self.assertEqual(len(evs), 1, "a failed source fetch left no trace at all")
        self.assertTrue(evs[0].get("skeleton"),
                        "an empty result was not marked skeleton, so a reader cannot tell it "
                        "returned nothing")

    def test_an_implementation_that_raises_is_recorded_as_a_failure(self):
        """When an implementation does NOT swallow the error, the record must say ok=False and
        the exception must still propagate — instrumentation observes, it does not intercept."""
        from persistence.ledger import instrument_source
        ledger.reset("raise-case")

        def boom(x):
            raise ValueError("upstream exploded")

        wrapped = instrument_source(boom, "boom_tool", "test")
        with self.assertRaises(ValueError):
            wrapped("q")
        evs = [e for e in ledger.LEDGER.events()
               if e.get("layer") == "tool" and e.get("name") == "boom_tool"]
        self.assertEqual(len(evs), 1)
        self.assertFalse(evs[0].get("ok"))
        self.assertIn("ValueError", evs[0].get("error") or "")

    def test_an_empty_but_successful_fetch_is_still_recorded(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"hits": []}
        with _LedgerCapture() as cap:
            from sources import hackernews_mentions
            with patch("tools.sources.forums.mrp_http.get", return_value=resp):
                hackernews_mentions("x", limit=3)
            self.assertEqual(len(cap.tool_events("hackernews_mentions")), 1)


class TestTheToolWrapperPathStillRecordsExactlyOnce(unittest.TestCase):
    """The wrapper delegates to the now-instrumented implementation, so the naive fix
    double-counts every get_tool() call. One call must produce one event."""

    def test_get_tool_path_records_once_not_twice(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"hits": []}
        with _LedgerCapture() as cap:
            from tools import get_tool
            with patch("tools.sources.forums.mrp_http.get", return_value=resp):
                ev = get_tool("hackernews_mentions").fn(query="x", limit=3)
            evs = cap.tool_events("hackernews_mentions")
        self.assertIsNotNone(ev)
        self.assertEqual(len(evs), 1,
                         f"one get_tool call produced {len(evs)} ledger events — the wrapper "
                         "and the implementation are both recording")

    def test_a_nested_source_call_is_recorded_separately_and_that_is_correct(self):
        """MEASURED and deliberate, not a double-count. sources.py:347 has validate_domain call
        is_parked_domain internally, so one validate_domain(...) plus one direct
        is_parked_domain(...) yields THREE events: validate_domain, its nested is_parked_domain,
        and the direct one. That is the truth about what ran.

        The in-flight guard suppresses only the @tool-wrapper/implementation pair for the SAME
        tool name — it must not suppress a genuinely different tool called underneath, or the
        trace would hide real work."""
        with _LedgerCapture() as cap:
            from sources import validate_domain
            resp = MagicMock(status_code=200, url="https://stripe.com", text="<html>hi</html>")
            with patch("sources.mrp_http.get", return_value=resp):
                validate_domain("stripe.com")
            outer = cap.tool_events("validate_domain")
            nested = cap.tool_events("is_parked_domain")
        self.assertEqual(len(outer), 1)
        self.assertEqual(len(nested), 1,
                         "the nested is_parked_domain call was swallowed by the guard")

    def test_the_wrapper_still_returns_evidence(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"hits": []}
        with _LedgerCapture():
            from tools import get_tool
            from tools.registry import Evidence
            with patch("tools.sources.forums.mrp_http.get", return_value=resp):
                ev = get_tool("hackernews_mentions").fn(query="x", limit=3)
        self.assertIsInstance(ev, Evidence)


class TestEveryInstrumentableSourceIsInstrumented(unittest.TestCase):
    """Drift guard. A new tool whose implementation lives in sources.py must be instrumented
    too, or the blind spot silently reopens one tool at a time."""

    def test_every_registered_tool_backed_by_sources_is_recorded(self):
        import sources
        from tools import TOOL_REGISTRY
        missing = []
        for name in TOOL_REGISTRY:
            impl = getattr(sources, name, None)
            if impl is None:
                continue                       # not backed by a sources.py export
            if not getattr(impl, "__records_to_ledger__", False):
                missing.append(name)
        self.assertEqual(missing, [],
                         "these registered tools have a sources.py implementation that is NOT "
                         f"instrumented, so calling it directly is invisible: {missing}")

    def test_the_instrumented_set_is_not_empty(self):
        """A test that passes because it found nothing to check is worthless — this is the
        vacuous-pass class, so assert the population is real."""
        import sources
        from tools import TOOL_REGISTRY
        n = sum(1 for name in TOOL_REGISTRY
                if getattr(getattr(sources, name, None), "__records_to_ledger__", False))
        self.assertGreaterEqual(n, 15,
                                f"only {n} sources implementations are instrumented; the "
                                "measured blind spot was 18 tools")

    def test_every_registered_tool_is_recordable_by_some_path(self):
        """THE INVARIANT THIS WHOLE CHANGE EXISTS FOR, and the one worth keeping green.

        A registered tool must be impossible to run invisibly. MEASURED before: 13/39 (33%)
        had ever reached the ledger. After: 39/39, via exactly three paths —

          1. the implementation is instrumented (26) — sources.py exports plus the five
             explicitly wired in scrape/wayback.py, scrape/structured.py, firmographics.py
             whose function names differ from their tool names;
          2. get_tool("name").fn(...) string dispatch (9) — the @tool wrapper records;
          3. the decorated module-level name imported directly, e.g.
             `from tools.scrape import web_search` (4) — @tool RETURNS the wrapper, so this
             records too.

        If a new tool is added with none of the three, this fails and names it. That is the
        drift guard: the blind spot reopened one tool at a time before, silently."""
        import ast
        import glob
        import os

        import firmographics
        import scrape.structured as _st
        import scrape.wayback as _wb
        import sources
        from tools import TOOL_REGISTRY

        reg = set(TOOL_REGISTRY)
        recordable = {n for n in reg
                      if getattr(getattr(sources, n, None), "__records_to_ledger__", False)}
        for name, fn in (("fetch_via_wayback", _wb.fetch_via_wayback),
                         ("wayback_snapshot_url", _wb.latest_snapshot_url),
                         ("extract_structured", _st.extract),
                         ("enrich_competitor", firmographics.enrich_one),
                         ("enrich_competitors_batch", firmographics.enrich_competitors)):
            if getattr(fn, "__records_to_ledger__", False):
                recordable.add(name)
        # path 2: get_tool("name") dispatch anywhere in production
        for f in glob.glob("**/*.py", recursive=True):
            if os.path.basename(f).startswith("test_") or ".venv" in f:
                continue
            try:
                tree = ast.parse(open(f).read())
            except (SyntaxError, OSError):
                continue
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "get_tool"):
                    for a in node.args:
                        if isinstance(a, ast.Constant) and a.value in reg:
                            recordable.add(a.value)
        # path 3: the registry's own wrapper is what the module exports
        for name, meta in TOOL_REGISTRY.items():
            if hasattr(meta.fn, "__tool_meta__"):
                recordable.add(name)

        missing = sorted(reg - recordable)
        self.assertEqual(missing, [],
                         f"{len(missing)} registered tool(s) can run without leaving any ledger "
                         f"record, so their output would have no provenance: {missing}")


class TestTheNeverFiredWrappersActuallyWork(unittest.TestCase):
    """4 registered tools had no call path to the wrapper at all. They are NOT dead code — each
    delegates to an implementation, and 3 of those implementations run in production:

        enrich_competitors_batch -> firmographics.enrich_competitors   plan.py:1739
        enrich_competitor        -> firmographics.enrich_one           firmographics.py:404
        extract_structured       -> scrape.structured.extract          5 call sites
        wayback_snapshot_url     -> scrape.wayback.latest_snapshot_url internal to fetch_via_wayback

    MEASURED LIVE, all four returned real data: extract_structured parsed 11 fields from JSON-LD,
    wayback_snapshot_url resolved an archive URL, enrich_competitor pulled Stripe's real GitHub
    org. So none is deleted — a working, documented capability the orchestrator can choose is not
    a defect.

    THE ACTUAL RISK IS THE census_business_counts LESSON: a registered tool that never fires can
    be silently broken for a long time — that one had the wrong NAICS parameter AND no API key,
    so it could not have worked, and nothing noticed because nothing called it. These tests
    exercise the delegation boundary with the implementation mocked, so a signature or kwarg
    drift fails here instead of waiting for the first real call."""

    def test_extract_structured_delegates_with_the_right_kwargs(self):
        from tools import get_tool
        with patch("scrape.structured.extract", return_value={"json_ld": [{"a": 1}]}) as m:
            ev = get_tool("extract_structured").fn(html="<html></html>", url="https://x.com/p")
        self.assertFalse(ev.skeleton, ev.error)
        m.assert_called_once()
        self.assertEqual(m.call_args.kwargs.get("base_url"), "https://x.com/p",
                         "url is not being passed through as base_url")

    def test_wayback_snapshot_url_delegates_and_passes_timeout(self):
        from tools import get_tool
        with patch("scrape.wayback.latest_snapshot_url",
                   return_value="http://web.archive.org/web/1/x") as m:
            ev = get_tool("wayback_snapshot_url").fn(url="https://x.com", timeout=3.0)
        self.assertFalse(ev.skeleton, ev.error)
        self.assertEqual(m.call_args.kwargs.get("timeout"), 3.0)

    def test_enrich_competitor_delegates_both_required_args(self):
        from tools import get_tool
        with patch("firmographics.enrich_one", return_value={"brand": "S", "sources": ["github"]}) as m:
            ev = get_tool("enrich_competitor").fn(brand="Stripe", domain="stripe.com")
        self.assertFalse(ev.skeleton, ev.error)
        self.assertEqual(m.call_args.args, ("Stripe", "stripe.com"))

    def test_enrich_competitors_batch_respects_its_cap(self):
        from tools import get_tool
        with patch("firmographics.enrich_competitors", return_value=[{"brand": "S"}]) as m:
            ev = get_tool("enrich_competitors_batch").fn(
                competitors=[{"brand": "S", "domain": "s.com"}], max_to_enrich=2)
        self.assertFalse(ev.skeleton, ev.error)
        self.assertEqual(m.call_args.kwargs.get("max_to_enrich"), 2)

    def test_all_four_return_evidence_not_raw_payloads(self):
        """The registry contract: .fn always yields Evidence, whatever the impl returns."""
        from tools import get_tool
        from tools.registry import Evidence
        combos = [
            ("extract_structured", dict(html="<html></html>"), "scrape.structured.extract", {}),
            ("wayback_snapshot_url", dict(url="https://x.com"),
             "scrape.wayback.latest_snapshot_url", "http://a/b"),
            ("enrich_competitor", dict(brand="A", domain="a.com"),
             "firmographics.enrich_one", {"brand": "A"}),
            ("enrich_competitors_batch", dict(competitors=[{"brand": "A", "domain": "a.com"}]),
             "firmographics.enrich_competitors", [{"brand": "A"}]),
        ]
        for name, kw, target, ret in combos:
            with self.subTest(tool=name):
                with patch(target, return_value=ret):
                    self.assertIsInstance(get_tool(name).fn(**kw), Evidence)


class TestInstrumentationIsInvisibleToMocks(unittest.TestCase):
    """A REGRESSION THIS CHANGE ALREADY CAUSED ONCE, pinned so it cannot come back.

    The first version of instrument_source closed over the function object. That silently broke
    `patch("tools.sources.trustpilot.trustpilot_reviews", ...)` at test_infra.py:2246 — the
    wrapper kept calling the captured original, so the mock was bypassed and the test started
    making REAL Trustpilot requests. The whole suite went from 213s to over 600s, which is how it
    was noticed; the tests still PASSED, which is why it would otherwise have shipped.

    Instrumentation that changes how mocks behave is worse than no instrumentation, because it
    invalidates every test that patches a source."""

    def test_the_sources_alias_is_a_separate_binding_and_always_was(self):
        """CORRECTING A FALSE PREMISE IN AN EARLIER VERSION OF THIS TEST. It asserted that
        patch("tools.sources.trustpilot.trustpilot_reviews") should reach sources.trustpilot_
        reviews. It never did — `from X import f` binds the object, so rebinding X.f leaves the
        importer's name pointing at the original. That is plain Python and predates this change.

        What matters is that instrumentation did not make it WORSE, so this pins the actual
        contract: the alias resolves independently, and the call still works and is recorded.
        Tests that need to stub a source patch `sources.<name>` (the pattern used in
        test_tools_round2, test_domain_identity, test_relevance_gate) or the HTTP layer."""
        import sources
        with patch("tools.sources.trustpilot.trustpilot_reviews", return_value=[{"m": 1}]):
            with patch("tools.sources.trustpilot.mrp_http") as http:
                http.get.return_value = MagicMock(status_code=404, text="")
                out = sources.trustpilot_reviews("acme.com")
        self.assertIsInstance(out, list, "the alias stopped returning its plain list")

    def test_patching_the_sources_attribute_still_takes_effect(self):
        import sources
        with patch("sources.is_parked_domain", return_value=True):
            self.assertIs(sources.is_parked_domain("whatever.com"), True)

    def test_a_sources_defined_impl_does_not_recurse(self):
        """Implementations defined IN sources.py resolve to the wrapper itself at the definition
        site. Without the identity guard that is infinite recursion, so assert it terminates."""
        import sources
        resp = MagicMock(status_code=200, url="https://x.com", text="<html>ok</html>")
        with patch("sources.mrp_http.get", return_value=resp):
            sources.validate_domain("x.com")     # completes or the test recurses to death

    def test_a_stubbed_source_call_is_still_recorded(self):
        """Transparency must not mean invisibility: a stubbed source still ran, so the ledger
        should say so."""
        with _LedgerCapture() as cap:
            import sources
            with patch("tools.sources.trustpilot.mrp_http") as http:
                http.get.return_value = MagicMock(status_code=404, text="")
                sources.trustpilot_reviews("acme.com")
            self.assertEqual(len(cap.tool_events("trustpilot_reviews")), 1)


class TestRecordingIsBestEffort(unittest.TestCase):
    """Instrumentation must never be able to break a working fetch."""

    def test_a_broken_ledger_does_not_break_the_fetch(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"hits": []}
        from sources import hackernews_mentions
        with patch("persistence.ledger.record_tool", side_effect=RuntimeError("ledger dead")), \
             patch("tools.sources.forums.mrp_http.get", return_value=resp):
            out = hackernews_mentions("x", limit=3)
        self.assertEqual(out, [], "a ledger failure propagated into the data path")

    def test_recording_survives_a_disabled_ledger(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"hits": []}
        ledger.disable()
        try:
            from sources import hackernews_mentions
            with patch("tools.sources.forums.mrp_http.get", return_value=resp):
                self.assertEqual(hackernews_mentions("x", limit=3), [])
        finally:
            ledger.reset("post-disable")


if __name__ == "__main__":
    unittest.main()
