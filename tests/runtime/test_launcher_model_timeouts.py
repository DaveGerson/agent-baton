"""Regression tests: per-tier launcher timeouts must be monotonic in tier.

Defect (MAJOR): ``_DEFAULT_MODEL_TIMEOUTS`` had no ``fable`` entry, so
``_resolve_timeout("fable")`` fell through to ``default_timeout_seconds``
(600s) — *less* wall-clock than opus (900s), on exactly the longest stages
(the ``adversarial-tdd`` preset runs its heaviest stages on fable).
"""
from __future__ import annotations

import pytest

from agent_baton.core.runtime.claude_launcher import (
    _DEFAULT_MODEL_TIMEOUTS,
    ClaudeCodeConfig,
    ClaudeCodeLauncher,
)


@pytest.fixture
def launcher(tmp_path) -> ClaudeCodeLauncher:
    return ClaudeCodeLauncher(ClaudeCodeConfig(working_directory=tmp_path))


@pytest.mark.parametrize(
    "model", ["fable", "Fable", "claude-fable-5", "claude-fable-5[1m]"]
)
def test_fable_gets_the_longest_timeout(
    launcher: ClaudeCodeLauncher, model: str
) -> None:
    assert launcher._resolve_timeout(model) == 1200.0


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("opus", 900.0),
        ("claude-opus-4-8", 900.0),
        ("sonnet", 600.0),
        ("haiku", 300.0),
        ("claude-haiku-4-5", 300.0),
    ],
)
def test_other_tiers_unchanged(
    launcher: ClaudeCodeLauncher, model: str, expected: float
) -> None:
    assert launcher._resolve_timeout(model) == expected


def test_timeouts_are_monotonic_in_tier() -> None:
    """No tier may be given less wall-clock than a lower tier."""
    order = ["haiku", "sonnet", "opus", "fable"]
    values = [_DEFAULT_MODEL_TIMEOUTS[tier] for tier in order]
    assert values == sorted(values), f"tier-ordering inversion: {dict(zip(order, values))}"


def test_unknown_model_still_falls_back_to_default(
    launcher: ClaudeCodeLauncher,
) -> None:
    assert launcher._resolve_timeout("some-other-model") == 600.0
