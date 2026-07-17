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
superset of ``build_and_write``, see that docstring), any field generated
from a wall-clock timestamp, and per-reference ``token_estimate`` /
``estimated_tokens`` / ``truncation_warnings`` in context bundles -- see
``TestCliPmoManagerModeParity.test_context_bundle_token_estimates_are_a_
known_pmo_vs_cli_gap`` below, which pins a CONFIRMED, currently-live
discrepancy this test file discovered rather than silently masking it:
``ManagerModePlanner.build()`` (used by ``rebuild_and_publish`` /
PMO) runs with ``persist_sidecars_early=False``, so
``ContextBundleBuilder`` estimates must-read token counts for scope
contracts and role cards BEFORE those files exist on disk -- every one
comes back as a 0-token estimate plus a spurious "Missing file for token
estimate" truncation warning. ``ManagerModePlanner.build_and_write()``
(the CLI path) writes those same files early specifically so the
estimator can read them, and never hits this. Net effect: a manager-mode
plan created via Forge/PMO gets systematically worse context-bundle
token accounting than the identical plan created via the CLI. Fixing
this is out of scope for this test-authoring step (it requires editing
``agent_baton/core/manager/planner.py`` / ``context_bundles.py``, outside
this step's allowed paths) -- see this suite's structured report for the
recommended follow-up.

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

    def test_manager_brief_is_identical_modulo_known_token_estimate_gap(self, built) -> None:
        """Byte-identical except for the "Missing file for token estimate"
        truncation-warning lines the PMO path spuriously emits -- see the
        module docstring and ``test_context_bundle_token_estimates_are_a_
        known_pmo_vs_cli_gap`` below for the confirmed root cause."""
        cli_paths, pmo_paths = built
        cli_lines = [
            ln for ln in cli_paths.manager_brief.read_text(encoding="utf-8").splitlines()
            if "Missing file for token estimate" not in ln
        ]
        pmo_lines = [
            ln for ln in pmo_paths.manager_brief.read_text(encoding="utf-8").splitlines()
            if "Missing file for token estimate" not in ln
        ]
        assert cli_lines == pmo_lines

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

    @staticmethod
    def _strip_token_estimate_gap(bundle: dict) -> dict:
        """Return *bundle* with the fields known to diverge due to the
        ``persist_sidecars_early`` ordering gap (see module docstring)
        zeroed/cleared out, so the REST of the bundle's structure and
        content can still be compared for genuine equivalence."""
        out = dict(bundle)
        out["estimated_tokens"] = None
        # Keep unrelated warnings (e.g. "Phantom knowledge pack ...", which
        # is identical on both sides and IS part of the genuine structural
        # comparison) -- only strip the ones caused by the known gap.
        out["truncation_warnings"] = [
            w for w in bundle.get("truncation_warnings", [])
            if "Missing file for token estimate" not in w
        ]
        for key in ("must_read", "reference_only"):
            out[key] = [{**ref, "token_estimate": None} for ref in bundle.get(key, [])]
        return out

    def test_every_context_bundle_is_structurally_identical(self, built) -> None:
        """Same step ids, same reference paths/reasons/kinds -- everything
        except the known token-estimate/truncation-warning gap (see below)."""
        cli_paths, pmo_paths = built
        cli_stems = {p.stem for p in cli_paths.context_bundles_dir.glob("*.json")}
        pmo_stems = {p.stem for p in pmo_paths.context_bundles_dir.glob("*.json")}
        assert cli_stems == pmo_stems
        assert cli_stems, "expected at least one context bundle"
        for stem in cli_stems:
            cli_json = _normalize_root(_read_json(cli_paths.context_bundles_dir / f"{stem}.json"), str(cli_paths.root))
            pmo_json = _normalize_root(_read_json(pmo_paths.context_bundles_dir / f"{stem}.json"), str(pmo_paths.root))
            assert self._strip_token_estimate_gap(cli_json) == self._strip_token_estimate_gap(pmo_json), (
                f"context bundle {stem!r} diverged beyond the known token-estimate gap"
            )

    def test_context_bundle_token_estimates_are_a_known_pmo_vs_cli_gap(self, built) -> None:
        """Pins the CONFIRMED, currently-live discrepancy this parity
        suite discovered: the PMO path's context bundles come back with
        0 token estimates and a "Missing file for token estimate"
        truncation warning for every must-read scope-contract/role-card
        reference, while the CLI path's do not, purely because of write
        ordering (``persist_sidecars_early``) -- not any real difference
        in plan/config input. This test intentionally asserts the
        CURRENT (undesirable) behavior so it is visible and tracked
        rather than silently normalized away; it should be deleted (not
        loosened) the day ``ManagerModePlanner.build()`` is fixed to
        either persist sidecars early too or estimate tokens from the
        in-memory rendered text instead of re-reading from disk."""
        cli_paths, pmo_paths = built
        cli_bundle = _read_json(cli_paths.context_bundles_dir / "1.1.json")
        pmo_bundle = _read_json(pmo_paths.context_bundles_dir / "1.1.json")

        assert not any("Missing file for token estimate" in w for w in cli_bundle["truncation_warnings"])
        assert all(ref["token_estimate"] > 0 for ref in cli_bundle["must_read"])

        assert any("Missing file for token estimate" in w for w in pmo_bundle["truncation_warnings"])
        assert all(ref["token_estimate"] == 0 for ref in pmo_bundle["must_read"])

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
