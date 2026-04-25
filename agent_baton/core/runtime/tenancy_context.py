"""Tenancy context resolver for usage attribution.

Centralised lookup for the (org, team, user, spec_author, cost_center)
identity tuple used by the usage logger and the user-identity
middleware.  Resolution order::

    BATON_*  env vars           # highest precedence
    ~/.baton/identity.yaml      # per-user fallback (YAML)
    hardcoded defaults          # final fallback

Env vars consulted: ``BATON_ORG_ID``, ``BATON_TEAM_ID``,
``BATON_USER_ID``, ``BATON_SPEC_AUTHOR_ID``, ``BATON_COST_CENTER``.

The result is cached for the life of the process; tests can clear the
cache with ``reset_tenancy_cache()``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

DEFAULT_ORG_ID: Final[str] = "default"
DEFAULT_TEAM_ID: Final[str] = "default"
DEFAULT_USER_ID: Final[str] = "local-user"

_IDENTITY_PATH: Final[Path] = Path.home() / ".baton" / "identity.yaml"

_ENV_KEYS: Final[dict[str, str]] = {
    "org_id": "BATON_ORG_ID",
    "team_id": "BATON_TEAM_ID",
    "user_id": "BATON_USER_ID",
    "spec_author_id": "BATON_SPEC_AUTHOR_ID",
    "cost_center": "BATON_COST_CENTER",
}


@dataclass(frozen=True)
class TenancyContext:
    """Five-field identity tuple attached to every usage row.

    Attributes:
        org_id: Owning organisation identifier.
        team_id: Owning team within the organisation.
        user_id: The human/agent operator identifier.
        spec_author_id: Author of the spec that drove the work, if any.
        cost_center: Free-form cost-allocation tag (e.g. "R&D-2026").
    """

    org_id: str = DEFAULT_ORG_ID
    team_id: str = DEFAULT_TEAM_ID
    user_id: str = DEFAULT_USER_ID
    spec_author_id: str = ""
    cost_center: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "org_id": self.org_id,
            "team_id": self.team_id,
            "user_id": self.user_id,
            "spec_author_id": self.spec_author_id,
            "cost_center": self.cost_center,
        }


_cached: TenancyContext | None = None


def reset_tenancy_cache() -> None:
    """Clear the cached tenancy context (test helper)."""
    global _cached
    _cached = None


def _load_identity_yaml(path: Path = _IDENTITY_PATH) -> dict[str, str]:
    """Parse ``~/.baton/identity.yaml`` into a dict.

    Returns an empty dict if the file is missing, unreadable, or
    malformed.  Uses PyYAML when available and falls back to a tiny
    ``key: value`` line parser so the resolver works in environments
    that do not have PyYAML installed.
    """
    if not path.is_file():
        return {}

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    try:
        import yaml  # type: ignore[import-untyped]

        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items() if v is not None}
    except ImportError:
        # Minimal fallback: parse "key: value" lines, ignore comments.
        out: dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            out[key.strip()] = value.strip().strip("'\"")
        return out


def get_current_tenancy(
    *, refresh: bool = False, identity_path: Path | None = None
) -> TenancyContext:
    """Return the active ``TenancyContext``.

    Args:
        refresh: When ``True``, ignore the cached value and re-resolve
            from the environment / yaml file.  Useful in tests that
            manipulate ``os.environ`` between assertions.
        identity_path: Override the YAML file location (test hook).

    Returns:
        A populated :class:`TenancyContext`.  Missing values fall back
        to ``"default"``/``"local-user"``/``""`` per the field defaults.
    """
    global _cached
    if not refresh and _cached is not None and identity_path is None:
        return _cached

    yaml_values = _load_identity_yaml(identity_path or _IDENTITY_PATH)
    resolved: dict[str, str] = {}
    for field_name, env_key in _ENV_KEYS.items():
        env_val = os.environ.get(env_key, "").strip()
        if env_val:
            resolved[field_name] = env_val
            continue
        yaml_val = yaml_values.get(field_name, "").strip()
        if yaml_val:
            resolved[field_name] = yaml_val

    ctx = TenancyContext(
        org_id=resolved.get("org_id", DEFAULT_ORG_ID),
        team_id=resolved.get("team_id", DEFAULT_TEAM_ID),
        user_id=resolved.get("user_id", DEFAULT_USER_ID),
        spec_author_id=resolved.get("spec_author_id", ""),
        cost_center=resolved.get("cost_center", ""),
    )
    if identity_path is None:
        _cached = ctx
    return ctx
