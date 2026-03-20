"""Tests for agent_baton.core.compliance."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.compliance import (
    ComplianceEntry,
    ComplianceReport,
    ComplianceReportGenerator,
)
from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SENTINEL: list[str] = []


def _entry(
    agent_name: str = "backend-engineer",
    action: str = "modified",
    files: list[str] | None = None,
    rules: list[str] | None = _SENTINEL,
    commit_hash: str = "abc1234567890",
    gate_result: str = "PASS",
    notes: str = "",
) -> ComplianceEntry:
    return ComplianceEntry(
        agent_name=agent_name,
        action=action,
        files=files if files is not None else ["models.py", "migrations/001.py"],
        business_rules_validated=rules if rules is not _SENTINEL else ["BR-001: No PII in logs"],
        commit_hash=commit_hash,
        gate_result=gate_result,
        notes=notes,
    )


def _report(
    task_id: str = "task-hipaa-001",
    entries: list[ComplianceEntry] | None = None,
    auditor_verdict: str = "SHIP",
    auditor_notes: str = "",
    gates_passed: int = 3,
    gates_failed: int = 0,
) -> ComplianceReport:
    return ComplianceReport(
        task_id=task_id,
        task_description="Add patient record export endpoint",
        risk_level="HIGH",
        classification="Regulated Data",
        timestamp="2026-03-20T10:00:00",
        entries=entries if entries is not None else [_entry()],
        auditor_verdict=auditor_verdict,
        auditor_notes=auditor_notes,
        total_gates_passed=gates_passed,
        total_gates_failed=gates_failed,
    )


def _usage(
    task_id: str = "task-1",
    gates_passed: int = 4,
    gates_failed: int = 1,
    risk_level: str = "HIGH",
) -> TaskUsageRecord:
    return TaskUsageRecord(
        task_id=task_id,
        timestamp="2026-03-20T09:00:00",
        agents_used=[
            AgentUsageRecord(name="subject-matter-expert", model="sonnet"),
            AgentUsageRecord(name="auditor", model="sonnet"),
        ],
        total_agents=2,
        risk_level=risk_level,
        sequencing_mode="phased_delivery",
        gates_passed=gates_passed,
        gates_failed=gates_failed,
        outcome="SHIP",
        notes="",
    )


# ---------------------------------------------------------------------------
# ComplianceEntry
# ---------------------------------------------------------------------------

class TestComplianceEntry:
    def test_defaults(self):
        e = ComplianceEntry(agent_name="arch", action="created")
        assert e.files == []
        assert e.business_rules_validated == []
        assert e.commit_hash == ""
        assert e.gate_result == ""
        assert e.notes == ""

    def test_explicit_fields(self):
        e = _entry(
            agent_name="auditor",
            action="reviewed",
            files=["report.py"],
            rules=["BR-002"],
            commit_hash="deadbeef",
            gate_result="FAIL",
            notes="Found issue",
        )
        assert e.agent_name == "auditor"
        assert e.action == "reviewed"
        assert e.files == ["report.py"]
        assert e.business_rules_validated == ["BR-002"]
        assert e.commit_hash == "deadbeef"
        assert e.gate_result == "FAIL"
        assert e.notes == "Found issue"


# ---------------------------------------------------------------------------
# ComplianceReport.to_markdown
# ---------------------------------------------------------------------------

class TestComplianceReportToMarkdown:
    def test_title_contains_task_id(self):
        md = _report("hipaa-export-v2").to_markdown()
        assert "# Compliance Report: hipaa-export-v2" in md

    def test_task_description_present(self):
        md = _report().to_markdown()
        assert "Add patient record export endpoint" in md

    def test_risk_level_present(self):
        md = _report().to_markdown()
        assert "**Risk Level:** HIGH" in md

    def test_classification_present(self):
        md = _report().to_markdown()
        assert "**Classification:** Regulated Data" in md

    def test_timestamp_present(self):
        md = _report().to_markdown()
        assert "2026-03-20T10:00:00" in md

    def test_auditor_verdict_present(self):
        md = _report(auditor_verdict="SHIP WITH NOTES").to_markdown()
        assert "SHIP WITH NOTES" in md

    def test_auditor_verdict_pending_when_empty(self):
        r = ComplianceReport(
            task_id="t1",
            task_description="desc",
            auditor_verdict="",
        )
        assert "Pending" in r.to_markdown()

    def test_auditor_notes_section_present_when_set(self):
        md = _report(auditor_notes="Review PII handling again.").to_markdown()
        assert "## Auditor Notes" in md
        assert "Review PII handling again." in md

    def test_auditor_notes_section_absent_when_empty(self):
        md = _report(auditor_notes="").to_markdown()
        assert "## Auditor Notes" not in md

    def test_change_log_table_header(self):
        md = _report().to_markdown()
        assert "## Change Log" in md
        assert "| Agent | Action | Files | Gate | Commit |" in md

    def test_entry_agent_name_in_table(self):
        md = _report(entries=[_entry(agent_name="backend-engineer")]).to_markdown()
        assert "backend-engineer" in md

    def test_entry_gate_result_in_table(self):
        md = _report(entries=[_entry(gate_result="PASS")]).to_markdown()
        assert "PASS" in md

    def test_entry_commit_hash_truncated_to_seven(self):
        md = _report(entries=[_entry(commit_hash="abc1234567890")]).to_markdown()
        assert "abc1234" in md
        assert "abc1234567890" not in md

    def test_entry_no_commit_hash_shows_dash(self):
        md = _report(entries=[_entry(commit_hash="")]).to_markdown()
        # The dash placeholder appears in the Commit column
        assert "| - |" in md

    def test_files_truncated_beyond_three(self):
        files = ["a.py", "b.py", "c.py", "d.py", "e.py"]
        md = _report(entries=[_entry(files=files)]).to_markdown()
        assert "(+2)" in md

    def test_files_not_truncated_at_exactly_three(self):
        files = ["a.py", "b.py", "c.py"]
        md = _report(entries=[_entry(files=files)]).to_markdown()
        assert "(+" not in md

    def test_business_rules_section_present(self):
        md = _report(entries=[_entry(rules=["BR-001: No PII in logs"])]).to_markdown()
        assert "## Business Rules Validated" in md
        assert "BR-001: No PII in logs" in md

    def test_business_rules_deduplicated(self):
        e1 = _entry(rules=["BR-001"])
        e2 = _entry(agent_name="auditor", rules=["BR-001"])
        md = _report(entries=[e1, e2]).to_markdown()
        assert md.count("- BR-001") == 1

    def test_business_rules_section_absent_when_empty(self):
        md = _report(entries=[_entry(rules=[])]).to_markdown()
        assert "## Business Rules Validated" not in md

    def test_gate_summary_section_present(self):
        md = _report(gates_passed=5, gates_failed=1).to_markdown()
        assert "## Gate Summary" in md
        assert "Gates passed: 5" in md
        assert "Gates failed: 1" in md

    def test_agent_notes_section_present_when_notes_exist(self):
        md = _report(entries=[_entry(notes="PII check required")]).to_markdown()
        assert "## Agent Notes" in md
        assert "PII check required" in md

    def test_agent_notes_section_absent_when_no_notes(self):
        md = _report(entries=[_entry(notes="")]).to_markdown()
        assert "## Agent Notes" not in md

    def test_multiple_entries_all_appear(self):
        entries = [
            _entry(agent_name="backend-engineer", action="modified"),
            _entry(agent_name="auditor", action="reviewed"),
        ]
        md = _report(entries=entries).to_markdown()
        assert "backend-engineer" in md
        assert "auditor" in md

    def test_timestamp_fallback_to_now_when_empty(self):
        r = ComplianceReport(task_id="t1", task_description="d", timestamp="")
        md = r.to_markdown()
        # Should contain a date string — not literally empty
        assert "**Date:**" in md
        # The date line should not be just empty after the colon
        date_line = [ln for ln in md.splitlines() if "**Date:**" in ln][0]
        assert date_line.strip() != "**Date:**"


# ---------------------------------------------------------------------------
# ComplianceReportGenerator.generate
# ---------------------------------------------------------------------------

class TestComplianceReportGeneratorGenerate:
    def test_generate_basic_fields(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        report = gen.generate(
            task_id="gen-001",
            task_description="Generate test",
            risk_level="MEDIUM",
            classification="Internal",
        )
        assert report.task_id == "gen-001"
        assert report.task_description == "Generate test"
        assert report.risk_level == "MEDIUM"
        assert report.classification == "Internal"

    def test_generate_timestamp_is_set(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        report = gen.generate("t1", "desc")
        assert report.timestamp != ""

    def test_generate_gates_from_usage(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        usage = _usage(gates_passed=7, gates_failed=2)
        report = gen.generate("t1", "desc", usage=usage)
        assert report.total_gates_passed == 7
        assert report.total_gates_failed == 2

    def test_generate_gates_zero_when_no_usage(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        report = gen.generate("t1", "desc")
        assert report.total_gates_passed == 0
        assert report.total_gates_failed == 0

    def test_generate_passes_entries(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        entries = [_entry()]
        report = gen.generate("t1", "desc", entries=entries)
        assert len(report.entries) == 1

    def test_generate_empty_entries_when_none(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        report = gen.generate("t1", "desc", entries=None)
        assert report.entries == []

    def test_generate_auditor_fields(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        report = gen.generate(
            "t1", "desc",
            auditor_verdict="REVISE",
            auditor_notes="Needs rework",
        )
        assert report.auditor_verdict == "REVISE"
        assert report.auditor_notes == "Needs rework"


# ---------------------------------------------------------------------------
# ComplianceReportGenerator.save / load (roundtrip)
# ---------------------------------------------------------------------------

class TestComplianceReportGeneratorSaveLoad:
    def test_save_creates_file(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        path = gen.save(_report("save-me"))
        assert path.exists()

    def test_save_returns_correct_path(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        path = gen.save(_report("rt-001"))
        assert path.name == "rt-001.md"

    def test_save_creates_parent_dirs(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "deep" / "reports")
        gen.save(_report("x"))
        assert (tmp_path / "deep" / "reports").is_dir()

    def test_save_content_starts_with_header(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        path = gen.save(_report("hdr-check"))
        assert path.read_text(encoding="utf-8").startswith("# Compliance Report:")

    def test_load_returns_content_for_existing(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        gen.save(_report("load-me"))
        content = gen.load("load-me")
        assert content is not None
        assert "# Compliance Report:" in content

    def test_load_returns_none_for_missing(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        assert gen.load("nonexistent-task") is None

    def test_load_roundtrip_preserves_task_id(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        gen.save(_report("roundtrip-id"))
        content = gen.load("roundtrip-id")
        assert content is not None
        assert "roundtrip-id" in content

    def test_save_sanitises_slashes_in_task_id(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        r = ComplianceReport(task_id="my/task/id", task_description="d")
        path = gen.save(r)
        assert "/" not in path.name
        assert path.name == "my-task-id.md"

    def test_load_handles_slash_in_task_id(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        r = ComplianceReport(task_id="a/b", task_description="d")
        gen.save(r)
        assert gen.load("a/b") is not None

    def test_save_sanitises_spaces_in_task_id(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        r = ComplianceReport(task_id="my task id", task_description="d")
        path = gen.save(r)
        assert " " not in path.name
        assert path.name == "my-task-id.md"


# ---------------------------------------------------------------------------
# ComplianceReportGenerator.list_reports / list_recent
# ---------------------------------------------------------------------------

class TestComplianceReportGeneratorList:
    def test_list_reports_empty_when_dir_missing(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "no-such-dir")
        assert gen.list_reports() == []

    def test_list_reports_returns_all_md_files(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        for tid in ("alpha", "beta", "gamma"):
            gen.save(ComplianceReport(task_id=tid, task_description="d"))
        assert len(gen.list_reports()) == 3

    def test_list_reports_sorted_alphabetically(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        for tid in ("c", "a", "b"):
            gen.save(ComplianceReport(task_id=tid, task_description="d"))
        names = [p.stem for p in gen.list_reports()]
        assert names == sorted(names)

    def test_list_reports_ignores_non_md_files(self, tmp_path: Path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        (reports_dir / "notes.txt").write_text("ignore me")
        gen = ComplianceReportGenerator(reports_dir)
        assert gen.list_reports() == []

    def test_list_recent_returns_last_n(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        for tid in ("t1", "t2", "t3", "t4", "t5"):
            gen.save(ComplianceReport(task_id=tid, task_description="d"))
        recent = gen.list_recent(3)
        assert len(recent) == 3
        stems = [p.stem for p in recent]
        assert "t3" in stems or "t4" in stems or "t5" in stems

    def test_list_recent_returns_all_when_fewer_than_n(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        gen.save(ComplianceReport(task_id="only", task_description="d"))
        assert len(gen.list_recent(10)) == 1

    def test_list_recent_default_count_five(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        for i in range(8):
            gen.save(ComplianceReport(task_id=f"t{i}", task_description="d"))
        assert len(gen.list_recent()) == 5

    def test_list_recent_empty_when_no_reports(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        assert gen.list_recent() == []


# ---------------------------------------------------------------------------
# reports_dir property
# ---------------------------------------------------------------------------

class TestComplianceReportGeneratorProperties:
    def test_default_reports_dir(self):
        gen = ComplianceReportGenerator()
        assert str(gen.reports_dir).endswith("compliance-reports")

    def test_custom_reports_dir(self, tmp_path: Path):
        custom = tmp_path / "custom-reports"
        gen = ComplianceReportGenerator(custom)
        assert gen.reports_dir == custom
