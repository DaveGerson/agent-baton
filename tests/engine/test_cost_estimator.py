"""Tests for ``agent_baton.core.engine.cost_estimator``.

Covers:
- Empty plan -> 0 tokens, $0.
- Single sonnet 5k step -> $0.03 exactly.
- Mixed opus + sonnet plans -> correct totals + per-model breakdown.
- Knowledge ``token_estimate`` ADDS to the role baseline (does not
  replace it).
- Wall-clock and gate-second heuristics.
"""
from __future__ import annotations

import pytest

from agent_baton.core.engine.cost_estimator import (
    MODEL_PRICING,
    CostForecast,
    estimate_gate_seconds,
    estimate_step_tokens,
    estimate_wall_clock_minutes,
    forecast_plan,
    normalise_model,
    role_baseline_tokens,
)
from agent_baton.models.execution import (
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
)
from agent_baton.models.knowledge import KnowledgeAttachment


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _step(
    step_id: str,
    agent: str,
    *,
    model: str = "sonnet",
    knowledge_tokens: list[int] | None = None,
) -> PlanStep:
    knowledge: list[KnowledgeAttachment] = []
    for n, est in enumerate(knowledge_tokens or []):
        knowledge.append(
            KnowledgeAttachment(
                source="explicit",
                pack_name=None,
                document_name=f"doc-{n}.md",
                path=f"/tmp/doc-{n}.md",
                delivery="inline",
                token_estimate=est,
            )
        )
    return PlanStep(
        step_id=step_id,
        agent_name=agent,
        task_description=f"do {step_id}",
        model=model,
        knowledge=knowledge,
    )


def _plan(steps_per_phase: list[list[PlanStep]]) -> MachinePlan:
    phases = [
        PlanPhase(phase_id=i + 1, name=f"Phase {i + 1}", steps=steps)
        for i, steps in enumerate(steps_per_phase)
    ]
    return MachinePlan(
        task_id="2026-04-25-cost-test-deadbeef",
        task_summary="cost estimator test",
        phases=phases,
    )


# ---------------------------------------------------------------------------
# normalise_model
# ---------------------------------------------------------------------------

class TestNormaliseModel:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("opus", "opus"),
            ("sonnet", "sonnet"),
            ("haiku", "haiku"),
            ("claude-opus-4-7", "opus"),
            ("claude-3-5-sonnet", "sonnet"),
            ("claude-haiku-3.5", "haiku"),
            ("", "sonnet"),
            ("unknown-model", "sonnet"),
            ("OPUS", "opus"),
        ],
    )
    def test_maps_to_family(self, raw: str, expected: str) -> None:
        assert normalise_model(raw) == expected


# ---------------------------------------------------------------------------
# role_baseline_tokens
# ---------------------------------------------------------------------------

class TestRoleBaseline:
    @pytest.mark.parametrize(
        "agent, expected",
        [
            ("architect", 8_000),
            ("code-reviewer", 8_000),
            ("auditor", 6_000),
            ("security-reviewer", 6_000),
            ("backend-engineer", 5_000),
            ("backend-engineer--python", 5_000),
            ("frontend-engineer--react", 5_000),
            ("test-engineer", 5_000),
            ("orchestrator", 4_000),
            ("", 4_000),
            ("some-niche-agent", 4_000),
        ],
    )
    def test_baseline_per_role(self, agent: str, expected: int) -> None:
        assert role_baseline_tokens(agent) == expected


# ---------------------------------------------------------------------------
# estimate_step_tokens
# ---------------------------------------------------------------------------

class TestEstimateStepTokens:
    def test_baseline_only(self) -> None:
        s = _step("1.1", "backend-engineer")
        assert estimate_step_tokens(s) == 5_000

    def test_knowledge_adds_to_baseline(self) -> None:
        # baseline 5_000 + 1_500 + 2_500 = 9_000
        s = _step("1.1", "backend-engineer", knowledge_tokens=[1_500, 2_500])
        assert estimate_step_tokens(s) == 9_000

    def test_knowledge_does_not_replace_baseline(self) -> None:
        """A single knowledge attachment must NOT replace the baseline."""
        s = _step("1.1", "architect", knowledge_tokens=[3_000])
        # architect baseline 8_000 + 3_000 = 11_000 (NOT 3_000)
        assert estimate_step_tokens(s) == 11_000

    def test_zero_token_estimate_treated_as_zero(self) -> None:
        s = _step("1.1", "auditor", knowledge_tokens=[0, 0])
        assert estimate_step_tokens(s) == 6_000


# ---------------------------------------------------------------------------
# forecast_plan
# ---------------------------------------------------------------------------

