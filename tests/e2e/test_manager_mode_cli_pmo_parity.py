"""CLI <-> PMO manager-mode artifact parity (Phase 7 7.3 "test-engineer").

Asserts that for the SAME deterministic plan input and the SAME (default)
``ManagerConfig``, ``baton plan --manager-mode --save`` (CLI,
``ManagerModePlanner.build_and_write``) and ``POST /pmo/forge/approve``
(PMO, ``ForgeSession.save_plan`` -> ``rebuild_and_publish`` ->
``ManagerModePlanner.build``) produce:

1. The identical persisted ``plan.json`` step/phase shape (including the
   ``PhasePolicyApplier``-injected adversarial-review step both paths run),
   and
2. Byte-identical manager-mode sidecar artifacts (charter, scope map, team
   blueprint, knowledge plan, manager brief, every scope contract, every
   context bundle) -- the two entry points are documented (see
   ``ForgeSession.save_plan``'s docstring) to run the exact same
   ``ManagerModePlanner`` composition; this test is the executable proof.

Deliberately excluded from the comparison: ``artifact-revision.json``
(only the PMO path writes one -- ``rebuild_and_publish`` is a strict
superset of ``build_and_write``, see that docstring) and any field
generated from a wall-clock timestamp. Everything else -- including
per-reference ``token_estimate`` / ``estimated_tokens`` /
``truncation_warnings`` in context bundles and the manager brief -- is
compared byte-for-byte. An earlier revision of this suite discovered
(and pinned) a live CLI-vs-PMO divergence here: ``ManagerModePlanner.
build()`` (the ``rebuild_and_publish`` / PMO path) estimated must-read
token counts by re-reading scope-contract/role-card files that had not
been written yet, so every PMO bundle carried a 0-token estimate plus a
spurious "Missing file for token estimate" truncation warning. That was
fixed in the Phase 7 review: ``ContextBundleBuilder.build`` now
estimates those references from the rendered in-memory text
(``contract_text`` / ``role_card_text``), which is byte-identical to
the published file's size, on both paths.
``test_context_bundle_token_estimates_match_and_are_nonzero`` below is
the regression test for that fix.

Hermetic: ``Path.home()`` is redirected by the autouse ``tests/e2e/
conftest.py::fake_home`` fixture; ``IntelligentPlanner`` is mocked (never
invoked) on the CLI side, and headless Claude is disabled on the PMO side
-- neither path shells out to a live ``claude`` binary or touches a real
developer machine's ``~/.baton`` / ``~/.claude/knowledge``.
"""
from __future__ import annotations

import argparse
import contextlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.cli.commands.execution import plan_cmd
from agent_baton.core.manager.paths import ManagerArtifactPaths
from agent_baton.core.pmo.forge import ForgeSession
from agent_baton.core.runtime.headless import HeadlessClaude, HeadlessConfig
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep
from agent_baton.models.pmo import PmoProject

TASK_ID = "2026-07-17-reporting-endpoint-parity01"


# ---------------------------------------------------------------------------
# Deterministic input
# ---------------------------------------------------------------------------


def _deterministic_plan() -> MachinePlan:
    """Two-phase plan with no randomness / wall-clock dependence anywhere
    in its fields -- mirrors ``tests/cli/test_plan_manager_mode_save.py``
    ``_plan()``."""
    return MachinePlan(
        task_id=TASK_ID,
        task_summary="Add a reporting endpoint with tests and docs",
        task_type="feature",
        complexity="medium",
        detected_stack="python",
        risk_level="MEDIUM",
        budget_tier="standard",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Implement the reporting endpoint.",
                        deliverables=["app/reporting/service.py"],
                        allowed_paths=["app/reporting/**"],
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
                        task_description="Add tests for the reporting endpoint.",
                        deliverables=["tests/reporting/test_service.py"],
                        allowed_paths=["tests/reporting/**"],
                        depends_on=["1.1"],
                        step_type="testing",
                    ),
                ],
            ),
        ],
    )


