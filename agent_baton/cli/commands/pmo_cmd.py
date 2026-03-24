"""CLI command: ``baton pmo`` — portfolio management overlay.

Subcommands
-----------
serve   Start the FastAPI server with PMO routes.
status  Print a terminal Kanban board summary.
add     Register a project with the PMO.
health  Print program health bar summary.

FastAPI and uvicorn are optional (``pip install agent-baton[api]``).
All imports of those packages are guarded inside :func:`_serve` so that
``baton --help`` works without the ``[api]`` extras installed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Register the ``pmo`` subcommand group."""
    p = subparsers.add_parser(
        "pmo",
        help="Portfolio management overlay — board, projects, health",
    )
    sub = p.add_subparsers(dest="subcommand")

    # baton pmo serve [--port PORT] [--host HOST]
    p_serve = sub.add_parser(
        "serve",
        help="Start the PMO HTTP server (requires: pip install agent-baton[api])",
    )
    p_serve.add_argument(
        "--port",
        type=int,
        default=8741,
        metavar="PORT",
        help="Port to listen on (default: 8741)",
    )
    p_serve.add_argument(
        "--host",
        default="127.0.0.1",
        metavar="HOST",
        help="Host to bind to (default: 127.0.0.1)",
    )

    # baton pmo status
    sub.add_parser(
        "status",
        help="Print a Kanban board summary of all registered projects",
    )

    # baton pmo add --id ID --name NAME --path PATH --program PROG [--color C]
    p_add = sub.add_parser(
        "add",
        help="Register a project with the PMO",
    )
    p_add.add_argument(
        "--id",
        required=True,
        dest="project_id",
        metavar="ID",
        help="Project slug identifier (e.g. nds)",
    )
    p_add.add_argument(
        "--name",
        required=True,
        metavar="NAME",
        help="Human-readable project name",
    )
    p_add.add_argument(
        "--path",
        required=True,
        metavar="PATH",
        help="Absolute filesystem path to the project root",
    )
    p_add.add_argument(
        "--program",
        required=True,
        metavar="PROGRAM",
        help="Program this project belongs to (e.g. NDS, ATL)",
    )
    p_add.add_argument(
        "--color",
        default="",
        metavar="COLOR",
        help="Optional display color for the project",
    )

    # baton pmo health
    sub.add_parser(
        "health",
        help="Print program health bar summary",
    )

    return p


# ---------------------------------------------------------------------------
# Handler dispatch
# ---------------------------------------------------------------------------


