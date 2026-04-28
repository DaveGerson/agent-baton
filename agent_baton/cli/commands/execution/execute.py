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
from datetime import datetime, timezone
from pathlib import Path

from agent_baton.cli.colors import success, error as color_error, warning, info as color_info
from agent_baton.cli.errors import user_error, validation_error
from agent_baton.core.engine.errors import ExecutionVetoed
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.persistence import StatePersistence
from agent_baton.core.events.bus import EventBus
from agent_baton.core.storage import get_project_storage
from agent_baton.core.orchestration.context import ContextManager
from agent_baton.core.runtime.supervisor import WorkerSupervisor
from agent_baton.core.storage import detect_backend, get_project_storage
from agent_baton.models.events import Event
from agent_baton.models.execution import MachinePlan, ActionType, PlanPhase, PlanStep
from agent_baton.models.parallel import ExecutionRecord
from agent_baton.models.plan import MissionLogEntry

_log = logging.getLogger(__name__)

# Step-ID validators (single source of truth shared across subcommands).
# See ``agent_baton/cli/commands/execution/_validators.py``.
from agent_baton.cli.commands.execution._validators import (
    PLAIN_STEP_ID_RE as _STEP_ID_RE,  # back-compat alias for any external import
    STEP_ID_RE,
    STEP_ID_FORMAT_HINT,
    TEAM_MEMBER_ID_RE as _TEAM_MEMBER_ID_RE,  # back-compat alias
    is_team_member_id as _is_team_member_id,
    parent_step_id as _parent_step_id,
    validate_step_id as _validate_step_id,
)


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

    # baton execute start [--plan PATH] [--task-id ID] [--dry-run]
    p_start = sub.add_parser("start", parents=[_task_id_parent],
                             help="Start execution from a saved plan")
    p_start.add_argument(
        "--plan",
        default=".claude/team-context/plan.json",
        help="Path to plan.json (default: .claude/team-context/plan.json)",
    )
    p_start.add_argument(
        "--predict-conflicts",
        action="store_true",
        dest="predict_conflicts",
        help=(
            "Run R3.7 file-conflict prediction over the plan before "
            "dispatching the first action and print warnings (never blocks). "
            "Equivalent to setting BATON_CONFLICT_PREDICT=1."
        ),
    )
    p_start.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        default=False,
        help=(
            "Dry-run mode: no Claude API calls and no file writes by agents.  "
            "Subsequent 'baton execute next/run' calls will use the dry-run "
            "launcher and gate runner.  Prints a banner so the mode is "
            "visible at every step."
        ),
    )

    # baton execute dry-run [--plan PATH] [--max-steps N]
    # Convenience: load plan, start in dry-run mode, drive the loop to
    # COMPLETE, and write a summary report.  Single-shot entrypoint.
    p_dry = sub.add_parser("dry-run", parents=[_task_id_parent],
                           help="Walk a plan end-to-end with no API calls and write a report")
    p_dry.add_argument("--plan", default=".claude/team-context/plan.json",
                       help="Path to plan.json (default: .claude/team-context/plan.json)")
    p_dry.add_argument("--max-steps", type=int, default=50, dest="max_steps",
                       help="Safety limit: maximum steps before aborting (default: 50)")

    # baton execute next [--all] [--terse] [--task-id ID]
    next_p = sub.add_parser("next", parents=[_task_id_parent],
                            help="Get the next action to perform")
    next_p.add_argument("--all", action="store_true", dest="all_actions",
                        help="Return all dispatchable actions (for parallel dispatch)")
    next_p.add_argument(
        "--terse",
        action="store_true",
        default=False,
        help=(
            "Terse mode: for DISPATCH actions, write the delegation_prompt to "
            ".claude/team-context/current-dispatch.prompt.md and emit only a "
            "Prompt-File pointer in stdout.  Reduces per-step token burn for "
            "long plans.  Non-DISPATCH actions are unaffected."
        ),
    )
    next_p.add_argument(
        "--force",
        action="store_true",
        default=False,
        dest="force_override",
        help=(
            "Override an auditor VETO on a HIGH/CRITICAL phase.  Requires "
            "--justification; an Override audit row is appended to "
            "compliance-audit.jsonl."
        ),
    )
    next_p.add_argument(
        "--justification",
        default="",
        dest="override_justification",
        help="Required when --force is supplied; recorded in the audit log.",
    )

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
    p_record.add_argument("--session-id", default="", dest="session_id",
                          help="Claude Code session UUID ($CLAUDE_SESSION_ID) for real token accounting")
    p_record.add_argument("--step-started-at", default="", dest="step_started_at",
                          help="ISO 8601 UTC timestamp when this step was dispatched (lower bound for JSONL scan)")
    p_record.add_argument("--outcome-spillover-path", default="", dest="outcome_spillover_path",
                          help="Relative path (under the per-task execution dir) to a spillover file holding the FULL outcome when --outcome was truncated. When omitted, the engine attempts to auto-detect the path from a TRUNCATED breadcrumb in --outcome.")

    # baton execute dispatched --step ID --agent NAME
    dispatched_p = sub.add_parser("dispatched", parents=[_task_id_parent],
                                  help="Mark a step as dispatched (in-flight)")
    dispatched_p.add_argument("--step", "--step-id", required=True, dest="step_id")
    dispatched_p.add_argument("--agent", required=True)

    # baton execute gate --phase-id N --result pass|fail [--gate-output TEXT]
    p_gate = sub.add_parser("gate", parents=[_task_id_parent],
                            help="Record a QA gate result")
    p_gate.add_argument("--phase-id", type=int, required=True, help="Phase ID")
    # Wave 4.1 — --result is required for the manual record path (pass/fail).
    # When --type=ci is supplied the CLI runs the CI gate itself and derives
    # pass/fail from the provider; --result becomes optional in that mode.
    p_gate.add_argument("--result", choices=["pass", "fail"], default=None,
                        help="Gate result (required unless --type=ci, which derives result from CI)")
    p_gate.add_argument("--gate-output", "--notes", default="", dest="gate_output",
                        help="Gate command output or notes (--notes is accepted as an alias; --output is reserved for format)")
    # Wave 4.1 — opt-in CI gate.  Lets an operator drive a CI gate
    # manually without modifying the plan: 'baton execute gate
    # --phase-id N --type ci --workflow ci.yml'.
    p_gate.add_argument("--type", default=None, dest="gate_type_override",
                        help="Gate type override (e.g. 'ci' to dispatch CI provider gate)")
    p_gate.add_argument("--workflow", default="", dest="ci_workflow",
                        help="CI workflow file (used with --type ci, e.g. 'ci.yml')")
    p_gate.add_argument("--ci-timeout", type=int, default=None, dest="ci_timeout_s",
                        help="CI gate timeout in seconds (used with --type ci, default 600)")

    # baton execute approve --phase-id N --result approve|reject|approve-with-feedback [--feedback TEXT]
    p_approve = sub.add_parser("approve", parents=[_task_id_parent],
                               help="Record a human approval decision")
    p_approve.add_argument("--phase-id", type=int, required=True, help="Phase ID requiring approval")
    p_approve.add_argument("--result", required=True,
                           choices=["approve", "reject", "approve-with-feedback"],
                           help="Approval decision")
    p_approve.add_argument("--feedback", "--notes", default="", dest="feedback",
                           help="Feedback or notes text (for approve-with-feedback; --notes is accepted as an alias)")

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
    p_team.add_argument("--outcome-spillover-path", default="", dest="outcome_spillover_path",
                        help="Relative path (under per-task execution dir) to a spillover file holding the FULL member outcome when --outcome was truncated. When omitted, the engine attempts to auto-detect from a TRUNCATED breadcrumb in --outcome.")

    # baton execute interact --step-id ID (--input TEXT | --done)
    p_interact = sub.add_parser("interact", parents=[_task_id_parent],
                                help="Provide input to an interactive step or signal it is done")
    p_interact.add_argument("--step-id", required=True, dest="step_id",
                            help="Step ID in 'interacting' status (e.g. 1.1)")
    _interact_grp = p_interact.add_mutually_exclusive_group(required=True)
    _interact_grp.add_argument("--input", dest="interact_input", default=None,
                               help="Human input text to send to the agent")
    _interact_grp.add_argument("--done", action="store_true", dest="interact_done",
                               help="Signal that the interaction is complete")

    # baton execute run [--plan PATH] [--task-id ID] [--model MODEL] [--max-steps N] [--dry-run]
    p_run = sub.add_parser("run", parents=[_task_id_parent],
                           help="Autonomous execution loop (no Claude Code session needed)")
    p_run.add_argument("--plan", default=".claude/team-context/plan.json",
                       help="Path to plan.json (default: .claude/team-context/plan.json)")
    p_run.add_argument("--model", default="sonnet",
                       help="Default model for dispatched agents (default: sonnet)")
    p_run.add_argument("--max-steps", type=int, default=50, dest="max_steps",
                       help="Safety limit: maximum steps before aborting (default: 50)")
    p_run.add_argument("--token-budget", type=int, default=0, dest="token_budget",
                       help="Soft token cap: stop dispatching new steps when exceeded (0 = use tier default)")
    p_run.add_argument("--dry-run", action="store_true", dest="dry_run",
                       help="Print actions without executing them")
    p_run.add_argument(
        "--force",
        action="store_true",
        default=False,
        dest="force_override",
        help=(
            "Override an auditor VETO on a HIGH/CRITICAL phase for the "
            "duration of this run.  Requires --justification; appends an "
            "Override row to compliance-audit.jsonl."
        ),
    )
    p_run.add_argument(
        "--justification",
        default="",
        dest="override_justification",
        help="Required when --force is supplied; recorded in the audit log.",
    )

    # baton execute complete [--task-id ID]
    sub.add_parser("complete", parents=[_task_id_parent],
                   help="Finalize execution (writes usage, trace, retrospective)")

    # baton execute status [--task-id ID]
    sub.add_parser("status", parents=[_task_id_parent],
                   help="Show current execution state")

    # baton execute resume [--task-id ID] [--abort] [--no-rerun-gate]
    p_resume = sub.add_parser("resume", parents=[_task_id_parent],
                              help="Resume execution after a crash")
    # bd-5d4f: declare --abort and --no-rerun-gate explicitly so they show
    # up in `baton execute resume --help`.  The dispatch code at the
    # paused-takeover branch already reads these via getattr() with safe
    # defaults, so behaviour is unchanged for non-takeover resumes.
    p_resume.add_argument(
        "--abort", dest="abort", action="store_true", default=False,
        help="Abort an active takeover (paused-takeover status only)",
    )
    p_resume.add_argument(
        "--no-rerun-gate", dest="no_rerun_gate", action="store_true", default=False,
        help="Skip automatic gate re-run when resuming from a takeover",
    )

    # baton execute cancel [--task-id ID] [--reason TEXT]
    p_cancel = sub.add_parser("cancel", parents=[_task_id_parent],
                              help="Cancel a running execution")
    p_cancel.add_argument("--reason", default="", help="Reason for cancellation")

    # baton execute retry-gate --phase-id N [--task-id ID]
    p_retry_gate = sub.add_parser("retry-gate", parents=[_task_id_parent],
                                  help="Reset a failed gate back to pending for retry")
    p_retry_gate.add_argument("--phase-id", type=int, required=True, dest="phase_id",
                              help="Phase ID whose failed gate should be reset")

    # baton execute fail --phase-id N [--task-id ID]
    p_fail = sub.add_parser("fail", parents=[_task_id_parent],
                            help="Permanently fail an execution that is in gate_failed status")
    p_fail.add_argument("--phase-id", type=int, required=True, dest="phase_id",
                        help="Phase ID of the failed gate (for confirmation output)")

    # baton execute resume-budget [--task-id ID]
    sub.add_parser("resume-budget", parents=[_task_id_parent],
                   help="Clear budget_exceeded status so execution can continue")

    # baton execute verify-dispatch STEP_ID [--task-id ID] [--output text|json]
    # Read-only post-dispatch isolation check (bd-edbf).  Verifies that the
    # files written by a single step fall under its declared allowed_paths
    # and that the recorded commit_hash resolves in the repo.
    p_verify = sub.add_parser(
        "verify-dispatch", parents=[_task_id_parent],
        help="Verify a dispatched step's filesystem + branch compliance",
    )
    p_verify.add_argument(
        "verify_step_id", metavar="STEP_ID",
        help="Step ID to verify (e.g. 1.1)",
    )

    # baton execute audit-isolation [--task-id ID] [--output text|json]
    # Task-wide audit; runs verify-dispatch over every recorded step.
    # Exits non-zero on any violation.
    sub.add_parser(
        "audit-isolation", parents=[_task_id_parent],
        help="Audit every dispatched step for isolation compliance",
    )

    # baton execute list
    sub.add_parser("list", help="List all executions (active and completed)")

    # baton execute switch TASK_ID
    p_switch = sub.add_parser("switch", help="Switch the active execution to a different task ID")
    p_switch.add_argument("switch_task_id", metavar="TASK_ID", help="Task ID to switch to")

    # baton execute handoff --note "..." [--branch] [--score]  (DX.3 / bd-d136)
    p_handoff = sub.add_parser(
        "handoff", parents=[_task_id_parent],
        help="Record a session-handoff note + quality score (DX.3)",
    )
    p_handoff.add_argument("--note", default=None,
                           help="Handoff note (free text). Required for record mode.")
    p_handoff.add_argument("--branch", action="store_true", default=False,
                           help="Include git branch + commits-ahead-of-master in the note")
    p_handoff.add_argument("--score", action="store_true", default=False,
                           help="Print the quality score after writing")
    p_handoff.add_argument("--limit", type=int, default=20,
                           help="(list mode only) Maximum rows to return (default: 20)")
    p_handoff.add_argument("handoff_action", nargs="?", default=None,
                           choices=[None, "record", "list", "show"],
                           help="record (default), list, or show")
    p_handoff.add_argument("handoff_id", nargs="?", default=None,
                           help="(show mode only) Handoff ID")
    # baton execute worktree-gc  (Wave 1.3, bd-86bf)
    p_wt_gc = sub.add_parser(
        "worktree-gc",
        help="Garbage-collect stale isolated worktrees (Wave 1.3)",
    )
    p_wt_gc.add_argument(
        "--max-age-hours",
        type=int,
        default=72,
        metavar="N",
        help="Reclaim worktrees older than N hours (default: 72)",
    )
    p_wt_gc.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print what would be removed without actually removing",
    )

    # ── Wave 5.1 (bd-e208): Developer Takeover ───────────────────────────────
    # baton execute takeover STEP_ID [--editor CMD] [--shell] [--reason TEXT] [--no-rerun-gate]
    p_takeover = sub.add_parser(
        "takeover", parents=[_task_id_parent],
        help="Open the retained failed worktree for manual developer intervention (Wave 5.1)",
    )
    p_takeover.add_argument(
        "takeover_step_id", metavar="STEP_ID",
        help="Step ID whose retained worktree to enter (e.g. 1.3)",
    )
    p_takeover.add_argument(
        "--editor", dest="takeover_editor", default="",
        help="Editor command to launch (default: $EDITOR or vim)",
    )
    p_takeover.add_argument(
        "--shell", dest="takeover_shell", action="store_true", default=False,
        help="Drop into $SHELL instead of an editor",
    )
    p_takeover.add_argument(
        "--reason", dest="takeover_reason", default="",
        help="Reason for takeover (required when step status is not 'failed')",
    )
    p_takeover.add_argument(
        "--no-rerun-gate", dest="no_rerun_gate", action="store_true", default=False,
        help="Skip automatic gate re-run on 'baton execute resume'",
    )

    # bd-5d4f: --abort and --no-rerun-gate are now declared on the resume
    # parser at registration time (see p_resume above).

    # ── Wave 5.2 (bd-1483): Manual Self-Heal Trigger ─────────────────────────
    # baton execute self-heal STEP_ID [--max-tier opus] [--task-id ID]
    p_selfheal = sub.add_parser(
        "self-heal", parents=[_task_id_parent],
        help="Manually trigger self-heal escalation for a failed step (Wave 5.2)",
    )
    p_selfheal.add_argument(
        "selfheal_step_id", metavar="STEP_ID",
        help="Step ID to attempt self-heal on",
    )
    p_selfheal.add_argument(
        "--max-tier", dest="selfheal_max_tier", default="opus",
        choices=["haiku", "sonnet", "opus"],
        help="Maximum escalation tier (default: opus)",
    )

    # ── Wave 5.3 (bd-9839): Speculation Management ───────────────────────────
    # baton execute speculate status|accept|reject|show [SPEC_ID]
    p_speculate = sub.add_parser(
        "speculate", parents=[_task_id_parent],
        help="Manage speculative pipeline worktrees (Wave 5.3)",
    )
    p_speculate.add_argument(
        "speculate_action", metavar="ACTION",
        choices=["status", "accept", "reject", "show"],
        help="Action: status | accept [spec_id] | reject <spec_id> | show <spec_id>",
    )
    p_speculate.add_argument(
        "speculate_id", metavar="SPEC_ID", nargs="?", default=None,
        help="Speculation ID (required for accept/reject/show)",
    )
    p_speculate.add_argument(
        "--reason", dest="speculate_reason", default="",
        help="Rejection reason (for reject action)",
    )

    return p


