"""Workflow-preset configuration — spec: docs/internal/adversarial-tdd-workflow-design.md §4.

Loads and validates the ``workflow:`` top-level section of ``.claude/baton.yaml``
/ ``baton.yaml`` (stage agent/model overrides, external verifier command,
final-review fan-out knobs). Coexists with
:class:`agent_baton.core.config.manager.ManagerConfig`, which owns a disjoint
set of top-level keys in the same file (see that module's ``_SIBLING_OWNED_KEYS``).

Mirrors :class:`ManagerConfig`'s layered-load conventions: built-in defaults
< ``~/.baton/config.yaml`` < project ``baton.yaml`` (``.claude/baton.yaml``
preferred over the project root; the first file found wins -- see
:meth:`ManagerConfig.find_config_file`). Only the ``workflow:`` sub-key of
each file is read; everything else in the file is ignored here.

Unlike :class:`ManagerConfig`, this loader has no ``cli_overrides`` layer and
no ``warnings``/``source_path`` bookkeeping -- the applier-facing settings
object (:class:`WorkflowSettings`) is small enough not to need it. Invalid
values (e.g. an unknown model tier) raise :class:`ValueError` with an
actionable message naming the offending key/value/valid-options, matching
:class:`ManagerConfig`'s "fail loudly" posture for malformed config.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_baton.core.config.manager import ManagerConfig

ModelTier = Literal["haiku", "sonnet", "opus", "fable"]


class _Section(BaseModel):
    model_config = ConfigDict(extra="ignore")  # unknown nested KEYS ignored; invalid VALUES fail via Literal


class StageOverride(_Section):
    agent: str | None = None
    model: ModelTier | None = None


class WorkflowSettings(_Section):
    stages: dict[str, StageOverride] = Field(default_factory=dict)
    external_command: str = ""
    external_timeout_seconds: int = 1800
    final_review_fanout_divisor: int = 4
    final_review_max_reviewers: int = 3


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    """Read *path* as YAML and require a mapping (or empty) root.

    Raises :class:`ValueError` on read failure, parse failure, or a
    non-mapping root.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Could not read workflow config {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in workflow config {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(
            f"{path}: top-level YAML must be a mapping, got {type(data).__name__}"
        )
    return data


def _workflow_section(path: Path) -> dict[str, Any]:
    section = _read_yaml_mapping(path).get("workflow")
    return dict(section) if isinstance(section, dict) else {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*; *override* wins on conflicts."""
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
    return f"Invalid workflow config{where}: " + "; ".join(parts)


def load_workflow_settings(project_root: Path | None = None) -> WorkflowSettings:
    """Resolve layered ``workflow:`` settings: defaults < user config < project config.

    Layers, lowest to highest precedence:

    1. Built-in field defaults.
    2. ``~/.baton/config.yaml`` (optional; skipped when absent).
    3. Project config discovered via
       :meth:`ManagerConfig.find_config_file` (``.claude/baton.yaml``
       preferred over root ``baton.yaml``; first file found wins -- the
       other is not merged in).

    Only the ``workflow:`` sub-key of each file is read. Raises
    :class:`ValueError` (the common base of both a Pydantic
    ``ValidationError`` and a hand-raised message) with an actionable
    message on an invalid nested value.
    """
    merged: dict[str, Any] = {}
    source: Path | None = None

    user_config_path = Path.home() / ".baton" / "config.yaml"
    if user_config_path.is_file():
        merged = _deep_merge(merged, _workflow_section(user_config_path))
        source = user_config_path

    project_path = ManagerConfig.find_config_file(project_root)
    if project_path is not None:
        merged = _deep_merge(merged, _workflow_section(project_path))
        source = project_path

    try:
        return WorkflowSettings(**merged)
    except ValidationError as exc:
        raise ValueError(_format_validation_error(exc, source)) from exc
