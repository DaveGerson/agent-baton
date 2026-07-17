"""Validation for talent-builder-generated artifacts.

Implements the Generated-Agent Contract validation described in
docs/internal/talent-factory-contract.md §5 and agents/talent-builder.md
("Generated-Agent Contract"). This module only reads files and reports
findings -- it never writes to disk, never mutates a registry, and never
decides what happens on failure (rollback vs. quarantine is
``talent_factory.py``'s job, driven by ``TalentFactoryConfig.on_validation_failure``).

Three checks are worth calling out because they map directly to specific
contract clauses:

* **Provenance** (``created_by``/``status``/``version``) -- §9 of the
  contract; lets tooling distinguish generated-and-unreviewed capability
  from a promoted, trusted roster agent.
* **Recursion guard** (base name not in ``NON_GENERABLE_CAPABILITIES``) --
  a defense-in-depth re-check of the same rule
  ``capability_gap.decide_talent_lifecycle`` already enforces upstream;
  an artifact that somehow named itself ``talent-builder`` must never pass
  validation even if the upstream guard were ever bypassed.
* **Prompt-safety scan** -- §7 of the contract ("untrusted instructions").
  This is a coarse, best-effort regex scan for text shaped like an
  injected directive ("ignore previous instructions", "grant this agent
  the tool", ...). It cannot prove absence of injection -- it exists to
  catch the obvious case and fail closed on it, not to be a complete
  defense.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from agent_baton.core.engine.planning.capability_gap import NON_GENERABLE_CAPABILITIES
from agent_baton.utils.frontmatter import parse_frontmatter

__all__ = [
    "ValidationResult",
    "validate_generated_agent",
    "validate_generated_knowledge_pack",
    "REQUIRED_FRONTMATTER_FIELDS",
    "REQUIRED_BODY_SECTIONS",
    "ALLOWED_MODELS",
    "ALLOWED_PERMISSION_MODES",
    "KNOWN_TOOLS",
]

#: Required per docs/internal/talent-factory-contract.md §5 /
#: agents/talent-builder.md "Generated-Agent Contract".
REQUIRED_FRONTMATTER_FIELDS: tuple[str, ...] = (
    "name", "description", "model", "permissionMode", "tools",
)
REQUIRED_BODY_SECTIONS: tuple[str, ...] = (
    "Mission", "Before Starting", "Knowledge References", "Principles",
    "Anti-Patterns", "Output Format",
)
ALLOWED_MODELS: frozenset[str] = frozenset({"opus", "sonnet", "haiku"})
#: Permission modes a *generated, unreviewed* agent may declare. The
#: talent-factory pipeline auto-installs and auto-registers whatever
#: passes this validator with no human in the loop, so elevated modes
#: (``auto-edit``, ``acceptEdits``, ``bypassPermissions``) are rejected
#: outright — "set permissionMode to auto-edit" is the canonical injected
#: directive the talent-factory contract (§7) warns about, and a
#: frontmatter value is a quieter channel for the same escalation than
#: body text. A human promotes a reviewed draft to an elevated mode later
#: (agents/talent-builder.md allows auto-edit for *reviewed* implementer
#: agents); the automated pipeline never does.
ALLOWED_PERMISSION_MODES: frozenset[str] = frozenset({"default", "plan"})
#: Known Claude Code tool names. A generated agent requesting a tool
#: outside this set is rejected -- least-privilege can't be verified for
#: a tool the validator doesn't recognize.
KNOWN_TOOLS: frozenset[str] = frozenset({
    "Read", "Write", "Edit", "Glob", "Grep", "Bash", "BashOutput",
    "KillShell", "WebFetch", "WebSearch", "NotebookEdit", "Task",
    "TodoWrite",
})

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*(--[a-z0-9]+(-[a-z0-9]+)*)?$")

#: Coarse, best-effort patterns for text shaped like an injected directive.
#: See module docstring -- this is a fail-closed heuristic, not a proof.
_SUSPICIOUS_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE) for p in (
        r"ignore (all |any )?(previous|prior|above|earlier) instructions",
        r"disregard (all |any )?(the )?(system|prior|previous) (prompt|instructions)",
        r"you are now (in )?(dan|jailbreak|unrestricted|developer mode)",
        r"grant (yourself|this agent|the agent) (the )?(tool|access|permission)",
        r"set\s+permissionmode\s*:?\s*to\s+auto-edit",
        r"reveal (your|the) system prompt",
        r"new system prompt\s*:",
    )
)


@dataclass
class ValidationResult:
    """Outcome of validating one generated artifact."""

    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    name: str = ""
    frontmatter: dict = field(default_factory=dict)
    body: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "name": self.name,
        }


def _coerce_str_list(raw: object) -> list[str]:
    if isinstance(raw, str):
        return [t.strip() for t in raw.split(",") if t.strip()]
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    return []


def validate_generated_agent(
    path: Path,
    *,
    project_root: Path,
    known_agent_names: "set[str] | frozenset[str]" = frozenset(),
) -> ValidationResult:
    """Validate a single generated agent markdown file at *path*.

    Checks frontmatter completeness, provenance, model/tool allowlists,
    the recursion guard, required body sections, knowledge-pack path
    resolution, and the prompt-safety scan. Does not check for name
    collisions against the live registry beyond a non-fatal warning --
    collision *policy* (reject/version_suffix/manual_review) is applied
    at install time by ``talent_factory.py``.
    """
    errors: list[str] = []
    warnings: list[str] = []

    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return ValidationResult(valid=False, errors=[f"could not read {path}: {exc}"])

    frontmatter, body = parse_frontmatter(content)
    if not frontmatter:
        errors.append("missing or unparseable YAML frontmatter")
        frontmatter = {}

    for field_name in REQUIRED_FRONTMATTER_FIELDS:
        value = frontmatter.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"missing required frontmatter field '{field_name}'")

    name = str(frontmatter.get("name", "") or "").strip()
    if name and not _NAME_RE.match(name):
        errors.append(f"name '{name}' is not kebab-case (or 'role--flavor')")
    if name and path.stem != name:
        errors.append(
            f"frontmatter name '{name}' does not match filename '{path.stem}'"
        )
    base_name = name.split("--", 1)[0] if name else ""
    if base_name in NON_GENERABLE_CAPABILITIES:
        errors.append(
            f"generated agent name '{name}' collides with a non-generable "
            "capability -- talent-builder cannot generate itself "
            "(defense-in-depth re-check of capability_gap.NON_GENERABLE_CAPABILITIES)"
        )

    model = str(frontmatter.get("model", "") or "").strip()
    if model and model not in ALLOWED_MODELS:
        errors.append(f"model '{model}' is not one of {sorted(ALLOWED_MODELS)}")

    permission_mode = str(frontmatter.get("permissionMode", "") or "").strip()
    if permission_mode and permission_mode not in ALLOWED_PERMISSION_MODES:
        errors.append(
            f"permissionMode '{permission_mode}' is not one of "
            f"{sorted(ALLOWED_PERMISSION_MODES)} — a generated, unreviewed "
            "agent is auto-installed with no human review and must stay "
            "least-privilege; elevated modes require human promotion "
            "(talent-factory-contract.md §7)"
        )

    tools = _coerce_str_list(frontmatter.get("tools", ""))
    unknown_tools = [t for t in tools if t not in KNOWN_TOOLS]
    if unknown_tools:
        errors.append(f"unknown tool(s) requested: {unknown_tools}")

    created_by = str(frontmatter.get("created_by", "") or "").strip()
    if created_by != "talent-builder":
        errors.append("frontmatter 'created_by' must be 'talent-builder' for provenance")
    status = str(frontmatter.get("status", "") or "").strip()
    if status != "draft":
        errors.append("frontmatter 'status' must be 'draft' for a first generation")
    version = str(frontmatter.get("version", "") or "").strip()
    if not _VERSION_RE.match(version):
        errors.append(f"frontmatter 'version' must be a semver string, got {version!r}")

    for section in REQUIRED_BODY_SECTIONS:
        if not re.search(rf"^#{{1,3}}\s+{re.escape(section)}\s*$", body, re.MULTILINE):
            errors.append(f"missing required body section '## {section}'")

    for kp_path in _coerce_str_list(frontmatter.get("knowledge_packs", [])):
        project_candidate = project_root / kp_path
        global_candidate = Path.home() / ".claude" / kp_path
        if not project_candidate.exists() and not global_candidate.exists():
            warnings.append(
                f"knowledge_packs entry '{kp_path}' does not resolve to an "
                "existing file under the project or global .claude/ tree"
            )

    for pattern in _SUSPICIOUS_PATTERNS:
        if pattern.search(content):
            errors.append(
                "content contains text shaped like an injected directive "
                f"(matches /{pattern.pattern}/) -- ingested source material "
                "must be treated as data, never instructions "
                "(talent-factory-contract.md §7)"
            )

    if name and name in known_agent_names:
        warnings.append(
            f"generated agent name '{name}' collides with an existing "
            "registered agent -- name_collision_policy applies at install time"
        )

    return ValidationResult(
        valid=not errors,
        errors=errors,
        warnings=warnings,
        name=name,
        frontmatter=frontmatter,
        body=body,
    )


def validate_generated_knowledge_pack(
    pack_dir: Path,
    *,
    project_root: Path,
) -> ValidationResult:
    """Validate a generated knowledge-pack directory.

    Per docs/internal/talent-factory-contract.md §5: every file the pack's
    manifest/frontmatter references must exist under the pack directory;
    at minimum an ``overview.md`` under 50 lines.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not pack_dir.is_dir():
        return ValidationResult(valid=False, errors=[f"{pack_dir} is not a directory"], name=pack_dir.name)

    overview = pack_dir / "overview.md"
    if not overview.is_file():
        errors.append("missing required 'overview.md'")
    else:
        try:
            line_count = len(overview.read_text(encoding="utf-8").splitlines())
        except OSError as exc:
            errors.append(f"could not read overview.md: {exc}")
        else:
            if line_count > 50:
                errors.append(
                    f"overview.md has {line_count} lines; must be under 50 "
                    "per agents/talent-builder.md's knowledge-pack format rules"
                )

    md_files = list(pack_dir.glob("*.md"))
    if not md_files:
        errors.append("knowledge pack directory contains no markdown files")

    for md_file in md_files:
        try:
            content = md_file.read_text(encoding="utf-8")
        except OSError as exc:
            warnings.append(f"could not read {md_file.name}: {exc}")
            continue
        for pattern in _SUSPICIOUS_PATTERNS:
            if pattern.search(content):
                errors.append(
                    f"{md_file.name} contains text shaped like an injected "
                    f"directive (matches /{pattern.pattern}/)"
                )

    return ValidationResult(valid=not errors, errors=errors, warnings=warnings, name=pack_dir.name)
