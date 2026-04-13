"""Tests for agent_baton.models.learning — LearningEvidence and LearningIssue."""
from __future__ import annotations

import json

import pytest

from agent_baton.models.learning import (
    VALID_ISSUE_TYPES,
    VALID_SEVERITIES,
    VALID_STATUSES,
    LearningEvidence,
    LearningIssue,
)


# ---------------------------------------------------------------------------
# LearningEvidence
# ---------------------------------------------------------------------------


class TestLearningEvidence:
    def test_to_dict_roundtrip(self):
        ev = LearningEvidence(
            timestamp="2026-04-13T12:00:00+00:00",
            source_task_id="task-123",
            detail="Agent used wrong flavor",
            data={"agent_name": "backend-engineer--node", "detected_stack": "python"},
        )
        d = ev.to_dict()
        restored = LearningEvidence.from_dict(d)
        assert restored.timestamp == ev.timestamp
        assert restored.source_task_id == ev.source_task_id
        assert restored.detail == ev.detail
        assert restored.data == ev.data

    def test_to_dict_keys(self):
        ev = LearningEvidence(
            timestamp="2026-01-01T00:00:00Z",
            source_task_id="t1",
            detail="something",
            data={"x": 1},
        )
        d = ev.to_dict()
        assert set(d.keys()) == {"timestamp", "source_task_id", "detail", "data"}

    def test_from_dict_missing_fields_use_defaults(self):
        ev = LearningEvidence.from_dict({})
        assert ev.timestamp == ""
        assert ev.source_task_id == ""
        assert ev.detail == ""
        assert ev.data == {}

    def test_from_dict_with_partial_data(self):
        ev = LearningEvidence.from_dict({"timestamp": "2026-01-01T00:00:00Z", "detail": "x"})
        assert ev.timestamp == "2026-01-01T00:00:00Z"
        assert ev.detail == "x"
        assert ev.source_task_id == ""

    def test_data_field_preserves_nested_structures(self):
        nested = {"scores": [1, 2, 3], "meta": {"k": "v"}}
        ev = LearningEvidence(
            timestamp="2026-04-13T00:00:00Z",
            source_task_id="t1",
            detail="d",
            data=nested,
        )
        restored = LearningEvidence.from_dict(ev.to_dict())
        assert restored.data == nested

    def test_default_data_is_empty_dict(self):
        ev = LearningEvidence(timestamp="t", source_task_id="s", detail="d")
        assert ev.data == {}


# ---------------------------------------------------------------------------
# LearningIssue
# ---------------------------------------------------------------------------