def handler(args: argparse.Namespace) -> None:
    if not hasattr(args, "subcommand") or args.subcommand is None:
        print("usage: baton pmo <subcommand>")
        print("subcommands: serve, status, add, health")
        sys.exit(1)

    if args.subcommand == "serve":
        _serve(args)
    elif args.subcommand == "status":
        _status(args)
    elif args.subcommand == "add":
        _add(args)
    elif args.subcommand == "health":
        _health(args)
    else:
        print(f"error: unknown pmo subcommand: {args.subcommand}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


def _serve(args: argparse.Namespace) -> None:
    """Start the FastAPI server with PMO routes."""
    try:
        import uvicorn  # noqa: F401 — imported for side-effect check
        from agent_baton.api.server import create_app
    except ImportError:
        print("Error: Install API extras: pip install -e '.[api]'")
        sys.exit(1)

    app = create_app(host=args.host, port=args.port)
    uvicorn.run(app, host=args.host, port=args.port)


def _status(args: argparse.Namespace) -> None:  # noqa: ARG001
    """Print a terminal Kanban board summary."""
    from agent_baton.core.pmo.scanner import PmoScanner
    from agent_baton.core.storage import get_pmo_central_store

    store = get_pmo_central_store()
    scanner = PmoScanner(store)
    config = store.load_config()

    projects = config.projects
    if not projects:
        print("PMO Board — no projects registered")
        print("Run: baton pmo add --id ID --name NAME --path PATH --program PROG")
        return

    cards = scanner.scan_all()

    print(f"PMO Board — {len(projects)} project{'s' if len(projects) != 1 else ''} registered")
    print()

    # Per-project progress bars
    for project in projects:
        project_cards = [c for c in cards if c.project_id == project.project_id]
        if not project_cards:
            _print_project_bar(project.project_id, 0, [])
            continue

        # Aggregate across all cards for this project
        total_steps = sum(c.steps_total for c in project_cards)
        done_steps = sum(c.steps_completed for c in project_cards)
        pct = int((done_steps / total_steps * 100)) if total_steps > 0 else 0

        col_counts: dict[str, int] = {}
        for c in project_cards:
            col_counts[c.column] = col_counts.get(c.column, 0) + 1

        detail_parts: list[str] = []
        if col_counts.get("executing", 0):
            n = col_counts["executing"]
            detail_parts.append(f"{n} active")
        if col_counts.get("deployed", 0):
            n = col_counts["deployed"]
            detail_parts.append(f"{n} deployed")
        if col_counts.get("awaiting_human", 0):
            n = col_counts["awaiting_human"]
            detail_parts.append(f"{n} blocked")
        if col_counts.get("validating", 0):
            n = col_counts["validating"]
            detail_parts.append(f"{n} validating")
        if col_counts.get("queued", 0):
            n = col_counts["queued"]
            detail_parts.append(f"{n} queued")

        _print_project_bar(project.project_id, pct, detail_parts)

    # Cards table
    if cards:
        print()
        print("Cards:")
        for card in cards:
            _print_card_row(card)


def _print_project_bar(project_id: str, pct: int, detail_parts: list[str]) -> None:
    """Print a single project progress bar line."""
    bar_width = 10
    filled = round(pct / 100 * bar_width)
    bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
    detail = f"({', '.join(detail_parts)})" if detail_parts else ""
    pct_str = f"{pct}%"
    print(f"  {project_id:<6}  {bar}  {pct_str:<5}  {detail}")


def _print_card_row(card) -> None:  # type: ignore[no-untyped-def]
    """Print a single card row in the cards table."""
    col = card.column
    short_id = card.card_id[:10] if len(card.card_id) > 10 else card.card_id
    title = card.title[:28] if len(card.title) > 28 else card.title
    prog = card.program
    extra = ""
    if col == "executing" and card.steps_total > 0:
        extra = f"step {card.steps_completed}/{card.steps_total}"
    elif col == "awaiting_human":
        extra = "waiting"
    elif col == "deployed":
        extra = "complete"
    elif col == "validating":
        extra = "validating"

    print(f"  {col:<18}  {short_id:<12}  {title!r:<32}  {prog:<6}  {extra}")


def _add(args: argparse.Namespace) -> None:
    """Register a project with the PMO."""
    from agent_baton.core.storage import get_pmo_central_store
    from agent_baton.models.pmo import PmoProject

    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"error: path does not exist: {project_path}")
        sys.exit(1)
    if not project_path.is_dir():
        print(f"error: path is not a directory: {project_path}")
        sys.exit(1)

    # Ensure .claude/team-context/ exists so baton can write plans there later
    team_context = project_path / ".claude" / "team-context"
    team_context.mkdir(parents=True, exist_ok=True)

    project = PmoProject(
        project_id=args.project_id,
        name=args.name,
        path=str(project_path),
        program=args.program,
        color=args.color,
    )

    store = get_pmo_central_store()
    store.register_project(project)

    print(f"Registered project: {project.project_id} ({project.name})")
    print(f"  Path:    {project.path}")
    print(f"  Program: {project.program}")
    if project.color:
        print(f"  Color:   {project.color}")
    print(f"  Context: {team_context}")


def _health(args: argparse.Namespace) -> None:  # noqa: ARG001
    """Print program health bar summary."""
    from agent_baton.core.pmo.scanner import PmoScanner
    from agent_baton.core.storage import get_pmo_central_store

    store = get_pmo_central_store()
    scanner = PmoScanner(store)
    health_map = scanner.program_health()

    if not health_map:
        print("No programs registered.")
        print("Run: baton pmo add --id ID --name NAME --path PATH --program PROG")
        return

    print("Program Health")
    print()

    bar_width = 20
    for program, h in sorted(health_map.items()):
        pct = h.completion_pct
        filled = round(pct / 100 * bar_width)
        bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
        pct_str = f"{pct:.0f}%"

        detail_parts: list[str] = []
        if h.active:
            detail_parts.append(f"{h.active} active")
        if h.completed:
            detail_parts.append(f"{h.completed} complete")
        if h.blocked:
            detail_parts.append(f"{h.blocked} blocked")
        if h.failed:
            detail_parts.append(f"{h.failed} failed")

        detail = f"({', '.join(detail_parts)})" if detail_parts else "(no plans)"
        print(f"  {program:<8}  {bar}  {pct_str:<6}  {detail}")
