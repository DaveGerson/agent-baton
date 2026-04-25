"""Tests for F0.3 AuditorVerdict enum and executor enforcement."""
from __future__ import annotations

import pytest

from agent_baton.core.govern.compliance import (
    AuditorVerdict,
    parse_auditor_verdict,
    extract_verdict_from_text,
    ComplianceReport,
    ComplianceEntry,
)


# ---------------------------------------------------------------------------
# AuditorVerdict enum
# ---------------------------------------------------------------------------

def test_verdict_values_exist() -> None:
    assert AuditorVerdict.APPROVE.value == "APPROVE"
    assert AuditorVerdict.APPROVE_WITH_CONCERNS.value == "APPROVE_WITH_CONCERNS"
    assert AuditorVerdict.REQUEST_CHANGES.value == "REQUEST_CHANGES"
    assert AuditorVerdict.VETO.value == "VETO"


def test_veto_blocks_execution() -> None:
    assert AuditorVerdict.VETO.blocks_execution is True


def test_non_veto_does_not_block() -> None:
    assert AuditorVerdict.APPROVE.blocks_execution is False
    assert AuditorVerdict.APPROVE_WITH_CONCERNS.blocks_execution is False
    assert AuditorVerdict.REQUEST_CHANGES.blocks_execution is False


# ---------------------------------------------------------------------------
# parse_auditor_verdict — backward compat mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("APPROVE", AuditorVerdict.APPROVE),
    ("APPROVE_WITH_CONCERNS", AuditorVerdict.APPROVE_WITH_CONCERNS),
    ("REQUEST_CHANGES", AuditorVerdict.REQUEST_CHANGES),
    ("VETO", AuditorVerdict.VETO),
    # Legacy values
    ("SHIP", AuditorVerdict.APPROVE),
    ("ship", AuditorVerdict.APPROVE),
    ("SHIP WITH NOTES", AuditorVerdict.APPROVE_WITH_CONCERNS),
    ("REVISE", AuditorVerdict.REQUEST_CHANGES),
    ("BLOCK", AuditorVerdict.VETO),
    ("block", AuditorVerdict.VETO),
])
def test_parse_auditor_verdict_mapping(raw: str, expected: AuditorVerdict) -> None:
    assert parse_auditor_verdict(raw) == expected


def test_parse_auditor_verdict_empty_returns_none() -> None:
    assert parse_auditor_verdict("") is None


def test_parse_auditor_verdict_unknown_returns_none() -> None:
    assert parse_auditor_verdict("DUNNO") is None


# ---------------------------------------------------------------------------
# extract_verdict_from_text — fenced JSON block parsing
# ---------------------------------------------------------------------------

def test_extract_verdict_from_fenced_json_approve() -> None:
    text = '```json\n{"verdict": "APPROVE", "rationale": "all good"}\n```'
    result = extract_verdict_from_text(text)
    assert result == AuditorVerdict.APPROVE


def test_extract_verdict_from_fenced_json_veto() -> None:
    text = 'Some analysis.\n\n```json\n{"verdict": "VETO", "rationale": "too risky"}\n```\n\nMore notes.'
    result = extract_verdict_from_text(text)
    assert result == AuditorVerdict.VETO


def test_extract_verdict_from_fenced_json_with_concerns() -> None:
    text = '```json\n{"verdict": "APPROVE_WITH_CONCERNS", "rationale": "minor issues"}\n```'
    result = extract_verdict_from_text(text)
    assert result == AuditorVerdict.APPROVE_WITH_CONCERNS


def test_extract_verdict_legacy_block_fallback() -> None:
    text = "Verdict: SHIP WITH NOTES\n\nSome report text."
    result = extract_verdict_from_text(text)
    assert result == AuditorVerdict.APPROVE_WITH_CONCERNS


def test_extract_verdict_empty_text_returns_none() -> None:
    assert extract_verdict_from_text("") is None


def test_extract_verdict_no_verdict_returns_none() -> None:
    assert extract_verdict_from_text("No verdict here at all.") is None


def test_extract_verdict_malformed_json_does_not_crash() -> None:
    text = "```json\n{broken json\n```"
    # Should not raise, should return None or fallback
    result = extract_verdict_from_text(text)
    # Might be None or match a legacy fallback depending on text
    assert result is None or isinstance(result, AuditorVerdict)


# ---------------------------------------------------------------------------
# ComplianceReport.parsed_verdict and blocks_execution
# ---------------------------------------------------------------------------

def test_compliance_report_parsed_verdict_ship() -> None:
    report = ComplianceReport(
        task_id="t1", task_description="test", auditor_verdict="SHIP"
    )
    assert report.parsed_verdict == AuditorVerdict.APPROVE
    assert report.blocks_execution is False


def test_compliance_report_parsed_verdict_block() -> None:
    report = ComplianceReport(
        task_id="t1", task_description="test", auditor_verdict="BLOCK"
    )
    assert report.parsed_verdict == AuditorVerdict.VETO
    assert report.blocks_execution is True


def test_compliance_report_parsed_verdict_veto_canonical() -> None:
    report = ComplianceReport(
        task_id="t1", task_description="test", auditor_verdict="VETO"
    )
    assert report.parsed_verdict == AuditorVerdict.VETO
    assert report.blocks_execution is True


def test_compliance_report_empty_verdict() -> None:
    report = ComplianceReport(task_id="t1", task_description="test")
    assert report.parsed_verdict is None
    assert report.blocks_execution is False


def test_compliance_report_approve_with_concerns() -> None:
    report = ComplianceReport(
        task_id="t1", task_description="test",
        auditor_verdict="APPROVE_WITH_CONCERNS",
    )
    assert report.parsed_verdict == AuditorVerdict.APPROVE_WITH_CONCERNS
    assert report.blocks_execution is False
