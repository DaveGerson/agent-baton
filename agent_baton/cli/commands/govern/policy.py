"""``baton policy`` -- list, show, or evaluate guardrail policy presets.

Policy presets define rules that constrain agent behaviour (allowed file
paths, permitted tools, required review patterns).  This command lists
available presets, shows their rules, and evaluates an agent against a
preset to check for violations.

Display modes:
    * ``baton policy`` -- List all available presets with descriptions.
    * ``baton policy --show NAME`` -- Show rules of a specific preset.
    * ``baton policy --check AGENT --preset NAME`` -- Evaluate an agent
      against a preset and report violations.

Delegates to:
    :class:`~agent_baton.core.govern.policy.PolicyEngine`
"""
from __future__ import annotations

import argparse

from agent_baton.core.govern.policy import PolicyEngine


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("policy", help="List or evaluate guardrail policy presets")
    p.add_argument(
        "--show", metavar="NAME", default=None,
        help="Show rules of a named policy preset",
    )
    p.add_argument(
        "--check", metavar="AGENT", default=None,
        help="Agent name to evaluate (use with --preset)",
    )
    p.add_argument(
        "--preset", metavar="NAME", default=None,
        help="Policy preset name to evaluate against (use with --check)",
    )
    p.add_argument(
        "--paths", metavar="PATHS", default=None,
        help="Comma-separated allowed file paths for the agent (used with --check)",
    )
    p.add_argument(
        "--tools", metavar="TOOLS", default=None,
        help="Comma-separated tools available to the agent (used with --check)",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    engine = PolicyEngine()

    if args.show:
        preset = engine.load_preset(args.show)
        if preset is None:
            print(f"Policy preset '{args.show}' not found.")
            return
        print(f"Policy: {preset.name}")
        print(f"Description: {preset.description}")
        print(f"Rules ({len(preset.rules)}):")
        for rule in preset.rules:
            print(f"  [{rule.rule_type}/{rule.severity}] {rule.name}: {rule.description}")
            if rule.pattern:
                print(f"    pattern: {rule.pattern}  scope: {rule.scope}")
        return

    if args.check and args.preset:
        preset = engine.load_preset(args.preset)
        if preset is None:
            print(f"Policy preset '{args.preset}' not found.")
            return
        allowed_paths = args.paths.split(",") if args.paths else []
        tools = args.tools.split(",") if args.tools else []
        violations = engine.evaluate(preset, args.check, allowed_paths, tools)
        if not violations:
            print(f"Agent '{args.check}' is compliant with preset '{args.preset}'.")
            return
        print(f"Violations for agent '{args.check}' against preset '{args.preset}':")
        for v in violations:
            severity_tag = f"[{v.rule.severity.upper()}]"
            print(f"  {severity_tag} {v.rule.name}: {v.details}")
        return

    # Default: list presets
    names = engine.list_presets()
    if not names:
        print("No policy presets found.")
        return
    print(f"Available policy presets ({len(names)}):")
    for name in names:
        preset = engine.load_preset(name)
        desc = preset.description if preset else ""
        print(f"  {name:<25} {desc}")
