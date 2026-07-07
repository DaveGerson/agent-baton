"""``baton agents`` -- list available agents.

Loads agent definitions from default paths and displays them grouped
by category with model tags and flavor indicators. This is the default
(no-subcommand) behavior of the shared ``baton agents`` parser; see
``agents/__init__.py`` for the cooperative-parser convention and
``doctor_cmd.py`` for ``baton agents doctor``.

Delegates to:
    agent_baton.core.orchestration.registry.AgentRegistry
"""
from __future__ import annotations

import argparse

from agent_baton.cli.commands.agents import ensure_parent_parser, register_handler
from agent_baton.core.orchestration.registry import AgentRegistry


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    ensure_parent_parser(subparsers)
    register_handler(None, _run_list)
    return subparsers.choices["agents"]


def handler(args: argparse.Namespace) -> None:
    """Auto-discovery entry point; delegate to the parent dispatcher."""
    dispatch = getattr(args, "_dispatch", None)
    if dispatch is None:
        raise SystemExit("baton agents: dispatcher missing")
    dispatch(args)


def _run_list(args: argparse.Namespace) -> None:
    registry = AgentRegistry()
    count = registry.load_default_paths()

    if count == 0:
        print("No agents found. Run scripts/install.sh to install.")
        return

    # Group by category
    by_category: dict[str, list[str]] = {}
    for agent in registry.agents.values():
        cat = agent.category.value
        by_category.setdefault(cat, []).append(agent.name)

    for category, names in sorted(by_category.items()):
        print(f"\n{category}:")
        for name in sorted(names):
            agent = registry.get(name)
            assert agent is not None
            model_tag = f"[{agent.model}]"
            flavor_tag = f" (flavor: {agent.flavor})" if agent.is_flavored else ""
            print(f"  {name:<35} {model_tag:<10}{flavor_tag}")

    print(f"\n{count} agents loaded.")
