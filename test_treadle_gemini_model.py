"""The Gemini model a run writes with, and the order it falls back through.

`gemini-flash-latest` is an alias Google moves. On 2026-11-03 it moved to a model that took
about 60 s for 540 tokens where the pinned models took 2 to 10 s, median time per call went
from 20 s to 80 s, and all five scorecard briefs ran out of time. These tests hold the two
decisions that came out of that: a run names a fixed model, and the moving alias is tried
only after every fixed model has refused.
"""
from __future__ import annotations

import pytest

import treadle.llm as llm


class _Refusing:
    """A Gemini client whose every model answers 503, recording the order it was asked."""

    def __init__(self) -> None:
        self.asked: list[str] = []
        self.models = self

    def generate_content(self, *, model: str, contents: str, config: dict) -> None:
        self.asked.append(model)
        raise RuntimeError("503 UNAVAILABLE")


@pytest.fixture
def refusing(monkeypatch: pytest.MonkeyPatch) -> _Refusing:
    client = _Refusing()
    monkeypatch.setattr(llm, "_gemini_client", lambda: client)
    monkeypatch.setattr(llm, "_gemini_rate_gate", lambda model="": None)
    return client


def test_a_run_writes_with_a_fixed_model_not_the_moving_alias() -> None:
    assert llm.BACKEND_DEFAULTS["gemini"]["model"] == llm.GEMINI_CHAIN[0]
    assert "latest" not in llm.GEMINI_CHAIN[0]


def test_the_moving_alias_is_the_last_model_tried(refusing: _Refusing) -> None:
    with pytest.raises(RuntimeError):
        llm._call_gemini("sys", "user", 64, llm.GEMINI_CHAIN[0])
    assert refusing.asked[-1] == "gemini-flash-latest"
    assert refusing.asked.index("gemini-flash-latest") == len(set(refusing.asked)) - 1


def test_every_model_is_asked_once_and_a_named_model_goes_first(refusing: _Refusing) -> None:
    with pytest.raises(RuntimeError):
        llm._call_gemini("sys", "user", 64, "gemini-2.5-flash-lite")
    assert refusing.asked[0] == "gemini-2.5-flash-lite"
    assert sorted(refusing.asked) == sorted(llm.GEMINI_CHAIN)
