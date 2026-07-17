"""``baton run`` -- autonomously drive an orchestrated execution in the foreground.

This module is a thin, backward-compatible CLI shim.  The actual autonomous
loop lives in ``_handle_run`` (``cli/commands/execution/execute.py``, also
reachable as ``baton execute run``) -- the canonical implementation of the
"one lifecycle, four surfaces" contract described in
``docs/internal/execution-runtime-contract.md`` (see its §7.4 compatibility
plan).  ``baton run`` used to construct its own independent
``ExecutionEngine`` + ``TaskWorker`` + ``BatonRunner`` stack, which was a
second, divergent implementation of the same autonomous-loop contract
(different active-task resolution, different resume guard, different
gate/approval semantics) and the source of a real bug: it called
``ExecutionEngine.start(plan, task_id=...)``, a signature the engine has
never had, so ``baton run --dry-run`` (and every other invocation that
started a fresh plan) crashed with a ``TypeError``.

Delegates to:
    agent_baton.cli.commands.execution.execute._handle_run
"""
from __future__ import annotations

import argparse

from agent_baton.cli.colors import error as color_error, info as color_info

# A practically non-constraining step ceiling for the canonical runner's
# ``--max-steps`` safety limit.  ``baton run`` never exposed its own cap, so
# this is chosen high enough that no real plan should hit it while still
# guarding against a genuine infinite loop.
_DEFAULT_MAX_STEPS = 2000


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "run",
        help="Autonomously run a plan in the foreground with interactive prompts",
    )
    p.add_argument(
        "--plan",
        default=".claude/team-context/plan.json",
        help="Path to plan.json (default: .claude/team-context/plan.json)",
    )
    p.add_argument(
        "--task-id",
        default=None,
        help="Target a specific execution by task ID (default: active execution)",
    )
    p.add_argument(
        "--max-parallel",
        metavar="N",
        type=int,
        default=3,
        help=(
            "Accepted for backward compatibility only -- the canonical "
            "runner dispatches one step at a time and does not honor this "
            "flag."
        ),
    )
    p.add_argument(
        "--max-steps",
        metavar="N",
        type=int,
        default=_DEFAULT_MAX_STEPS,
        help=f"Safety limit: maximum steps before aborting (default: {_DEFAULT_MAX_STEPS})",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        default=False,
        help="Dry-run mode: no real agent calls will be made",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Accepted for backward compatibility only -- the canonical "
            "runner always resumes an active/resumable execution "
            "automatically and refuses to silently restart one, whether "
            "or not this flag is passed."
        ),
    )
    return p


def handler(args: argparse.Namespace) -> None:
    """Translate the ``baton run`` CLI surface onto the canonical runner.

    Keeps the ``baton run`` flags stable (no breaking CLI change) while
    running through the exact same active-task resolution,
    resumable/terminal status guard, and non-TTY-safe approval/feedback/
    interact pausing that ``baton execute run`` already implements --
    "the canonical execute runner is the single implementation" per the
    Phase 2 unified-lifecycle contract.  No second state machine is
    constructed here.
    """
    from agent_baton.cli.commands.execution.execute import _handle_run

    if getattr(args, "max_parallel", 3) != 3:
        print(
            color_info(
                "note: 'baton run' delegates to the canonical sequential "
                "runner ('baton execute run'); --max-parallel is accepted "
                "for backward compatibility but has no effect."
            )
        )

    delegate_args = argparse.Namespace(
        subcommand="run",
        plan=args.plan,
        task_id=getattr(args, "task_id", None),
        model="sonnet",
        max_steps=getattr(args, "max_steps", _DEFAULT_MAX_STEPS),
        token_budget=0,
        dry_run=getattr(args, "dry_run", False),
        force_override=False,
        override_justification="",
        output="text",
    )

    try:
        _handle_run(delegate_args)
    except KeyboardInterrupt:
        print(color_error("\nExecution aborted by user."))
