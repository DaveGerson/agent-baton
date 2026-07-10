"""Tests for :mod:`agent_baton.core.manager.scope_amendment` (Phase 3
"Make scope contracts authoritative", step 3.2).
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_baton.core.manager.paths import ManagerArtifactPaths
from agent_baton.core.manager.scope_amendment import (
    apply_scope_amendment,
    deny_scope_amendment,
    load_decision,
    load_scope_evidence,
    write_scope_evidence,
)
from agent_baton.models.manager import ManagerDecision


def _paths(tmp_path: Path, task_id: str = "task-amend") -> ManagerArtifactPaths:
    return ManagerArtifactPaths(tmp_path, task_id)


def _decision(decision_id: str = "", **kwargs) -> ManagerDecision:
    defaults = dict(
        decision_type="scope_expansion",
        task_id="task-amend",
        summary="Out-of-contract diff detected for step 1.1: infra/x.yml",
        context="evidence...",
        options=["approve", "reject"],
        created_at="2026-07-10T00:00:00Z",
    )
    defaults.update(kwargs)
    d = ManagerDecision(decision_id=decision_id, **defaults)
    return d


class _Violation:
    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason


# ---------------------------------------------------------------------------
# write_scope_evidence / load_scope_evidence
# ---------------------------------------------------------------------------


def test_write_and_load_scope_evidence_roundtrip(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    write_scope_evidence(
        paths=paths,
        decision_id="dec-abc12345",
        step_id="1.1",
        agent_name="backend-engineer--python",
        violations=[_Violation("infra/x.yml", "[diff-verified] outside allowed_paths")],
        real_changed_files=["app/a.py", "infra/x.yml"],
        created_at="2026-07-10T00:00:00Z",
    )
    loaded = load_scope_evidence(paths, "dec-abc12345")
    assert loaded is not None
    assert loaded["step_id"] == "1.1"
    assert loaded["agent_name"] == "backend-engineer--python"
    assert loaded["violations"] == [
        {"path": "infra/x.yml", "reason": "[diff-verified] outside allowed_paths"}
    ]
    assert loaded["real_changed_files"] == ["app/a.py", "infra/x.yml"]


def test_load_scope_evidence_missing_returns_none(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    assert load_scope_evidence(paths, "dec-nonexistent") is None


# ---------------------------------------------------------------------------
# load_decision
# ---------------------------------------------------------------------------


def test_load_decision_returns_none_when_no_log(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    assert load_decision(paths, "dec-abc12345") is None


def test_load_decision_returns_last_matching_entry(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    from agent_baton.core.manager.artifacts import append_decision_log

    d1 = _decision(decision_id="dec-abc12345")
    append_decision_log(paths, d1)

    d2 = _decision(decision_id="dec-abc12345", resolution="approved", resolved_at="2026-07-10T01:00:00Z")
    append_decision_log(paths, d2)

    loaded = load_decision(paths, "dec-abc12345")
    assert loaded is not None
    assert loaded.resolution == "approved"
    assert loaded.resolved_at == "2026-07-10T01:00:00Z"


# ---------------------------------------------------------------------------
# apply_scope_amendment
# ---------------------------------------------------------------------------


def test_apply_scope_amendment_merges_and_normalizes_paths(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    decision = _decision(decision_id="dec-abc12345")
    result = apply_scope_amendment(
        step_id="1.1",
        current_allowed_paths=["app"],
        additional_paths=["infra/x.yml", "app"],  # dup + new
        paths=paths,
        decision=decision,
    )
    assert result.applied is True
    assert result.new_allowed_paths == ["app", "infra/x.yml"]


def test_apply_scope_amendment_fails_when_nothing_usable(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    decision = _decision(decision_id="dec-abc12345")
    result = apply_scope_amendment(
        step_id="1.1",
        current_allowed_paths=[],
        additional_paths=["../escape", ""],
        paths=paths,
        decision=decision,
    )
    assert result.applied is False
    assert "no usable allowed_paths" in result.error


def test_apply_scope_amendment_resolves_decision_and_appends_log(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    decision = _decision(decision_id="dec-abc12345")
    assert decision.resolved_at is None

    result = apply_scope_amendment(
        step_id="1.1",
        current_allowed_paths=["app"],
        additional_paths=["infra/x.yml"],
        paths=paths,
        decision=decision,
    )
    assert result.applied is True
    assert decision.resolution == "approved"
    assert decision.resolved_at

    reloaded = load_decision(paths, "dec-abc12345")
    assert reloaded is not None
    assert reloaded.resolution == "approved"
    assert reloaded.resolved_at == decision.resolved_at

    md = paths.decision("dec-abc12345").read_text(encoding="utf-8")
    assert "Manager Decision Required" in md


def test_apply_scope_amendment_updates_existing_json_sidecar(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    contract_path = paths.scope_contract("1.1", ext="json")
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(
        json.dumps({"step_id": "1.1", "allowed_paths": ["app"]}), encoding="utf-8"
    )

    decision = _decision(decision_id="dec-abc12345")
    result = apply_scope_amendment(
        step_id="1.1",
        current_allowed_paths=["app"],
        additional_paths=["infra/x.yml"],
        paths=paths,
        decision=decision,
    )
    assert result.applied is True
    updated = json.loads(contract_path.read_text(encoding="utf-8"))
    assert updated["allowed_paths"] == ["app", "infra/x.yml"]
    assert contract_path in result.written_paths


def test_apply_scope_amendment_updates_existing_markdown_sidecar(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    contract_md_path = paths.scope_contract("1.1", ext="md")
    contract_md_path.parent.mkdir(parents=True, exist_ok=True)
    contract_md_path.write_text(
        "# Scope Contract: Step 1.1\n\n"
        "## Mission\nDo the thing.\n\n"
        "## Allowed Paths\n- app\n\n"
        "## Definition of Done\n- done\n",
        encoding="utf-8",
    )

    decision = _decision(decision_id="dec-abc12345")
    result = apply_scope_amendment(
        step_id="1.1",
        current_allowed_paths=["app"],
        additional_paths=["infra/x.yml"],
        paths=paths,
        decision=decision,
    )
    assert result.applied is True
    updated_md = contract_md_path.read_text(encoding="utf-8")
    assert "- app" in updated_md
    assert "- infra/x.yml" in updated_md
    assert "## Definition of Done" in updated_md  # other sections preserved
    assert "- done" in updated_md


def test_apply_scope_amendment_skips_missing_sidecars_without_failing(tmp_path: Path) -> None:
    """No scope-contract sidecars on disk (e.g. a plan built without
    manager-mode artifacts) still succeeds -- the caller's plan mutation is
    authoritative regardless."""
    paths = _paths(tmp_path)
    decision = _decision(decision_id="dec-abc12345")
    result = apply_scope_amendment(
        step_id="1.1",
        current_allowed_paths=["app"],
        additional_paths=["infra/x.yml"],
        paths=paths,
        decision=decision,
    )
    assert result.applied is True
    assert result.new_allowed_paths == ["app", "infra/x.yml"]


# ---------------------------------------------------------------------------
# deny_scope_amendment
# ---------------------------------------------------------------------------


def test_deny_scope_amendment_resolves_without_touching_sidecars(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    contract_json_path = paths.scope_contract("1.1", ext="json")
    contract_json_path.parent.mkdir(parents=True, exist_ok=True)
    contract_json_path.write_text(json.dumps({"allowed_paths": ["app"]}), encoding="utf-8")
    original_mtime = contract_json_path.stat().st_mtime_ns

    decision = _decision(decision_id="dec-abc12345")
    path = deny_scope_amendment(paths=paths, decision=decision)

    assert path is not None
    assert decision.resolution == "rejected"
    assert decision.resolved_at

    # The scope-contract sidecar is untouched by a denial.
    assert contract_json_path.stat().st_mtime_ns == original_mtime
    assert json.loads(contract_json_path.read_text(encoding="utf-8"))["allowed_paths"] == ["app"]

    reloaded = load_decision(paths, "dec-abc12345")
    assert reloaded is not None
    assert reloaded.resolution == "rejected"