_DISPATCH_PROMPT_SIDECAR = ".claude/team-context/current-dispatch.prompt.md"

# Banner printed at the start of any dry-run flow.  Used by both
# ``baton execute start --dry-run`` and ``baton execute dry-run`` so the
# mode is visible at every entrypoint.
_DRY_RUN_BANNER = (
    "=== DRY RUN MODE — no API calls, no file writes will occur ==="
)

# Filename written by the dry-run report writer (relative to context_root).
_DRY_RUN_REPORT_FILENAME = "dry-run-report.md"

# Default placeholder durations used when a real launch did not actually
# occur.  These are crude order-of-magnitude estimates intended to give
# developers a "this plan would take ~N seconds" feel rather than precise
# wall-clock values.
_DRY_RUN_DISPATCH_SECONDS = 10.0
_DRY_RUN_GATE_SECONDS = 2.0


def _emit_conflict_predictions(plan: MachinePlan) -> None:
    """Run R3.7 conflict prediction over *plan* and emit warnings.

    Velocity-positive: warnings only, never blocks.  Defaults OFF; the
    caller is responsible for the opt-in check (``--predict-conflicts``
    flag or ``BATON_CONFLICT_PREDICT=1``).
    """
    # Local import keeps engine startup latency at zero for the default
    # (opt-in disabled) path.
    from agent_baton.core.release.conflict_predictor import ConflictPredictor

    report = ConflictPredictor(plan).predict()
    if not report.has_conflicts:
        _log.info(
            "Conflict prediction: no parallel-step conflicts found "
            "(steps=%d, parallel_groups=%d)",
            report.total_steps_analyzed,
            report.parallel_groups_analyzed,
        )
        return
    for c in report.conflicts:
        msg = (
            f"Predicted conflict: {c.step_a_id} <-> {c.step_b_id} "
            f"on {c.file_path} ({c.conflict_type}, conf={c.confidence:.2f})"
        )
        _log.warning(msg)
        # Also surface to stderr so operators see warnings without enabling
        # debug logging.
        print(f"WARNING: {msg}", file=sys.stderr)


def _resolve_context_root() -> Path:
    """Resolve the team-context root to an absolute path anchored at the project root.

    Walks up from ``Path.cwd()`` looking for a directory that contains
    ``.claude/team-context/``.  This makes all CLI subcommands immune to
    CWD changes (e.g. ``cd pmo-ui && baton execute gate ...`` still finds
    the correct ``baton.db``).

    If no ancestor directory contains the marker, falls back to
    ``Path.cwd() / ".claude/team-context"`` (the legacy behaviour) so that
    ``baton execute start`` can still bootstrap a fresh project.

    Returns:
        An absolute ``Path`` to the ``.claude/team-context/`` directory.
    """
    import subprocess

    # Fastest path: ask git for the repo root.
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            git_root = Path(result.stdout.strip())
            ctx = git_root / ".claude" / "team-context"
            if ctx.is_dir():
                return ctx.resolve()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # Fallback: walk up the directory tree.
    cwd = Path.cwd()
    for ancestor in [cwd, *cwd.parents]:
        candidate = ancestor / ".claude" / "team-context"
        if candidate.is_dir():
            return candidate.resolve()

    # Last resort: relative to CWD (allows bootstrap of fresh project).
    return (cwd / ".claude" / "team-context").resolve()


def _write_dispatch_sidecar(prompt: str) -> str:
    """Write *prompt* to the standard sidecar path and return the path string.

    The directory is created if it does not already exist.  The file is always
    overwritten so it always reflects the most-recently-dispatched step.
    """
    sidecar = Path(_DISPATCH_PROMPT_SIDECAR)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(prompt, encoding="utf-8")
    return _DISPATCH_PROMPT_SIDECAR


