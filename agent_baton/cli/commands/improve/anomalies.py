"""``baton anomalies`` -- detect and display system anomalies.

Anomalies are statistical deviations from normal agent behaviour:
sudden drops in success rate, spikes in token usage, or elevated
retry rates.

Delegates to:
    agent_baton.core.improve.triggers.TriggerEvaluator
"""
from __future__ import annotations

import argparse

from agent_baton.core.improve.triggers import TriggerEvaluator
from agent_baton.models.improvement import TriggerConfig


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "anomalies",
        help="Detect and display system anomalies",
    )
    p.add_argument(
        "--watch",
        action="store_true",
        help="Show anomaly detection status and trigger readiness",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    evaluator = TriggerEvaluator()

    if args.watch:
        _handle_watch(evaluator)
        return

    # Default: detect and display anomalies
    anomalies = evaluator.detect_anomalies()
    if not anomalies:
        print("No anomalies detected.")
        return

    print(f"Anomalies Detected ({len(anomalies)}):")
    print()
    for a in anomalies:
        severity_tag = a.severity.upper()
        print(f"  [{severity_tag}] {a.anomaly_type}")
        if a.agent_name:
            print(f"    Agent:     {a.agent_name}")
        print(f"    Metric:    {a.metric}")
        print(f"    Current:   {a.current_value:.4f}")
        print(f"    Threshold: {a.threshold:.4f}")
        if a.sample_size:
            print(f"    Samples:   {a.sample_size}")
        if a.evidence:
            for ev in a.evidence:
                print(f"    Evidence:  {ev}")
        print()


def _handle_watch(evaluator: TriggerEvaluator) -> None:
    ready = evaluator.should_analyze()
    anomalies = evaluator.detect_anomalies()

    print("Improvement System Status")
    print("=" * 40)
    print(f"  Analysis trigger ready: {'YES' if ready else 'NO'}")
    print(f"  Active anomalies:       {len(anomalies)}")

    if anomalies:
        print()
        print("Current anomalies:")
        for a in anomalies:
            severity_tag = a.severity.upper()
            agent_info = f" ({a.agent_name})" if a.agent_name else ""
            print(f"  [{severity_tag}] {a.anomaly_type}{agent_info}: {a.metric}={a.current_value:.4f}")
