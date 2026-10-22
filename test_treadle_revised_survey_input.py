"""Skipped optional questions do not veto information in the opening description."""
from copy import deepcopy

import pytest

from packs.market_research_next.business_object.integration import _sources
from packs.market_research_next.prep import prepare
from test_treadle_venture_prep import model_output


def test_skipped_question_can_be_interpreted_from_the_original_description():
    brief = {
        "description": "Tutoring centres in Toronto and Vancouver with online lessons across Canada.",
        "intake": {
            "confirmed": True,
            "facts": {"original_description": "Tutoring centres in Toronto and Vancouver."},
            "slots": {"geography": {"unknown": True}},
            "unknowns": ["geography", "pricing"],
            "unanswered": ["geography"],
        },
    }
    before = deepcopy(brief)
    response = model_output()
    response["venture"].update(geography="Canada", pricing="$100")
    result = prepare(brief, model=lambda **_: response)
    attributes = result["venture"]["attributes"]
    assert attributes["geography"]["value"] == "Canada"
    assert attributes["geography"]["basis"] == "inferred"
    assert attributes["pricing"]["basis"] == "unknown"
    assert attributes["pricing"]["value"] is None
    assert result["venture"]["customer_inputs"] == before == brief
    statements, paths = _sources(brief)
    selected = {paths[item["id"]]: item["text"] for item in statements}
    assert "/intake/facts/original_description" in selected
    assert "/intake/slots/geography" not in selected
    assert not any("for geography" in value and "explicitly marks" in value
                   for value in selected.values())
    assert any("for pricing" in value and "explicitly marks" in value
               for value in selected.values())


def test_skipped_answers_do_not_resurrect_old_structured_facts():
    brief = {"description": "Our operating plan is still changing.", "intake": {
        "confirmed": True,
        "facts": {"geography": "Superseded region", "service_capacity": "Obsolete capacity"},
        "slots": {"geography": {"unknown": True}, "service_capacity": {"unknown": True}},
        "unknowns": ["geography", "service_capacity"],
        "unanswered": ["geography", "service_capacity"],
    }}
    result = prepare(brief, model=lambda **_: model_output())
    assert result["venture"]["attributes"]["geography"]["value"] is None
    statements, _ = _sources(brief)
    assert len(statements) == 1
    assert "Superseded" not in str(statements)
    assert "Obsolete" not in str(statements)


def test_a_direct_explicit_unknown_still_wins_over_a_skipped_followup():
    brief = {"description": "Our location is undecided.", "geography": {"unknown": True},
             "intake": {"confirmed": True, "unknowns": ["geography"],
                        "unanswered": ["geography"]}}
    response = model_output()
    response["venture"]["geography"] = "Canada"
    result = prepare(brief, model=lambda **_: response)
    assert result["venture"]["attributes"]["geography"]["basis"] == "unknown"
    statements, paths = _sources(brief)
    assert any(paths[item["id"]] == "/geography" and "explicitly marks" in item["text"]
               for item in statements)


@pytest.mark.parametrize("unanswered", ["geography", [None], [1], {"geography": True}])
def test_unanswered_metadata_must_be_a_list_of_field_names(unanswered):
    with pytest.raises(ValueError, match="intake.unanswered"):
        prepare({"description": "Example", "intake": {"unanswered": unanswered}},
                model=lambda **_: model_output())
