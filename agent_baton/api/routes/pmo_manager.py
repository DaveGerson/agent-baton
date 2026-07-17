"""Manager-mode PMO API — the "director console" read + narrow-mutation
surface for a manager-mode plan's full sidecar artifact set (Phase 7 "Turn
PMO into the director console").

GET  /pmo/manager/{card_id}/charter                        — project charter (Markdown)
GET  /pmo/manager/{card_id}/scope-map                       — scope map (workstreams)
GET  /pmo/manager/{card_id}/workstreams                     — phase <-> workstream links
GET  /pmo/manager/{card_id}/team-blueprint                  — team blueprint
GET  /pmo/manager/{card_id}/role-cards                      — every role card (Markdown)
GET  /pmo/manager/{card_id}/role-cards/{role}                — one role card (Markdown)
GET  /pmo/manager/{card_id}/knowledge-plan                  — plan-wide knowledge selection
GET  /pmo/manager/{card_id}/scope-contracts                 — every step's contract (summary)
GET  /pmo/manager/{card_id}/scope-contracts/{step_id}        — one step's full contract
GET  /pmo/manager/{card_id}/context-bundles                 — every step's bundle (metadata)
GET  /pmo/manager/{card_id}/context-bundles/{step_id}         — one step's full bundle
GET  /pmo/manager/{card_id}/report                          — manager brief + report Markdown
GET  /pmo/manager/{card_id}/decisions                       — decision packets (decision-log.jsonl)
GET  /pmo/manager/{card_id}/decisions/{decision_id}           — one decision packet
POST /pmo/manager/{card_id}/decisions/{decision_id}/resolve   — approve/reject a scope-expansion decision
GET  /pmo/manager/{card_id}/version                          — published artifact-revision manifest
GET  /pmo/manager/{card_id}/validation                       — version-consistency check

Every read is scoped to a single ``card_id`` (resolved to its owning
project/context root exactly like the existing per-card decision endpoints
in ``api/routes/pmo.py`` — see ``_resolve_worker_context``, reused here) and
reads ONLY through ``agent_baton.core.manager.paths.ManagerArtifactPaths``'s
conventional, sanitized path builders — never a client-supplied filesystem
path. A card whose plan is not ``manager_mode`` returns 409 (a real
task exists, just not in the requested state); a card that doesn't exist at
all returns 404; an individual artifact file that is absent (e.g. a role or
step_id the plan doesn't have) returns 404 too.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from agent_baton.api.deps import get_bus, get_pmo_scanner, get_pmo_store
from agent_baton.api.models.requests import ManagerDecisionResolveRequest
from agent_baton.api.models.responses import (
    ManagerCharterResponse,
    ManagerContextBundleResponse,
    ManagerContextBundleSummary,
    ManagerContextBundlesResponse,
    ManagerDecisionListResponse,
    ManagerDecisionResolveResponse,
    ManagerDecisionResponse,
    ManagerKnowledgePlanResponse,
    ManagerReportResponse,
    ManagerRoleCardResponse,
    ManagerRoleCardsResponse,
    ManagerScopeContractResponse,
    ManagerScopeContractSummary,
    ManagerScopeContractsResponse,
    ManagerScopeMapResponse,
    ManagerTeamBlueprintResponse,
    ManagerValidationResponse,
    ManagerVersionResponse,
    ManagerWorkstreamPhaseLink,
    ManagerWorkstreamsResponse,
)
# Reuse the existing card -> (card, project_root, context_root) resolver
# rather than duplicating it: both modules resolve a PMO card to its owning
# project's `.claude/team-context` directory identically, and pmo.py already
# owns that logic (used by the pause/resume/cancel/retry-step/decisions
# endpoints).
from agent_baton.api.routes.pmo import _resolve_worker_context
from agent_baton.core.events.bus import EventBus
from agent_baton.core.manager.paths import ManagerArtifactPaths
from agent_baton.core.manager.rebuild import load_revision_manifest, plan_fingerprint
from agent_baton.core.manager.scope_amendment import load_decision
from agent_baton.core.orchestration.context import ContextManager
from agent_baton.core.pmo.scanner import PmoScanner
from agent_baton.core.pmo.store import PmoStore

router = APIRouter()


# ---------------------------------------------------------------------------
# Shared resolution helpers
# ---------------------------------------------------------------------------


def _manager_paths(context_root: Path, card_id: str) -> ManagerArtifactPaths:
    return ManagerArtifactPaths(context_root, card_id)


def _require_manager_plan(context_root: Path, card_id: str) -> Any:
    """Load the persisted plan for *card_id* and assert it is manager_mode.

    Raises:
        HTTPException 404: No plan is persisted for this card.
        HTTPException 409: The plan exists but is not a manager-mode plan.
    """
    ctx = ContextManager(team_context_dir=context_root, task_id=card_id)
    plan = ctx.load_plan()
    if plan is None:
        raise HTTPException(
            status_code=404,
            detail=f"No plan found for card '{card_id}'.",
        )
    if not plan.manager_mode:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Card '{card_id}' is not a manager-mode plan; the manager "
                "console API is only available for plans with "
                "manager_mode=True."
            ),
        )
    return plan


def _envelope(paths: ManagerArtifactPaths) -> tuple[int | None, str | None]:
    """Return ``(revision, published_at)`` from the revision manifest, or
    ``(None, None)`` when nothing has ever been published."""
    manifest = load_revision_manifest(paths)
    if manifest is None:
        return None, None
    revision = manifest.get("revision")
    return (int(revision) if revision is not None else None), manifest.get("created_at")


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Charter
# ---------------------------------------------------------------------------


@router.get(
    "/pmo/manager/{card_id}/charter",
    response_model=ManagerCharterResponse,
    tags=["pmo-manager"],
)
async def get_manager_charter(
    card_id: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
) -> ManagerCharterResponse:
    """Return the project charter (Markdown — no JSON sidecar exists)."""
    _, _, context_root = _resolve_worker_context(card_id, scanner, store)
    _require_manager_plan(context_root, card_id)
    paths = _manager_paths(context_root, card_id)

    markdown = _read_text(paths.charter)
    if markdown is None:
        raise HTTPException(status_code=404, detail=f"No charter found for card '{card_id}'.")

    revision, published_at = _envelope(paths)
    return ManagerCharterResponse(
        task_id=card_id, revision=revision, published_at=published_at, markdown=markdown,
    )


# ---------------------------------------------------------------------------
# Scope map / workstreams
# ---------------------------------------------------------------------------


@router.get(
    "/pmo/manager/{card_id}/scope-map",
    response_model=ManagerScopeMapResponse,
    tags=["pmo-manager"],
)
async def get_manager_scope_map(
    card_id: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
) -> ManagerScopeMapResponse:
    """Return the scope map (workstream decomposition)."""
    _, _, context_root = _resolve_worker_context(card_id, scanner, store)
    _require_manager_plan(context_root, card_id)
    paths = _manager_paths(context_root, card_id)

    data = _read_json(paths.scope_map)
    if data is None:
        raise HTTPException(status_code=404, detail=f"No scope map found for card '{card_id}'.")

    revision, published_at = _envelope(paths)
    return ManagerScopeMapResponse(
        task_id=card_id, revision=revision, published_at=published_at, scope_map=data,
    )


@router.get(
    "/pmo/manager/{card_id}/workstreams",
    response_model=ManagerWorkstreamsResponse,
    tags=["pmo-manager"],
)
async def get_manager_workstreams(
    card_id: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
) -> ManagerWorkstreamsResponse:
    """Return each plan phase paired with its owning workstream.

    ``ManagerModePlanner`` builds exactly one ``Workstream`` per
    ``plan.phases`` entry, in order (see
    ``ManagerModePlanner._compose``'s ``workstream_by_phase_id``) — this
    endpoint reconstructs that same positional correspondence from the
    persisted plan and scope map so the UI doesn't have to.
    """
    _, _, context_root = _resolve_worker_context(card_id, scanner, store)
    plan = _require_manager_plan(context_root, card_id)
    paths = _manager_paths(context_root, card_id)

    data = _read_json(paths.scope_map)
    if data is None:
        raise HTTPException(status_code=404, detail=f"No scope map found for card '{card_id}'.")

    workstreams = data.get("workstreams", [])
    links = [
        ManagerWorkstreamPhaseLink(phase_id=phase.phase_id, phase_name=phase.name, workstream=ws)
        for phase, ws in zip(plan.phases, workstreams)
    ]
    return ManagerWorkstreamsResponse(task_id=card_id, links=links)


# ---------------------------------------------------------------------------
# Team blueprint / role cards
# ---------------------------------------------------------------------------


@router.get(
    "/pmo/manager/{card_id}/team-blueprint",
    response_model=ManagerTeamBlueprintResponse,
    tags=["pmo-manager"],
)
async def get_manager_team_blueprint(
    card_id: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
) -> ManagerTeamBlueprintResponse:
    """Return the ad-hoc team composition for this plan."""
    _, _, context_root = _resolve_worker_context(card_id, scanner, store)
    _require_manager_plan(context_root, card_id)
    paths = _manager_paths(context_root, card_id)

    data = _read_json(paths.team_blueprint)
    if data is None:
        raise HTTPException(status_code=404, detail=f"No team blueprint found for card '{card_id}'.")

    revision, published_at = _envelope(paths)
    return ManagerTeamBlueprintResponse(
        task_id=card_id, revision=revision, published_at=published_at, team_blueprint=data,
    )


@router.get(
    "/pmo/manager/{card_id}/role-cards",
    response_model=ManagerRoleCardsResponse,
    tags=["pmo-manager"],
)
async def list_manager_role_cards(
    card_id: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
) -> ManagerRoleCardsResponse:
    """Return every role card, rendered Markdown (the canonical dispatch form)."""
    _, _, context_root = _resolve_worker_context(card_id, scanner, store)
    _require_manager_plan(context_root, card_id)
    paths = _manager_paths(context_root, card_id)

    role_cards: list[ManagerRoleCardResponse] = []
    if paths.role_cards_dir.is_dir():
        for entry in sorted(paths.role_cards_dir.glob("*.md")):
            text = _read_text(entry)
            if text is not None:
                role_cards.append(ManagerRoleCardResponse(role=entry.stem, markdown=text))

    revision, published_at = _envelope(paths)
    return ManagerRoleCardsResponse(
        task_id=card_id, revision=revision, published_at=published_at, role_cards=role_cards,
    )


@router.get(
    "/pmo/manager/{card_id}/role-cards/{role}",
    response_model=ManagerRoleCardResponse,
    tags=["pmo-manager"],
)
async def get_manager_role_card(
    card_id: str,
    role: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
) -> ManagerRoleCardResponse:
    """Return a single role's card Markdown."""
    _, _, context_root = _resolve_worker_context(card_id, scanner, store)
    _require_manager_plan(context_root, card_id)
    paths = _manager_paths(context_root, card_id)

    text = _read_text(paths.role_card(role))
    if text is None:
        raise HTTPException(
            status_code=404,
            detail=f"No role card '{role}' found for card '{card_id}'.",
        )
    return ManagerRoleCardResponse(role=role, markdown=text)


# ---------------------------------------------------------------------------
# Knowledge plan
# ---------------------------------------------------------------------------


@router.get(
    "/pmo/manager/{card_id}/knowledge-plan",
    response_model=ManagerKnowledgePlanResponse,
    tags=["pmo-manager"],
)
async def get_manager_knowledge_plan(
    card_id: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
) -> ManagerKnowledgePlanResponse:
    """Return the plan-wide knowledge pack selection/gap analysis."""
    _, _, context_root = _resolve_worker_context(card_id, scanner, store)
    _require_manager_plan(context_root, card_id)
    paths = _manager_paths(context_root, card_id)

    data = _read_json(paths.knowledge_plan)
    if data is None:
        raise HTTPException(status_code=404, detail=f"No knowledge plan found for card '{card_id}'.")

    revision, published_at = _envelope(paths)
    return ManagerKnowledgePlanResponse(
        task_id=card_id, revision=revision, published_at=published_at, knowledge_plan=data,
    )


# ---------------------------------------------------------------------------
# Scope contracts
# ---------------------------------------------------------------------------


@router.get(
    "/pmo/manager/{card_id}/scope-contracts",
    response_model=ManagerScopeContractsResponse,
    tags=["pmo-manager"],
)
async def list_manager_scope_contracts(
    card_id: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
) -> ManagerScopeContractsResponse:
    """Return a summary of every nontrivial step's scope contract."""
    _, _, context_root = _resolve_worker_context(card_id, scanner, store)
    _require_manager_plan(context_root, card_id)
    paths = _manager_paths(context_root, card_id)

    contracts: list[ManagerScopeContractSummary] = []
    if paths.scope_contracts_dir.is_dir():
        for entry in sorted(paths.scope_contracts_dir.glob("*.json")):
            data = _read_json(entry)
            if data is None:
                continue
            contracts.append(
                ManagerScopeContractSummary(
                    step_id=data.get("step_id", entry.stem),
                    agent_name=data.get("agent_name", ""),
                    workstream_id=data.get("workstream_id", ""),
                    allowed_paths=list(data.get("allowed_paths", [])),
                )
            )

    revision, published_at = _envelope(paths)
    return ManagerScopeContractsResponse(
        task_id=card_id, revision=revision, published_at=published_at, contracts=contracts,
    )


@router.get(
    "/pmo/manager/{card_id}/scope-contracts/{step_id:path}",
    response_model=ManagerScopeContractResponse,
    tags=["pmo-manager"],
)
async def get_manager_scope_contract(
    card_id: str,
    step_id: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
) -> ManagerScopeContractResponse:
    """Return one step's full scope contract (JSON + rendered Markdown)."""
    _, _, context_root = _resolve_worker_context(card_id, scanner, store)
    _require_manager_plan(context_root, card_id)
    paths = _manager_paths(context_root, card_id)

    data = _read_json(paths.scope_contract(step_id, ext="json"))
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"No scope contract for step '{step_id}' found for card '{card_id}'.",
        )
    markdown = _read_text(paths.scope_contract(step_id, ext="md")) or ""

    revision, published_at = _envelope(paths)
    return ManagerScopeContractResponse(
        task_id=card_id,
        revision=revision,
        published_at=published_at,
        step_id=step_id,
        contract=data,
        markdown=markdown,
    )


