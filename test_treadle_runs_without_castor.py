"""Treadle outside Castor: what the engine does when the host's modules are not there.

Inside Castor the engine still borrows two things from its host at CALL time: the
market-research tool set (the default registry for the agent loop and the gateway) and
billing (refunds for runs buried at startup). Outside Castor neither exists, and the
engine must say so plainly, never crash on an unexplained ModuleNotFoundError, and never
pretend a refund happened.

The absence is simulated by hiding exactly the host module, so a broken import INSIDE a
host module still fails loudly: "billing is not installed" and "billing is broken" are
different facts, and only the first may be skipped.
"""
from __future__ import annotations

import builtins
import contextlib
import pathlib
import unittest

from treadle import defaults, jobs


@contextlib.contextmanager
def _without(module: str):
    """Make `import <module>` behave as it would in an environment without it."""
    real = builtins.__import__

    def fake(name, *args, **kwargs):
        if name == module or name.startswith(module + "."):
            raise ModuleNotFoundError(f"No module named {module!r}", name=module)
        return real(name, *args, **kwargs)

    builtins.__import__ = fake
    try:
        yield
    finally:
        builtins.__import__ = real


class TheDefaultToolSetIsTheHosts(unittest.TestCase):
    def test_inside_castor_it_is_castors_tool_registry(self):
        from tools import TOOL_REGISTRY
        self.assertIs(defaults.default_tool_registry(), TOOL_REGISTRY)

    def test_outside_castor_it_asks_for_a_registry(self):
        with _without("tools"), self.assertRaises(LookupError) as ctx:
            defaults.default_tool_registry()
        self.assertIn("registry=", str(ctx.exception))

    def test_the_agent_loop_and_the_gateway_share_it(self):
        from treadle.harness import agent
        from tools import TOOL_REGISTRY
        self.assertIs(agent._default_registry(), TOOL_REGISTRY)


class APackCanLiveOutsideTheRepo(unittest.TestCase):
    """Found by scripts/treadle_standalone_check.py: a bare pack name was only ever looked
    for under packs/, so a pack written by someone using Treadle could not be loaded."""

    def test_a_bare_name_that_is_not_a_shipped_pack_imports_as_itself(self):
        import sys
        import tempfile
        import textwrap

        from treadle.registry import REPORT_TYPES, load_pack
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "my_outside_pack.py").write_text(textwrap.dedent('''
                from treadle import ReportType, register
                from treadle.core.section import Section
                register(ReportType(key="outside_for_test", label="x", sections=lambda b: [
                    Section(key="one", produce=lambda ctx: {"n": 1})]))
            '''))
            sys.path.insert(0, d)
            try:
                load_pack("my_outside_pack")
                self.assertIn("outside_for_test", REPORT_TYPES)
            finally:
                sys.path.remove(d)
                sys.modules.pop("my_outside_pack", None)
                REPORT_TYPES.pop("outside_for_test", None)

    def test_a_shipped_pack_still_wins(self):
        from treadle.registry import REPORT_TYPES, load_pack
        load_pack("travel_guide")
        self.assertIn("travel_guide", REPORT_TYPES)

    def test_a_name_that_is_nowhere_says_so(self):
        from treadle.registry import load_pack
        with self.assertRaises(ModuleNotFoundError):
            load_pack("no_pack_by_this_name_anywhere")


class ARefundNeedsABillingModule(unittest.TestCase):
    def test_without_billing_buried_runs_are_logged_not_refunded(self):
        with _without("billing"), self.assertLogs("mrp.jobs", level="WARNING") as logs:
            jobs._refund_buried([("job-123456789", "stale")])
        self.assertTrue(any("no billing module" in m for m in logs.output), logs.output)

    def test_a_broken_billing_module_is_not_mistaken_for_an_absent_one(self):
        real = builtins.__import__

        def broken(name, *args, **kwargs):
            if name == "billing":
                # billing exists but one of ITS imports fails
                raise ModuleNotFoundError("No module named 'stripe'", name="stripe")
            return real(name, *args, **kwargs)

        builtins.__import__ = broken
        try:
            with self.assertRaises(ModuleNotFoundError):
                jobs._refund_buried([("job-123456789", "stale")])
        finally:
            builtins.__import__ = real


if __name__ == "__main__":
    unittest.main()
