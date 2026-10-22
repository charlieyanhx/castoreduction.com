"""Explicit PREP model selection, independent of the ordinary research model chain."""
from __future__ import annotations

import json
import os

from treadle.execution_limits import charge_model_call, check_budget, remaining_seconds


class ExplicitPrepModel:
    """Call the operator-selected high-capability model without a weaker fallback.

    This default adapter uses Anthropic, already supported by the host. A caller can
    supply any callable accepting system= and user= to build_report_type instead.
    No process-wide model environment is changed during a run.
    """

    def __init__(self, model: str | None = None, *, max_tokens: int = 7000) -> None:
        if type(max_tokens) is not int or not 1 <= max_tokens <= 16000:
            raise ValueError("max_tokens must be an integer between 1 and 16000")
        self.model_name = model or os.environ.get("TREADLE_PREP_MODEL", "").strip()
        self.max_tokens = max_tokens

    def __call__(self, *, system: str, user: str) -> dict:
        check_budget()
        if not self.model_name:
            raise ValueError("set TREADLE_PREP_MODEL to the model selected for PREP, "
                             "or inject prep_model when building the report type")
        from treadle.llm import backend_configured, get_usage, paid_backend_allowed

        if not backend_configured("anthropic") or not paid_backend_allowed("anthropic"):
            raise ValueError("PREP requires configured Anthropic access and paid-backend "
                             "opt-in, or an injected prep_model")
        import anthropic

        remaining = remaining_seconds()
        timeout = 120.0 if remaining is None else min(120.0, remaining)
        client = anthropic.Anthropic(timeout=timeout, max_retries=0)
        charge_model_call()
        response = client.messages.create(
            model=self.model_name, max_tokens=self.max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
        )
        check_budget()
        get_usage().add(self.model_name, response.usage.input_tokens,
                        response.usage.output_tokens)
        if response.stop_reason != "end_turn":
            raise ValueError(f"PREP model did not finish: {response.stop_reason}")
        raw = "".join(block.text for block in response.content
                      if getattr(block, "type", "") == "text").strip()
        # Match the successful live trial's representation handling: unwrap one
        # complete JSON fence, without extracting prose or repairing field values.
        if raw.startswith("```json\n") and raw.endswith("\n```"):
            raw = raw[len("```json\n"):-len("\n```")]
        # A malformed or truncated object is a visible PREP failure. No JSON repair or
        # fallback may silently invent a venture field at this boundary.
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("PREP model must return a JSON object")
        check_budget()
        return value
