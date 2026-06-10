"""``baton classify`` -- classify task sensitivity and select guardrail preset.

Analyses a task description (and optionally affected file paths) to
determine the risk level, confidence, and appropriate guardrail preset.

Delegates to:
    agent_baton.core.govern.classifier.DataClassifier

``--activate`` additionally writes ``.claude/active-policy.json`` with the
resolved PolicyEngine key so that ``baton policy-check`` hook enforcement
uses the correct preset for the current work session.
"""
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
    p.add_argument(
        "--activate",
        action="store_true",
        default=False,
        help=(
            "Write .claude/active-policy.json with the resolved preset key so "
            "that baton policy-check hook enforcement uses the correct preset."
        ),
    )
    return p


def handler(args: argparse.Namespace) -> None:
    from pathlib import Path

    # Build a pack-aware classifier and register pack policies so that
    # classify_to_preset_key / load_preset resolves pack presets.
    try:
        from agent_baton.core.govern.packs import (
            load_packs,
            make_classifier_for_packs,
            register_pack_policies,
        )
        _packs = load_packs(Path.cwd())
        register_pack_policies(_packs)
        classifier = make_classifier_for_packs(_packs)
    except Exception:
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

    if getattr(args, "activate", False):
        _write_active_policy(args.description, result)


def _write_active_policy(description: str, result) -> None:  # type: ignore[type-arg]
    """Write .claude/active-policy.json with the resolved preset key."""
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    from agent_baton.core.engine.planning.utils.risk_and_policy import (
        classify_to_preset_key,
    )

    preset_key = classify_to_preset_key(result)

    active_policy = {
        "preset": preset_key,
        "preset_display_name": result.guardrail_preset,
        "risk_level": result.risk_level.value,
        "confidence": result.confidence,
        "signals": result.signals_found,
        "activated_at": datetime.now(tz=timezone.utc).isoformat(),
        "activated_by": "baton classify --activate",
        "task_hint": description[:120],
    }

    out_path = Path(".claude") / "active-policy.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(active_policy, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Active policy written to {out_path} (preset: {preset_key})")
