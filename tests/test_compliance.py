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
    # Decision: group "required field present" checks into one parameterized test
    # because each assertion targets a different region of the output and they
    # are genuinely independent — a single formatting change would fail exactly
    # one tuple, not all of them.
    @pytest.mark.parametrize("field,expected_substring", [
        ("title",          "# Compliance Report: task-hipaa-001"),
        ("description",    "Add patient record export endpoint"),
        ("risk_level",     "**Risk Level:** HIGH"),
        ("classification", "**Classification:** Regulated Data"),
        ("timestamp",      "2026-03-20T10:00:00"),
    ])
    def test_required_fields_present(self, field, expected_substring):
        md = _report().to_markdown()
        assert expected_substring in md

    def test_title_uses_task_id(self):
        md = _report("hipaa-export-v2").to_markdown()
        assert "# Compliance Report: hipaa-export-v2" in md

    # Decision: keep auditor verdict tests together — they test conditional rendering
    @pytest.mark.parametrize("verdict,expected", [
        ("SHIP WITH NOTES", "SHIP WITH NOTES"),
        ("",                "Pending"),
    ])
    def test_auditor_verdict_rendering(self, verdict, expected):
        if verdict:
            md = _report(auditor_verdict=verdict).to_markdown()
        else:
            md = ComplianceReport(task_id="t1", task_description="desc",
                                  auditor_verdict="").to_markdown()
        assert expected in md

    # Decision: section present/absent pairs stay parameterized — they test the
    # same conditional-section logic.
    @pytest.mark.parametrize("section_heading,notes_kwarg,notes_value,should_be_present", [
        ("## Auditor Notes", "auditor_notes", "Review PII handling again.", True),
        ("## Auditor Notes", "auditor_notes", "",                          False),
        ("## Agent Notes",   "entry_notes",   "PII check required",        True),
        ("## Agent Notes",   "entry_notes",   "",                          False),
    ])
    def test_section_present_or_absent(self, section_heading, notes_kwarg,
                                       notes_value, should_be_present):
        if notes_kwarg == "auditor_notes":
            md = _report(auditor_notes=notes_value).to_markdown()
        else:
            md = _report(entries=[_entry(notes=notes_value)]).to_markdown()

        if should_be_present:
            assert section_heading in md
            assert notes_value in md
        else:
            assert section_heading not in md

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

    def test_change_log_table_content(self):
        md = _report(
            entries=[
                _entry(agent_name="backend-engineer", gate_result="PASS",
                       commit_hash="abc1234567890"),
                _entry(agent_name="auditor", action="reviewed"),
            ]
        ).to_markdown()
        # Table header
        assert "## Change Log" in md
        assert "| Agent | Action | Files | Gate | Commit |" in md
        # Agent names appear in rows
        assert "backend-engineer" in md
        assert "auditor" in md
        # Gate result
        assert "PASS" in md
        # Commit truncated to 7 chars
        assert "abc1234" in md
        assert "abc1234567890" not in md

    def test_entry_no_commit_hash_shows_dash(self):
        md = _report(entries=[_entry(commit_hash="")]).to_markdown()
        assert "| - |" in md

    @pytest.mark.parametrize("files,expected_in,expected_not_in", [
        (["a.py", "b.py", "c.py", "d.py", "e.py"], "(+2)", None),
        (["a.py", "b.py", "c.py"],                  None,   "(+"),
    ])
    def test_files_truncation(self, files, expected_in, expected_not_in):
        md = _report(entries=[_entry(files=files)]).to_markdown()
        if expected_in:
            assert expected_in in md
        if expected_not_in:
            assert expected_not_in not in md

    def test_gate_summary_section(self):
        md = _report(gates_passed=5, gates_failed=1).to_markdown()
        assert "## Gate Summary" in md
        assert "Gates passed: 5" in md
        assert "Gates failed: 1" in md

    def test_timestamp_fallback_to_now_when_empty(self):
        r = ComplianceReport(task_id="t1", task_description="d", timestamp="")
        md = r.to_markdown()
        assert "**Date:**" in md
        date_line = [ln for ln in md.splitlines() if "**Date:**" in ln][0]
        assert date_line.strip() != "**Date:**"


# ---------------------------------------------------------------------------
# ComplianceReportGenerator.generate
# ---------------------------------------------------------------------------

