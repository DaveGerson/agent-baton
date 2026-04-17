"""``baton evolve`` -- analyse agent performance and propose prompt improvements.

.. deprecated::
    ``baton evolve`` is deprecated. Use ``baton learn run-cycle`` instead.
    The learning-cycle pipeline dispatches the ``learning-analyst`` agent which
    reads actual retrospectives rather than matching scorecard thresholds to
    generic suggestion templates.

The evolution engine identifies underperforming agents and generates
prompt improvement proposals based on scorecard thresholds.

Delegates to:
    agent_baton.core.improve.evolution.PromptEvolutionEngine
"""
from __future__ import annotations

import argparse
import warnings


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "evolve",
        help=(
            "[DEPRECATED] Propose prompt improvements for underperforming agents. "
            "Use 'baton learn run-cycle' instead."
        ),
    )
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--agent", metavar="NAME", help="Show proposal for a specific agent",
    )
    group.add_argument(
        "--save",
        action="store_true",
        help="Write proposals to .claude/team-context/evolution-proposals/",
    )
    group.add_argument(
        "--write",
        action="store_true",
        help="Write summary report to disk",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    from agent_baton.core.improve.evolution import PromptEvolutionEngine

    print(
        "Warning: 'baton evolve' is deprecated. "
        "Use 'baton learn run-cycle' instead.\n"
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        engine = PromptEvolutionEngine()

    if args.agent:
        proposal = engine.propose_for_agent(args.agent)
        if proposal is None:
            print(f"No issues found for agent '{args.agent}' (no usage data or performing well).")
            return
        print(proposal.to_markdown())
        return

    if args.save:
        proposals = engine.analyze()
        if not proposals:
            print("All agents performing well. No proposals to save.")
            return
        paths = engine.save_proposals(proposals)
        print(f"Saved {len(paths)} proposal(s):")
        for p in paths:
            print(f"  {p}")
        return

    if args.write:
        path = engine.write_report()
        print(f"Evolution report written to {path}")
        return

    # Default: print report to stdout
    print(engine.generate_report())