def _print_action(action: dict, *, terse: bool = False) -> None:
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
          Expected: <demo statement>     # Wave 3.1, omitted when not derived

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

    Note:
        ``CANCELLED`` is **not** an action type emitted by ``next``.  It is a
        status transition applied directly to :class:`ExecutionState` by the
        ``baton execute cancel`` subcommand and is never produced by
        :meth:`ExecutionEngine.next_action`.

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
        step_id = action.get('step_id', '')
        step_type = action.get('step_type', '')
        print(f"ACTION: DISPATCH")
        if step_type == "automation":
            # Automation steps have no agent or model — show command block only.
            print(f"  Step:    {step_id}")
            print(f"  Type:    automation")
            print(f"  Command: {action.get('command', '')}")
            print(f"  Message: {msg}")
            print()
            print("--- Command ---")
            print(action.get("command", ""))
            print("--- End Command ---")
        else:
            print(f"  Agent: {action.get('agent_name', '')}")
            print(f"  Model: {action.get('agent_model', '')}")
            print(f"  Step:  {step_id}")
            if step_type:
                print(f"  Type:  {step_type}")
            if _is_team_member_id(step_id):
                # Derive the parent step ID (e.g. "1.1.a" → "1.1") so the
                # orchestrator knows which parent to reference in team-record.
                parent_step_id = ".".join(step_id.split(".")[:2])
                print(f"  Team-Step: yes")
                print(f"  Parent-Step: {parent_step_id}")
                print(f"  Record-With: baton execute team-record --step-id {parent_step_id} --member-id {step_id} ...")
            if action.get("interactive"):
                print(f"  Interactive: yes")
                print(f"  Max-Turns: {action.get('interact_max_turns', 10)}")
            # Wave 1.3 (bd-86bf): emit worktree fields when present.
            # ADDITIVE — existing field order is preserved.
            if action.get("worktree_path"):
                print(f"  Worktree:  {action['worktree_path']}")
            if action.get("worktree_branch"):
                print(f"  Branch:    {action['worktree_branch']}")
            print(f"  Message: {msg}")
            # Wave 3.1 — Expected Outcome (Demo Statement). Surfaced on its
            # own line so the orchestrator and human reviewer can see the
            # behavioral demo statement without parsing the delegation prompt.
            expected = action.get("expected_outcome", "")
            if expected:
                print(f"  Expected: {expected}")
            if terse:
                prompt = action.get("delegation_prompt", "")
                sidecar_path = _write_dispatch_sidecar(prompt)
                print(f"  Prompt-File: {sidecar_path}")
            else:
                print()
                print("--- Delegation Prompt ---")
                print(action.get("delegation_prompt", ""))
                print("--- End Prompt ---")
            print()
            agent_name = action.get('agent_name', '')
            print("When complete, record the result:")
            print(f"  baton execute record --step {step_id} --agent {agent_name} --status complete --outcome \"summary\"")
            print(f"  baton execute record --step {step_id} --agent {agent_name} --status failed --error \"what went wrong\"")

    elif atype == ActionType.GATE.value:
        phase_id = action.get('phase_id', '')
        gate_type = action.get('gate_type', '')
        gate_cmd = action.get('gate_command', '')
        print(f"ACTION: GATE")
        print(f"  Type:    {gate_type}")
        print(f"  Phase:   {phase_id}")
        print(f"  Command: {gate_cmd}")
        print(f"  Message: {msg}")
        # Wave 4.1 — surface CI gate context up-front so the orchestrator
        # knows this is a long-running poll, not a local subprocess gate.
        if gate_type == "ci":
            try:
                import subprocess as _sp_pa
                _sha = _sp_pa.run(
                    ["git", "rev-parse", "HEAD"],
                    capture_output=True, text=True, check=False, timeout=5,
                ).stdout.strip()[:8] or "?"
            except Exception:
                _sha = "?"
            workflow_hint = (gate_cmd or "ci.yml").strip() or "ci.yml"
            print(f"  CI:      Waiting for CI workflow {workflow_hint} on {_sha}...")
        print()
        print("To record gate result:")
        print(f"  baton execute gate --phase-id {phase_id} --result pass")
        print(f"  baton execute gate --phase-id {phase_id} --result fail --gate-output \"failure details\"")

    elif atype == ActionType.APPROVAL.value:
        phase_id = action.get('phase_id', '')
        print(f"ACTION: APPROVAL")
        print(f"  Phase:   {phase_id}")
        print(f"  Message: {msg}")
        print()
        print("--- Approval Context ---")
        print(action.get("approval_context", ""))
        print("--- End Context ---")
        print()
        print("To respond:")
        print(f"  baton execute approve --phase-id {phase_id} --result approve")
        print(f"  baton execute approve --phase-id {phase_id} --result reject --feedback \"reason\"")
        print(f"  baton execute approve --phase-id {phase_id} --result approve-with-feedback --feedback \"notes\"")

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

    elif atype == ActionType.INTERACT.value:
        step_id = action.get("interact_step_id", "")
        agent = action.get("interact_agent_name", "")
        turn = action.get("interact_turn", 0)
        max_turns = action.get("interact_max_turns", 10)
        print(f"ACTION: INTERACT")
        print(f"  Step:    {step_id}")
        print(f"  Agent:   {agent}")
        print(f"  Turn:    {turn}/{max_turns}")
        print(f"  Message: {msg}")
        print()
        print("--- Agent Output ---")
        print(action.get("interact_prompt", ""))
        print("--- End Output ---")
        print()
        print(f"Respond with: baton execute interact --step-id {step_id} --input \"<your input>\"")
        print(f"Signal done:  baton execute interact --step-id {step_id} --done")

    else:
        print(f"ACTION: {atype}")
        print(f"  {msg}")


