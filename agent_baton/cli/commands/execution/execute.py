"""``baton execute`` -- drive the execution engine through an orchestrated task.

This module implements the core execution loop CLI that an orchestrator
agent (typically Claude Code) uses to advance a plan step-by-step.
It exposes subcommands for starting, advancing, recording, gating,
approving, amending, and completing orchestrated executions.

The module also contains :func:`_print_action`, which formats engine
actions into the text protocol that Claude Code parses to drive
orchestration.  This function is treated as a **public API** -- see
``docs/invariants.md``.

Delegates to:
    :class:`~agent_baton.core.engine.executor.ExecutionEngine`
    :class:`~agent_baton.core.engine.persistence.StatePersistence`
    :class:`~agent_baton.core.orchestration.context.ContextManager`
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

from agent_baton.cli.colors import success, error as color_error, warning, info as color_info
from agent_baton.cli.errors import user_error, validation_error
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

_STEP_ID_RE = re.compile(r'^\d+\.\d+$')


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
    _task_id_parent.add_argument(
        "--output", choices=["text", "json"], default="text",
        help="Output format: text (human-readable, default) or json (machine-readable)",
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
    p_record.add_argument("--outcome", "--summary", default="", dest="outcome", help="Summary of what was done (--summary is deprecated, use --outcome)")
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

    # baton execute gate --phase-id N --result pass|fail [--gate-output TEXT]
    p_gate = sub.add_parser("gate", parents=[_task_id_parent],
                            help="Record a QA gate result")
    p_gate.add_argument("--phase-id", type=int, required=True, help="Phase ID")
    p_gate.add_argument("--result", required=True, choices=["pass", "fail"], help="Gate result")
    p_gate.add_argument("--gate-output", default="", dest="gate_output",
                        help="Gate command output (use --gate-output; --output is reserved for format)")

    # baton execute approve --phase-id N --result approve|reject|approve-with-feedback [--feedback TEXT]
    p_approve = sub.add_parser("approve", parents=[_task_id_parent],
                               help="Record a human approval decision")
    p_approve.add_argument("--phase-id", type=int, required=True, help="Phase ID requiring approval")
    p_approve.add_argument("--result", required=True,
                           choices=["approve", "reject", "approve-with-feedback"],
                           help="Approval decision")
    p_approve.add_argument("--feedback", default="", help="Feedback text (for approve-with-feedback)")

    # baton execute feedback --phase-id N --question-id ID --chosen-index N
    p_feedback = sub.add_parser("feedback", parents=[_task_id_parent],
                                help="Record a feedback question answer (dispatches based on choice)")
    p_feedback.add_argument("--phase-id", type=int, required=True, help="Phase ID with feedback questions")
    p_feedback.add_argument("--question-id", required=True, dest="question_id",
                            help="Feedback question ID to answer")
    p_feedback.add_argument("--chosen-index", type=int, required=True, dest="chosen_index",
                            help="Zero-based index of the chosen option")

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
    p_team.add_argument("--step-id", "--step", required=True, dest="step_id", help="Parent team step ID")
    p_team.add_argument("--member-id", required=True, dest="member_id", help="Team member ID")
    p_team.add_argument("--agent", required=True, help="Agent name")
    p_team.add_argument("--status", default="complete", choices=["complete", "failed"])
    p_team.add_argument("--outcome", default="", help="Summary of work done")
    p_team.add_argument("--files", default="", help="Comma-separated files changed")

    # baton execute run [--plan PATH] [--task-id ID] [--model MODEL] [--max-steps N] [--dry-run]
    p_run = sub.add_parser("run", parents=[_task_id_parent],
                           help="Autonomous execution loop (no Claude Code session needed)")
    p_run.add_argument("--plan", default=".claude/team-context/plan.json",
                       help="Path to plan.json (default: .claude/team-context/plan.json)")
    p_run.add_argument("--model", default="sonnet",
                       help="Default model for dispatched agents (default: sonnet)")
    p_run.add_argument("--max-steps", type=int, default=50, dest="max_steps",
                       help="Safety limit: maximum steps before aborting (default: 50)")
    p_run.add_argument("--dry-run", action="store_true", dest="dry_run",
                       help="Print actions without executing them")

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
    """Print an execution action in the structured text format that Claude Code parses.

    **PUBLIC API** -- This function defines the control protocol between the
    CLI output and the Claude Code orchestrator agent.  The orchestrator reads
    stdout line-by-line and keys on the exact field labels and delimiters
    emitted here to decide what to do next.

    Any change to action type keywords (``DISPATCH``, ``GATE``, ``APPROVAL``,
    ``COMPLETE``, ``FAILED``), field label prefixes (``Agent:``, ``Step:``,
    ``Phase:``, ``Command:``), or section delimiters (``--- Delegation Prompt ---``,
    ``--- End Prompt ---``, ``--- Approval Context ---``, ``--- End Context ---``)
    is a **breaking change** and must be coordinated with updates to the
    orchestrator agent definition and ``docs/invariants.md``.

    Output formats by action type:

    **DISPATCH** -- Instructs the orchestrator to spawn a subagent::

        ACTION: DISPATCH
          Agent: <agent_name>
          Model: <agent_model>
          Step:  <step_id>
          Message: <human-readable description>

        --- Delegation Prompt ---
        <full prompt text for the subagent>
        --- End Prompt ---

    **GATE** -- Instructs the orchestrator to run a QA check::

        ACTION: GATE
          Type:    <gate_type>
          Phase:   <phase_id>
          Command: <shell command to run>
          Message: <description>

    **APPROVAL** -- Requests human approval before proceeding::

        ACTION: APPROVAL
          Phase:   <phase_id>
          Message: <what needs approval>

        --- Approval Context ---
        <context for the approver>
        --- End Context ---

        Options: approve, reject, approve-with-feedback

    **COMPLETE** -- Execution finished successfully::

        ACTION: COMPLETE
          <summary text>

    **FAILED** -- Execution terminated due to failure::

        ACTION: FAILED
          <failure summary>

    Args:
        action: Dictionary from ``ExecutionAction.to_dict()``.  Must contain
            at least an ``action_type`` key whose value is a string matching
            one of the :class:`~agent_baton.models.execution.ActionType` enum
            values.

    Raises:
        ValueError: If ``action_type`` is not a string (indicates a bug
            where the raw enum was passed instead of calling ``.to_dict()``).
    """
    atype = action.get("action_type", "")
    if not isinstance(atype, str):
        raise ValueError(f"Internal error: action_type must be str, got {type(atype).__name__}. Report this issue with the full execution trace.")
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

    elif atype == ActionType.FEEDBACK.value:
        print(f"ACTION: FEEDBACK")
        print(f"  Phase:   {action.get('phase_id', '')}")
        print(f"  Message: {msg}")
        print()
        fb_context = action.get("feedback_context", "")
        if fb_context:
            print("--- Feedback Context ---")
            print(fb_context)
            print("--- End Context ---")
            print()
        questions = action.get("feedback_questions", [])
        for q in questions:
            print(f"--- Question: {q.get('question_id', '')} ---")
            print(f"  {q.get('question', '')}")
            if q.get("context"):
                print(f"  Context: {q['context']}")
            opts = q.get("options", [])
            for idx, opt in enumerate(opts):
                print(f"  [{idx}] {opt}")
            print(f"--- End Question ---")
            print()
        print("Respond with: baton execute feedback --phase-id <N> --question-id <ID> --chosen-index <N>")

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
        validation_error("supply a subcommand: start, next, record, dispatched, gate, approve, feedback, amend, team-record, complete, status, resume, list, switch, run")

    if args.subcommand == "list":
        _handle_list()
        return

    if args.subcommand == "switch":
        _handle_switch(args.switch_task_id)
        return

    if args.subcommand == "run":
        _handle_run(args)
        return

    # Resolve task_id: explicit flag → BATON_TASK_ID env var → SQLite active_task →
    #   file active-task-id.txt → None (legacy flat file)
    task_id = getattr(args, "task_id", None)
    context_root = Path(".claude/team-context").resolve()
    if task_id is None:
        task_id = os.environ.get("BATON_TASK_ID")
    if task_id is None and args.subcommand != "start":
        # SQLite-first: read active task from baton.db before falling back to
        # the file-based active-task-id.txt marker.
        _backend = detect_backend(context_root)
        if _backend == "sqlite":
            try:
                _early_storage = get_project_storage(context_root, backend="sqlite")
                task_id = _early_storage.get_active_task()
            except Exception as _exc:
                _log.debug(
                    "SQLite active-task lookup failed, falling back to file: %s", _exc
                )
        if task_id is None:
            task_id = StatePersistence.get_active_task_id(
                Path(".claude/team-context")
            )

    bus = EventBus()
    storage = get_project_storage(context_root)
    engine = ExecutionEngine(bus=bus, task_id=task_id, storage=storage)

    if args.subcommand == "start":
        plan_path = Path(args.plan)
        if not plan_path.exists():
            user_error(f"plan file not found: {plan_path}", hint="Run 'baton plan --save \"task description\"' first.")
        try:
            data = json.loads(plan_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            validation_error(f"plan.json is not valid JSON: {exc}", hint="Re-create with: baton plan --save \"task description\"")
        try:
            plan = MachinePlan.from_dict(data)
        except (KeyError, ValueError, TypeError) as exc:
            validation_error(f"plan.json has invalid structure: {exc}", hint="Re-create with: baton plan --save \"task description\"")
        # Use namespaced execution directory for the new plan
        task_id = plan.task_id

        # Build a KnowledgeResolver from the plan's knowledge configuration so
        # runtime KNOWLEDGE_GAP signals can be auto-resolved without human gates.
        knowledge_resolver = _build_knowledge_resolver(plan)

        engine = ExecutionEngine(
            bus=bus,
            task_id=task_id,
            storage=storage,
            knowledge_resolver=knowledge_resolver,
        )
        ContextManager(task_id=task_id).init_mission_log(plan.task_summary, risk_level=plan.risk_level)
        try:
            action = engine.start(plan)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        # Mark this as the active execution
        try:
            storage.set_active_task(task_id)
        except Exception:
            # Fallback to legacy persistence marker when storage is unavailable.
            if engine._persistence is not None:
                engine._persistence.set_active()
        if getattr(args, "output", "text") == "json":
            result = {"task_id": task_id, "action": action.to_dict()}
            print(json.dumps(result, indent=2))
        else:
            print(f"Session binding: export BATON_TASK_ID={task_id}\n")
            _print_action(action.to_dict())

    elif args.subcommand == "next":
        try:
            if getattr(args, "output", "text") == "json":
                if args.all_actions:
                    actions = engine.next_actions()
                    if actions:
                        result = [a.to_dict() for a in actions]
                    else:
                        action = engine.next_action()
                        result = [action.to_dict()]
                else:
                    action = engine.next_action()
                    result = [action.to_dict()]
                print(json.dumps(result, indent=2))
            elif args.all_actions:
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
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            print("Recovery options:", file=sys.stderr)
            print("  baton execute resume    — resume from saved state", file=sys.stderr)
            print("  baton execute status    — check current state", file=sys.stderr)
            print("  baton execute list      — see all executions", file=sys.stderr)
            sys.exit(1)

    elif args.subcommand == "dispatched":
        if not _STEP_ID_RE.match(args.step_id):
            validation_error(f"invalid step ID '{args.step_id}' (expected format: N.N, e.g. '1.1')")
        engine.mark_dispatched(step_id=args.step_id, agent_name=args.agent)
        print(json.dumps({"status": "dispatched", "step_id": args.step_id}))

    elif args.subcommand == "record":
        # Deprecation warning for --summary
        if "--summary" in sys.argv:
            print("warning: --summary is deprecated, use --outcome instead", file=sys.stderr)
        if not _STEP_ID_RE.match(args.step_id):
            validation_error(f"invalid step ID '{args.step_id}' (expected format: N.N, e.g. '1.1')")
        files = [f.strip() for f in args.files.split(",") if f.strip()] if args.files else []
        try:
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
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
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
        if getattr(args, "output", "text") == "json":
            print(json.dumps({"status": "recorded", "step_id": args.step_id, "agent": args.agent, "result": args.status}))
        else:
            print(f"Recorded: step {args.step_id} ({args.agent}) — {args.status}")

    elif args.subcommand == "gate":
        passed = args.result == "pass"
        engine.record_gate_result(
            phase_id=args.phase_id,
            passed=passed,
            output=args.gate_output,
        )
        status = "PASS" if passed else "FAIL"
        if getattr(args, "output", "text") == "json":
            print(json.dumps({"status": "recorded", "phase_id": args.phase_id, "result": args.result}))
        else:
            print(f"Gate recorded: phase {args.phase_id} — {status}")

    elif args.subcommand == "approve":
        engine.record_approval_result(
            phase_id=args.phase_id,
            result=args.result,
            feedback=args.feedback,
        )
        if getattr(args, "output", "text") == "json":
            print(json.dumps({"status": "recorded", "phase_id": args.phase_id, "result": args.result}))
        else:
            print(f"Approval recorded: phase {args.phase_id} — {args.result}")

    elif args.subcommand == "feedback":
        engine.record_feedback_result(
            phase_id=args.phase_id,
            question_id=args.question_id,
            chosen_index=args.chosen_index,
        )
        if getattr(args, "output", "text") == "json":
            print(json.dumps({
                "status": "recorded",
                "phase_id": args.phase_id,
                "question_id": args.question_id,
                "chosen_index": args.chosen_index,
            }))
        else:
            print(f"Feedback recorded: phase {args.phase_id}, question {args.question_id} — option {args.chosen_index}")

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
        if getattr(args, "output", "text") == "json":
            print(json.dumps({"status": "amended", "amendment_id": amendment.amendment_id, "description": amendment.description}))
        else:
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
        if getattr(args, "output", "text") == "json":
            print(json.dumps({"status": "recorded", "step_id": args.step_id, "member_id": args.member_id, "agent": args.agent, "result": args.status}))
        else:
            print(f"Team member recorded: {args.member_id} ({args.agent}) — {args.status}")

    elif args.subcommand == "complete":
        summary = engine.complete()
        if getattr(args, "output", "text") == "json":
            print(json.dumps({"status": "complete", "summary": summary}))
        else:
            print(summary)

        # Auto-sync to central.db (best-effort, non-blocking)
        try:
            from agent_baton.core.storage.sync import auto_sync_current_project
            sync_result = auto_sync_current_project()
            if sync_result and sync_result.rows_synced > 0 and getattr(args, "output", "text") != "json":
                print(f"Synced {sync_result.rows_synced} rows to central.db")
        except Exception as exc:
            _log.warning("Auto-sync to central.db failed (non-blocking): %s", exc)

    elif args.subcommand == "status":
        st = engine.status()
        if not st or st.get("status") == "no_active_execution":
            if getattr(args, "output", "text") == "json":
                print(json.dumps({"status": "no_active_execution"}))
            else:
                print("No active execution.")
                print()
                print("  List executions:  baton execute list")
                print("  Switch execution: export BATON_TASK_ID=<task-id>")
                print("  Start new:        baton plan --save \"task description\"")
            return
        if getattr(args, "output", "text") == "json":
            print(json.dumps(st, indent=2))
            return
        print(f"Task:    {st.get('task_id', '?')}")
        # Determine binding source
        if getattr(args, "task_id", None):
            bound_via = "--task-id"
        elif os.environ.get("BATON_TASK_ID"):
            bound_via = "BATON_TASK_ID (from env)"
        else:
            bound_via = "active-task-id.txt"
        print(f"Bound:   {bound_via}")
        status_text = st.get('status', '?')
        if status_text == 'running':
            status_text = color_info(status_text)
        elif status_text == 'completed':
            status_text = success(status_text)
        elif status_text == 'failed':
            status_text = color_error(status_text)
        print(f"Status:  {status_text}")
        total_phases = st.get("total_phases", "?")
        print(f"Phase:   {st.get('current_phase', '?')} / {total_phases}")
        print(f"Steps:   {st.get('steps_complete', 0)}/{st.get('steps_total', 0)} complete")
        elapsed = st.get("elapsed_seconds", 0)
        if elapsed:
            print(f"Elapsed: {elapsed:.0f}s")

        # Step detail
        step_results = st.get("step_results", [])
        step_plan = st.get("step_plan", [])
        if step_results or step_plan:
            print()
            print("Steps:")
            shown_ids: set[str] = set()
            for r in step_results:
                sid = r.get("step_id", "?")
                shown_ids.add(sid)
                agent = r.get("agent_name", "?")
                status_val = r.get("status", "?")
                outcome = r.get("outcome", "")
                if status_val == "complete":
                    marker = success("done")
                elif status_val == "dispatched":
                    marker = color_info("  >>")
                elif status_val == "failed":
                    marker = color_error("FAIL")
                else:
                    marker = "  .."
                outcome_short = f" ({outcome[:50]})" if outcome else ""
                print(f"  {marker}  {sid:<5}  {agent:<24} — {status_val}{outcome_short}")
            for sp in step_plan:
                sid = sp.get("step_id", "?")
                if sid not in shown_ids:
                    agent = sp.get("agent_name", "?")
                    print(f"  ...   {sid:<5}  {agent:<24} — pending")

        # Gate detail
        gate_results = st.get("gate_results", [])
        if gate_results:
            print()
            print("Gates:")
            for g in gate_results:
                phase_id = g.get("phase_id", "?")
                passed = g.get("passed", False)
                gate_type = g.get("gate_type", "")
                marker = "pass" if passed else "FAIL"
                type_label = f" ({gate_type})" if gate_type else ""
                print(f"  {marker}  Phase {phase_id}{type_label}")

    elif args.subcommand == "resume":
        action = engine.resume()
        if getattr(args, "output", "text") == "json":
            result = {"action": action.to_dict()}
            print(json.dumps(result, indent=2))
        else:
            _print_action(action.to_dict())


# ---------------------------------------------------------------------------
# Knowledge resolver construction
# ---------------------------------------------------------------------------

def _build_knowledge_resolver(plan: MachinePlan):
    """Construct a KnowledgeResolver from the plan's knowledge configuration.

    Loads the default knowledge registry paths (same paths used at plan time)
    and returns a resolver ready for runtime gap auto-resolution.  Returns
    None (gracefully) if imports fail or the registry cannot be loaded.

    The resolver is stored on the engine so that KNOWLEDGE_GAP signals in
    agent output can be matched against the registry instead of falling
    through to best-effort or queue-for-gate escalation.
    """
    try:
        from agent_baton.core.engine.knowledge_resolver import KnowledgeResolver
        from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry

        registry = KnowledgeRegistry()
        registry.load_default_paths()

        return KnowledgeResolver(registry)
    except Exception as exc:
        _log.debug(
            "KnowledgeResolver construction failed (non-fatal): %s", exc
        )
        return None


# ---------------------------------------------------------------------------
# Autonomous execution loop
# ---------------------------------------------------------------------------

def _handle_run(args: argparse.Namespace) -> None:
    """Drive the full execution loop autonomously using headless Claude.

    This is the standalone CLI execution mode: no active Claude Code session
    needed.  It starts (or resumes) an execution, then loops through
    DISPATCH → agent launch → record until COMPLETE or FAILED.

    Gate checks are run as shell subprocesses.  Approval actions pause
    for user input on stdin.
    """
    import subprocess as _subprocess

    plan_path = Path(args.plan)
    context_root = Path(".claude/team-context").resolve()
    max_steps = args.max_steps
    dry_run = args.dry_run
    model_override = args.model

    # Resolve or create the execution
    bus = EventBus()
    storage = get_project_storage(context_root)
    task_id = getattr(args, "task_id", None)

    if task_id is None:
        task_id = os.environ.get("BATON_TASK_ID")

    # If no active execution, start from plan
    engine: ExecutionEngine | None = None
    if task_id:
        engine = ExecutionEngine(bus=bus, task_id=task_id, storage=storage)
        st = engine.status()
        if st and st.get("status") in ("running", "pending"):
            print(f"Resuming execution: {task_id}", file=sys.stderr)
        else:
            engine = None  # Stale or completed — start fresh

    if engine is None:
        if not plan_path.exists():
            user_error(
                f"plan file not found: {plan_path}",
                hint="Run 'baton plan --save \"task description\"' first.",
            )
        try:
            data = json.loads(plan_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            validation_error(f"plan.json is not valid JSON: {exc}")
        try:
            plan = MachinePlan.from_dict(data)
        except (KeyError, ValueError, TypeError) as exc:
            validation_error(f"plan.json has invalid structure: {exc}")
        task_id = plan.task_id
        knowledge_resolver = _build_knowledge_resolver(plan)
        engine = ExecutionEngine(
            bus=bus, task_id=task_id, storage=storage,
            knowledge_resolver=knowledge_resolver,
        )
        ContextManager(task_id=task_id).init_mission_log(
            plan.task_summary, risk_level=plan.risk_level
        )
        action = engine.start(plan)
        try:
            storage.set_active_task(task_id)
        except Exception:
            if engine._persistence is not None:
                engine._persistence.set_active()
        print(f"Started execution: {task_id}", file=sys.stderr)
    else:
        action = engine.next_action()

    # Import the launcher (deferred so it only fails when actually running)
    from agent_baton.core.runtime.claude_launcher import ClaudeCodeLauncher, ClaudeCodeConfig
    from agent_baton.core.orchestration.registry import AgentRegistry

    launcher: ClaudeCodeLauncher | None = None
    if not dry_run:
        try:
            registry = AgentRegistry()
            registry.load_default_paths()
            launcher = ClaudeCodeLauncher(
                config=ClaudeCodeConfig(
                    working_directory=Path.cwd(),
                ),
                registry=registry,
            )
        except RuntimeError as exc:
            user_error(
                f"Cannot initialize Claude launcher: {exc}",
                hint="Install Claude Code CLI or use --dry-run.",
            )

    steps_executed = 0
    action_dict = action.to_dict()

    while True:
        atype = action_dict.get("action_type", "")

        if atype == ActionType.COMPLETE.value:
            summary = engine.complete()
            print(f"\n{success('COMPLETE')}: {summary}")
            # Auto-sync
            try:
                from agent_baton.core.storage.sync import auto_sync_current_project
                sync_result = auto_sync_current_project()
                if sync_result and sync_result.rows_synced > 0:
                    print(f"Synced {sync_result.rows_synced} rows to central.db")
            except Exception:
                pass
            return

        if atype == ActionType.FAILED.value:
            print(f"\n{color_error('FAILED')}: {action_dict.get('summary', action_dict.get('message', ''))}", file=sys.stderr)
            sys.exit(1)

        if steps_executed >= max_steps:
            print(f"\n{warning('ABORTED')}: reached max-steps limit ({max_steps})", file=sys.stderr)
            sys.exit(1)

        if atype == ActionType.DISPATCH.value:
            agent_name = action_dict.get("agent_name", "")
            step_id = action_dict.get("step_id", "")
            agent_model = action_dict.get("agent_model", model_override)
            prompt = action_dict.get("delegation_prompt", "")
            msg = action_dict.get("message", "")

            print(f"\n  [{step_id}] Dispatching {agent_name} (model={agent_model})...", file=sys.stderr)
            if msg:
                print(f"    {msg[:120]}", file=sys.stderr)

            engine.mark_dispatched(step_id=step_id, agent_name=agent_name)

            if dry_run:
                print(f"  [DRY RUN] Would launch {agent_name} with {len(prompt)} char prompt", file=sys.stderr)
                engine.record_step_result(
                    step_id=step_id, agent_name=agent_name,
                    status="complete", outcome="dry-run skip",
                )
            else:
                import asyncio as _asyncio
                assert launcher is not None  # guarded by user_error above
                result = _asyncio.run(launcher.launch(
                    agent_name=agent_name,
                    model=agent_model,
                    prompt=prompt,
                    step_id=step_id,
                ))
                engine.record_step_result(
                    step_id=step_id,
                    agent_name=agent_name,
                    status=result.status,
                    outcome=result.outcome,
                    files_changed=result.files_changed,
                    commit_hash=result.commit_hash,
                    estimated_tokens=result.estimated_tokens,
                    duration_seconds=result.duration_seconds,
                    error=result.error,
                )
                status_marker = success("done") if result.status == "complete" else color_error("FAIL")
                print(f"  [{step_id}] {status_marker} ({result.duration_seconds:.1f}s)", file=sys.stderr)
                if result.error:
                    print(f"    Error: {result.error[:200]}", file=sys.stderr)

                # Log to mission log
                log_status = "COMPLETE" if result.status == "complete" else "FAILED"
                entry = MissionLogEntry(
                    agent_name=agent_name,
                    status=log_status,
                    assignment=step_id,
                    result=result.outcome[:200],
                    files=result.files_changed,
                    commit_hash=result.commit_hash,
                    issues=[result.error] if result.error else [],
                )
                ContextManager(task_id=task_id).append_to_mission_log(entry)

            steps_executed += 1

        elif atype == ActionType.GATE.value:
            gate_cmd = action_dict.get("gate_command", "")
            phase_id = action_dict.get("phase_id", 0)
            gate_type = action_dict.get("gate_type", "")

            print(f"\n  [GATE] Phase {phase_id} ({gate_type}): {gate_cmd}", file=sys.stderr)

            if dry_run:
                print(f"  [DRY RUN] Would run: {gate_cmd}", file=sys.stderr)
                engine.record_gate_result(phase_id=phase_id, passed=True, output="dry-run skip")
            else:
                try:
                    proc = _subprocess.run(
                        gate_cmd, shell=True, capture_output=True, text=True,
                        timeout=300, cwd=str(Path.cwd()),
                    )
                    passed = proc.returncode == 0
                    output = proc.stdout[-2000:] if proc.stdout else ""
                    if not passed and proc.stderr:
                        output += f"\n--- stderr ---\n{proc.stderr[-1000:]}"
                    engine.record_gate_result(
                        phase_id=phase_id, passed=passed, output=output,
                    )
                    marker = success("PASS") if passed else color_error("FAIL")
                    print(f"  [GATE] {marker}", file=sys.stderr)
                except _subprocess.TimeoutExpired:
                    engine.record_gate_result(
                        phase_id=phase_id, passed=False, output="Gate timed out after 300s",
                    )
                    print(f"  [GATE] {color_error('TIMEOUT')}", file=sys.stderr)

        elif atype == ActionType.APPROVAL.value:
            phase_id = action_dict.get("phase_id", 0)
            msg = action_dict.get("message", "")
            context = action_dict.get("approval_context", "")

            print(f"\n  [APPROVAL REQUIRED] Phase {phase_id}", file=sys.stderr)
            print(f"    {msg}", file=sys.stderr)
            if context:
                print(f"    Context: {context[:300]}", file=sys.stderr)

            if dry_run:
                print("  [DRY RUN] Auto-approving", file=sys.stderr)
                engine.record_approval_result(
                    phase_id=phase_id, result="approve",
                )
            else:
                # Interactive approval prompt
                print("  Options: approve, reject, approve-with-feedback", file=sys.stderr)
                try:
                    choice = input("  Decision> ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    choice = "reject"
                feedback = ""
                if choice == "approve-with-feedback":
                    try:
                        feedback = input("  Feedback> ").strip()
                    except (EOFError, KeyboardInterrupt):
                        feedback = ""
                if choice not in ("approve", "reject", "approve-with-feedback"):
                    choice = "approve"
                engine.record_approval_result(
                    phase_id=phase_id, result=choice, feedback=feedback,
                )
                print(f"  [APPROVAL] {choice}", file=sys.stderr)

        # Get next action
        try:
            next_act = engine.next_action()
            action_dict = next_act.to_dict()
        except RuntimeError as exc:
            print(f"\n{color_error('ERROR')}: {exc}", file=sys.stderr)
            sys.exit(1)


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

    # Resolve active task ID: SQLite-first, then file-based fallback.
    active_task_id: str | None = None

    # Collect task IDs from SQLite backend (union with file IDs)
    sqlite_task_ids: list[str] = []
    _sqlite_storage = None
    backend = detect_backend(context_root)
    if backend == "sqlite":
        _log.debug("execute list: using SQLite backend at %s", context_root / "baton.db")
        try:
            _sqlite_storage = get_project_storage(context_root, backend="sqlite")
            sqlite_task_ids = _sqlite_storage.list_executions()
            # SQLite-first: prefer the active task recorded in baton.db.
            active_task_id = _sqlite_storage.get_active_task()
        except Exception as exc:
            _log.info("execute list: SQLite backend unavailable, using file backend: %s", exc)
    else:
        _log.debug("execute list: using file backend at %s", context_root)

    # Fall back to file-based active marker when SQLite didn't yield a result.
    if active_task_id is None:
        active_task_id = StatePersistence.get_active_task_id(context_root)

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
        # SQLite-first: when the SQLite backend is available, read from it
        # directly instead of going through the file-based StatePersistence
        # loader.  Fall back to file only if SQLite has no record for this
        # task (handles executions that pre-date the SQLite migration).
        state = None
        if _sqlite_storage is not None:
            try:
                state = _sqlite_storage.load_execution(tid)
            except Exception as exc:
                _log.info("execute list: failed to load %s from SQLite: %s", tid, exc)
        if state is None:
            # File fallback: covers pre-migration tasks and SQLite-unavailable envs.
            sp = StatePersistence(context_root, task_id=tid)
            state = sp.load()
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

    from agent_baton.cli.formatting import print_table

    table_rows = []
    for rec in records:
        active_marker = "*" if rec.execution_id == active_task_id else " "
        steps_str = f"{rec.steps_complete}/{rec.steps_total}"
        pid_str = str(rec.worker_pid) if rec.worker_pid else "-"
        table_rows.append({
            "task_id": f"{active_marker} {rec.execution_id}",
            "status": rec.status,
            "steps": steps_str,
            "pid": pid_str,
            "summary": rec.plan_summary[:40],
        })

    print_table(
        table_rows,
        columns=["task_id", "status", "steps", "pid", "summary"],
        headers={"task_id": "TASK ID", "status": "STATUS", "steps": "STEPS", "pid": "PID", "summary": "SUMMARY"},
        alignments={"steps": ">", "pid": ">"},
        prefix="  ",
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
        except Exception as exc:
            _log.info("execute switch: SQLite check failed: %s", exc)

    if not exists_in_files and not exists_in_sqlite:
        user_error(f"no execution found with task ID '{task_id}'", hint="Run 'baton execute list' to see available executions.")

    # Update active task in both backends that are available
    sp.set_active()
    if _sqlite_storage is not None:
        _log.debug("execute switch: setting active task in SQLite to %s", task_id)
        try:
            _sqlite_storage.set_active_task(task_id)
        except Exception as exc:
            _log.info("execute switch: SQLite set_active_task failed: %s", exc)

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
        if not name:
            validation_error(f"--add-phase spec #{i} has empty name: '{spec}'", hint="Expected format: NAME:AGENT (e.g. 'Design phase:architect')")
        agent = parts[1].strip() if len(parts) > 1 else "backend-engineer"
        if len(parts) == 1:
            print(f"warning: --add-phase '{spec}' has no agent specified, defaulting to 'backend-engineer'", file=sys.stderr)
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
            validation_error(f"--add-step spec #{i} is malformed: '{spec}'", hint="Expected format: PHASE_ID:AGENT:DESCRIPTION (e.g. '2:data-engineer:Run migration')")
        try:
            phase_id = int(parts[0].strip())
        except ValueError:
            validation_error(f"--add-step spec #{i} has non-numeric phase ID: '{parts[0]}'")
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
