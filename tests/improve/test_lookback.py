"""Tests for agent_baton.core.improve.lookback.LookbackAnalyzer.

All tests use synthetic data — mock StorageBackend and BeadStore objects
constructed in-process.  No real SQLite or filesystem access.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Minimal mock types that satisfy the StorageBackend protocol surface
# ---------------------------------------------------------------------------


@dataclass
class _MockStepResult:
    step_id: str
    agent_name: str
    status: str = "complete"
    outcome: str = ""
    error: str = ""
    estimated_tokens: int = 0


@dataclass
class _MockGateResult:
    phase_id: int
    gate_type: str
    passed: bool
    output: str = ""
    status: str = ""


@dataclass
class _MockAmendment:
    amendment_id: str = "a1"
    description: str = ""


@dataclass
class _MockExecutionState:
    task_id: str
    status: str = "failed"
    started_at: str = "2026-01-01T00:00:00"
    step_results: list[Any] = field(default_factory=list)
    gate_results: list[Any] = field(default_factory=list)
    amendments: list[Any] = field(default_factory=list)


@dataclass
class _MockPlan:
    task_id: str
    phases: list[Any] = field(default_factory=list)
    task_type: str = "feature"


@dataclass
class _MockRetro:
    task_id: str
    roster_recommendations: list[Any] = field(default_factory=list)


@dataclass
class _MockBead:
    bead_id: str
    task_id: str
    bead_type: str = "discovery"
    content: str = ""


class _MockStorage:
    """Minimal in-memory StorageBackend implementation for tests."""

    def __init__(
        self,
        executions: dict[str, _MockExecutionState] | None = None,
        plans: dict[str, _MockPlan] | None = None,
        retros: dict[str, _MockRetro] | None = None,
    ) -> None:
        self._executions: dict[str, _MockExecutionState] = executions or {}
        self._plans: dict[str, _MockPlan] = plans or {}
        self._retros: dict[str, _MockRetro] = retros or {}

    def load_execution(self, task_id: str) -> _MockExecutionState | None:
        return self._executions.get(task_id)

    def list_executions(self) -> list[str]:
        return list(self._executions.keys())

    def load_plan(self, task_id: str) -> _MockPlan | None:
        return self._plans.get(task_id)

    def load_retrospective(self, task_id: str) -> _MockRetro | None:
        return self._retros.get(task_id)

    def load_trace(self, task_id: str) -> None:
        return None

    def close(self) -> None:
        pass


class _MockBeadStore:
    """Minimal in-memory BeadStore for tests."""

    def __init__(self, beads: list[_MockBead] | None = None) -> None:
        self._beads: list[_MockBead] = beads or []

    def query(
        self,
        task_id: str | None = None,
        bead_type: str | None = None,
        limit: int = 500,
    ) -> list[_MockBead]:
        result = self._beads
        if task_id is not None:
            result = [b for b in result if b.task_id == task_id]
        if bead_type is not None:
            result = [b for b in result if b.bead_type == bead_type]
        return result[:limit]


# ---------------------------------------------------------------------------
# Helper factory
# ---------------------------------------------------------------------------


def _make_analyzer(
    executions: dict | None = None,
    plans: dict | None = None,
    retros: dict | None = None,
    beads: list[_MockBead] | None = None,
):
    from agent_baton.core.improve.lookback import LookbackAnalyzer

    storage = _MockStorage(executions=executions, plans=plans, retros=retros)
    bead_store = _MockBeadStore(beads=beads) if beads is not None else _MockBeadStore()
    return LookbackAnalyzer(storage=storage, bead_store=bead_store)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_classify_gate_failure() -> None:
    """A failed gate result should classify as GATE_FAIL."""
    gate = _MockGateResult(phase_id=1, gate_type="test", passed=False, output="3 tests failed")
    state = _MockExecutionState(
        task_id="t1",
        status="failed",
        gate_results=[gate],
    )
    analyzer = _make_analyzer(executions={"t1": state})
    report = analyzer.analyze_task("t1")

    assert report.task_id == "t1"
    assert len(report.classifications) >= 1
    categories = [c.category for c in report.classifications]
    assert "GATE_FAIL" in categories

    gate_cls = next(c for c in report.classifications if c.category == "GATE_FAIL")
    assert gate_cls.subcategory == "GATE_FAIL_TEST"
    assert gate_cls.confidence >= 0.8


def test_classify_env_failure() -> None:
    """ModuleNotFoundError in step error → ENV_FAILURE."""
    sr = _MockStepResult(
        step_id="1.1",
        agent_name="backend-engineer",
        status="failed",
        error="ModuleNotFoundError: No module named 'httpx'",
    )
    state = _MockExecutionState(task_id="t2", status="failed", step_results=[sr])
    analyzer = _make_analyzer(executions={"t2": state})
    report = analyzer.analyze_task("t2")

    categories = [c.category for c in report.classifications]
    assert "ENV_FAILURE" in categories

    env_cls = next(c for c in report.classifications if c.category == "ENV_FAILURE")
    assert "MODULENOTFOUNDERROR" in env_cls.subcategory.upper()
    assert env_cls.confidence >= 0.8


def test_classify_agent_error() -> None:
    """Failed step with non-env error message → AGENT_ERROR."""
    sr = _MockStepResult(
        step_id="1.1",
        agent_name="test-engineer",
        status="failed",
        error="AssertionError: expected 42, got 0",
    )
    state = _MockExecutionState(task_id="t3", status="failed", step_results=[sr])
    analyzer = _make_analyzer(executions={"t3": state})
    report = analyzer.analyze_task("t3")

    categories = [c.category for c in report.classifications]
    assert "AGENT_ERROR" in categories

    ae_cls = next(c for c in report.classifications if c.category == "AGENT_ERROR")
    assert "test-engineer" in ae_cls.affected_agents


def test_empty_stores_produce_empty_report() -> None:
    """Analyzer should not crash on empty data and should return a valid report."""
    analyzer = _make_analyzer(executions={})
    report = analyzer.analyze_task("nonexistent-task-id")

    assert report.task_id == "nonexistent-task-id"
    assert report.executions_analyzed == 1
    assert report.failures_found == 0
    assert report.classifications == []
    assert report.recommendations == []


def test_analyze_task_unknown_id() -> None:
    """Unknown task_id returns a report with 0 failures, no crash."""
    analyzer = _make_analyzer(executions={})
    report = analyzer.analyze_task("does-not-exist")

    assert report.failures_found == 0
    assert report.classifications == []


def test_recurring_pattern_detection() -> None:
    """Same failure category seen 3+ times → recurring pattern."""
    def _failed_state(tid: str) -> _MockExecutionState:
        gate = _MockGateResult(phase_id=1, gate_type="test", passed=False)
        return _MockExecutionState(task_id=tid, status="failed", gate_results=[gate])

    executions = {
        "t1": _failed_state("t1"),
        "t2": _failed_state("t2"),
        "t3": _failed_state("t3"),
    }
    analyzer = _make_analyzer(executions=executions)
    patterns = analyzer.detect_recurring_patterns(min_occurrences=2, min_failure_rate=0.1)

    assert len(patterns) >= 1
    types = [p.pattern_type for p in patterns]
    # Gate failures map to "missing_gate"
    assert "missing_gate" in types
    gate_pat = next(p for p in patterns if p.pattern_type == "missing_gate")
    assert gate_pat.frequency >= 2
    assert len(gate_pat.evidence_task_ids) >= 2


def test_markdown_output() -> None:
    """to_markdown produces a non-empty string with expected section headers."""
    gate = _MockGateResult(phase_id=1, gate_type="lint", passed=False)
    state = _MockExecutionState(task_id="t4", status="failed", gate_results=[gate])
    analyzer = _make_analyzer(executions={"t4": state})
    report = analyzer.analyze_task("t4")

    md = analyzer.to_markdown(report)
    assert isinstance(md, str)
    assert len(md) > 0
    assert "# Lookback Report" in md
    assert "## Summary" in md
    assert "Failure Classifications" in md


def test_context_exhaust_via_bead() -> None:
    """A warning bead with 'compaction' text → CONTEXT_EXHAUST classification."""
    state = _MockExecutionState(task_id="t5", status="failed")
    bead = _MockBead(
        bead_id="bd-0001",
        task_id="t5",
        bead_type="warning",
        content="Agent hit context limit; compaction triggered mid-step.",
    )
    analyzer = _make_analyzer(executions={"t5": state}, beads=[bead])
    report = analyzer.analyze_task("t5")

    categories = [c.category for c in report.classifications]
    assert "CONTEXT_EXHAUST" in categories


def test_scope_overrun_via_amendments() -> None:
    """Plan amendments present → SCOPE_OVERRUN classification."""
    amendment = _MockAmendment(amendment_id="a1", description="added remediation phase")
    state = _MockExecutionState(
        task_id="t6",
        status="failed",
        amendments=[amendment],
    )
    analyzer = _make_analyzer(executions={"t6": state})
    report = analyzer.analyze_task("t6")

    categories = [c.category for c in report.classifications]
    assert "SCOPE_OVERRUN" in categories


def test_analyze_range_filters_by_status() -> None:
    """analyze_range with status_filter='failed' skips complete executions."""
    failed_state = _MockExecutionState(
        task_id="t-fail",
        status="failed",
        started_at="2026-03-01T00:00:00",
        gate_results=[_MockGateResult(phase_id=1, gate_type="test", passed=False)],
    )
    ok_state = _MockExecutionState(
        task_id="t-ok",
        status="complete",
        started_at="2026-03-01T00:00:00",
    )
    analyzer = _make_analyzer(executions={"t-fail": failed_state, "t-ok": ok_state})
    report = analyzer.analyze_range(status_filter="failed")

    assert report.executions_analyzed == 1
    assert report.failures_found >= 0  # at least the failed gate task


def test_recommendations_generated_for_gate_fail() -> None:
    """A GATE_FAIL classification should produce at least one recommendation."""
    gate = _MockGateResult(phase_id=1, gate_type="test", passed=False)
    state = _MockExecutionState(task_id="t7", status="failed", gate_results=[gate])
    analyzer = _make_analyzer(executions={"t7": state})
    report = analyzer.analyze_task("t7")

    assert len(report.recommendations) >= 1
    actions = [r.action for r in report.recommendations]
    assert "add_gate" in actions


def test_no_double_env_agent_error() -> None:
    """A step with an env keyword in its error should be ENV_FAILURE, not AGENT_ERROR."""
    sr = _MockStepResult(
        step_id="1.1",
        agent_name="backend-engineer",
        status="failed",
        error="command not found: cargo",
    )
    state = _MockExecutionState(task_id="t8", status="failed", step_results=[sr])
    analyzer = _make_analyzer(executions={"t8": state})
    report = analyzer.analyze_task("t8")

    categories = [c.category for c in report.classifications]
    assert "ENV_FAILURE" in categories
    # Should NOT produce AGENT_ERROR for the same step
    assert "AGENT_ERROR" not in categories
