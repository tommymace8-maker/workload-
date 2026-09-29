"""Language-model access with per-call cost metering."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .economy import Usage

# Server-side refusal fallback: routed by refusal category, Claude API only.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}


@dataclass
class LLMResult:
    content: list[Any]  # SDK blocks or dicts; passed back verbatim as the assistant turn
    stop_reason: str
    model: str
    usage: Usage


def block_get(block: Any, key: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


class LLM:
    def complete(self, *, model: str, effort: str | None, system: str,
                 messages: list[dict], tools: list[dict]) -> LLMResult:
        raise NotImplementedError

    def judge(self, *, model: str, system: str, prompt: str) -> tuple[dict, LLMResult]:
        """Return a JSON verdict {"allow": bool, "reason": str}."""
        raise NotImplementedError


class AnthropicLLM(LLM):
    def __init__(self, client=None):
        import anthropic

        self.anthropic = anthropic
        self.client = client or anthropic.Anthropic()

    @staticmethod
    def _usage(u) -> Usage:
        return Usage(
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )

    def complete(self, *, model, effort, system, messages, tools) -> LLMResult:
        kwargs: dict[str, Any] = dict(
            model=model,
            max_tokens=16000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            tools=tools,
        )
        # Thinking is left at each model's default (adaptive on the 5.x models).
        if effort:
            kwargs["output_config"] = {"effort": effort}
        if model in FALLBACK_MODELS:
            resp = self.client.beta.messages.create(
                betas=[FALLBACK_BETA], fallbacks="default", **kwargs
            )
        else:
            resp = self.client.messages.create(**kwargs)
        return LLMResult(list(resp.content), resp.stop_reason, resp.model, self._usage(resp.usage))

    def judge(self, *, model, system, prompt):
        schema = {
            "type": "object",
            "properties": {"allow": {"type": "boolean"}, "reason": {"type": "string"}},
            "required": ["allow", "reason"],
            "additionalProperties": False,
        }
        resp = self.client.messages.create(
            model=model,
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        )
        result = LLMResult(list(resp.content), resp.stop_reason, resp.model, self._usage(resp.usage))
        if resp.stop_reason != "end_turn":
            return {"allow": False, "reason": f"guardian stopped with {resp.stop_reason}"}, result
        text = next((b.text for b in resp.content if b.type == "text"), "{}")
        return json.loads(text), result


@dataclass
class ScriptedLLM(LLM):
    """Deterministic stand-in for tests and dry runs.

    ``script`` is a list of callables; each receives the messages so far and
    returns a list of content-block dicts for one assistant turn.
    """

    script: list[Callable[[list[dict]], list[dict]]]
    verdict: Callable[[str], dict] = lambda prompt: {"allow": True, "reason": "scripted"}
    calls: list[dict] = field(default_factory=list)

    def complete(self, *, model, effort, system, messages, tools) -> LLMResult:
        self.calls.append({"model": model, "system": system, "messages": list(messages)})
        blocks = self.script.pop(0)(messages) if self.script else [
            {"type": "tool_use", "id": "done", "name": "sleep",
             "input": {"seconds": 60, "summary": "script exhausted"}}
        ]
        stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
        return LLMResult(blocks, stop, model, Usage(input_tokens=1000, output_tokens=200))

    def judge(self, *, model, system, prompt):
        return self.verdict(prompt), LLMResult([], "end_turn", model, Usage(100, 20))
