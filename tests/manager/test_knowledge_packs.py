"""Tests for :mod:`agent_baton.core.manager.knowledge_plan` (M5 — knowledge
pack lifecycle), plus the manifest extension in
:mod:`agent_baton.core.orchestration.knowledge_registry` /
:mod:`agent_baton.models.knowledge`, and the deprecated-pack skip in
:mod:`agent_baton.core.engine.knowledge_resolver`.

See docs/internal/manager-mode-pmo-plan.md Wave 1 / Task 7 and
docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md §12, §16
Milestone 5.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from agent_baton.core.config.manager import ManagerConfig
from agent_baton.core.manager import knowledge_plan as kp_module
from agent_baton.core.manager.knowledge_plan import (
    KnowledgePlanBuilder,
    audit_packs,
    load_gap_records,
    propose_from_gap_records,
    scan_project,
    write_proposals,
    write_scan_report,
)
from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_pack(root: Path, name: str, manifest_yaml: str, docs: dict[str, str] | None = None) -> Path:
    pack_dir = root / name
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "knowledge.yaml").write_text(manifest_yaml, encoding="utf-8")
    for doc_name, content in (docs or {}).items():
        (pack_dir / doc_name).write_text(content, encoding="utf-8")
    return pack_dir


def _make_registry(tmp_path: Path, packs: dict[str, str]) -> KnowledgeRegistry:
    root = tmp_path / ".claude" / "knowledge"
    root.mkdir(parents=True, exist_ok=True)
    for name, manifest_yaml in packs.items():
        _write_pack(root, name, manifest_yaml)
    registry = KnowledgeRegistry()
    registry.load_directory(root)
    return registry


def _make_manager_config(tmp_path: Path, yaml_text: str = "") -> ManagerConfig:
    if yaml_text:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "baton.yaml").write_text(yaml_text, encoding="utf-8")
    return ManagerConfig.load(tmp_path)


def _make_plan(task_id: str = "task-1") -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Add a reporting endpoint with tests and docs",
        detected_stack="python",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Implement the endpoint",
                        step_type="developing",
                    ),
                ],
            ),
            PlanPhase(
                phase_id=2,
                name="Test",
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="test-engineer",
                        task_description="Write tests",
                        step_type="testing",
                    ),
                    PlanStep(
                        step_id="2.2",
                        agent_name="architect",
                        task_description="Review the design",
                        step_type="planning",
                    ),
                ],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# knowledge.yaml manifest extension (registry + model)
# ---------------------------------------------------------------------------


def test_extended_manifest_parses(tmp_path: Path) -> None:
    _write_pack(
        tmp_path,
        "testing-strategy",
        (
            "name: testing-strategy\n"
            "status: active\n"
            "confidence: high\n"
            "source_files:\n"
            "  - pyproject.toml\n"
            "  - tests/README.md\n"
            "last_reviewed: 2026-06-01\n"
            "stale_after_days: 90\n"
        ),
    )
    registry = KnowledgeRegistry()
    registry.load_directory(tmp_path)

    pack = registry.get_pack("testing-strategy")
    assert pack is not None
    assert pack.status == "active"
    assert pack.confidence == "high"
    assert pack.source_files == ["pyproject.toml", "tests/README.md"]
    assert pack.last_reviewed == "2026-06-01"
    assert pack.stale_after_days == 90
    assert "testing-strategy" not in registry.degraded_pack_names


def test_extended_manifest_defaults_when_absent(tmp_path: Path) -> None:
    _write_pack(tmp_path, "bare-pack", "name: bare-pack\n")
    registry = KnowledgeRegistry()
    registry.load_directory(tmp_path)

    pack = registry.get_pack("bare-pack")
    assert pack is not None
    assert pack.status == "active"
    assert pack.confidence == "medium"
    assert pack.source_files == []
    assert pack.last_reviewed is None
    assert pack.stale_after_days is None


def test_invalid_status_fails_audit(tmp_path: Path) -> None:
    registry = _make_registry(tmp_path, {"weird-pack": "name: weird-pack\nstatus: bogus\n"})

    # Registry itself never rejects the pack -- graceful degradation is
    # sacred -- but the manifest is untrustworthy, so it's degraded.
    pack = registry.get_pack("weird-pack")
    assert pack is not None
    assert pack.status == "bogus"
    assert "weird-pack" in registry.degraded_pack_names

    config = _make_manager_config(tmp_path)
    issues = audit_packs(registry, config, root=tmp_path)
    invalid = [i for i in issues if i.kind == "invalid_status" and i.pack_name == "weird-pack"]
    assert len(invalid) == 1
    assert "bogus" in invalid[0].message
    assert "active" in invalid[0].message  # valid options named


def test_valid_status_is_not_flagged(tmp_path: Path) -> None:
    registry = _make_registry(
        tmp_path,
        {"tidy-pack": "name: tidy-pack\nstatus: deprecated\nconfidence: high\nsource_files: [README.md]\n"},
    )
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
    config = _make_manager_config(tmp_path)

    issues = audit_packs(registry, config, root=tmp_path)
    assert not [i for i in issues if i.pack_name == "tidy-pack" and i.kind == "invalid_status"]


# ---------------------------------------------------------------------------
# Deprecated-pack skip in KnowledgeResolver (non-explicit layers only)
# ---------------------------------------------------------------------------


def test_deprecated_pack_skipped_for_non_explicit_layers(tmp_path: Path) -> None:
    from agent_baton.core.engine.knowledge_resolver import KnowledgeResolver

    pack_dir = tmp_path / "legacy-pack"
    pack_dir.mkdir()
    (pack_dir / "knowledge.yaml").write_text(
        "name: legacy-pack\nstatus: deprecated\ntags: [legacy]\n", encoding="utf-8"
    )
    (pack_dir / "doc1.md").write_text(
        "---\nname: doc1\ntags: [legacy]\n---\nBody text.\n", encoding="utf-8"
    )

    registry = KnowledgeRegistry()
    registry.load_directory(tmp_path)
    resolver = KnowledgeResolver(registry)

    # Layer 3 (strict tag match, non-explicit) must skip the deprecated pack.
    attachments = resolver.resolve(
        agent_name="backend-engineer",
        task_description="Investigate legacy behavior",
    )
    assert attachments == []


def test_deprecated_pack_still_allowed_when_explicit(tmp_path: Path) -> None:
    from agent_baton.core.engine.knowledge_resolver import KnowledgeResolver

    pack_dir = tmp_path / "legacy-pack"
    pack_dir.mkdir()
    (pack_dir / "knowledge.yaml").write_text(
        "name: legacy-pack\nstatus: deprecated\ntags: [legacy]\n", encoding="utf-8"
    )
    (pack_dir / "doc1.md").write_text(
        "---\nname: doc1\ntags: [legacy]\n---\nBody text.\n", encoding="utf-8"
    )

    registry = KnowledgeRegistry()
    registry.load_directory(tmp_path)
    resolver = KnowledgeResolver(registry)

    explicit = resolver.resolve(
        agent_name="backend-engineer",
        task_description="Investigate legacy behavior",
        explicit_packs=["legacy-pack"],
    )
    assert [a.document_name for a in explicit] == ["doc1"]


# ---------------------------------------------------------------------------
# KnowledgePlanBuilder
# ---------------------------------------------------------------------------


def test_missing_required_pack_in_plan(tmp_path: Path) -> None:
    registry = KnowledgeRegistry()  # empty -- nothing on disk
    config = _make_manager_config(
        tmp_path,
        "knowledge_packs:\n"
        "  default_packs: []\n"
        "  required_for_code_steps:\n"
        "    - coding-conventions\n",
    )
    plan = _make_plan()

    result = KnowledgePlanBuilder(config, registry).build(plan, blueprint_roles=[])

    missing_by_name = {m.name: m.reason for m in result.missing_packs}
    assert missing_by_name.get("coding-conventions") == "config: required_for_code_steps"


def test_stale_pack_flagged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _make_registry(
        tmp_path,
        {
            "testing-strategy": (
                "name: testing-strategy\n"
                "last_reviewed: 2026-01-01\n"
                "stale_after_days: 90\n"
            ),
        },
    )
    config = _make_manager_config(tmp_path, "knowledge_packs:\n  default_packs: []\n")
    plan = _make_plan()

    monkeypatch.setattr(kp_module, "_today", lambda: date(2026, 7, 2))

    result = KnowledgePlanBuilder(config, registry).build(plan, blueprint_roles=[])
    assert "testing-strategy" in result.stale_packs


def test_fresh_pack_not_flagged_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _make_registry(
        tmp_path,
        {
            "testing-strategy": (
                "name: testing-strategy\n"
                "last_reviewed: 2026-06-20\n"
                "stale_after_days: 90\n"
            ),
        },
    )
    config = _make_manager_config(tmp_path, "knowledge_packs:\n  default_packs: []\n")
    plan = _make_plan()

    monkeypatch.setattr(kp_module, "_today", lambda: date(2026, 7, 2))

    result = KnowledgePlanBuilder(config, registry).build(plan, blueprint_roles=[])
    assert "testing-strategy" not in result.stale_packs


def test_stale_check_falls_back_to_config_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pack has no `stale_after_days` of its own -- config default applies."""
    registry = _make_registry(
        tmp_path,
        {"docs-pack": "name: docs-pack\nlast_reviewed: 2026-01-01\n"},
    )
    config = _make_manager_config(
        tmp_path, "knowledge_packs:\n  default_packs: []\n  stale_after_days: 30\n"
    )
    plan = _make_plan()

    monkeypatch.setattr(kp_module, "_today", lambda: date(2026, 7, 2))

    result = KnowledgePlanBuilder(config, registry).build(plan, blueprint_roles=[])
    assert "docs-pack" in result.stale_packs


