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


def render_all(
    paths: ManagerArtifactPaths, artifacts: ManagerArtifacts
) -> list[tuple[Path, str]]:
    """Render every non-None/non-empty artifact to ``(path, text)`` pairs,
    in composition order, without touching the filesystem.

    This is the pure-rendering half of :func:`write_all` -- extracted so a
    caller (e.g. ``agent_baton.core.manager.rebuild``) can stage every
    sidecar's final byte content in memory, validate it, and only then
    decide whether to write anything. ``write_all`` itself is unchanged in
    behavior; it now delegates here for content and does the writing.

    ``charter`` is rendered to Markdown via
    ``agent_baton.core.manager.charter.charter_to_markdown`` (imported
    lazily so this module has no hard dependency on the Wave 1 charter
    builder); calling this with a populated ``charter`` before that module
    exists raises ``ImportError`` rather than silently degrading the
    output.
    """
    rendered: list[tuple[Path, str]] = []

    if artifacts.charter is not None:
        from agent_baton.core.manager.charter import charter_to_markdown

        rendered.append((paths.charter, charter_to_markdown(artifacts.charter)))

    if artifacts.scope_map is not None:
        rendered.append((paths.scope_map, _json_text(artifacts.scope_map)))

    if artifacts.blueprint is not None:
        rendered.append((paths.team_blueprint, _json_text(artifacts.blueprint)))

    for role, md in artifacts.role_cards_md.items():
        rendered.append((paths.role_card(role), md))

    if artifacts.knowledge_plan is not None:
        rendered.append((paths.knowledge_plan, _json_text(artifacts.knowledge_plan)))

    for step_id, contract in artifacts.scope_contracts.items():
        rendered.append((paths.scope_contract(step_id, ext="json"), _json_text(contract)))

    for step_id, md in artifacts.scope_contracts_md.items():
        rendered.append((paths.scope_contract(step_id, ext="md"), md))

    for step_id, bundle in artifacts.context_bundles.items():
        rendered.append((paths.context_bundle(step_id), _json_text(bundle)))

    if artifacts.brief_md:
        rendered.append((paths.manager_brief, artifacts.brief_md))

    return rendered


def _json_text(model: ManagerModel) -> str:
    return json.dumps(model.to_dict(), indent=2, ensure_ascii=False) + "\n"


def write_all(paths: ManagerArtifactPaths, artifacts: ManagerArtifacts) -> list[Path]:
    """Write every non-None/non-empty artifact to its conventional location.

    Returns the list of paths written, in composition order (see
    :func:`render_all`, which this delegates to for content).
    """
    written: list[Path] = []
    for path, text in render_all(paths, artifacts):
        write_text(path, text)
        written.append(path)
    return written


def preview_paths(
    paths: ManagerArtifactPaths, artifacts: ManagerArtifacts
) -> list[tuple[Path, str]]:
    """Non-mutating preview of what ``write_all`` would write.

    Mirrors ``write_all``'s traversal order exactly, pairing each path
    with a short human-readable description, so ``baton plan
    --manager-mode --dry-run`` can print an accurate artifact list without
    touching the filesystem (see ``agent_baton.core.manager.planner``).
    """
    items: list[tuple[Path, str]] = []

    if artifacts.charter is not None:
        items.append((paths.charter, "Project charter"))

    if artifacts.scope_map is not None:
        n = len(artifacts.scope_map.workstreams)
        items.append((paths.scope_map, f"Scope map ({n} workstream(s))"))

    if artifacts.blueprint is not None:
        n = len(artifacts.blueprint.roles)
        items.append((paths.team_blueprint, f"Team blueprint ({n} role(s))"))

    for role in artifacts.role_cards_md:
        items.append((paths.role_card(role), f"Role card: {role}"))

    if artifacts.knowledge_plan is not None:
        n = len(artifacts.knowledge_plan.selected_packs)
        items.append((paths.knowledge_plan, f"Knowledge plan ({n} pack(s) selected)"))

    for step_id in artifacts.scope_contracts:
        items.append((paths.scope_contract(step_id, ext="json"), f"Scope contract (JSON): step {step_id}"))

    for step_id in artifacts.scope_contracts_md:
        items.append((paths.scope_contract(step_id, ext="md"), f"Scope contract: step {step_id}"))

    for step_id in artifacts.context_bundles:
        items.append((paths.context_bundle(step_id), f"Context bundle: step {step_id}"))

    if artifacts.brief_md:
        items.append((paths.manager_brief, "Manager brief"))

    return items
