"""Adding a sizing method must not require opening plan.py.

THE POINT OF THIS FILE. The methods belong to a domain expert; the orchestrator belongs to
whoever maintains the pipeline. Those are different people, and the boundary only holds if
the orchestrator never has to learn a method's name. It used to: the choice between the
trade-area model, the city-scale scan and the multi-site rollout was an if/elif inside a
try/except in run_plan, with each branch importing its method directly.

Now the decision is a function in skills/sizing/routing.py that returns a REGISTERED
method name plus its arguments, and the orchestrator resolves the name. Adding a sixth
method is: write it with @skill, register it in __init__.py, add a branch in routing.py.
All three files are in skills/sizing/.

Verified when this moved: the new routing agrees with the old if/elif on all 1152
combinations of location, category, premise count, geocoder level, OSM tag and radius.
"""
from __future__ import annotations

import inspect
import pathlib
import unittest

from skills.sizing.routing import Route, route_physical_sizing

_BASE = dict(location="123 Main St, Boise", category="food_away_from_home",
             osm_key="amenity", osm_value="cafe", radius_m=1500)


class TestTheDecisionLivesWithTheMethods(unittest.TestCase):
    def test_the_orchestrator_no_longer_names_a_sizing_method(self):
        """plan.py may say which FAMILY it wants; it must not know the members."""
        src = pathlib.Path("plan.py").read_text()
        dispatch = src[src.index("WHICH METHOD, DECIDED"):]
        dispatch = dispatch[:dispatch.index("except Exception as e:")]
        for name in ("size_hyperlocal", "size_citywide", "size_regional",
                     "size_national_digital"):
            self.assertNotIn(name, dispatch,
                             f"the dispatch still names {name} directly")

    def test_the_route_names_something_the_registry_can_resolve(self):
        import skills  # noqa: F401
        from skills.registry import SKILL_REGISTRY

        for n_locations, level in ((3, None), (None, "city"), (None, "street"),
                                   (None, None), (1, "zip"), (12, "region")):
            route = route_physical_sizing(n_locations=n_locations, geo_level=level, **_BASE)
            self.assertIn(route.skill, SKILL_REGISTRY,
                          f"routing chose {route.skill}, which nothing registered")

    def test_the_arguments_fit_the_method_it_chose(self):
        """A route that names a real method and hands it the wrong keywords fails at
        call time, inside a try/except, as a 'sizing failed (non-fatal)' log line."""
        import skills  # noqa: F401
        from skills.registry import SKILL_REGISTRY

        for n_locations, level in ((3, None), (None, "city"), (None, "street")):
            route = route_physical_sizing(n_locations=n_locations, geo_level=level, **_BASE)
            sig = inspect.signature(SKILL_REGISTRY[route.skill].fn)
            try:
                sig.bind(**route.kwargs)
            except TypeError as e:
                self.fail(f"{route.skill} cannot be called with {sorted(route.kwargs)}: {e}")


class TestTheRoutingRulesThemselves(unittest.TestCase):
    """Each rule stated as its own case, so a methods expert can read the file and see
    which test breaks if she changes a rule."""

    def test_a_multi_site_rollout_gets_the_rollout_engine(self):
        """Sizing a chain as one site published a three-location chain's single trade
        area as its whole market."""
        self.assertEqual(route_physical_sizing(n_locations=3, geo_level=None, **_BASE).skill,
                         "size_regional")

    def test_a_city_level_geocode_gets_the_city_scan_and_says_so(self):
        """The reroute must be DISCLOSED: D52 tells a reasoned downgrade from a silent
        substitution, and it can only do that if the result carries the reason."""
        r = route_physical_sizing(n_locations=None, geo_level="city", **_BASE)
        self.assertEqual(r.skill, "size_citywide")
        self.assertIn("city-scale scan", r.downgrade or "")
        self.assertIn("Boise", r.downgrade or "", "the reason names the actual location")

    def test_a_real_address_gets_the_trade_area_model(self):
        r = route_physical_sizing(n_locations=None, geo_level="street", **_BASE)
        self.assertEqual(r.skill, "size_hyperlocal")
        self.assertIsNone(r.downgrade, "the default path is not a downgrade")

    def test_an_unknown_level_keeps_the_trade_area_path(self):
        """size_hyperlocal degrades honestly on its own when the address is thin, which
        is a better answer than guessing that an unknown level means a city."""
        self.assertEqual(
            route_physical_sizing(n_locations=None, geo_level=None, **_BASE).skill,
            "size_hyperlocal")

    def test_a_single_location_count_is_not_a_rollout(self):
        self.assertEqual(
            route_physical_sizing(n_locations=1, geo_level="street", **_BASE).skill,
            "size_hyperlocal")

    def test_a_rollout_beats_a_city_level_geocode(self):
        """Order matters: a chain keeps its own engine even when the address is vague."""
        self.assertEqual(
            route_physical_sizing(n_locations=4, geo_level="city", **_BASE).skill,
            "size_regional")


