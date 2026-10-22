"""The revised survey preserves complex answers and their authorship."""
from unittest.mock import patch

import pytest

import intake
import slots
from intake_tree import classify_turn, plan_questions
from packs.market_research_next.business_object.integration import _sources
from survey_questions import FIELDS, INTRODUCTION, OPENING_QUESTION, QUESTIONS


@pytest.fixture(autouse=True)
def isolated_survey_database(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBS_DB_PATH", str(tmp_path / "survey.sqlite"))


def test_fixed_groups_are_optional_paragraphs_for_every_business():
    for product in ("A coffee shop.", "Subscription software.", "A satellite system."):
        ex = {"product": product, "success_target": "$500k", "geography": "Canada"}
        questions = plan_questions(ex, classify_turn(ex))
        assert [q["field"] for q in questions] == list(FIELDS)
        assert [q["group"] for q in questions] == ["core"] * 7 + ["specific"] * 7
        assert all(q["input_kind"] == "para" and q["optional"] for q in questions)
        assert [q["question"] for q in questions] == [q["question"] for q in QUESTIONS]


def test_opening_uses_the_approved_invitation_to_give_detail():
    assert intake.start_session()["assistant_message"] == OPENING_QUESTION


def test_complex_answers_survive_as_text_and_reach_prep_sources():
    original = ("Toronto and Vancouver studios, online classes throughout Canada, "
                "and curriculum licenses sold to schools.")
    session = {"survey_version": "2026-10", "extracted": {}, "original_description": original}
    answers = {
        "monthly_cost_estimate": "$4,000 rent, $12,000 salaries, and $800 software per month",
        "service_capacity": "120 studio students weekly plus 500 online subscribers monthly",
        "unit_economics": "Studio lesson costs $20 and sells for $50; licenses cost $900 and sell for $4,000 annually",
        "delivery_mode": "In person at two studios and online",
        "fixed_costs": "Two leases; teachers shared across physical and online lessons",
        "acquisition_channels": "School referrals, SEO, and foot traffic",
        "scalability": "Contract teachers; software scales separately",
        "constraints": "Child-safety checks and local teaching licenses",
    }
    intake.apply_form_answers(session, answers)
    intake.mark_confirmed(session)
    record = session["intake_record"]
    for field, value in answers.items():
        assert record["facts"][field] == value
        assert record["slots"][field]["kind"] == slots.TEXT
        assert record["slots"][field]["value"] == value
        assert value in session["final_description"]
    assert "pricing" not in record["facts"]
    assert record["facts"]["original_description"] == original
    sources, _ = _sources({"description": session["final_description"], "intake": record})
    source_text = " ".join(source["text"] for source in sources)
    for value in (original, *answers.values()):
        assert value in source_text


def test_blank_is_unknown_but_omitted_answer_is_unchanged():
    session = {"extracted": {"scalability": "Contract teaching staff"}}
    intake.apply_form_answers(session, {"service_capacity": ""})
    assert session["extracted"]["service_capacity"] == {"unknown": True}
    assert session["extracted"]["scalability"] == "Contract teaching staff"
    assert "scalability" not in session.get("founder_fields", [])
    record = intake.intake_record(session)
    assert record["unknowns"] == ["service_capacity"]
    assert record["unanswered"] == ["service_capacity"]
    intake.apply_form_answers(session, {"service_capacity": "40 clients per week"})
    assert intake.intake_record(session)["unanswered"] == []


def test_skipping_location_does_not_discard_original_location_or_claim_refusal():
    session = {"survey_version": "2026-10", "extracted": {},
               "original_description": "Tutoring studios in Toronto and Vancouver."}
    intake.apply_form_answers(session, {"geography": ""})
    intake.mark_confirmed(session)
    assert "Toronto and Vancouver" in session["final_description"]
    assert "does not know" not in session["final_description"]
    assert session["intake_record"]["unanswered"] == ["geography"]


def test_chat_ends_after_fixed_questions_even_without_inferred_required_fields():
    with patch.object(intake, "call_json", return_value={
        "extracted": {"product": "A learning platform"}, "next_action": "ask",
    }):
        response = intake.start_session("A learning platform")
        sid = response["session_id"]
        for _ in range(14):
            if response["ready"]:
                break
            response = intake.process_message(sid, "not sure")
    assert response["ready"]
    session = intake.get_session(sid)
    asked = [m["content"] for m in session["messages"] if m["role"] == "assistant"]
    assert asked[:-1] == [q["question"] for q in QUESTIONS]


def test_chat_cost_answer_accepts_words_without_a_numeric_guess():
    session = {"survey_version": "2026-10", "extracted": {
        "monthly_cost_estimate": "The extractor guessed $1,000"}}
    answer = "Rent and part-time teacher salaries; quotes are still pending"
    intake._file_pending_answer(session, "monthly_cost_estimate", answer)
    assert slots.text(session["extracted"]["monthly_cost_estimate"]) == answer
    assert session["extracted"]["monthly_cost_estimate"]["kind"] == slots.TEXT


def test_inferred_prefills_are_not_promoted_on_confirmation():
    session = {"survey_version": "2026-10", "original_description": "An online learning platform.",
               "extracted": {"business_model": "Monthly subscription", "geography": "US",
                             "target_customer": "Large companies"}}
    questions = {q["field"]: q for q in intake.form_questions(session)}
    assert questions["business_model"]["source"] == "inferred"
    assert questions["business_model"]["confirmed"] is False
    intake.apply_form_answers(session, {"business_model": "Annual school licenses"})
    intake.mark_confirmed(session)
    assert session["intake_record"]["facts"] == {
        "business_model": "Annual school licenses",
        "original_description": "An online learning platform.",
    }
    assert session["confirmed_facts"] == session["intake_record"]["facts"]
    assert "Large companies" not in session["final_description"]
    assert "Monthly subscription" not in session["final_description"]
    assert intake.form_questions(session)[5]["confirmed"] is True


def test_initial_description_survives_lossy_extraction():
    original = ("We operate two clinics in Toronto and Montreal and a subscription app; "
                "clinics serve local patients and employers license the app in Quebec.")
    with patch.object(intake, "call_json", return_value={
        "extracted": {"product": "A healthcare app."}, "next_action": "ask",
    }):
        first = intake.start_session(original)
    session = intake.get_session(first["session_id"])
    assert session["original_description"] == original
    intake.mark_confirmed(session)
    assert original in session["final_description"]
    assert session["intake_record"]["facts"]["original_description"] == original


def test_all_new_fields_round_trip_through_composed_labels():
    answers = {field: "Answer for " + field for field in FIELDS}
    recovered = intake.facts_from_description(intake._synthesize_from_extracted(answers))
    assert recovered == answers


def test_form_metadata_and_group_completion_are_restorable():
    from fastapi.testclient import TestClient
    import api

    with TestClient(api.app) as client:
        sid = client.post("/intake/start", json={}).json()["session_id"]
        form = client.get(f"/intake/{sid}/form").json()
        assert form["opening_question"] == OPENING_QUESTION
        assert form["introduction"] == INTRODUCTION
        assert form["submitted_groups"] == []
        answers = {field: "" for field in FIELDS[:7]}
        assert client.post(f"/intake/{sid}/form", json={"answers": answers}).status_code == 200
        restored = client.get(f"/intake/{sid}/form").json()
        assert restored["submitted_groups"] == ["core"]
        assert restored["report_style"] == "full"


def test_revised_form_upgrades_legacy_draft_without_confirming_old_guesses():
    from fastapi.testclient import TestClient
    import api

    with TestClient(api.app) as client:
        sid = client.post("/intake/start", json={}).json()["session_id"]
        session = intake.get_session(sid)
        session.pop("survey_version")
        session["extracted"]["business_model"] = "Extractor guessed advertising"
        session["extracted"]["geography"] = "Extractor guessed US"
        intake.save_session(session)
        answer = "$4,000 rent and $12,000 staff monthly"
        rejected = client.post(f"/intake/{sid}/form", json={
            "survey_version": "future", "answers": {"monthly_cost_estimate": answer},
        })
        assert rejected.status_code == 400
        assert "survey_version" not in intake.get_session(sid)
        accepted = client.post(f"/intake/{sid}/form", json={
            "survey_version": "2026-10", "answers": {"monthly_cost_estimate": answer},
        })
        assert accepted.status_code == 200
        confirmed = client.post(f"/intake/{sid}/confirm", json={"commit": False}).json()
        assert confirmed["intake_record"]["facts"] == {"monthly_cost_estimate": answer}
        assert confirmed["intake_record"]["slots"]["monthly_cost_estimate"]["kind"] == slots.TEXT
        assert "Extractor guessed" not in confirmed["final_description"]


def test_confirmation_correction_replaces_a_skipped_answer_in_description():
    from fastapi.testclient import TestClient
    import api

    with TestClient(api.app) as client:
        sid = client.post("/intake/start", json={}).json()["session_id"]
        client.post(f"/intake/{sid}/form", json={"answers": {"fixed_costs": ""}})
        confirmed = client.post(f"/intake/{sid}/confirm", json={
            "commit": False, "corrections": {"fixed_costs": "Rent $400, salaries $800"},
        }).json()
        assert "Rent $400, salaries $800" in confirmed["final_description"]
        assert confirmed["intake_record"]["unanswered"] == []


@pytest.mark.parametrize("answer", [
    "$4,000 rent, $12,000 salaries, and $800 software per month",
    "USD 4,000 rent per month",
    "USD 4,000–8,000 per month",
    "USD 4,000 per month plus USD 10,000 each year",
    "CAD 5,000 per month",
    "EUR 5,000 per month",
    "$5,000 per month",
    "5000",
    "USD 60,000 per year",
    "USD 5,000",
])
def test_revised_cost_paragraph_does_not_become_a_false_sourced_total(answer):
    from pricing import _apply_founder_cost, founder_cost_anchor

    session = {"survey_version": "2026-10", "extracted": {}}
    intake.apply_form_answers(session, {"monthly_cost_estimate": answer})
    intake.mark_confirmed(session)
    record = session["intake_record"]
    anchor, rent = founder_cost_anchor({"intake": record})
    cost = _apply_founder_cost(
        {"monthly_fixed_cost": 20000.0, "sourced": False, "source": "estimate"}, anchor, rent)
    assert anchor is None
    assert cost["monthly_fixed_cost"] == 20000.0
    assert cost["sourced"] is False
    assert record["facts"]["monthly_cost_estimate"] == answer
    assert answer in session["final_description"]


@pytest.mark.parametrize("answer", [
    "USD 16,800 per month",
    "US$16,800/month",
    "$16,800 USD per month",
    "Total monthly operating cost: USD 16,800",
    "Roughly USD 16,800 monthly",
])
def test_revised_unambiguous_monthly_usd_total_can_anchor(answer):
    from pricing import _apply_founder_cost, founder_cost_anchor

    session = {"survey_version": "2026-10", "extracted": {}}
    intake.apply_form_answers(session, {"monthly_cost_estimate": answer})
    intake.mark_confirmed(session)
    anchor, rent = founder_cost_anchor({"intake": session["intake_record"]})
    cost = _apply_founder_cost(
        {"monthly_fixed_cost": 20000.0, "sourced": False}, anchor, rent)
    assert anchor == 16800.0
    assert cost["monthly_fixed_cost"] == 16800.0
    assert cost["sourced"] is True
    assert cost["model_benchmark_monthly_fixed"] == 20000.0


def test_typed_legacy_answer_migrates_with_currency_unit_and_period():
    from pricing import founder_cost_anchor

    session = {"survey_version": "2026-10", "extracted": {}}
    previous = slots.make("monthly_cost_estimate", "USD 5000 per month", source="form")
    intake.apply_form_answers(session, {"monthly_cost_estimate": previous})
    stored = session["extracted"]["monthly_cost_estimate"]
    assert stored["kind"] == slots.TEXT
    assert stored["value"] == "USD 5,000 per month"
    assert founder_cost_anchor({"intake": intake.intake_record(session)}) == (5000.0, None)


def test_resubmitting_blank_group_preserves_explicit_unknown_authority():
    from fastapi.testclient import TestClient
    import api

    with TestClient(api.app) as client:
        sid = client.post("/intake/start", json={}).json()["session_id"]
        session = intake.get_session(sid)
        session["extracted"]["geography"] = {"unknown": True}
        intake.save_session(session)
        answers = {field: "" for field in FIELDS[7:]}
        for _ in range(2):
            response = client.post(f"/intake/{sid}/form", json={
                "survey_version": "2026-10", "answers": answers,
            })
            assert response.status_code == 200
        restored = client.get(f"/intake/{sid}/form").json()
        assert restored["submitted_groups"] == ["specific"]
        record = client.post(f"/intake/{sid}/confirm", json={
            "commit": False,
        }).json()["intake_record"]
        assert "geography" in record["unknowns"]
        assert "geography" not in record["unanswered"]
        assert set(record["unanswered"]) == set(FIELDS[8:])


def test_form_exposes_formatted_legacy_cost_without_mutating_typed_answer():
    from fastapi.testclient import TestClient
    import api

    with TestClient(api.app) as client:
        sid = client.post("/intake/start", json={}).json()["session_id"]
        session = intake.get_session(sid)
        session.pop("survey_version")
        previous = slots.make("monthly_cost_estimate", "USD 5000 per month", source="form")
        session["extracted"]["monthly_cost_estimate"] = previous
        session["founder_fields"] = ["monthly_cost_estimate"]
        intake.save_session(session)
        form = client.get(f"/intake/{sid}/form").json()
        cost = next(q for q in form["questions"] if q["field"] == "monthly_cost_estimate")
        assert cost["display_value"] == "USD 5,000 per month"
        assert cost["value"] == previous
        assert cost["confirmed"] is True
        assert intake.get_session(sid)["extracted"]["monthly_cost_estimate"] == previous
