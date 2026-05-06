"""``baton metrics show`` -- four read-side performance metrics (H3.5).

Surfaces the four metrics introduced by H3.5:

* ``spec_effectiveness`` — first-pass spec→ship rate.
* ``delegation_roi`` — minutes saved per agent.
* ``knowledge_contribution`` — per-doc impact score.
* ``review_quality`` — reviewer verdict distribution.

Usage examples::

    baton metrics show
    baton metrics show --metric spec_effectiveness
    baton metrics show --format json

Note on naming: the H3.5 spec calls for ``baton improve metrics show``.
The agent-baton CLI is flat (one module = one top-level subcommand), and
``improve`` is already taken by the improvement-cycle command.  To keep
the literal command-line stable while honouring the spec's intent, this
module registers the top-level alias ``metrics``; running
``baton metrics show`` produces exactly the report the spec describes.

Delegates to:
    agent_baton.core.improve.new_metrics
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from pathlib import Path

from agent_baton.core.improve.new_metrics import (
    AgentROI,
    DocContribution,
    ReviewerStats,
    SpecEffectivenessReport,
    compute_all_metrics,
    compute_delegation_roi,
    compute_knowledge_contribution,
    compute_review_quality,
    compute_spec_effectiveness,
    to_json,
    to_jsonable,
)


_METRIC_NAMES = (
    "spec_effectiveness",
    "delegation_roi",
    "knowledge_contribution",
    "review_quality",
)


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "metrics",
        help="H3.5 performance metrics (spec / delegation / knowledge / review)",
    )
    sub = p.add_subparsers(dest="metrics_command", metavar="COMMAND")

    show_p = sub.add_parser(
        "show",
        help="Show one or all four H3.5 metrics",
    )
    show_p.add_argument(
        "--metric",
        choices=_METRIC_NAMES,
        default=None,
        help="Restrict output to a single metric (default: show all four).",
    )
    show_p.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (default: markdown).",
    )
    show_p.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="Override project root (defaults to CWD). Used by tests.",
    )

    return p


# ---------------------------------------------------------------------------
# Markdown rendering helpers
# ---------------------------------------------------------------------------


def _render_spec_effectiveness(report: SpecEffectivenessReport) -> str:
    lines: list[str] = ["## Spec effectiveness"]
    lines.append("")
    lines.append(
        f"- Project: `{report.project_id}`"
    )
    lines.append(
        f"- Total spec-linked tasks: **{report.total_specs}**"
    )
    lines.append(
        f"- First-pass complete: **{report.complete_first_pass}**"
    )
    lines.append(f"- First-pass rate: **{report.rate:.2%}**")
    period = report.sample_period
    if period[0] and period[1]:
        lines.append(
            f"- Sample period: {period[0].isoformat()} → {period[1].isoformat()}"
        )
    if report.per_author:
        lines.append("")
        lines.append("| Author | Specs | First-pass | Rate |")
        lines.append("|---|---:|---:|---:|")
        for a in report.per_author:
            lines.append(
                f"| {a.author} | {a.total_specs} | {a.complete_first_pass} | {a.rate:.2%} |"
            )
    return "\n".join(lines)


def _render_delegation_roi(rows: list[AgentROI]) -> str:
    lines = ["## Delegation ROI"]
    if not rows:
        lines.append("")
        lines.append("_No agent dispatches recorded._")
        return "\n".join(lines)
    lines.append("")
    lines.append("| Agent | Dispatches | Accepted | Revised | Rejected | ROI (min) |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for r in rows:
        lines.append(
            f"| {r.agent_name} | {r.total_dispatches} | {r.accepted} | "
            f"{r.revised} | {r.rejected} | {r.roi_minutes:.1f} |"
        )
    return "\n".join(lines)


def _render_knowledge_contribution(rows: list[DocContribution]) -> str:
    lines = ["## Knowledge contribution"]
    if not rows:
        lines.append("")
        lines.append("_No knowledge_telemetry rows recorded._")
        return "\n".join(lines)
    lines.append("")
    lines.append("| Pack | Doc | Attachments | Successes | Score |")
    lines.append("|---|---|---:|---:|---:|")
    for r in rows:
        pack = r.pack or "(unscoped)"
        lines.append(
            f"| {pack} | {r.doc} | {r.attachment_count} | {r.success_count} | "
            f"{r.contribution_score:.2%} |"
        )
    return "\n".join(lines)


def _render_review_quality(rows: list[ReviewerStats]) -> str:
    lines = ["## Review quality"]
    if not rows:
        lines.append("")
        lines.append("_No reviewer dispatches recorded._")
        return "\n".join(lines)
    lines.append("")
    lines.append(
        "| Reviewer | APPROVE | FLAG | BLOCK | UNKNOWN | "
        "Approve % | Block % | Avg min |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        v = r.verdicts
        lines.append(
            f"| {r.reviewer} | {v.get('APPROVE', 0)} | {v.get('FLAG', 0)} | "
            f"{v.get('BLOCK', 0)} | {v.get('UNKNOWN', 0)} | "
            f"{r.approve_rate:.2%} | {r.block_rate:.2%} | {r.avg_minutes:.1f} |"
        )
    return "\n".join(lines)


def _render_all_markdown(payload: dict) -> str:
    parts = [
        "# H3.5 performance metrics",
        "",
        _render_spec_effectiveness(payload["spec_effectiveness"]),
        "",
        _render_delegation_roi(payload["delegation_roi"]),
        "",
        _render_knowledge_contribution(payload["knowledge_contribution"]),
        "",
        _render_review_quality(payload["review_quality"]),
        "",
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def _compute_one(metric: str, project_root: Path | None):
    if metric == "spec_effectiveness":
        return compute_spec_effectiveness(project_root=project_root)
    if metric == "delegation_roi":
        return compute_delegation_roi(project_root=project_root)
    if metric == "knowledge_contribution":
        return compute_knowledge_contribution(project_root=project_root)
    if metric == "review_quality":
        return compute_review_quality(project_root=project_root)
    raise ValueError(f"Unknown metric: {metric}")


def _render_one_markdown(metric: str, value) -> str:
    if metric == "spec_effectiveness":
        return _render_spec_effectiveness(value)
    if metric == "delegation_roi":
        return _render_delegation_roi(value)
    if metric == "knowledge_contribution":
        return _render_knowledge_contribution(value)
    if metric == "review_quality":
        return _render_review_quality(value)
    raise ValueError(f"Unknown metric: {metric}")


def handler(args: argparse.Namespace) -> None:
    sub = getattr(args, "metrics_command", None)
    if sub is None or sub == "show":
        _handle_show(args)
        return
    raise SystemExit(f"unknown metrics subcommand: {sub}")


def _handle_show(args: argparse.Namespace) -> None:
    project_root = getattr(args, "project_root", None)
    metric = getattr(args, "metric", None)
    fmt = getattr(args, "format", "markdown")

    if metric is None:
        payload = compute_all_metrics(project_root=project_root)
        if fmt == "json":
            print(to_json(payload))
        else:
            print(_render_all_markdown(payload))
        return

    value = _compute_one(metric, project_root)
    if fmt == "json":
        print(to_json(value))
    else:
        print(_render_one_markdown(metric, value))
