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
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agent_baton.cli.errors import validation_error
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
        "--manager-mode",
        dest="manager_mode",
        action="store_true",
        help=(
            "Post-process the plan into manager-mode PMO sidecar artifacts "
            "(project charter, scope map, team blueprint, role cards, "
            "knowledge plan, scope contracts, context bundles, manager "
            "brief) under .claude/team-context/executions/<task_id>/. Also "
            "enabled by manager_mode.enabled_by_default in "
            ".claude/baton.yaml. See "
            "docs/internal/manager-mode-pmo-design.md."
        ),
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
    p.add_argument(
        "--release",
        dest="release_id",
        default=None,
        metavar="RELEASE_ID",
        help=(
            "Tag the saved plan against an existing Release (R3.1). "
            "Requires --save. The release must exist (create with "
            "`baton release create --id ...`); otherwise the plan is "
            "tagged anyway and a warning is printed."
        ),
    )
    p.add_argument(
        "--gate-scope",
        dest="gate_scope",
        default=None,
        choices=["focused", "full", "smoke"],
        help=(
            "How broadly gate commands run (bd-124f). "
            "'focused' (default) scopes pytest to the test files that cover "
            "changed source paths — fast, permission-policy-safe. "
            "'full' produces legacy unscoped pytest / pytest --cov (escape hatch). "
            "'smoke' runs import-only (build gates) or collect-only (test gates). "
            "Default sentinel is None (not 'focused') so the handler can "
            "tell whether this flag was explicitly passed vs. defaulted — "
            "see cli_gate_scope_explicit in handler()."
        ),
    )
    p.add_argument(
        "--goal",
        dest="goal",
        default=None,
        metavar="CONDITION",
        help=(
            "Set a completion condition (G1, /goal wrap). The engine "
            "evaluates the goal at phase boundaries and uses amend_plan "
            "to round out gaps until the condition is met, the amend "
            "budget is exhausted, or BATON_RUN_TOKEN_CEILING is hit. "
            "See docs/internal/agent-teams-and-goal-design.md."
        ),
    )
    p.add_argument(
        "--max-amend-cycles",
        dest="max_amend_cycles",
        type=int,
        default=3,
        metavar="N",
        help=(
            "Maximum number of goal-driven round-out cycles. Only "
            "meaningful with --goal. Default: 3."
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


def _tag_plan_with_release(ctx_dir: Path, task_id: str, release_id: str) -> None:
    """Tag *task_id* against *release_id* in the plans table (R3.1).

    Best-effort: warns to stderr on failure but never raises. The release
    does not need to exist to be tagged (soft FK); a missing release
    triggers a non-fatal warning so the user notices typos.
    """
    try:
        from agent_baton.core.storage.release_store import ReleaseStore

        db_path = (ctx_dir / "baton.db").resolve()
        store = ReleaseStore(db_path)
        if store.get(release_id) is None:
            print(
                f"warning: release {release_id!r} does not exist; "
                "tagging anyway (create with `baton release create --id ...`).",
                file=sys.stderr,
            )
        ok = store.tag_plan(task_id, release_id)
        if ok:
            print(f"Tagged plan {task_id} -> release {release_id}", file=sys.stderr)
        else:
            print(
                f"warning: could not tag plan {task_id} (no plans row)",
                file=sys.stderr,
            )
    except Exception as exc:  # noqa: BLE001
        _log.warning("Could not tag plan %s with release %s: %s", task_id, release_id, exc)


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
    # bd-47b4: surface the ±50% confidence band on every forecast so
    # developers do not treat the dollar figure as authoritative.
    low_usd = forecast.total_cost_usd * 0.5
    high_usd = forecast.total_cost_usd * 1.5
    lines.append(
        "Estimate ±50%; based on historical token-rate samples — "
        "actual cost depends on model + retries."
    )
    lines.append(
        f"Cost forecast: ~{forecast.total_tokens:,} tokens   "
        f"~${forecast.total_cost_usd:.2f}   "
        f"(range ~${low_usd:.2f}–${high_usd:.2f}; {breakdown})"
    )
    total_min = agent_min + gate_min
    lines.append(
        f"Wall-clock:    ~{agent_min} min agent time + "
        f"{gate_min} min gate time = {total_min} min total"
    )
    lines.append("")
    lines.append("Re-run with --save to commit this plan. Use --explain for rationale.")
    return "\n".join(lines)


def _render_manager_mode_explain_section(manager_artifacts, manager_config) -> str:
    """Render the ``## Manager Mode`` section appended to ``explanation.md``
    when ``--manager-mode --explain`` are both set (W3).

    Reads only public fields off *manager_artifacts*
    (``agent_baton.core.manager.artifacts.ManagerArtifacts``) and
    *manager_config* (``agent_baton.core.config.manager.ManagerConfig``) --
    workstream ownership is read exclusively from
    ``TeamBlueprint.workstream_assignments`` (never
    ``Workstream.owner_role``), matching the binding rule documented on
    ``ManagerModePlanner``.
    """
    lines: list[str] = ["## Manager Mode", ""]

    scope_map = manager_artifacts.scope_map
    blueprint = manager_artifacts.blueprint

    lines.append("### Workstreams")
    if scope_map is not None and scope_map.workstreams:
        for ws in scope_map.workstreams:
            owner = "(unassigned)"
            if blueprint is not None:
                owner = blueprint.workstream_assignments.get(ws.id) or owner
            lines.append(f"- {ws.name or ws.id} — owner: {owner}")
    else:
        lines.append("_None recorded._")
    lines.append("")

    lines.append("### Team")
    if blueprint is not None and blueprint.roles:
        for card in blueprint.roles:
            lines.append(f"- {card.role}: {card.mission or '(no mission set)'}")
    else:
        lines.append("_None recorded._")
    lines.append("")

    lines.append("### Policies")
    lines.append(
        f"- Phase adversarial review: {manager_config.policies.phase_completion.adversarial_review}"
    )
    lines.append(
        f"- Project adversarial review: {manager_config.policies.project_completion.adversarial_review}"
    )
    lines.append(
        f"- Handoff required: {manager_config.policies.phase_completion.handoff_required}"
    )
    if blueprint is not None:
        gate_scope_applied = blueprint.phase_policies.get("gate_scope_applied")
        if gate_scope_applied:
            lines.append(f"- Gate scope applied: {gate_scope_applied}")
        injected = blueprint.phase_policies.get("injected_review_steps") or []
        if injected:
            lines.append(f"- Injected review steps: {', '.join(injected)}")
        final_review = blueprint.phase_policies.get("final_review_step")
        if final_review:
            lines.append(f"- Final project review step: {final_review}")
    lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def _print_manager_mode_artifacts(ctx_dir: Path, plan: MachinePlan, manager_artifacts) -> None:
    """Print the ``Artifacts:`` block for a manager-mode ``--save`` run.

    Matches PRD §20's example shape: an ``Artifacts:`` header followed by
    one indented path per line. Reuses ``preview_paths`` (see
    ``agent_baton.core.manager.artifacts``), which mirrors ``write_all``'s
    traversal order exactly, so this reports precisely what was just
    persisted to disk -- charter, scope map, team blueprint, role cards,
    knowledge plan, scope contracts, context bundles, and the manager
    brief (PRD's "full write_all list acceptable").

    Manager-mode only: callers must gate this behind
    ``manager_artifacts is not None`` so non-manager ``--save`` output
    stays byte-identical.
    """
    from agent_baton.core.manager.artifacts import preview_paths
    from agent_baton.core.manager.paths import ManagerArtifactPaths

    paths = ManagerArtifactPaths(ctx_dir, plan.task_id)
    items = preview_paths(paths, manager_artifacts)
    print()
    print("Artifacts:")
    for artifact_path, _description in items:
        print(f"  {artifact_path}")


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

        # G1: --goal on the CLI overrides any completion_condition in
        # the imported file.
        goal_text = getattr(args, "goal", None)
        if goal_text:
            plan.completion_condition = goal_text
            plan.max_amend_cycles = max(0, getattr(args, "max_amend_cycles", 3))

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
            release_id = getattr(args, "release_id", None)
            if release_id:
                _tag_plan_with_release(ctx_dir, plan.task_id, release_id)
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
        from agent_baton.core.engine.bead_backend import make_bead_store
        _db = Path(".claude/team-context/baton.db")
        if _db.exists():
            bead_store = make_bead_store(_db)
    except Exception:
        pass

    # Build a pack-aware classifier and register pack policies so that
    # load_preset("pack:…") resolves correctly during planning.
    try:
        from agent_baton.core.govern.packs import (
            load_packs,
            make_classifier_for_packs,
            register_pack_policies,
        )
        _packs = load_packs(project_root)
        register_pack_policies(_packs)
        _classifier: DataClassifier = make_classifier_for_packs(_packs)
    except Exception:
        _classifier = DataClassifier()

    print("  Analyzing patterns and history...", file=sys.stderr)
    planner = IntelligentPlanner(
        retro_engine=retro_engine,
        classifier=_classifier,
        policy_engine=PolicyEngine(),
        knowledge_registry=knowledge_registry,
        bead_store=bead_store,
    )
    print("  Creating execution plan...", file=sys.stderr)
    # M6 prep: record whether --gate-scope was explicitly passed (argparse
    # default sentinel is None, not "focused" -- see register() above) so
    # PhasePolicyApplier can tell "user asked for X" apart from "nothing
    # specified, use the project-configured default" (threaded into
    # ManagerModePlanner as `cli_gate_scope_explicit` below).
    # getattr (not direct attribute access) because several existing test
    # fixtures hand-construct an argparse.Namespace without a gate_scope
    # attribute at all; that must keep behaving like "not explicit".
    _gate_scope_arg = getattr(args, "gate_scope", None)
    cli_gate_scope_explicit = _gate_scope_arg is not None
    gate_scope = _gate_scope_arg or "focused"
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
        gate_scope=gate_scope,
    )
    print("  Done.", file=sys.stderr)

    # A1.d: surface claude-teams + long-running resumability warnings.
    # Strict mode (BATON_TEAMS_STRICT_RESUMABILITY=1) treats the warning
    # as a hard error and refuses the plan, matching the design's
    # "refuse to place team phases late" wording. Default is a warning
    # so the planner stays usable when the user accepts the trade-off.
    try:
        from agent_baton.core.engine.team_backends import (
            check_resumability_constraints,
        )
        _warnings = check_resumability_constraints(plan)
        _strict = os.environ.get("BATON_TEAMS_STRICT_RESUMABILITY", "0").strip() == "1"
        for _warn in _warnings:
            print(f"warning: {_warn}", file=sys.stderr)
        if _warnings and _strict:
            print(
                "error: BATON_TEAMS_STRICT_RESUMABILITY=1 and the plan "
                "violates resumability constraints; refusing to save. "
                "Unset the flag, restructure the plan, or change "
                "BATON_TEAMS_BACKEND.",
                file=sys.stderr,
            )
            sys.exit(2)
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 — diagnostic only, never fatal
        pass

    # G1: stamp goal fields onto the plan after creation. We do this
    # post-creation (rather than threading through planner.create_plan)
    # to keep the planner signature stable and let `baton goal` reuse
    # the same path.
    goal_text = getattr(args, "goal", None)
    if goal_text:
        plan.completion_condition = goal_text
        plan.max_amend_cycles = max(0, getattr(args, "max_amend_cycles", 3))

    # Manager-mode PMO layer (M1, see docs/internal/manager-mode-pmo-design.md).
    # ManagerConfig is only loaded when it can matter -- --manager-mode was
    # passed, or a project baton.yaml exists that could set
    # manager_mode.enabled_by_default -- so a plain `baton plan` with no
    # manager-mode footprint anywhere skips the lookup entirely. The
    # heavier `agent_baton.core.manager` builder package is only imported
    # further below when manager mode is actually requested, so
    # non-manager plans have zero core.manager import side effects and
    # `plan.to_dict()` is unchanged apart from the `manager_mode: False`
    # field added in this milestone.
    #
    # ManagerConfig.load() fails early (ManagerConfigError) on malformed
    # YAML or an invalid nested policy value -- by design, so `baton
    # config validate` surfaces problems loudly. But a plain `baton plan`
    # (manager mode not requested) must never crash on someone else's
    # broken baton.yaml/~/.baton/config.yaml: downgrade to a warning and
    # fall back to defaults in that case. Only when the user explicitly
    # asked for manager mode (--manager-mode) do we treat a bad config as
    # a hard, user-facing error (typed, no raw traceback).
    from agent_baton.core.config.manager import ManagerConfig, ManagerConfigError

    _manager_mode_flag = bool(getattr(args, "manager_mode", False))
    manager_config = ManagerConfig()
    if _manager_mode_flag or ManagerConfig.find_config_file(project_root) is not None:
        try:
            manager_config = ManagerConfig.load(project_root)
        except ManagerConfigError as exc:
            if _manager_mode_flag:
                validation_error(
                    f"invalid manager config: {exc}",
                    hint=(
                        "Fix .claude/baton.yaml (or ~/.baton/config.yaml), "
                        "or omit --manager-mode."
                    ),
                    docs="docs/internal/manager-mode-pmo-design.md",
                )
            _log.warning(
                "Ignoring invalid manager config (non-fatal, manager mode "
                "not requested): %s",
                exc,
            )

    manager_requested = _manager_mode_flag or manager_config.manager_mode.enabled_by_default
    if manager_requested:
        plan.manager_mode = True

    # --dry-run: print compact forecast and exit without writing anything.
    if getattr(args, "dry_run", False):
        # Manager-mode PMO preview (W3): build the full composition
        # in-memory (never touches disk -- see
        # agent_baton.core.manager.planner.ManagerModePlanner.build) so the
        # forecast below and the artifact-preview list both reflect the
        # FINAL plan shape, including any adversarial-review steps the
        # policy applier injects and any gate rescoping it applies.
        manager_preview: list[tuple[Path, str]] = []
        if manager_requested:
            from agent_baton.core.manager.artifacts import preview_paths
            from agent_baton.core.manager.paths import ManagerArtifactPaths
            from agent_baton.core.manager.planner import ManagerModePlanner

            preview_ctx_dir = Path(".claude/team-context").resolve()
            manager_planner = ManagerModePlanner(
                manager_config,
                project_root=project_root,
                team_context_dir=preview_ctx_dir,
                knowledge_registry=knowledge_registry,
                cli_gate_scope_explicit=cli_gate_scope_explicit,
            )
            manager_artifacts = manager_planner.build(plan, plan.task_summary)
            manager_preview = preview_paths(
                ManagerArtifactPaths(preview_ctx_dir, plan.task_id), manager_artifacts
            )

        # bd-47b4: when paired with --json, emit a structured payload that
        # includes the ±50% range so machine consumers (CI dashboards,
        # forge, etc.) cannot mistake the central estimate for an
        # authoritative figure.
        if getattr(args, "json", False):
            from agent_baton.core.engine.cost_estimator import (
                estimate_wall_clock_minutes,
                forecast_plan,
            )
            forecast = forecast_plan(plan)
            agent_min, gate_min = estimate_wall_clock_minutes(plan)
            payload = {
                "task_id": plan.task_id,
                "risk_level": plan.risk_level,
                "budget_tier": plan.budget_tier,
                "phases": len(plan.phases),
                "steps": plan.total_steps,
                "cost_forecast": {
                    "total_tokens": forecast.total_tokens,
                    "total_cost_usd": round(forecast.total_cost_usd, 4),
                    "estimate_band": {
                        "low_usd": round(forecast.total_cost_usd * 0.5, 4),
                        "high_usd": round(forecast.total_cost_usd * 1.5, 4),
                        "confidence": "±50%",
                        "basis": "historical token-rate samples; "
                                 "actual cost depends on model + retries",
                    },
                    "model_breakdown_tokens": dict(forecast.model_breakdown),
                },
                "wall_clock": {
                    "agent_minutes": agent_min,
                    "gate_minutes": gate_min,
                    "total_minutes": agent_min + gate_min,
                },
            }
            if manager_preview:
                payload["manager_mode_artifacts"] = [
                    {"path": str(path), "description": description}
                    for path, description in manager_preview
                ]
            print(json.dumps(payload, indent=2))
            return
        print(_render_dry_run_forecast(plan))
        if manager_preview:
            print()
            print("Manager Mode artifacts (preview only -- nothing written):")
            for path, description in manager_preview:
                print(f"  {path}  ({description})")
        return

    if args.save:
        from agent_baton.core.orchestration.context import ContextManager

        ctx_dir = Path(".claude/team-context").resolve()
        ctx_dir.mkdir(parents=True, exist_ok=True)

        manager_artifacts = None
        if manager_requested:
            # Import here (not at module top) so non-manager plans never
            # import agent_baton.core.manager. Runs BEFORE the plan itself
            # is persisted below: PhasePolicyApplier (invoked inside
            # build_and_write) mutates `plan` in place -- injecting
            # adversarial-review steps and optionally rescaling gates --
            # so plan.json/plan.md must reflect the FINAL, policy-applied
            # step list, not the pre-policy draft.
            from agent_baton.core.manager.planner import ManagerModePlanner

            manager_planner = ManagerModePlanner(
                manager_config,
                project_root=project_root,
                team_context_dir=ctx_dir,
                knowledge_registry=knowledge_registry,
                cli_gate_scope_explicit=cli_gate_scope_explicit,
            )
            manager_artifacts = manager_planner.build_and_write(plan, plan.task_summary)

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
        release_id = getattr(args, "release_id", None)
        if release_id:
            _tag_plan_with_release(ctx_dir, plan.task_id, release_id)

        if args.explain:
            explanation_text = planner.explain_plan(plan)
            if manager_artifacts is not None:
                explanation_text += "\n\n" + _render_manager_mode_explain_section(
                    manager_artifacts, manager_config
                )
            explanation_path = ctx_dir / "explanation.md"
            explanation_path.write_text(explanation_text, encoding="utf-8")
            print(f"Plan saved: {json_path}")
            print(f"Plan markdown: {md_path}")
            n_phases = len(plan.phases)
            n_steps = plan.total_steps
            print(
                f"Task ID: {plan.task_id} | Risk: {plan.risk_level} | "
                f"Budget: {plan.budget_tier} | Phases: {n_phases} | Steps: {n_steps}"
            )
            print(f"Plan explanation: {explanation_path}")
            if manager_artifacts is not None:
                _print_manager_mode_artifacts(ctx_dir, plan, manager_artifacts)
            print("Next: baton execute start")
        elif getattr(args, "verbose", False):
            print(f"Plan saved: {ctx.plan_json_path} and {ctx.plan_path}")
            print(f"  (also copied to {json_path} for backward compat)")
            print()
            print(plan.to_markdown())
            print()
            if manager_artifacts is not None:
                _print_manager_mode_artifacts(ctx_dir, plan, manager_artifacts)
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
            if manager_artifacts is not None:
                _print_manager_mode_artifacts(ctx_dir, plan, manager_artifacts)
            print("Next: baton execute start")
        return

    if args.json:
        print(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(plan.to_markdown())
