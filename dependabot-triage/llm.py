"""The only place the agent talks to Claude.

Two properties make the rest of the system safe to reason about:

* every call is charged against a :class:`~budget.Budget` before and after it runs,
  so a ceiling cannot be bypassed by adding a new call site; and
* every call returns parsed JSON validated against a schema, so a model response
  can never be interpreted as a command. The executor consumes data, not prose.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from budget import Budget

log = logging.getLogger(__name__)

# Retried automatically by the SDK; surfaced here so a sustained outage ends the
# run instead of spinning against the wall clock.
MAX_RETRIES = 3

# `output_config.effort` is not universal: Haiku 4.5 and Sonnet 4.5 reject it
# with a 400. This is an allowlist rather than a denylist so an unrecognised
# model silently omits the parameter instead of failing the run — the cost of
# omitting it is slightly different pacing, the cost of sending it is a crash.
EFFORT_CAPABLE_MODELS = frozenset(
    {
        "claude-opus-5",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-opus-4-5",
        "claude-sonnet-5",
        "claude-sonnet-4-6",
        "claude-fable-5",
        "claude-mythos-5",
    }
)


def supports_effort(model: str) -> bool:
    """Whether ``model`` accepts ``output_config.effort``."""
    return model in EFFORT_CAPABLE_MODELS


class ModelResponseInvalid(RuntimeError):
    """The model returned something the schema does not allow."""


class Client:
    """Budget-aware Anthropic client."""

    def __init__(self, budget: Budget, *, dry_run: bool = False) -> None:
        self._client = anthropic.Anthropic(max_retries=MAX_RETRIES)
        self.budget = budget
        self.dry_run = dry_run

    def count_tokens(self, model: str, system: str, prompt: str) -> int:
        """Pre-flight size check, used to decide whether to chunk the corpus."""
        result = self._client.messages.count_tokens(
            model=model,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return result.input_tokens

    def complete(
        self,
        *,
        phase: str,
        model: str,
        system: str,
        prompt: str,
        effort: str = "medium",
        max_tokens: int = 8000,
        cache_system: bool = False,
        schema: dict[str, Any] | None = None,
    ) -> tuple[Any, dict]:
        """Run one call and return ``(parsed_content, usage)``.

        ``cache_system`` marks the system prompt as a cache breakpoint. The risk
        rubric is stable across nights and comfortably above the 1024-token
        minimum, so it bills at roughly a tenth of list price after the first call.
        """
        self.budget.check_before_call()

        system_blocks: list[dict[str, Any]] = [{"type": "text", "text": system}]
        if cache_system:
            system_blocks[0]["cache_control"] = {"type": "ephemeral"}

        output_config: dict[str, Any] = {}
        if supports_effort(model):
            output_config["effort"] = effort
        elif effort:
            log.debug("%s: %s does not accept effort; omitting it", phase, model)
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_blocks,
            "messages": [{"role": "user", "content": prompt}],
        }
        if output_config:
            kwargs["output_config"] = output_config

        log.info("%s: calling %s (effort=%s)", phase, model, effort)
        # Always stream. The SDK refuses a non-streaming request whose max_tokens
        # implies it could run past ~10 minutes, because idle connections get
        # dropped; the assessment's output budget is well over that line.
        # get_final_message() gives the same object create() would have returned,
        # so nothing downstream changes.
        with self._client.messages.stream(**kwargs) as stream:
            response = stream.get_final_message()

        usage = response.usage.model_dump() if hasattr(response.usage, "model_dump") else {}
        self.budget.record(phase, model, usage)

        if response.stop_reason == "refusal":
            raise ModelResponseInvalid(
                f"{phase}: request was declined by safety classifiers "
                f"({getattr(response.stop_details, 'category', 'unknown')})"
            )

        text = "".join(block.text for block in response.content if block.type == "text")

        if response.stop_reason == "max_tokens":
            raise ModelResponseInvalid(
                f"{phase}: response hit max_tokens ({max_tokens}); output is truncated"
            )

        if schema is None:
            return text.strip(), usage

        try:
            return json.loads(text), usage
        except json.JSONDecodeError as exc:
            raise ModelResponseInvalid(f"{phase}: response was not valid JSON: {exc}") from exc
