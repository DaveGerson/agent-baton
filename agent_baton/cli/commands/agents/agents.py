"""baton agents — list available agents."""
from __future__ import annotations

import argparse

from agent_baton.core.orchestration.registry import AgentRegistry


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("agents", help="List available agents")
    return p


def handler(args: argparse.Namespace) -> None:
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
