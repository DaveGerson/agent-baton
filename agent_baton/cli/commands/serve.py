"""CLI command: ``baton serve`` — start the HTTP API server.

FastAPI and uvicorn are optional dependencies (``pip install agent-baton[api]``).
All imports of those packages are guarded inside :func:`handler` so that
``baton --help`` works without the ``[api]`` extras installed.
"""
from __future__ import annotations

import argparse
import os
import sys


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Register the ``serve`` subcommand."""
    p = subparsers.add_parser(
        "serve",
        help="Start the HTTP API server (requires: pip install agent-baton[api])",
    )
    p.add_argument(
        "--port",
        type=int,
        default=8741,
        metavar="PORT",
        help="Port to listen on (default: 8741)",
    )
    p.add_argument(
        "--host",
        default="127.0.0.1",
        metavar="HOST",
        help="Host to bind to (default: 127.0.0.1)",
    )
    p.add_argument(
        "--token",
        default=None,
        metavar="TOKEN",
        help=(
            "API token for authentication. "
            "Also reads the BATON_API_TOKEN environment variable "
            "(CLI flag takes precedence)."
        ),
    )
    p.add_argument(
        "--team-context",
        dest="team_context",
        default=None,
        metavar="DIR",
        help="Path to the team-context root directory (default: .claude/team-context)",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    """Start uvicorn serving the FastAPI application.

    Imports of fastapi/uvicorn are deferred to this function so the CLI
    remains importable even when the [api] extras are not installed.
    """
    # DECISION: guard imports here rather than at module top-level so that
    # ``baton --help`` (which imports every command module to collect
    # subparsers) never fails due to missing optional dependencies.
    try:
        import uvicorn  # noqa: F401 — imported for side-effect check
        from agent_baton.api.server import create_app
    except ImportError:
        print(
            "API dependencies not installed. Run: pip install agent-baton[api]"
        )
        sys.exit(1)

    # Token resolution: explicit CLI flag wins over environment variable.
    token: str | None = args.token or os.environ.get("BATON_API_TOKEN")

    # Build keyword arguments for create_app; only pass team_context when
    # the caller supplied it so the server can apply its own default.
    create_kwargs: dict = {
        "host": args.host,
        "port": args.port,
        "token": token,
    }
    if args.team_context is not None:
        from pathlib import Path
        create_kwargs["team_context_root"] = Path(args.team_context).resolve()

    app = create_app(**create_kwargs)

    uvicorn.run(app, host=args.host, port=args.port)
