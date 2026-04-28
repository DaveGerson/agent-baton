"""Wave 6.2 Part A — ``baton swarm`` CLI subcommand (bd-707d).

Exposes swarm refactoring via:

    baton swarm refactor <directive-json> [--max-agents N] [--language python]
                                          [--model claude-haiku]

The directive is passed as a JSON string to avoid argparse juggling complex
types.  Example:

    baton swarm refactor '{"kind":"rename-symbol","old":"mymod.Foo","new":"mymod.Bar"}'

Feature gate: ``BATON_SWARM_ENABLED=1`` required (in env or baton.yaml).
When disabled the command exits with a clear error message.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

_log = logging.getLogger(__name__)

_SWARM_ENABLED_ENV = "BATON_SWARM_ENABLED"
_DISABLED_MSG = (
    "Swarm is disabled; set BATON_SWARM_ENABLED=1 in baton.yaml or the "
    "BATON_SWARM_ENABLED environment variable to enable it."
)


# ---------------------------------------------------------------------------
# Registration + handler (auto-discovered by cli/main.py)
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Register the ``swarm`` subcommand and its sub-subcommands."""
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "swarm",
        help="Massive parallel AST-aware swarm refactoring (Wave 6.2, bd-707d).",
        description=(
            "Dispatch up to 100 Haiku agents in parallel to apply a single "
            "refactor directive across provably-independent code chunks.  "
            "Requires BATON_SWARM_ENABLED=1."
        ),
    )
    swarm_sub = parser.add_subparsers(dest="swarm_command", metavar="COMMAND")
    swarm_sub.required = True

    # -- refactor sub-subcommand --
    refactor_parser = swarm_sub.add_parser(
        "refactor",
        help="Apply a refactor directive via a swarm of parallel Haiku agents.",
        description=(
            "Partition the codebase into independent AST chunks and dispatch "
            "one Haiku agent per chunk.  Results are coalesced via sequential "
            "deterministic rebase."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_REFACTOR_EPILOG,
    )
    refactor_parser.add_argument(
        "directive_json",
        metavar="DIRECTIVE_JSON",
        help=(
            "JSON object describing the refactor directive.  "
            'Required keys: "kind" (rename-symbol | change-signature | '
            "replace-import | migrate-api) plus directive-specific fields.  "
            "Pass as a single-quoted string."
        ),
    )
    refactor_parser.add_argument(
        "--max-agents",
        type=int,
        default=100,
        metavar="N",
        help="Maximum number of parallel chunk agents (default: 100, max: 100).",
    )
    refactor_parser.add_argument(
        "--language",
        choices=["python"],
        default="python",
        help="Target language for AST partitioning (default: python; v1 only).",
    )
    refactor_parser.add_argument(
        "--model",
        default="claude-haiku",
        metavar="MODEL",
        help="LLM model tier for chunk agents (default: claude-haiku).",
    )
    refactor_parser.add_argument(
        "--codebase-root",
        default=None,
        metavar="PATH",
        help=(
            "Root directory of the Python project to refactor.  "
            "Defaults to the current working directory."
        ),
    )
    refactor_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Partition and print chunks without dispatching agents.",
    )

    return parser


_REFACTOR_EPILOG = """\
Examples:

  Rename a symbol:
    baton swarm refactor '{"kind":"rename-symbol","old":"mymod.OldName","new":"mymod.NewName"}'

  Replace an import:
    baton swarm refactor '{"kind":"replace-import","old":"requests","new":"httpx"}'

  Change a function signature:
    baton swarm refactor '{"kind":"change-signature","symbol":"mymod.my_func","transform":{"add_param":"timeout=30"}}'

  Migrate an API pattern:
    baton swarm refactor '{"kind":"migrate-api","old_call_pattern":"requests.get(...)","new_call_template":"httpx.get(...)"}'

Environment:
  BATON_SWARM_ENABLED=1   Required to enable swarm dispatch.
"""