def _independent_copy() -> MachinePlan:
    """A fresh, unshared ``MachinePlan`` object with identical content --
    both build paths mutate their plan in place (policy-injected review
    steps), so the CLI and PMO runs must never share one Python object."""
    return MachinePlan.from_dict(_deterministic_plan().to_dict())


# ---------------------------------------------------------------------------
# CLI path -- baton plan --manager-mode --save
# ---------------------------------------------------------------------------


def _run_cli_manager_save(project_root: Path, monkeypatch: Any, plan: MachinePlan) -> Path:
    """Drive ``plan_cmd.handler`` exactly as ``tests/cli/
    test_plan_manager_mode_save.py`` does: the real 7-stage
    ``IntelligentPlanner`` is replaced by a mock returning *plan* directly
    (nothing about ManagerModePlanner's composition is mocked), all other
    manager-mode config resolution/writing runs for real."""
    monkeypatch.chdir(project_root)

    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = plan
    mock_planner.explain_plan.return_value = "Why this plan."

    args = argparse.Namespace(
        summary=plan.task_summary,
        save=True,
        dry_run=False,
        explain=False,
        json=False,
        verbose=False,
        manager_mode=True,
        import_path=None,
        template=False,
        task_type=None,
        agents=None,
        project=None,
        knowledge=[],
        knowledge_pack=[],
        intervention="low",
        model=None,
        complexity=None,
        save_as_template=None,
        from_template=None,
        skip_init=False,
        release_id=None,
        gate_scope=None,
        goal=None,
        max_amend_cycles=3,
    )

    patches = [
        patch("agent_baton.cli.commands.execution.plan_cmd.IntelligentPlanner", return_value=mock_planner),
        patch("agent_baton.cli.commands.execution.plan_cmd.RetrospectiveEngine", return_value=MagicMock()),
        patch("agent_baton.cli.commands.execution.plan_cmd.DataClassifier", return_value=MagicMock()),
        patch("agent_baton.cli.commands.execution.plan_cmd.PolicyEngine", return_value=MagicMock()),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        plan_cmd.handler(args)

    return project_root / ".claude" / "team-context"


# ---------------------------------------------------------------------------
# PMO path -- POST /pmo/forge/plan -> POST /pmo/forge/approve, at the
# ForgeSession layer (the API route is a thin HTTP wrapper over exactly
# this call -- see tests/api/test_pmo_manager_journey.py for the HTTP-level
# equivalent of the *approve* half of this journey).
# ---------------------------------------------------------------------------


def _run_pmo_forge_save(project_root: Path, plan: MachinePlan) -> Path:
    project_root.mkdir(parents=True, exist_ok=True)
    project = PmoProject(project_id="parity-proj", name="Parity", path=str(project_root), program="PAR")
    disabled_headless = HeadlessClaude(HeadlessConfig(claude_path="/nonexistent/claude"))
    forge = ForgeSession(planner=MagicMock(), store=MagicMock(), headless=disabled_headless)

    plan.manager_mode = True
    forge.save_plan(plan, project)  # manager_config=None -> ManagerConfig() default, same as CLI's default
    return project_root / ".claude" / "team-context"


# ---------------------------------------------------------------------------
# Comparison helpers
# ---------------------------------------------------------------------------


def _step_ids(plan_dict: dict) -> list[str]:
    return [s["step_id"] for p in plan_dict["phases"] for s in p["steps"]]


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _dir_stems(d: Path) -> set[str]:
    return {p.stem for p in d.glob("*") if p.is_file()}


def _normalize_root(value: Any, root: str) -> Any:
    """Recursively replace every occurrence of *root* (the CLI or PMO
    side's absolute ``.../executions/<task_id>`` prefix) in string values
    with a placeholder, so two artifact trees rooted at different tmp
    directories can still be compared for genuine content equality."""
    if isinstance(value, str):
        return value.replace(root, "<ROOT>")
    if isinstance(value, list):
        return [_normalize_root(v, root) for v in value]
    if isinstance(value, dict):
        return {k: _normalize_root(v, root) for k, v in value.items()}
    return value


# ---------------------------------------------------------------------------
# The parity test
# ---------------------------------------------------------------------------


class TestCliPmoManagerModeParity:
    @pytest.fixture()
    def built(self, tmp_path: Path, monkeypatch: Any):
        cli_root = tmp_path / "cli-side"
        cli_root.mkdir()
        pmo_root = tmp_path / "pmo-side"

        cli_ctx = _run_cli_manager_save(cli_root, monkeypatch, _independent_copy())
        pmo_ctx = _run_pmo_forge_save(pmo_root, _independent_copy())

        cli_paths = ManagerArtifactPaths(cli_ctx, TASK_ID)
        pmo_paths = ManagerArtifactPaths(pmo_ctx, TASK_ID)
        return cli_paths, pmo_paths

    def test_persisted_plan_json_step_shape_matches(self, built) -> None:
        cli_paths, pmo_paths = built
        cli_plan = _read_json(cli_paths.root / "plan.json")
        pmo_plan = _read_json(pmo_paths.root / "plan.json")

        cli_steps = _step_ids(cli_plan)
        pmo_steps = _step_ids(pmo_plan)
        assert cli_steps == pmo_steps
        # Sanity: the default policy really did inject review steps beyond
        # the two hand-authored ones -- otherwise this test would pass
        # trivially without exercising PhasePolicyApplier at all.
        assert len(cli_steps) > 2, "expected PhasePolicyApplier to inject review step(s)"

        for cli_phase, pmo_phase in zip(cli_plan["phases"], pmo_plan["phases"]):
            assert cli_phase["phase_id"] == pmo_phase["phase_id"]
            assert cli_phase["name"] == pmo_phase["name"]
            assert [s["step_id"] for s in cli_phase["steps"]] == [s["step_id"] for s in pmo_phase["steps"]]
            assert [s["agent_name"] for s in cli_phase["steps"]] == [s["agent_name"] for s in pmo_phase["steps"]]

    def test_charter_markdown_is_identical(self, built) -> None:
        cli_paths, pmo_paths = built
        assert cli_paths.charter.read_text(encoding="utf-8") == pmo_paths.charter.read_text(encoding="utf-8")

    def test_scope_map_is_identical(self, built) -> None:
        cli_paths, pmo_paths = built
        assert _read_json(cli_paths.scope_map) == _read_json(pmo_paths.scope_map)

    def test_team_blueprint_is_identical(self, built) -> None:
        cli_paths, pmo_paths = built
        assert _read_json(cli_paths.team_blueprint) == _read_json(pmo_paths.team_blueprint)

    def test_knowledge_plan_is_identical(self, built) -> None:
        cli_paths, pmo_paths = built
        assert _read_json(cli_paths.knowledge_plan) == _read_json(pmo_paths.knowledge_plan)

    def test_manager_brief_is_identical(self, built) -> None:
        cli_paths, pmo_paths = built
        assert (
            cli_paths.manager_brief.read_text(encoding="utf-8")
            == pmo_paths.manager_brief.read_text(encoding="utf-8")
        )

    def test_role_cards_are_identical(self, built) -> None:
        cli_paths, pmo_paths = built
        cli_roles = _dir_stems(cli_paths.role_cards_dir)
        pmo_roles = _dir_stems(pmo_paths.role_cards_dir)
        assert cli_roles == pmo_roles
        assert cli_roles, "expected at least one role card"
        for role in cli_roles:
            cli_text = cli_paths.role_card(role).read_text(encoding="utf-8")
            pmo_text = pmo_paths.role_card(role).read_text(encoding="utf-8")
            assert cli_text == pmo_text, f"role card {role!r} diverged between CLI and PMO"

    def test_every_scope_contract_is_identical(self, built) -> None:
        cli_paths, pmo_paths = built
        cli_stems = {p.stem for p in cli_paths.scope_contracts_dir.glob("*.json")}
        pmo_stems = {p.stem for p in pmo_paths.scope_contracts_dir.glob("*.json")}
        assert cli_stems == pmo_stems
        assert cli_stems, "expected at least one scope contract"
        for stem in cli_stems:
            cli_json = _read_json(cli_paths.scope_contracts_dir / f"{stem}.json")
            pmo_json = _read_json(pmo_paths.scope_contracts_dir / f"{stem}.json")
            assert cli_json == pmo_json, f"scope contract {stem!r} JSON diverged"
            cli_md = (cli_paths.scope_contracts_dir / f"{stem}.md").read_text(encoding="utf-8")
            pmo_md = (pmo_paths.scope_contracts_dir / f"{stem}.md").read_text(encoding="utf-8")
            assert cli_md == pmo_md, f"scope contract {stem!r} Markdown diverged"

    def test_every_context_bundle_is_identical(self, built) -> None:
        """Byte-equivalent bundles (modulo the tmp-dir root prefix in
        absolute paths): same step ids, same reference paths/reasons/kinds,
        AND the same token estimates / truncation warnings -- the
        token-accounting divergence an earlier revision of this suite had
        to strip out was fixed (see the module docstring)."""
        cli_paths, pmo_paths = built
        cli_stems = {p.stem for p in cli_paths.context_bundles_dir.glob("*.json")}
        pmo_stems = {p.stem for p in pmo_paths.context_bundles_dir.glob("*.json")}
        assert cli_stems == pmo_stems
        assert cli_stems, "expected at least one context bundle"
        for stem in cli_stems:
            cli_json = _normalize_root(_read_json(cli_paths.context_bundles_dir / f"{stem}.json"), str(cli_paths.root))
            pmo_json = _normalize_root(_read_json(pmo_paths.context_bundles_dir / f"{stem}.json"), str(pmo_paths.root))
            assert cli_json == pmo_json, f"context bundle {stem!r} diverged between CLI and PMO"

    def test_context_bundle_token_estimates_match_and_are_nonzero(self, built) -> None:
        """Regression test (phase 7 review): the PMO/``rebuild_and_publish``
        path used to estimate must-read tokens by re-reading
        scope-contract/role-card files that had not been written yet, so
        every PMO bundle carried ``token_estimate == 0`` plus a spurious
        "Missing file for token estimate" warning, while the CLI path's did
        not. Both paths now estimate from the rendered in-memory text
        (``ContextBundleBuilder.build``'s ``contract_text`` /
        ``role_card_text``), so both must produce real, matching,
        nonzero estimates and no missing-file noise."""
        cli_paths, pmo_paths = built
        cli_bundle = _read_json(cli_paths.context_bundles_dir / "1.1.json")
        pmo_bundle = _read_json(pmo_paths.context_bundles_dir / "1.1.json")

        for side, bundle in (("cli", cli_bundle), ("pmo", pmo_bundle)):
            assert not any(
                "Missing file for token estimate" in w for w in bundle["truncation_warnings"]
            ), f"{side} bundle carries spurious missing-file warnings"
            assert all(ref["token_estimate"] > 0 for ref in bundle["must_read"]), (
                f"{side} bundle has zeroed must-read token estimates"
            )

        assert [r["token_estimate"] for r in cli_bundle["must_read"]] == [
            r["token_estimate"] for r in pmo_bundle["must_read"]
        ]
        assert cli_bundle["estimated_tokens"] == pmo_bundle["estimated_tokens"] > 0

    def test_pmo_records_a_revision_manifest_cli_does_not(self, built) -> None:
        """Documents the one intentional asymmetry (see module docstring
        and ``ForgeSession.save_plan``'s docstring): only the PMO path
        (``rebuild_and_publish``) writes ``artifact-revision.json`` --
        the CLI's ``build_and_write`` does not. Guards against either side
        silently changing that contract."""
        cli_paths, pmo_paths = built
        assert not cli_paths.revision_manifest.exists()
        assert pmo_paths.revision_manifest.exists()
        manifest = _read_json(pmo_paths.revision_manifest)
        assert manifest["revision"] == 1
        assert manifest["trigger"] == "forge_approve"
