"""A billing or auth refusal stops the run at once, with a reason a person can act on.

Measured 2026-11-03: with the Anthropic balance at zero, four briefs retried the same
refused call through every correction round and ended as a vague PREP failure.
"""
from __future__ import annotations

import time
from unittest.mock import Mock, patch

import pytest

from packs.market_research_next.models import ExplicitPrepModel
from treadle import llm
from treadle.execution_limits import (BudgetExceeded, ProviderFatal, activate_budget,
                                      check_budget, provider_fatal)


class Refused(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


CREDITS = "Error code: 400 - Your credit balance is too low to access the Anthropic API."


def _scope():
    return activate_budget(model_calls=5, tool_calls=5, deadline=time.monotonic() + 30)


@pytest.mark.parametrize("error, code", [
    (Refused(400, CREDITS), "provider_billing"),
    (Refused(402, "payment required"), "provider_billing"),
    (Exception("You exceeded your current quota, please check your plan and billing details"),
     "provider_billing"),
    (Refused(401, "invalid x-api-key"), "provider_auth"),
    (Refused(403, "forbidden"), "provider_auth"),
    (Exception("400 INVALID_ARGUMENT. API key not valid. Please pass a valid API key."),
     "provider_auth"),
    (Exception("API_KEY_INVALID"), "provider_auth"),
])
def test_billing_and_auth_refusals_are_recognised(error, code):
    assert llm.fatal_provider_code(error) == code


@pytest.mark.parametrize("error", [
    Refused(429, "rate limit"), Refused(500, "server error"), Refused(529, "overloaded"),
    Refused(400, "max_tokens is too large"), TimeoutError("timed out"),
    Exception("429 RESOURCE_EXHAUSTED. retry in 30s"),
])
def test_ordinary_failures_are_not_fatal(error):
    assert llm.fatal_provider_code(error) is None


def test_the_stop_is_sticky_even_when_the_caller_swallows_it():
    with _scope():
        try:
            provider_fatal("provider_billing")
        except Exception:
            pass  # Broad handlers in tool and section code must not lose the stop.
        with pytest.raises(ProviderFatal) as caught:
            check_budget()
    assert caught.value.code == "provider_billing"
    assert isinstance(caught.value, BudgetExceeded)


def _prep_call(error):
    client = Mock()
    client.messages.create.side_effect = error
    with patch("treadle.llm.backend_configured", return_value=True), \
         patch("treadle.llm.paid_backend_allowed", return_value=True), \
         patch("anthropic.Anthropic", return_value=client):
        ExplicitPrepModel("chosen-model")(system="system", user="survey")


def test_prep_stops_on_an_empty_balance_and_keeps_no_provider_text():
    with pytest.raises(ProviderFatal) as caught:
        _prep_call(Refused(400, CREDITS + " req_secret"))
    assert caught.value.code == "provider_billing"
    assert "req_secret" not in str(caught.value)


def test_prep_reraises_an_ordinary_failure_unchanged():
    with pytest.raises(TimeoutError):
        _prep_call(TimeoutError("timed out"))


def _chain(monkeypatch, outcomes):
    """_chain_text over scripted backends; each outcome is an error or a reply."""
    calls = []
    monkeypatch.setattr(llm, "fallback_chain", lambda: list(outcomes))
    monkeypatch.setattr(llm, "_backend_and_model", lambda: (next(iter(outcomes)), "model"))
    monkeypatch.setattr(llm, "backend_configured", lambda backend: True)
    monkeypatch.setattr(llm, "_budget_sleep", lambda seconds: None)

    def fake(backend, system, user, max_tokens, tier=None):
        calls.append(backend)
        outcome = outcomes[backend]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(llm, "_try_one_backend", fake)
    return calls


def test_a_refused_backend_does_not_stop_a_run_another_can_serve(monkeypatch):
    calls = _chain(monkeypatch, {"anthropic": llm._BackendRefused("provider_billing"),
                                 "gemini": ("{}", 1, 1, "gemini-x")})
    with _scope():
        assert llm._chain_text("s", "u", 10) == "{}"
    assert calls == ["anthropic", "gemini"]


def test_a_refused_backend_is_not_asked_again_in_later_attempts(monkeypatch):
    calls = _chain(monkeypatch, {"anthropic": llm._BackendRefused("provider_billing"),
                                 "gemini": None})
    with _scope():
        assert llm._chain_text("s", "u", 10) is None
    assert calls.count("anthropic") == 1 and calls.count("gemini") == 4


def test_every_backend_refused_stops_the_run_without_backing_off(monkeypatch):
    calls = _chain(monkeypatch, {"gemini": llm._BackendRefused("provider_auth")})
    with _scope():
        with pytest.raises(ProviderFatal) as caught:
            llm._chain_text("s", "u", 10)
    assert caught.value.code == "provider_auth" and calls == ["gemini"]


def test_outside_a_run_the_chain_keeps_its_old_contract(monkeypatch):
    # Legacy product callers read a None here; they never see a new exception type.
    calls = _chain(monkeypatch, {"gemini": llm._BackendRefused("provider_auth")})
    assert llm._chain_text("s", "u", 10) is None
    assert calls == ["gemini"]


def test_try_one_backend_reports_a_refusal_before_the_transient_substring_check(monkeypatch):
    # A request id holding "500" or "ssl" must not read as a transient server error.
    def refuse(*args, **kwargs):
        raise Refused(400, CREDITS + " request_id req_500ssl")

    monkeypatch.setitem(llm._BACKENDS, "anthropic", refuse)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with pytest.raises(llm._BackendRefused) as caught:
        llm._try_one_backend("anthropic", "s", "u", 10)
    assert caught.value.code == "provider_billing"


# ---------------------------------------------------------------------- through real workers
def refused(_context):
    provider_fatal("provider_billing")


def swallowed(_context):
    try:
        provider_fatal("provider_auth")
    except Exception:
        pass
    return {"looks": "fine"}


def counted(path):
    def task(_context):
        with open(path, "a", encoding="utf-8") as stream:
            stream.write("x")
        provider_fatal("provider_billing")
    return task


def _limits():
    from treadle.execution_limits import ExecutionLimits

    return ExecutionLimits(run_timeout_s=20, task_timeout_s=5, review_timeout_s=5,
                           max_model_calls=24, max_tool_calls=12, task_model_calls=3,
                           task_tool_calls=2, review_model_calls=1)


def _execute(sections, review=None, rounds=2):
    from treadle.workflow import DeterministicWorkflow

    return DeterministicWorkflow(limits=_limits(), max_workers=2, review=review,
                                 max_correction_rounds=rounds).execute(sections, {})


def test_a_refusal_stops_the_run_with_its_reason_and_skips_correction_rounds(tmp_path):
    from treadle.core.section import FAILED, Section

    path = tmp_path / "attempts"
    outcome = _execute([Section("root", counted(path)),
                        Section("next", lambda context: 1, consumes=("root",))])
    assert outcome.stop_reason == "provider_billing"
    assert outcome.correction_rounds == 0
    assert path.read_text() == "x"  # One attempt; before this, one per correction round.
    assert outcome.verdicts[0].status == FAILED
    assert "out of credits" in outcome.issues[-1].message


def test_a_swallowed_refusal_still_stops_the_run():
    from treadle.core.section import Section

    outcome = _execute([Section("root", swallowed)])
    assert outcome.stop_reason == "provider_auth"
    assert "API key" in outcome.issues[-1].message


def test_a_refusal_during_review_stops_the_run():
    from treadle.core.section import Section

    def review(result, verdicts):
        provider_fatal("provider_billing")

    outcome = _execute([Section("root", lambda context: {"value": 1})], review=review)
    assert outcome.stop_reason == "provider_billing"


def test_the_builder_does_not_ask_a_refusing_provider_again():
    from packs.market_research_next.business_object.builder import build_business_object

    statements = [{"id": "S1", "text": "A cafe."}]
    calls = []

    def model(*, system, user):
        calls.append(system)
        provider_fatal("provider_billing")

    result = build_business_object(statements, model=model)
    assert result["stop_reason"] == "expansion_failed"
    assert len(calls) == 2  # The one expansion and the review; no re-ask of either.
