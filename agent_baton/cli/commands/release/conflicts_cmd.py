"""``baton predict-conflicts`` -- predict file-level conflicts in a saved plan.

Loads a ``plan.json`` produced by ``baton plan --save`` and runs the
:class:`ConflictPredictor` against it.  Prints a markdown table by default
or JSON with ``--json``.

Velocity-positive Tier 2: this command is purely informational.  It never
mutates state and never blocks execution.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_baton.core.release.conflict_predictor import ConflictPredictor
from agent_baton.models.execution import MachinePlan


_DEFAULT_PLAN_PATH = ".claude/team-context/plan.json"


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "predict-conflicts",
        help="Predict file-level conflicts between parallel plan steps",
        description=(
            "Scan a saved plan.json and warn about likely file-level "
            "conflicts between parallel-eligible steps. Velocity-positive: "
            "warnings only, never blocks."
        ),
    )
    p.add_argument(
        "--plan",
        default=_DEFAULT_PLAN_PATH,
        help=f"Path to plan.json (default: {_DEFAULT_PLAN_PATH})",
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit machine-readable JSON instead of a markdown table",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    plan_path = Path(args.plan)
    if not plan_path.exists():
        print(f"error: plan file not found: {plan_path}", file=sys.stderr)
        print(
            "  Hint: run 'baton plan --save \"task description\"' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        data = json.loads(plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"error: plan.json is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        plan = MachinePlan.from_dict(data)
    except (KeyError, ValueError, TypeError) as exc:
        print(f"error: plan.json has invalid structure: {exc}", file=sys.stderr)
        sys.exit(1)

    report = ConflictPredictor(plan).predict()

    if args.as_json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(ConflictPredictor.summarize(report))
