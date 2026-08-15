"""Spend accounting and hard ceilings for model usage.

Cost is computed from the ``usage`` block each response actually returns, never
estimated from prompt length, so the ledger figure matches the invoice. Two
ceilings apply: one per run and one per calendar month, the latter read back from
the metrics history. Hitting either aborts the run — merges stop, the email goes
out flagged red, and nothing is left half-finished.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# USD per million tokens: (input, output). Cache reads bill at ~0.1x input and
# cache writes at ~1.25x input.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
CACHE_READ_MULTIPLIER = 0.10
CACHE_WRITE_MULTIPLIER = 1.25


class BudgetExceeded(RuntimeError):
    """A spend ceiling was reached. The run stops; nothing further is merged."""


@dataclass
class Spend:
    """Running total for one phase or one whole run."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    usd: float = 0.0
    calls: int = 0

    def __add__(self, other: Spend) -> Spend:
        return Spend(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            usd=self.usd + other.usd,
            calls=self.calls + other.calls,
        )


def price(model: str, usage: dict) -> Spend:
    """Convert one response's ``usage`` block into a costed :class:`Spend`.

    An unknown model is priced at the most expensive published rate rather than
    zero, so a model-id typo shows up as an alarming number instead of silently
    disabling the ceiling.
    """
    if model not in PRICING:
        log.warning("no published price for %r; charging at the highest known rate", model)
    in_rate, out_rate = PRICING.get(model, max(PRICING.values()))

    input_tokens = int(usage.get("input_tokens", 0))
    output_tokens = int(usage.get("output_tokens", 0))
    cache_read = int(usage.get("cache_read_input_tokens", 0))
    cache_write = int(usage.get("cache_creation_input_tokens", 0))

    usd = (
        input_tokens * in_rate
        + cache_read * in_rate * CACHE_READ_MULTIPLIER
        + cache_write * in_rate * CACHE_WRITE_MULTIPLIER
        + output_tokens * out_rate
    ) / 1_000_000

    return Spend(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        usd=usd,
        calls=1,
    )


@dataclass
class Budget:
    """Enforces the per-run and per-month ceilings."""

    max_per_run: float = 1.00
    max_per_month: float = 20.00
    month_to_date: float = 0.0
    total: Spend = field(default_factory=Spend)
    by_phase: dict[str, Spend] = field(default_factory=dict)

    @property
    def run_usd(self) -> float:
        return self.total.usd

    @property
    def month_usd(self) -> float:
        return self.month_to_date + self.total.usd

    def check_before_call(self) -> None:
        """Refuse to start another call when a ceiling is already reached."""
        if self.run_usd >= self.max_per_run:
            raise BudgetExceeded(
                f"per-run ceiling reached: ${self.run_usd:.4f} >= ${self.max_per_run:.2f}"
            )
        if self.month_usd >= self.max_per_month:
            raise BudgetExceeded(
                f"monthly ceiling reached: ${self.month_usd:.2f} >= ${self.max_per_month:.2f}"
            )

    def record(self, phase: str, model: str, usage: dict) -> Spend:
        """Charge one response against the budget and return its cost."""
        spend = price(model, usage)
        self.total = self.total + spend
        self.by_phase[phase] = self.by_phase.get(phase, Spend()) + spend
        log.debug(
            "%s on %s cost $%.5f (run total $%.4f)", phase, model, spend.usd, self.run_usd
        )
        return spend

    def summary(self) -> dict:
        """Serialisable snapshot for the ledger and the email."""
        return {
            "run_usd": round(self.run_usd, 4),
            "month_usd": round(self.month_usd, 4),
            "max_per_run": self.max_per_run,
            "max_per_month": self.max_per_month,
            "calls": self.total.calls,
            "input_tokens": self.total.input_tokens,
            "output_tokens": self.total.output_tokens,
            "cache_read_tokens": self.total.cache_read_tokens,
            "by_phase": {
                phase: {"usd": round(s.usd, 5), "calls": s.calls}
                for phase, s in sorted(self.by_phase.items())
            },
        }


def should_chunk(token_count: int, ceiling: int) -> bool:
    """True when the assessment corpus is too large to send as one request."""
    return token_count > ceiling
