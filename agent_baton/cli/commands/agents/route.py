"""``baton route`` -- route roles to agent flavors.

Detects the project stack and resolves base agent roles to their
stack-specific flavors (e.g. backend-engineer -> backend-engineer--python).

Delegates to:
    agent_baton.core.orchestration.router.AgentRouter
"""
from __future__ import annotations

import argparse
from pathlib import Path

from agent_baton.core.orchestration.registry import AgentRegistry
from agent_baton.core.orchestration.router import AgentRouter


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("route", help="Route roles to agent flavors")
    p.add_argument("roles", nargs="*", help="Base agent names to route")
    p.add_argument("--path", default=None, help="Project root path")
    return p


def handler(args: argparse.Namespace) -> None:
    registry = AgentRegistry()
    registry.load_default_paths()
    router = AgentRouter(registry)

    root = Path(args.path) if args.path else Path.cwd()
    stack = router.detect_stack(root)

    roles = args.roles or ["backend-engineer", "frontend-engineer"]
    routing = router.route_team(roles, stack)

    print(f"Stack: {stack.language or '?'}/{stack.framework or 'generic'}")
    print()
    for base, resolved in routing.items():
        marker = " *" if resolved != base else ""
        print(f"  {base:<30} → {resolved}{marker}")
