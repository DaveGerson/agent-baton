"""`baton goal` — set a completion condition and plan against it (G1).

Thin wrapper over `baton plan --goal=<condition> --save`. The actual
goal-driven execution loop is implemented in the engine: after each
gate passes, ``ExecutionEngine._evaluate_goal_after_gate`` consults the
``GoalEvaluator`` and uses ``amend_plan`` to round out gaps the initial
plan missed. See ``docs/internal/agent-teams-and-goal-design.md``.

Examples::

    baton goal "all four integration tests pass under load"
    baton goal "no failing pytest collection" --max-amend-cycles 5
    baton goal "build succeeds with --release" --no-execute
"""
from __future__ import annotations

import argparse
import sys

from agent_baton.cli.commands.execution import plan_cmd


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "goal",
        help=(
            "Plan against a completion condition; the engine drives "
            "amend cycles until met (or budget runs out)."
        ),
        description=(
            "Set a completion condition and plan against it. After each "
            "gate passes, the engine evaluates whether the goal is met "
            "and uses amend_plan() to round out gaps. Termination: goal "
            "met, amend budget exhausted, or BATON_RUN_TOKEN_CEILING hit."
        ),
    )
    p.add_argument(
        "condition",
        help="The completion condition (a single quoted sentence).",
    )
    p.add_argument(
        "--max-amend-cycles",
        dest="max_amend_cycles",
        type=int,
        default=3,
        metavar="N",
        help="Maximum goal-driven round-out cycles. Default: 3.",
    )
    p.add_argument(
        "--task-type",
        dest="task_type",
        default=None,
        help="Override task type (passed through to baton plan).",
    )
    p.add_argument(
        "--complexity",
        dest="complexity",
        default=None,
        choices=["light", "medium", "heavy"],
        help="Override task complexity (passed through to baton plan).",
    )
    p.add_argument(
        "--project",
        default=None,
        help="Project root (default: cwd).",
    )
    p.add_argument(
        "--knowledge",
        dest="knowledge",
        action="append",
        default=[],
        metavar="PATH",
        help="Explicit knowledge document path (repeatable).",
    )
    p.add_argument(
        "--knowledge-pack",
        dest="knowledge_pack",
        action="append",
        default=[],
        metavar="PACK",
        help="Explicit knowledge pack name (repeatable).",
    )
    p.add_argument(
        "--model",
        dest="model",
        default=None,
        help="Default model for dispatched agents.",
    )
    p.add_argument(
        "--gate-scope",
        dest="gate_scope",
        default="focused",
        choices=["focused", "full", "smoke"],
        help="How broadly gate commands run.",
    )
    p.add_argument(
        "--intervention",
        dest="intervention",
        default="low",
        choices=["low", "medium", "high"],
        help="Knowledge-gap escalation aggressiveness.",
    )
    p.add_argument(
        "--explain",
        action="store_true",
        help="Write an explanation.md alongside the saved plan.",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Print full plan markdown after save.",
    )
    p.add_argument(
        "--no-execute",
        dest="no_execute",
        action="store_true",
        help=(
            "Stop after creating the plan; do not print the "
            "'next: baton execute start' guidance."
        ),
    )
    return p


def handler(args: argparse.Namespace) -> None:
    """Delegate to the plan handler with --goal preset, then print
    next-step guidance.

    We don't auto-start execution: the orchestrator owns the action
    loop. This keeps the goal command additive and resumable — re-run
    it without --save to preview, then run `baton execute start`.
    """
    if not args.condition or not args.condition.strip():
        print("Error: goal condition must be a non-empty string.", file=sys.stderr)
        sys.exit(2)

    # Build a Namespace shaped like `baton plan`'s handler expects.
    plan_args = argparse.Namespace(
        summary=args.condition,
        task_type=args.task_type,
        agents=None,
        project=args.project,
        json=False,
        save=True,
        explain=args.explain,
        knowledge=list(args.knowledge),
        knowledge_pack=list(args.knowledge_pack),
        intervention=args.intervention,
        model=args.model,
        complexity=args.complexity,
        import_path=None,
        template=False,
        save_as_template=None,
        from_template=None,
        skip_init=False,
        verbose=args.verbose,
        dry_run=False,
        release_id=None,
        gate_scope=args.gate_scope,
        goal=args.condition,
        max_amend_cycles=args.max_amend_cycles,
    )

    plan_cmd.handler(plan_args)

    if not args.no_execute:
        print()
        print(
            f"Goal set: \"{args.condition}\"  "
            f"(amend budget: {args.max_amend_cycles})"
        )
        print(
            "The engine will evaluate the goal after each gate passes "
            "and amend the plan to close gaps."
        )
        print("Next: baton execute start")
