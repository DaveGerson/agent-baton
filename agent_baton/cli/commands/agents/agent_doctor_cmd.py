"""``baton agents doctor`` -- validate generated agent definitions.

Catches broken or contract-violating agent files *before* Baton dispatches
them: malformed frontmatter, missing required fields, dangling
``knowledge_packs`` references, dangling local file references under
"Before Starting", and safety-relevant tool/permission combinations.

Scans the same three sources :class:`AgentRegistry` does (bundled,
global ``~/.claude/agents``, project ``.claude/agents``), reusing the
registry for discovery and precedence so this module never re-implements
that logic. Read-only: no file is ever mutated.

UX mirrors ``baton knowledge doctor`` (agent_baton/cli/commands/knowledge/
doctor_cmd.py): a flat issue list with ``severity``/``code``/``message``,
``--json``, and ``--strict``. Report shape mirrors ``baton doctor``
(agent_baton/cli/commands/diagnostics_cmd.py): a summary line + per-item
output.

Required-field severity is scoped by authorship. ``name``/``description``
are required for every agent -- error if missing, regardless of origin.
``model``/``permissionMode``/``tools`` are only mandated by the Phase 1
generated-agent contract that governs ``talent-builder`` output (frontmatter
``created_by: talent-builder``): missing there is an error
(``missing-required-field``). For hand-authored agents (no ``created_by``,
or any other value) missing those three is a warning
(``missing-recommended-field``) -- omitting them is a meaningful, valid
choice (e.g. no ``tools`` means "inherit all tools", which agents like
``orchestrator`` rely on to spawn subagents). Lint compliance must never
force a hand-authored agent to add a restrictive ``tools:`` list just to
silence this doctor.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_baton.cli.commands.agents import ensure_parent_parser, register_handler
from agent_baton.core.govern.validator import AgentValidator
from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry
from agent_baton.core.orchestration.registry import AgentRegistry
from agent_baton.models.agent import AgentDefinition
from agent_baton.models.enums import AgentCategory
from agent_baton.utils.frontmatter import parse_frontmatter

# ---------------------------------------------------------------------------
# Thresholds (documented here; adjust with evidence, not vibes)
# ---------------------------------------------------------------------------

# Below this, a description is unlikely to carry enough trigger signal for
# the router/orchestrator to pick the agent correctly (all 30 bundled agents
# are 200-710 chars; this floor only catches genuinely thin descriptions).
_DESCRIPTION_MIN_CHARS = 40
# Above this, a description has stopped being a trigger condition and started
# being documentation; it bloats every routing decision that considers it.
_DESCRIPTION_MAX_CHARS = 4000
# Baked-in knowledge (the markdown body) above this starts to dominate the
# agent's own dispatch cost. talent-builder (~18K chars) is the only bundled
# agent over this line today -- by design, per its own token-budget guidance.
_LARGE_BODY_CHAR_THRESHOLD = 15_000

# Tool combination broad enough to warrant a stated reason per
# references/agent-authoring.md's Tool Policy ("When broad tools are
# included, state the reason in the Principles or Before Starting section").
_BROAD_TOOL_MARKERS = {"Bash", "Write", "Edit"}
# A justification is "stated" if any of these tokens appears in the agent's
# body (outside frontmatter). This is a coarse, literal heuristic -- it will
# flag many pre-Phase-1-contract bundled agents that never had to justify
# broad tools. That's expected, not a bug: see references/agent-authoring.md.
_JUSTIFICATION_TOKENS = ("Bash", "Write", "Edit")

_LOCAL_PATH_ROOTS = (
    ".claude/", "references/", "knowledge/", "docs/", "templates/", "skills/",
)
_LOCAL_PATH_RE = re.compile(
    r"`?((?:%s)[\w./-]+\.[A-Za-z0-9]+)`?" % "|".join(re.escape(r) for r in _LOCAL_PATH_ROOTS)
)
_SECTION_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_OUTPUT_FORMAT_RE = re.compile(r"^##\s+Output Format\s*$", re.IGNORECASE | re.MULTILINE)

# name/description are required for every agent, hand-authored or generated:
# without them the router/orchestrator has nothing to match on.
_REQUIRED_FIELDS_ALWAYS = ("name", "description")
# model/permissionMode/tools are only mandated by the Phase 1 generated-agent
# contract (references/agent-authoring.md), which governs talent-builder
# output (frontmatter `created_by: talent-builder`). For hand-authored agents,
# omitting them is a meaningful choice -- e.g. no `tools` means "inherit all
# tools", which is the correct default for agents like `orchestrator` that
# must be able to spawn subagents. Flag the omission as a warning there
# instead of an error so lint compliance can't silently revoke capability
# (see the F2 finding this scoping was added to fix).
_REQUIRED_FIELDS_GENERATED_ONLY = ("model", "permissionMode", "tools")


@dataclass(frozen=True)
class DoctorIssue:
    """Actionable validation issue emitted by ``agents doctor``."""

    severity: str  # "error" | "warning"
    code: str
    message: str
    agent: str
    field: str = ""
    path: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "agent": self.agent,
            "field": self.field,
            "path": self.path,
        }


def register(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Hook ``doctor`` into the shared ``baton agents`` parser."""
    sub = ensure_parent_parser(subparsers)

    doctor_p = sub.add_parser(
        "doctor",
        help="Validate generated agent definitions before Baton dispatches them",
    )
    doctor_p.add_argument(
        "--json",
        action="store_true",
        help="Emit the doctor report as JSON",
    )
    doctor_p.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when any warning is found (in addition to errors)",
    )

    register_handler("doctor", _run_doctor)
    return subparsers.choices["agents"]


