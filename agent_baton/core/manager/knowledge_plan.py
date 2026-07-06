"""Knowledge-pack lifecycle for manager-mode (M5).

Wraps the existing :class:`~agent_baton.core.orchestration.knowledge_registry.KnowledgeRegistry`
(and the attachments a :class:`~agent_baton.core.engine.knowledge_resolver.KnowledgeResolver`
already resolved onto each :class:`~agent_baton.models.execution.PlanStep` during
planning) into a plan-wide :class:`~agent_baton.models.manager.KnowledgePlan`,
plus the scan/audit/propose primitives behind ``baton knowledge
list|show|scan|audit|propose`` (``agent_baton.cli.commands.knowledge.pack_cmds``).

Per locked decision 2 (docs/internal/manager-mode-pmo-design.md): this
extends the existing ``knowledge.yaml`` manifest — there is no separate
``pack.yaml`` file or schema.

Staleness comparisons use the injectable :func:`_today` hook (rather than a
direct ``datetime.date.today()`` call inline) so tests can freeze "now" via
``monkeypatch.setattr(knowledge_plan_module, "_today", lambda: date(...))``
without any real clock read leaking into an assertion.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_baton.models.knowledge import KnowledgeGapRecord
from agent_baton.models.manager import (
    KnowledgePackReference,
    KnowledgePlan,
    MissingKnowledgePack,
)

if TYPE_CHECKING:
    from agent_baton.core.config.manager import ManagerConfig
    from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry
    from agent_baton.models.execution import MachinePlan

logger = logging.getLogger(__name__)

# step_type values (agent_baton/core/engine/planning/rules/step_types.py)
# treated as "implementation" steps for the purpose of forcibly attaching
# `knowledge_packs.required_for_code_steps` packs.
_IMPLEMENTATION_STEP_TYPES = frozenset({"developing", "testing"})

# Candidate filename globs / paths `scan_project` looks for at the project
# root, in addition to any `.claude/knowledge/` packs the registry finds
# (spec §12.4 `baton knowledge scan`).
_SCAN_DOC_GLOBS = ("README*", "CONTRIBUTING*", "ARCHITECTURE*")
_SCAN_CONFIG_FILES = ("pyproject.toml", "package.json")

_SCAN_FILENAME = "knowledge-scan.json"
_PROPOSALS_DIRNAME = "knowledge-proposals"
_DEFAULT_MIN_PROPOSAL_OCCURRENCES = 2


def _today() -> _dt.date:
    """Injectable "now" for staleness comparisons — monkeypatch in tests."""
    return _dt.date.today()


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_date(value: Any) -> _dt.date | None:
    """Parse an ISO ``YYYY-MM-DD`` string (or date/datetime) to a ``date``."""
    if value is None or value == "":
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    try:
        return _dt.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# KnowledgePlanBuilder
# ---------------------------------------------------------------------------


class KnowledgePlanBuilder:
    """Build a plan-wide :class:`KnowledgePlan` for a manager-mode plan.

    Combines three sources of knowledge-pack attachment, each contributing
    to :attr:`KnowledgePlan.selected_packs` with an explanatory ``reason``
    (spec §12.5 "each attachment should include ... reason attached"):

    1. **Step attachments** — packs the :class:`KnowledgeResolver` already
       resolved onto ``step.knowledge`` during planning (4-layer pipeline:
       explicit / agent-declared / tag / relevance).
    2. **Required-for-code-steps** — ``knowledge_packs.required_for_code_steps``
       config packs are force-attached to every step whose ``step_type`` is
       an implementation type (``developing``/``testing``), regardless of
       whether the resolver's keyword matching happened to find them —
       this is what makes required packs *guaranteed*, not probabilistic.
    3. **Default packs** — ``knowledge_packs.default_packs`` config packs
       present in the registry, attached plan-wide.
    4. **Role packs** — packs whose manifest ``target_agents`` names a
       role in *blueprint_roles* (``registry.packs_for_agent``).

    ``missing_packs`` lists every ``default_packs``/``required_for_code_steps``
    name absent from the registry (reason ``"config: default_packs"`` or
    ``"config: required_for_code_steps"``). ``stale_packs`` lists every
    *registered* pack (not just selected ones — staleness is a manager-wide
    signal, spec §4.4 "track source/confidence/staleness") whose
    ``last_reviewed`` + effective ``stale_after_days`` (pack-level value,
    falling back to ``knowledge_packs.stale_after_days``) has elapsed as of
    :func:`_today`.
    """

    def __init__(self, config: "ManagerConfig", registry: "KnowledgeRegistry") -> None:
        self._config = config
        self._registry = registry

    def build(self, plan: "MachinePlan", blueprint_roles: list[str]) -> KnowledgePlan:
        kp_config = self._config.knowledge_packs

        selected: dict[str, KnowledgePackReference] = {}
        per_step_packs: dict[str, list[str]] = {}

        for phase in plan.phases:
            for step in phase.steps:
                step_pack_names: list[str] = []

                for attachment in step.knowledge:
                    pack_name = getattr(attachment, "pack_name", None)
                    if not pack_name:
                        continue
                    if pack_name not in step_pack_names:
                        step_pack_names.append(pack_name)
                    if pack_name not in selected:
                        source = getattr(attachment, "source", "") or "resolver"
                        selected[pack_name] = self._make_reference(
                            pack_name, reason=f"step attachment: {source}"
                        )

                if step.step_type in _IMPLEMENTATION_STEP_TYPES:
                    for name in kp_config.required_for_code_steps:
                        if self._registry.get_pack(name) is None:
                            continue
                        if name not in step_pack_names:
                            step_pack_names.append(name)
                        if name not in selected:
                            selected[name] = self._make_reference(
                                name, reason="config: required_for_code_steps"
                            )

                if step_pack_names:
                    per_step_packs[step.step_id] = step_pack_names

        for name in kp_config.default_packs:
            if name in selected:
                continue
            if self._registry.get_pack(name) is None:
                continue
            selected[name] = self._make_reference(name, reason="config: default_packs")

        missing: list[MissingKnowledgePack] = []
        seen_missing: set[str] = set()
        for name, reason in (
            *((n, "config: default_packs") for n in kp_config.default_packs),
            *((n, "config: required_for_code_steps") for n in kp_config.required_for_code_steps),
        ):
            if name in seen_missing:
                continue
            if self._registry.get_pack(name) is not None:
                continue
            seen_missing.add(name)
            missing.append(MissingKnowledgePack(name=name, reason=reason))

        stale = self._stale_pack_names(fallback_days=kp_config.stale_after_days)

        per_role_packs: dict[str, list[str]] = {}
        for role in blueprint_roles:
            packs = self._registry.packs_for_agent(role)
            if not packs:
                continue
            per_role_packs[role] = [p.name for p in packs]
            for pack in packs:
                if pack.name not in selected:
                    selected[pack.name] = self._make_reference(
                        pack.name, reason=f"role: {role}"
                    )

        return KnowledgePlan(
            task_id=plan.task_id,
            selected_packs=list(selected.values()),
            missing_packs=missing,
            stale_packs=stale,
            per_role_packs=per_role_packs,
            per_step_packs=per_step_packs,
        )

    def _stale_pack_names(self, *, fallback_days: int | None) -> list[str]:
        today = _today()
        stale: list[str] = []
        for pack in self._registry.all_packs.values():
            last_reviewed = _parse_date(getattr(pack, "last_reviewed", None))
            if last_reviewed is None:
                continue
            stale_after = getattr(pack, "stale_after_days", None)
            if stale_after is None:
                stale_after = fallback_days
            if stale_after is None:
                continue
            if (today - last_reviewed).days > stale_after:
                stale.append(pack.name)
        return stale

    def _make_reference(self, name: str, *, reason: str) -> KnowledgePackReference:
        pack = self._registry.get_pack(name)
        if pack is None:
            return KnowledgePackReference(name=name, reason=reason)
        source_path = getattr(pack, "source_path", None)
        return KnowledgePackReference(
            name=pack.name,
            path=Path(source_path).as_posix() if source_path else "",
            reason=reason,
            confidence=getattr(pack, "confidence", "medium") or "medium",
            status=getattr(pack, "status", "active") or "active",
            token_estimate=sum(d.token_estimate for d in pack.documents),
            documents=[d.name for d in pack.documents],
        )


# ---------------------------------------------------------------------------
# Audit (`baton knowledge audit`) — spec §12.4
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KnowledgeAuditIssue:
    """A single finding from :func:`audit_packs`."""

    pack_name: str
    kind: str  # "invalid_status" | "stale" | "missing_source_file" | "missing_metadata"
    message: str


def audit_packs(
    registry: "KnowledgeRegistry",
    config: "ManagerConfig",
    *,
    root: Path | None = None,
) -> list[KnowledgeAuditIssue]:
    """Audit every pack in *registry* for lifecycle/metadata problems.

    Checks (spec §12.4 ``baton knowledge audit``):

    - **invalid_status** — manifest ``status`` outside
      :data:`~agent_baton.core.orchestration.knowledge_registry.PACK_STATUSES`.
      The registry itself never rejects an invalid status (graceful
      degradation) — this is where it becomes an actionable finding.
    - **stale** — ``last_reviewed`` + effective ``stale_after_days``
      (pack-level, falling back to ``config.knowledge_packs.stale_after_days``)
      has elapsed as of :func:`_today`.
    - **missing_source_file** — a ``source_files`` entry that does not
      exist relative to *root* (default: ``Path.cwd()``).
    - **missing_metadata** — no ``confidence`` or no ``source_files``
      recorded at all (nothing to check freshness/trust against).

    Conflicting-guidance and unused-pack checks (also listed in spec
    §12.3) are out of scope for M5 — they need cross-pack NLP comparison
    and usage telemetry respectively, neither of which this milestone's
    required test cases exercise.
    """
    from agent_baton.core.orchestration.knowledge_registry import PACK_STATUSES

    project_root = (root or Path.cwd()).resolve()
    today = _today()
    fallback_days = config.knowledge_packs.stale_after_days

    issues: list[KnowledgeAuditIssue] = []
    for pack in sorted(registry.all_packs.values(), key=lambda p: p.name):
        status = getattr(pack, "status", "active")
        if status not in PACK_STATUSES:
            issues.append(
                KnowledgeAuditIssue(
                    pack.name,
                    "invalid_status",
                    f"Pack {pack.name!r} has invalid status {status!r}; "
                    f"valid values: {sorted(PACK_STATUSES)}",
                )
            )

        last_reviewed = _parse_date(getattr(pack, "last_reviewed", None))
        stale_after = getattr(pack, "stale_after_days", None)
        if stale_after is None:
            stale_after = fallback_days
        if last_reviewed is not None and stale_after is not None:
            age_days = (today - last_reviewed).days
            if age_days > stale_after:
                issues.append(
                    KnowledgeAuditIssue(
                        pack.name,
                        "stale",
                        f"Pack {pack.name!r} is stale (last reviewed "
                        f"{last_reviewed.isoformat()}, {age_days}d ago, "
                        f"{stale_after}-day threshold exceeded)",
                    )
                )

        source_files = getattr(pack, "source_files", []) or []
        for source_file in source_files:
            candidate = project_root / source_file
            if not candidate.exists():
                issues.append(
                    KnowledgeAuditIssue(
                        pack.name,
                        "missing_source_file",
                        f"Pack {pack.name!r} references missing source file: "
                        f"{source_file}",
                    )
                )

        confidence = getattr(pack, "confidence", "") or ""
        if not source_files:
            issues.append(
                KnowledgeAuditIssue(
                    pack.name,
                    "missing_metadata",
                    f"Pack {pack.name!r} is missing source metadata "
                    f"(confidence={confidence or '(none)'}, "
                    f"source_files={len(source_files)})",
                )
            )

    return issues


# ---------------------------------------------------------------------------
# Scan (`baton knowledge scan`) — spec §12.4
# ---------------------------------------------------------------------------


def scan_project(root: Path, registry: "KnowledgeRegistry") -> dict[str, Any]:
    """Discover knowledge packs + candidate documentation under *root*.

    Returns the JSON-serializable payload written by :func:`write_scan_report`:

    - ``packs`` — every pack currently loaded in *registry* (name, path,
      status, document count, whether it loaded in degraded mode).
    - ``discovered_files`` — repo-relative paths of README/CONTRIBUTING/
      ARCHITECTURE docs, every ``docs/**/*.md`` file, and
      ``pyproject.toml``/``package.json`` if present (spec §12.4
      "Discovers: existing packs, README/CONTRIBUTING/ARCHITECTURE docs,
      package/test config files, ...").

    Does not write to disk — see :func:`write_scan_report`.
    """
    root = Path(root).resolve()

    packs = [
        {
            "name": pack.name,
            "path": Path(pack.source_path).as_posix() if pack.source_path else "",
            "status": getattr(pack, "status", "active"),
            "document_count": len(pack.documents),
            "degraded": pack.name in registry.degraded_pack_names,
        }
        for pack in sorted(registry.all_packs.values(), key=lambda p: p.name)
    ]

    discovered_files: list[str] = []
    seen_files: set[str] = set()

    def _add(path: Path) -> None:
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            return
        if rel not in seen_files:
            seen_files.add(rel)
            discovered_files.append(rel)

    for pattern in _SCAN_DOC_GLOBS:
        for path in sorted(root.glob(pattern)):
            if path.is_file():
                _add(path)

    docs_dir = root / "docs"
    if docs_dir.is_dir():
        for path in sorted(docs_dir.rglob("*.md")):
            _add(path)

    for name in _SCAN_CONFIG_FILES:
        candidate = root / name
        if candidate.is_file():
            _add(candidate)

    return {
        "scanned_at": _now_iso(),
        "root": root.as_posix(),
        "packs": packs,
        "discovered_files": discovered_files,
    }


def write_scan_report(root: Path, registry: "KnowledgeRegistry") -> Path:
    """Run :func:`scan_project` and write the result to
    ``<root>/.claude/team-context/knowledge-scan.json``.

    Note the destination is the team-context *root*, not
    ``executions/<task_id>/`` — the scan is project-wide, not tied to a
    single manager-mode execution (spec §12.4).
    """
    root = Path(root).resolve()
    payload = scan_project(root, registry)
    out_path = root / ".claude" / "team-context" / _SCAN_FILENAME
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return out_path


# ---------------------------------------------------------------------------
# Propose (`baton knowledge propose`) — spec §12.4
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KnowledgePackProposal:
    """A draft knowledge-pack proposal derived from repeated gap signals."""

    slug: str
    description: str
    occurrences: int
    agents: tuple[str, ...]
    markdown: str


def _normalize_gap_description(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug or "gap")[:60]


def load_gap_records(team_context_root: Path) -> list[KnowledgeGapRecord]:
    """Read every :class:`KnowledgeGapRecord` from retrospective sidecars.

    Scans ``<team_context_root>/retrospectives/*.json`` for a
    ``knowledge_gaps`` list (the shape
    :meth:`agent_baton.models.retrospective.Retrospective.to_dict` writes),
    mirroring :meth:`agent_baton.core.learn.pattern_learner.PatternLearner.knowledge_gaps_for`'s
    file-reading technique but without its per-agent filter — proposals
    need cross-agent aggregation (the same gap reported by two different
    agents is exactly the "repeated gap" signal spec §12.4 asks for).
    """
    retros_dir = Path(team_context_root) / "retrospectives"
    if not retros_dir.is_dir():
        return []

    records: list[KnowledgeGapRecord] = []
    for path in sorted(retros_dir.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        raw_gaps = raw.get("knowledge_gaps", [])
        if not isinstance(raw_gaps, list):
            continue
        for entry in raw_gaps:
            if not isinstance(entry, dict):
                continue
            try:
                records.append(KnowledgeGapRecord.from_dict(entry))
            except (KeyError, TypeError, ValueError):
                continue
    return records


def propose_from_gap_records(
    team_context_root: Path,
    *,
    min_occurrences: int = _DEFAULT_MIN_PROPOSAL_OCCURRENCES,
) -> list[KnowledgePackProposal]:
    """Group gap records by normalized description; propose a draft pack
    for every description seen at least *min_occurrences* times.

    Grouping is cross-agent (see :func:`load_gap_records`) and
    case/whitespace-insensitive. Results are sorted by descending
    occurrence count, then description, for determinism.
    """
    records = load_gap_records(team_context_root)

    grouped: dict[str, list[KnowledgeGapRecord]] = {}
    for record in records:
        key = _normalize_gap_description(record.description)
        if not key:
            continue
        grouped.setdefault(key, []).append(record)

    proposals: list[KnowledgePackProposal] = []
    for group in grouped.values():
        if len(group) < min_occurrences:
            continue
        sample = group[0]
        agents = tuple(sorted({r.agent_name for r in group if r.agent_name}))
        proposals.append(
            KnowledgePackProposal(
                slug=_slugify(sample.description),
                description=sample.description,
                occurrences=len(group),
                agents=agents,
                markdown=_render_proposal_markdown(sample.description, group, agents),
            )
        )

    proposals.sort(key=lambda p: (-p.occurrences, p.description))
    return proposals


def _render_proposal_markdown(
    description: str,
    group: list[KnowledgeGapRecord],
    agents: tuple[str, ...],
) -> str:
    task_summaries = sorted({r.task_summary for r in group if r.task_summary})
    lines = [
        f"# Proposed Knowledge Pack: {description}",
        "",
        f"**Occurrences:** {len(group)}",
        f"**Reported by:** {', '.join(agents) if agents else '(unknown)'}",
        "",
        "## Gap description",
        "",
        description,
        "",
    ]
    if task_summaries:
        lines.append("## Seen during")
        lines.append("")
        for summary in task_summaries:
            lines.append(f"- {summary}")
        lines.append("")
    lines.extend([
        "## Suggested next step",
        "",
        "Review the repeated gap above and create a knowledge pack "
        "(`.claude/knowledge/<pack-name>/knowledge.yaml` + supporting "
        "documents) that resolves it, or update an existing pack's "
        "documents so the gap does not recur.",
        "",
    ])
    return "\n".join(lines)


def write_proposals(
    team_context_root: Path, proposals: list[KnowledgePackProposal]
) -> list[Path]:
    """Write every proposal to ``<team_context_root>/knowledge-proposals/<slug>.md``.

    Returns the list of paths written, in the same order as *proposals*.
    """
    out_dir = Path(team_context_root) / _PROPOSALS_DIRNAME
    written: list[Path] = []
    if not proposals:
        return written
    out_dir.mkdir(parents=True, exist_ok=True)
    for proposal in proposals:
        path = out_dir / f"{proposal.slug}.md"
        path.write_text(proposal.markdown, encoding="utf-8")
        written.append(path)
    return written
