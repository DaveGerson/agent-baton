"""Data models for the Tenancy & Cost Attribution hierarchy (F0.2).

The tenancy hierarchy is: Org → Team → Project → User → Agent.
These models back the ``tenancy_orgs``, ``tenancy_teams``, and
``tenancy_cost_centers`` tables added in the v16 migration.

Tenancy context is resolved at runtime from:
1. ``~/.baton/identity.yaml`` — persistent local identity file
2. Environment variables: ``BATON_ORG_ID``, ``BATON_TEAM_ID``,
   ``BATON_COST_CENTER``, ``BATON_USER_ID``
3. Fallback defaults: ``org_id="default"``, ``team_id="default"``
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_CENTRAL_DB_DEFAULT = Path.home() / ".baton" / "central.db"
_IDENTITY_FILE = Path.home() / ".baton" / "identity.yaml"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Org:
    """An organisation in the tenancy hierarchy.

    Attributes:
        org_id: Unique org identifier.
        display_name: Human-readable org name.
        created_at: ISO-8601 creation timestamp.
    """

    org_id: str
    display_name: str = ""
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "display_name": self.display_name,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Org:
        return cls(
            org_id=data["org_id"],
            display_name=data.get("display_name", ""),
            created_at=data.get("created_at", _now_iso()),
        )


@dataclass
class Team:
    """A team within an org.

    Attributes:
        team_id: Unique team identifier.
        org_id: Parent org.
        display_name: Human-readable team name.
        created_at: ISO-8601 creation timestamp.
    """

    team_id: str
    org_id: str = "default"
    display_name: str = ""
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "org_id": self.org_id,
            "display_name": self.display_name,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Team:
        return cls(
            team_id=data["team_id"],
            org_id=data.get("org_id", "default"),
            display_name=data.get("display_name", ""),
            created_at=data.get("created_at", _now_iso()),
        )


@dataclass
class CostCenter:
    """A cost center for FinOps attribution.

    Attributes:
        cost_center_id: Unique cost-center identifier.
        org_id: Parent org.
        display_name: Human-readable cost-center name.
        created_at: ISO-8601 creation timestamp.
    """

    cost_center_id: str
    org_id: str = "default"
    display_name: str = ""
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cost_center_id": self.cost_center_id,
            "org_id": self.org_id,
            "display_name": self.display_name,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CostCenter:
        return cls(
            cost_center_id=data["cost_center_id"],
            org_id=data.get("org_id", "default"),
            display_name=data.get("display_name", ""),
            created_at=data.get("created_at", _now_iso()),
        )


@dataclass
class TenancyContext:
    """Resolved tenancy context for tagging usage events.

    This is a lightweight value object resolved at runtime from
    identity.yaml / env vars and passed to ``UsageLogger.log()``.

    Attributes:
        org_id: Organisation identifier.
        team_id: Team identifier.
        user_id: User identifier.
        cost_center: Cost center code.
    """

    org_id: str = "default"
    team_id: str = "default"
    user_id: str = "local-user"
    cost_center: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "org_id": self.org_id,
            "team_id": self.team_id,
            "user_id": self.user_id,
            "cost_center": self.cost_center,
        }


# ---------------------------------------------------------------------------
# Context resolver
# ---------------------------------------------------------------------------

def resolve_tenancy_context() -> TenancyContext:
    """Resolve tenancy context from identity.yaml then env vars.

    Resolution order for each field:
    1. ``~/.baton/identity.yaml`` (YAML key matching field name)
    2. Environment variable (``BATON_ORG_ID``, ``BATON_TEAM_ID``, etc.)
    3. Default value

    Returns:
        A ``TenancyContext`` with all fields populated.
    """
    identity: dict[str, str] = {}
    if _IDENTITY_FILE.exists():
        try:
            import yaml  # type: ignore[import]
            raw = yaml.safe_load(_IDENTITY_FILE.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                identity = {str(k): str(v) for k, v in raw.items()}
        except Exception:
            pass

    def _pick(key: str, env_var: str, default: str) -> str:
        return (
            identity.get(key)
            or os.environ.get(env_var, "").strip()
            or default
        )

    return TenancyContext(
        org_id=_pick("org_id", "BATON_ORG_ID", "default"),
        team_id=_pick("team_id", "BATON_TEAM_ID", "default"),
        user_id=_pick("user_id", "BATON_USER_ID", "local-user"),
        cost_center=_pick("cost_center", "BATON_COST_CENTER", ""),
    )


# ---------------------------------------------------------------------------
# TenancyStore
# ---------------------------------------------------------------------------

class TenancyStore:
    """Persist and query tenancy hierarchy entities.

    Args:
        db_path: SQLite database path.  Defaults to ``~/.baton/central.db``.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = (db_path or _CENTRAL_DB_DEFAULT).resolve()

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # -- Orgs ----------------------------------------------------------------

    def create_org(self, org_id: str, display_name: str = "") -> Org:
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO tenancy_orgs (org_id, display_name, created_at) VALUES (?,?,?)",
                (org_id, display_name, now),
            )
            conn.commit()
        return Org(org_id=org_id, display_name=display_name, created_at=now)

    def get_org(self, org_id: str) -> Org | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tenancy_orgs WHERE org_id = ?", (org_id,)
            ).fetchone()
        if row is None:
            return None
        return Org(org_id=row["org_id"], display_name=row["display_name"],
                   created_at=row["created_at"])

    def list_orgs(self) -> list[Org]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM tenancy_orgs ORDER BY created_at").fetchall()
        return [Org(org_id=r["org_id"], display_name=r["display_name"],
                    created_at=r["created_at"]) for r in rows]

    # -- Teams ---------------------------------------------------------------

    def create_team(self, team_id: str, org_id: str = "default",
                    display_name: str = "") -> Team:
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO tenancy_teams (team_id, org_id, display_name, created_at) VALUES (?,?,?,?)",
                (team_id, org_id, display_name, now),
            )
            conn.commit()
        return Team(team_id=team_id, org_id=org_id, display_name=display_name,
                    created_at=now)

    def get_team(self, team_id: str) -> Team | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tenancy_teams WHERE team_id = ?", (team_id,)
            ).fetchone()
        if row is None:
            return None
        return Team(team_id=row["team_id"], org_id=row["org_id"],
                    display_name=row["display_name"], created_at=row["created_at"])

    def list_teams(self, org_id: str | None = None) -> list[Team]:
        with self._connect() as conn:
            if org_id:
                rows = conn.execute(
                    "SELECT * FROM tenancy_teams WHERE org_id = ? ORDER BY created_at",
                    (org_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tenancy_teams ORDER BY created_at"
                ).fetchall()
        return [Team(team_id=r["team_id"], org_id=r["org_id"],
                     display_name=r["display_name"], created_at=r["created_at"])
                for r in rows]

    # -- Cost Centers --------------------------------------------------------

    def create_cost_center(self, cost_center_id: str, org_id: str = "default",
                           display_name: str = "") -> CostCenter:
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO tenancy_cost_centers "
                "(cost_center_id, org_id, display_name, created_at) VALUES (?,?,?,?)",
                (cost_center_id, org_id, display_name, now),
            )
            conn.commit()
        return CostCenter(cost_center_id=cost_center_id, org_id=org_id,
                          display_name=display_name, created_at=now)

    # -- Identity file -------------------------------------------------------

    @staticmethod
    def write_identity(
        org_id: str | None = None,
        team_id: str | None = None,
        user_id: str | None = None,
        cost_center: str | None = None,
    ) -> Path:
        """Persist tenancy context to ``~/.baton/identity.yaml``.

        Merges with any existing file so unrelated keys are preserved.

        Args:
            org_id: Org to set (omit to leave unchanged).
            team_id: Team to set (omit to leave unchanged).
            user_id: User ID to set (omit to leave unchanged).
            cost_center: Cost center to set (omit to leave unchanged).

        Returns:
            Path to the written identity file.
        """
        existing: dict[str, str] = {}
        if _IDENTITY_FILE.exists():
            try:
                import yaml  # type: ignore[import]
                raw = yaml.safe_load(_IDENTITY_FILE.read_text(encoding="utf-8")) or {}
                if isinstance(raw, dict):
                    existing = dict(raw)
            except Exception:
                pass

        if org_id is not None:
            existing["org_id"] = org_id
        if team_id is not None:
            existing["team_id"] = team_id
        if user_id is not None:
            existing["user_id"] = user_id
        if cost_center is not None:
            existing["cost_center"] = cost_center

        _IDENTITY_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            import yaml  # type: ignore[import]
            _IDENTITY_FILE.write_text(
                yaml.dump(existing, default_flow_style=False), encoding="utf-8"
            )
        except ImportError:
            # Fallback: write simple key: value lines without PyYAML
            lines = [f"{k}: {v}\n" for k, v in existing.items()]
            _IDENTITY_FILE.write_text("".join(lines), encoding="utf-8")
        return _IDENTITY_FILE

    # -- Backfill ------------------------------------------------------------

    def migrate_existing(self, org_id: str = "default",
                         team_id: str = "default") -> int:
        """Backfill tenancy columns on existing usage_records rows.

        Sets ``org_id`` and ``team_id`` on rows where they still hold
        the empty-string sentinel (pre-v16 rows that were upgraded via
        ALTER TABLE but never had values written).

        Args:
            org_id: Org ID to apply.
            team_id: Team ID to apply.

        Returns:
            Number of rows updated.
        """
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE usage_records SET org_id=?, team_id=? WHERE org_id='' OR team_id=''",
                (org_id, team_id),
            )
            conn.commit()
        return cur.rowcount
