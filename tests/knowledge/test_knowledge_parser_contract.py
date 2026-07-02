"""Parser contract tests for the shared ``baton knowledge`` command tree."""
from __future__ import annotations

import argparse

from agent_baton.cli.main import discover_commands


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="baton")
    subparsers = parser.add_subparsers(dest="command")
    for mod in discover_commands().values():
        mod.register(subparsers)
    return parser


def _knowledge_subparser(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction:  # type: ignore[type-arg]
    top_level = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    knowledge = top_level.choices["knowledge"]
    nested = [
        action
        for action in knowledge._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    assert len(nested) == 1
    return nested[0]


def test_knowledge_parent_uses_shared_dest_and_all_subcommands() -> None:
    parser = _build_parser()
    sub = _knowledge_subparser(parser)

    assert sub.dest == "knowledge_cmd"
    assert {
        "ab",
        "brief",
        "doctor",
        "effectiveness",
        "harvest",
        "ranking",
        "resolve",
        "search",
        "usage",
        "stale",
        "sweep",
        "deprecate",
        "retire",
    } <= set(sub.choices)


def test_parse_knowledge_ab_keeps_nested_ab_dest() -> None:
    parser = _build_parser()

    args = parser.parse_args(["knowledge", "ab", "list"])

    assert args.command == "knowledge"
    assert args.knowledge_cmd == "ab"
    assert args.ab_subcommand == "list"


def test_parse_knowledge_doctor_contract() -> None:
    parser = _build_parser()

    args = parser.parse_args([
        "knowledge",
        "doctor",
        "--knowledge-root",
        "X",
        "--format",
        "json",
        "--strict",
    ])

    assert args.knowledge_cmd == "doctor"
    assert args.knowledge_root == ["X"]
    assert args.format == "json"
    assert args.strict is True


def test_parse_knowledge_search_contract() -> None:
    parser = _build_parser()

    args = parser.parse_args([
        "knowledge",
        "search",
        "auth middleware",
        "--knowledge-root",
        "X",
        "--limit",
        "5",
        "--format",
        "json",
    ])

    assert args.knowledge_cmd == "search"
    assert args.query == ["auth middleware"]
    assert args.knowledge_root == ["X"]
    assert args.limit == 5
    assert args.format == "json"


def test_parse_knowledge_resolve_contract() -> None:
    parser = _build_parser()

    args = parser.parse_args([
        "knowledge",
        "resolve",
        "--agent",
        "backend-engineer--python",
        "--task",
        "Fix auth middleware",
        "--task-type",
        "bug-fix",
        "--risk",
        "HIGH",
        "--knowledge-pack",
        "security",
        "--knowledge",
        "docs/auth.md",
        "--format",
        "json",
    ])

    assert args.knowledge_cmd == "resolve"
    assert args.agent == "backend-engineer--python"
    assert args.task == "Fix auth middleware"
    assert args.task_type == "bug-fix"
    assert args.risk == "HIGH"
    assert args.knowledge_pack == ["security"]
    assert args.knowledge == ["docs/auth.md"]
    assert args.format == "json"