def test_role_pack_attaches_to_role(tmp_path: Path) -> None:
    registry = _make_registry(
        tmp_path,
        {"test-conventions": "name: test-conventions\ntarget_agents: [test-engineer]\n"},
    )
    config = _make_manager_config(tmp_path, "knowledge_packs:\n  default_packs: []\n")
    plan = _make_plan()

    result = KnowledgePlanBuilder(config, registry).build(
        plan, blueprint_roles=["test-engineer", "backend-engineer"]
    )

    assert "test-conventions" in result.per_role_packs.get("test-engineer", [])
    assert "backend-engineer" not in result.per_role_packs or (
        "test-conventions" not in result.per_role_packs.get("backend-engineer", [])
    )


def test_required_for_code_steps_attach(tmp_path: Path) -> None:
    registry = _make_registry(tmp_path, {"coding-conventions": "name: coding-conventions\n"})
    config = _make_manager_config(
        tmp_path,
        "knowledge_packs:\n"
        "  default_packs: []\n"
        "  required_for_code_steps:\n"
        "    - coding-conventions\n",
    )
    plan = _make_plan()

    result = KnowledgePlanBuilder(config, registry).build(plan, blueprint_roles=[])

    assert "coding-conventions" in result.per_step_packs.get("1.1", [])  # developing
    assert "coding-conventions" in result.per_step_packs.get("2.1", [])  # testing
    # step 2.2 is step_type="planning" -- not an implementation step.
    assert "coding-conventions" not in result.per_step_packs.get("2.2", [])


