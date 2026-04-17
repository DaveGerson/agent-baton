"""``baton plan`` -- create an intelligent execution plan for a task.

Generates a MachinePlan by analysing the task description, detecting the
project stack, classifying risk, and routing to appropriate agents. The
plan can be output as markdown or JSON, and optionally saved to
.claude/team-context/plan.json for consumption by ``baton execute start``.

Additional flags:
    --template          Print a skeleton plan.json to stdout for hand-editing.
    --import            Import a hand-crafted plan.json instead of auto-generating.
    --save-as-template  Save the generated plan's phase/step structure as a
                        reusable template in .claude/plan-templates/.
    --from-template     Instantiate a previously saved template with a new
                        task description instead of auto-generating.

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
    return p


# ---------------------------------------------------------------------------
# E1 — Plan template helpers
# ---------------------------------------------------------------------------

_PLAN_TEMPLATES_DIR = Path(".claude/plan-templates")


def _template_path(name: str) -> Path:
    """Return the resolved path for a named plan template."""
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    return (_PLAN_TEMPLATES_DIR / f"{safe_name}.json").resolve()


def _plan_to_template(plan_dict: dict) -> dict:
    """Strip task-specific fields from a plan dict, leaving only scaffold structure.

    Retains phase names, step agents, step types, gate configs, and deliverable
    stubs.  Removes task_id, task_summary, created_at, shared_context, and any
    per-task knowledge attachments so the template is reusable across tasks.
    """
    template: dict = {
        "_template_version": 1,
        "task_type": plan_dict.get("task_type"),
        "risk_level": plan_dict.get("risk_level", "LOW"),
        "budget_tier": plan_dict.get("budget_tier", "standard"),
        "git_strategy": plan_dict.get("git_strategy", "commit-per-agent"),
        "complexity": plan_dict.get("complexity", "medium"),
        "phases": [],
    }
    for phase in plan_dict.get("phases", []):
        phase_entry: dict = {
            "phase_id": phase.get("phase_id"),
            "name": phase.get("name"),
            "steps": [],
        }
        for step in phase.get("steps", []):
            step_entry: dict = {
                "agent_name": step.get("agent_name"),
                "step_type": step.get("step_type", "developing"),
                "deliverables": step.get("deliverables", []),
            }
            phase_entry["steps"].append(step_entry)
        gate = phase.get("gate")
        if gate:
            phase_entry["gate"] = {
                "gate_type": gate.get("gate_type"),
                "command": gate.get("command", ""),
                "description": gate.get("description", ""),
            }
        template["phases"].append(phase_entry)
    return template


def _instantiate_template(template: dict, task_summary: str) -> dict:
    """Build a plan dict from a template and a new task description.

    Assigns a fresh task_id and populates task_summary.  Step descriptions
    are generated as '<agent_name>: <task_summary>' placeholders so the
    plan is immediately usable with ``baton execute start``.
    """
    task_id = _make_task_id(task_summary)
    plan: dict = {
        "task_id": task_id,
        "task_summary": task_summary,
        "risk_level": template.get("risk_level", "LOW"),
        "budget_tier": template.get("budget_tier", "standard"),
        "git_strategy": template.get("git_strategy", "commit-per-agent"),
        "complexity": template.get("complexity", "medium"),
        "task_type": template.get("task_type"),
        "execution_mode": "phased",
        "shared_context": "",
        "pattern_source": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "explicit_knowledge_packs": [],
        "explicit_knowledge_docs": [],
        "intervention_level": "low",
        "classification_source": "template",
        "detected_stack": None,
        "foresight_insights": [],
        "phases": [],
    }
    step_counter = 1
    for phase in template.get("phases", []):
        phase_entry: dict = {
            "phase_id": phase.get("phase_id"),
            "name": phase.get("name", f"Phase {phase.get('phase_id', step_counter)}"),
            "steps": [],
            "approval_required": False,
            "approval_description": None,
        }
        for step in phase.get("steps", []):
            agent = step.get("agent_name", "backend-engineer")
            phase_entry["steps"].append({
                "step_id": str(step_counter),
                "agent_name": agent,
                "task_description": f"{agent}: {task_summary}",
                "step_type": step.get("step_type", "developing"),
                "context_files": ["CLAUDE.md"],
                "deliverables": step.get("deliverables", []),
                "knowledge": [],
                "model": None,
                "team": [],
                "depends_on": [],
            })
            step_counter += 1
        gate = phase.get("gate")
        if gate:
            phase_entry["gate"] = gate
        else:
            phase_entry["gate"] = None
        plan["phases"].append(phase_entry)
    return plan


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


def handler(args: argparse.Namespace) -> None:  # noqa: C901
    # --from-template: load a saved template and instantiate with new description
    from_template = getattr(args, "from_template", None)
    if from_template is not None:
        tpl_path = _template_path(from_template)
        if not tpl_path.exists():
            # Also check relative to cwd (for projects that embed .claude elsewhere)
            alt = Path(".claude/plan-templates") / f"{from_template}.json"
            if alt.resolve().exists():
                tpl_path = alt.resolve()
            else:
                print(
                    f"Error: template '{from_template}' not found. "
                    f"Expected at {tpl_path}",
                    file=sys.stderr,
                )
                print(
                    "Tip: use 'baton plan <desc> --save-as-template NAME' to save one first.",
                    file=sys.stderr,
                )
                sys.exit(1)

        try:
            template_data = json.loads(tpl_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            print(f"Error: invalid template JSON: {exc}", file=sys.stderr)
            sys.exit(1)

        plan_dict = _instantiate_template(template_data, args.summary)
        try:
            plan = MachinePlan.from_dict(plan_dict)
        except Exception as exc:
            print(f"Error: template instantiation failed: {exc}", file=sys.stderr)
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
            print(
                f"Template '{from_template}' instantiated and saved: "
                f"{ctx.plan_json_path} and {ctx.plan_path}"
            )
            print(f"  (also copied to {json_path} for backward compat)")
            print()
            print("Next: baton execute start")
        else:
            if getattr(args, "json", False):
                print(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False))
            else:
                print(plan.to_markdown())
        return

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
                            "step_type": "planning",
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
                            "step_type": "developing",
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
        print(f"Plan saved: {ctx.plan_json_path} and {ctx.plan_path}")
        print(f"  (also copied to {json_path} for backward compat)")
        print()
        print("Next: baton execute start")

    # --save-as-template: serialize phase/step scaffold after generation
    save_as_template = getattr(args, "save_as_template", None)
    if save_as_template is not None:
        tpl_dir = Path(".claude/plan-templates").resolve()
        tpl_dir.mkdir(parents=True, exist_ok=True)
        tpl_path = _template_path(save_as_template)
        template_data = _plan_to_template(plan.to_dict())
        tpl_path.write_text(
            json.dumps(template_data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Plan template '{save_as_template}' saved to {tpl_path}")
        print(
            f"  Use: baton plan \"<new description>\" --from-template {save_as_template} --save"
        )

    if args.explain:
        print(planner.explain_plan(plan))
        return

    if args.json:
        print(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(plan.to_markdown())
