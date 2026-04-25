"""``baton plan`` -- create an intelligent execution plan for a task.

Generates a MachinePlan by analysing the task description, detecting the
project stack, classifying risk, and routing to appropriate agents. The
plan can be output as markdown or JSON, and optionally saved to
.claude/team-context/plan.json for consumption by ``baton execute start``.

Additional flags:
    --template   Print a skeleton plan.json to stdout for hand-editing.
    --import     Import a hand-crafted plan.json instead of auto-generating.

Delegates to:
    agent_baton.core.engine.planner.IntelligentPlanner
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agent_baton.core.engine.planner import IntelligentPlanner
from agent_baton.core.govern.classifier import DataClassifier
from agent_baton.core.govern.policy import PolicyEngine
from agent_baton.core.observe.retrospective import RetrospectiveEngine
from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry
from agent_baton.models.execution import MachinePlan

_log = logging.getLogger(__name__)


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "plan",
        help="Create a data-driven execution plan for an orchestrated task",
    )
    p.add_argument("summary", help="Task summary / description")
    p.add_argument(
        "--task-type",
        dest="task_type",
        default=None,
        help="Override task type (new-feature, bug-fix, refactor, data-analysis, documentation, migration, test)",
    )
    p.add_argument(
        "--agents",
        default=None,
        help="Comma-separated agent names to use (overrides auto-selection)",
    )
    p.add_argument(
        "--project",
        default=None,
        help="Project root for stack detection (default: current directory)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Output plan as JSON instead of markdown",
    )
    p.add_argument(
        "--save",
        action="store_true",
        help="Save plan to .claude/team-context/plan.json and plan.md",
    )
    p.add_argument(
        "--explain",
        action="store_true",
        help="Show explanation of why this plan was chosen",
    )
    p.add_argument(
        "--knowledge",
        dest="knowledge",
        action="append",
        default=[],
        metavar="PATH",
        help="Explicit document file path to attach globally to all steps (repeatable)",
    )
    p.add_argument(
        "--knowledge-pack",
        dest="knowledge_pack",
        action="append",
        default=[],
        metavar="PACK",
        help="Explicit knowledge pack name to attach globally to all steps (repeatable)",
    )
    p.add_argument(
        "--intervention",
        dest="intervention",
        default="low",
        choices=["low", "medium", "high"],
        help="How aggressively agents escalate knowledge gaps (default: low)",
    )
    p.add_argument(
        "--model",
        dest="model",
        default=None,
        help="Default model for dispatched agents (e.g. 'opus', 'sonnet'). "
             "Overrides the built-in 'sonnet' default; agent definitions still take priority.",
    )
    p.add_argument(
        "--complexity",
        dest="complexity",
        default=None,
        choices=["light", "medium", "heavy"],
        help="Override task complexity (light, medium, heavy). "
             "Skips automatic classification when provided.",
    )
    p.add_argument(
        "--import", "--import-plan",
        dest="import_path",
        default=None,
        metavar="FILE",
        help="Import a hand-crafted plan.json file instead of auto-generating",
    )
    p.add_argument(
        "--template",
        action="store_true",
        help="Output a skeleton plan.json template for hand-editing",
    )
    p.add_argument(
        "--save-as-template",
        dest="save_as_template",
        default=None,
        metavar="NAME",
        help=(
            "Save the generated plan's phase/step structure as a reusable "
            "template named NAME in .claude/plan-templates/NAME.json"
        ),
    )
    p.add_argument(
        "--from-template",
        dest="from_template",
        default=None,
        metavar="NAME",
        help=(
            "Load a saved plan template by NAME from .claude/plan-templates/ "
            "and instantiate it with the provided task description"
        ),
    )
    p.add_argument(
        "--skip-init",
        dest="skip_init",
        action="store_true",
        help=(
            "Skip talent-builder auto-initiation even when .claude/agents/ is "
            "empty. Uses bundled generic agents instead."
        ),
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="When --save is set, print the full plan markdown to stdout (default: compact summary only)",
    )
    p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help=(
            "Preview the plan + cost/token forecast without saving. "
            "Use to sanity-check before --save / baton execute start."
        ),
    )
    return p


def _persist_plan_to_db(ctx_dir: Path, plan: MachinePlan) -> None:
    """Best-effort: save *plan* to the SQLite ``plans`` table in baton.db.

    Logs a warning on failure — never raises, so the caller's file-based
    save path is not interrupted.

    Args:
        ctx_dir: Path to ``.claude/team-context/`` (the context root).
        plan:    The plan to persist.
    """
    try:
        from agent_baton.core.storage import get_project_storage
        storage = get_project_storage(ctx_dir, backend="sqlite")
        storage.save_plan(plan)
        _log.debug("plan %s persisted to baton.db", plan.task_id)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "Could not persist plan %s to baton.db (non-fatal): %s",
            plan.task_id,
            exc,
        )


def _make_task_id(summary: str) -> str:
    """Generate a collision-free task ID without instantiating IntelligentPlanner."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = re.sub(r"[^a-z0-9]+", "-", summary.lower()).strip("-")
    slug = slug[:50].rstrip("-")
    uid = uuid.uuid4().hex[:8]
    base = f"{date_str}-{slug}" if slug else date_str
    return f"{base}-{uid}"


