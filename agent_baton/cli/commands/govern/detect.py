"""``baton detect`` -- detect the project technology stack.

Scans the project root for language and framework indicator files
(e.g. ``package.json``, ``pyproject.toml``, ``Cargo.toml``) and
reports the detected language, framework, and signal files.

This is the same stack detection used by the planner and router to
select appropriate agent flavors.

Delegates to:
    :class:`~agent_baton.core.orchestration.router.AgentRouter`
"""
from __future__ import annotations

import argparse
from pathlib import Path

from agent_baton.core.orchestration.registry import AgentRegistry
from agent_baton.core.orchestration.router import AgentRouter


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("detect", help="Detect project stack")
    p.add_argument("--path", default=None, help="Project root path")
    return p


def handler(args: argparse.Namespace) -> None:
    registry = AgentRegistry()
    registry.load_default_paths()
    router = AgentRouter(registry)

    root = Path(args.path) if args.path else Path.cwd()
    stack = router.detect_stack(root)

    print(f"Language:  {stack.language or 'unknown'}")
    print(f"Framework: {stack.framework or 'unknown'}")
    if stack.detected_files:
        print(f"Signals:   {', '.join(stack.detected_files)}")