def handler(args: argparse.Namespace) -> None:
    """Dispatch the appropriate swarm sub-command handler."""
    _check_enabled()
    if args.swarm_command == "refactor":
        _handle_refactor(args)
    else:
        print(f"Unknown swarm command: {args.swarm_command}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _handle_refactor(args: argparse.Namespace) -> None:
    """Parse directive JSON and dispatch the swarm."""
    # Parse directive
    try:
        directive_data = json.loads(args.directive_json)
    except json.JSONDecodeError as exc:
        print(
            f"Error: DIRECTIVE_JSON is not valid JSON: {exc}\n"
            "Pass the directive as a single-quoted JSON object.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        from agent_baton.core.swarm.partitioner import RefactorDirective
        directive = RefactorDirective.from_dict(directive_data)
    except (KeyError, ValueError) as exc:
        print(f"Error: Invalid directive: {exc}", file=sys.stderr)
        sys.exit(1)
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Validate max_agents
    max_agents = min(args.max_agents, 100)
    if max_agents < 1:
        print("Error: --max-agents must be at least 1.", file=sys.stderr)
        sys.exit(1)

    codebase_root = Path(args.codebase_root) if args.codebase_root else Path.cwd()
    if not codebase_root.is_dir():
        print(f"Error: --codebase-root {codebase_root} is not a directory.", file=sys.stderr)
        sys.exit(1)

    print(
        f"Swarm refactor starting:\n"
        f"  directive: {directive.kind}\n"
        f"  codebase_root: {codebase_root}\n"
        f"  max_agents: {max_agents}\n"
        f"  model: {args.model}\n"
        f"  language: {args.language}",
    )

    if args.dry_run:
        _dry_run_partition(directive, codebase_root, max_agents)
        return

    _dispatch_swarm(directive, codebase_root, max_agents, args.model)


def _dry_run_partition(
    directive: object,
    codebase_root: Path,
    max_agents: int,
) -> None:
    """Partition and print chunks without dispatching agents."""
    try:
        from agent_baton.core.swarm.partitioner import ASTPartitioner
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    partitioner = ASTPartitioner(codebase_root)
    try:
        chunks = partitioner.partition(directive, max_chunks=max_agents)  # type: ignore[arg-type]
    except Exception as exc:
        print(f"Error during partitioning: {exc}", file=sys.stderr)
        sys.exit(1)

    if not chunks:
        print("No call sites found — nothing to refactor.")
        return

    print(f"\n[DRY RUN] {len(chunks)} chunk(s) identified:\n")
    for i, chunk in enumerate(chunks, start=1):
        print(f"  Chunk {i}: id={chunk.chunk_id[:12]} "
              f"files={len(chunk.files)} "
              f"call_sites={len(chunk.call_sites)} "
              f"est_tokens={chunk.estimated_tokens} "
              f"proof={chunk.independence_proof.kind}")
        for f in chunk.files[:3]:
            print(f"    - {f}")
        if len(chunk.files) > 3:
            print(f"    ... and {len(chunk.files) - 3} more")

    total_tokens = sum(c.estimated_tokens for c in chunks)
    # Haiku pricing estimate
    cost_est = len(chunks) * 8_000 * (0.25 / 1_000_000) + len(chunks) * 2_000 * (1.25 / 1_000_000)
    print(f"\n  Total estimated tokens: {total_tokens:,}")
    print(f"  Estimated cost (Haiku):  ${cost_est:.4f}")
    print(f"\n[DRY RUN] Use without --dry-run to dispatch agents.")


def _dispatch_swarm(
    directive: object,
    codebase_root: Path,
    max_agents: int,
    model: str,
) -> None:
    """Build SwarmDispatcher and run the swarm."""
    try:
        from agent_baton.core.govern.budget import BudgetEnforcer
        from agent_baton.core.swarm.dispatcher import SwarmBudgetError, SwarmDispatcher
        from agent_baton.core.swarm.partitioner import ASTPartitioner
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    partitioner = ASTPartitioner(codebase_root)
    budget = BudgetEnforcer()

    # SwarmDispatcher needs an engine + worktree_mgr — wire them from the
    # project context.  For the CLI path we build minimal stubs so the
    # planner/budget path works without a full engine session.
    try:
        result = _run_with_engine(
            directive=directive,  # type: ignore[arg-type]
            partitioner=partitioner,
            budget=budget,
            max_agents=max_agents,
            model=model,
            codebase_root=codebase_root,
        )
    except SwarmBudgetError as exc:
        print(f"Budget check failed: {exc}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as exc:
        print(f"Swarm error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        _log.exception("Swarm dispatch failed")
        sys.exit(1)

    print(
        f"\nSwarm complete:\n"
        f"  swarm_id:      {result.swarm_id}\n"
        f"  succeeded:     {result.n_succeeded}\n"
        f"  failed:        {result.n_failed}\n"
        f"  total_tokens:  {result.total_tokens:,}\n"
        f"  cost_usd:      ${result.total_cost_usd:.4f}\n"
        f"  wall_clock:    {result.wall_clock_sec:.1f}s\n"
        f"  coalesce_branch: {result.coalesce_branch}"
    )
    if result.failed_chunks:
        print(f"  failed_chunks: {', '.join(c[:8] for c in result.failed_chunks)}")


def _run_with_engine(
    directive: object,
    partitioner: object,
    budget: object,
    max_agents: int,
    model: str,
    codebase_root: Path,
) -> object:
    """Construct minimal engine/worktree_mgr stubs for CLI-path swarm dispatch."""
    from agent_baton.core.engine.worktree_manager import WorktreeManager
    from agent_baton.core.swarm.dispatcher import SwarmDispatcher

    worktree_mgr = WorktreeManager(
        project_root=codebase_root,
        max_concurrent=max_agents,
    )

    # CLI path: engine is a lightweight namespace (plan synthesis only,
    # no full execution loop needed for the partition→synthesize path).
    class _CliEngine:
        _bead_store = None

    dispatcher = SwarmDispatcher(
        engine=_CliEngine(),  # type: ignore[arg-type]
        worktree_mgr=worktree_mgr,
        partitioner=partitioner,  # type: ignore[arg-type]
        budget=budget,  # type: ignore[arg-type]
    )
    return dispatcher.dispatch(
        directive=directive,  # type: ignore[arg-type]
        max_agents=max_agents,
        model=model,
    )


# ---------------------------------------------------------------------------
# Feature gate
# ---------------------------------------------------------------------------


def _check_enabled() -> None:
    """Exit with a clear error when swarm is not enabled."""
    if os.environ.get(_SWARM_ENABLED_ENV, "0").strip().lower() not in ("1", "true", "yes"):
        print(_DISABLED_MSG, file=sys.stderr)
        sys.exit(1)
