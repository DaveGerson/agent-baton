"""Regression tests: ``baton forecast cost`` must price the fable tier correctly.

Defect (MAJOR): ``_model_key()`` had branches for haiku and opus only, so every
fable model string fell through to ``"sonnet"``.  The ``fable`` rows already
present in ``_DEFAULT_TOKENS`` and ``_PRICE_PER_1M`` were dead code and fable
stages (the top tier used by the ``adversarial-tdd`` workflow) were forecast at
roughly a tenth of their real cost.
"""
from __future__ import annotations

import pytest

from agent_baton.core.observe.cost_forecaster import (
    _DEFAULT_TOKENS,
    _PRICE_PER_1M,
    CostForecaster,
    _model_key,
)
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep


@pytest.mark.parametrize(
    "raw",
    ["fable", "Fable", "claude-fable-5", "claude-fable-5[1m]", "FABLE-5"],
)
def test_model_key_resolves_fable(raw: str) -> None:
    assert _model_key(raw) == "fable"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("haiku", "haiku"),
        ("claude-haiku-4-5", "haiku"),
        ("opus", "opus"),
        ("claude-opus-4-8", "opus"),
        ("sonnet", "sonnet"),
        ("", "sonnet"),
        ("something-unknown", "sonnet"),
    ],
)
def test_model_key_other_tiers_unchanged(raw: str, expected: str) -> None:
    assert _model_key(raw) == expected


def _plan_with(model: str) -> MachinePlan:
    return MachinePlan(
        task_id="task-fable-forecast",
        task_summary="price a fable stage",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Spec",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="requirements-analyst",
                        task_description="write the spec",
                        model=model,
                    )
                ],
            )
        ],
    )


def test_forecast_prices_fable_step_at_fable_rates() -> None:
    forecast = CostForecaster(None).forecast(_plan_with("claude-fable-5"))

    in_tok, out_tok = _DEFAULT_TOKENS["fable"]
    price_in, price_out = _PRICE_PER_1M["fable"]
    expected = (in_tok * price_in + out_tok * price_out) / 1_000_000

    assert forecast.est_input_tokens == in_tok
    assert forecast.est_output_tokens == out_tok
    assert forecast.est_usd_mid == pytest.approx(round(expected, 6))
    assert [row["model"] for row in forecast.breakdown] == ["fable"]


def test_fable_forecast_is_not_sonnet_priced() -> None:
    """The old bug: a fable step was reported (and priced) as sonnet."""
    fable = CostForecaster(None).forecast(_plan_with("claude-fable-5"))
    sonnet = CostForecaster(None).forecast(_plan_with("sonnet"))

    assert fable.breakdown[0]["model"] == "fable"
    # Roughly an order of magnitude apart — assert a wide, stable margin.
    assert fable.est_usd_mid > sonnet.est_usd_mid * 5