class TestForecastPlan:
    def test_empty_plan_zero_cost(self) -> None:
        plan = _plan([])
        forecast = forecast_plan(plan)
        assert isinstance(forecast, CostForecast)
        assert forecast.total_tokens == 0
        assert forecast.total_cost_usd == 0.0
        assert forecast.per_step_tokens == []
        assert forecast.model_breakdown == {}

    def test_single_sonnet_5k_costs_three_cents(self) -> None:
        # Construct a step that yields exactly 5_000 tokens.
        # backend-engineer baseline = 5_000 with no knowledge attachments.
        s = _step("1.1", "backend-engineer", model="sonnet")
        plan = _plan([[s]])
        forecast = forecast_plan(plan)

        assert forecast.total_tokens == 5_000
        # 5_000 / 1_000_000 * 6.00 = 0.03
        assert forecast.total_cost_usd == pytest.approx(0.03, abs=1e-6)
        assert forecast.per_step_tokens == [("1.1", 5_000)]
        assert forecast.model_breakdown == {"sonnet": 5_000}

    def test_mixed_opus_and_sonnet(self) -> None:
        plan = _plan([
            [
                _step("1.1", "architect", model="opus"),                 # 8_000 opus
                _step("1.2", "backend-engineer", model="sonnet"),        # 5_000 sonnet
            ],
            [
                _step("2.1", "code-reviewer", model="opus"),             # 8_000 opus
                _step("2.2", "test-engineer", model="sonnet"),           # 5_000 sonnet
            ],
        ])
        forecast = forecast_plan(plan)

        # Totals
        assert forecast.total_tokens == 8_000 + 5_000 + 8_000 + 5_000  # 26_000

        # Per-step accumulates back to total
        assert sum(t for _, t in forecast.per_step_tokens) == forecast.total_tokens

        # Per-model breakdown
        assert forecast.model_breakdown == {"opus": 16_000, "sonnet": 10_000}

        # Cost = 16_000 * 30 / 1M + 10_000 * 6 / 1M = 0.48 + 0.06 = 0.54
        expected_cost = (16_000 * MODEL_PRICING["opus"] + 10_000 * MODEL_PRICING["sonnet"]) / 1_000_000.0
        assert forecast.total_cost_usd == pytest.approx(expected_cost, abs=1e-6)
        assert forecast.total_cost_usd == pytest.approx(0.54, abs=1e-6)

    def test_per_step_sum_matches_total_with_knowledge(self) -> None:
        plan = _plan([
            [
                _step("1.1", "architect", model="opus", knowledge_tokens=[2_000]),
                _step("1.2", "backend-engineer--python", model="sonnet", knowledge_tokens=[500, 500]),
                _step("1.3", "auditor", model="haiku"),
            ],
        ])
        forecast = forecast_plan(plan)
        assert sum(t for _, t in forecast.per_step_tokens) == forecast.total_tokens

    def test_haiku_pricing(self) -> None:
        # auditor baseline 6_000, haiku rate 1.25/M = $0.0075
        plan = _plan([[_step("1.1", "auditor", model="haiku")]])
        forecast = forecast_plan(plan)
        assert forecast.total_tokens == 6_000
        assert forecast.total_cost_usd == pytest.approx(0.0075, abs=1e-9)
        assert forecast.model_breakdown == {"haiku": 6_000}


# ---------------------------------------------------------------------------
# Wall-clock + gate seconds
# ---------------------------------------------------------------------------

class TestGateSeconds:
    @pytest.mark.parametrize(
        "cmd, expected",
        [
            ("", 1),
            ("echo 'gate'", 1),
            ("true", 1),
            ("pytest --cov", 2_220),
            ("pytest --cov=agent_baton", 2_220),
            ("pytest tests/govern/", 30),
            ("pytest tests/cli/test_plan_dry_run.py", 30),
            ("pytest", 2_220),
            ("ruff check .", 60),
        ],
    )
    def test_gate_seconds_heuristic(self, cmd: str, expected: int) -> None:
        assert estimate_gate_seconds(cmd) == expected


class TestWallClock:
    def test_sums_agents_and_gates(self) -> None:
        plan = _plan([
            [
                _step("1.1", "architect"),                 # 4 min
                _step("1.2", "backend-engineer"),          # 6 min
            ],
            [
                _step("2.1", "test-engineer"),             # 4 min
            ],
        ])
        plan.phases[0].gate = PlanGate(gate_type="build", command="echo ok")          # 1 s
        plan.phases[1].gate = PlanGate(gate_type="test", command="pytest tests/govern/")  # 30 s

        agent_min, gate_min = estimate_wall_clock_minutes(plan)
        assert agent_min == 14  # 4+6+4
        # gate seconds = 1 + 30 = 31, rounded up to 1 minute
        assert gate_min == 1

    def test_no_gates(self) -> None:
        plan = _plan([[_step("1.1", "code-reviewer")]])
        agent_min, gate_min = estimate_wall_clock_minutes(plan)
        assert agent_min == 3
        assert gate_min == 0


# ---------------------------------------------------------------------------
# bd-a8b2 — composite / router model IDs MUST use prefix, not substring
# ---------------------------------------------------------------------------

