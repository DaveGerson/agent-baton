"""Transactional manager-artifact regeneration + publishing (Phase 6, 6.3
"Improve planning specificity and prevent context rot").

Every accepted plan mutation (scope expansion, goal round-out, feedback
remediation, or a manual/CLI amendment -- anything that changes
``MachinePlan.phases``/``PlanStep`` for a ``manager_mode`` plan) must leave
the charter, scope map, team blueprint, role cards, knowledge plan, scope
contracts, and context bundles describing the SAME plan revision the
engine is about to execute against. Before this module, none of the
runtime amendment paths in ``agent_baton.core.engine.executor`` touched
the manager-mode sidecars at all -- ``ManagerModePlanner.build_and_write``
only ever ran once, at ``baton plan --save`` time -- so an amended plan's
sidecars silently went stale (missing scope contracts / context bundles
for newly inserted steps, a knowledge plan that never saw them, a
blueprint whose ``workstream_assignments`` predate the amendment).

:func:`rebuild_and_publish` closes that gap: given a *proposed* plan (the
caller's plan object AFTER it has applied its own phase/step mutation,
optionally on a throwaway copy -- see the caller-contract note below), it
re-runs the full ``ManagerModePlanner.build()`` composition, validates the
result's cross-artifact references via :func:`validate_manager_artifacts`,
stages every sidecar's final bytes to same-directory temp files, and only
then atomically publishes (renames) every one of them plus a monotonic
revision manifest -- all-or-nothing. A validation failure or a staged
write failure leaves every previously published file (and the immutable
``decision-log.jsonl`` / ``decisions/`` / ``scope-evidence/`` trees, which
this module never touches) byte-for-byte untouched.

Caller contract for full plan-level rollback (not just sidecars): this
function's OWN mutation of *plan* is limited to what
``ManagerModePlanner.build()`` already does internally (running
``PhasePolicyApplier`` -- the plan graph's one sanctioned mutator -- which
may inject an adversarial-review step into a newly added phase, exactly as
it would at initial ``baton plan --save`` time). It never touches phases/
steps the caller didn't already add. A caller that wants "if publishing
fails, the plan itself must look exactly like it did before this call" --
i.e. real transactional plan+sidecar rollback -- must pass a deep copy of
its live plan, and only swap that copy back into its own authoritative
state once ``ok=True`` comes back. See
``agent_baton.core.engine.executor.ExecutionEngine.amend_plan`` for the
reference implementation of that pattern.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from agent_baton.core.manager.artifacts import ManagerArtifacts, render_all
from agent_baton.core.manager.context_bundles import is_nontrivial_step
from agent_baton.core.manager.paths import ManagerArtifactPaths

if TYPE_CHECKING:
    from agent_baton.core.config.manager import ManagerConfig
    from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry
    from agent_baton.models.execution import MachinePlan

logger = logging.getLogger(__name__)

__all__ = [
    "ManagerArtifactPublishError",
    "ManagerArtifactRebuildResult",
    "validate_manager_artifacts",
    "rebuild_and_publish",
    "load_revision_manifest",
    "plan_fingerprint",
]


class ManagerArtifactPublishError(RuntimeError):
    """Raised by callers (never by this module) when a rebuild's ``ok=False``
    result must abort the triggering plan mutation entirely.

    This module itself never raises -- :func:`rebuild_and_publish` always
    returns a :class:`ManagerArtifactRebuildResult`, even on failure, so a
    caller that wants to inspect ``.errors`` before deciding how to react
    (log-and-continue vs. hard-fail) can do so without a try/except. This
    exception type exists purely as the conventional "I decided to hard-fail"
    signal callers may raise after inspecting a failed result.
    """


@dataclass
class ManagerArtifactRebuildResult:
    """Outcome of :func:`rebuild_and_publish`.

    ``ok=False`` guarantees zero filesystem side effects: every candidate
    write happened only to a temp sibling file, and every temp file was
    removed before returning.
    """

    ok: bool
    artifacts: "ManagerArtifacts | None" = None
    errors: list[str] = field(default_factory=list)
    published_paths: list[Path] = field(default_factory=list)
    revision: int = 0


# ---------------------------------------------------------------------------
# Cross-artifact reference validator
# ---------------------------------------------------------------------------


def validate_manager_artifacts(
    plan: "MachinePlan", artifacts: ManagerArtifacts
) -> list[str]:
    """Return a list of human-readable cross-reference errors in *artifacts*
    relative to *plan* -- empty when the artifact set is internally
    consistent.

    Checks (all deterministic, no filesystem/network IO):

    1. Every nontrivial step (:func:`is_nontrivial_step`) in *plan* has
       exactly one scope contract and one context bundle, and neither
       sidecar map references a step_id that doesn't exist in *plan*.
    2. Every scope contract's ``workstream_id`` resolves to a workstream in
       ``artifacts.scope_map`` (when a scope map was built).
    3. Every blueprint role named in ``workstream_assignments`` -- and every
       role card the blueprint itself declares -- has rendered role-card
       markdown.
    4. Every context bundle is keyed by its own ``step_id`` and has a
       paired scope contract.
    5. ``knowledge_plan.per_step_packs`` only references step_ids that exist
       in *plan*.
    6. Plan-graph sanity: no duplicate ``step_id`` across phases (a
       ``MachinePlan`` validator already forbids this at construction, but
       a rebuild caller may have hand-built phases before the plan-level
       validator saw them -- this is defense in depth, not redundant).
    """
    errors: list[str] = []

    step_ids: set[str] = set()
    nontrivial_step_ids: set[str] = set()
    for phase in plan.phases:
        for step in phase.steps:
            if step.step_id in step_ids:
                errors.append(f"duplicate step_id in plan: {step.step_id!r}")
            step_ids.add(step.step_id)
            if is_nontrivial_step(step):
                nontrivial_step_ids.add(step.step_id)

    contract_ids = set(artifacts.scope_contracts)
    bundle_ids = set(artifacts.context_bundles)

    missing_contracts = nontrivial_step_ids - contract_ids
    missing_bundles = nontrivial_step_ids - bundle_ids
    orphan_contracts = contract_ids - step_ids
    orphan_bundles = bundle_ids - step_ids

    if missing_contracts:
        errors.append(
            f"steps missing a scope contract: {sorted(missing_contracts)}"
        )
    if missing_bundles:
        errors.append(
            f"steps missing a context bundle: {sorted(missing_bundles)}"
        )
    if orphan_contracts:
        errors.append(
            f"scope contracts reference unknown steps: {sorted(orphan_contracts)}"
        )
    if orphan_bundles:
        errors.append(
            f"context bundles reference unknown steps: {sorted(orphan_bundles)}"
        )

    if artifacts.scope_map is not None:
        workstream_ids = {ws.id for ws in artifacts.scope_map.workstreams if ws.id}
        for step_id, contract in artifacts.scope_contracts.items():
            if contract.workstream_id and contract.workstream_id not in workstream_ids:
                errors.append(
                    f"scope contract {step_id!r} references unknown workstream "
                    f"{contract.workstream_id!r}"
                )

    if artifacts.blueprint is not None:
        for ws_id, role in artifacts.blueprint.workstream_assignments.items():
            if role and role not in artifacts.role_cards_md:
                errors.append(
                    f"blueprint assigns workstream {ws_id!r} to role {role!r} "
                    "with no rendered role card"
                )
        blueprint_roles = {card.role for card in artifacts.blueprint.roles if card.role}
        missing_role_md = blueprint_roles - set(artifacts.role_cards_md)
        if missing_role_md:
            errors.append(
                f"blueprint roles missing rendered role-card markdown: {sorted(missing_role_md)}"
            )

    for step_id, bundle in artifacts.context_bundles.items():
        if bundle.step_id != step_id:
            errors.append(
                f"context bundle keyed {step_id!r} carries mismatched "
                f"bundle.step_id {bundle.step_id!r}"
            )
        if step_id not in artifacts.scope_contracts:
            errors.append(f"context bundle {step_id!r} has no paired scope contract")

    if artifacts.knowledge_plan is not None:
        unknown_steps = set(artifacts.knowledge_plan.per_step_packs) - step_ids
        if unknown_steps:
            errors.append(
                "knowledge plan per_step_packs references unknown steps: "
                f"{sorted(unknown_steps)}"
            )

    return errors


# ---------------------------------------------------------------------------
# Staged, all-or-nothing filesystem publish
# ---------------------------------------------------------------------------


def _stage_write(rendered: list[tuple[Path, str]]) -> tuple[list[tuple[Path, Path]], list[str]]:
    """Write every ``(final_path, text)`` pair to a same-directory temp
    sibling file. Returns ``(staged, errors)``.

    On the first failure, every temp file already written in THIS call is
    removed before returning -- callers see either "every candidate file
    has a temp sibling ready to publish" or "no temp files exist at all",
    never a partial set.
    """
    staged: list[tuple[Path, Path]] = []
    for final_path, text in rendered:
        final_path = Path(final_path)
        tmp_path = final_path.with_name(
            f".{final_path.name}.rebuild-tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )
        try:
            final_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            for staged_tmp, _staged_final in staged:
                staged_tmp.unlink(missing_ok=True)
            tmp_path.unlink(missing_ok=True)
            return [], [f"staged write failed for {final_path}: {exc}"]
        staged.append((tmp_path, final_path))
    return staged, []


def _publish_staged(staged: list[tuple[Path, Path]]) -> list[Path]:
    """Rename every staged temp file onto its final path.

    Each individual ``os.replace`` is atomic (same-filesystem, guaranteed
    by :func:`_stage_write` placing the temp file as a sibling of its
    final path). The *sequence* of renames is not itself a single
    filesystem transaction -- the same filesystem-level-atomicity caveat
    documented on ``agent_baton.core.manager.scope_amendment``'s
    ``_atomic_write_text`` applies here: a crash mid-sequence could leave
    some files published and others not. That risk window is reached only
    after every write has already succeeded (see :func:`_stage_write`), so
    the only remaining failure mode is a rename itself failing (disk full,
    permissions changing mid-flight) -- vanishingly rare compared to a
    content-write failure, and the same residual risk every other
    manager-mode sidecar writer in this codebase already carries.
    """
    published: list[Path] = []
    for tmp_path, final_path in staged:
        os.replace(tmp_path, final_path)
        published.append(final_path)
    return published


def _discard_staged(staged: list[tuple[Path, Path]]) -> None:
    for tmp_path, _final_path in staged:
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Revision manifest
# ---------------------------------------------------------------------------


def load_revision_manifest(paths: ManagerArtifactPaths) -> "dict | None":
    """Load the manifest written by the most recent successful
    :func:`rebuild_and_publish` call, or ``None`` when absent/unreadable."""
    path = paths.revision_manifest
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def plan_fingerprint(plan: "MachinePlan") -> str:
    """A short, order-sensitive digest of *plan*'s phase/step shape.

    Not a security control -- purely a cheap "did the published sidecars
    correspond to this exact step list" debugging aid surfaced in the
    revision manifest. Public (Phase 7 "Turn PMO into the director
    console"): the manager-mode validation API
    (``agent_baton/api/routes/pmo_manager.py``) recomputes this over the
    CURRENT persisted plan and compares it against the manifest's recorded
    fingerprint to answer "is the published management view still
    version-consistent with the plan on disk" without re-running the full
    ``ManagerModePlanner`` composition.
    """
    step_ids = [step.step_id for phase in plan.phases for step in phase.steps]
    raw = json.dumps(
        {"phase_ids": [p.phase_id for p in plan.phases], "step_ids": step_ids},
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# Backward-compat alias for the module-private name used before this was
# promoted to a public helper -- keeps any existing internal call sites
# (and this module's own rebuild_and_publish, below) working unchanged.
_plan_fingerprint = plan_fingerprint


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def rebuild_and_publish(
    plan: "MachinePlan",
    task_summary: str,
    *,
    config: "ManagerConfig",
    project_root: Path,
    team_context_dir: Path,
    trigger: str,
    knowledge_registry: "KnowledgeRegistry | None" = None,
    cli_gate_scope_explicit: bool = False,
    strict_scope: bool = False,
) -> ManagerArtifactRebuildResult:
    """Rebuild every manager-mode sidecar artifact for *plan* and publish
    it transactionally. See the module docstring for the full contract.

    *trigger* is a short label (``"scope_expansion"``, ``"goal_round_out"``,
    ``"feedback"``, ``"approval_feedback"``, ``"manual"``, ...) recorded on
    the revision manifest -- purely diagnostic, never branched on.
    """
    from agent_baton.core.manager.planner import ManagerModePlanner

    paths = ManagerArtifactPaths(Path(team_context_dir).resolve(), plan.task_id)

    planner = ManagerModePlanner(
        config,
        project_root=project_root,
        team_context_dir=team_context_dir,
        knowledge_registry=knowledge_registry,
        cli_gate_scope_explicit=cli_gate_scope_explicit,
        strict_scope=strict_scope,
    )

    try:
        artifacts = planner.build(plan, task_summary)
    except Exception as exc:  # noqa: BLE001 — surface as a normal failure result
        logger.warning(
            "rebuild_and_publish: ManagerModePlanner.build raised for "
            "task_id=%r trigger=%r: %s", plan.task_id, trigger, exc,
        )
        return ManagerArtifactRebuildResult(
            ok=False, errors=[f"artifact build raised {type(exc).__name__}: {exc}"]
        )

    errors = validate_manager_artifacts(plan, artifacts)
    if errors:
        return ManagerArtifactRebuildResult(ok=False, artifacts=artifacts, errors=errors)

    rendered = render_all(paths, artifacts)
    staged, stage_errors = _stage_write(rendered)
    if stage_errors:
        return ManagerArtifactRebuildResult(
            ok=False, artifacts=artifacts, errors=stage_errors
        )

    prior_manifest = load_revision_manifest(paths) or {}
    prior_revision = int(prior_manifest.get("revision", 0) or 0)
    next_revision = prior_revision + 1
    manifest = {
        "revision": next_revision,
        "prior_revision": prior_revision,
        "trigger": trigger,
        "created_at": _now_iso(),
        "task_id": plan.task_id,
        "plan_fingerprint": _plan_fingerprint(plan),
        "phase_count": len(plan.phases),
        "step_count": sum(len(p.steps) for p in plan.phases),
        "published_paths": [str(final_path) for _tmp, final_path in staged],
    }
    manifest_path = paths.revision_manifest
    manifest_tmp = manifest_path.with_name(
        f".{manifest_path.name}.rebuild-tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    )
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_tmp.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        _discard_staged(staged)
        manifest_tmp.unlink(missing_ok=True)
        return ManagerArtifactRebuildResult(
            ok=False,
            artifacts=artifacts,
            errors=[f"revision manifest staging failed: {exc}"],
        )

    published = _publish_staged(staged)
    os.replace(manifest_tmp, manifest_path)
    published.append(manifest_path)

    return ManagerArtifactRebuildResult(
        ok=True,
        artifacts=artifacts,
        published_paths=published,
        revision=next_revision,
    )


def _now_iso() -> str:
    from agent_baton.utils.time import utcnow_zulu

    return utcnow_zulu()
