"""Treadle phase 4: 'not sure' is one constant, and it goes wherever a brief goes.

A caller who does not know an answer passes treadle.DECLINE, Castor's {"unknown": true}.
That value rides inside a brief, and the engine deep-copies every brief before anything
reads it; a client sends it as JSON. So the constant copies, pickles and serialises exactly
as the plain dict does, and is still one shared value that nobody can rewrite.
"""
from __future__ import annotations

import copy
import json
import pickle
import unittest

import treadle
from treadle.inputs import DECLINE, is_decline
from treadle.registry import REPORT_TYPES


class NotSureIsOneConstant(unittest.TestCase):
    def test_not_sure_is_none_or_castors_unknown(self):
        for decline in (None, DECLINE, {"unknown": True}):
            self.assertTrue(is_decline(decline))
        for answer in (0, [], "", False, {"unknown": False}, {"unknown": "yes"}):
            self.assertFalse(is_decline(answer), answer)
        self.assertEqual(DECLINE, {"unknown": True})

    def test_nobody_can_rewrite_the_shared_constant(self):
        attempts = (("__setitem__", ("unknown", False)), ("__delitem__", ("unknown",)),
                    ("update", ({"unknown": False},)), ("pop", ("unknown",)),
                    ("popitem", ()), ("clear", ()), ("setdefault", ("why", 1)),
                    ("__ior__", ({"unknown": False},)))
        for method, args in attempts:
            with self.subTest(method), self.assertRaises(TypeError):
                getattr(DECLINE, method)(*args)
        self.assertEqual(DECLINE, {"unknown": True}, "still 'not sure' after every attempt")
        mine = DECLINE | {"why": "never been"}
        mine["unknown"] = False
        self.assertEqual(DECLINE, {"unknown": True}, "a dict built from it is the caller's")


class NotSureTravelsWithTheBrief(unittest.TestCase):
    def test_not_sure_survives_every_copy_a_brief_goes_through(self):
        brief = {"city": "Lisbon", "diet": DECLINE}
        copies = {"deepcopy": copy.deepcopy(brief), "copy": copy.copy(brief),
                  "pickle": pickle.loads(pickle.dumps(brief)),
                  "json": json.loads(json.dumps(brief))}
        for how, back in copies.items():
            with self.subTest(how):
                self.assertTrue(is_decline(back["diet"]))
                self.assertEqual(back["diet"], {"unknown": True})
        self.assertEqual(json.dumps(DECLINE), '{"unknown": true}', "Castor's spelling")
        for back in (copy.deepcopy(DECLINE), pickle.loads(pickle.dumps(DECLINE))):
            self.assertIs(back, DECLINE, "a constant comes back as itself")

    def test_a_brief_that_says_not_sure_goes_through_the_door(self):
        seen = []
        treadle.register(treadle.ReportType(
            key="not_sure_for_test", label="x",
            runner=lambda brief, progress=None: seen.append(brief) or {}))
        try:
            treadle.run_report("not_sure_for_test", {"city": "Lisbon", "diet": DECLINE})
        finally:
            del REPORT_TYPES["not_sure_for_test"]
        self.assertTrue(is_decline(seen[0]["diet"]), "the door deep-copies the brief")


if __name__ == "__main__":
    unittest.main()
