"""baton execute — drive the execution engine through an orchestrated task."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.models.execution import MachinePlan, ActionType


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "execute",
        help="Drive an orchestrated task through the execution engine",
    )
    sub = p.add_subparsers(dest="subcommand")

    # baton execute start [--plan PATH]
    p_start = sub.add_parser("start", help="Start execution from a saved plan")
    p_start.add_argument(
        "--plan",
        default=".claude/team-context/plan.json",
        help="Path to plan.json (default: .claude/team-context/plan.json)",
    )

    # baton execute next
    sub.add_parser("next", help="Get the next action to perform")

    # baton execute record --step-id ID --agent NAME [--status S] [--outcome O] [--tokens N] [--duration N] [--error E]
    p_record = sub.add_parser("record", help="Record a step completion")
    p_record.add_argument("--step-id", required=True, help="Step ID (e.g. 1.1)")
    p_record.add_argument("--agent", required=True, help="Agent name")
    p_record.add_argument("--status", default="complete", help="complete or failed")
    p_record.add_argument("--outcome", default="", help="Summary of what was done")
    p_record.add_argument("--tokens", type=int, default=0, help="Estimated tokens used")
    p_record.add_argument("--duration", type=float, default=0.0, help="Duration in seconds")
    p_record.add_argument("--error", default="", help="Error message if failed")
    p_record.add_argument("--files", default="", help="Comma-separated files changed")
    p_record.add_argument("--commit", default="", help="Commit hash")

    # baton execute gate --phase-id N --result pass|fail [--output TEXT]
    p_gate = sub.add_parser("gate", help="Record a QA gate result")
    p_gate.add_argument("--phase-id", type=int, required=True, help="Phase ID")
    p_gate.add_argument("--result", required=True, choices=["pass", "fail"], help="Gate result")
    p_gate.add_argument("--output", default="", help="Gate command output")

    # baton execute complete
    sub.add_parser("complete", help="Finalize execution (writes usage, trace, retrospective)")

    # baton execute status
    sub.add_parser("status", help="Show current execution state")

    # baton execute resume
    sub.add_parser("resume", help="Resume execution after a crash")

    return p


def _print_action(action: dict) -> None:
    """Print an execution action in a human-readable format."""
    atype = action.get("action_type", "")
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
        print("error: supply a subcommand: start, next, record, gate, complete, status, resume")
        sys.exit(1)

    engine = ExecutionEngine()

    if args.subcommand == "start":
        plan_path = Path(args.plan)
        if not plan_path.exists():
            print(f"error: plan file not found: {plan_path}")
            print("Run 'baton plan --save \"task description\"' first.")
            sys.exit(1)
        data = json.loads(plan_path.read_text(encoding="utf-8"))
        plan = MachinePlan.from_dict(data)
        action = engine.start(plan)
        _print_action(action.to_dict())

    elif args.subcommand == "next":
        action = engine.next_action()
        _print_action(action.to_dict())

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

    elif args.subcommand == "complete":
        summary = engine.complete()
        print(summary)

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
