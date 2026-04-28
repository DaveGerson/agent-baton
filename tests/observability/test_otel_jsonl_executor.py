"""OTel JSONL span emission from ExecutionEngine (bd-0899 follow-up).

These tests assert that the executor emits OTLP-shaped spans for two key
lifecycle events:

* ``step.dispatch`` — emitted by :meth:`ExecutionEngine.record_step_result`
  for every step that reaches a terminal status (``complete`` or ``failed``).
  Mid-flight ``dispatched`` rows are intentionally skipped: there is no
  end timestamp to record yet.
* ``gate.run`` — emitted by :meth:`ExecutionEngine.record_gate_result`
  for every gate result, regardless of pass/fail.

Span emission is gated on the ``BATON_OTEL_ENABLED`` env var.  When the
exporter is disabled (the default), no spans are written and no file is
created.  The executor's behaviour must be byte-for-byte identical with
and without the exporter — the only difference is the JSONL side-channel.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.models.execution import (
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
)


# ---------------------------------------------------------------------------
# Plan factories — kept local so this file does not depend on test_executor.
# ---------------------------------------------------------------------------


def _step(
    step_id: str = "1.1",
    agent_name: str = "backend-engineer",
    model: str = "sonnet",
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description="Do the thing",
        model=model,
    )


def _phase(
    phase_id: int = 0,
    steps: list[PlanStep] | None = None,
    gate: PlanGate | None = None,
) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name="Implementation",
        steps=steps or [_step()],
        gate=gate,
    )


def _plan(
    task_id: str = "task-otel-001",
    phases: list[PlanPhase] | None = None,
) -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Span emission smoke test",
        risk_level="LOW",
        phases=phases or [_phase()],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _otel_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Enable the exporter and route it at a tmp file."""
    out = tmp_path / "spans.jsonl"
    monkeypatch.setenv("BATON_OTEL_ENABLED", "1")
    monkeypatch.setenv("BATON_OTEL_PATH", str(out))
    return out


@pytest.fixture
def _otel_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Explicitly leave the exporter off — verifies the no-op default."""
    out = tmp_path / "must-not-exist.jsonl"
    monkeypatch.delenv("BATON_OTEL_ENABLED", raising=False)
    monkeypatch.setenv("BATON_OTEL_PATH", str(out))
    return out


def _read_spans(path: Path) -> list[dict]:
    """Return parsed span dicts from a JSONL file (empty list if missing)."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _attrs(span: dict) -> dict:
    """Flatten an OTLP attributes list to ``{key: scalar}`` for assertions."""
    out: dict = {}
    for kv in span.get("attributes", []):
        v = kv["value"]
        if "stringValue" in v:
            out[kv["key"]] = v["stringValue"]
        elif "intValue" in v:
            out[kv["key"]] = int(v["intValue"])
        elif "boolValue" in v:
            out[kv["key"]] = v["boolValue"]
        elif "doubleValue" in v:
            out[kv["key"]] = v["doubleValue"]
    return out


# ---------------------------------------------------------------------------
# step.dispatch spans
# ---------------------------------------------------------------------------