class TestLearningIssue:
    def _make_issue(self, **overrides) -> LearningIssue:
        defaults = dict(
            issue_id="issue-uuid-001",
            issue_type="routing_mismatch",
            severity="medium",
            status="open",
            title="Agent flavor mismatch",
            target="python:backend-engineer",
        )
        defaults.update(overrides)
        return LearningIssue(**defaults)

    def test_to_dict_roundtrip_minimal(self):
        issue = self._make_issue()
        d = issue.to_dict()
        restored = LearningIssue.from_dict(d)
        assert restored.issue_id == issue.issue_id
        assert restored.issue_type == issue.issue_type
        assert restored.severity == issue.severity
        assert restored.status == issue.status
        assert restored.title == issue.title
        assert restored.target == issue.target
        assert restored.evidence == []
        assert restored.occurrence_count == 1

    def test_to_dict_roundtrip_with_evidence(self):
        ev1 = LearningEvidence("2026-04-01T00:00:00Z", "t1", "detail1", {"k": "v"})
        ev2 = LearningEvidence("2026-04-02T00:00:00Z", "t2", "detail2", {})
        issue = self._make_issue(evidence=[ev1, ev2], occurrence_count=2)
        d = issue.to_dict()
        restored = LearningIssue.from_dict(d)
        assert len(restored.evidence) == 2
        assert restored.evidence[0].detail == "detail1"
        assert restored.evidence[1].source_task_id == "t2"

    def test_to_dict_roundtrip_optional_fields(self):
        issue = self._make_issue(
            proposed_fix="Add flavor override",
            resolution="Applied routing fix",
            resolution_type="auto",
            experiment_id="exp-42",
        )
        restored = LearningIssue.from_dict(issue.to_dict())
        assert restored.proposed_fix == "Add flavor override"
        assert restored.resolution == "Applied routing fix"
        assert restored.resolution_type == "auto"
        assert restored.experiment_id == "exp-42"

    def test_optional_fields_default_to_none(self):
        issue = self._make_issue()
        assert issue.proposed_fix is None
        assert issue.resolution is None
        assert issue.resolution_type is None
        assert issue.experiment_id is None

    def test_from_dict_evidence_as_json_string(self):
        """SQLite stores evidence as JSON text; from_dict must parse it."""
        ev_dict = {
            "timestamp": "2026-04-13T00:00:00Z",
            "source_task_id": "t1",
            "detail": "parsed from string",
            "data": {},
        }
        d = {
            "issue_id": "id1",
            "issue_type": "gate_mismatch",
            "severity": "high",
            "status": "open",
            "title": "Gate mismatch",
            "target": "typescript:test",
            "evidence": json.dumps([ev_dict]),  # string, not list
        }
        issue = LearningIssue.from_dict(d)
        assert len(issue.evidence) == 1
        assert issue.evidence[0].detail == "parsed from string"

    def test_from_dict_evidence_as_invalid_string_yields_empty(self):
        d = {
            "issue_id": "id2",
            "issue_type": "gate_mismatch",
            "severity": "low",
            "status": "open",
            "title": "x",
            "target": "y",
            "evidence": "not valid json {{{{",
        }
        issue = LearningIssue.from_dict(d)
        assert issue.evidence == []

    def test_from_dict_occurrence_count_coerced_to_int(self):
        d = {
            "issue_id": "id3",
            "issue_type": "agent_degradation",
            "severity": "high",
            "status": "open",
            "title": "x",
            "target": "y",
            "occurrence_count": "7",  # string from DB
        }
        issue = LearningIssue.from_dict(d)
        assert issue.occurrence_count == 7

    def test_from_dict_defaults_for_missing_fields(self):
        d = {"issue_id": "id4"}
        issue = LearningIssue.from_dict(d)
        assert issue.issue_type == "routing_mismatch"
        assert issue.severity == "medium"
        assert issue.status == "open"
        assert issue.title == ""
        assert issue.target == ""
        assert issue.occurrence_count == 1

    def test_all_valid_issue_types(self):
        for itype in VALID_ISSUE_TYPES:
            issue = self._make_issue(issue_type=itype)
            assert issue.issue_type == itype

    def test_all_valid_statuses(self):
        for status in VALID_STATUSES:
            issue = self._make_issue(status=status)
            assert issue.status == status

    def test_all_valid_severities(self):
        for severity in VALID_SEVERITIES:
            issue = self._make_issue(severity=severity)
            assert issue.severity == severity

    def test_to_dict_all_keys_present(self):
        issue = self._make_issue()
        d = issue.to_dict()
        expected_keys = {
            "issue_id", "issue_type", "severity", "status", "title", "target",
            "evidence", "first_seen", "last_seen", "occurrence_count",
            "proposed_fix", "resolution", "resolution_type", "experiment_id",
        }
        assert set(d.keys()) == expected_keys

    def test_evidence_list_serialized_as_list_of_dicts(self):
        ev = LearningEvidence("2026-01-01T00:00:00Z", "t1", "d", {})
        issue = self._make_issue(evidence=[ev])
        d = issue.to_dict()
        assert isinstance(d["evidence"], list)
        assert isinstance(d["evidence"][0], dict)

    @pytest.mark.parametrize("bad_type", [
        "invalid_type", "", "ROUTING_MISMATCH", "routing mismatch",
    ])
    def test_issue_type_set_as_given_no_validation_in_model(self, bad_type: str):
        """The model itself does not validate; validation lives in the ledger/engine.
        Confirm from_dict preserves whatever value is stored."""
        d = {
            "issue_id": "id5",
            "issue_type": bad_type,
            "severity": "low",
            "status": "open",
            "title": "t",
            "target": "x",
        }
        issue = LearningIssue.from_dict(d)
        assert issue.issue_type == bad_type