# ---------------------------------------------------------------------------
# Context bundles
# ---------------------------------------------------------------------------


@router.get(
    "/pmo/manager/{card_id}/context-bundles",
    response_model=ManagerContextBundlesResponse,
    tags=["pmo-manager"],
)
async def list_manager_context_bundles(
    card_id: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
) -> ManagerContextBundlesResponse:
    """Return metadata (no document bodies) for every step's context bundle."""
    _, _, context_root = _resolve_worker_context(card_id, scanner, store)
    _require_manager_plan(context_root, card_id)
    paths = _manager_paths(context_root, card_id)

    bundles: list[ManagerContextBundleSummary] = []
    if paths.context_bundles_dir.is_dir():
        for entry in sorted(paths.context_bundles_dir.glob("*.json")):
            data = _read_json(entry)
            if data is None:
                continue
            bundles.append(
                ManagerContextBundleSummary(
                    step_id=data.get("step_id", entry.stem),
                    agent_name=data.get("agent_name", ""),
                    must_read_count=len(data.get("must_read", [])),
                    reference_only_count=len(data.get("reference_only", [])),
                    knowledge_pack_count=len(data.get("knowledge_packs", [])),
                    token_budget=int(data.get("token_budget", 0) or 0),
                    estimated_tokens=int(data.get("estimated_tokens", 0) or 0),
                    truncation_warnings=list(data.get("truncation_warnings", [])),
                )
            )

    revision, published_at = _envelope(paths)
    return ManagerContextBundlesResponse(
        task_id=card_id, revision=revision, published_at=published_at, bundles=bundles,
    )


