"""Contract tests between config.yml and what the API will actually accept.

Every failure this suite guards against has already happened in production once.
They share a shape: the code was internally consistent and the unit tests passed,
but the request the API received was rejected. These tests assert the *request*
is valid, not just that the code runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import budget as budget_mod  # noqa: E402
import llm  # noqa: E402

CONFIG = yaml.safe_load((Path(__file__).parent.parent / "config.yml").read_text(encoding="utf-8"))


def _configured_models() -> dict[str, str]:
    return dict(CONFIG["models"])


def test_every_configured_model_has_a_published_price():
    """An unpriced model silently disables the spend ceiling."""
    for role, model in _configured_models().items():
        assert model in budget_mod.PRICING, f"{role}={model} has no entry in PRICING"


def test_effort_is_only_configured_for_models_that_accept_it():
    """Haiku 4.5 rejects `output_config.effort` with a 400.

    This exact combination — effort: low on claude-haiku-4-5 — crashed a live
    run at the adversarial-review step after five successful assessment calls.
    """
    for role, effort in CONFIG["effort"].items():
        model = CONFIG["models"].get(role)
        if model is None or not effort:
            continue
        if not llm.supports_effort(model):
            pytest.fail(
                f"config sets effort={effort!r} for role {role!r}, but {model} "
                "rejects the effort parameter. Remove the effort entry or use a "
                "model from llm.EFFORT_CAPABLE_MODELS."
            )


def test_every_effort_role_maps_to_a_real_model_role():
    """A typo in an effort key would otherwise be silently ignored."""
    unknown = set(CONFIG["effort"]) - set(CONFIG["models"])
    assert not unknown, f"effort roles with no matching model: {sorted(unknown)}"


@pytest.mark.parametrize("model", ["claude-haiku-4-5", "claude-sonnet-4-5"])
def test_known_models_without_effort_support_are_excluded(model):
    assert not llm.supports_effort(model)


@pytest.mark.parametrize("model", ["claude-opus-5", "claude-sonnet-5", "claude-opus-4-8"])
def test_known_models_with_effort_support_are_included(model):
    assert llm.supports_effort(model)


def test_unknown_model_omits_effort_rather_than_risking_a_400():
    """Allowlist, not denylist: omitting effort costs pacing, sending it crashes."""
    assert not llm.supports_effort("claude-some-future-model")


def test_caps_and_budgets_are_positive():
    assert CONFIG["caps"]["max_merges_per_repo"] > 0
    assert CONFIG["caps"]["max_merges_total"] >= CONFIG["caps"]["max_merges_per_repo"]
    assert CONFIG["budget"]["max_per_run"] > 0
    assert CONFIG["budget"]["max_per_month"] >= CONFIG["budget"]["max_per_run"]


def test_assessment_output_budget_leaves_room_for_a_full_chunk():
    """Too small and the response truncates; that aborted a live run."""
    assert CONFIG["budget"]["assessment_max_output_tokens"] >= 16000
    assert CONFIG["budget"]["max_prs_per_assessment"] <= 12