def test_default_packs_selected_when_present(tmp_path: Path) -> None:
    registry = _make_registry(tmp_path, {"repo-architecture": "name: repo-architecture\n"})
    config = _make_manager_config(
        tmp_path, "knowledge_packs:\n  default_packs: [repo-architecture]\n"
    )
    plan = _make_plan()

    result = KnowledgePlanBuilder(config, registry).build(plan, blueprint_roles=[])

    selected_names = {p.name: p.reason for p in result.selected_packs}
    assert selected_names.get("repo-architecture") == "config: default_packs"


def test_knowledge_plan_round_trips_through_write_all(tmp_path: Path) -> None:
    """Sanity: the builder's output satisfies the frozen KnowledgePlan model
    (round-trips via to_dict/from_dict, as write_all will do)."""
    from agent_baton.models.manager import KnowledgePlan

    registry = _make_registry(tmp_path, {"coding-conventions": "name: coding-conventions\n"})
    config = _make_manager_config(
        tmp_path,
        "knowledge_packs:\n  default_packs: [coding-conventions]\n",
    )
    plan = _make_plan()

    result = KnowledgePlanBuilder(config, registry).build(plan, blueprint_roles=["backend-engineer"])
    reloaded = KnowledgePlan.from_dict(json.loads(json.dumps(result.to_dict())))
    assert reloaded == result


# ---------------------------------------------------------------------------
# audit_packs — missing source files
# ---------------------------------------------------------------------------


def test_audit_reports_missing_source_file(tmp_path: Path) -> None:
    registry = _make_registry(
        tmp_path,
        {
            "testing-strategy": (
                "name: testing-strategy\nconfidence: high\nsource_files:\n  - pyproject.toml\n"
            ),
        },
    )
    config = _make_manager_config(tmp_path)

    issues = audit_packs(registry, config, root=tmp_path)
    missing = [i for i in issues if i.kind == "missing_source_file"]
    assert len(missing) == 1
    assert "pyproject.toml" in missing[0].message

    (tmp_path / "pyproject.toml").write_text("[tool.x]\n", encoding="utf-8")
    issues_after = audit_packs(registry, config, root=tmp_path)
    assert not [i for i in issues_after if i.kind == "missing_source_file"]


def test_audit_reports_missing_metadata(tmp_path: Path) -> None:
    registry = _make_registry(tmp_path, {"bare-pack": "name: bare-pack\n"})
    config = _make_manager_config(tmp_path)

    issues = audit_packs(registry, config, root=tmp_path)
    missing_meta = [i for i in issues if i.kind == "missing_metadata" and i.pack_name == "bare-pack"]
    assert len(missing_meta) == 1