def handler(args: argparse.Namespace) -> None:
    """Auto-discovery entry point; delegate to the parent dispatcher."""
    dispatch = getattr(args, "_dispatch", None)
    if dispatch is None:
        raise SystemExit("baton agents: dispatcher missing")
    dispatch(args)


def _run_doctor(args: argparse.Namespace) -> None:
    payload = build_report()

    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2))
    else:
        print(_render_doctor(payload))

    summary = payload["summary"]
    strict = bool(getattr(args, "strict", False))
    if summary["errors"] > 0 or (strict and summary["warnings"] > 0):
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Report building
# ---------------------------------------------------------------------------

def build_report() -> dict[str, Any]:
    """Validate every agent :class:`AgentRegistry` would load, read-only.

    Discovery and override precedence (bundled < global < project) come
    entirely from :class:`AgentRegistry` -- this function never re-derives
    which agent "wins" a name collision, it only re-reads the winning
    file's raw content for checks an :class:`AgentDefinition` can't answer
    (was a field literally present in frontmatter? does a referenced
    knowledge pack exist? does a referenced local path exist?).
    """
    registry = AgentRegistry()
    registry.load_default_paths()

    knowledge_registry = KnowledgeRegistry()
    knowledge_registry.load_default_paths()

    bundled_dir = AgentRegistry.bundled_agents_dir()

    issues: list[DoctorIssue] = []
    agent_count = 0

    for name, agent_def in sorted(registry.agents.items()):
        agent_count += 1
        path = _resolve_source_path(agent_def, bundled_dir)
        if path is None:
            issues.append(DoctorIssue(
                severity="warning",
                code="unresolved-source-path",
                agent=name,
                message=(
                    f"Agent '{name}' has no resolvable file on disk; skipped "
                    "frontmatter and body checks (bundled resources may be "
                    "packaged without a real directory)."
                ),
            ))
            continue
        issues.extend(_check_agent_file(name, path, knowledge_registry))

    error_count = sum(1 for i in issues if i.severity == "error")
    warning_count = sum(1 for i in issues if i.severity == "warning")
    summary = {
        "agents": agent_count,
        "errors": error_count,
        "warnings": warning_count,
    }
    return {
        "ok": error_count == 0,
        "summary": summary,
        "issues": [issue.to_dict() for issue in issues],
    }


def _resolve_source_path(agent_def: AgentDefinition, bundled_dir: Path | None) -> Path | None:
    """Return a real, readable path for *agent_def*, or ``None``.

    Global/project agents already carry a real absolute ``source_path``
    (set by :meth:`AgentRegistry._parse_agent_file`). Bundled agents loaded
    via the ``importlib.resources`` traversal branch carry a synthetic,
    filename-only ``source_path`` that does not resolve on disk -- fall
    back to the real bundled directory for those.
    """
    source_path = agent_def.source_path
    if source_path is not None and source_path.is_file():
        return source_path
    if bundled_dir is not None:
        candidate = bundled_dir / f"{agent_def.name}.md"
        if candidate.is_file():
            return candidate
    return None