class TestNormaliseModelComposite:
    """Regression coverage for bd-a8b2.

    The previous substring implementation could mis-classify composite
    model IDs (``"opus-via-haiku-router"`` would accidentally match
    ``"haiku"`` depending on dict iteration order).  The replacement
    explicit prefix→canonical map must always pick the *leading* family
    for composite IDs.
    """

    @pytest.mark.parametrize(
        "raw, expected",
        [
            # The motivating example from the bead.
            ("opus-via-haiku-router", "opus"),
            ("haiku-via-opus-cache", "haiku"),
            ("sonnet-routed-through-haiku", "sonnet"),
            # Additional vendor-style composites.
            ("claude-opus-4-7-via-router", "opus"),
            ("claude-sonnet-4-6-thinking", "sonnet"),
        ],
    )
    def test_composite_id_uses_leading_family(
        self, raw: str, expected: str
    ) -> None:
        assert normalise_model(raw) == expected

    def test_unknown_model_warns_and_defaults(self, caplog) -> None:
        import logging
        caplog.set_level(logging.WARNING, logger="agent_baton.core.engine.cost_estimator")
        result = normalise_model("gpt-5-turbo")
        assert result == "sonnet"
        # A warning was emitted naming the offending model + the fallback.
        assert any(
            "gpt-5-turbo" in rec.getMessage() for rec in caplog.records
        ), [r.getMessage() for r in caplog.records]


# ---------------------------------------------------------------------------
# bd-1359 — team-augmented step forecast
# ---------------------------------------------------------------------------

class TestForecastPlanTeamAugmented:
    """Regression coverage for bd-1359.

    ``forecast_plan`` must aggregate each team member's cost on top of
    the lead step.  Previously only single-agent steps were exercised,
    leaving the team-walk path uncovered.
    """

    def _team_plan(self) -> MachinePlan:
        from agent_baton.models.execution import TeamMember
        lead = PlanStep(
            step_id="1.1",
            agent_name="architect",                       # baseline 8_000
            task_description="lead",
            model="opus",                                 # 30 / 1M
            team=[
                TeamMember(
                    member_id="1.1.a",
                    agent_name="backend-engineer",        # baseline 5_000
                    role="implementer",
                    model="sonnet",                       # 6 / 1M
                ),
                TeamMember(
                    member_id="1.1.b",
                    agent_name="test-engineer",           # baseline 5_000
                    role="implementer",
                    model="haiku",                        # 1.25 / 1M
                ),
            ],
        )
        return _plan([[lead]])

    def test_team_member_tokens_aggregated(self) -> None:
        plan = self._team_plan()
        forecast = forecast_plan(plan)
        # 8_000 (lead) + 5_000 (member a) + 5_000 (member b) = 18_000
        assert forecast.total_tokens == 18_000

    def test_team_member_cost_aggregated(self) -> None:
        plan = self._team_plan()
        forecast = forecast_plan(plan)
        # opus: 8000 * 30 / 1M = 0.24
        # sonnet: 5000 * 6 / 1M = 0.03
        # haiku: 5000 * 1.25 / 1M = 0.00625
        # total: 0.27625
        expected = (
            8_000 * MODEL_PRICING["opus"]
            + 5_000 * MODEL_PRICING["sonnet"]
            + 5_000 * MODEL_PRICING["haiku"]
        ) / 1_000_000.0
        assert forecast.total_cost_usd == pytest.approx(expected, abs=1e-6)
        assert forecast.total_cost_usd == pytest.approx(0.27625, abs=1e-6)

    def test_team_per_step_records_each_member(self) -> None:
        plan = self._team_plan()
        forecast = forecast_plan(plan)
        # The lead step + each team member appear individually.
        ids = [sid for sid, _ in forecast.per_step_tokens]
        assert ids == ["1.1", "1.1.a", "1.1.b"]
        # And the per-step token sum matches the aggregate total.
        assert sum(t for _, t in forecast.per_step_tokens) == forecast.total_tokens

    def test_team_model_breakdown_separates_families(self) -> None:
        plan = self._team_plan()
        forecast = forecast_plan(plan)
        assert forecast.model_breakdown == {
            "opus": 8_000,
            "sonnet": 5_000,
            "haiku": 5_000,
        }

    def test_team_with_unknown_member_model_falls_back_to_default(self) -> None:
        """A team member with an unrecognised model defaults to sonnet pricing."""
        from agent_baton.models.execution import TeamMember
        lead = PlanStep(
            step_id="2.1",
            agent_name="backend-engineer",                # 5_000 sonnet
            task_description="lead",
            model="sonnet",
            team=[
                TeamMember(
                    member_id="2.1.a",
                    agent_name="frontend-engineer",       # 5_000 baseline
                    role="implementer",
                    model="some-future-model",            # → sonnet fallback
                ),
            ],
        )
        plan = _plan([[lead]])
        forecast = forecast_plan(plan)
        # Both contribute under sonnet pricing in the breakdown.
        assert forecast.model_breakdown == {"sonnet": 10_000}
        assert forecast.total_tokens == 10_000
