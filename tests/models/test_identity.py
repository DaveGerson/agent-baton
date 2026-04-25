"""Unit tests for :mod:`agent_baton.models.identity` and the user-store
helpers backing the H3.1 (bd-0dea) human-role taxonomy.

Coverage:

* Enum string values are stable (used by future SoD policy code).
* :meth:`HumanRole.parse` normalises common variants and rejects junk
  with a helpful error.
* :class:`UserIdentity` round-trips through ``to_dict`` / ``from_dict``.
* The default human role is :attr:`HumanRole.UNASSIGNED`.
* Pre-H3.1 dicts (no ``human_role`` key) load as ``UNASSIGNED``.
* :meth:`UserStore.assign_role` writes the SQL row; reading it back
  returns the new role.
* :func:`get_user_role` returns ``UNASSIGNED`` for unknown users.
* Pre-v16 ``users`` table (no ``human_role`` column) is migrated on
  first access and existing rows load as ``UNASSIGNED``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.storage.user_store import UserStore, get_user_role
from agent_baton.models.identity import HumanRole, UserIdentity


# ---------------------------------------------------------------------------
# Enum
# ---------------------------------------------------------------------------


class TestHumanRoleEnum:
    def test_string_values_are_stable(self):
        """Stable string values matter -- they are persisted and consumed
        by future SoD policy.  Renaming is a breaking change."""
        assert HumanRole.JUNIOR.value == "junior"
        assert HumanRole.SENIOR.value == "senior"
        assert HumanRole.TECH_LEAD.value == "tech_lead"
        assert HumanRole.ARCHITECT.value == "architect"
        assert HumanRole.ENGINEERING_MANAGER.value == "engineering_manager"
        assert HumanRole.QA.value == "qa"
        assert HumanRole.UNASSIGNED.value == ""

    def test_is_str_subclass(self):
        # str subclassing matters for SQL and JSON encoding.
        assert isinstance(HumanRole.JUNIOR, str)
        assert HumanRole.JUNIOR == "junior"

    def test_parse_canonical(self):
        for member in HumanRole:
            assert HumanRole.parse(member.value) is member

    def test_parse_none_and_empty(self):
        assert HumanRole.parse(None) is HumanRole.UNASSIGNED
        assert HumanRole.parse("") is HumanRole.UNASSIGNED
        assert HumanRole.parse("unassigned") is HumanRole.UNASSIGNED
        assert HumanRole.parse("  Unassigned  ") is HumanRole.UNASSIGNED

    def test_parse_human_friendly_variants(self):
        assert HumanRole.parse("Tech Lead") is HumanRole.TECH_LEAD
        assert HumanRole.parse("TECH-LEAD") is HumanRole.TECH_LEAD
        assert HumanRole.parse("Engineering Manager") is HumanRole.ENGINEERING_MANAGER
        assert HumanRole.parse("QA") is HumanRole.QA

    def test_parse_unknown_raises_with_helpful_message(self):
        with pytest.raises(ValueError) as exc_info:
            HumanRole.parse("ceo")
        msg = str(exc_info.value)
        assert "ceo" in msg
        # The error must list the valid options so the operator can
        # self-correct.
        for canonical in ("junior", "senior", "tech_lead", "architect", "qa"):
            assert canonical in msg


# ---------------------------------------------------------------------------
# UserIdentity dataclass
# ---------------------------------------------------------------------------


class TestUserIdentityDefaults:
    def test_default_human_role_is_unassigned(self):
        u = UserIdentity(user_id="alice")
        assert u.human_role is HumanRole.UNASSIGNED

    def test_default_role_is_creator(self):
        # Backwards compatibility with the pre-H3.1 schema default.
        u = UserIdentity(user_id="alice")
        assert u.role == "creator"

    def test_created_at_is_iso_z(self):
        u = UserIdentity(user_id="alice")
        assert u.created_at.endswith("Z")
        assert "T" in u.created_at


class TestUserIdentityRoundTrip:
    def test_to_dict_emits_string_value(self):
        u = UserIdentity(
            user_id="alice",
            display_name="Alice",
            email="alice@example.com",
            role="approver",
            human_role=HumanRole.TECH_LEAD,
            created_at="2026-04-25T00:00:00Z",
        )
        d = u.to_dict()
        # human_role must be a plain string so json.dumps works without
        # custom encoders.
        assert d["human_role"] == "tech_lead"
        assert isinstance(d["human_role"], str)

    def test_round_trip_preserves_role(self):
        original = UserIdentity(
            user_id="alice",
            display_name="Alice",
            email="alice@example.com",
            role="approver",
            human_role=HumanRole.ARCHITECT,
            created_at="2026-04-25T00:00:00Z",
        )
        restored = UserIdentity.from_dict(original.to_dict())
        assert restored == original
        assert restored.human_role is HumanRole.ARCHITECT

    def test_round_trip_unassigned(self):
        original = UserIdentity(user_id="bob")
        restored = UserIdentity.from_dict(original.to_dict())
        assert restored.human_role is HumanRole.UNASSIGNED

    def test_from_dict_tolerates_missing_human_role(self):
        """Pre-H3.1 dicts (no ``human_role`` key) must load as UNASSIGNED."""
        legacy = {
            "user_id": "carol",
            "display_name": "Carol",
            "email": "carol@example.com",
            "role": "creator",
            "created_at": "2026-04-25T00:00:00Z",
        }
        restored = UserIdentity.from_dict(legacy)
        assert restored.human_role is HumanRole.UNASSIGNED
        assert restored.user_id == "carol"

    def test_from_dict_tolerates_none_human_role(self):
        legacy = {
            "user_id": "dave",
            "human_role": None,
        }
        restored = UserIdentity.from_dict(legacy)
        assert restored.human_role is HumanRole.UNASSIGNED


# ---------------------------------------------------------------------------
# UserStore (SQLite)
# ---------------------------------------------------------------------------


@pytest.fixture()
def central_db(tmp_path: Path) -> Path:
    """A throwaway central.db path inside the test tmp_path."""
    return tmp_path / "central.db"


class TestUserStore:
    def test_list_all_empty(self, central_db: Path):
        store = UserStore(db_path=central_db)
        assert store.list_all() == []

    def test_get_unknown_returns_none(self, central_db: Path):
        store = UserStore(db_path=central_db)
        assert store.get("nobody") is None

    def test_assign_role_creates_row(self, central_db: Path):
        store = UserStore(db_path=central_db)
        identity = store.assign_role("alice", HumanRole.TECH_LEAD)
        assert identity.user_id == "alice"
        assert identity.human_role is HumanRole.TECH_LEAD

    def test_assign_role_persists_to_sql(self, central_db: Path):
        store = UserStore(db_path=central_db)
        store.assign_role("alice", HumanRole.SENIOR)

        # New store instance -> reads from disk, not memory.
        store2 = UserStore(db_path=central_db)
        loaded = store2.get("alice")
        assert loaded is not None
        assert loaded.human_role is HumanRole.SENIOR

    def test_assign_role_overwrites_existing(self, central_db: Path):
        store = UserStore(db_path=central_db)
        store.assign_role("alice", HumanRole.JUNIOR)
        store.assign_role("alice", HumanRole.ARCHITECT)
        loaded = store.get("alice")
        assert loaded is not None
        assert loaded.human_role is HumanRole.ARCHITECT

    def test_list_all_returns_assigned_users(self, central_db: Path):
        store = UserStore(db_path=central_db)
        store.assign_role("alice", HumanRole.JUNIOR)
        store.assign_role("bob", HumanRole.QA)

        rows = store.list_all()
        ids = {r.user_id for r in rows}
        assert ids == {"alice", "bob"}

    def test_list_all_orders_by_user_id(self, central_db: Path):
        store = UserStore(db_path=central_db)
        store.assign_role("zara", HumanRole.JUNIOR)
        store.assign_role("alice", HumanRole.SENIOR)
        rows = store.list_all()
        assert [r.user_id for r in rows] == ["alice", "zara"]


class TestPreV16MigrationCompatibility:
    """The v16 migration must add ``human_role`` to existing ``users`` tables.

    Simulates a database that was created at a pre-v16 schema (no
    ``human_role`` column) and confirms that opening it via
    :class:`UserStore` upgrades the schema and preserves existing rows
    as :attr:`HumanRole.UNASSIGNED`.
    """

    def test_existing_user_row_loads_as_unassigned(self, central_db: Path):
        import sqlite3

        # Hand-craft a pre-v16 users table (no human_role column) and
        # mark the schema version as 15 so the connection manager runs
        # the v16 migration on next access.
        central_db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(central_db))
        conn.execute("CREATE TABLE _schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO _schema_version VALUES (15)")
        conn.execute(
            "CREATE TABLE users ("
            "user_id TEXT PRIMARY KEY,"
            "display_name TEXT NOT NULL DEFAULT '',"
            "email TEXT NOT NULL DEFAULT '',"
            "role TEXT NOT NULL DEFAULT 'creator',"
            "created_at TEXT NOT NULL DEFAULT ''"
            ")"
        )
        conn.execute(
            "INSERT INTO users (user_id, display_name, role) "
            "VALUES (?, ?, ?)",
            ("legacy-alice", "Legacy Alice", "approver"),
        )
        conn.commit()
        conn.close()

        # Opening through UserStore must run the v16 migration.
        store = UserStore(db_path=central_db)
        loaded = store.get("legacy-alice")
        assert loaded is not None
        assert loaded.user_id == "legacy-alice"
        assert loaded.display_name == "Legacy Alice"
        assert loaded.role == "approver"
        # Pre-v16 row defaults to UNASSIGNED.
        assert loaded.human_role is HumanRole.UNASSIGNED

        # Subsequent assign_role works -> column was added by the migration.
        store.assign_role("legacy-alice", HumanRole.QA)
        reloaded = UserStore(db_path=central_db).get("legacy-alice")
        assert reloaded is not None
        assert reloaded.human_role is HumanRole.QA


class TestGetUserRoleHelper:
    def test_unknown_user_returns_unassigned(self, central_db: Path):
        assert get_user_role("ghost", db_path=central_db) is HumanRole.UNASSIGNED

    def test_empty_user_id_returns_unassigned(self, central_db: Path):
        assert get_user_role("", db_path=central_db) is HumanRole.UNASSIGNED

    def test_known_user_returns_role(self, central_db: Path):
        store = UserStore(db_path=central_db)
        store.assign_role("alice", HumanRole.ENGINEERING_MANAGER)
        assert (
            get_user_role("alice", db_path=central_db)
            is HumanRole.ENGINEERING_MANAGER
        )
