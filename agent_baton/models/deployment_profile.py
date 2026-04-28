"""Data model for deployment profiles (R3.8).

A DeploymentProfile bundles the deploy-time configuration that an operator
attaches to a release: target environment, required gate types, target SLO
names, and allowed risk levels.  The checker compares a release's actual
state against the profile's requirements and reports soft warnings — no
hard blocks.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class DeploymentProfile:
    """Named set of deploy-time requirements for a release.

    List fields are stored as JSON-encoded TEXT in SQLite and round-trip
    through :meth:`to_dict` / :meth:`from_dict`.
    """

    profile_id: str
    name: str
    environment: str
    required_gates: list[str]
    target_slos: list[str]
    allowed_risk_levels: list[str]
    description: str
    created_at: str

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        """Return a dict suitable for SQLite storage (lists JSON-encoded)."""
        return {
            "profile_id": self.profile_id,
            "name": self.name,
            "environment": self.environment,
            "required_gates": json.dumps(self.required_gates),
            "target_slos": json.dumps(self.target_slos),
            "allowed_risk_levels": json.dumps(self.allowed_risk_levels),
            "description": self.description,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, row: dict[str, object]) -> DeploymentProfile:
        """Construct from a SQLite row dict (JSON-decode list columns)."""

        def _load(value: object) -> list[str]:
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        return [str(x) for x in parsed]
                except (json.JSONDecodeError, TypeError):
                    pass
            return []

        return cls(
            profile_id=str(row["profile_id"]),
            name=str(row.get("name", "")),
            environment=str(row.get("environment", "")),
            required_gates=_load(row.get("required_gates", "[]")),
            target_slos=_load(row.get("target_slos", "[]")),
            allowed_risk_levels=_load(
                row.get("allowed_risk_levels", '["LOW","MEDIUM"]')
            ),
            description=str(row.get("description", "")),
            created_at=str(row.get("created_at", "")),
        )