def _render_dry_run_forecast(plan: MachinePlan) -> str:
    """Render the compact dry-run forecast block for *plan*.

    Format target: ~25 lines, fixed-width, eyeball-readable.
    See ``baton plan --dry-run`` documentation for the exact format.
    """
    from agent_baton.core.engine.cost_estimator import (
        estimate_gate_seconds,
        estimate_step_tokens,
        estimate_wall_clock_minutes,
        forecast_plan,
    )

    forecast = forecast_plan(plan)
    agent_min, gate_min = estimate_wall_clock_minutes(plan)

    n_phases = len(plan.phases)
    n_steps = plan.total_steps
    stack = plan.detected_stack or "—"

    lines: list[str] = []
    lines.append("=== Plan Preview (NOT saved) ===")
    lines.append(f"Task ID:    {plan.task_id}")
    lines.append(
        f"Risk:       {plan.risk_level:<14} Budget: {plan.budget_tier:<13} "
        f"Mode: {plan.execution_mode}"
    )
    lines.append(
        f"Phases: {n_phases}   Steps: {n_steps}       Stack: {stack}"
    )
    lines.append("")
    lines.append(
        f"{'Phase / Step':<22}{'Agent':<28}{'Model':<9}{'Est. tokens':>12}"
    )
    lines.append("-" * 71)

    for phase in plan.phases:
        for step in phase.steps:
            ident = f"{phase.phase_id}.{step.step_id.split('.')[-1]} {phase.name}"[:21]
            agent = (step.agent_name or "—")[:27]
            model = (step.model or "sonnet")[:8]
            tokens = estimate_step_tokens(step)
            lines.append(
                f"{ident:<22}{agent:<28}{model:<9}{tokens:>12,}"
            )

    # Gates that will run
    gate_lines: list[str] = []
    for phase in plan.phases:
        if phase.gate is None:
            continue
        secs = estimate_gate_seconds(phase.gate.command)
        if secs >= 600:
            label = f"~{secs // 60}min — heavy"
        elif secs >= 60:
            label = f"~{secs // 60}min"
        else:
            label = f"~{secs}s"
        cmd = phase.gate.command or "(no command)"
        # Trim to keep the line eyeballable.
        if len(cmd) > 28:
            cmd = cmd[:25] + "..."
        gate_lines.append(
            f"  Phase {phase.phase_id}  {phase.gate.gate_type:<6} "
            f"{cmd:<28} [ {label} ]"
        )
    if gate_lines:
        lines.append("")
        lines.append("Gates that will block:")
        lines.extend(gate_lines)

    # Cost summary
    lines.append("")
    breakdown_parts = [
        f"{model} {count}" for model, count in sorted(forecast.model_breakdown.items())
    ]
    breakdown = ", ".join(breakdown_parts) if breakdown_parts else "—"
    lines.append(
        f"Cost forecast: ~{forecast.total_tokens:,} tokens   "
        f"~${forecast.total_cost_usd:.2f}   ({breakdown})"
    )
    total_min = agent_min + gate_min
    lines.append(
        f"Wall-clock:    ~{agent_min} min agent time + "
        f"{gate_min} min gate time = {total_min} min total"
    )
    lines.append("")
    lines.append("Re-run with --save to commit this plan. Use --explain for rationale.")
    return "\n".join(lines)


