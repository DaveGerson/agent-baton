"""baton execute — drive the execution engine through an orchestrated task."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.persistence import StatePersistence
from agent_baton.core.events.bus import EventBus
from agent_baton.core.storage import get_project_storage
from agent_baton.core.orchestration.context import ContextManager
from agent_baton.core.runtime.supervisor import WorkerSupervisor
from agent_baton.core.storage import detect_backend, get_project_storage
from agent_baton.models.execution import MachinePlan, ActionType, PlanPhase, PlanStep
from agent_baton.models.parallel import ExecutionRecord
from agent_baton.models.plan import MissionLogEntry

_log = logging.getLogger(__name__)


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "execute",
        help="Drive an orchestrated task through the execution engine",
    )
    # Shared parent parser so --task-id works before OR after the subcommand
    _task_id_parent = argparse.ArgumentParser(add_help=False)
    _task_id_parent.add_argument(
        "--task-id",
        default=None,
        help="Target a specific execution by task ID (default: active execution)",
    )
    sub = p.add_subparsers(dest="subcommand")

    # baton execute start [--plan PATH] [--task-id ID]
    p_start = sub.add_parser("start", parents=[_task_id_parent],
                             help="Start execution from a saved plan")
    p_start.add_argument(
        "--plan",
        default=".claude/team-context/plan.json",
        help="Path to plan.json (default: .claude/team-context/plan.json)",
    )

    # baton execute next [--all] [--task-id ID]
    next_p = sub.add_parser("next", parents=[_task_id_parent],
                            help="Get the next action to perform")
    next_p.add_argument("--all", action="store_true", dest="all_actions",
                        help="Return all dispatchable actions (for parallel dispatch)")

    # baton execute record --step ID --agent NAME [--status S] [--outcome O] [--tokens N] [--duration N] [--error E]
    p_record = sub.add_parser("record", parents=[_task_id_parent],
                              help="Record a step completion")
    p_record.add_argument("--step", "--step-id", required=True, dest="step_id", help="Step ID (e.g. 1.1)")
    p_record.add_argument("--agent", required=True, help="Agent name")
    p_record.add_argument("--status", default="complete", choices=["complete", "failed"], help="complete or failed")
    p_record.add_argument("--outcome", "--summary", default="", dest="outcome", help="Summary of what was done")
    p_record.add_argument("--tokens", type=int, default=0, help="Estimated tokens used")
    p_record.add_argument("--duration", type=float, default=0.0, help="Duration in seconds")
    p_record.add_argument("--error", default="", help="Error message if failed")
    p_record.add_argument("--files", default="", help="Comma-separated files changed")
    p_record.add_argument("--commit", default="", help="Commit hash")

    # baton execute dispatched --step ID --agent NAME
    dispatched_p = sub.add_parser("dispatched", parents=[_task_id_parent],
                                  help="Mark a step as dispatched (in-flight)")
    dispatched_p.add_argument("--step", "--step-id", required=True, dest="step_id")
    dispatched_p.add_argument("--agent", required=True)

    # baton execute gate --phase-id N --result pass|fail [--output TEXT]
    p_gate = sub.add_parser("gate", parents=[_task_id_parent],
                            help="Record a QA gate result")
    p_gate.add_argument("--phase-id", type=int, required=True, help="Phase ID")
    p_gate.add_argument("--result", required=True, choices=["pass", "fail"], help="Gate result")
    p_gate.add_argument("--output", default="", help="Gate command output")

    # baton execute approve --phase-id N --result approve|reject|approve-with-feedback [--feedback TEXT]
    p_approve = sub.add_parser("approve", parents=[_task_id_parent],
                               help="Record a human approval decision")
    p_approve.add_argument("--phase-id", type=int, required=True, help="Phase ID requiring approval")
    p_approve.add_argument("--result", required=True,
                           choices=["approve", "reject", "approve-with-feedback"],
                           help="Approval decision")
    p_approve.add_argument("--feedback", default="", help="Feedback text (for approve-with-feedback)")

    # baton execute amend --description TEXT [--add-phase NAME:AGENT] [--after-phase N] [--add-step PHASE_ID:AGENT:DESC]
    p_amend = sub.add_parser("amend", parents=[_task_id_parent],
                             help="Amend the running plan")
    p_amend.add_argument("--description", required=True, help="Why this amendment is needed")
    p_amend.add_argument("--add-phase", action="append", default=[],
                         help="Add phase as NAME:AGENT (repeatable)")
    p_amend.add_argument("--after-phase", type=int, default=None,
                         help="Insert new phases after this phase_id")
    p_amend.add_argument("--add-step", action="append", default=[],
                         help="Add step as PHASE_ID:AGENT:DESCRIPTION (repeatable)")

    # baton execute team-record --step-id S --member-id M --agent NAME [--status S] [--outcome O] [--files F]
    p_team = sub.add_parser("team-record", parents=[_task_id_parent],
                            help="Record a team member completion")
    p_team.add_argument("--step-id", required=True, dest="step_id", help="Parent team step ID")
    p_team.add_argument("--member-id", required=True, dest="member_id", help="Team member ID")
    p_team.add_argument("--agent", required=True, help="Agent name")
    p_team.add_argument("--status", default="complete", choices=["complete", "failed"])
    p_team.add_argument("--outcome", default="", help="Summary of work done")
    p_team.add_argument("--files", default="", help="Comma-separated files changed")

    # baton execute complete [--task-id ID]
    sub.add_parser("complete", parents=[_task_id_parent],
                   help="Finalize execution (writes usage, trace, retrospective)")

    # baton execute status [--task-id ID]
    sub.add_parser("status", parents=[_task_id_parent],
                   help="Show current execution state")

    # baton execute resume [--task-id ID]
    sub.add_parser("resume", parents=[_task_id_parent],
                   help="Resume execution after a crash")

    # baton execute list
    sub.add_parser("list", help="List all executions (active and completed)")

    # baton execute switch TASK_ID
    p_switch = sub.add_parser("switch", help="Switch the active execution to a different task ID")
    p_switch.add_argument("switch_task_id", metavar="TASK_ID", help="Task ID to switch to")

    return p


def _print_action(action: dict) -> None:
    """Print an execution action in a human-readable format.

    IMPORTANT: This output format is the control protocol between Claude Code
    (the orchestrator) and the execution engine. Changes to action type strings,
    field labels, or output structure will break the orchestrator's ability to
    parse engine responses. Treat this function as a public API.
    """
    atype = action.get("action_type", "")
    assert isinstance(atype, str), f"action_type must be str from to_dict(), got {type(atype)}"
    msg = action.get("message", "")

    if atype == ActionType.DISPATCH.value:
        print(f"ACTION: DISPATCH")
        print(f"  Agent: {action.get('agent_name', '')}")
        print(f"  Model: {action.get('agent_model', '')}")
        print(f"  Step:  {action.get('step_id', '')}")
        print(f"  Message: {msg}")
        print()
        print("--- Delegation Prompt ---")
        print(action.get("delegation_prompt", ""))
        print("--- End Prompt ---")

    elif atype == ActionType.GATE.value:
        print(f"ACTION: GATE")
        print(f"  Type:    {action.get('gate_type', '')}")
        print(f"  Phase:   {action.get('phase_id', '')}")
        print(f"  Command: {action.get('gate_command', '')}")
        print(f"  Message: {msg}")

    elif atype == ActionType.APPROVAL.value:
        print(f"ACTION: APPROVAL")
        print(f"  Phase:   {action.get('phase_id', '')}")
        print(f"  Message: {msg}")
        print()
        print("--- Approval Context ---")
        print(action.get("approval_context", ""))
        print("--- End Context ---")
        print()
        options = action.get("approval_options", ["approve", "reject", "approve-with-feedback"])
        print(f"Options: {', '.join(options)}")

    elif atype == ActionType.COMPLETE.value:
        print(f"ACTION: COMPLETE")
        print(f"  {action.get('summary', msg)}")

    elif atype == ActionType.FAILED.value:
        print(f"ACTION: FAILED")
        print(f"  {action.get('summary', msg)}")

    else:
        print(f"ACTION: {atype}")
        print(f"  {msg}")


def handler(args: argparse.Namespace) -> None:
    if args.subcommand is None:
        print("error: supply a subcommand: start, next, record, dispatched, gate, complete, status, resume, list, switch")
        sys.exit(1)

    if args.subcommand == "list":
        _handle_list()
        return

    if args.subcommand == "switch":
        _handle_switch(args.switch_task_id)
        return

    # Resolve task_id: explicit flag → active marker → None (legacy flat file)
    task_id = getattr(args, "task_id", None)
    context_root = Path(".claude/team-context").resolve()
    if task_id is None and args.subcommand != "start":
        task_id = StatePersistence.get_active_task_id(
            Path(".claude/team-context")
        )

    bus = EventBus()
    storage = get_project_storage(context_root)
    engine = ExecutionEngine(bus=bus, task_id=task_id, storage=storage)

    if args.subcommand == "start":
        plan_path = Path(args.plan)
        if not plan_path.exists():
            print(f"error: plan file not found: {plan_path}")
            print("Run 'baton plan --save \"task description\"' first.")
            sys.exit(1)
        data = json.loads(plan_path.read_text(encoding="utf-8"))
        plan = MachinePlan.from_dict(data)
        # Use namespaced execution directory for the new plan
        task_id = plan.task_id
        engine = ExecutionEngine(bus=bus, task_id=task_id, storage=storage)
        ContextManager(task_id=task_id).init_mission_log(plan.task_summary, risk_level=plan.risk_level)
        action = engine.start(plan)
        # Mark this as the active execution
        try:
            storage.set_active_task(task_id)
        except Exception:
            # Fallback to legacy persistence marker when storage is unavailable.
            if engine._persistence is not None:
                engine._persistence.set_active()
        _print_action(action.to_dict())

    elif args.subcommand == "next":
        if args.all_actions:
            actions = engine.next_actions()
            if actions:
                result = [a.to_dict() for a in actions]
            else:
                # Fall back to single next_action for terminal states
                action = engine.next_action()
                result = [action.to_dict()]
            print(json.dumps(result, indent=2))
        else:
            action = engine.next_action()
            _print_action(action.to_dict())

    elif args.subcommand == "dispatched":
        engine.mark_dispatched(step_id=args.step_id, agent_name=args.agent)
        print(json.dumps({"status": "dispatched", "step_id": args.step_id}))

    elif args.subcommand == "record":
        files = [f.strip() for f in args.files.split(",") if f.strip()] if args.files else []
        engine.record_step_result(
            step_id=args.step_id,
            agent_name=args.agent,
            status=args.status,
            outcome=args.outcome,
            files_changed=files,
            commit_hash=args.commit,
            estimated_tokens=args.tokens,
            duration_seconds=args.duration,
            error=args.error,
        )
        log_status = "COMPLETE" if args.status == "complete" else "FAILED"
        entry = MissionLogEntry(
            agent_name=args.agent,
            status=log_status,
            assignment=args.step_id,
            result=args.outcome,
            files=files,
            commit_hash=args.commit,
            issues=[args.error] if args.error else [],
        )
        ContextManager(task_id=task_id).append_to_mission_log(entry)
        print(f"Recorded: step {args.step_id} ({args.agent}) — {args.status}")

    elif args.subcommand == "gate":
        passed = args.result == "pass"
        engine.record_gate_result(
            phase_id=args.phase_id,
            passed=passed,
            output=args.output,
        )
        status = "PASS" if passed else "FAIL"
        print(f"Gate recorded: phase {args.phase_id} — {status}")

    elif args.subcommand == "approve":
        engine.record_approval_result(
            phase_id=args.phase_id,
            result=args.result,
            feedback=args.feedback,
        )
        print(f"Approval recorded: phase {args.phase_id} — {args.result}")

    elif args.subcommand == "amend":
        new_phases = _parse_add_phases(args.add_phase) or None
        add_steps_to, new_steps = _parse_add_steps(args.add_step)
        amendment = engine.amend_plan(
            description=args.description,
            new_phases=new_phases,
            insert_after_phase=args.after_phase,
            add_steps_to_phase=add_steps_to,
            new_steps=new_steps or None,
        )
        print(f"Plan amended: {amendment.amendment_id} — {amendment.description}")

    elif args.subcommand == "team-record":
        files = [f.strip() for f in args.files.split(",") if f.strip()] if args.files else []
        engine.record_team_member_result(
            step_id=args.step_id,
            member_id=args.member_id,
            agent_name=args.agent,
            status=args.status,
            outcome=args.outcome,
            files_changed=files,
        )
        print(f"Team member recorded: {args.member_id} ({args.agent}) — {args.status}")

    elif args.subcommand == "complete":
        summary = engine.complete()
        print(summary)

        # Auto-sync to central.db (best-effort, non-blocking)
        try:
            from agent_baton.core.storage.sync import auto_sync_current_project
            sync_result = auto_sync_current_project()
            if sync_result and sync_result.rows_synced > 0:
                print(f"Synced {sync_result.rows_synced} rows to central.db")
        except Exception:
            pass  # sync failure must never block execution completion

    elif args.subcommand == "status":
        st = engine.status()
        if not st:
            print("No active execution.")
            return
        print(f"Task:    {st.get('task_id', '?')}")
        print(f"Status:  {st.get('status', '?')}")
        print(f"Phase:   {st.get('current_phase', '?')}")
        print(f"Steps:   {st.get('steps_complete', 0)}/{st.get('steps_total', 0)}")
        print(f"Gates:   {st.get('gates_passed', 0)} passed, {st.get('gates_failed', 0)} failed")
        elapsed = st.get("elapsed_seconds", 0)
        if elapsed:
            print(f"Elapsed: {elapsed:.0f}s")

    elif args.subcommand == "resume":
        action = engine.resume()
        _print_action(action.to_dict())


# ---------------------------------------------------------------------------
# Helpers for list and switch subcommands
# ---------------------------------------------------------------------------

def _handle_list() -> None:
    """Print a table of all known executions with status and worker info."""
    context_root = Path(".claude/team-context")

    # Collect task IDs from file backend (namespaced dirs)
    file_task_ids = StatePersistence.list_executions(context_root)

    # Also include a legacy flat-file execution if present
    legacy_sp = StatePersistence(context_root)
    legacy_state = legacy_sp.load()

    active_task_id = StatePersistence.get_active_task_id(context_root)

    # Collect task IDs from SQLite backend (union with file IDs)
    sqlite_task_ids: list[str] = []
    _sqlite_storage = None
    backend = detect_backend(context_root)
    if backend == "sqlite":
        _log.debug("execute list: using SQLite backend at %s", context_root / "baton.db")
        try:
            _sqlite_storage = get_project_storage(context_root, backend="sqlite")
            sqlite_task_ids = _sqlite_storage.list_executions()
            # Also check for active task in SQLite if not found on disk
            if active_task_id is None:
                active_task_id = _sqlite_storage.get_active_task()
        except Exception:
            _log.debug("execute list: SQLite load failed, using file backend only", exc_info=True)
    else:
        _log.debug("execute list: using file backend at %s", context_root)

    # Merge: union of file and SQLite task IDs, preserving file order first
    all_task_ids_seen: set[str] = set(file_task_ids)
    merged_task_ids: list[str] = list(file_task_ids)
    for tid in sqlite_task_ids:
        if tid not in all_task_ids_seen:
            merged_task_ids.append(tid)
            all_task_ids_seen.add(tid)

    # Build worker liveness index: task_id -> pid
    workers_by_task: dict[str, int] = {}
    for w in WorkerSupervisor.list_workers(context_root):
        if w["alive"]:
            workers_by_task[w["task_id"]] = w["pid"]

    records: list[ExecutionRecord] = []

    for tid in merged_task_ids:
        # Try file backend first, then SQLite
        state = None
        sp = StatePersistence(context_root, task_id=tid)
        state = sp.load()
        if state is None and _sqlite_storage is not None:
            try:
                state = _sqlite_storage.load_execution(tid)
            except Exception:
                _log.debug("execute list: failed to load %s from SQLite", tid, exc_info=True)
        if state is None:
            continue
        steps_complete = sum(
            1 for r in state.step_results if r.status == "complete"
        )
        records.append(ExecutionRecord(
            execution_id=tid,
            status=state.status,
            plan_summary=state.plan.task_summary[:120],
            worker_pid=workers_by_task.get(tid, 0),
            started_at=state.started_at[:19] if state.started_at else "",
            updated_at=state.completed_at[:19] if state.completed_at else "",
            risk_level=state.plan.risk_level,
            budget_tier=state.plan.budget_tier,
            steps_total=state.plan.total_steps,
            steps_complete=steps_complete,
        ))

    # Add legacy flat-file if not already covered
    if legacy_state is not None and legacy_state.task_id not in {r.execution_id for r in records}:
        steps_complete = sum(
            1 for r in legacy_state.step_results if r.status == "complete"
        )
        records.append(ExecutionRecord(
            execution_id=legacy_state.task_id,
            status=legacy_state.status,
            plan_summary=legacy_state.plan.task_summary[:120],
            worker_pid=workers_by_task.get("(legacy)", 0),
            started_at=legacy_state.started_at[:19] if legacy_state.started_at else "",
            updated_at=legacy_state.completed_at[:19] if legacy_state.completed_at else "",
            risk_level=legacy_state.plan.risk_level,
            budget_tier=legacy_state.plan.budget_tier,
            steps_total=legacy_state.plan.total_steps,
            steps_complete=steps_complete,
        ))

    if not records:
        print("No executions found.")
        return

    # Print header
    print(f"  {'TASK ID':<38}  {'STATUS':<18}  {'STEPS':>7}  {'PID':>7}  SUMMARY")
    print("-" * 90)
    for rec in records:
        active_marker = "*" if rec.execution_id == active_task_id else " "
        steps_str = f"{rec.steps_complete}/{rec.steps_total}"
        pid_str = str(rec.worker_pid) if rec.worker_pid else "-"
        summary = rec.plan_summary[:40]
        print(
            f"{active_marker} {rec.execution_id:<38}  {rec.status:<18}  "
            f"{steps_str:>7}  {pid_str:>7}  {summary}"
        )


def _handle_switch(task_id: str) -> None:
    """Switch the active execution to the given task ID."""
    context_root = Path(".claude/team-context")
    sp = StatePersistence(context_root, task_id=task_id)

    # Check whether the execution exists in either backend
    exists_in_files = sp.exists()
    exists_in_sqlite = False
    _sqlite_storage = None
    backend = detect_backend(context_root)
    if backend == "sqlite":
        _log.debug("execute switch: checking SQLite backend for task %s", task_id)
        try:
            _sqlite_storage = get_project_storage(context_root, backend="sqlite")
            exists_in_sqlite = _sqlite_storage.load_execution(task_id) is not None
        except Exception:
            _log.debug("execute switch: SQLite check failed", exc_info=True)

    if not exists_in_files and not exists_in_sqlite:
        print(f"error: no execution found with task ID '{task_id}'")
        print("Run 'baton execute list' to see available executions.")
        sys.exit(1)

    # Update active task in both backends that are available
    sp.set_active()
    if _sqlite_storage is not None:
        _log.debug("execute switch: setting active task in SQLite to %s", task_id)
        try:
            _sqlite_storage.set_active_task(task_id)
        except Exception:
            _log.debug("execute switch: SQLite set_active_task failed", exc_info=True)

    print(f"Active execution switched to: {task_id}")


# ---------------------------------------------------------------------------
# CLI parsing helpers for amend subcommand
# ---------------------------------------------------------------------------

def _parse_add_phases(specs: list[str]) -> list[PlanPhase]:
    """Parse --add-phase NAME:AGENT specs into PlanPhase objects."""
    phases: list[PlanPhase] = []
    for i, spec in enumerate(specs, start=1):
        parts = spec.split(":", 1)
        name = parts[0].strip()
        agent = parts[1].strip() if len(parts) > 1 else "backend-engineer"
        phases.append(PlanPhase(
            phase_id=0,  # placeholder — renumbered by amend_plan
            name=name,
            steps=[PlanStep(
                step_id=f"0.{i}",
                agent_name=agent,
                task_description=f"{name} phase work",
            )],
        ))
    return phases


def _parse_add_steps(specs: list[str]) -> tuple[int | None, list[PlanStep]]:
    """Parse --add-step PHASE_ID:AGENT:DESCRIPTION specs.

    Returns (target_phase_id, list_of_steps).  All steps target the same phase
    (the phase_id from the first spec).
    """
    if not specs:
        return None, []
    steps: list[PlanStep] = []
    target_phase: int | None = None
    for i, spec in enumerate(specs, start=1):
        parts = spec.split(":", 2)
        if len(parts) < 2:
            continue
        phase_id = int(parts[0].strip())
        agent = parts[1].strip()
        desc = parts[2].strip() if len(parts) > 2 else f"Additional work in phase {phase_id}"
        if target_phase is None:
            target_phase = phase_id
        steps.append(PlanStep(
            step_id=f"{phase_id}.{100 + i}",  # high number to avoid collisions
            agent_name=agent,
            task_description=desc,
        ))
    return target_phase, steps
