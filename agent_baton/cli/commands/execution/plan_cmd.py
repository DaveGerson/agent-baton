"""baton plan — create an intelligent execution plan for a task."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_baton.core.engine.planner import IntelligentPlanner
from agent_baton.core.observe.retrospective import RetrospectiveEngine


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
    return p


def handler(args: argparse.Namespace) -> None:
    project_root = Path(args.project) if args.project else Path.cwd()
    agents = [a.strip() for a in args.agents.split(",") if a.strip()] if args.agents else None

    retro_engine = RetrospectiveEngine()
    planner = IntelligentPlanner(retro_engine=retro_engine)
    plan = planner.create_plan(
        args.summary,
        task_type=args.task_type,
        project_root=project_root,
        agents=agents,
    )

    if args.save:
        ctx_dir = Path(".claude/team-context").resolve()
        ctx_dir.mkdir(parents=True, exist_ok=True)
        json_path = ctx_dir / "plan.json"
        md_path = ctx_dir / "plan.md"
        json_path.write_text(
            json.dumps(plan.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        md_path.write_text(plan.to_markdown(), encoding="utf-8")
        print(f"Plan saved: {json_path} and {md_path}")

    if args.explain:
        print(planner.explain_plan(plan))
        return

    if args.json:
        print(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(plan.to_markdown())
