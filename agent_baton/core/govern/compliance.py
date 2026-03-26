"""Compliance report generation and persistence for auditable agent workflows.

This module produces structured compliance artifacts for tasks that involve
regulated data, PII, or other sensitive domains. Each compliance report
records the full chain of agent actions, gate results, business rules
validated, and auditor verdicts so that external auditors can trace every
change back to the responsible agent and its authorization checkpoint.

Reports are persisted as individual markdown files under
``.claude/team-context/compliance-reports/`` (configurable). The markdown
format is designed to be human-readable and version-control friendly.

Typical usage in the execution engine:

1. The planner classifies a task as HIGH or CRITICAL risk.
2. The executor creates ``ComplianceEntry`` objects for each agent dispatch.
3. After all gates pass, ``ComplianceReportGenerator.generate()`` assembles
   the report and ``save()`` writes it to disk.
4. The auditor agent reviews the report and sets ``auditor_verdict``.

**Status: Experimental** -- built and tested but not yet validated with real
usage data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from agent_baton.models.usage import TaskUsageRecord
from agent_baton.models.enums import RiskLevel


@dataclass
class ComplianceEntry:
    """A single auditable change within a compliance report.

    Each entry corresponds to one agent's contribution during the execution
    of a regulated task. Entries are ordered chronologically and together
    form the change log section of the compliance report.

    Attributes:
        agent_name: Name of the agent that performed the action.
        action: What the agent did -- typically ``"created"``,
            ``"modified"``, or ``"reviewed"``.
        files: List of file paths touched by this agent action.
        business_rules_validated: Domain-specific rules that the agent
            or gate confirmed (e.g. ``"append-only historical records"``).
        commit_hash: Git commit SHA associated with this change, if any.
        gate_result: Outcome of the gate check for this step.
            One of ``"PASS"``, ``"FAIL"``, or ``"PASS WITH NOTES"``.
        notes: Free-text notes from the agent or gate about this entry.
    """

    agent_name: str
    action: str  # "created", "modified", "reviewed"
    files: list[str] = field(default_factory=list)
    business_rules_validated: list[str] = field(default_factory=list)
    commit_hash: str = ""
    gate_result: str = ""  # "PASS", "FAIL", "PASS WITH NOTES"
    notes: str = ""


@dataclass
class ComplianceReport:
    """Structured compliance artifact for regulated-data tasks.

    A compliance report is the top-level audit document for a single
    orchestrated task. It aggregates all ``ComplianceEntry`` objects,
    records gate pass/fail statistics, and captures the auditor's final
    verdict.

    Attributes:
        task_id: Unique identifier for the orchestrated task.
        task_description: Human-readable description of what was done.
        risk_level: The risk classification applied (e.g. ``"HIGH"``).
        classification: Name of the guardrail preset that governed
            this task (e.g. ``"Regulated Data"``).
        timestamp: ISO-8601 timestamp of report generation.
        entries: Ordered list of agent actions that make up the change log.
        auditor_verdict: Final auditor decision. One of ``"SHIP"``,
            ``"SHIP WITH NOTES"``, ``"REVISE"``, or ``"BLOCK"``.
        auditor_notes: Free-text auditor commentary.
        total_gates_passed: Count of gate checks that passed.
        total_gates_failed: Count of gate checks that failed.
    """

    task_id: str
    task_description: str
    risk_level: str = "HIGH"
    classification: str = ""  # guardrail preset applied
    timestamp: str = ""
    entries: list[ComplianceEntry] = field(default_factory=list)
    auditor_verdict: str = ""  # "SHIP", "SHIP WITH NOTES", "REVISE", "BLOCK"
    auditor_notes: str = ""
    total_gates_passed: int = 0
    total_gates_failed: int = 0

    def to_markdown(self) -> str:
        """Render as audit-ready markdown."""
        lines = [
            f"# Compliance Report: {self.task_id}",
            "",
            f"**Task:** {self.task_description}",
            f"**Risk Level:** {self.risk_level}",
            f"**Classification:** {self.classification}",
            f"**Date:** {self.timestamp or datetime.now().isoformat()}",
            f"**Auditor Verdict:** {self.auditor_verdict or 'Pending'}",
            "",
        ]
        if self.auditor_notes:
            lines.extend(["## Auditor Notes", self.auditor_notes, ""])

        lines.extend([
            "## Change Log",
            "",
            "| Agent | Action | Files | Gate | Commit |",
            "|-------|--------|-------|------|--------|",
        ])
        for e in self.entries:
            files_str = ", ".join(e.files[:3])
            if len(e.files) > 3:
                files_str += f" (+{len(e.files) - 3})"
            lines.append(
                f"| {e.agent_name} | {e.action} | {files_str} |"
                f" {e.gate_result} | {e.commit_hash[:7] if e.commit_hash else '-'} |"
            )
        lines.append("")

        # Business rules section
        all_rules: list[str] = []
        for e in self.entries:
            all_rules.extend(e.business_rules_validated)
        if all_rules:
            lines.extend(["## Business Rules Validated", ""])
            for rule in sorted(set(all_rules)):
                lines.append(f"- {rule}")
            lines.append("")

        lines.extend([
            "## Gate Summary",
            f"- Gates passed: {self.total_gates_passed}",
            f"- Gates failed: {self.total_gates_failed}",
            "",
        ])

        # Notes from individual entries
        entry_notes = [(e.agent_name, e.notes) for e in self.entries if e.notes]
        if entry_notes:
            lines.extend(["## Agent Notes", ""])
            for agent, note in entry_notes:
                lines.append(f"- **{agent}:** {note}")
            lines.append("")

        return "\n".join(lines)


class ComplianceReportGenerator:
    """Generate, persist, and retrieve compliance reports.

    The generator assembles ``ComplianceReport`` objects from task execution
    data and writes them as markdown files to a reports directory. Reports
    can be listed, loaded by task ID, and filtered to recent entries.

    The default storage location is
    ``.claude/team-context/compliance-reports/``, which is created on first
    write. Each report is named ``<task_id>.md`` with path-unsafe characters
    replaced by hyphens.
    """

    def __init__(self, reports_dir: Path | None = None) -> None:
        self._dir = (reports_dir or Path(".claude/team-context/compliance-reports")).resolve()

    @property
    def reports_dir(self) -> Path:
        return self._dir

    def generate(
        self,
        task_id: str,
        task_description: str,
        risk_level: str = "HIGH",
        classification: str = "",
        entries: list[ComplianceEntry] | None = None,
        auditor_verdict: str = "",
        auditor_notes: str = "",
        usage: TaskUsageRecord | None = None,
    ) -> ComplianceReport:
        """Generate a compliance report from task execution data.

        Assembles a ``ComplianceReport`` by combining task metadata,
        agent entries, auditor findings, and gate statistics from the
        usage record.

        Args:
            task_id: Unique identifier for the task.
            task_description: Human-readable description of the task.
            risk_level: Risk tier applied to this task (e.g. ``"HIGH"``).
            classification: Name of the guardrail preset.
            entries: List of ``ComplianceEntry`` objects recording each
                agent's contribution. Defaults to an empty list.
            auditor_verdict: Final auditor decision, if available.
            auditor_notes: Free-text auditor commentary.
            usage: Optional ``TaskUsageRecord`` from which gate pass/fail
                counts are extracted.

        Returns:
            A fully populated ``ComplianceReport`` ready for rendering
            or persistence.
        """
        gates_passed = 0
        gates_failed = 0
        if usage is not None:
            gates_passed = usage.gates_passed
            gates_failed = usage.gates_failed

        return ComplianceReport(
            task_id=task_id,
            task_description=task_description,
            risk_level=risk_level,
            classification=classification,
            timestamp=datetime.now().isoformat(),
            entries=entries or [],
            auditor_verdict=auditor_verdict,
            auditor_notes=auditor_notes,
            total_gates_passed=gates_passed,
            total_gates_failed=gates_failed,
        )

    def save(self, report: ComplianceReport) -> Path:
        """Write a compliance report to disk as a markdown file.

        Creates the reports directory if it does not exist. The filename
        is derived from ``report.task_id`` with slashes and spaces replaced
        by hyphens.

        Args:
            report: The ``ComplianceReport`` to persist.

        Returns:
            The ``Path`` to the written markdown file.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        safe_id = report.task_id.replace("/", "-").replace(" ", "-")
        path = self._dir / f"{safe_id}.md"
        path.write_text(report.to_markdown(), encoding="utf-8")
        return path

    def load(self, task_id: str) -> str | None:
        """Read a compliance report by task ID.

        Args:
            task_id: Identifier of the task whose report to load.

        Returns:
            The raw markdown content of the report, or ``None`` if no
            report exists for the given task ID.
        """
        safe_id = task_id.replace("/", "-").replace(" ", "-")
        path = self._dir / f"{safe_id}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def list_reports(self) -> list[Path]:
        """List all compliance report file paths, sorted by name.

        Returns:
            A sorted list of ``Path`` objects pointing to ``*.md`` files
            in the reports directory. Returns an empty list if the
            directory does not exist.
        """
        if not self._dir.is_dir():
            return []
        return sorted(self._dir.glob("*.md"))

    def list_recent(self, count: int = 5) -> list[Path]:
        """Return the N most recently created reports.

        Reports are sorted alphabetically by filename, so "most recent"
        refers to the last entries in that sort order.

        Args:
            count: Maximum number of reports to return. Defaults to 5.

        Returns:
            A list of up to ``count`` report file paths.
        """
        return self.list_reports()[-count:]
