"""baton detect — detect project stack."""
from __future__ import annotations

import argparse
from pathlib import Path

from agent_baton.core.registry import AgentRegistry
from agent_baton.core.router import AgentRouter


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