def _check_agent_file(
    name: str, path: Path, knowledge_registry: KnowledgeRegistry,
) -> list[DoctorIssue]:
    issues: list[DoctorIssue] = []

    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        issues.append(DoctorIssue(
            severity="error",
            code="unreadable-agent-file",
            agent=name,
            path=str(path),
            message=f"Agent '{name}' cannot be read: {exc}. Edit {path} permissions.",
        ))
        return issues

    # Reuse AgentValidator for structural + already-covered value-sanity
    # checks (frontmatter present/valid YAML, name/description required and
    # well-formed, model/permissionMode/tools values when present, unknown
    # tool names). This module adds only the checks AgentValidator
    # intentionally leaves out because it treats model/permissionMode/tools
    # as optional (correct for hand-authored agents; too loose for the
    # Phase 1 generated-agent contract this doctor enforces).
    result = AgentValidator().validate_file(path)
    for err in result.errors:
        issues.append(DoctorIssue(
            severity="error",
            code="contract-violation",
            agent=name,
            path=str(path),
            message=f"Agent '{name}': {err}. Edit {path}.",
        ))
    for warn in result.warnings:
        if "'model' field should be present" in warn:
            # Superseded below by `_check_required_fields`, which reports the
            # same root cause at the correct severity for this agent (error
            # for talent-builder-generated agents under the Phase 1 contract,
            # warning for hand-authored ones) -- avoid double-reporting it
            # here at a fixed severity.
            continue
        issues.append(DoctorIssue(
            severity="warning",
            code="validator-warning",
            agent=name,
            path=str(path),
            message=f"Agent '{name}': {warn}. Edit {path}.",
        ))

    metadata, body = parse_frontmatter(raw)
    if not isinstance(metadata, dict) or not metadata:
        # No usable frontmatter -- AgentValidator already reported this
        # above (missing/unclosed/invalid frontmatter); nothing further to
        # check against an empty metadata dict.
        return issues

    issues.extend(_check_required_fields(name, path, metadata))
    issues.extend(_check_value_sanity(name, path, metadata, body))
    issues.extend(_check_knowledge_packs(name, path, metadata, knowledge_registry))
    issues.extend(_check_before_starting_paths(name, path, body))
    issues.extend(_check_safety(name, path, metadata, body))
    return issues


# ---------------------------------------------------------------------------
# Check 1: frontmatter shape (Phase 1 generated-agent contract)
# ---------------------------------------------------------------------------

def _is_generated_agent(metadata: dict[str, Any]) -> bool:
    """Return True when frontmatter declares this a talent-builder output.

    Only generated agents (`created_by: talent-builder`) are bound by the
    Phase 1 contract's model/permissionMode/tools requirement -- see the
    module docstring and the comment above `_REQUIRED_FIELDS_GENERATED_ONLY`.
    """
    return str(metadata.get("created_by") or "").strip() == "talent-builder"


def _check_required_fields(
    name: str, path: Path, metadata: dict[str, Any],
) -> list[DoctorIssue]:
    issues: list[DoctorIssue] = []
    for required in _REQUIRED_FIELDS_ALWAYS:
        if not metadata.get(required):
            issues.append(DoctorIssue(
                severity="error",
                code="missing-required-field",
                agent=name,
                field=required,
                path=str(path),
                message=(
                    f"Agent '{name}' is missing required field '{required}' "
                    f"(see references/agent-authoring.md). Edit {path}."
                ),
            ))

    generated = _is_generated_agent(metadata)
    for required in _REQUIRED_FIELDS_GENERATED_ONLY:
        if metadata.get(required):
            continue
        if generated:
            issues.append(DoctorIssue(
                severity="error",
                code="missing-required-field",
                agent=name,
                field=required,
                path=str(path),
                message=(
                    f"Generated agent '{name}' (created_by: talent-builder) is "
                    f"missing required field '{required}' (see "
                    f"references/agent-authoring.md). Edit {path}."
                ),
            ))
        else:
            issues.append(DoctorIssue(
                severity="warning",
                code="missing-recommended-field",
                agent=name,
                field=required,
                path=str(path),
                message=(
                    f"Agent '{name}' omits recommended field '{required}'. "
                    "This is only an error for talent-builder-generated "
                    "agents; for hand-authored agents the omission can be a "
                    "deliberate choice (e.g. no 'tools' means inherit all "
                    f"tools). Confirm the omission is intentional. Edit {path}."
                ),
            ))
    return issues


