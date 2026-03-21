"""Validate agent definition markdown files for format correctness."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from agent_baton.utils.frontmatter import parse_frontmatter

_VALID_MODELS = {"opus", "sonnet", "haiku"}
_VALID_PERMISSION_MODES = {"auto-edit", "default"}
_VALID_TOOLS = {
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Bash",
    "NotebookEdit",
    "WebFetch",
    "WebSearch",
    "Agent",
}
# kebab-case with optional double-dash flavor: e.g. backend-engineer--python
_KEBAB_CASE_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*(--[a-z0-9]+(-[a-z0-9]+)*)?$")


@dataclass
class ValidationResult:
    path: Path
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class AgentValidator:
    """Validate agent definition .md files for format correctness."""

    def validate_file(self, path: Path) -> ValidationResult:
        """Validate a single agent markdown file.

        Errors are blocking issues; warnings are non-blocking suggestions.
        """
        errors: list[str] = []
        warnings: list[str] = []

        # Read the file
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            return ValidationResult(
                path=path,
                valid=False,
                errors=[f"cannot read file: {exc}"],
            )

        # Error: must start with ---
        if not content.startswith("---"):
            errors.append("file must start with '---' (missing frontmatter)")
            return ValidationResult(path=path, valid=False, errors=errors)

        # Error: frontmatter must be valid YAML
        parts = content.split("---", 2)
        if len(parts) < 3:
            errors.append("frontmatter is not closed with '---'")
            return ValidationResult(path=path, valid=False, errors=errors)

        try:
            metadata: dict = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError as exc:
            errors.append(f"frontmatter YAML is invalid: {exc}")
            return ValidationResult(path=path, valid=False, errors=errors)

        body = parts[2].strip()

        # ── Error checks ─────────────────────────────────────────────────────

        # name: required, non-empty string
        name = metadata.get("name")
        if not name or not isinstance(name, str) or not name.strip():
            errors.append("'name' field is required and must be a non-empty string")
            name = None
        else:
            name = name.strip()
            # name must be kebab-case (optionally with double-dash flavor)
            if not _KEBAB_CASE_RE.match(name):
                errors.append(
                    f"'name' must be kebab-case (got '{name}'); "
                    "allowed: lowercase letters, numbers, hyphens, "
                    "optionally double-dash for flavors"
                )

        # description: required, non-empty string
        description = metadata.get("description")
        if not description or not isinstance(description, str) or not description.strip():
            errors.append("'description' field is required and must be a non-empty string")
            description = None
        else:
            description = description.strip()

        # model: if present, must be one of the valid values
        model = metadata.get("model")
        if model is not None:
            if not isinstance(model, str) or model.strip() not in _VALID_MODELS:
                errors.append(
                    f"'model' must be one of {sorted(_VALID_MODELS)} (got '{model}')"
                )

        # permissionMode: if present, must be one of the valid values
        permission_mode = metadata.get("permissionMode")
        if permission_mode is not None:
            if (
                not isinstance(permission_mode, str)
                or permission_mode.strip() not in _VALID_PERMISSION_MODES
            ):
                errors.append(
                    f"'permissionMode' must be one of {sorted(_VALID_PERMISSION_MODES)} "
                    f"(got '{permission_mode}')"
                )

        # tools: if present as a string, each tool must be valid
        tools_raw = metadata.get("tools")
        if tools_raw is not None and isinstance(tools_raw, str):
            tools = [t.strip() for t in tools_raw.split(",") if t.strip()]
            bad_tools = [t for t in tools if t not in _VALID_TOOLS]
            if bad_tools:
                errors.append(
                    f"invalid tool(s): {bad_tools}; "
                    f"allowed: {sorted(_VALID_TOOLS)}"
                )

        # markdown body must not be empty
        if not body:
            errors.append("markdown body (after frontmatter) must not be empty")

        # ── Warning checks ────────────────────────────────────────────────────

        # description should be multi-line (at least 2 lines)
        if description is not None:
            desc_lines = [ln for ln in description.splitlines() if ln.strip()]
            if len(desc_lines) < 2:
                warnings.append(
                    "description should be multi-line (at least 2 lines) "
                    "for good trigger matching"
                )

        # agent name should match filename stem
        if name is not None:
            expected_stem = path.stem
            if name != expected_stem:
                warnings.append(
                    f"agent name '{name}' does not match filename stem '{expected_stem}'"
                )

        # reviewers/auditors should have permissionMode 'default'
        if name is not None and permission_mode is not None:
            if ("reviewer" in name or "auditor" in name) and permission_mode == "auto-edit":
                warnings.append(
                    "reviewer/auditor agents should have permissionMode 'default', "
                    "not 'auto-edit'"
                )

        # model field should be present
        if model is None:
            warnings.append("'model' field should be present")

        # markdown body should contain a top-level heading
        if body and not re.search(r"^# ", body, re.MULTILINE):
            warnings.append("markdown body should contain a top-level heading ('# ...')")

        valid = len(errors) == 0
        return ValidationResult(
            path=path,
            valid=valid,
            errors=errors,
            warnings=warnings,
        )

    def validate_directory(self, directory: Path) -> list[ValidationResult]:
        """Validate all *.md files in a directory (non-recursive)."""
        if not directory.is_dir():
            return [
                ValidationResult(
                    path=directory,
                    valid=False,
                    errors=[f"'{directory}' is not a directory"],
                )
            ]
        results: list[ValidationResult] = []
        for md_file in sorted(directory.glob("*.md")):
            results.append(self.validate_file(md_file))
        return results
