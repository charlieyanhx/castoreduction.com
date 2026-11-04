"""Boot is one lifespan, read top to bottom, and it runs in the order it is written.

api.py used to register three @app.on_event("startup") handlers: refuse a misconfigured
container, resume the runs a dead worker left behind, push the coupons minted before Stripe
was configured. FastAPI 0.135 and Starlette 1.0 deprecate that form, and the three
decorators hid what mattered about them: their order. A reader had to know that Starlette
runs on_startup in registration order and then find all three registrations to learn that
refuse comes first. Now there is one context manager, passed as lifespan= to the app, and
the sequence is three lines in one place.

Three things have to hold:

  one place        the router has no on_startup handlers left, and its lifespan is the
                   module's own context manager, not Starlette's default one
  stated order     booting runs refuse, then resume, then push, and nothing else
  refuse aborts    a raise from the refuse step raises out of boot, and neither of the
                   later steps is reached: a misconfigured container must not resume a
                   paying customer's run or touch Stripe on its way down

The order test patches the three step names on the api module, so it only passes if the
lifespan looks them up at boot rather than capturing them at definition time. That is
deliberate: a decorator captures, and a lifespan that captured would be the old shape
with a new name.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

STEPS = ("_refuse_to_boot_misconfigured",
         "_resume_interrupted_runs",
         "_push_coupons_minted_without_stripe")


class _Env(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("JOBS_DB_PATH")
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        import jobs
        jobs._reset_for_tests()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = self._old
        import jobs
        jobs._reset_for_tests()

    @staticmethod
    def _boot():
        """`with` is what runs the lifespan; a bare TestClient never does."""
        from fastapi.testclient import TestClient

        import api
        return TestClient(api.app)

    @staticmethod
    def _recorders(seen: list, refuse_raises: Exception | None = None):
        """Patch the three steps on the api module with functions that log their name.

        Patched on the module, because that is where the lifespan looks them up. The
        refuse step can be told to raise, which is how a misconfigured boot presents.
        """
        def recorder(name):
            def step():
                seen.append(name)
                if name == STEPS[0] and refuse_raises is not None:
                    raise refuse_raises
            return step

        return [patch("api." + name, side_effect=recorder(name)) for name in STEPS]


class BootIsOneLifespan(_Env):
    def test_the_router_has_no_startup_handlers_left(self):
        import api
        self.assertEqual(
            api.app.router.on_startup, [],
            "boot is still spread across on_event handlers; every one of them is a "
            "place a reader has to find to learn the order")

    def test_the_router_boots_through_the_module_lifespan(self):
        """The router's lifespan is set, and it is ours.

        Not an identity check: include_router wraps the app's lifespan around every
        router it pulls in, so what the router holds is FastAPI's merged chain. What
        matters is that the chain reaches the module's context manager, and the way to
        see that is to run it and watch a step fire. No TestClient here: this is the
        router's own lifespan being driven, nothing else.
        """
        import asyncio

        import api
        self.assertTrue(callable(api.app.router.lifespan_context))
        seen: list[str] = []
        r, s, p = self._recorders(seen)

        async def drive():
            async with api.app.router.lifespan_context(api.app):
                pass

        with r, s, p:
            asyncio.run(drive())
        self.assertIn(STEPS[0], seen,
                      "the router's lifespan ran and never reached the module's boot "
                      "sequence: the app is booting through something else")

    def test_the_three_steps_are_still_plain_functions(self):
        """Other tests and one-off tools call them by name; the shape change must not
        have taken the names with it."""
        import api
        for name in STEPS:
            self.assertTrue(callable(getattr(api, name, None)), name)


class BootRunsRefuseThenResumeThenPush(_Env):
    def test_the_steps_run_in_the_stated_order(self):
        seen: list[str] = []
        r, s, p = self._recorders(seen)
        with r, s, p:
            with self._boot() as c:
                self.assertEqual(c.get("/healthz").status_code, 200)
        self.assertEqual(seen, list(STEPS),
                         "refuse first, resume second, push last; anything else means "
                         "a misconfigured container could do work before it is told to "
                         "exit, or a paid run waits behind the coupon backlog")


class ARefusedBootStopsThere(_Env):
    def test_a_raise_from_refuse_aborts_boot_and_the_other_steps_never_run(self):
        seen: list[str] = []
        r, s, p = self._recorders(seen, refuse_raises=RuntimeError("SESSION_SECRET unset"))
        with r, s, p:
            with self.assertRaises(RuntimeError):
                with self._boot():
                    self.fail("a boot the refuse step rejected still started serving")
        self.assertEqual(seen, [STEPS[0]],
                         "the container was told to exit and kept booting: it resumed "
                         "runs or reached for Stripe after being refused")


if __name__ == "__main__":
    unittest.main()