class TestComplianceReportGeneratorGenerate:
    # Decision: group the simple field-assignment tests into one because they
    # all exercise the same code path (kwargs are forwarded to ComplianceReport).
    def test_generate_basic_and_auditor_fields(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        report = gen.generate(
            task_id="gen-001",
            task_description="Generate test",
            risk_level="MEDIUM",
            classification="Internal",
            auditor_verdict="REVISE",
            auditor_notes="Needs rework",
        )
        assert report.task_id == "gen-001"
        assert report.task_description == "Generate test"
        assert report.risk_level == "MEDIUM"
        assert report.classification == "Internal"
        assert report.auditor_verdict == "REVISE"
        assert report.auditor_notes == "Needs rework"

    def test_generate_timestamp_is_set(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        report = gen.generate("t1", "desc")
        assert report.timestamp != ""

    @pytest.mark.parametrize("passed,failed,entries,exp_passed,exp_failed,exp_entries", [
        (7,    2,    None, 7, 2, 0),
        (None, None, None, 0, 0, 0),
        (None, None, ...,  0, 0, 1),
    ])
    def test_generate_gates_and_entries(self, tmp_path: Path,
                                        passed, failed, entries,
                                        exp_passed, exp_failed, exp_entries):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        kwargs: dict = {}
        if passed is not None:
            kwargs["usage"] = _usage(gates_passed=passed, gates_failed=failed)
        if entries is ...:
            kwargs["entries"] = [_entry()]
        else:
            kwargs["entries"] = None
        report = gen.generate("t1", "desc", **kwargs)
        assert report.total_gates_passed == exp_passed
        assert report.total_gates_failed == exp_failed
        assert len(report.entries) == exp_entries


# ---------------------------------------------------------------------------
# ComplianceReportGenerator.save / load (roundtrip)
# ---------------------------------------------------------------------------

class TestComplianceReportGeneratorSaveLoad:
    # Decision: keep the four save-behavior tests separate — they test file
    # existence, path naming, parent creation, and content format independently.
    def test_save_creates_file_with_correct_name(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        path = gen.save(_report("rt-001"))
        assert path.exists()
        assert path.name == "rt-001.md"

    def test_save_creates_parent_dirs(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "deep" / "reports")
        gen.save(_report("x"))
        assert (tmp_path / "deep" / "reports").is_dir()

    def test_save_content_starts_with_header(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        path = gen.save(_report("hdr-check"))
        assert path.read_text(encoding="utf-8").startswith("# Compliance Report:")

    def test_load_roundtrip_preserves_task_id(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        gen.save(_report("roundtrip-id"))
        content = gen.load("roundtrip-id")
        assert content is not None
        assert "roundtrip-id" in content

    def test_load_returns_none_for_missing(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        assert gen.load("nonexistent-task") is None

    # Decision: task_id sanitisation tests kept together — both test the same
    # normalisation logic (special chars replaced with hyphens).
    @pytest.mark.parametrize("task_id,expected_name", [
        ("my/task/id", "my-task-id.md"),
        ("my task id", "my-task-id.md"),
    ])
    def test_save_sanitises_task_id(self, tmp_path: Path, task_id, expected_name):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        r = ComplianceReport(task_id=task_id, task_description="d")
        path = gen.save(r)
        assert path.name == expected_name
        # Also verify load works with the original (unsanitised) id
        assert gen.load(task_id) is not None


# ---------------------------------------------------------------------------
# ComplianceReportGenerator.list_reports / list_recent
# ---------------------------------------------------------------------------

class TestComplianceReportGeneratorList:
    # Decision: list_reports edge cases (missing dir, non-md files) cannot be
    # merged without loss because they set up distinct filesystem states.
    def test_list_reports_empty_when_dir_missing(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "no-such-dir")
        assert gen.list_reports() == []

    def test_list_reports_ignores_non_md_files(self, tmp_path: Path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        (reports_dir / "notes.txt").write_text("ignore me")
        gen = ComplianceReportGenerator(reports_dir)
        assert gen.list_reports() == []

    def test_list_reports_count_and_sorted(self, tmp_path: Path):
        gen = ComplianceReportGenerator(tmp_path / "reports")
        for tid in ("c", "a", "b"):
            gen.save(ComplianceReport(task_id=tid, task_description="d"))
        reports = gen.list_reports()
        assert len(reports) == 3
        names = [p.stem for p in reports]
        assert names == sorted(names)

    @pytest.mark.parametrize("total,n,expected_len", [
        (5, 3, 3),   # returns last n
        (1, 10, 1),  # fewer than n → return all
        (8, 5, 5),   # default count of 5
        (0, 5, 0),   # empty dir
    ])
    def test_list_recent(self, tmp_path: Path, total, n, expected_len):
        gen = ComplianceReportGenerator(tmp_path / f"reports-{total}-{n}")
        for i in range(total):
            gen.save(ComplianceReport(task_id=f"t{i}", task_description="d"))
        result = gen.list_recent(n)
        assert len(result) == expected_len


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
