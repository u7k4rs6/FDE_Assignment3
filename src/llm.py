"""Thin Anthropic client wrapper with call/token telemetry.

Model notes (checked against current API docs for claude-sonnet-5-5):
- forced ``tool_choice`` ({"type": "tool"} / "any") returns 400 on this model,
  so the final ``submit_*`` call is enforced by prompt + a strict schema + a
  code check (one nudge, one validation-repair retry);
- non-default ``temperature`` returns 400, so determinism comes from
  ``thinking: between_tools`` (no extended thinking) and a fixed effort level
  instead of temperature=0;
- server-side refusal fallback is enabled when the API accepts it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from src.config import llm_effort, llm_timeout_seconds, model_name


class LLMFailure(RuntimeError):
    pass


@dataclass
class LLMStats:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    errors: list[str] = field(default_factory=list)


class LLM:
    _fallback_supported: bool | None = None  # learned once per process

    def __init__(self) -> None:
        if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
            raise LLMFailure("No ANTHROPIC_API_KEY configured")
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(timeout=llm_timeout_seconds(), max_retries=2)
        self.model = model_name()
        self.stats = LLMStats()

    def create(self, system: str, messages: list, tools: list[dict], max_tokens: int = 8000):
        kwargs = dict(model=self.model, max_tokens=max_tokens, system=system, messages=messages, tools=tools,
                      tool_choice={"type": "auto"})
        if self.model.startswith("claude-sonnet-5") or self.model.startswith("claude-opus-5"):
            kwargs["output_config"] = {"effort": llm_effort()}
        if self.model.startswith("claude-sonnet-5-5"):
            kwargs["thinking"] = {"type": "between_tools"}
        self.stats.calls += 1
        try:
            if LLM._fallback_supported is not False and self.model.startswith("claude-sonnet-5-5"):
                try:
                    resp = self.client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                                            fallbacks="default", **kwargs)
                    LLM._fallback_supported = True
                except self._anthropic.BadRequestError as exc:
                    if "fallback" not in str(exc).lower():
                        raise
                    LLM._fallback_supported = False
                    resp = self.client.messages.create(**kwargs)
            else:
                resp = self.client.messages.create(**kwargs)
        except self._anthropic.APIError as exc:
            self.stats.errors.append(f"{type(exc).__name__}: {exc}")
            raise LLMFailure(f"LLM API error: {type(exc).__name__}: {exc}") from exc
        usage = getattr(resp, "usage", None)
        if usage is not None:
            self.stats.input_tokens += getattr(usage, "input_tokens", 0) or 0
            self.stats.output_tokens += getattr(usage, "output_tokens", 0) or 0
        if resp.stop_reason == "refusal":
            raise LLMFailure("Model declined the request (refusal)")
        return resp
