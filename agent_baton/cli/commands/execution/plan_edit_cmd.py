"""``baton plan-edit`` — surgical modifications to a saved plan.

Lets agents fix a misaligned plan without regenerating from scratch.
Operates on the saved plan.json in .claude/team-context/.

Examples::

    baton plan-edit --swap-agent 1.1 backend-engineer
    baton plan-edit --set-risk HIGH
    baton plan-edit --set-type documentation
    baton plan-edit --set-description 1.1 "Audit the auth subsystem"
    baton plan-edit --add-phase Review --add-agent code-reviewer
    baton plan-edit --remove-phase 3
    baton plan-edit --set-complexity heavy
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_baton.models.execution import MachinePlan


def _find_plan() -> Path:
    candidates = [
        Path(".claude/team-context/plan.json"),
        Path("plan.json"),
    ]
    for p in candidates:
        if p.exists():
            return p
    print(
        "Error: no plan.json found. Run 'baton plan --save' first.",
        file=sys.stderr,
    )
    sys.exit(1)


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "plan-edit",
        help="Edit a saved plan in-place (swap agents, adjust risk, add phases)",
    )
    p.add_argument(
        "--swap-agent",
        nargs=2,
        action="append",
        default=[],
        metavar=("STEP_ID", "NEW_AGENT"),
        help="Replace agent in STEP_ID with NEW_AGENT (repeatable)",
    )
    p.add_argument(
        "--set-risk",
        default=None,
        choices=["LOW", "MEDIUM", "HIGH", "CRITICAL"],
        help="Override the plan's risk level",
    )
    p.add_argument(
        "--set-type",
        default=None,
        help="Override the plan's task_type",
    )
    p.add_argument(
        "--set-complexity",
        default=None,
        choices=["light", "medium", "heavy"],
        help="Override the plan's complexity",
    )
    p.add_argument(
        "--set-description",
        nargs=2,
        action="append",
        default=[],
        metavar=("STEP_ID", "DESCRIPTION"),
        help="Set a step's task_description (repeatable)",
    )
    p.add_argument(
        "--add-phase",
        action="append",
        default=[],
        metavar="NAME",
        help="Append a new phase with the given name (repeatable)",
    )
    p.add_argument(
        "--add-agent",
        action="append",
        default=[],
        metavar="AGENT",
        help="Add agent to the last --add-phase or to a new Implement phase",
    )
    p.add_argument(
        "--remove-phase",
        action="append",
        default=[],
        type=int,
        metavar="PHASE_ID",
        help="Remove phase by phase_id (repeatable)",
    )
    p.add_argument(
        "--set-model",
        nargs=2,
        action="append",
        default=[],
        metavar=("STEP_ID", "MODEL"),
        help="Set model for a step (repeatable)",
    )
    p.add_argument(
        "--plan-file",
        default=None,
        metavar="PATH",
        help="Path to plan.json (default: .claude/team-context/plan.json)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    plan_path = Path(args.plan_file) if args.plan_file else _find_plan()
    data = json.loads(plan_path.read_text(encoding="utf-8"))

    changes: list[str] = []

    if args.set_risk:
        old = data.get("risk_level", "?")
        data["risk_level"] = args.set_risk
        changes.append(f"risk: {old} -> {args.set_risk}")

    if args.set_type:
        old = data.get("task_type", "?")
        data["task_type"] = args.set_type
        changes.append(f"task_type: {old} -> {args.set_type}")

    if args.set_complexity:
        old = data.get("complexity", "?")
        data["complexity"] = args.set_complexity
        changes.append(f"complexity: {old} -> {args.set_complexity}")

    for step_id, new_agent in args.swap_agent:
        _applied = False
        for phase in data.get("phases", []):
            for step in phase.get("steps", []):
                if step.get("step_id") == step_id:
                    old = step.get("agent_name", "?")
                    step["agent_name"] = new_agent
                    changes.append(f"step {step_id}: {old} -> {new_agent}")
                    _applied = True
                for member in step.get("team", []):
                    if member.get("member_id") == step_id:
                        old = member.get("agent_name", "?")
                        member["agent_name"] = new_agent
                        changes.append(f"team member {step_id}: {old} -> {new_agent}")
                        _applied = True
        if not _applied:
            print(f"Warning: step_id '{step_id}' not found", file=sys.stderr)

    for step_id, desc in args.set_description:
        _applied = False
        for phase in data.get("phases", []):
            for step in phase.get("steps", []):
                if step.get("step_id") == step_id:
                    step["task_description"] = desc
                    changes.append(f"step {step_id} description updated")
                    _applied = True
        if not _applied:
            print(f"Warning: step_id '{step_id}' not found", file=sys.stderr)

    for step_id, model in args.set_model:
        _applied = False
        for phase in data.get("phases", []):
            for step in phase.get("steps", []):
                if step.get("step_id") == step_id:
                    step["model"] = model
                    changes.append(f"step {step_id} model -> {model}")
                    _applied = True
        if not _applied:
            print(f"Warning: step_id '{step_id}' not found", file=sys.stderr)

    for phase_id in args.remove_phase:
        before = len(data.get("phases", []))
        data["phases"] = [
            p for p in data.get("phases", [])
            if p.get("phase_id") != phase_id
        ]
        if len(data.get("phases", [])) < before:
            changes.append(f"removed phase {phase_id}")
        else:
            print(f"Warning: phase_id {phase_id} not found", file=sys.stderr)

    if args.add_phase:
        existing_ids = [p.get("phase_id", 0) for p in data.get("phases", [])]
        next_id = max(existing_ids, default=0) + 1
        agents_to_add = list(args.add_agent) if args.add_agent else []
        for phase_name in args.add_phase:
            agent = agents_to_add.pop(0) if agents_to_add else "architect"
            new_phase = {
                "phase_id": next_id,
                "name": phase_name,
                "steps": [{
                    "step_id": f"{next_id}.1",
                    "agent_name": agent,
                    "task_description": f"{phase_name}: {data.get('task_summary', '')}",
                    "model": "sonnet",
                }],
            }
            data.setdefault("phases", []).append(new_phase)
            changes.append(f"added phase {next_id} '{phase_name}' with {agent}")
            next_id += 1

    if not changes:
        print("No changes specified. Use --help for available options.")
        return

    # Validate round-trip
    try:
        MachinePlan.from_dict(data)
    except Exception as exc:
        print(f"Error: edits produced invalid plan: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print("Changes (dry-run, not saved):")
        for c in changes:
            print(f"  - {c}")
        return

    plan_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    print(f"Plan updated ({plan_path}):")
    for c in changes:
        print(f"  - {c}")

    # Update plan.md if it exists alongside
    md_path = plan_path.with_suffix(".md")
    if md_path.exists():
        try:
            plan = MachinePlan.from_dict(data)
            from agent_baton.cli.commands.execution.plan_cmd import _render_plan_md
            md_path.write_text(_render_plan_md(plan), encoding="utf-8")
            print(f"  plan.md regenerated")
        except Exception:
            print(f"  Warning: could not regenerate plan.md", file=sys.stderr)