# ---------------------------------------------------------------------------
# Check 2: value sanity
# ---------------------------------------------------------------------------

def _check_value_sanity(
    name: str, path: Path, metadata: dict[str, Any], body: str,
) -> list[DoctorIssue]:
    issues: list[DoctorIssue] = []

    tools_raw = metadata.get("tools")
    if tools_raw is not None and not isinstance(tools_raw, (str, list)):
        issues.append(DoctorIssue(
            severity="error",
            code="invalid-tools-type",
            agent=name,
            field="tools",
            path=str(path),
            message=(
                f"Agent '{name}' has a 'tools' field that is neither a "
                f"comma-separated string nor a list (got {type(tools_raw).__name__}). "
                f"Edit {path}."
            ),
        ))

    description = str(metadata.get("description") or "").strip()
    if description:
        if len(description) < _DESCRIPTION_MIN_CHARS:
            issues.append(DoctorIssue(
                severity="warning",
                code="description-too-short",
                agent=name,
                field="description",
                path=str(path),
                message=(
                    f"Agent '{name}' description is only {len(description)} chars "
                    f"(< {_DESCRIPTION_MIN_CHARS}); likely too thin for reliable "
                    f"trigger matching. Edit {path}."
                ),
            ))
        elif len(description) > _DESCRIPTION_MAX_CHARS:
            issues.append(DoctorIssue(
                severity="warning",
                code="description-too-long",
                agent=name,
                field="description",
                path=str(path),
                message=(
                    f"Agent '{name}' description is {len(description)} chars "
                    f"(> {_DESCRIPTION_MAX_CHARS}); consider trimming to the "
                    f"essential trigger conditions. Edit {path}."
                ),
            ))

    if body and not _OUTPUT_FORMAT_RE.search(body):
        issues.append(DoctorIssue(
            severity="warning",
            code="missing-output-format-section",
            agent=name,
            path=str(path),
            message=(
                f"Agent '{name}' has no '## Output Format' section; callers "
                f"can't rely on a stable response shape. Edit {path}."
            ),
        ))

    if len(body) > _LARGE_BODY_CHAR_THRESHOLD:
        issues.append(DoctorIssue(
            severity="warning",
            code="large-baked-in-knowledge",
            agent=name,
            path=str(path),
            message=(
                f"Agent '{name}' body is {len(body)} chars "
                f"(> {_LARGE_BODY_CHAR_THRESHOLD}); consider moving detail "
                f"into a knowledge pack (reference delivery) instead of "
                f"baking it into every dispatch. Edit {path}."
            ),
        ))

    return issues


# ---------------------------------------------------------------------------
# Check 3: knowledge_packs references
# ---------------------------------------------------------------------------

