"""Manager-mode sidecar artifact container and writer.

``ManagerArtifacts`` is the in-memory container builders populate and pass
between each other during ``ManagerModePlanner.build()`` composition (see
``agent_baton.core.manager.planner``). ``write_all`` persists a populated
container to disk via ``ManagerArtifactPaths``.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agent_baton.core.manager.paths import ManagerArtifactPaths
from agent_baton.models.manager import (
    ContextBundle,
    KnowledgePlan,
    ManagerDecision,
    ManagerModel,
    ProjectCharter,
    ScopeContract,
    ScopeMap,
    TeamBlueprint,
)


class ManagerArtifacts(BaseModel):
    """Container passed between manager-mode builders.

    Populated incrementally in composition order (charter -> scope map ->
    blueprint+role cards -> knowledge plan -> scope contracts + context
    bundles -> manager brief) and persisted at the end via ``write_all``.
    """

    model_config = ConfigDict(extra="ignore")

    charter: ProjectCharter | None = None
    scope_map: ScopeMap | None = None
    blueprint: TeamBlueprint | None = None
    role_cards_md: dict[str, str] = Field(default_factory=dict)  # role -> rendered markdown
    knowledge_plan: KnowledgePlan | None = None
    scope_contracts: dict[str, ScopeContract] = Field(default_factory=dict)  # step_id -> contract
    scope_contracts_md: dict[str, str] = Field(default_factory=dict)
    context_bundles: dict[str, ContextBundle] = Field(default_factory=dict)
    brief_md: str = ""
    warnings: list[str] = Field(default_factory=list)


def write_json(path: Path, model: ManagerModel) -> None:
    """Serialize *model* via ``to_dict()`` and write as indented JSON.

    Creates parent directories as needed.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(model.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_text(path: Path, text: str) -> None:
    """Write *text* to *path*, creating parent directories as needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def append_decision_log(paths: ManagerArtifactPaths, decision: ManagerDecision) -> None:
    """Append *decision* as one JSON line to ``decision-log.jsonl``.

    Creates parent directories as needed. Never rewrites or truncates
    existing entries — this is an append-only audit log.
    """
    path = paths.decision_log
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(decision.to_dict(), ensure_ascii=False))
        fh.write("\n")


def write_all(paths: ManagerArtifactPaths, artifacts: ManagerArtifacts) -> list[Path]:
    """Write every non-None/non-empty artifact to its conventional location.

    Returns the list of paths written, in composition order. ``charter``
    is rendered to Markdown via
    ``agent_baton.core.manager.charter.charter_to_markdown`` (imported
    lazily so this module has no hard dependency on the Wave 1 charter
    builder); calling ``write_all`` with a populated ``charter`` before
    that module exists raises ``ImportError`` rather than silently
    degrading the output.
    """
    written: list[Path] = []

    if artifacts.charter is not None:
        from agent_baton.core.manager.charter import charter_to_markdown

        write_text(paths.charter, charter_to_markdown(artifacts.charter))
        written.append(paths.charter)

    if artifacts.scope_map is not None:
        write_json(paths.scope_map, artifacts.scope_map)
        written.append(paths.scope_map)

    if artifacts.blueprint is not None:
        write_json(paths.team_blueprint, artifacts.blueprint)
        written.append(paths.team_blueprint)

    for role, md in artifacts.role_cards_md.items():
        path = paths.role_card(role)
        write_text(path, md)
        written.append(path)

    if artifacts.knowledge_plan is not None:
        write_json(paths.knowledge_plan, artifacts.knowledge_plan)
        written.append(paths.knowledge_plan)

    for step_id, contract in artifacts.scope_contracts.items():
        path = paths.scope_contract(step_id, ext="json")
        write_json(path, contract)
        written.append(path)

    for step_id, md in artifacts.scope_contracts_md.items():
        path = paths.scope_contract(step_id, ext="md")
        write_text(path, md)
        written.append(path)

    for step_id, bundle in artifacts.context_bundles.items():
        path = paths.context_bundle(step_id)
        write_json(path, bundle)
        written.append(path)

    if artifacts.brief_md:
        write_text(paths.manager_brief, artifacts.brief_md)
        written.append(paths.manager_brief)

    return written
