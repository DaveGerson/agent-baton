"""``baton experiment`` -- manage improvement experiments.

Experiments are controlled trials that test whether an improvement
recommendation actually improves agent performance.  Each experiment
tracks a metric (e.g. success rate) against a baseline, collects
samples over subsequent executions, and can be concluded or rolled
back.

Subcommands:
    * ``list`` -- List all experiments with status and progress.
    * ``show ID`` -- Show detailed experiment state.
    * ``conclude ID --result improved|degraded|inconclusive`` -- Manually
      conclude an experiment.
    * ``rollback ID`` -- Roll back an experiment and its recommendation.
      Triggers circuit breaker warning if 3+ rollbacks in 7 days.

Delegates to:
    :class:`~agent_baton.core.improve.experiments.ExperimentManager`
    :class:`~agent_baton.core.improve.proposals.ProposalManager`
    :class:`~agent_baton.core.improve.rollback.RollbackManager`
"""
from __future__ import annotations

import argparse

from agent_baton.core.improve.experiments import ExperimentManager
from agent_baton.core.improve.proposals import ProposalManager
from agent_baton.core.improve.rollback import RollbackManager


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "experiment",
        help="Manage improvement experiments",
    )
    sub = p.add_subparsers(dest="subcommand")

    # experiment list
    sub.add_parser("list", help="List all experiments")

    # experiment show <id>
    show_p = sub.add_parser("show", help="Show details of an experiment")
    show_p.add_argument("experiment_id", help="Experiment ID")

    # experiment conclude <id> --result <result>
    conclude_p = sub.add_parser("conclude", help="Manually conclude an experiment")
    conclude_p.add_argument("experiment_id", help="Experiment ID")
    conclude_p.add_argument(
        "--result",
        required=True,
        choices=["improved", "degraded", "inconclusive"],
        help="Experiment outcome",
    )

    # experiment rollback <id>
    rollback_p = sub.add_parser("rollback", help="Roll back an experiment")
    rollback_p.add_argument("experiment_id", help="Experiment ID")

    return p


def handler(args: argparse.Namespace) -> None:
    mgr = ExperimentManager()

    if args.subcommand == "list":
        _handle_list(mgr)
    elif args.subcommand == "show":
        _handle_show(mgr, args.experiment_id)
    elif args.subcommand == "conclude":
        _handle_conclude(mgr, args.experiment_id, args.result)
    elif args.subcommand == "rollback":
        _handle_rollback(mgr, args.experiment_id)
    else:
        # Default: list
        _handle_list(mgr)


def _handle_list(mgr: ExperimentManager) -> None:
    experiments = mgr.list_all()
    if not experiments:
        print("No experiments found.")
        return

    print(f"Experiments ({len(experiments)}):")
    print()
    for exp in experiments:
        status_tag = exp.status.upper()
        result_tag = f" [{exp.result}]" if exp.result else ""
        print(f"  {exp.experiment_id}  [{status_tag}]{result_tag}")
        print(f"    Agent: {exp.agent_name}  |  Metric: {exp.metric}")
        print(f"    Samples: {len(exp.samples)}/{exp.min_samples}")
        print()


def _handle_show(mgr: ExperimentManager, experiment_id: str) -> None:
    exp = mgr.get(experiment_id)
    if exp is None:
        print(f"Experiment '{experiment_id}' not found.")
        return

    print(f"Experiment: {exp.experiment_id}")
    print(f"  Recommendation: {exp.recommendation_id}")
    print(f"  Hypothesis: {exp.hypothesis}")
    print(f"  Agent:      {exp.agent_name}")
    print(f"  Metric:     {exp.metric}")
    print(f"  Baseline:   {exp.baseline_value:.4f}")
    print(f"  Target:     {exp.target_value:.4f}")
    print(f"  Status:     {exp.status}")
    print(f"  Result:     {exp.result or '(pending)'}")
    print(f"  Started:    {exp.started_at}")
    print(f"  Samples:    {len(exp.samples)}/{exp.min_samples}")
    if exp.samples:
        avg = sum(exp.samples) / len(exp.samples)
        print(f"  Avg sample: {avg:.4f}")
        print(f"  Samples:    {', '.join(f'{s:.4f}' for s in exp.samples)}")


def _handle_conclude(
    mgr: ExperimentManager, experiment_id: str, result: str
) -> None:
    if mgr.conclude(experiment_id, result):
        print(f"Experiment {experiment_id} concluded with result: {result}")
    else:
        print(f"Experiment '{experiment_id}' not found.")


def _handle_rollback(mgr: ExperimentManager, experiment_id: str) -> None:
    exp = mgr.get(experiment_id)
    if exp is None:
        print(f"Experiment '{experiment_id}' not found.")
        return

    # Roll back the recommendation
    proposals = ProposalManager()
    rec = proposals.get(exp.recommendation_id)
    if rec is None:
        print(f"Recommendation '{exp.recommendation_id}' not found.")
        return

    rollbacks = RollbackManager()
    entry = rollbacks.rollback(rec, f"Manual rollback of experiment {experiment_id}")
    proposals.update_status(rec.rec_id, "rolled_back")
    mgr.mark_rolled_back(experiment_id)

    print(f"Rolled back experiment {experiment_id}")
    print(f"  Recommendation: {rec.rec_id}")
    print(f"  Agent: {rec.target}")
    print(f"  Logged at: {entry.rolled_back_at}")

    if rollbacks.circuit_breaker_tripped():
        print()
        print("WARNING: Circuit breaker tripped! 3+ rollbacks in 7 days.")
        print("Auto-apply is now paused. Review system health before resuming.")
