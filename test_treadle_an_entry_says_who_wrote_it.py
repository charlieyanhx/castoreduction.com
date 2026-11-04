"""Treadle phase 4: an entry is checked when it is built, and a refusal says why in code.

What a value's tier may be follows from who wrote it (test_treadle_found_values_are_confirmed
pins that). These pin the rest of an Entry's shape: a finding says where it came from and
never rests on itself, only a decline is empty, what an entry rests on comes from its writer
too, it round-trips through JSON and refuses a malformed one as a ValueError, and there is
one entry per field. A SurveyError carries a stable code, listed once, that survives a copy.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import pickle
import unittest

from test_treadle_found_values_are_confirmed import (ANSWERED, ASSUMPTION, FOUND,
                                                      FROM_THE_BRIEF, NOT_SURE)
from treadle.facts import COMPUTED, FILED, INFERRED, OBSERVED, STATED
from treadle.survey import (ASSUMED, CODES, DECLINED, FINDER, FOUND_TIERS, PERSON,
                            REQUESTER, STATING, WRITERS, Entry, SurveyError)
from treadle.survey.entry import drop, put


class AnEntryIsCheckedWhenItIsBuilt(unittest.TestCase):
    def test_a_finding_says_where_it_came_from_and_not_from_itself(self):
        for source, basis in (("   ", ()), ("ecb.europa.eu", ("currency",)),
                              ("ecb.europa.eu", ("city", "city"))):
            with self.subTest(source=source, basis=basis), self.assertRaises(ValueError):
                Entry("currency", "EUR", OBSERVED, source, FINDER, basis=basis)

    def test_only_a_decline_is_empty_and_every_writer_is_known(self):
        for case in (dict(value=None, tier=STATED, by=PERSON),
                     dict(value="EUR", tier=STATED, by="agent"),
                     dict(value="EUR", tier="certain", by=FINDER)):
            with self.subTest(**{k: str(v) for k, v in case.items()}):
                with self.assertRaises(ValueError):
                    Entry("currency", source="somewhere", **case)
        with self.assertRaises(ValueError):
            Entry("diet", None, INFERRED, "not sure", DECLINED)
        for name in ("", 5):
            with self.subTest(field=name), self.assertRaises(ValueError):
                Entry(name, "EUR", STATED, "answered", PERSON)
        for honest in (FROM_THE_BRIEF, ANSWERED, FOUND, ASSUMPTION, NOT_SURE):
            with self.subTest(source_of=honest.by), self.assertRaises(ValueError):
                dataclasses.replace(honest, source=5)        # a source is words, always
        self.assertEqual((WRITERS, STATING), ((REQUESTER, PERSON, FINDER, ASSUMED, DECLINED),
                                              (REQUESTER, PERSON)))
        self.assertEqual(set(FOUND_TIERS), {FILED, OBSERVED, COMPUTED, INFERRED}, "not stated")

    def test_what_an_entry_rests_on_comes_from_its_writer_too(self):
        cases = (
            (REQUESTER, STATED, ()),            # a brief entry rests on the brief
            (PERSON, STATED, ("city",)),        # an answer rests on nobody else's word
            (ASSUMED, INFERRED, ("city",)),     # an assumption rests on its declaration
            (FINDER, OBSERVED, ["city"]),       # a basis is a tuple, so the entry is frozen
        )
        for by, tier, basis in cases:
            with self.subTest(by=by), self.assertRaises(ValueError):
                Entry("currency", "EUR", tier, "somewhere", by, basis=basis)
        with self.assertRaises(ValueError):
            Entry("diet", None, None, "not sure", DECLINED, basis=("city",))


class AnEntryIsPlainJson(unittest.TestCase):
    def test_an_entry_round_trips_through_json(self):
        for entry in (FROM_THE_BRIEF, ANSWERED, FOUND, ASSUMPTION, NOT_SURE):
            back = Entry.from_dict(json.loads(json.dumps(entry.as_dict())))
            self.assertEqual(back, entry)
            self.assertEqual(back.value, entry.value)
        picks = Entry("interests", ["food"], STATED, "answered: What for?", PERSON)
        picks.as_dict()["value"].append("museums")
        self.assertEqual(picks.value, ["food"], "as_dict hands out a copy")

    def test_a_malformed_entry_is_refused_as_a_value_error(self):
        for data in ({**FOUND.as_dict(), "signed_by": "me"},
                     {**FOUND.as_dict(), "basis": "city"},
                     {**FOUND.as_dict(), "basis": [5]},
                     {**FOUND.as_dict(), "basis": ""},
                     {**ANSWERED.as_dict(), "source": 5},
                     ["field", "value"],
                     "currency"):
            with self.subTest(data=str(data)[:40]), self.assertRaises(ValueError):
                Entry.from_dict(data)


class EntriesAreOnePerField(unittest.TestCase):
    def test_put_replaces_an_entry_and_keeps_field_order(self):
        held = put(put((), ANSWERED), ASSUMPTION)
        held = put(held, NOT_SURE)
        self.assertEqual([e.field for e in held], ["currency", "diet", "travellers"])
        corrected = put(held, FOUND)
        self.assertEqual([e.by for e in corrected], [FINDER, DECLINED, ASSUMED])
        self.assertEqual([e.by for e in held], [PERSON, DECLINED, ASSUMED], "held unchanged")
        self.assertEqual([e.field for e in drop(corrected, "diet")], ["currency", "travellers"])
        self.assertEqual(drop(corrected, "month"), corrected)


class ARefusalCarriesAStableCode(unittest.TestCase):
    def test_a_refusal_carries_a_code_a_caller_can_branch_on(self):
        error = SurveyError("invalid_answer", "days: takes a number", field="days",
                            detail={"days": "takes a number"})
        self.assertIsInstance(error, ValueError)
        self.assertEqual(error.as_dict(), {"code": "invalid_answer",
                                           "message": "days: takes a number",
                                           "field": "days",
                                           "detail": {"days": "takes a number"}})
        error.as_dict()["detail"]["days"] = "changed"
        self.assertEqual(error.detail, {"days": "takes a number"}, "as_dict is a copy")
        self.assertEqual(json.loads(json.dumps(error.as_dict())), error.as_dict())
        detail = {"days": ["takes a number"]}
        nested = SurveyError("invalid_answer", "days", field="days", detail=detail)
        detail["days"].append("changed by the caller")
        nested.as_dict()["detail"]["days"].append("changed by a reader")
        self.assertEqual(nested.detail, {"days": ["takes a number"]}, "a deep copy, both ways")

    def test_every_code_is_listed_and_an_unlisted_one_is_a_bug(self):
        self.assertEqual(set(CODES), {"unknown_field", "invalid_answer", "invalid_finding",
                                      "not_findable", "open_dispute", "stale_question",
                                      "bad_session", "no_survey"})
        with self.assertRaises(ValueError) as ctx:
            SurveyError("oops", "a typo in the engine")
        self.assertNotIsInstance(ctx.exception, SurveyError)
        self.assertIn("oops", str(ctx.exception))

    def test_a_refusal_survives_copy_and_pickle(self):
        error = SurveyError("stale_question", "no longer open", field="currency",
                            detail={"currency": "x"})
        for back in (copy.copy(error), pickle.loads(pickle.dumps(error))):
            self.assertEqual(back.as_dict(), error.as_dict())


if __name__ == "__main__":
    unittest.main()