class TestStepDispatchSpan:
    def test_complete_step_emits_step_dispatch_span(
        self, tmp_path: Path, _otel_path: Path
    ) -> None:
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(_plan())
        engine.record_step_result(
            "1.1", "backend-engineer", status="complete", outcome="ok",
        )

        spans = _read_spans(_otel_path)
        dispatch_spans = [s for s in spans if s["name"] == "step.dispatch"]
        assert len(dispatch_spans) == 1, spans

    def test_failed_step_emits_step_dispatch_span(
        self, tmp_path: Path, _otel_path: Path
    ) -> None:
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(_plan())
        engine.record_step_result(
            "1.1", "backend-engineer", status="failed", error="boom",
        )

        spans = _read_spans(_otel_path)
        dispatch_spans = [s for s in spans if s["name"] == "step.dispatch"]
        assert len(dispatch_spans) == 1, spans

    def test_dispatch_span_carries_required_attributes(
        self, tmp_path: Path, _otel_path: Path
    ) -> None:
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(
            _plan(phases=[_phase(steps=[_step(model="opus")])])
        )
        engine.record_step_result(
            "1.1", "backend-engineer", status="complete",
            outcome="done", estimated_tokens=1234,
        )

        spans = _read_spans(_otel_path)
        dispatch = next(s for s in spans if s["name"] == "step.dispatch")
        a = _attrs(dispatch)
        assert a["step_id"] == "1.1"
        assert a["agent_name"] == "backend-engineer"
        assert a["task_id"] == "task-otel-001"
        assert a["model"] == "opus"
        assert a["status"] == "complete"
        assert a["tokens_used"] == 1234

    def test_dispatch_span_status_reflects_failure(
        self, tmp_path: Path, _otel_path: Path
    ) -> None:
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(_plan())
        engine.record_step_result(
            "1.1", "backend-engineer", status="failed", error="boom",
        )
        dispatch = next(
            s for s in _read_spans(_otel_path) if s["name"] == "step.dispatch"
        )
        assert _attrs(dispatch)["status"] == "failed"

    def test_outcome_is_truncated_in_attributes(
        self, tmp_path: Path, _otel_path: Path
    ) -> None:
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(_plan())
        long_outcome = "x" * 2000
        engine.record_step_result(
            "1.1", "backend-engineer", status="complete", outcome=long_outcome,
        )

        dispatch = next(
            s for s in _read_spans(_otel_path) if s["name"] == "step.dispatch"
        )
        truncated = _attrs(dispatch)["outcome_truncated"]
        # Cap is generous but bounded — must be < the full input.
        assert len(truncated) < len(long_outcome)
        assert truncated.startswith("xxx")

    def test_dispatched_status_does_not_emit_span(
        self, tmp_path: Path, _otel_path: Path
    ) -> None:
        # Mid-flight "dispatched" rows have no end timestamp; emitting a
        # span for them would create a misleading zero-duration record.
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(_plan())
        engine.mark_dispatched("1.1", "backend-engineer")

        dispatch_spans = [
            s for s in _read_spans(_otel_path) if s["name"] == "step.dispatch"
        ]
        assert dispatch_spans == []


# ---------------------------------------------------------------------------
# gate.run spans
# ---------------------------------------------------------------------------


class TestGateRunSpan:
    def test_passed_gate_emits_gate_run_span(
        self, tmp_path: Path, _otel_path: Path
    ) -> None:
        plan = _plan(
            phases=[_phase(
                steps=[_step()],
                gate=PlanGate(gate_type="test", command="pytest"),
            )]
        )
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="complete")
        engine.record_gate_result(phase_id=0, passed=True, output="ok")

        gate_spans = [
            s for s in _read_spans(_otel_path) if s["name"] == "gate.run"
        ]
        assert len(gate_spans) == 1
        a = _attrs(gate_spans[0])
        assert a["gate_type"] == "test"
        assert a["passed"] is True
        assert a["phase_id"] == 0

    def test_failed_gate_emits_gate_run_span_with_failure_status(
        self, tmp_path: Path, _otel_path: Path
    ) -> None:
        plan = _plan(
            phases=[_phase(
                steps=[_step()],
                gate=PlanGate(gate_type="lint", command="ruff"),
            )]
        )
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="complete")
        engine.record_gate_result(phase_id=0, passed=False, output="bad")

        gate = next(
            s for s in _read_spans(_otel_path) if s["name"] == "gate.run"
        )
        assert _attrs(gate)["passed"] is False


# ---------------------------------------------------------------------------
# Disabled-by-default contract
# ---------------------------------------------------------------------------


class TestExporterDisabledByDefault:
    def test_no_jsonl_file_when_exporter_disabled(
        self, tmp_path: Path, _otel_disabled: Path
    ) -> None:
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer", status="complete")
        engine.record_gate_result(phase_id=0, passed=True)
        assert not _otel_disabled.exists()

    def test_executor_completes_normally_with_exporter_disabled(
        self, tmp_path: Path, _otel_disabled: Path
    ) -> None:
        # Sanity check: the executor's existing behaviour is unaffected
        # when no exporter is available.  The state must still record the
        # step result correctly.
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(_plan())
        engine.record_step_result(
            "1.1", "backend-engineer", status="complete", outcome="ok",
        )
        result = engine._load_state().get_step_result("1.1")
        assert result is not None
        assert result.outcome == "ok"
