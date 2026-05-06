"""``baton knowledge ab`` — manage knowledge A/B experiments (K2.4).

Subcommands
-----------
list                    List all experiments.
create                  Register a new experiment.
results <experiment_id> Show per-variant success stats as a markdown table.
stop <experiment_id>    Stop an active experiment.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_DEFAULT_DB = Path(".claude/team-context/baton.db")


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "knowledge",
        help="Manage knowledge documents and A/B experiments.",
    )
    sub = p.add_subparsers(dest="knowledge_subcommand")

    ab_p = sub.add_parser("ab", help="Knowledge A/B experiment commands.")
    ab_sub = ab_p.add_subparsers(dest="ab_subcommand")

    # ab list
    ab_sub.add_parser("list", help="List all knowledge A/B experiments.")

    # ab create
    create_p = ab_sub.add_parser("create", help="Register a new A/B experiment.")
    create_p.add_argument("--kid", required=True, metavar="KNOWLEDGE_ID",
                          help="Canonical pack/doc id (e.g. 'security/owasp.md').")
    create_p.add_argument("--a", required=True, metavar="PATH_A",
                          help="Relative path to variant A document.")
    create_p.add_argument("--b", required=True, metavar="PATH_B",
                          help="Relative path to variant B document.")
    create_p.add_argument("--ratio", type=float, default=0.5, metavar="RATIO",
                          help="Fraction routed to variant A (default: 0.5).")

    # ab results <experiment_id>
    results_p = ab_sub.add_parser("results", help="Show A/B results as a markdown table.")
    results_p.add_argument("experiment_id", help="Experiment ID.")

    # ab stop <experiment_id>
    stop_p = ab_sub.add_parser("stop", help="Stop an active experiment.")
    stop_p.add_argument("experiment_id", help="Experiment ID.")

    return p


def handler(args: argparse.Namespace) -> None:
    if getattr(args, "knowledge_subcommand", None) != "ab":
        print("Usage: baton knowledge ab <list|create|results|stop>")
        return

    sub = getattr(args, "ab_subcommand", None)
    svc = _get_service()

    if sub == "list":
        _cmd_list(svc)
    elif sub == "create":
        _cmd_create(svc, args)
    elif sub == "results":
        _cmd_results(svc, args.experiment_id)
    elif sub == "stop":
        _cmd_stop(svc, args.experiment_id)
    else:
        print("Usage: baton knowledge ab <list|create|results|stop>")


# ---------------------------------------------------------------------------
# Sub-command implementations
# ---------------------------------------------------------------------------

def _cmd_list(svc) -> None:
    experiments = svc.list_experiments()
    if not experiments:
        print("No knowledge A/B experiments found.")
        return

    print(f"Knowledge A/B experiments ({len(experiments)}):\n")
    for exp in experiments:
        tag = exp.status.upper()
        print(f"  {exp.experiment_id}  [{tag}]  {exp.knowledge_id}")
        print(f"    A: {exp.variant_a_path}")
        print(f"    B: {exp.variant_b_path}")
        print(f"    split={exp.split_ratio}  started={exp.started_at}")
        if exp.stopped_at:
            print(f"    stopped={exp.stopped_at}")
        print()


def _cmd_create(svc, args: argparse.Namespace) -> None:
    ratio = args.ratio
    if not 0.0 <= ratio <= 1.0:
        print(f"Error: --ratio must be between 0.0 and 1.0, got {ratio}", file=sys.stderr)
        sys.exit(1)
    exp_id = svc.create_experiment(
        knowledge_id=args.kid,
        variant_a_path=args.a,
        variant_b_path=args.b,
        split_ratio=ratio,
    )
    print(f"Created experiment: {exp_id}")
    print(f"  knowledge_id : {args.kid}")
    print(f"  variant A    : {args.a}")
    print(f"  variant B    : {args.b}")
    print(f"  split ratio  : {ratio} (A) / {1 - ratio:.2f} (B)")


def _cmd_results(svc, experiment_id: str) -> None:
    exp = svc.get_experiment(experiment_id)
    if exp is None:
        print(f"Experiment not found: {experiment_id}", file=sys.stderr)
        sys.exit(1)

    results = svc.compute_results(experiment_id)

    print(f"## A/B Results: {experiment_id}\n")
    print(f"knowledge_id : {exp.knowledge_id}")
    print(f"status       : {exp.status}\n")

    # Markdown table
    print("| Variant | Path | Count | Success Rate |")
    print("|---------|------|-------|--------------|")
    print(
        f"| A | {exp.variant_a_path} | {results['a_count']} "
        f"| {results['a_success_rate']:.1%} |"
    )
    print(
        f"| B | {exp.variant_b_path} | {results['b_count']} "
        f"| {results['b_success_rate']:.1%} |"
    )
    print()

    winner = results["winner"]
    if winner:
        print(f"Winner: variant {winner.upper()}")
    else:
        a_n = results["a_count"]
        b_n = results["b_count"]
        if a_n < 10 or b_n < 10:
            print("Winner: undetermined (insufficient samples — need >=10 each)")
        else:
            print("Winner: undetermined (margin <10%)")


def _cmd_stop(svc, experiment_id: str) -> None:
    exp = svc.get_experiment(experiment_id)
    if exp is None:
        print(f"Experiment not found: {experiment_id}", file=sys.stderr)
        sys.exit(1)
    if exp.status == "stopped":
        print(f"Experiment {experiment_id} is already stopped.")
        return
    svc.stop_experiment(experiment_id)
    print(f"Stopped experiment: {experiment_id}")


# ---------------------------------------------------------------------------
# Service factory
# ---------------------------------------------------------------------------

def _get_service():
    from agent_baton.core.knowledge.ab_testing import KnowledgeABService
    from agent_baton.core.storage.connection import ConnectionManager
    from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION

    db_path = _DEFAULT_DB.resolve()
    if not db_path.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)

    mgr = ConnectionManager(db_path)
    mgr.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
    conn = mgr.get_connection()

    # Return a thin adapter that exposes _db_path for KnowledgeABService.
    class _StoreAdapter:
        def __init__(self, path: Path) -> None:
            self._db_path = path

    return KnowledgeABService(_StoreAdapter(db_path))