def handler(args: argparse.Namespace) -> None:
    if args.subcommand is None:
        # bd-8944: consolidated single validation_error with the full list of
        # registered subcommands (removed stale duplicate that was unreachable).
        validation_error(
            "supply a subcommand: start, dry-run, next, record, dispatched, gate, "
            "approve, feedback, amend, team-record, interact, complete, status, "
            "resume, list, switch, cancel, run, retry-gate, fail, resume-budget, "
            "verify-dispatch, audit-isolation, handoff, worktree-gc, "
            "takeover, self-heal, speculate"
        )

    if args.subcommand == "list":
        _handle_list()
        return

    if args.subcommand == "switch":
        _handle_switch(args.switch_task_id)
        return

    # Wave 1.3 (bd-86bf): worktree GC subcommand — no engine / task_id required.
    if args.subcommand == "worktree-gc":
        _handle_worktree_gc(args)
        return

    if args.subcommand == "run":
        _handle_run(args)
        return

    if args.subcommand == "handoff":
        _handle_handoff(args)
        return

    if args.subcommand == "dry-run":
        _handle_dry_run(args)
        return

    # Resolve task_id: explicit flag → BATON_TASK_ID env var → SQLite active_task →
    #   file active-task-id.txt → None (legacy flat file)
    task_id = getattr(args, "task_id", None)
    context_root = _resolve_context_root()
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
            task_id = StatePersistence.get_active_task_id(context_root)

    # F0.3 — VETO override (bd-f606): validate --force / --justification
    # before constructing the engine so error messages surface uniformly.
    force_override = bool(getattr(args, "force_override", False))
    override_justification = (getattr(args, "override_justification", "") or "").strip()
    if force_override and not override_justification:
        validation_error(
            "--force requires --justification \"<reason>\"",
            hint="Re-run with --force --justification \"why this VETO is being overridden\"",
        )

    # G1.6 (bd-1a09): mirror the --force invocation into the
    # governance_overrides table + compliance audit chain BEFORE the
    # engine consumes the flag.  Failures are logged but never block
    # execution — overrides must remain a recovery primitive.
    if force_override:
        try:
            from agent_baton.cli._override_helper import record_override

            record_override(
                flag="--force",
                justification=override_justification,
                command=f"baton execute {args.subcommand}",
            )
        except Exception as _ovr_exc:  # pragma: no cover - best-effort logging
            print(
                f"warning: failed to record override audit row: {_ovr_exc}",
                file=sys.stderr,
            )

    bus = EventBus()
    storage = get_project_storage(context_root)
    engine = ExecutionEngine(
        bus=bus,
        task_id=task_id,
        storage=storage,
        force_override=force_override,
        override_justification=override_justification,
    )

    if args.subcommand == "start":
        # Resolve plan path: if task_id is known (from --task-id or
        # BATON_TASK_ID), prefer the task-scoped plan file over the
        # global backward-compat plan.json.  This prevents a later
        # `baton plan --save` from silently hijacking an earlier task.
        plan_path = Path(args.plan)
        if task_id and plan_path == Path(".claude/team-context/plan.json"):
            scoped = context_root / "executions" / task_id / "plan.json"
            if scoped.exists():
                plan_path = scoped
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
        # Use task_id from env/flag if provided; fall back to plan's task_id
        # only when no explicit binding exists.
        if task_id is None:
            task_id = plan.task_id

        # Build a KnowledgeResolver from the plan's knowledge configuration so
        # runtime KNOWLEDGE_GAP signals can be auto-resolved without human gates.
        knowledge_resolver = _build_knowledge_resolver(plan)

        # Build a PolicyEngine so block-severity violations inject APPROVAL
        # actions at dispatch time rather than silently proceeding.
        policy_engine = _build_policy_engine()

        engine = ExecutionEngine(
            team_context_root=context_root,
            bus=bus,
            task_id=task_id,
            storage=storage,
            knowledge_resolver=knowledge_resolver,
            policy_engine=policy_engine,
        )
        ContextManager(task_id=task_id).init_mission_log(plan.task_summary, risk_level=plan.risk_level)

        # R3.7 — opt-in conflict prediction (warnings only, never blocks).
        if (
            getattr(args, "predict_conflicts", False)
            or os.environ.get("BATON_CONFLICT_PREDICT") == "1"
        ):
            _emit_conflict_predictions(plan)

        try:
            action = engine.start(plan)
        except (ValueError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        # engine.start() already calls set_active_task() post-save; no need
        # to call it again here.
        if getattr(args, "output", "text") == "json":
            result = {"task_id": task_id, "action": action.to_dict()}
            if getattr(args, "dry_run", False):
                result["dry_run"] = True
            print(json.dumps(result, indent=2))
        else:
            if getattr(args, "dry_run", False):
                print(_DRY_RUN_BANNER)
            print(f"Session binding: export BATON_TASK_ID={task_id}\n")
            _print_action(action.to_dict())

    elif args.subcommand == "next":
        terse = getattr(args, "terse", False)
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
                if terse:
                    for item in result:
                        if item.get("action_type") == ActionType.DISPATCH.value:
                            prompt = item.get("delegation_prompt", "")
                            sidecar_path = _write_dispatch_sidecar(prompt)
                            item["delegation_prompt"] = sidecar_path
                            item["prompt_file"] = sidecar_path
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
                _action_dict = action.to_dict()
                _print_action(_action_dict, terse=terse)
                # DX.3 (bd-d136): nudge operator to record a handoff at
                # natural pause points (gate failure, approval required,
                # completion).  No-op when stdout is not a TTY or when a
                # handoff has already been recorded for this task.
                _maybe_handoff_nudge(_action_dict, task_id, context_root)
        except ExecutionVetoed as exc:
            print(f"error: {exc}", file=sys.stderr)
            print("Recovery options:", file=sys.stderr)
            print(
                "  Re-run with --force --justification \"...\" to override",
                file=sys.stderr,
            )
            print(
                "  Or amend the plan to address the auditor's concerns",
                file=sys.stderr,
            )
            sys.exit(2)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            print("Recovery options:", file=sys.stderr)
            print("  baton execute resume    — resume from saved state", file=sys.stderr)
            print("  baton execute status    — check current state", file=sys.stderr)
            print("  baton execute list      — see all executions", file=sys.stderr)
            sys.exit(1)

    elif args.subcommand == "dispatched":
        _validate_step_id(args.step_id, validation_error)
        # Symmetric routing with `next`: if the step ID is a team-member form
        # (N.N.x[.y...]), record a dispatched marker against the parent team
        # step using the same store as `team-record`.  Otherwise mark the
        # plain step as dispatched.
        if _is_team_member_id(args.step_id):
            parent_id = _parent_step_id(args.step_id)
            engine.record_team_member_result(
                step_id=parent_id,
                member_id=args.step_id,
                agent_name=args.agent,
                status="dispatched",
            )
            print(json.dumps({
                "status": "dispatched",
                "step_id": args.step_id,
                "parent_step_id": parent_id,
                "team_member": True,
            }))
        else:
            engine.mark_dispatched(step_id=args.step_id, agent_name=args.agent)
            print(json.dumps({"status": "dispatched", "step_id": args.step_id}))

    elif args.subcommand == "record":
        # Deprecation warning for --summary
        if "--summary" in sys.argv:
            print("warning: --summary is deprecated, use --outcome instead", file=sys.stderr)
        _validate_step_id(args.step_id, validation_error)
        files = [f.strip() for f in args.files.split(",") if f.strip()] if args.files else []
        # Symmetric routing: a team-member step ID (N.N.x[.y...]) is recorded
        # against the parent step via the team-member tracking table — the
        # same store that `team-record` writes to.  This makes `record` accept
        # anything `next`/`next --all` emits.
        is_team_member = _is_team_member_id(args.step_id)
        try:
            if is_team_member:
                parent_id = _parent_step_id(args.step_id)
                engine.record_team_member_result(
                    step_id=parent_id,
                    member_id=args.step_id,
                    agent_name=args.agent,
                    status=args.status,
                    outcome=args.outcome,
                    files_changed=files,
                    outcome_spillover_path=getattr(args, "outcome_spillover_path", ""),
                )
            else:
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
                    session_id=getattr(args, "session_id", ""),
                    step_started_at=getattr(args, "step_started_at", ""),
                    outcome_spillover_path=getattr(args, "outcome_spillover_path", ""),
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
            payload = {"status": "recorded", "step_id": args.step_id, "agent": args.agent, "result": args.status}
            if is_team_member:
                payload["parent_step_id"] = _parent_step_id(args.step_id)
                payload["team_member"] = True
            print(json.dumps(payload))
        else:
            print(f"Recorded: step {args.step_id} ({args.agent}) — {args.status}")

    elif args.subcommand == "gate":
        # Wave 4.1 — opt-in CI gate via manual CLI.  When --type=ci is
        # supplied we drive the CI provider directly rather than recording
        # an externally-decided pass/fail.  Otherwise behave as before:
        # the orchestrator (or operator) has already determined the
        # result and is just recording it.
        gate_type_override = getattr(args, "gate_type_override", None)
        if gate_type_override == "ci":
            from agent_baton.core.gates.ci_gate import (
                CIGateRunner,
                parse_ci_gate_config,
            )
            workflow = (args.ci_workflow or "").strip() or "ci.yml"
            timeout_s = args.ci_timeout_s or 600
            try:
                import subprocess as _sp_ci
                sha = _sp_ci.run(
                    ["git", "rev-parse", "HEAD"],
                    capture_output=True, text=True, check=False, timeout=10,
                ).stdout.strip()
                branch = _sp_ci.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True, text=True, check=False, timeout=10,
                ).stdout.strip() or "HEAD"
            except (FileNotFoundError, Exception):
                sha, branch = "", "HEAD"
            print(f"Waiting for CI workflow {workflow} on {sha[:8] or '?'}...", file=sys.stderr)
            ci_result = CIGateRunner().wait_for_workflow(
                provider="github",
                workflow=workflow,
                branch=branch,
                commit_sha=sha,
                timeout_s=timeout_s,
            )
            passed = ci_result.passed
            output = (
                f"CI conclusion: {ci_result.conclusion}\n"
                f"Run URL: {ci_result.url}\n"
                f"Run ID: {ci_result.run_id}\n"
                f"Duration: {ci_result.duration_s:.1f}s"
                + (f"\n--- log excerpt ---\n{ci_result.log_excerpt}" if ci_result.log_excerpt else "")
            )
            engine.record_gate_result(
                phase_id=args.phase_id,
                passed=passed,
                output=output,
                command=f"gh workflow run {workflow}",
                exit_code=0 if passed else 1,
            )
            status = "PASS" if passed else "FAIL"
            if getattr(args, "output", "text") == "json":
                print(json.dumps({
                    "status": "recorded",
                    "phase_id": args.phase_id,
                    "result": "pass" if passed else "fail",
                    "ci": ci_result.to_dict(),
                }))
            else:
                marker = success(status) if passed else color_error(status)
                print(f"CI gate {marker}: {ci_result.conclusion} — {ci_result.url or '(no run url)'}")
        else:
            if args.result is None:
                user_error("--result is required when --type is not 'ci'.")
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
            # DX.3 (bd-d136): a gate FAIL is a natural pause point;
            # remind the operator to record a session handoff.
            if not passed:
                _maybe_handoff_nudge({"action_type": "gate_fail"}, task_id, context_root)

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
        # Guard: verify the referenced step is actually a team step.  This
        # catches the common mistake of calling team-record for a plain step
        # (e.g. after seeing "Step: 7.1" in DISPATCH output and reaching for
        # the wrong subcommand).  The check is best-effort: if the plan cannot
        # be loaded we fall through and let record_team_member_result raise.
        state = engine._load_execution()
        if state is not None:
            plan_step = engine._find_step(state, args.step_id)
            if plan_step is not None and not plan_step.team:
                user_error(
                    f"Step {args.step_id!r} is not a team step.",
                    hint=f"Use 'baton execute record --step {args.step_id} --agent {args.agent} ...' instead.",
                )
        files = [f.strip() for f in args.files.split(",") if f.strip()] if args.files else []
        try:
            engine.record_team_member_result(
                step_id=args.step_id,
                member_id=args.member_id,
                agent_name=args.agent,
                status=args.status,
                outcome=args.outcome,
                files_changed=files,
                outcome_spillover_path=getattr(args, "outcome_spillover_path", ""),
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        if getattr(args, "output", "text") == "json":
            print(json.dumps({"status": "recorded", "step_id": args.step_id, "member_id": args.member_id, "agent": args.agent, "result": args.status}))
        else:
            print(f"Team member recorded: {args.member_id} ({args.agent}) — {args.status}")

    elif args.subcommand == "interact":
        step_id = args.step_id
        if getattr(args, "interact_done", False):
            try:
                engine.complete_interaction(step_id=step_id)
            except (RuntimeError, ValueError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                sys.exit(1)
            if getattr(args, "output", "text") == "json":
                print(json.dumps({"status": "interaction_complete", "step_id": step_id}))
            else:
                print(f"Interaction completed: step {step_id}")
        else:
            input_text = getattr(args, "interact_input", None) or ""
            try:
                engine.provide_interact_input(step_id=step_id, input_text=input_text)
            except (RuntimeError, ValueError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                sys.exit(1)
            if getattr(args, "output", "text") == "json":
                print(json.dumps({"status": "input_recorded", "step_id": step_id}))
            else:
                print(f"Interaction input recorded: step {step_id}")

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

        # DX.3 (bd-d136): TTY nudge -- prompt operator to capture a
        # session handoff if they have not already done so.
        if getattr(args, "output", "text") != "json":
            _maybe_handoff_nudge({"action_type": "complete"}, task_id, context_root)

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
        # Wave 5.1 (bd-e208): if status is paused-takeover, route to
        # resume_from_takeover instead of the standard crash-recovery path.
        _resume_state = engine._load_execution()
        _resume_status = (_resume_state.status if _resume_state else None)
        if _resume_status == "paused-takeover":
            _resume_abort = getattr(args, "abort", False)
            _resume_rerun_gate = not getattr(args, "no_rerun_gate", False)
            # Find active takeover step_id.
            _takeover_records = getattr(_resume_state, "takeover_records", [])
            _active_tr = next(
                (r for r in reversed(_takeover_records) if not r.get("resumed_at")),
                None,
            )
            if _active_tr is None:
                user_error(
                    "No active takeover record found; cannot resume from takeover.",
                    hint="Check 'baton execute status' for the current state.",
                )
            _takeover_step_id = _active_tr.get("step_id", "")
            ok = engine.resume_from_takeover(
                _takeover_step_id,
                abort=_resume_abort,
                rerun_gate=_resume_rerun_gate,
            )
            if getattr(args, "output", "text") == "json":
                print(json.dumps({"resumed": ok, "step_id": _takeover_step_id}))
            else:
                if ok:
                    print(f"Takeover for step '{_takeover_step_id}' resolved. Execution resumed.")
                else:
                    print(
                        f"Takeover for step '{_takeover_step_id}' not yet resolved.\n"
                        "Make commits in the worktree and run 'baton execute resume' again."
                    )
        else:
            action = engine.resume()
            if getattr(args, "output", "text") == "json":
                result = {"action": action.to_dict()}
                print(json.dumps(result, indent=2))
            else:
                _print_action(action.to_dict())

    elif args.subcommand == "cancel":
        state = engine._load_execution()
        if state is None:
            user_error("no active execution found", hint="Nothing to cancel.")
        if state.status in ("complete", "failed", "cancelled"):
            user_error(
                f"execution {state.task_id} already {state.status}",
                hint="Only running executions can be cancelled.",
            )
        reason = getattr(args, "reason", "")
        state.status = "cancelled"
        state.completed_at = datetime.now(timezone.utc).isoformat()
        engine._save_execution(state)
        bus.publish(Event.create(
            topic="execution.cancelled",
            task_id=state.task_id,
            payload={
                "task_id": state.task_id,
                "reason": reason,
                "cancelled_at": state.completed_at,
            },
        ))
        if args.output == "json":
            print(json.dumps({
                "cancelled": True,
                "task_id": state.task_id,
                "reason": reason,
            }))
        else:
            print(f"Execution {state.task_id} cancelled.")
            if reason:
                print(f"  Reason: {reason}")

    elif args.subcommand == "retry-gate":
        try:
            engine.reset_gate_failed(phase_id=args.phase_id)
        except ValueError as exc:
            user_error(str(exc))
        if getattr(args, "output", "text") == "json":
            print(json.dumps({"status": "reset", "phase_id": args.phase_id}))
        else:
            print(f"Gate for phase {args.phase_id} reset to pending — run 'baton execute next' to retry.")

    elif args.subcommand == "fail":
        try:
            engine.fail_gate(phase_id=args.phase_id)
        except ValueError as exc:
            user_error(str(exc))
        if getattr(args, "output", "text") == "json":
            print(json.dumps({"status": "failed", "phase_id": args.phase_id}))
        else:
            print(f"Execution permanently failed at phase {args.phase_id} gate.")

    elif args.subcommand == "resume-budget":
        try:
            engine.resume_budget()
        except ValueError as exc:
            user_error(str(exc))
        if getattr(args, "output", "text") == "json":
            print(json.dumps({"status": "running", "message": "budget_exceeded cleared"}))
        else:
            print("Budget status cleared — execution resumed. Run 'baton execute next' to continue.")

    elif args.subcommand == "takeover":
        _handle_takeover(args, engine, context_root)

    elif args.subcommand == "self-heal":
        _handle_self_heal(args, engine, context_root)

    elif args.subcommand == "speculate":
        _handle_speculate(args, engine, context_root)

    elif args.subcommand == "verify-dispatch":
        _handle_verify_dispatch(args, engine, context_root)

    elif args.subcommand == "audit-isolation":
        _handle_audit_isolation(args, engine, context_root)


# ---------------------------------------------------------------------------
# Wave 5.1 — Developer Takeover (bd-e208)
# ---------------------------------------------------------------------------


def _handle_takeover(args: argparse.Namespace, engine, context_root: Path) -> None:
    """Handle ``baton execute takeover STEP_ID``.

    1. Resolve the editor/shell command.
    2. Call ``engine.start_takeover()`` to transition state.
    3. Print the takeover banner.
    4. Launch the editor/shell subprocess (blocking).
    5. On exit, prompt the developer to run ``baton execute resume``.
    """
    import subprocess as _sp
    from agent_baton.core.engine.takeover import (
        TakeoverError,
        TakeoverInvalidStateError,
        TakeoverSession,
        TakeoverWorktreeMissingError,
    )

    step_id = args.takeover_step_id
    use_shell = getattr(args, "takeover_shell", False)
    editor_override = getattr(args, "takeover_editor", "") or ""
    reason = getattr(args, "takeover_reason", "") or ""

    editor_cmd = TakeoverSession.resolve_editor_command(
        use_shell=use_shell,
        editor_override=editor_override,
    )

    # Resolve the worktree handle for the banner (before start_takeover mutates state).
    state = engine._load_execution()
    if state is None:
        user_error("no active execution found")

    wt_mgr = getattr(engine, "_worktree_mgr", None)
    session = TakeoverSession(worktree_mgr=wt_mgr, task_id=state.task_id)

    try:
        handle = session.resolve_handle(step_id)
    except TakeoverWorktreeMissingError as exc:
        user_error(str(exc))
        return  # unreachable; satisfies type checker

    # Start takeover — transitions state to paused-takeover.
    try:
        record = engine.start_takeover(
            step_id,
            reason=reason or "manual takeover",
            editor_or_shell=editor_cmd,
            pid=0,  # updated below after Popen
        )
    except TakeoverInvalidStateError as exc:
        user_error(str(exc))
        return
    except TakeoverError as exc:
        user_error(f"Takeover failed: {exc}")
        return

    if record is None:
        user_error(
            "Takeover is disabled (BATON_TAKEOVER_ENABLED=0).",
            hint="Set BATON_TAKEOVER_ENABLED=1 or enable takeover in baton.yaml.",
        )
        return

    TakeoverSession.print_banner(
        step_id=step_id,
        task_id=state.task_id,
        worktree_path=handle.path,
        branch=handle.branch,
        editor_cmd=editor_cmd,
    )

    print()
    print(f"Launching: {editor_cmd}")
    print(f"Working directory: {handle.path}")
    print()

    try:
        proc = TakeoverSession.launch_editor(editor_cmd, handle.path)
        # Update the record's pid now that we have it.
        _pid = proc.pid
        # Patch pid into the state record (best-effort).
        _state2 = engine._load_execution()
        if _state2 is not None:
            _records = list(getattr(_state2, "takeover_records", []))
            for _r in reversed(_records):
                if _r.get("step_id") == step_id and not _r.get("resumed_at"):
                    _r["pid"] = _pid
                    break
            _state2.takeover_records = _records
            engine._save_execution(_state2)

        proc.wait()
    except FileNotFoundError:
        user_error(
            f"Editor/shell not found: {editor_cmd!r}",
            hint="Set $EDITOR to a valid command, or use --shell to drop to $SHELL.",
        )
        return
    except KeyboardInterrupt:
        print("\nEditor interrupted.")

    print()
    print("Editor exited. When you are done with your changes:")
    print("  1. Commit them inside the worktree (git commit ...)")
    print(f"  2. Run: baton execute resume")
    print(f"  Or abort: baton execute resume --abort")


# ---------------------------------------------------------------------------
# Wave 5.2 — Manual Self-Heal Trigger (bd-1483)
# ---------------------------------------------------------------------------


def _handle_self_heal(args: argparse.Namespace, engine, context_root: Path) -> None:
    """Handle ``baton execute self-heal STEP_ID``.

    Manually triggers a self-heal escalation cycle for a failed step.
    Requires ``selfheal.enabled`` (or BATON_SELFHEAL_ENABLED=1).

    For v1 this surfaces the SelfHealEscalator status and the next tier
    that would be attempted.  Full automated dispatch fires in a future step.
    """
    import os as _os
    from agent_baton.core.engine.selfheal import EscalationTier, SelfHealEscalator

    step_id = args.selfheal_step_id
    max_tier_str = getattr(args, "selfheal_max_tier", "opus")

    _tier_map = {
        "haiku": EscalationTier.HAIKU_2,  # cap escalation at the last haiku tier
        "sonnet": EscalationTier.SONNET_2,
        "opus": EscalationTier.OPUS,
    }
    max_tier = _tier_map.get(max_tier_str, EscalationTier.OPUS)

    if _os.environ.get("BATON_SELFHEAL_ENABLED", "0") in ("0", "false", "False", "no"):
        user_error(
            "Self-heal is disabled. Set BATON_SELFHEAL_ENABLED=1 to enable.",
            hint="Or set selfheal.enabled: true in baton.yaml.",
        )
        return

    state = engine._load_execution()
    if state is None:
        user_error("no active execution found")

    wt_mgr = getattr(engine, "_worktree_mgr", None)
    if wt_mgr is None:
        user_error(
            "WorktreeManager is unavailable; self-heal requires worktree isolation.",
            hint="Ensure BATON_WORKTREE_ENABLED=1 and the project is a git repo.",
        )
        return

    handle = wt_mgr.handle_for(state.task_id, step_id)
    if handle is None:
        user_error(
            f"No retained worktree for step '{step_id}'. "
            "Self-heal requires a retained failed worktree.",
        )
        return

    phase_obj = state.current_phase_obj
    gate_cmd = (phase_obj.gate.command if (phase_obj and phase_obj.gate) else "") or ""

    escalator = SelfHealEscalator(
        step_id=step_id,
        gate_command=gate_cmd,
        worktree_path=handle.path,
        max_tier=max_tier,
    )

    next_tier = escalator.next_tier()
    if next_tier is None:
        print(f"Self-heal ladder exhausted for step '{step_id}' — no more tiers to try.")
        return

    agent_name = SelfHealEscalator.TIER_AGENTS[next_tier]
    model = SelfHealEscalator.TIER_MODELS[next_tier]
    input_cap = SelfHealEscalator.INPUT_CAPS[next_tier]

    print(f"Self-heal next tier: {next_tier.value}")
    print(f"  Agent:      {agent_name}")
    print(f"  Model:      {model}")
    print(f"  Input cap:  {input_cap:,} tokens")
    print(f"  Worktree:   {handle.path}")
    print()
    print("To dispatch manually:")
    print(f"  baton execute dispatch --step {step_id} --agent {agent_name} --model {model}")
    print()
    print("(Automated dispatch will fire on next gate failure when selfheal is enabled.)")


# ---------------------------------------------------------------------------
# Wave 5.3 — Speculation Management (bd-9839)
# ---------------------------------------------------------------------------


def _handle_speculate(args: argparse.Namespace, engine, context_root: Path) -> None:
    """Handle ``baton execute speculate status|accept|reject|show [SPEC_ID]``."""
    action = args.speculate_action
    spec_id = getattr(args, "speculate_id", None)
    reason = getattr(args, "speculate_reason", "") or ""
    output_fmt = getattr(args, "output", "text")

    speculator = engine.get_speculator()
    if speculator is None:
        if output_fmt == "json":
            print(json.dumps({"enabled": False, "message": "Speculation is disabled."}))
        else:
            user_error(
                "Speculation is disabled. Set BATON_SPECULATE_ENABLED=1 to enable.",
                hint="Or set speculate.enabled: true in baton.yaml.",
            )
        return

    state = engine._load_execution()
    if state is not None:
        speculator.load_from_state(getattr(state, "speculations", {}))

    if action == "status":
        active = speculator.list_active()
        if output_fmt == "json":
            print(json.dumps({"speculations": [s.to_dict() for s in active]}, indent=2))
        else:
            if not active:
                print("No active speculations.")
            else:
                print(f"Active speculations ({len(active)}):")
                for s in active:
                    print(f"  {s.spec_id[:8]}  target={s.target_step_id}  "
                          f"trigger={s.trigger}  status={s.status}")

    elif action == "accept":
        if not spec_id:
            user_error("'accept' requires a SPEC_ID argument")
        spec = speculator.accept(spec_id)
        if spec is None:
            user_error(f"Speculation '{spec_id}' not found.")
        # Persist updated speculation state.
        if state is not None:
            state.speculations = speculator.to_dict()
            engine._save_execution(state)
        if output_fmt == "json":
            print(json.dumps({"accepted": True, "spec_id": spec_id}))
        else:
            print(f"Speculation {spec_id} accepted.")
            print("Dispatch the heavy-model agent into the speculative worktree to complete the step.")

    elif action == "reject":
        if not spec_id:
            user_error("'reject' requires a SPEC_ID argument")
        spec = speculator.reject(spec_id, reason=reason)
        if spec is None:
            user_error(f"Speculation '{spec_id}' not found.")
        if state is not None:
            state.speculations = speculator.to_dict()
            engine._save_execution(state)
        if output_fmt == "json":
            print(json.dumps({"rejected": True, "spec_id": spec_id, "reason": reason}))
        else:
            print(f"Speculation {spec_id} rejected. Worktree cleaned up.")

    elif action == "show":
        if not spec_id:
            user_error("'show' requires a SPEC_ID argument")
        spec = speculator.get(spec_id)
        if spec is None:
            user_error(f"Speculation '{spec_id}' not found.")
        if output_fmt == "json":
            print(json.dumps(spec.to_dict(), indent=2))
        else:
            d = spec.to_dict()
            for k, v in d.items():
                print(f"  {k}: {v}")


# ---------------------------------------------------------------------------
# Dispatch verification (bd-edbf) — read-only post-hoc audit
# ---------------------------------------------------------------------------


def _project_root_for_audit(context_root: Path) -> Path:
    """Resolve the git project root for verifier git operations.

    ``context_root`` is the ``.claude/team-context/`` directory; the audit
    runs against the repo containing it.  We walk two levels up which
    always lands on the project root for the standard layout.
    """
    return context_root.parent.parent


def _format_verify_text(result) -> list[str]:
    """Render a single ``VerificationResult`` as text lines."""
    lines: list[str] = []
    if result.inconclusive:
        lines.append(f"INCONCLUSIVE  step {result.step_id}: no files_changed and no commit_hash recorded")
        return lines
    if result.passed:
        lines.append(f"PASS  step {result.step_id}")
        return lines
    lines.append(f"FAIL  step {result.step_id}")
    for v in result.violations:
        lines.append(f"  - {v}")
    return lines


def _handle_verify_dispatch(args: argparse.Namespace, engine, context_root: Path) -> None:
    """Handle ``baton execute verify-dispatch <step_id>``.

    Read-only: never writes to state, plan, git, or any artifact.
    """
    from agent_baton.core.audit.dispatch_verifier import DispatchVerifier

    state = engine._load_execution()
    if state is None:
        user_error(
            "no active execution found",
            hint="Run 'baton execute list' to find an execution, then "
                 "'baton execute verify-dispatch <step_id> --task-id <id>'.",
        )

    step_id = args.verify_step_id
    step = None
    for phase in state.plan.phases:
        for s in phase.steps:
            if s.step_id == step_id:
                step = s
                break
        if step is not None:
            break
    if step is None:
        user_error(
            f"step '{step_id}' not found in plan",
            hint="Use 'baton execute status' to list known step IDs.",
        )

    step_result = state.get_step_result(step_id)
    if step_result is None:
        user_error(
            f"step '{step_id}' has no recorded result yet",
            hint="The step has not been dispatched and recorded — nothing to verify.",
        )

    project_root = _project_root_for_audit(context_root)
    result = DispatchVerifier().verify_step(step, step_result, project_root)

    if getattr(args, "output", "text") == "json":
        print(json.dumps(result.to_dict(), indent=2))
    else:
        for line in _format_verify_text(result):
            print(line)

    # Exit non-zero on a definite violation; inconclusive is exit 0.
    if not result.passed:
        sys.exit(1)


def _handle_audit_isolation(args: argparse.Namespace, engine, context_root: Path) -> None:
    """Handle ``baton execute audit-isolation``.

    Read-only: aggregates per-step verifications across the whole task.
    Exit non-zero on any violation; exit 0 when all steps pass or are
    inconclusive.
    """
    from agent_baton.core.audit.dispatch_verifier import DispatchVerifier

    state = engine._load_execution()
    if state is None:
        user_error(
            "no active execution found",
            hint="Run 'baton execute list' to find an execution, then "
                 "'baton execute audit-isolation --task-id <id>'.",
        )

    project_root = _project_root_for_audit(context_root)
    report = DispatchVerifier().audit_task(state, project_root)

    if getattr(args, "output", "text") == "json":
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"Isolation audit — task {report.task_id}")
        print(f"  Steps inspected: {report.total_steps}")
        print(f"  Compliant:       {report.compliant_count}")
        print(f"  Violations:      {report.violation_count}")
        if report.results:
            print()
            for r in report.results:
                for line in _format_verify_text(r):
                    print(f"  {line}")

    if report.has_violations:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Policy engine construction
# ---------------------------------------------------------------------------

def _build_policy_engine():
    """Construct a PolicyEngine for runtime pre-dispatch enforcement.

    Loads the project-local policy presets directory (same as plan time).
    Returns None (gracefully) if the import fails, so callers in file-only
    installations degrade gracefully without ImportError.
    """
    try:
        from agent_baton.core.govern.policy import PolicyEngine
        return PolicyEngine()
    except Exception as exc:
        _log.debug(
            "PolicyEngine construction failed (non-fatal): %s", exc
        )
        return None


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
        from agent_baton.core.engine.knowledge_telemetry import KnowledgeTelemetryStore
        from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry

        registry = KnowledgeRegistry()
        registry.load_default_paths()

        # Wire F0.4 lifecycle telemetry (bd-a313).  Defaults to ~/.baton/central.db.
        # KnowledgeTelemetryStore manages its own connection; failures inside the
        # resolver are swallowed so dispatch is never blocked.
        try:
            telemetry = KnowledgeTelemetryStore()
        except Exception as tel_exc:
            _log.debug(
                "KnowledgeTelemetryStore construction failed (non-fatal): %s",
                tel_exc,
            )
            telemetry = None

        return KnowledgeResolver(registry, telemetry=telemetry)
    except Exception as exc:
        _log.debug(
            "KnowledgeResolver construction failed (non-fatal): %s", exc
        )
        return None


# ---------------------------------------------------------------------------
# Autonomous execution loop
# ---------------------------------------------------------------------------

def _handle_dry_run(args: argparse.Namespace) -> None:
    """Drive a plan end-to-end with mock launchers and write a report.

    Convenience entrypoint for the DX.5 dry-run testing harness.  Loads the
    saved plan, starts a fresh execution in a tmp_path-style flow, walks
    every action the engine emits using
    :class:`agent_baton.core.engine.dry_run_launcher.TracingDryRunLauncher`
    (no Claude API calls) and
    :class:`agent_baton.core.engine.gates.DryRunGateRunner` (always-pass),
    and writes ``dry-run-report.md`` to the team-context directory.

    Approval and INTERACT actions are auto-progressed so the loop reaches
    COMPLETE without user prompts.
    """
    plan_path = Path(args.plan)
    context_root = _resolve_context_root()
    max_steps = getattr(args, "max_steps", 50)

    # ── Load plan ────────────────────────────────────────────────────────
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

    # ── Banner: visible at the very top of every dry-run ────────────────
    print(_DRY_RUN_BANNER)

    # ── Build engine + dry-run launcher/gate runner ──────────────────────
    bus = EventBus()
    storage = get_project_storage(context_root)
    task_id = plan.task_id

    engine = ExecutionEngine(
        team_context_root=context_root,
        bus=bus,
        task_id=task_id,
        storage=storage,
    )
    ContextManager(task_id=task_id).init_mission_log(
        plan.task_summary, risk_level=plan.risk_level
    )
    action = engine.start(plan)

    # Imports are local so file-only installs don't pay the cost on every
    # ``baton execute`` invocation.
    from agent_baton.core.engine.dry_run_launcher import (
        TracingDryRunLauncher,
    )
    from agent_baton.core.engine.gates import DryRunGateRunner

    launcher = TracingDryRunLauncher()
    gate_runner = DryRunGateRunner()

    # ── Drive the loop ───────────────────────────────────────────────────
    import asyncio as _asyncio

    steps_executed = 0
    action_dict = action.to_dict()

    while True:
        atype = action_dict.get("action_type", "")

        if atype == ActionType.COMPLETE.value:
            engine.complete()
            break

        if atype == ActionType.FAILED.value:
            print(
                color_error("FAILED")
                + f": {action_dict.get('summary', action_dict.get('message', ''))}",
                file=sys.stderr,
            )
            break

        if steps_executed >= max_steps:
            print(
                warning("ABORTED")
                + f": reached max-steps limit ({max_steps})",
                file=sys.stderr,
            )
            break

        if atype == ActionType.DISPATCH.value:
            step_id = action_dict.get("step_id", "")
            agent_name = action_dict.get("agent_name", "")
            agent_model = action_dict.get("agent_model", "sonnet")
            prompt = action_dict.get("delegation_prompt", "")
            print(
                f"  [{step_id}] (dry-run) would dispatch {agent_name} "
                f"(model={agent_model}, prompt={len(prompt)} chars)"
            )
            engine.mark_dispatched(step_id=step_id, agent_name=agent_name)
            result = _asyncio.run(
                launcher.launch(
                    agent_name=agent_name,
                    model=agent_model,
                    prompt=prompt,
                    step_id=step_id,
                )
            )
            engine.record_step_result(
                step_id=step_id,
                agent_name=agent_name,
                status=result.status,
                outcome=result.outcome or "dry-run complete",
                files_changed=list(result.files_changed),
                duration_seconds=_DRY_RUN_DISPATCH_SECONDS,
            )
            steps_executed += 1

        elif atype == ActionType.GATE.value:
            phase_id = action_dict.get("phase_id", 0)
            gate_type = action_dict.get("gate_type", "")
            gate_cmd = action_dict.get("gate_command", "")
            # Synthesize a PlanGate so DryRunGateRunner can record it.
            from agent_baton.models.execution import PlanGate as _PlanGate
            synthetic_gate = _PlanGate(
                gate_type=gate_type or "test",
                command=gate_cmd,
            )
            gate_runner.evaluate_output(
                synthetic_gate, "", 0, phase_id=phase_id
            )
            print(
                f"  [GATE] (dry-run) phase {phase_id} ({gate_type}): "
                f"would run {gate_cmd or '(no command)'}"
            )
            engine.record_gate_result(
                phase_id=phase_id,
                passed=True,
                output=f"[dry-run] {gate_cmd}",
            )

        elif atype == ActionType.APPROVAL.value:
            phase_id = action_dict.get("phase_id", 0)
            print(
                f"  [APPROVAL] (dry-run) phase {phase_id}: auto-approving"
            )
            engine.record_approval_result(phase_id=phase_id, result="approve")

        elif atype == ActionType.INTERACT.value:
            step_id = action_dict.get("step_id", "")
            print(
                f"  [INTERACT] (dry-run) step {step_id}: "
                "auto-completing interaction"
            )
            try:
                engine.complete_interaction(step_id=step_id)
            except (RuntimeError, ValueError) as exc:
                print(f"    skipped (engine refused): {exc}", file=sys.stderr)
                break

        else:
            # Unknown action type — bail to avoid an infinite loop.
            print(
                f"  [unknown action_type={atype!r}] aborting dry-run",
                file=sys.stderr,
            )
            break

        try:
            action_dict = engine.next_action().to_dict()
        except RuntimeError as exc:
            print(
                color_error("ERROR") + f": {exc}",
                file=sys.stderr,
            )
            break

    # ── Write report ─────────────────────────────────────────────────────
    report_path = context_root / _DRY_RUN_REPORT_FILENAME
    report_text = _build_dry_run_report(
        plan=plan,
        launcher=launcher,
        gate_runner=gate_runner,
        steps_executed=steps_executed,
    )
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_text, encoding="utf-8")
        print(f"\nDry-run report written to: {report_path}")
    except OSError as exc:
        print(
            warning("WARN")
            + f": could not write dry-run report to {report_path}: {exc}",
            file=sys.stderr,
        )

    # Always end with a clear summary banner.
    print(success("COMPLETE") + ": dry-run finished without API calls.")


def _build_dry_run_report(
    *,
    plan: MachinePlan,
    launcher,  # TracingDryRunLauncher; typed as Any to avoid forward-ref churn
    gate_runner,  # DryRunGateRunner
    steps_executed: int,
) -> str:
    """Render the dry-run summary as Markdown.

    Surfaces:
    - Total steps executed, total dispatches, total gates run.
    - Predicted token cost (sum of TracingDryRunLauncher per-step estimates;
      falls back to "N/A" when no launches recorded).
    - Total wall-clock estimate using the placeholder per-action constants.
    - Per-step table with step_id, agent, model, status.
    """
    total_dispatches = len(launcher.launches)
    total_gates = len(gate_runner.gates_run)
    total_tokens = sum(
        int(entry.get("estimated_tokens", 0)) for entry in launcher.launches
    )
    token_display = str(total_tokens) if total_dispatches else "N/A"
    wall_clock = (
        total_dispatches * _DRY_RUN_DISPATCH_SECONDS
        + total_gates * _DRY_RUN_GATE_SECONDS
    )

    lines: list[str] = []
    lines.append(f"# Dry-Run Report — {plan.task_id}")
    lines.append("")
    lines.append(f"**Task summary:** {plan.task_summary}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Total steps executed: {steps_executed}")
    lines.append(f"- Total dispatches: {total_dispatches}")
    lines.append(f"- Total gates run: {total_gates}")
    lines.append(f"- Predicted token cost: {token_display}")
    lines.append(
        f"- Total wall-clock estimate: {wall_clock:.1f}s "
        f"(placeholder: {_DRY_RUN_DISPATCH_SECONDS:.0f}s/dispatch + "
        f"{_DRY_RUN_GATE_SECONDS:.0f}s/gate)"
    )
    lines.append("")
    lines.append("## Steps")
    lines.append("")
    lines.append("| step_id | agent | model | status |")
    lines.append("|---------|-------|-------|--------|")
    # Build a step_id → model lookup from the plan so we can show the
    # configured model alongside what the launcher actually saw.
    plan_models: dict[str, str] = {}
    for phase in plan.phases:
        for step in phase.steps:
            model = getattr(step, "model", "") or "sonnet"
            plan_models[step.step_id] = model

    for entry in launcher.launches:
        sid = entry.get("step_id", "?")
        agent = entry.get("agent_name", "?")
        model = entry.get("model") or plan_models.get(sid, "?")
        # Status column is always "complete" in dry-run unless the caller
        # pre-configured a per-step override on the launcher.
        result = launcher._results.get(sid)
        status = result.status if result is not None else "complete"
        lines.append(f"| {sid} | {agent} | {model} | {status} |")

    if total_gates:
        lines.append("")
        lines.append("## Gates")
        lines.append("")
        lines.append("| phase_id | gate_type | command |")
        lines.append("|----------|-----------|---------|")
        for entry in gate_runner.gates_run:
            phase = entry.get("phase_id", "?")
            gtype = entry.get("gate_type", "?")
            cmd = entry.get("command", "") or "(none)"
            lines.append(f"| {phase} | {gtype} | `{cmd}` |")

    lines.append("")
    return "\n".join(lines)


def _print_dry_run_preview(plan: "MachinePlan", max_steps: int) -> None:  # noqa: F821
    """Print a read-only preview of what ``baton execute run`` would do.

    Walks the plan's phases/steps/gates/approvals in order and prints the
    same ``[DRY RUN]`` lines that the live loop emits, but touches no engine
    state whatsoever.  Called by ``_handle_run`` when ``--dry-run`` is set,
    immediately after the plan is resolved, before any ``engine.start()`` or
    ``engine.next_action()`` call.

    Exits via ``sys.exit(1)`` if ``max_steps`` is exhausted, matching the
    behaviour of the live loop when the ``--max-steps`` limit is reached.
    """
    print(_DRY_RUN_BANNER)
    print(f"Task: {plan.task_id}", file=sys.stderr)
    steps_shown = 0
    for phase in plan.phases:
        for step in phase.steps:
            if steps_shown >= max_steps:
                print(
                    f"\n{warning('ABORTED')}: reached max-steps limit ({max_steps})",
                    file=sys.stderr,
                )
                sys.exit(1)
            step_id = step.step_id
            agent_name = step.agent_name
            step_type = getattr(step, "step_type", "") or ""
            if step_type == "automation":
                command = getattr(step, "command", "") or ""
                print(
                    f"\n  [{step_id}] Running automation: {command[:80]}...",
                    file=sys.stderr,
                )
                print(f"  [DRY RUN] Would run: {command}", file=sys.stderr)
            else:
                model = getattr(step, "model", "sonnet") or "sonnet"
                print(
                    f"\n  [{step_id}] Dispatching {agent_name} (model={model})...",
                    file=sys.stderr,
                )
                print(
                    f"  [DRY RUN] Would launch {agent_name} with prompt",
                    file=sys.stderr,
                )
            steps_shown += 1

        if phase.gate:
            gate_cmd = getattr(phase.gate, "command", "") or ""
            gate_type = getattr(phase.gate, "gate_type", "") or ""
            print(
                f"\n  [GATE] Phase {phase.phase_id} ({gate_type}): {gate_cmd}",
                file=sys.stderr,
            )
            print(f"  [DRY RUN] Would run: {gate_cmd}", file=sys.stderr)

        if phase.approval_required:
            print(
                f"\n  [APPROVAL REQUIRED] Phase {phase.phase_id}",
                file=sys.stderr,
            )
            print("  [DRY RUN] Auto-approving", file=sys.stderr)

    print(
        f"\n{success('COMPLETE')}: dry-run preview finished — execution state unchanged"
    )


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
    context_root = _resolve_context_root()
    max_steps = args.max_steps
    dry_run = args.dry_run
    model_override = args.model
    token_budget: int = getattr(args, "token_budget", 0)

    # F0.3 — VETO override (bd-f606): validate --force / --justification
    # before constructing any engine.
    force_override = bool(getattr(args, "force_override", False))
    override_justification = (getattr(args, "override_justification", "") or "").strip()
    if force_override and not override_justification:
        validation_error(
            "--force requires --justification \"<reason>\"",
            hint="Re-run with --force --justification \"why this VETO is being overridden\"",
        )

    # G1.6 (bd-1a09): record the override invocation in the
    # governance_overrides SQL table + compliance chain.
    if force_override:
        try:
            from agent_baton.cli._override_helper import record_override

            record_override(
                flag="--force",
                justification=override_justification,
                command="baton execute run",
            )
        except Exception as _ovr_exc:  # pragma: no cover - best-effort logging
            print(
                f"warning: failed to record override audit row: {_ovr_exc}",
                file=sys.stderr,
            )

    # Resolve or create the execution.
    #
    # Task-id resolution priority (matches general handler at lines ~529-548):
    #   1. --task-id flag
    #   2. BATON_TASK_ID env var
    #   3. SQLite active task pointer
    #   4. file-based active-task-id.txt marker
    #
    # Without steps 3-4, `baton execute run` invoked with no flag/env after
    # an approval would silently start fresh, wiping prior step_results /
    # approval_results / gate_results and re-dispatching completed agents
    # (bd-7444).  Treat ANY non-terminal status as "resume", not just
    # "running"/"pending" — approval_pending, gate_pending, feedback_pending,
    # gate_failed, and budget_exceeded all warrant resume.
    bus = EventBus()
    storage = get_project_storage(context_root)
    task_id = getattr(args, "task_id", None)

    if task_id is None:
        task_id = os.environ.get("BATON_TASK_ID")

    if task_id is None:
        # SQLite-first: read active task from baton.db before falling back to
        # the file-based active-task-id.txt marker.
        try:
            _backend = detect_backend(context_root)
        except Exception:  # noqa: BLE001 — defensive: backend detection is best-effort
            _backend = "file"
        if _backend == "sqlite":
            try:
                _early_storage = get_project_storage(context_root, backend="sqlite")
                task_id = _early_storage.get_active_task()
            except Exception as _exc:  # noqa: BLE001
                _log.debug(
                    "SQLite active-task lookup failed in execute run, "
                    "falling back to file: %s",
                    _exc,
                )
        if task_id is None:
            task_id = StatePersistence.get_active_task_id(context_root)

    # Statuses that mean "this execution is in progress; do NOT start fresh".
    # Only "complete", "failed", and "cancelled" are terminal — everything
    # else is resumable, never a reason to overwrite recorded results.
    _RESUMABLE_STATUSES = frozenset({
        "running", "pending",
        "approval_pending", "feedback_pending",
        "gate_pending", "gate_failed",
        "budget_exceeded",
    })
    _TERMINAL_STATUSES = frozenset({"complete", "failed", "cancelled"})

    engine: ExecutionEngine | None = None
    if task_id:
        engine = ExecutionEngine(
            team_context_root=context_root,
            bus=bus, task_id=task_id, storage=storage,
            token_budget=token_budget or None,
            force_override=force_override,
            override_justification=override_justification,
        )
        st = engine.status()
        st_status = (st or {}).get("status")
        if st_status in _RESUMABLE_STATUSES:
            print(
                f"Resuming execution: {task_id} (status={st_status})",
                file=sys.stderr,
            )
        elif st_status in _TERMINAL_STATUSES:
            # Already finished — surface clearly rather than silently
            # restarting and overwriting the prior result.
            user_error(
                f"execution {task_id} already {st_status}; not restarting.",
                hint=(
                    "Use 'baton execute list' to see executions, "
                    "'baton execute switch TASK_ID' to switch tasks, "
                    "or run 'baton plan --save' to create a new plan."
                ),
            )
        else:
            # status == "no_active_execution" or unrecognised — treat as
            # missing and fall through to the start-from-plan branch, but
            # forget the stale task_id so we use the plan's task_id below.
            engine = None
            task_id = None

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

        # Defence in depth: even if no active marker pointed at this task,
        # an execution row with the SAME task_id may already exist on disk
        # (e.g. a previous `baton execute start` then a stale active marker).
        # Probe one more time before calling engine.start(), which would
        # unconditionally overwrite ExecutionState (bd-7444).
        _probe_engine = ExecutionEngine(
            team_context_root=context_root,
            bus=bus, task_id=task_id, storage=storage,
            token_budget=token_budget or None,
            force_override=force_override,
            override_justification=override_justification,
        )
        _probe_status = _probe_engine.status()
        _probe_st = (_probe_status or {}).get("status")
        if _probe_st in _RESUMABLE_STATUSES:
            print(
                f"Resuming execution: {task_id} (status={_probe_st}; "
                "matched via plan task_id)",
                file=sys.stderr,
            )
            engine = _probe_engine
            # bd-f4de: do NOT short-circuit to _print_dry_run_preview here.
            # That function walks the plan blindly without checking existing
            # step/gate state, so it re-emits previews for already-completed
            # steps and already-passed gates.  Fall through to _run_loop
            # instead — next_action() is state-aware and will skip terminal
            # steps/gates naturally.
            action = engine.next_action()
        elif _probe_st in _TERMINAL_STATUSES:
            user_error(
                f"execution {task_id} (from plan) already {_probe_st}; "
                "refusing to overwrite.",
                hint=(
                    "Use 'baton plan --save' with a new task description to "
                    "create a fresh task, or 'baton execute list' to inspect."
                ),
            )
        else:
            knowledge_resolver = _build_knowledge_resolver(plan)
            policy_engine = _build_policy_engine()
            engine = ExecutionEngine(
                team_context_root=context_root,
                bus=bus, task_id=task_id, storage=storage,
                knowledge_resolver=knowledge_resolver,
                policy_engine=policy_engine,
                token_budget=token_budget or None,
                force_override=force_override,
                override_justification=override_justification,
            )
            if dry_run:
                _print_dry_run_preview(plan, max_steps)
                return
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
        # bd-f4de: do NOT short-circuit to _print_dry_run_preview here.
        # The active-marker resume path must also use _run_loop so that
        # next_action() skips already-completed steps and already-passed
        # gates instead of blindly re-previewing them.
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

    # bd-f4de: resume paths no longer call _print_dry_run_preview (which
    # would blindly re-preview terminal-state steps/gates), so print the
    # dry-run banner here before entering the state-aware _run_loop.
    if dry_run:
        print(_DRY_RUN_BANNER)

    steps_executed = 0
    action_dict = action.to_dict()

    try:
        _run_loop(
            engine=engine,
            launcher=launcher,
            action_dict=action_dict,
            max_steps=max_steps,
            dry_run=dry_run,
            model_override=model_override,
            task_id=task_id,
            steps_executed=steps_executed,
        )
    finally:
        # Issue 3: clean up any launcher subprocesses that are still alive
        # if the loop exits via CancelledError, KeyboardInterrupt, or sys.exit.
        _cleanup = getattr(launcher, "cleanup", None)
        if _cleanup is not None:
            import asyncio as _asyncio
            try:
                _asyncio.run(_cleanup())
            except Exception:
                pass


def _run_loop(
    *,
    engine: ExecutionEngine,
    launcher: object,
    action_dict: dict,
    max_steps: int,
    dry_run: bool,
    model_override: str,
    task_id: str | None,
    steps_executed: int = 0,
) -> None:
    """Inner execution loop extracted so _handle_run can wrap it in try/finally."""
    import subprocess as _subprocess

    # bd-92bc: In dry-run mode next_action() always returns the same pending
    # step (because we must not persist state).  Track previewed step/gate ids
    # locally so we can detect the repeat and break cleanly.
    _dry_run_previewed_steps: set[str] = set()
    _dry_run_previewed_gates: set[str] = set()

    while True:
        atype = action_dict.get("action_type", "")

        if atype == ActionType.COMPLETE.value:
            if dry_run:
                print(f"\n{success('COMPLETE')}: dry-run preview finished — execution state unchanged")
                return
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
            step_id = action_dict.get("step_id", "")
            step_type = action_dict.get("step_type", "")
            msg = action_dict.get("message", "")

            if step_type == "automation":
                # ── Automation: run shell command directly, no LLM ───────
                command = action_dict.get("command", "")
                print(f"\n  [{step_id}] Running automation: {command[:80]}...", file=sys.stderr)
                if not dry_run:
                    engine.mark_dispatched(step_id=step_id, agent_name="automation")

                if dry_run:
                    # bd-ae75: guard against infinite loop — automation steps
                    # also need deduplication in dry-run mode, same as the LLM
                    # path.  Without this, next_action() returns the same
                    # DISPATCH forever and the loop hits max_steps + exit 1.
                    if step_id in _dry_run_previewed_steps:
                        print(
                            f"\n{success('COMPLETE')}: dry-run preview finished"
                            " — execution state unchanged",
                        )
                        return
                    _dry_run_previewed_steps.add(step_id)
                    print(f"  [DRY RUN] Would run: {command}", file=sys.stderr)
                else:
                    import subprocess as _subprocess
                    try:
                        proc = _subprocess.run(
                            command, shell=True, capture_output=True,
                            text=True, timeout=300,
                        )
                        succeeded = proc.returncode == 0
                        engine.record_step_result(
                            step_id=step_id,
                            agent_name="automation",
                            status="complete" if succeeded else "failed",
                            outcome=proc.stdout,
                            error=proc.stderr if not succeeded else "",
                        )
                        status_marker = success("done") if succeeded else color_error("FAIL")
                        print(f"  [{step_id}] {status_marker}", file=sys.stderr)
                        if not succeeded:
                            print(f"    Error: {proc.stderr[:200]}", file=sys.stderr)
                    except _subprocess.TimeoutExpired:
                        engine.record_step_result(
                            step_id=step_id, agent_name="automation",
                            status="failed",
                            error=f"Automation command timed out after 300s: {command}",
                        )
                        print(f"  [{step_id}] {color_error('TIMEOUT')}", file=sys.stderr)
            else:
                # ── Agent dispatch: existing LLM path ────────────────────
                agent_name = action_dict.get("agent_name", "")
                agent_model = action_dict.get("agent_model", model_override)
                prompt = action_dict.get("delegation_prompt", "")

                print(f"\n  [{step_id}] Dispatching {agent_name} (model={agent_model})...", file=sys.stderr)
                if msg:
                    print(f"    {msg[:120]}", file=sys.stderr)

                if not dry_run:
                    engine.mark_dispatched(step_id=step_id, agent_name=agent_name)

                # Wave 1.3 (bd-86bf): after mark_dispatched, the engine may have
                # created a worktree and stored it in state.step_worktrees.
                # Read the path back from the action_dict (already present if
                # next_action() populated it) or from engine state as fallback.
                _wt_path: str | None = action_dict.get("worktree_path") or None
                if _wt_path is None:
                    try:
                        _wt_state = engine._load_execution()
                        _wt_dict = (
                            getattr(_wt_state, "step_worktrees", {}).get(step_id)
                            if _wt_state else None
                        )
                        if _wt_dict:
                            _wt_path = _wt_dict.get("path") or None
                    except Exception:
                        pass

                if dry_run:
                    # bd-92bc: guard against infinite loop — next_action()
                    # returns the same DISPATCH repeatedly in dry-run mode
                    # because we must not persist state.  If this step_id was
                    # already previewed this pass, we've exhausted pending work.
                    if step_id in _dry_run_previewed_steps:
                        print(
                            f"\n{success('COMPLETE')}: dry-run preview finished"
                            " — execution state unchanged",
                        )
                        return
                    _dry_run_previewed_steps.add(step_id)
                    print(f"  [DRY RUN] Would launch {agent_name} with {len(prompt)} char prompt", file=sys.stderr)
                    if _wt_path:
                        print(f"  [DRY RUN] Worktree: {_wt_path}", file=sys.stderr)
                    # Do NOT call engine.record_step_result() — dry-run is read-only.
                else:
                    import asyncio as _asyncio
                    assert launcher is not None  # guarded by user_error above
                    result = _asyncio.run(launcher.launch(
                        agent_name=agent_name,
                        model=agent_model,
                        prompt=prompt,
                        step_id=step_id,
                        cwd_override=_wt_path,
                        task_id=getattr(engine, "_task_id", "") or "",
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
                # bd-92bc: same loop-guard as DISPATCH — gates also loop
                # infinitely without state persistence.
                # bd-145f: include gate_cmd in the key so two phases with the
                # same phase_id + gate_type but different commands don't collide.
                _gate_key = f"{phase_id}:{gate_type}:{gate_cmd}"
                if _gate_key in _dry_run_previewed_gates:
                    print(
                        f"\n{success('COMPLETE')}: dry-run preview finished"
                        " — execution state unchanged",
                    )
                    return
                _dry_run_previewed_gates.add(_gate_key)
                print(f"  [DRY RUN] Would run: {gate_cmd}", file=sys.stderr)
            elif gate_type == "ci":
                # Wave 4.1 — CI provider gate.  Polls GitHub Actions for the
                # current branch's HEAD commit (rather than dispatching a new
                # workflow run, which is the model used by the older
                # ``run_github_actions_gate`` path).  CIGateRunner returns a
                # CIGateResult whose ``passed`` field is the gate verdict.
                from agent_baton.core.gates.ci_gate import (
                    CIGateRunner,
                    parse_ci_gate_config,
                )
                import subprocess as _sp_ci

                config = parse_ci_gate_config(gate_cmd)
                workflow_name = config.workflow or "ci.yml"
                try:
                    sha = _sp_ci.run(
                        ["git", "rev-parse", "HEAD"],
                        capture_output=True, text=True, check=False, timeout=10,
                    ).stdout.strip()
                except (FileNotFoundError, _sp_ci.TimeoutExpired):
                    sha = ""
                branch = config.branch
                if not branch or branch == "auto":
                    try:
                        branch = _sp_ci.run(
                            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                            capture_output=True, text=True, check=False, timeout=10,
                        ).stdout.strip() or "HEAD"
                    except (FileNotFoundError, _sp_ci.TimeoutExpired):
                        branch = "HEAD"
                print(
                    f"  [GATE] Waiting for CI workflow {workflow_name} on "
                    f"{(sha[:8] or '?')} (branch {branch})...",
                    file=sys.stderr,
                )
                ci_result = CIGateRunner(poll_interval_s=config.poll_interval_s).wait_for_workflow(
                    provider=config.provider,
                    workflow=workflow_name,
                    branch=branch,
                    commit_sha=sha,
                    timeout_s=config.timeout_s,
                )
                output = (
                    f"CI conclusion: {ci_result.conclusion}\n"
                    f"Run URL: {ci_result.url}\n"
                    f"Run ID: {ci_result.run_id}\n"
                    f"Duration: {ci_result.duration_s:.1f}s"
                    + (f"\n--- log excerpt ---\n{ci_result.log_excerpt}"
                       if ci_result.log_excerpt else "")
                )
                engine.record_gate_result(
                    phase_id=phase_id,
                    passed=ci_result.passed,
                    output=output,
                    command=f"gh run watch (workflow={workflow_name})",
                    exit_code=0 if ci_result.passed else 1,
                )
                marker = success("PASS") if ci_result.passed else color_error("FAIL")
                print(
                    f"  [GATE] {marker} ({ci_result.conclusion}) — "
                    f"{ci_result.url or '(no run url)'}",
                    file=sys.stderr,
                )
                if not ci_result.passed and ci_result.log_excerpt:
                    print(f"    Log: {ci_result.log_excerpt[:200]}", file=sys.stderr)
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
                        phase_id=phase_id,
                        passed=passed,
                        output=output,
                        command=gate_cmd,
                        exit_code=proc.returncode,
                    )
                    marker = success("PASS") if passed else color_error("FAIL")
                    print(f"  [GATE] {marker}", file=sys.stderr)
                except _subprocess.TimeoutExpired:
                    engine.record_gate_result(
                        phase_id=phase_id,
                        passed=False,
                        output="Gate timed out after 300s",
                        command=gate_cmd,
                        exit_code=-1,
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
            else:
                # Non-TTY safety (bd-7444): without a controlling terminal we
                # cannot prompt for an approval decision.  Previous behaviour
                # caught the EOFError on input() and silently set choice =
                # "reject", which marked the execution failed and destroyed
                # state.  Instead, exit non-zero with a clear remediation hint
                # and leave state untouched so the operator can record an
                # explicit decision via `baton execute approve`.
                if not sys.stdin.isatty():
                    print(
                        f"\n{color_error('ERROR')}: pending approval for phase "
                        f"{phase_id} requires an explicit decision, but stdin "
                        "is not a TTY so 'baton execute run' cannot prompt.",
                        file=sys.stderr,
                    )
                    print(
                        "  Run: baton execute approve "
                        f"--phase-id {phase_id} --result approve|reject "
                        "[--feedback TEXT]",
                        file=sys.stderr,
                    )
                    print(
                        "  Then re-invoke 'baton execute run' to continue.",
                        file=sys.stderr,
                    )
                    # Do NOT mutate execution state — execution remains in
                    # approval_pending for the operator to resolve.
                    sys.exit(2)

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
        except ExecutionVetoed as exc:
            print(f"\n{color_error('VETOED')}: {exc}", file=sys.stderr)
            print(
                "Recovery: re-run with --force --justification \"...\" "
                "to override, or amend the plan.",
                file=sys.stderr,
            )
            sys.exit(2)
        except RuntimeError as exc:
            print(f"\n{color_error('ERROR')}: {exc}", file=sys.stderr)
            sys.exit(1)


# ---------------------------------------------------------------------------
# Helpers for list and switch subcommands
# ---------------------------------------------------------------------------

def _handle_list() -> None:
    """Print a table of all known executions with status and worker info."""
    context_root = _resolve_context_root()

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
    context_root = _resolve_context_root()
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
# Wave 1.3 (bd-86bf): worktree GC handler
# ---------------------------------------------------------------------------

def _handle_worktree_gc(args: argparse.Namespace) -> None:
    """Handler for ``baton execute worktree-gc``.

    Calls ``WorktreeManager.gc_stale()`` and prints a summary of reclaimed
    and skipped worktrees.  Exits non-zero if gc raises unexpectedly.
    """
    max_age_hours: int = getattr(args, "max_age_hours", 72)
    dry_run: bool = getattr(args, "dry_run", False)

    context_root = _resolve_context_root()
    # project root is two levels above team-context
    project_root = context_root.parent.parent

    try:
        from agent_baton.core.engine.worktree_manager import WorktreeManager
        mgr = WorktreeManager(project_root=project_root)
        reclaimed = mgr.gc_stale(max_age_hours=max_age_hours, dry_run=dry_run)
    except Exception as exc:
        print(f"error: worktree-gc failed: {exc}", file=sys.stderr)
        sys.exit(1)

    prefix = "[DRY RUN] Would reclaim" if dry_run else "Reclaimed"
    print(f"{prefix} {len(reclaimed)} worktree(s) (max_age_hours={max_age_hours})")
    for h in reclaimed:
        print(f"  step={h.step_id}  path={h.path}")


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


# ---------------------------------------------------------------------------
# DX.3 (bd-d136): TTY end-of-run handoff nudge.
# ---------------------------------------------------------------------------


_HANDOFF_NUDGE_TYPES = frozenset({
    ActionType.COMPLETE.value,
    ActionType.APPROVAL.value,
    ActionType.FAILED.value,
    "gate_fail",
    "complete",
})


def _maybe_handoff_nudge(
    action_dict: dict,
    task_id: str | None,
    context_root: Path,
) -> None:
    """Print the one-line handoff reminder at natural pause points.

    Fires only when:
    * The current action is a natural pause (complete, approval-required,
      gate failure, or failed terminal state).
    * stdout is a TTY -- never pollute machine-readable output.
    * No handoff has been recorded for ``task_id`` yet.

    All errors are swallowed -- a nudge must never break the CLI.
    """
    try:
        atype = (action_dict or {}).get("action_type", "")
        if atype not in _HANDOFF_NUDGE_TYPES:
            return
        from agent_baton.cli.commands.execution.handoff import (
            maybe_print_handoff_nudge,
        )
        maybe_print_handoff_nudge(task_id=task_id, context_root=context_root)
    except Exception:  # noqa: BLE001 - never let a nudge break the CLI
        return


# ---------------------------------------------------------------------------
# DX.3 (bd-d136): handoff subcommand dispatcher.
#
# The actual record/list/show implementations live in handoff.py so they
# can be imported by other entry points (the auto-discovered top-level
# ``baton handoff`` alias and the TTY end-of-run nudge).
# ---------------------------------------------------------------------------


def _handle_handoff(args: argparse.Namespace) -> None:
    """Dispatch ``baton execute handoff [record|list|show] ...``.

    Defaults to ``record`` when no positional action is given (so the
    spec's headline form ``baton execute handoff --note "..."`` works).
    """
    from agent_baton.cli.commands.execution import handoff as _handoff_mod

    action = getattr(args, "handoff_action", None) or "record"

    # Re-shape the namespace so the handoff module's handlers can consume
    # the same arg names regardless of which entry point invoked them.
    proxy = argparse.Namespace(
        note=getattr(args, "note", None),
        task_id=getattr(args, "task_id", None),
        branch=getattr(args, "branch", False),
        score=getattr(args, "score", False),
        output=getattr(args, "output", "text"),
        limit=getattr(args, "limit", 20),
        handoff_id=getattr(args, "handoff_id", None),
    )

    if action == "list":
        _handoff_mod._handle_list(proxy)
    elif action == "show":
        if not proxy.handoff_id:
            validation_error(
                "show requires a handoff ID",
                hint="Try: baton execute handoff show ho-abc123",
            )
        _handoff_mod._handle_show(proxy)
    else:
        _handoff_mod._handle_record(proxy)
