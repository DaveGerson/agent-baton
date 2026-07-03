"""Fixture tests for app.reporting.service.

Exercised only as fixture data by tests/e2e/test_manager_mode_planning.py
(via IntelligentPlanner's repo-signal detection) -- never collected
directly by the real agent-baton test suite (see
tests/fixtures/medium_project_repo/conftest.py's collect_ignore_glob).
"""
from __future__ import annotations

from app.reporting.service import ReportRecord, ReportingService


def test_summarize_empty() -> None:
    service = ReportingService()
    summary = service.summarize()
    assert summary.total == 0
    assert summary.completed == 0
    assert summary.failed == 0


def test_summarize_mixed_statuses() -> None:
    service = ReportingService()
    service.record(ReportRecord(task_id="t1", status="completed", duration_seconds=2.0))
    service.record(ReportRecord(task_id="t2", status="failed", duration_seconds=4.0))
    service.record(ReportRecord(task_id="t3", status="completed", duration_seconds=3.0))

    summary = service.summarize()

    assert summary.total == 3
    assert summary.completed == 2
    assert summary.failed == 1
    assert summary.average_duration_seconds == 3.0


def test_clear_resets_records() -> None:
    service = ReportingService()
    service.record(ReportRecord(task_id="t1", status="completed"))
    service.clear()

    assert service.summarize().total == 0
