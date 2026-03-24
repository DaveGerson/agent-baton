"""baton classify — classify task sensitivity and select guardrail preset."""
from __future__ import annotations

import argparse

from agent_baton.core.govern.classifier import DataClassifier


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "classify", help="Classify task sensitivity and select guardrail preset"
    )
    p.add_argument(
        "description",
        help="Task description to classify",
    )
    p.add_argument(
        "--files",
        nargs="*",
        metavar="FILE",
        help="File paths affected by the task (used to elevate risk from path patterns)",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    classifier = DataClassifier()
    file_paths: list[str] | None = args.files if args.files else None
    result = classifier.classify(args.description, file_paths)

    print(f"Risk Level: {result.risk_level.value}")
    print(f"Preset: {result.guardrail_preset}")
    print(f"Confidence: {result.confidence}")
    if result.signals_found:
        print(f"Signals: {', '.join(result.signals_found)}")
    if result.explanation:
        print(f"Explanation: {result.explanation}")
