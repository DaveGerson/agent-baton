"""Dashboard generator -- produces a Markdown usage dashboard from JSONL logs.

The dashboard is the human-facing summary of the observe layer.  It
aggregates data from :class:`~agent_baton.core.observe.usage.UsageLogger`
and :class:`~agent_baton.core.observe.telemetry.AgentTelemetry` into a
single Markdown document covering cost trends, agent utilization, retry
rates, model mix, risk distribution, sequencing modes, and (optionally)
telemetry event breakdowns.

The generated dashboard is written to
``.claude/team-context/usage-dashboard.md`` by default and is intended for
periodic review by the human operator to spot trends before the automated
:mod:`~agent_baton.core.learn` and :mod:`~agent_baton.core.improve`
subsystems flag them.
"""
from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

from agent_baton.core.observe.telemetry import AgentTelemetry
from agent_baton.core.observe.usage import UsageLogger
from agent_baton.models.usage import TaskUsageRecord

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from agent_baton.core.storage.protocol import StorageBackend


class DashboardGenerator:
    """Generate a markdown usage dashboard from JSONL logs and/or SQLite storage.

    Produces .claude/team-context/usage-dashboard.md with cost trends,
    agent utilization, retry rates, and model mix.  When *telemetry* is
    supplied (or the default telemetry.jsonl exists), a telemetry summary
    section is appended.

    When *storage* is provided (a :class:`StorageBackend` instance), usage
    records are read from it and merged with any JSONL-file records.
    Records that exist in both sources are deduplicated by task_id, with
    the SQLite version taking precedence.
    """

    def __init__(
        self,
        usage_logger: UsageLogger | None = None,
        telemetry: AgentTelemetry | None = None,
        storage: "StorageBackend | None" = None,
    ) -> None:
        self._usage = usage_logger or UsageLogger()
        self._telemetry = telemetry or AgentTelemetry()
        self._storage = storage

    def _read_records(self) -> list[TaskUsageRecord]:
        """Return usage records, merging JSONL and SQLite sources.

        When a storage backend is available its records take precedence.
        Any task_ids present in the backend are excluded from the JSONL
        result to avoid double-counting.
        """
        storage_records: list[TaskUsageRecord] = []
        if self._storage is not None:
            try:
                storage_records = self._storage.read_usage()
            except Exception:
                logger.warning(
                    "Failed to read usage records from storage backend — falling back to JSONL only",
                    exc_info=True,
                )
                storage_records = []

        jsonl_records = self._usage.read_all()

        if not storage_records:
            return jsonl_records

        # Deduplicate: storage wins; exclude task_ids already in storage.
        storage_task_ids = {r.task_id for r in storage_records}
        jsonl_only = [r for r in jsonl_records if r.task_id not in storage_task_ids]
        return storage_records + jsonl_only

    def generate(self) -> str:
        """Generate the full dashboard as a Markdown string.

        Reads all usage records (merging JSONL and SQLite sources when a
        storage backend is configured) and all telemetry events, then
        computes aggregate metrics and formats them into titled sections
        with Markdown tables.

        Sections produced:

        * **Overview** -- total tasks, agent uses, tokens, retry rate, gate
          pass rate.
        * **Outcomes** -- distribution of task outcomes (e.g. SHIP, FAIL).
        * **Risk Distribution** -- tasks per risk level.
        * **Model Mix** -- model usage counts.
        * **Agent Utilization** -- per-agent use count and average retries.
        * **Sequencing Modes** -- tasks per sequencing mode.
        * **Telemetry** (optional) -- event counts by agent and type, plus
          files read/written, when telemetry data is available.

        Returns:
            A complete Markdown document string.  Returns a short
            placeholder message if no usage data exists yet.
        """
        records = self._read_records()
        if not records:
            return "# Usage Dashboard\n\nNo usage data available yet.\n"

        total_tasks = len(records)
        total_agents = sum(len(r.agents_used) for r in records)
        total_tokens = sum(
            a.estimated_tokens for r in records for a in r.agents_used
        )
        total_retries = sum(
            a.retries for r in records for a in r.agents_used
        )
        total_gates_passed = sum(r.gates_passed for r in records)
        total_gates_failed = sum(r.gates_failed for r in records)

        # Agent frequency
        agent_counter: Counter[str] = Counter()
        for r in records:
            for a in r.agents_used:
                agent_counter[a.name] += 1

        # Model mix
        model_counter: Counter[str] = Counter()
        for r in records:
            for a in r.agents_used:
                model_counter[a.model] += 1

        # Risk distribution
        risk_counter: Counter[str] = Counter()
        for r in records:
            risk_counter[r.risk_level] += 1

        # Outcome distribution
        outcome_counter: Counter[str] = Counter()
        for r in records:
            if r.outcome:
                outcome_counter[r.outcome] += 1

        # Sequencing mode distribution
        seq_counter: Counter[str] = Counter()
        for r in records:
            seq_counter[r.sequencing_mode] += 1

        # Per-agent retry rates
        agent_retries: dict[str, list[int]] = {}
        for r in records:
            for a in r.agents_used:
                agent_retries.setdefault(a.name, []).append(a.retries)

        avg_agents = total_agents / total_tasks if total_tasks else 0
        avg_retries = total_retries / total_agents if total_agents else 0
        gate_total = total_gates_passed + total_gates_failed
        gate_pass_pct = (
            f"{total_gates_passed / gate_total:.0%}" if gate_total else "n/a"
        )

        lines = [
            "# Usage Dashboard",
            "",
            f"*{total_tasks} tasks tracked*",
            "",
            "## Overview",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Total tasks | {total_tasks} |",
            f"| Total agent uses | {total_agents} |",
            f"| Estimated tokens | {total_tokens:,} |",
            f"| Avg agents/task | {avg_agents:.1f} |",
            f"| Avg retries/agent | {avg_retries:.2f} |",
            f"| Gate pass rate | {gate_pass_pct} |",
            "",
            "## Outcomes",
            "",
            "| Outcome | Count |",
            "|---------|-------|",
        ]
        for outcome, count in outcome_counter.most_common():
            lines.append(f"| {outcome} | {count} |")

        lines.extend([
            "",
            "## Risk Distribution",
            "",
            "| Risk Level | Tasks |",
            "|------------|-------|",
        ])
        for risk in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
            if risk in risk_counter:
                lines.append(f"| {risk} | {risk_counter[risk]} |")

        lines.extend([
            "",
            "## Model Mix",
            "",
            "| Model | Uses |",
            "|-------|------|",
        ])
        for model, count in model_counter.most_common():
            lines.append(f"| {model} | {count} |")

        lines.extend([
            "",
            "## Agent Utilization",
            "",
            "| Agent | Uses | Avg Retries |",
            "|-------|------|-------------|",
        ])
        for name, count in agent_counter.most_common():
            retries = agent_retries.get(name, [])
            avg_r = sum(retries) / len(retries) if retries else 0
            lines.append(f"| {name} | {count} | {avg_r:.1f} |")

        lines.extend([
            "",
            "## Sequencing Modes",
            "",
            "| Mode | Tasks |",
            "|------|-------|",
        ])
        for mode, count in seq_counter.most_common():
            lines.append(f"| {mode} | {count} |")

        lines.append("")

        # ── Telemetry summary ─────────────────────────────────────────────
        try:
            tel_summary = self._telemetry.summary()
        except Exception:
            logger.warning(
                "Failed to retrieve telemetry summary — telemetry section omitted from dashboard",
                exc_info=True,
            )
            tel_summary = None

        if tel_summary and tel_summary.get("total_events", 0) > 0:
            lines.extend([
                "## Telemetry",
                "",
                "| Metric | Value |",
                "|--------|-------|",
                f"| Total events | {tel_summary['total_events']} |",
                f"| Files read | {len(tel_summary['files_read'])} |",
                f"| Files written | {len(tel_summary['files_written'])} |",
                "",
                "### Events by Agent",
                "",
                "| Agent | Events |",
                "|-------|--------|",
            ])
            for agent, count in sorted(
                tel_summary["events_by_agent"].items(),
                key=lambda kv: kv[1],
                reverse=True,
            ):
                lines.append(f"| {agent} | {count} |")

            lines.extend([
                "",
                "### Events by Type",
                "",
                "| Type | Count |",
                "|------|-------|",
            ])
            for etype, count in sorted(
                tel_summary["events_by_type"].items(),
                key=lambda kv: kv[1],
                reverse=True,
            ):
                lines.append(f"| {etype} | {count} |")

            lines.append("")

        return "\n".join(lines)

    def write(self, path: Path | None = None) -> Path:
        """Write the dashboard to disk.

        Args:
            path: Destination path.  Defaults to
                ``.claude/team-context/usage-dashboard.md``.

        Returns:
            Absolute path to the written file.
        """
        out_path = (path or Path(".claude/team-context/usage-dashboard.md")).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(self.generate(), encoding="utf-8")
        return out_path
