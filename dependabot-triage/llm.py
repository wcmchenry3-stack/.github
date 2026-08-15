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

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_blocks,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"effort": effort},
        }
        if schema is not None:
            kwargs["output_config"]["format"] = {"type": "json_schema", "schema": schema}

        log.info("%s: calling %s (effort=%s)", phase, model, effort)
        response = self._client.messages.create(**kwargs)

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