@router.get(
    "/pmo/manager/{card_id}/context-bundles/{step_id:path}",
    response_model=ManagerContextBundleResponse,
    tags=["pmo-manager"],
)
async def get_manager_context_bundle(
    card_id: str,
    step_id: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
) -> ManagerContextBundleResponse:
    """Return one step's full context bundle."""
    _, _, context_root = _resolve_worker_context(card_id, scanner, store)
    _require_manager_plan(context_root, card_id)
    paths = _manager_paths(context_root, card_id)

    data = _read_json(paths.context_bundle(step_id))
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"No context bundle for step '{step_id}' found for card '{card_id}'.",
        )

    revision, published_at = _envelope(paths)
    return ManagerContextBundleResponse(
        task_id=card_id, revision=revision, published_at=published_at, step_id=step_id, bundle=data,
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@router.get(
    "/pmo/manager/{card_id}/report",
    response_model=ManagerReportResponse,
    tags=["pmo-manager"],
)
async def get_manager_report(
    card_id: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
) -> ManagerReportResponse:
    """Return the manager brief (always present post-save) and the
    manager report (a retrospective; only present post-execution)."""
    _, _, context_root = _resolve_worker_context(card_id, scanner, store)
    _require_manager_plan(context_root, card_id)
    paths = _manager_paths(context_root, card_id)

    brief = _read_text(paths.manager_brief)
    if brief is None:
        raise HTTPException(status_code=404, detail=f"No manager brief found for card '{card_id}'.")
    report = _read_text(paths.manager_report) or ""

    revision, published_at = _envelope(paths)
    return ManagerReportResponse(
        task_id=card_id,
        revision=revision,
        published_at=published_at,
        manager_brief=brief,
        manager_report=report,
    )


# ---------------------------------------------------------------------------
# Decision packets
# ---------------------------------------------------------------------------


def _read_decision_log(paths: ManagerArtifactPaths) -> list[dict]:
    """Parse ``decision-log.jsonl``, keeping only the LAST entry per
    ``decision_id`` (a later resolution supersedes the original filing) —
    mirrors ``agent_baton.core.manager.scope_amendment.load_decision``'s
    single-id lookup, generalized to the whole log."""
    text = _read_text(paths.decision_log)
    if not text:
        return []
    by_id: dict[str, dict] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        decision_id = data.get("decision_id")
        if decision_id:
            by_id[decision_id] = data
    return list(by_id.values())


@router.get(
    "/pmo/manager/{card_id}/decisions",
    response_model=ManagerDecisionListResponse,
    tags=["pmo-manager"],
)
async def list_manager_decisions(
    card_id: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
) -> ManagerDecisionListResponse:
    """Return every decision packet filed for this card's manager-mode plan.

    Distinct from ``GET /pmo/execute/{card_id}/decisions`` (the generic
    engine ``DecisionRequest`` inbox for APPROVAL/FEEDBACK/INTERACT
    actions): this surfaces the typed ``ManagerDecision`` packets
    (scope_expansion, ambiguity, knowledge_gap, review_veto, approval)
    ``DecisionPacketBuilder`` files to ``decision-log.jsonl``. An empty
    list is a valid response (no decisions filed yet), unlike a genuinely
    missing card/plan.

    Deliberately does NOT require a persisted ``plan.json``/manager-mode
    plan the way the artifact-read endpoints above do: decision packets
    are filed by ``DecisionPacketBuilder`` keyed only on task_id and can
    exist (e.g. a durable diff-derived scope-expansion decision) while an
    execution is only tracked via ``ExecutionState`` in the storage
    backend, with no ``plan.json`` sidecar ever written for this task_id.
    Only manager-mode plans ever produce a ``ManagerDecision`` in the
    first place, so this is never reachable for a plain plan in practice.
    """
    _, _, context_root = _resolve_worker_context(card_id, scanner, store)
    paths = _manager_paths(context_root, card_id)

    responses: list[ManagerDecisionResponse] = []
    for data in _read_decision_log(paths):
        decision_id = data.get("decision_id", "")
        markdown = _read_text(paths.decision(decision_id)) or "" if decision_id else ""
        responses.append(
            ManagerDecisionResponse(
                decision_id=decision_id,
                decision_type=data.get("decision_type", ""),
                task_id=data.get("task_id", ""),
                summary=data.get("summary", ""),
                context=data.get("context", ""),
                options=list(data.get("options", [])),
                recommended_option=data.get("recommended_option", ""),
                created_at=data.get("created_at", ""),
                resolved_at=data.get("resolved_at"),
                resolution=data.get("resolution"),
                markdown=markdown,
            )
        )

    return ManagerDecisionListResponse(task_id=card_id, count=len(responses), decisions=responses)


@router.get(
    "/pmo/manager/{card_id}/decisions/{decision_id}",
    response_model=ManagerDecisionResponse,
    tags=["pmo-manager"],
)
async def get_manager_decision(
    card_id: str,
    decision_id: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
) -> ManagerDecisionResponse:
    """Return one decision packet (current, i.e. post-resolution, state).

    Like ``list_manager_decisions``, this does not require a persisted
    manager-mode ``plan.json`` -- see that function's docstring.
    """
    _, _, context_root = _resolve_worker_context(card_id, scanner, store)
    paths = _manager_paths(context_root, card_id)

    decision = load_decision(paths, decision_id)
    if decision is None:
        raise HTTPException(
            status_code=404,
            detail=f"No decision '{decision_id}' found for card '{card_id}'.",
        )
    markdown = _read_text(paths.decision(decision_id)) or ""
    return ManagerDecisionResponse.from_manager_decision(decision, markdown=markdown)


@router.post(
    "/pmo/manager/{card_id}/decisions/{decision_id}/resolve",
    response_model=ManagerDecisionResolveResponse,
    tags=["pmo-manager"],
)
async def resolve_manager_decision(
    card_id: str,
    decision_id: str,
    body: ManagerDecisionResolveRequest,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
    bus: EventBus = Depends(get_bus),
) -> ManagerDecisionResolveResponse:
    """Approve or reject a scope-expansion decision.

    This is the plan-amendment mutation surface Phase 7 exposes: approving
    a diff-derived ``scope_expansion`` decision durably widens the failed
    step's scope contract (routed through
    ``ExecutionEngine.resolve_scope_expansion``, which atomically amends
    the sidecars, mutates the in-memory plan, and republishes the FULL
    manager-mode artifact set via the same transactional
    ``rebuild_and_publish`` path ``amend_plan`` uses) so the widened step
    becomes eligible for re-dispatch. Rejecting records the denial and
    changes nothing else.

    Only ``decision_type == "scope_expansion"`` is supported today — see
    ``ManagerDecisionResolveRequest``'s docstring for why other decision
    types are refused rather than silently no-op'd.

    Raises:
        HTTPException 404: Card, project, or decision not found.
        HTTPException 400: The decision is not a scope_expansion decision,
            or the engine could not apply the resolution (bad state).
        HTTPException 409: The decision was already resolved, or no
            execution state exists for this card yet.
    """
    _, _, context_root = _resolve_worker_context(card_id, scanner, store)
    paths = _manager_paths(context_root, card_id)

    decision = load_decision(paths, decision_id)
    if decision is None:
        raise HTTPException(
            status_code=404,
            detail=f"No decision '{decision_id}' found for card '{card_id}'.",
        )
    if decision.decision_type != "scope_expansion":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Decision '{decision_id}' has type "
                f"'{decision.decision_type}', not 'scope_expansion' — this "
                "endpoint only resolves scope-expansion decisions."
            ),
        )
    if decision.resolved_at:
        raise HTTPException(
            status_code=409,
            detail=f"Decision '{decision_id}' is already resolved (resolved_at={decision.resolved_at!r}).",
        )

    from agent_baton.core.engine.executor import ExecutionEngine
    from agent_baton.core.storage import get_project_storage

    try:
        storage = get_project_storage(context_root)
        engine = ExecutionEngine(
            team_context_root=context_root, bus=bus, task_id=card_id, storage=storage,
        )
        result = engine.resolve_scope_expansion(
            decision_id,
            body.resolution,
            additional_paths=body.additional_paths or None,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot resolve decision '{decision_id}': {exc}",
        ) from exc

    if not result.get("applied"):
        error = str(result.get("error", "resolution could not be applied"))
        status_code = 404 if "not found" in error else 400
        raise HTTPException(status_code=status_code, detail=error)

    return ManagerDecisionResolveResponse(**result)


