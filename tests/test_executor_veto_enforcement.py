"""Executor VETO enforcement tests (bd-f606).

Validates that ``ExecutionEngine`` honours ``ComplianceReport.blocks_execution``
when advancing past HIGH/CRITICAL phases:

  * APPROVE / APPROVE_WITH_CONCERNS / REQUEST_CHANGES → advance normally
  * VETO + no --force          → halt with ``ExecutionVetoed``
  * VETO + --force/justification → advance + Override row written to chain
  * LOW/MEDIUM phase + VETO     → advance (VETO only enforced at HIGH/CRITICAL)
  * --force without justification → CLI rejects (argparse + validation_error)

These tests intentionally avoid touching any subprocess / Claude launcher
machinery; they construct an ``ExecutionEngine`` directly with a single-step
plan, manually record a step result whose outcome contains a fenced JSON
verdict block, then drive ``next_action()`` past the phase boundary.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_baton.core.engine.errors import ExecutionVetoed
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.govern.compliance import (
    AuditorVerdict,
    ComplianceChainWriter,
    verify_chain,
)
from agent_baton.models.execution import (
    ActionType,
    MachinePlan,
    PlanPhase,
    PlanStep,
)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _verdict_block(verdict: str, rationale: str = "") -> str:
    """Render the fenced JSON block the auditor agent emits."""
    payload: dict = {"verdict": verdict}
    if rationale:
        payload["rationale"] = rationale
    return f"```json\n{json.dumps(payload)}\n```"


def _two_phase_plan(
    risk_level: str = "HIGH",
    task_id: str = "veto-task",
) -> MachinePlan:
    """Plan with two phases: an auditor phase, then a downstream phase.

    The first phase is the auditor; the second is the work-after-audit phase
    we want to gate.  Both phases have a single step.
    """
    return MachinePlan(
        task_id=task_id,
        task_summary="Regulated change requiring auditor sign-off",
        risk_level=risk_level,
        phases=[
            PlanPhase(
                phase_id=1,
                name="Audit",
                steps=[PlanStep(
                    step_id="1.1",
                    agent_name="auditor",
                    task_description="Audit the prior changes",
                    model="sonnet",
                )],
            ),
            PlanPhase(
                phase_id=2,
                name="Ship",
                steps=[PlanStep(
                    step_id="2.1",
                    agent_name="backend-engineer",
                    task_description="Ship the change",
                    model="sonnet",
                )],
            ),
        ],
    )


def _engine(tmp_path: Path, **kw) -> ExecutionEngine:
    return ExecutionEngine(team_context_root=tmp_path, **kw)


def _start_with_audit(
    tmp_path: Path,
    *,
    risk_level: str,
    verdict: str,
    rationale: str = "",
    force_override: bool = False,
    override_justification: str = "",
) -> ExecutionEngine:
    """Bootstrap an engine, advance past the start gate, record auditor outcome."""
    engine = _engine(
        tmp_path,
        force_override=force_override,
        override_justification=override_justification,
    )
    plan = _two_phase_plan(risk_level=risk_level)
    engine.start(plan)

    # HIGH/CRITICAL plans inject an approval_required gate on the first phase
    # before any agents are dispatched.  Auto-approve so we can record the
    # auditor's outcome.
    action = engine.next_action()
    if action.action_type == ActionType.APPROVAL:
        engine.record_approval_result(
            phase_id=action.phase_id,
            result="approve",
            feedback="proceed for test",
        )
        action = engine.next_action()

    # Now we should be in DISPATCH for step 1.1; record auditor result.
    engine.record_step_result(
        step_id="1.1",
        agent_name="auditor",
        status="complete",
        outcome=_verdict_block(verdict, rationale=rationale),
    )
    return engine


# ===========================================================================
# Tests
# ===========================================================================

class TestVetoEnforcement:
    def test_high_risk_with_approve_advances_normally(self, tmp_path: Path) -> None:
        engine = _start_with_audit(
            tmp_path,
            risk_level="HIGH",
            verdict="APPROVE",
        )
        action = engine.next_action()
        # Should advance past phase 1 and dispatch phase 2 step.
        assert action.action_type in (ActionType.DISPATCH, ActionType.APPROVAL)
        # Check we did move on from phase 1.
        state = engine.status()
        assert state["current_phase"] >= 1, state

    def test_high_risk_with_veto_no_force_halts(self, tmp_path: Path) -> None:
        engine = _start_with_audit(
            tmp_path,
            risk_level="HIGH",
            verdict="VETO",
            rationale="missing audit logs",
        )
        with pytest.raises(ExecutionVetoed) as excinfo:
            engine.next_action()
        assert excinfo.value.verdict == AuditorVerdict.VETO
        assert excinfo.value.phase_id == 1
        assert "missing audit logs" in str(excinfo.value)

    def test_critical_risk_with_veto_no_force_halts(self, tmp_path: Path) -> None:
        engine = _start_with_audit(
            tmp_path,
            risk_level="CRITICAL",
            verdict="VETO",
        )
        with pytest.raises(ExecutionVetoed):
            engine.next_action()

    def test_high_risk_with_veto_and_force_advances_and_writes_override(
        self, tmp_path: Path
    ) -> None:
        engine = _start_with_audit(
            tmp_path,
            risk_level="HIGH",
            verdict="VETO",
            rationale="missing audit logs",
            force_override=True,
            override_justification="Operator confirmed concerns are stale",
        )
        action = engine.next_action()
        # Should NOT raise; should advance and dispatch phase 2.
        assert action.action_type in (
            ActionType.DISPATCH,
            ActionType.APPROVAL,
            ActionType.WAIT,
            ActionType.GATE,
        )

        # Override row must exist in the hash-chained log.
        chain_path = tmp_path / "compliance-audit.jsonl"
        assert chain_path.exists(), "compliance-audit.jsonl was not written"

        lines = [json.loads(l) for l in chain_path.read_text().splitlines() if l.strip()]
        override_rows = [r for r in lines if r.get("entry_type") == "Override"]
        assert override_rows, f"no Override row in chain: {lines}"
        row = override_rows[-1]
        assert row["task_id"] == "veto-task"
        assert row["overridden_verdict"] == "VETO"
        assert "Operator confirmed" in row["justification"]
        # Hash chain integrity preserved.
        ok, msg = verify_chain(chain_path)
        assert ok, msg

    def test_low_risk_with_veto_advances(self, tmp_path: Path) -> None:
        # LOW risk plans never gate on VETO.
        engine = _start_with_audit(
            tmp_path,
            risk_level="LOW",
            verdict="VETO",
        )
        action = engine.next_action()
        assert action.action_type in (
            ActionType.DISPATCH,
            ActionType.APPROVAL,
            ActionType.WAIT,
            ActionType.GATE,
            ActionType.COMPLETE,
        )

    def test_medium_risk_with_veto_advances(self, tmp_path: Path) -> None:
        engine = _start_with_audit(
            tmp_path,
            risk_level="MEDIUM",
            verdict="VETO",
        )
        action = engine.next_action()
        assert action.action_type in (
            ActionType.DISPATCH,
            ActionType.APPROVAL,
            ActionType.WAIT,
            ActionType.GATE,
            ActionType.COMPLETE,
        )

    def test_request_changes_does_not_block(self, tmp_path: Path) -> None:
        # REQUEST_CHANGES is non-blocking at the executor level — only VETO
        # halts.  Operators are expected to amend the plan in response.
        engine = _start_with_audit(
            tmp_path,
            risk_level="HIGH",
            verdict="REQUEST_CHANGES",
        )
        action = engine.next_action()
        assert action.action_type in (
            ActionType.DISPATCH,
            ActionType.APPROVAL,
            ActionType.WAIT,
            ActionType.GATE,
        )

    def test_force_override_state_persists(self, tmp_path: Path) -> None:
        # The force_override flag should round-trip through state save/load.
        engine = _engine(
            tmp_path,
            force_override=True,
            override_justification="round-trip test",
        )
        plan = _two_phase_plan(risk_level="HIGH")
        engine.start(plan)
        st = engine.status()
        assert st["force_override"] is True
        assert "round-trip test" in st["override_justification"]


class TestForceFlagRejectsWithoutJustification:
    """The CLI rejects --force without --justification at handler entry.

    We exercise the real argparse + handler() function path the user sees,
    invoking it in-process rather than through a subprocess so we do not
    depend on the installed ``baton`` script being on PATH.
    """

    def _run_cli(self, *cli_args: str) -> tuple[int, str, str]:
        """Invoke ``agent_baton.cli.commands.execution.execute`` in-process."""
        import argparse as _argparse
        import contextlib
        import io as _io

        from agent_baton.cli.commands.execution import execute as ex_mod

        parser = _argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="root")
        # ex_mod.register adds an "execute" subparser to the supplied
        # subparsers action; the inner subcommands are nested under that.
        ex_mod.register(sub)

        # Inject default --output text so any handler branch that reads it
        # succeeds without needing the global parent-parser flag.
        ns = parser.parse_args(["execute", *cli_args])
        if not hasattr(ns, "output"):
            ns.output = "text"

        stderr_buf = _io.StringIO()
        stdout_buf = _io.StringIO()
        rc = 0
        try:
            with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                ex_mod.handler(ns)
        except SystemExit as exc:
            rc = int(exc.code) if exc.code is not None else 0
        return rc, stdout_buf.getvalue(), stderr_buf.getvalue()

    def test_cli_rejects_force_without_justification_on_run(self) -> None:
        rc, out, err = self._run_cli("run", "--force", "--task-id", "nonexistent")
        assert rc != 0, (rc, out, err)
        combined = (out + err).lower()
        assert "justification" in combined, combined

    def test_cli_rejects_force_without_justification_on_next(self) -> None:
        rc, out, err = self._run_cli("next", "--force", "--task-id", "nonexistent")
        assert rc != 0, (rc, out, err)
        combined = (out + err).lower()
        assert "justification" in combined, combined
