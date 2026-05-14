"""Tests for the LLMGoalEvaluator network path (mocked SDK).

The real Anthropic SDK call is hermetic-banned per tests/CLAUDE.md.
Instead we monkeypatch ``anthropic.Anthropic`` to return canned
responses and verify the JSON-parsing path, the safety-rail
integration, and graceful degradation when the SDK / response shape
is wrong.
"""
from __future__ import annotations

import json
import sys
import types
from typing import Any

import pytest

from agent_baton.core.engine.goal_evaluator import LLMGoalEvaluator
from agent_baton.models.execution import (
    ExecutionState,
    MachinePlan,
    PlanPhase,
    PlanStep,
)


# ---------------------------------------------------------------------------
# Fake anthropic SDK
# ---------------------------------------------------------------------------

class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, response_text: str | Exception) -> None:
        self._response = response_text
        self.last_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.last_kwargs = kwargs
        if isinstance(self._response, Exception):
            raise self._response
        return _FakeResponse(self._response)


class _FakeAnthropic:
    def __init__(self, *, api_key: str) -> None:
        self.api_key = api_key
        self.messages = _FakeMessages(_FakeAnthropic._response_for_test)

    # Class-level slot so the test can swap the canned response before
    # constructing the evaluator (lazy-import means the SDK is imported
    # inside .evaluate()).
    _response_for_test: Any = ""


def _install_fake_sdk(monkeypatch: pytest.MonkeyPatch, response: Any) -> _FakeAnthropic:
    """Install a fake `anthropic` module into sys.modules so the
    evaluator's lazy ``import anthropic`` picks it up."""
    fake_module = types.ModuleType("anthropic")
    _FakeAnthropic._response_for_test = response
    fake_module.Anthropic = _FakeAnthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    return fake_module  # type: ignore[return-value]


def _plan() -> MachinePlan:
    return MachinePlan(
        task_id="t1",
        task_summary="goal eval",
        completion_condition="all tests pass",
        max_amend_cycles=3,
        phases=[PlanPhase(
            phase_id=1, name="Implement",
            steps=[PlanStep(
                step_id="1.1", agent_name="backend-engineer",
                task_description="x",
            )],
        )],
    )


class TestLLMEvaluatorHappyPath:
    def test_well_formed_met_response(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install_fake_sdk(monkeypatch, json.dumps({
            "met": True, "confidence": 0.92,
            "missing": [],
            "suggested_phases": [],
            "reasoning": "all gates green; condition satisfied",
        }))
        plan = _plan()
        state = ExecutionState(task_id="t1", plan=plan)
        ev = LLMGoalEvaluator()
        chk = ev.evaluate(
            state=state, plan=plan,
            last_gate_passed=True, check_id="g1",
        )
        assert chk.met is True
        assert chk.confidence == pytest.approx(0.92)
        assert chk.evaluator_source == "haiku"
        assert chk.last_gate_passed is True

    def test_safety_rail_fires_on_met_with_failed_gate(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Even when the LLM says met=True, the universal safety rail
        forces met=False when the most recent gate failed."""
        _install_fake_sdk(monkeypatch, json.dumps({
            "met": True, "confidence": 0.95,
            "missing": [],
            "suggested_phases": [],
            "reasoning": "looks done to me",
        }))
        plan = _plan()
        state = ExecutionState(task_id="t1", plan=plan)
        ev = LLMGoalEvaluator()
        chk = ev.evaluate(
            state=state, plan=plan,
            last_gate_passed=False, check_id="g1",
        )
        assert chk.met is False
        assert any("last gate" in m for m in chk.missing)

    def test_not_met_with_suggested_phases(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install_fake_sdk(monkeypatch, json.dumps({
            "met": False, "confidence": 0.45,
            "missing": ["coverage gap on auth module", "no perf test"],
            "suggested_phases": [
                {
                    "phase_id": 99,
                    "name": "Close coverage gap",
                    "steps": [{
                        "step_id": "99.1",
                        "agent_name": "test-engineer",
                        "task_description": "add auth coverage",
                    }],
                }
            ],
            "reasoning": "missing coverage",
        }))
        plan = _plan()
        state = ExecutionState(task_id="t1", plan=plan)
        ev = LLMGoalEvaluator()
        chk = ev.evaluate(
            state=state, plan=plan,
            last_gate_passed=True, check_id="g2",
        )
        assert chk.met is False
        assert "coverage gap on auth module" in chk.missing
        assert len(chk.suggested_phases) == 1
        assert chk.suggested_phases[0]["name"] == "Close coverage gap"


class TestLLMEvaluatorPromptShape:
    def test_prompt_carries_required_fields(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install_fake_sdk(monkeypatch, json.dumps({
            "met": False, "confidence": 0.0,
            "missing": [], "suggested_phases": [], "reasoning": "",
        }))
        plan = _plan()
        state = ExecutionState(task_id="t1", plan=plan)
        ev = LLMGoalEvaluator()
        ev.evaluate(
            state=state, plan=plan,
            last_gate_passed=True, check_id="g1",
        )
        # The fake .messages object captured the kwargs
        import anthropic  # type: ignore[import-not-found]
        client = anthropic.Anthropic(api_key="x")
        # `create` is called on a fresh _FakeMessages instance per
        # evaluate(), but the canned response is class-level. Inspect
        # the prompt indirectly by constructing it manually.
        prompt = ev._render_prompt(state, plan, last_gate_passed=True)
        assert "all tests pass" in prompt  # completion_condition
        assert "phases_planned" in prompt or "Phases planned" in prompt
        assert "Most recent gate passed" in prompt


class TestLLMEvaluatorGracefulDegradation:
    def test_malformed_json_falls_back_to_stub(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install_fake_sdk(monkeypatch, "this is not JSON {")
        plan = _plan()
        state = ExecutionState(task_id="t1", plan=plan)
        ev = LLMGoalEvaluator()
        chk = ev.evaluate(
            state=state, plan=plan,
            last_gate_passed=True, check_id="g1",
        )
        # Falls back to stub → source flips to "stub".
        assert chk.evaluator_source == "stub"

    def test_network_exception_falls_back_to_stub(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install_fake_sdk(monkeypatch, ConnectionError("simulated outage"))
        plan = _plan()
        state = ExecutionState(task_id="t1", plan=plan)
        ev = LLMGoalEvaluator()
        chk = ev.evaluate(
            state=state, plan=plan,
            last_gate_passed=True, check_id="g1",
        )
        assert chk.evaluator_source == "stub"

    def test_missing_api_key_falls_back_to_stub(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # SDK present but no key.
        _install_fake_sdk(monkeypatch, json.dumps({
            "met": True, "confidence": 1.0,
            "missing": [], "suggested_phases": [], "reasoning": "",
        }))
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        plan = _plan()
        state = ExecutionState(task_id="t1", plan=plan)
        ev = LLMGoalEvaluator()
        chk = ev.evaluate(
            state=state, plan=plan,
            last_gate_passed=True, check_id="g1",
        )
        assert chk.evaluator_source == "stub"
