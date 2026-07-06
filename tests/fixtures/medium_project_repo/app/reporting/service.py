"""Reporting service — aggregates task completion metrics for a project.

Fixture module for tests/e2e/test_manager_mode_planning.py. Deliberately
a real (not stubbed) module: manager-mode planning reads repo *paths* and
signals (allowed_paths, step deliverables, directory names), never file
contents, but a genuine module keeps the fixture representative of a real
project agent-baton would plan against.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReportRecord:
    """One completed (or failed) task, as reported to the service."""

    task_id: str
    status: str
    duration_seconds: float = 0.0


@dataclass
class ReportSummary:
    """Aggregated view over a batch of ReportRecord entries."""

    total: int = 0
    completed: int = 0
    failed: int = 0
    average_duration_seconds: float = 0.0


class ReportingService:
    """Aggregates ReportRecord entries into a ReportSummary."""

    def __init__(self) -> None:
        self._records: list[ReportRecord] = []

    def record(self, entry: ReportRecord) -> None:
        self._records.append(entry)

    def summarize(self) -> ReportSummary:
        total = len(self._records)
        completed = sum(1 for r in self._records if r.status == "completed")
        failed = sum(1 for r in self._records if r.status == "failed")
        durations = [r.duration_seconds for r in self._records]
        average = sum(durations) / total if total else 0.0
        return ReportSummary(
            total=total,
            completed=completed,
            failed=failed,
            average_duration_seconds=average,
        )

    def clear(self) -> None:
        self._records.clear()