def _normalise_knowledge_packs(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [p.strip() for p in raw.split(",") if p.strip()]
    if isinstance(raw, list):
        return [str(p).strip() for p in raw if str(p).strip()]
    return []


def _check_knowledge_packs(
    name: str, path: Path, metadata: dict[str, Any], knowledge_registry: KnowledgeRegistry,
) -> list[DoctorIssue]:
    issues: list[DoctorIssue] = []
    pack_names = _normalise_knowledge_packs(metadata.get("knowledge_packs"))
    for pack_name in pack_names:
        if knowledge_registry.get_pack(pack_name) is None:
            issues.append(DoctorIssue(
                severity="error",
                code="missing-knowledge-pack",
                agent=name,
                field="knowledge_packs",
                path=str(path),
                message=(
                    f"Agent '{name}' declares knowledge_packs entry "
                    f"'{pack_name}', but no pack with that name is loaded "
                    "in the knowledge registry. Edit the frontmatter or "
                    "create the pack under .claude/knowledge/."
                ),
            ))
    return issues


# ---------------------------------------------------------------------------
# Check 4: "Before Starting" local file references
# ---------------------------------------------------------------------------

def _extract_section(body: str, heading: str) -> str | None:
    matches = list(_SECTION_HEADING_RE.finditer(body))
    for i, m in enumerate(matches):
        if m.group(1).strip().lower() == heading.lower():
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
            return body[start:end]
    return None


def _check_before_starting_paths(name: str, path: Path, body: str) -> list[DoctorIssue]:
    issues: list[DoctorIssue] = []
    section = _extract_section(body, "Before Starting")
    if not section:
        return issues

    seen: set[str] = set()
    for match in _LOCAL_PATH_RE.finditer(section):
        candidate = match.group(1)
        if candidate in seen:
            continue
        seen.add(candidate)
        if not (Path.cwd() / candidate).is_file():
            issues.append(DoctorIssue(
                severity="warning",
                code="missing-before-starting-reference",
                agent=name,
                field="Before Starting",
                path=str(path),
                message=(
                    f"Agent '{name}' Before Starting section references "
                    f"'{candidate}', which does not exist relative to the "
                    "current project. Edit the reference or create the file."
                ),
            ))
    return issues


# ---------------------------------------------------------------------------
# Check 5: safety warnings (non-blocking unless --strict)
# ---------------------------------------------------------------------------

def _normalise_tools(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [t.strip() for t in raw.split(",") if t.strip()]
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    return []


def _is_reviewer_or_auditor(name: str, category: AgentCategory) -> bool:
    return category == AgentCategory.REVIEW or "reviewer" in name or "auditor" in name


def _check_safety(
    name: str, path: Path, metadata: dict[str, Any], body: str,
) -> list[DoctorIssue]:
    issues: list[DoctorIssue] = []
    tools = set(_normalise_tools(metadata.get("tools")))
    base_name = name.split("--")[0] if "--" in name else name
    # Recompute category the same way AgentDefinition.category does, without
    # needing a full AgentDefinition instance built from raw frontmatter.
    category = _category_for(base_name)
    is_reviewer = _is_reviewer_or_auditor(name, category)

    is_broad = "*" in tools or _BROAD_TOOL_MARKERS <= tools
    if is_broad and not is_reviewer:
        justified = any(token in body for token in _JUSTIFICATION_TOKENS)
        if not justified:
            issues.append(DoctorIssue(
                severity="warning",
                code="broad-tools-no-justification",
                agent=name,
                field="tools",
                path=str(path),
                message=(
                    f"Agent '{name}' has broad tool access ({sorted(tools) or ['*']}) "
                    "with no stated justification in the body. Per "
                    "references/agent-authoring.md's Tool Policy, state the "
                    f"reason in Principles or Before Starting. Edit {path}."
                ),
            ))

    if is_reviewer and tools & {"Write", "Edit"}:
        issues.append(DoctorIssue(
            severity="warning",
            code="reviewer-with-mutating-tools",
            agent=name,
            field="tools",
            path=str(path),
            message=(
                f"Agent '{name}' looks like a reviewer/auditor but has "
                f"mutating tools ({sorted(tools & {'Write', 'Edit'})}); "
                f"reviewers should stay read-only unless explicitly "
                f"promoted to an implementer. Edit {path}."
            ),
        ))

    return issues


def _category_for(base_name: str) -> AgentCategory:
    engineering = {
        "architect", "backend-engineer", "frontend-engineer",
        "devops-engineer", "test-engineer", "data-engineer",
    }
    data = {"data-scientist", "data-analyst", "visualization-expert"}
    domain = {"subject-matter-expert"}
    review = {"security-reviewer", "code-reviewer", "auditor"}
    meta = {"talent-builder", "orchestrator"}

    if base_name in engineering:
        return AgentCategory.ENGINEERING
    if base_name in data:
        return AgentCategory.DATA
    if base_name in domain:
        return AgentCategory.DOMAIN
    if base_name in review:
        return AgentCategory.REVIEW
    if base_name in meta:
        return AgentCategory.META
    return AgentCategory.ENGINEERING


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_doctor(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "Agent doctor",
        (
            f"agents={summary['agents']} errors={summary['errors']} "
            f"warnings={summary['warnings']}"
        ),
    ]
    issues = payload["issues"]
    if not issues:
        lines.append("OK: no agent issues found.")
        return "\n".join(lines)
    for issue in issues:
        lines.append(f"{issue['severity'].upper()} [{issue['code']}] {issue['message']}")
    return "\n".join(lines)