def handler(args: argparse.Namespace) -> None:
    # --dry-run + --save are mutually exclusive: don't let the user
    # accidentally believe nothing was written when --save is set, and
    # don't let --save silently win over --dry-run.
    if getattr(args, "dry_run", False) and getattr(args, "save", False):
        print(
            "Error: --dry-run and --save are mutually exclusive. "
            "Use --dry-run to preview without saving, then re-run with --save "
            "to commit the plan.",
            file=sys.stderr,
        )
        sys.exit(2)

    # --template: emit a skeleton plan.json and exit
    if getattr(args, "template", False):
        template: dict = {
            "task_summary": "Describe the task here",
            "task_type": "new-feature",
            "risk_level": "medium",
            "budget_tier": "standard",
            "git_strategy": "feature-branch",
            "complexity": "medium",
            "phases": [
                {
                    "phase_id": 1,
                    "name": "Design",
                    "steps": [
                        {
                            "step_id": "1.1",
                            "agent_name": "architect",
                            "task_description": "Describe what this agent should do",
                            "context_files": ["CLAUDE.md"],
                            "deliverables": [],
                        }
                    ],
                    "gate": {
                        "gate_type": "build",
                        "command": "echo 'gate check'",
                        "description": "Verify phase output",
                    },
                },
                {
                    "phase_id": 2,
                    "name": "Implement",
                    "steps": [
                        {
                            "step_id": "2.1",
                            "agent_name": "backend-engineer",
                            "task_description": "Implement the changes",
                            "context_files": ["CLAUDE.md"],
                            "deliverables": [],
                        }
                    ],
                    "gate": {
                        "gate_type": "test",
                        "command": "pytest",
                        "description": "Run test suite",
                    },
                },
            ],
        }
        print(json.dumps(template, indent=2))
        return

    # --import / --import-plan: load a hand-crafted plan.json and skip generation
    import_path = getattr(args, "import_path", None)
    if import_path is not None:
        plan_path = Path(import_path)
        if not plan_path.exists():
            print(f"Error: file not found: {plan_path}", file=sys.stderr)
            sys.exit(1)
        try:
            data = json.loads(plan_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            print(f"Error: invalid JSON: {exc}", file=sys.stderr)
            sys.exit(1)

        # Assign a task_id if the hand-crafted file omitted it
        if not data.get("task_id"):
            summary = data.get("task_summary", "imported-plan")
            data["task_id"] = _make_task_id(summary)

        # Validate by round-tripping through MachinePlan
        try:
            plan = MachinePlan.from_dict(data)
        except Exception as exc:
            print(f"Error: plan validation failed: {exc}", file=sys.stderr)
            print(
                "Hint: Use 'baton plan --template' to see the expected schema.",
                file=sys.stderr,
            )
            sys.exit(1)

        if args.save:
            from agent_baton.core.orchestration.context import ContextManager

            ctx_dir = Path(".claude/team-context").resolve()
            ctx_dir.mkdir(parents=True, exist_ok=True)

            ctx = ContextManager(team_context_dir=ctx_dir, task_id=plan.task_id)
            ctx.write_plan(plan)

            json_path = ctx_dir / "plan.json"
            md_path = ctx_dir / "plan.md"
            json_path.write_text(
                json.dumps(plan.to_dict(), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            md_path.write_text(plan.to_markdown(), encoding="utf-8")
            _persist_plan_to_db(ctx_dir, plan)
            print(f"Imported plan saved: {ctx.plan_json_path} and {ctx.plan_path}")
            print(f"  (also copied to {json_path} for backward compat)")
            print()
            print("Next: baton execute start")
        else:
            if getattr(args, "json", False):
                print(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False))
            else:
                print(plan.to_markdown())
        return

    project_root = Path(args.project) if args.project else Path.cwd()
    agents = [a.strip() for a in args.agents.split(",") if a.strip()] if args.agents else None

    print("Planning...", file=sys.stderr)

    knowledge_registry = KnowledgeRegistry()
    knowledge_registry.load_default_paths()

    retro_engine = RetrospectiveEngine()
    bead_store = None
    try:
        from agent_baton.core.engine.bead_store import BeadStore
        _db = Path(".claude/team-context/baton.db")
        if _db.exists():
            bead_store = BeadStore(_db)
    except Exception:
        pass
    print("  Analyzing patterns and history...", file=sys.stderr)
    planner = IntelligentPlanner(
        retro_engine=retro_engine,
        classifier=DataClassifier(),
        policy_engine=PolicyEngine(),
        knowledge_registry=knowledge_registry,
        bead_store=bead_store,
    )
    print("  Creating execution plan...", file=sys.stderr)
    plan = planner.create_plan(
        args.summary,
        task_type=args.task_type,
        complexity=args.complexity,
        project_root=project_root,
        agents=agents,
        explicit_knowledge_packs=args.knowledge_pack,
        explicit_knowledge_docs=args.knowledge,
        intervention_level=args.intervention,
        default_model=getattr(args, "model", None),
    )
    print("  Done.", file=sys.stderr)

    # --dry-run: print compact forecast and exit without writing anything.
    if getattr(args, "dry_run", False):
        print(_render_dry_run_forecast(plan))
        return

    if args.save:
        from agent_baton.core.orchestration.context import ContextManager

        ctx_dir = Path(".claude/team-context").resolve()
        ctx_dir.mkdir(parents=True, exist_ok=True)

        # Write to task-scoped directory (executions/<task_id>/)
        ctx = ContextManager(team_context_dir=ctx_dir, task_id=plan.task_id)
        ctx.write_plan(plan)

        # Also write to root for backward compat (most-recent plan shortcut)
        json_path = ctx_dir / "plan.json"
        md_path = ctx_dir / "plan.md"
        json_path.write_text(
            json.dumps(plan.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        md_path.write_text(plan.to_markdown(), encoding="utf-8")
        _persist_plan_to_db(ctx_dir, plan)

        if args.explain:
            explanation_path = ctx_dir / "explanation.md"
            explanation_path.write_text(planner.explain_plan(plan), encoding="utf-8")
            print(f"Plan saved: {json_path}")
            print(f"Plan markdown: {md_path}")
            n_phases = len(plan.phases)
            n_steps = plan.total_steps
            print(
                f"Task ID: {plan.task_id} | Risk: {plan.risk_level} | "
                f"Budget: {plan.budget_tier} | Phases: {n_phases} | Steps: {n_steps}"
            )
            print(f"Plan explanation: {explanation_path}")
            print("Next: baton execute start")
        elif getattr(args, "verbose", False):
            print(f"Plan saved: {ctx.plan_json_path} and {ctx.plan_path}")
            print(f"  (also copied to {json_path} for backward compat)")
            print()
            print(plan.to_markdown())
            print()
            print("Next: baton execute start")
        else:
            print(f"Plan saved: {json_path}")
            print(f"Plan markdown: {md_path}")
            n_phases = len(plan.phases)
            n_steps = plan.total_steps
            print(
                f"Task ID: {plan.task_id} | Risk: {plan.risk_level} | "
                f"Budget: {plan.budget_tier} | Phases: {n_phases} | Steps: {n_steps}"
            )
            print("Next: baton execute start")
        return

    if args.json:
        print(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(plan.to_markdown())
