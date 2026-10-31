"""Treadle phase 4: a survey kept as JSON comes back only if the engine could have written it.

A survey is a value, so it can be kept between calls as plain JSON (Survey.as_dict) and
taken back (Survey.from_dict). Whoever holds that JSON can edit it, so nothing in it is
taken on trust: the brief is checked as start() checks it, and each stored entry is
admitted only if the engine could have written it. A tier its writer cannot hold, a value
not in its input's one shape, a brief entry the brief does not give, an assumption that
is not the default, a finding for what only a person may say, a finding resting on a
value the survey does not hold, two entries for one field, and a dispute with no
statement to contradict are each refused as 'bad_session'.

THE STORED ENTRIES ARE THE WHOLE SURVEY. The engine can lose a brief entry: a finding put
in place of the brief's 'not sure' goes when what it was found from changes, and nothing
comes back. A session that left out a brief entry therefore stays without it, and its
question is asked again; laying the brief underneath would answer that question for the
person.
"""
from __future__ import annotations

import json
import unittest

from test_treadle_found_values_are_confirmed import KEY, SRC, TRIP, eur, guide
from treadle import Survey
from treadle.facts import INFERRED, OBSERVED, STATED
from treadle.inputs import DECLINE
from treadle.registry import load_pack
from treadle.survey import ASSUMED, DECLINED, FINDER, PERSON, REQUESTER, SurveyError
from treadle.survey.question import PROTOCOL


def said(field, value, tier, by, source="answered", basis=()) -> dict:
    """An entry as a stored session holds it."""
    return {"field": field, "value": value, "tier": tier, "source": source, "by": by,
            "basis": list(basis)}


def swap(session: dict, entry: dict) -> dict:
    """`session` holding `entry` in place of whatever it held for that field, so a forgery
    is refused for what it says, never for being a second entry."""
    kept = [e for e in session["entries"] if e["field"] != entry["field"]]
    return {**session, "entries": [*kept, entry]}


def ruled(survey: Survey, reply) -> Survey:
    """`survey` after `reply` to its currency question, whatever else is still open."""
    ask = {q["field"]: q for q in survey.questions()}["currency"]
    return survey.answer("currency", reply, reply_to=ask["id"])


class ASurveyKeepsAsJson(unittest.TestCase):
    def setUp(self):
        load_pack(KEY)

    def test_a_survey_round_trips_through_json(self):
        base = guide({"city": " Lisbon ", "diet": DECLINE, "travellers": DECLINE}).answer(
            "interests", ["museums", "food"])
        found, disputed = eur(base), eur(guide({**TRIP, "currency": "GBP"}))
        # The brief's 'not sure' to currency, replaced by a finding the city change drops.
        lost = eur(guide({**TRIP, "currency": DECLINE})).answer("city", "Porto")
        self.assertEqual((lost.entry("currency"), lost.next_question()["field"]),
                         (None, "currency"), "asked again, as nothing stands in")
        # Found from a stated traveller count that 'not sure' then made an assumption of
        # the same value: the finding stays, above what it now rests on, and loads so.
        weakened = guide({**TRIP, "travellers": 1}).found(
            "currency", "EUR", SRC, from_fields=("travellers",)).answer("travellers", DECLINE)
        self.assertEqual((weakened.entry("currency").tier,
                          weakened.entry("travellers").tier), (OBSERVED, INFERRED))
        for survey in (base, found, ruled(found, DECLINE), found.answer("currency", "GBP"),
                       ruled(found, "GBP"), disputed, ruled(disputed, DECLINE), lost,
                       weakened):
            data = json.loads(json.dumps(survey.as_dict()))
            back = Survey.from_dict(data)
            self.assertEqual(back, survey)
            self.assertEqual((back.questions(), back.as_dict()), (survey.questions(), data))
        self.assertEqual((sorted(data), data["protocol"], data["type"]), (sorted(
            ["protocol", "type", "brief", "entries", "disputes", "settled"]), PROTOCOL, KEY))

    def test_a_forged_session_is_refused(self):
        bare, found = guide(TRIP).as_dict(), eur(guide(TRIP)).as_dict()
        stated = guide({**TRIP, "currency": "GBP", "travellers": DECLINE}).as_dict()
        brief = ("the brief", ["brief"])
        finding = said("currency", "EUR", OBSERVED, FINDER, SRC, ["city"])
        forged = {
            "an assumption that is not the default": said("travellers", 2, INFERRED, ASSUMED),
            "a finder claiming a person's word": said("currency", "EUR", STATED, FINDER, SRC),
            "not what the brief says": said("city", "Porto", STATED, REQUESTER, *brief),
            "what the brief left unsure": said("travellers", 1, STATED, REQUESTER, *brief),
            "an unknown field": said("colour", "blue", STATED, PERSON),
            "a finding only a person may give": said("days", 4, OBSERVED, FINDER, SRC),
            "a value its input refuses": said("days", 0, STATED, PERSON),
            "a code spelled as typed": said("currency", "eur", STATED, PERSON),
            "a whole number as a float": said("days", 3.0, STATED, PERSON),
            "picks out of order": said("interests", ["museums", "food"], STATED, PERSON),
            "text as typed": said("city", " Lisbon ", STATED, PERSON),
            "nothing where a default stands in": said("travellers", None, None, DECLINED),
            "found from an input it lacks": {**finding, "basis": ["colour"]},
            "found from a field with no value": {**finding, "basis": ["month"]},
        }
        sessions = {case: swap(stated, entry) for case, entry in forged.items()}
        rival = said("currency", "USD", OBSERVED, FINDER, "a forum", ["city"])
        sessions.update({
            "a second entry for one field": {**stated, "entries": [
                *stated["entries"], said("days", 4, STATED, PERSON)]},
            "a dispute with no statement": {**bare, "disputes": [finding]},
            "a dispute against a finding": {**found, "disputes": [finding]},
            "a dispute that is not a finding": {**stated, "disputes": [
                said("currency", "EUR", STATED, PERSON)]},
            "a dispute as typed": {**stated, "disputes": [{**finding, "value": "eur"}]},
            "a dispute that agrees": {**stated, "disputes": [{**finding, "value": "GBP"}]},
            "a dispute from a field with no value": {**stated, "disputes": [
                {**finding, "basis": ["month"]}]},
            "two disputes for one field": {**stated, "disputes": [finding, rival]},
            "another protocol": {**stated, "protocol": "treadle.survey/0"},
            "no protocol": {k: v for k, v in stated.items() if k != "protocol"},
            "an unknown report": {**stated, "type": "nope_for_test"},
            "a brief field no input declares": {**stated, "brief": {"colour": "blue"}},
            "not an object": [stated],
            "seals that are not a list": {**stated, "settled": 5},
            "seals spelled as one string": {**stated, "settled": "abc"},
            "a seal that is not text": {**stated, "settled": [5]},
        })
        for case, session in sessions.items():
            with self.subTest(case):
                with self.assertRaises(SurveyError) as ctx:
                    Survey.from_dict(session)
                self.assertEqual(ctx.exception.code, "bad_session")
        honest = Survey.from_dict({**stated, "disputes": [finding]})
        self.assertEqual(honest.dispute("currency").value, "EUR", "an honest dispute loads")
        held = Survey.from_dict(swap(stated, finding)).entry("currency")
        self.assertEqual((held.by, held.basis), (FINDER, ("city",)), "found after 'not sure'")


if __name__ == "__main__":
    unittest.main()
