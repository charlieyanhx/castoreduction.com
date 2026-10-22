"""Provider attempts and agent tool dispatches obey the active execution budget.

SDK boundaries are mocked: these tests make no paid calls or network requests.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from packs.market_research_next.models import ExplicitPrepModel
from treadle import llm
from treadle.core import Evidence, Registry
from treadle.execution_limits import (
    BudgetExceeded, activate_budget, charge_model_call,
)
from treadle.harness.agent import run_agent


def scope(*, models=10, tools=10):
    return activate_budget(model_calls=models, tool_calls=tools,
                           deadline=time.monotonic() + 30)


def anthropic_response(text='{"ok":true}'):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )


def gemini_response():
    return SimpleNamespace(text='{"ok":true}', usage_metadata=SimpleNamespace(
        prompt_token_count=1, candidates_token_count=1))


@pytest.fixture(autouse=True)
def clean_llm_state(monkeypatch):
    monkeypatch.setenv("LLM_CACHE_BYPASS", "1")
    monkeypatch.setattr(llm, "_GEMINI_THINKING_OK", {})
    # The suite normally replaces the provider table with an offline stub. These
    # boundary tests restore the adapters while mocking every SDK invocation below.
    for name in ("anthropic", "groq", "gemini"):
        monkeypatch.setitem(llm._BACKENDS, name, getattr(llm, f"_call_{name}"))


def test_anthropic_charges_before_each_provider_attempt():
    client = Mock()
    client.messages.create.return_value = anthropic_response()
    with patch("anthropic.Anthropic", return_value=client) as sdk, scope(models=1):
        llm._call_anthropic("system", "user", 100, "model")
        with pytest.raises(BudgetExceeded):
            llm._call_anthropic("system", "user", 100, "model")
    assert client.messages.create.call_count == 1
    assert sdk.call_args.kwargs["max_retries"] == 0
    assert 0 < client.messages.create.call_args.kwargs["timeout"] <= 30


def test_groq_charges_before_each_provider_attempt():
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))
    with patch("groq.Groq", return_value=client) as sdk, scope(models=1):
        llm._call_groq("system", "user", 100, "model")
        with pytest.raises(BudgetExceeded):
            llm._call_groq("system", "user", 100, "model")
    assert client.chat.completions.create.call_count == 1
    assert sdk.call_args.kwargs["max_retries"] == 0
    assert 0 < client.chat.completions.create.call_args.kwargs["timeout"] <= 30


@pytest.mark.parametrize("backend,constructor,method", [
    (llm._call_anthropic, "anthropic.Anthropic", "messages.create"),
    (llm._call_groq, "groq.Groq", "chat.completions.create"),
])
def test_outside_scope_preserves_sdk_options(backend, constructor, method):
    client = Mock()
    boundary = client
    for attr in method.split("."):
        boundary = getattr(boundary, attr)
    if method.startswith("messages"):
        boundary.return_value = anthropic_response()
    else:
        boundary.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))
    with patch(constructor, return_value=client) as sdk:
        backend("system", "user", 100, "model")
    assert "max_retries" not in sdk.call_args.kwargs
    assert "timeout" not in sdk.call_args.kwargs
    assert "timeout" not in boundary.call_args.kwargs


def test_gemini_thinking_retry_is_a_separate_charged_attempt():
    client = Mock()
    client.models.generate_content.side_effect = [
        RuntimeError("400 INVALID_ARGUMENT"), gemini_response()]
    with patch.object(llm, "_gemini_client", return_value=client), \
         patch.object(llm, "_gemini_rate_gate"), scope(models=2):
        assert llm._call_gemini("system", "user", 100, "gemini-model")[0] == '{"ok":true}'
    calls = client.models.generate_content.call_args_list
    assert len(calls) == 2
    assert "thinking_config" in calls[0].kwargs["config"]
    assert "thinking_config" not in calls[1].kwargs["config"]
    for call in calls:
        options = call.kwargs["config"]["http_options"]
        assert options["retry_options"]["attempts"] == 1
        assert 0 < options["timeout"] <= 30000


@pytest.mark.parametrize("failure", ["400 INVALID_ARGUMENT", "429 RESOURCE_EXHAUSTED"])
def test_gemini_budget_blocks_thinking_retry_and_model_fallback(failure):
    client = Mock()
    client.models.generate_content.side_effect = RuntimeError(failure)
    with patch.object(llm, "_gemini_client", return_value=client), \
         patch.object(llm, "_gemini_rate_gate"), scope(models=1):
        with pytest.raises(BudgetExceeded):
            llm._call_gemini("system", "user", 100, "gemini-model")
    assert client.models.generate_content.call_count == 1


def test_gemini_model_fallback_is_charged():
    client = Mock()
    client.models.generate_content.side_effect = [
        RuntimeError("429 RESOURCE_EXHAUSTED"), gemini_response()]
    with patch.object(llm, "_gemini_client", return_value=client), \
         patch.object(llm, "_gemini_rate_gate"), scope(models=2):
        llm._call_gemini("system", "user", 100, "gemini-model")
    calls = client.models.generate_content.call_args_list
    assert len(calls) == 2
    assert calls[0].kwargs["model"] != calls[1].kwargs["model"]


def test_gemini_client_retry_options_are_scoped():
    with patch("google.genai.Client") as sdk:
        llm._gemini_client()
        assert "http_options" not in sdk.call_args.kwargs
        with scope():
            llm._gemini_client()
    options = sdk.call_args.kwargs["http_options"]
    assert options["retry_options"] == {"attempts": 1}
    assert 0 < options["timeout"] <= 30000


def test_provider_result_arriving_after_deadline_is_rejected():
    now = [100.0]
    client = Mock()

    def late_result(**_kwargs):
        now[0] = 102.0
        return anthropic_response()

    client.messages.create.side_effect = late_result
    with patch("treadle.execution_limits.time.monotonic", side_effect=lambda: now[0]), \
         patch("anthropic.Anthropic", return_value=client), \
         activate_budget(model_calls=1, tool_calls=0, deadline=101.0):
        with pytest.raises(BudgetExceeded, match="deadline"):
            llm._call_anthropic("system", "user", 100, "model")
    assert client.messages.create.call_count == 1
    assert client.messages.create.call_args.kwargs["timeout"] == 1.0


def test_retry_backoff_does_not_sleep_past_deadline():
    now = [100.0]

    def advance(seconds):
        now[0] += seconds

    with patch("treadle.execution_limits.time.monotonic", side_effect=lambda: now[0]), \
         patch.object(llm.time, "sleep", side_effect=advance) as sleep, \
         activate_budget(model_calls=1, tool_calls=0, deadline=102.0):
        with pytest.raises(BudgetExceeded, match="deadline"):
            llm._budget_sleep(15.0)
    sleep.assert_called_once_with(2.0)


@pytest.mark.parametrize("allowance,expected_success", [(1, False), (2, True)])
def test_whole_chain_retry_is_charged_and_budget_never_falls_through(
        monkeypatch, allowance, expected_success):
    monkeypatch.setenv("LLM_BACKEND", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = Mock()
    client.messages.create.side_effect = [RuntimeError("429 rate limit"), anthropic_response()]
    with patch("anthropic.Anthropic", return_value=client), \
         patch.object(llm, "fallback_chain", return_value=["anthropic"]), \
         patch.object(llm.time, "sleep"), scope(models=allowance):
        if expected_success:
            assert llm._chain_text("system", "user", 100) == '{"ok":true}'
        else:
            with pytest.raises(BudgetExceeded):
                llm._chain_text("system", "user", 100)
    assert client.messages.create.call_count == allowance


def test_cross_provider_fallback_cannot_reset_budget(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    anthropic_client, groq_client = Mock(), Mock()
    anthropic_client.messages.create.side_effect = RuntimeError("429 rate limit")
    with patch("anthropic.Anthropic", return_value=anthropic_client), \
         patch("groq.Groq", return_value=groq_client), \
         patch.object(llm, "fallback_chain", return_value=["anthropic", "groq"]), \
         scope(models=1):
        with pytest.raises(BudgetExceeded):
            llm._chain_text("system", "user", 100)
    assert anthropic_client.messages.create.call_count == 1
    groq_client.chat.completions.create.assert_not_called()


def test_streaming_long_text_obeys_same_budget(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client, stream = Mock(), Mock()
    manager = Mock()
    manager.__enter__ = Mock(return_value=stream)
    manager.__exit__ = Mock(return_value=False)
    stream.get_final_message.return_value = anthropic_response("Report")
    client.messages.stream.return_value = manager
    with patch("anthropic.Anthropic", return_value=client) as sdk, scope(models=1):
        assert llm.call_long_text("system", "user").text == "Report"
        with pytest.raises(BudgetExceeded):
            llm.call_long_text("system", "user")
    assert client.messages.stream.call_count == 1
    assert sdk.call_args.kwargs["max_retries"] == 0
    assert 0 < client.messages.stream.call_args.kwargs["timeout"] <= 30


@pytest.mark.parametrize("allowance", [0, 1])
def test_explicit_prep_adapter_obeys_budget(allowance):
    client = Mock()
    client.messages.create.return_value = anthropic_response()
    with patch("anthropic.Anthropic", return_value=client) as sdk, \
         patch.object(llm, "backend_configured", return_value=True), \
         patch.object(llm, "paid_backend_allowed", return_value=True), scope(models=allowance):
        if allowance:
            assert ExplicitPrepModel("model")(system="system", user="user") == {"ok": True}
        else:
            with pytest.raises(BudgetExceeded):
                ExplicitPrepModel("model")(system="system", user="user")
    assert client.messages.create.call_count == allowance
    assert sdk.call_args.kwargs["max_retries"] == 0
    assert 0 < sdk.call_args.kwargs["timeout"] <= 30


def test_bounded_schemaless_zero_retries_really_means_no_reask():
    with patch.object(llm, "_chain_text", return_value='{"amount":3') as call, scope():
        result = llm.call_json("system", "user", max_retries=0)
    assert "_parse_error" in result
    assert call.call_count == 1


def test_legacy_schemaless_zero_retries_keeps_its_reask():
    with patch.object(llm, "_chain_text", return_value='{"amount":3') as call:
        result = llm.call_json("system", "user", max_retries=0)
    assert "_parse_error" in result
    assert call.call_count == 2


def registry_with(tool):
    registry = Registry("test-tool")
    registry["fixture"] = SimpleNamespace(name="fixture", category="test", fn=tool,
        signature="()", docstring="Fixture tool", returns="Evidence")
    return registry


def test_custom_registry_dispatch_stops_before_tool_body_exceeds_budget():
    tool = Mock(return_value=Evidence(source="fixture", category="test", count=1,
                                     payload={"result": "value"}))
    with patch("treadle.harness.agent._default_registry", side_effect=AssertionError(
            "must use supplied registry")), \
         patch("treadle.harness.agent.call_next", return_value={"tool": "fixture"}), \
         scope(tools=1):
        with pytest.raises(BudgetExceeded):
            run_agent("test", registry=registry_with(tool), max_steps=4)
    assert tool.call_count == 1


def test_tool_budget_zero_blocks_first_dispatch():
    tool = Mock()
    with patch("treadle.harness.agent.call_next", return_value={"tool": "fixture"}), \
         scope(tools=0):
        with pytest.raises(BudgetExceeded):
            run_agent("test", registry=registry_with(tool))
    tool.assert_not_called()


def test_swallowed_budget_error_cannot_be_accepted_as_tool_evidence():
    def swallowed():
        try:
            charge_model_call()
        except RuntimeError:
            return Evidence(source="fixture", category="test", count=1, payload={"ok": True})
        raise AssertionError("model call should have been rejected")

    with patch("treadle.harness.agent.call_next", return_value={"tool": "fixture"}), \
         scope(models=0):
        with pytest.raises(BudgetExceeded):
            run_agent("test", registry=registry_with(swallowed))


def test_sticky_violation_cannot_be_hidden_by_cached_answer(monkeypatch):
    monkeypatch.setenv("LLM_CACHE_BYPASS", "0")
    with scope(models=0), patch("treadle.cache.get", return_value={"cached": True}) as cache:
        with pytest.raises(BudgetExceeded):
            charge_model_call()
        with pytest.raises(BudgetExceeded):
            llm.call_json("system", "user")
    cache.assert_not_called()


def test_custom_registry_works_without_an_active_scope():
    tool = Mock(return_value=Evidence(source="fixture", category="test", count=1, payload={}))
    with patch("treadle.harness.agent._default_registry", side_effect=AssertionError(
            "must use supplied registry")), \
         patch("treadle.harness.agent.call_next", side_effect=[
             {"tool": "fixture"}, {"done": True, "answer": "done"}]):
        result = run_agent("test", registry=registry_with(tool))
    assert result.completed
    tool.assert_called_once()