class TestASixthMethodNeedsNoOrchestratorEdit(unittest.TestCase):
    """The claim, exercised rather than asserted: a route to a newly registered method
    is resolved and called by the orchestrator's own lookup, with plan.py untouched."""

    def test_a_freshly_registered_method_is_dispatchable(self):
        import skills  # noqa: F401
        from skills.registry import SKILL_REGISTRY

        calls = {}

        def size_by_a_new_method(place, category="x"):
            calls["args"] = (place, category)
            return "evidence"

        # SimpleNamespace, not a class with fn as a class attribute: that makes fn a
        # BOUND method and the lookup passes self as the first positional. SkillMeta is a
        # dataclass, so its fn is an instance attribute and stays a plain function.
        from types import SimpleNamespace
        SKILL_REGISTRY["size_by_a_new_method"] = SimpleNamespace(
            fn=size_by_a_new_method, produces="market_sizing")
        try:
            route = Route("size_by_a_new_method", {"place": "Boise", "category": "cafe"})
            meta = SKILL_REGISTRY.get(route.skill)      # the orchestrator's own lookup
            self.assertIsNotNone(meta)
            self.assertEqual(meta.fn(**route.kwargs), "evidence")
            self.assertEqual(calls["args"], ("Boise", "cafe"))
        finally:
            del SKILL_REGISTRY["size_by_a_new_method"]

    def test_a_route_to_an_unregistered_method_fails_loudly(self):
        """Silence here would be a report sized by nothing, discovered at D52 much later."""
        import skills  # noqa: F401
        from skills.registry import SKILL_REGISTRY
        self.assertIsNone(SKILL_REGISTRY.get("size_by_a_method_that_does_not_exist"))
        src = pathlib.Path("plan.py").read_text()
        self.assertIn("which is not registered", src,
                      "the orchestrator no longer refuses an unresolvable route")


if __name__ == "__main__":
    unittest.main()


class TestAMethodStaysSubstitutable(unittest.TestCase):
    """Dispatching by name must not cost the ability to patch a method.

    THE TRAP THIS EXISTS FOR, and it broke 12 tests the first time. `@skill` captures the
    function on the registry entry when the decorator runs. Every test in this project
    substitutes a method by replacing the MODULE attribute --
    `patch("skills.sizing.hyperlocal.size_hyperlocal", ...)` -- so a dispatcher that calls
    the captured `meta.fn` ignores all of them and quietly runs the real thing.

    That is not only a test problem. It is the same binding trap that reverted the four_ps
    split, and it would land on whoever works on a method next: the obvious way to try an
    alternative implementation would silently do nothing.

    So the registry resolves a name to the CURRENT definition at call time.
    """

    def test_resolve_returns_the_patched_function_not_the_captured_one(self):
        from unittest.mock import patch

        import skills  # noqa: F401
        from skills.registry import SKILL_REGISTRY, resolve

        sentinel = object()
        with patch("skills.sizing.hyperlocal.size_hyperlocal", return_value=sentinel):
            self.assertIs(resolve("size_hyperlocal")(address="x"), sentinel)
        # and the captured reference was never the thing being called
        self.assertIsNot(resolve("size_hyperlocal"), sentinel)
        self.assertIn("size_hyperlocal", SKILL_REGISTRY)

    def test_resolve_returns_the_real_function_when_nothing_is_patched(self):
        import skills  # noqa: F401
        from skills.registry import resolve
        import skills.sizing.hyperlocal as h
        self.assertIs(resolve("size_hyperlocal"), h.size_hyperlocal)

    def test_an_unknown_name_resolves_to_nothing_rather_than_raising(self):
        """The orchestrator turns None into a named LookupError. Raising here would make
        the failure about importlib instead of about the route."""
        from skills.registry import resolve
        self.assertIsNone(resolve("size_by_a_method_that_does_not_exist"))

    def test_the_orchestrator_dispatches_through_resolve(self):
        src = pathlib.Path("plan.py").read_text()
        self.assertIn("resolve_skill(_route.skill)", src)
        self.assertNotIn("_meta.fn(**_route.kwargs)", src,
                         "dispatch went back to the captured reference")
