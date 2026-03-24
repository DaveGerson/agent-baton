"""ComplianceReportGenerator — generate and manage compliance artifacts.

**Status: Experimental** — built and tested but not yet validated with real usage data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from agent_baton.models.usage import TaskUsageRecord
from agent_baton.models.enums import RiskLevel


@dataclass
class ComplianceEntry:
    """A single auditable change within a compliance report."""

    agent_name: str
    action: str  # "created", "modified", "reviewed"
    files: list[str] = field(default_factory=list)
    business_rules_validated: list[str] = field(default_factory=list)
    commit_hash: str = ""
    gate_result: str = ""  # "PASS", "FAIL", "PASS WITH NOTES"
    notes: str = ""


@dataclass
class ComplianceReport:
    """Structured compliance artifact for regulated-data tasks."""

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
    """Generate and manage compliance reports."""

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
        """Generate a compliance report from task data."""
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
        """Write report to disk as markdown. Returns the path written."""
        self._dir.mkdir(parents=True, exist_ok=True)
        safe_id = report.task_id.replace("/", "-").replace(" ", "-")
        path = self._dir / f"{safe_id}.md"
        path.write_text(report.to_markdown(), encoding="utf-8")
        return path

    def load(self, task_id: str) -> str | None:
        """Read a compliance report by task ID. Returns markdown content or None."""
        safe_id = task_id.replace("/", "-").replace(" ", "-")
        path = self._dir / f"{safe_id}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def list_reports(self) -> list[Path]:
        """List all compliance reports sorted by name."""
        if not self._dir.is_dir():
            return []
        return sorted(self._dir.glob("*.md"))

    def list_recent(self, count: int = 5) -> list[Path]:
        """Return the N most recent reports."""
        return self.list_reports()[-count:]
