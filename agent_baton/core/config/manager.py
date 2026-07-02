"""Manager-mode (PMO) configuration — spec: docs/internal/manager-mode-pmo-design.md.

Loads and validates ``.claude/baton.yaml`` / ``baton.yaml`` manager-mode
sections (``manager_mode``, ``team``, ``scoping``, ``context``,
``knowledge_packs``, ``policies``, ``gates``, ``reporting``). Coexists with
:class:`agent_baton.core.config.project_config.ProjectConfig`, which owns a
disjoint set of top-level keys (``default_agents``, ``default_gates``,
``default_risk_level``, ``auto_route_rules``, ``excluded_paths``,
``default_isolation``) in the same file.

Unlike :class:`ProjectConfig` (best-effort — parse failures fall back to an
empty config), this loader **fails early**: malformed YAML or invalid
nested policy values raise :class:`ManagerConfigError` with an actionable
message naming the offending key, value, and valid options. Missing files
and unknown top-level keys are non-fatal (defaults / warnings respectively)
per spec §9.2.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

CONFIG_BASENAME = "baton.yaml"
Size = Literal["light", "medium", "heavy"]


class ManagerConfigError(ValueError):
    """Raised for unparseable YAML or invalid nested values.

    The message names the offending key/value and valid options so the
    error is actionable without opening the source file.
    """


class _Section(BaseModel):
    model_config = ConfigDict(extra="ignore")  # unknown nested KEYS ignored; invalid VALUES fail via Literal


class ManagerModeConfig(_Section):
    enabled_by_default: bool = False
    project_size_default: Size = "medium"
    manager_decision_threshold: Size = "medium"
    assumptions_policy: Literal["record_and_continue", "ask_always"] = "record_and_continue"
    ambiguity_policy: Literal["ask_when_high_impact", "always_ask", "record_and_continue"] = "ask_when_high_impact"


class TeamConfig(_Section):
    max_agents_by_complexity: dict[str, int] = Field(default_factory=lambda: {"light": 2, "medium": 5, "heavy": 8})
    require_role_cards: bool = True
    require_workstream_owners: bool = True
    prefer_specialists_over_generalists: bool = True
    allow_talent_builder: bool = True
    default_roles: list[str] = Field(default_factory=lambda: ["architect", "backend-engineer", "test-engineer"])


class ScopingConfig(_Section):
    require_scope_contracts: bool = True
    require_allowed_paths: bool = True
    allow_cross_scope_edits: Literal["manager_approval", "allow", "block"] = "manager_approval"
    scope_expansion_policy: Literal["allow_with_note", "queue_for_manager", "block"] = "queue_for_manager"
    out_of_scope_policy: Literal["block_or_escalate", "warn"] = "block_or_escalate"


class ContextConfig(_Section):
    default_step_token_budget: int = 12000
    max_knowledge_docs_per_step: int = 6
    include_prior_phase_handoff: bool = True
    include_full_prior_outputs: bool = False
    summarize_prior_outputs: bool = True
    dedupe_knowledge_across_session: bool = True
    context_bundle_format: Literal["json"] = "json"


class KnowledgePackConfig(_Section):
    discovery_paths: list[str] = Field(default_factory=lambda: [".claude/knowledge", "docs", "."])
    default_packs: list[str] = Field(default_factory=lambda: ["repo-architecture", "coding-conventions", "testing-strategy"])
    required_for_code_steps: list[str] = Field(default_factory=lambda: ["coding-conventions", "testing-strategy"])
    stale_after_days: int = 90
    missing_pack_policy: Literal["propose", "warn", "ignore"] = "propose"


class PhaseCompletionPolicy(_Section):
    adversarial_review: Literal["always", "risk_based", "off"] = "always"
    handoff_required: bool = True
    gates: Literal["project_configured", "focused", "full", "smoke", "off"] = "project_configured"


class ProjectCompletionPolicy(_Section):
    adversarial_review: Literal["always", "off"] = "always"
    manager_report: Literal["required", "optional"] = "required"
    retrospective: Literal["required", "optional"] = "required"


class ReviewAgentsConfig(_Section):
    adversarial_review: str = "code-reviewer"
    project_review: str = "auditor"


class PoliciesConfig(_Section):
    phase_completion: PhaseCompletionPolicy = Field(default_factory=PhaseCompletionPolicy)
    project_completion: ProjectCompletionPolicy = Field(default_factory=ProjectCompletionPolicy)
    review_agents: ReviewAgentsConfig = Field(default_factory=ReviewAgentsConfig)


class GatesConfig(_Section):
    mode: Literal["project_configured", "focused", "full", "smoke", "off"] = "project_configured"
    gate_scope: Literal["focused", "full", "smoke"] = "focused"
    allow_smoke_fallback: bool = True
    missing_gate_policy: Literal["warn_and_request_manager_decision", "warn", "fail"] = "warn_and_request_manager_decision"


class ReportingConfig(_Section):
    write_manager_brief: bool = True
    write_manager_report: bool = True
    decision_log: bool = True
    include_raw_logs_by_default: bool = False


_KNOWN_SECTIONS = {"version", "manager_mode", "team", "scoping", "context", "knowledge_packs", "policies", "gates", "reporting"}
_PROJECT_CONFIG_KEYS = {"default_agents", "default_gates", "default_risk_level", "auto_route_rules", "excluded_paths", "default_isolation"}


class ManagerConfig(_Section):
    version: int = 1
    manager_mode: ManagerModeConfig = Field(default_factory=ManagerModeConfig)
    team: TeamConfig = Field(default_factory=TeamConfig)
    scoping: ScopingConfig = Field(default_factory=ScopingConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    knowledge_packs: KnowledgePackConfig = Field(default_factory=KnowledgePackConfig)
    policies: PoliciesConfig = Field(default_factory=PoliciesConfig)
    gates: GatesConfig = Field(default_factory=GatesConfig)
    reporting: ReportingConfig = Field(default_factory=ReportingConfig)
    source_path: Path | None = Field(default=None, exclude=True)
    warnings: list[str] = Field(default_factory=list, exclude=True)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ManagerConfig":
        return cls._validated(data)

    @classmethod
    def _validated(cls, raw: dict[str, Any], *, source: Path | None = None) -> "ManagerConfig":
        """Split known/unknown top-level keys; wrap ValidationError.

        Unknown keys that belong to :class:`ProjectConfig` (a sibling
        loader reading the same file) are silently ignored. Any other
        unknown top-level key is recorded on ``warnings`` and logged, but
        does not prevent the config from loading. Invalid nested values
        (bad Literal choices) raise :class:`ManagerConfigError` naming the
        offending key, value, and valid options.
        """
        raw = dict(raw or {})
        warnings: list[str] = []
        known: dict[str, Any] = {}
        for key, value in raw.items():
            if key in _KNOWN_SECTIONS:
                known[key] = value
            elif key in _PROJECT_CONFIG_KEYS:
                continue  # owned by ProjectConfig; silently ignored here
            else:
                where = f" ({source})" if source else ""
                message = f"Unknown top-level key {key!r} in manager config{where}; ignoring."
                warnings.append(message)
                logger.warning(message)

        try:
            config = cls(**known)
        except ValidationError as exc:
            raise ManagerConfigError(_format_validation_error(exc, source)) from exc

        config.source_path = source
        config.warnings = warnings
        return config

    @classmethod
    def from_yaml(cls, path: Path) -> "ManagerConfig":
        """Parse *path* as YAML and validate. Fails early (unlike ``ProjectConfig``)."""
        data = _read_yaml_mapping(path)
        return cls._validated(data, source=path.resolve())

    @classmethod
    def find_config_file(cls, start_dir: Path | None = None) -> Path | None:
        """Walk UP from *start_dir* (default cwd) toward the filesystem root.

        At each level, check ``<dir>/.claude/baton.yaml`` first, then
        ``<dir>/baton.yaml``; the first hit wins. Mirrors
        :meth:`ProjectConfig.load`'s upward walk shape.
        """
        cwd = (start_dir or Path.cwd()).resolve()
        for d in [cwd, *cwd.parents]:
            claude_candidate = d / ".claude" / CONFIG_BASENAME
            if claude_candidate.is_file():
                return claude_candidate
            root_candidate = d / CONFIG_BASENAME
            if root_candidate.is_file():
                return root_candidate
        return None

    @classmethod
    def load(
        cls,
        start_dir: Path | None = None,
        *,
        cli_overrides: dict[str, Any] | None = None,
    ) -> "ManagerConfig":
        """Resolve layered config: defaults < user config < project config < CLI overrides.

        Layers, lowest to highest precedence:

        1. Built-in field defaults.
        2. ``~/.baton/config.yaml`` (optional; skipped when absent).
        3. Project config discovered via :meth:`find_config_file`.
        4. *cli_overrides*.

        Layers are deep-merged dict-wise (nested mappings merge key-by-key;
        scalars and lists from a higher-precedence layer replace the lower
        layer's value outright) and validated once against the merged
        result, so a single :class:`ManagerConfigError` names the true
        offending source.
        """
        merged: dict[str, Any] = {}
        source: Path | None = None

        user_config_path = Path.home() / ".baton" / "config.yaml"
        if user_config_path.is_file():
            merged = _deep_merge(merged, _read_yaml_mapping(user_config_path))
            source = user_config_path

        project_path = cls.find_config_file(start_dir)
        if project_path is not None:
            merged = _deep_merge(merged, _read_yaml_mapping(project_path))
            source = project_path

        if cli_overrides:
            merged = _deep_merge(merged, cli_overrides)

        return cls._validated(merged, source=source)


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    """Read *path* as YAML and require a mapping (or empty) root.

    Raises :class:`ManagerConfigError` on read failure, parse failure, or
    a non-mapping root — the manager config loader fails early rather than
    falling back to defaults silently.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManagerConfigError(f"Could not read manager config {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManagerConfigError(f"Invalid YAML in manager config {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ManagerConfigError(
            f"{path}: top-level YAML must be a mapping, got {type(data).__name__}"
        )
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*; *override* wins on conflicts.

    Nested mappings are merged key-by-key; any other value type (scalars,
    lists) from *override* replaces the corresponding value in *base*
    outright.
    """
    result = dict(base)
    for key, value in override.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[key] = _deep_merge(existing, value)
        else:
            result[key] = value
    return result


def _format_validation_error(exc: ValidationError, source: Path | None) -> str:
    """Render a Pydantic :class:`ValidationError` as an actionable message.

    Names the dotted path to the offending key, the bad value supplied,
    and (via Pydantic's own literal_error message) the valid options.
    """
    where = f" in {source}" if source else ""
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        bad_value = err.get("input")
        msg = err.get("msg", "")
        parts.append(f"{loc}: {bad_value!r} is invalid — {msg}")
    return f"Invalid manager config{where}: " + "; ".join(parts)