# ---------------------------------------------------------------------------
# Version / validation
# ---------------------------------------------------------------------------


@router.get(
    "/pmo/manager/{card_id}/version",
    response_model=ManagerVersionResponse,
    tags=["pmo-manager"],
)
async def get_manager_version(
    card_id: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
) -> ManagerVersionResponse:
    """Return the published artifact-revision manifest, if any."""
    _, _, context_root = _resolve_worker_context(card_id, scanner, store)
    _require_manager_plan(context_root, card_id)
    paths = _manager_paths(context_root, card_id)

    manifest = load_revision_manifest(paths)
    if manifest is None:
        return ManagerVersionResponse(task_id=card_id, published=False)

    return ManagerVersionResponse(
        task_id=card_id,
        published=True,
        revision=int(manifest.get("revision", 0) or 0),
        prior_revision=int(manifest.get("prior_revision", 0) or 0),
        trigger=str(manifest.get("trigger", "")),
        created_at=str(manifest.get("created_at", "")),
        plan_fingerprint=str(manifest.get("plan_fingerprint", "")),
        phase_count=int(manifest.get("phase_count", 0) or 0),
        step_count=int(manifest.get("step_count", 0) or 0),
        published_paths=list(manifest.get("published_paths", [])),
    )


@router.get(
    "/pmo/manager/{card_id}/validation",
    response_model=ManagerValidationResponse,
    tags=["pmo-manager"],
)
async def get_manager_validation(
    card_id: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
) -> ManagerValidationResponse:
    """Check whether the published manager-mode artifacts are still
    version-consistent with the plan currently on disk.

    See ``ManagerValidationResponse``'s docstring for the exact contract.
    """
    _, _, context_root = _resolve_worker_context(card_id, scanner, store)
    plan = _require_manager_plan(context_root, card_id)
    paths = _manager_paths(context_root, card_id)

    current_fp = plan_fingerprint(plan)
    manifest = load_revision_manifest(paths)
    if manifest is None:
        return ManagerValidationResponse(
            task_id=card_id,
            published=False,
            valid=False,
            fingerprint_match=False,
            current_plan_fingerprint=current_fp,
            errors=["no manager-mode artifacts have been published for this task"],
        )

    published_fp = str(manifest.get("plan_fingerprint", ""))
    match = bool(published_fp) and published_fp == current_fp
    errors: list[str] = []
    if not match:
        errors.append(
            f"published revision {manifest.get('revision')} was built from a "
            "different plan shape than the plan currently on disk "
            "(plan_fingerprint mismatch) -- the manager view may be stale."
        )

    return ManagerValidationResponse(
        task_id=card_id,
        published=True,
        valid=match,
        fingerprint_match=match,
        revision=int(manifest.get("revision", 0) or 0),
        current_plan_fingerprint=current_fp,
        published_plan_fingerprint=published_fp,
        errors=errors,
    )
