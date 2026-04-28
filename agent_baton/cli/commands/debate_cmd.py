"""CLI command: ``baton debate`` — D4 Multi-Agent Debate.

Run a structured debate between 2-3 specialist agents on a high-stakes
design question, then have a moderator synthesize the result.

Examples
--------
    baton debate "Should we adopt event sourcing for the order service?"

    baton debate "Choose auth strategy" \\
        --viewpoints architect:long-term-maintainability,security-reviewer:risk-minimization \\
        --rounds 3 \\
        --moderator architect

    baton debate "Should we shard the bead store?" --summary-only --output json

This command is opt-in. Nothing in the engine auto-invokes it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_baton.core.intel.debate import (
    DEFAULT_MODERATOR,
    DEFAULT_ROUNDS,
    MAX_VIEWPOINTS,
    DebateOrchestrator,
    DebateResult,
    ViewpointSpec,
)


DEFAULT_VIEWPOINTS_RAW = (
    "architect:long-term maintainability,"
    "backend-engineer:pragmatic delivery,"
    "security-reviewer:risk minimization"
)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    p = subparsers.add_parser(
        "debate",
        help="Run a structured multi-agent debate on a design question",
        description=(
            "Dispatch 2-5 specialist agents with different framings to debate "
            "a question across N rounds, then have a moderator synthesize a "
            "recommendation. Opt-in only — never auto-invoked."
        ),
    )
    p.add_argument(
        "question",
        help="The design question to debate (quote it)",
    )
    p.add_argument(
        "--viewpoints",
        default=DEFAULT_VIEWPOINTS_RAW,
        help=(
            "Comma-separated list of agent:framing pairs "
            "(e.g. architect:long-term,backend-engineer:pragmatic-delivery). "
            f"Max {MAX_VIEWPOINTS}. Default: 3 standard viewpoints."
        ),
    )
    p.add_argument(
        "--rounds",
        type=int,
        default=DEFAULT_ROUNDS,
        help=f"Number of debate rounds (default: {DEFAULT_ROUNDS})",
    )
    p.add_argument(
        "--moderator",
        default=DEFAULT_MODERATOR,
        help=f"Moderator agent name (default: {DEFAULT_MODERATOR})",
    )
    p.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    p.add_argument(
        "--summary-only",
        action="store_true",
        help="Show only the recommendation + unresolved list (skip transcript)",
    )
    p.add_argument(
        "--db-path",
        default=None,
        help="Override the baton.db path used for debate persistence",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Use the stub runner (no real Claude dispatch). Useful for smoke tests.",
    )
    return p


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def parse_viewpoints(raw: str) -> list[ViewpointSpec]:
    """Parse ``agent1:framing1,agent2:framing2`` into a list of ViewpointSpec.

    Whitespace around tokens is stripped. Empty entries are skipped.
    Raises ``ValueError`` if any entry lacks a colon or has an empty agent.
    """
    out: list[ViewpointSpec] = []
    if not raw or not raw.strip():
        return out
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ValueError(
                f"viewpoint {chunk!r} missing ':' — expected agent:framing"
            )
        agent, framing = chunk.split(":", 1)
        agent = agent.strip()
        framing = framing.strip()
        if not agent:
            raise ValueError(f"viewpoint {chunk!r} has empty agent name")
        out.append(ViewpointSpec(agent_name=agent, framing=framing))
    return out


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def handler(args: argparse.Namespace) -> None:
    try:
        viewpoints = parse_viewpoints(args.viewpoints)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)

    if len(viewpoints) < 2:
        print("error: need at least 2 viewpoints", file=sys.stderr)
        sys.exit(2)
    if len(viewpoints) > MAX_VIEWPOINTS:
        print(
            f"error: too many viewpoints ({len(viewpoints)} > {MAX_VIEWPOINTS})",
            file=sys.stderr,
        )
        sys.exit(2)

    db_path = _resolve_db_path(args.db_path)

    if args.dry_run:
        from agent_baton.core.intel.debate import _wrap_sync, stub_runner

        runner = _wrap_sync(stub_runner)
    else:
        runner = None  # let DebateOrchestrator pick the best available

    orchestrator = DebateOrchestrator(runner=runner, db_path=db_path)
    try:
        result = orchestrator.run_debate(
            question=args.question,
            viewpoints=viewpoints,
            rounds=args.rounds,
            moderator_agent=args.moderator,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:  # noqa: BLE001
        print(f"error: debate failed: {exc}", file=sys.stderr)
        sys.exit(1)

    _emit(result, output=args.output, summary_only=args.summary_only)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _emit(result: DebateResult, *, output: str, summary_only: bool) -> None:
    if output == "json":
        payload = result.to_dict()
        if summary_only:
            payload = {
                "debate_id": payload.get("debate_id", ""),
                "question": payload.get("question", ""),
                "recommendation": payload.get("recommendation", ""),
                "unresolved": payload.get("unresolved", []),
            }
        print(json.dumps(payload, indent=2))
        return

    # text output
    lines: list[str] = []
    lines.append(f"# Debate: {result.question}")
    if result.debate_id:
        lines.append(f"debate_id: {result.debate_id}")
    lines.append("")

    if not summary_only:
        lines.append("## Transcript")
        for turn in result.transcript:
            lines.append("")
            lines.append(f"### {turn.agent_name} (round {turn.round_number})")
            lines.append(turn.content)
        lines.append("")

    lines.append("## Recommendation")
    lines.append(result.recommendation or "(no recommendation produced)")
    lines.append("")

    lines.append("## Unresolved")
    if result.unresolved:
        for item in result.unresolved:
            lines.append(f"- {item}")
    else:
        lines.append("- none")

    print("\n".join(lines))


def _resolve_db_path(override: str | None) -> str | None:
    if override:
        return override
    # Best-effort: use the project baton.db if we're inside one.
    candidate = Path.cwd() / ".claude" / "team-context" / "baton.db"
    if candidate.exists():
        return str(candidate)
    return None