# ---------------------------------------------------------------------------
# scan_project / write_scan_report
# ---------------------------------------------------------------------------


def test_scan_discovers_packs_and_docs(tmp_path: Path) -> None:
    knowledge_root = tmp_path / ".claude" / "knowledge"
    _write_pack(
        knowledge_root,
        "coding-conventions",
        "name: coding-conventions\n",
        docs={"conventions.md": "---\nname: conventions\n---\nBody\n"},
    )
    (tmp_path / "README.md").write_text("# Project\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[tool.x]\n", encoding="utf-8")

    registry = KnowledgeRegistry()
    registry.load_directory(knowledge_root)

    result = scan_project(tmp_path, registry)
    pack_names = {p["name"] for p in result["packs"]}
    assert "coding-conventions" in pack_names
    assert "README.md" in result["discovered_files"]
    assert "pyproject.toml" in result["discovered_files"]


def test_write_scan_report_writes_team_context_root_not_executions(tmp_path: Path) -> None:
    """`knowledge-scan.json` lands at `.claude/team-context/` root, per
    docs/internal/manager-mode-pmo-plan.md self-review notes -- NOT under
    `executions/<task_id>/`."""
    registry = KnowledgeRegistry()
    out_path = write_scan_report(tmp_path, registry)

    assert out_path == tmp_path / ".claude" / "team-context" / "knowledge-scan.json"
    assert "executions" not in out_path.parts
    assert out_path.exists()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["packs"] == []
    assert isinstance(payload["discovered_files"], list)


# ---------------------------------------------------------------------------
# propose_from_gap_records / write_proposals
# ---------------------------------------------------------------------------


def _write_retro(retros_dir: Path, name: str, gaps: list[dict]) -> None:
    retros_dir.mkdir(parents=True, exist_ok=True)
    (retros_dir / name).write_text(json.dumps({"knowledge_gaps": gaps}), encoding="utf-8")


def test_propose_writes_draft_from_repeated_gaps(tmp_path: Path) -> None:
    team_context_root = tmp_path / ".claude" / "team-context"
    retros_dir = team_context_root / "retrospectives"

    gap = {
        "description": "No documented convention for API error responses",
        "gap_type": "contextual",
        "resolution": "unresolved",
        "resolution_detail": "",
        "agent_name": "backend-engineer",
        "task_summary": "Add endpoint",
        "task_type": "feature",
    }
    gap2 = dict(gap, agent_name="test-engineer", task_summary="Add tests")
    _write_retro(retros_dir, "retro-1.json", [gap])
    _write_retro(retros_dir, "retro-2.json", [gap2])

    records = load_gap_records(team_context_root)
    assert len(records) == 2

    proposals = propose_from_gap_records(team_context_root)
    assert len(proposals) == 1
    assert proposals[0].occurrences == 2
    assert proposals[0].agents == ("backend-engineer", "test-engineer")

    written = write_proposals(team_context_root, proposals)
    assert len(written) == 1
    assert written[0].parent == team_context_root / "knowledge-proposals"
    assert written[0].exists()
    content = written[0].read_text(encoding="utf-8")
    assert "API error responses" in content
    assert "Occurrences:** 2" in content


def test_propose_ignores_single_occurrence(tmp_path: Path) -> None:
    team_context_root = tmp_path / ".claude" / "team-context"
    retros_dir = team_context_root / "retrospectives"
    _write_retro(
        retros_dir,
        "retro-1.json",
        [{"description": "One-off gap", "agent_name": "backend-engineer", "task_summary": "x"}],
    )

    proposals = propose_from_gap_records(team_context_root)
    assert proposals == []


def test_propose_normalizes_whitespace_and_case(tmp_path: Path) -> None:
    team_context_root = tmp_path / ".claude" / "team-context"
    retros_dir = team_context_root / "retrospectives"
    _write_retro(
        retros_dir,
        "retro-1.json",
        [{"description": "  No error-handling convention  ", "agent_name": "a", "task_summary": "x"}],
    )
    _write_retro(
        retros_dir,
        "retro-2.json",
        [{"description": "No error-handling convention", "agent_name": "b", "task_summary": "y"}],
    )

    proposals = propose_from_gap_records(team_context_root)
    assert len(proposals) == 1
    assert proposals[0].occurrences == 2


def test_no_retrospectives_dir_returns_empty(tmp_path: Path) -> None:
    team_context_root = tmp_path / ".claude" / "team-context"
    assert load_gap_records(team_context_root) == []
    assert propose_from_gap_records(team_context_root) == []
