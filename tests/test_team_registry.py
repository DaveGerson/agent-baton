"""Tests for :class:`TeamRegistry` — multi-team registry persistence.

Covers create/lookup/complete semantics, multiple concurrent teams per
leader, and parent/child nesting.  The registry is backed by the ``teams``
table (schema v15); tests use a tmp_path SQLite DB to isolate state.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.engine.team_registry import TeamRegistry
from agent_baton.models.team import Team


@pytest.fixture
def registry(tmp_path: Path) -> TeamRegistry:
    # Instantiating the registry applies PROJECT_SCHEMA_DDL (incl. executions).
    reg = TeamRegistry(tmp_path / "baton.db")
    return reg


def _seed_execution(db_path: Path, task_id: str) -> None:
    """Insert a minimal executions row so the FK constraint is satisfied."""
    from datetime import datetime, timezone
    from agent_baton.core.storage.connection import ConnectionManager
    from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION
    mgr = ConnectionManager(db_path)
    mgr.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
    conn = mgr.get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO executions (task_id, status, started_at) VALUES (?, ?, ?)",
        (task_id, "running", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


class TestTeamCreation:
    def test_create_team_returns_persisted_object(
        self, registry: TeamRegistry, tmp_path: Path
    ) -> None:
        _seed_execution(tmp_path / "baton.db", "t1")
        team = registry.create_team(
            task_id="t1",
            team_id="team-1.1",
            step_id="1.1",
            leader_agent="architect",
            leader_member_id="1.1.a",
        )
        assert team is not None
        assert team.team_id == "team-1.1"
        assert team.task_id == "t1"
        assert team.status == "active"
        assert team.created_at != ""

    def test_create_team_is_idempotent(
        self, registry: TeamRegistry, tmp_path: Path
    ) -> None:
        _seed_execution(tmp_path / "baton.db", "t1")
        first = registry.create_team(
            task_id="t1", team_id="team-1.1", step_id="1.1",
            leader_agent="architect", leader_member_id="1.1.a",
        )
        second = registry.create_team(
            task_id="t1", team_id="team-1.1", step_id="1.1",
            leader_agent="architect", leader_member_id="1.1.a",
        )
        assert first is not None and second is not None
        assert first.team_id == second.team_id
        assert first.created_at == second.created_at

    def test_create_returns_none_when_table_missing(self, tmp_path: Path) -> None:
        """Graceful degradation: absent table yields None, no exception."""
        registry = TeamRegistry(tmp_path / "baton.db")
        # Drop the teams table to simulate a pre-v15 DB.
        conn = registry._conn()
        conn.execute("DROP TABLE IF EXISTS teams")
        conn.commit()
        team = registry.create_team(
            task_id="t1", team_id="team-1.1", step_id="1.1",
            leader_agent="architect", leader_member_id="1.1.a",
        )
        assert team is None


class TestTeamLookup:
    def test_get_team_returns_existing(
        self, registry: TeamRegistry, tmp_path: Path
    ) -> None:
        _seed_execution(tmp_path / "baton.db", "t1")
        registry.create_team(
            task_id="t1", team_id="team-1.1", step_id="1.1",
            leader_agent="architect", leader_member_id="1.1.a",
        )
        team = registry.get_team("t1", "team-1.1")
        assert team is not None
        assert team.leader_agent == "architect"

    def test_get_team_returns_none_when_absent(
        self, registry: TeamRegistry, tmp_path: Path
    ) -> None:
        _seed_execution(tmp_path / "baton.db", "t1")
        assert registry.get_team("t1", "team-missing") is None


class TestMultipleTeamsPerLeader:
    def test_leader_can_head_multiple_concurrent_teams(
        self, registry: TeamRegistry, tmp_path: Path
    ) -> None:
        """The whole point of the registry: no UNIQUE on leader_agent."""
        _seed_execution(tmp_path / "baton.db", "t1")
        a = registry.create_team(
            task_id="t1", team_id="team-billing", step_id="1.1",
            leader_agent="architect", leader_member_id="1.1.a",
        )
        b = registry.create_team(
            task_id="t1", team_id="team-search", step_id="1.2",
            leader_agent="architect", leader_member_id="1.2.a",
        )
        assert a is not None and b is not None
        teams = registry.list_teams("t1", leader_agent="architect")
        assert len(teams) == 2
        assert {t.team_id for t in teams} == {"team-billing", "team-search"}


class TestNestedTeams:
    def test_child_team_references_parent(
        self, registry: TeamRegistry, tmp_path: Path
    ) -> None:
        _seed_execution(tmp_path / "baton.db", "t1")
        registry.create_team(
            task_id="t1", team_id="team-parent", step_id="1.1",
            leader_agent="architect", leader_member_id="1.1.a",
        )
        registry.create_team(
            task_id="t1", team_id="team-child", step_id="1.1.a",
            leader_agent="backend-engineer", leader_member_id="1.1.a.b",
            parent_team_id="team-parent",
        )
        children = registry.child_teams("t1", "team-parent")
        assert len(children) == 1
        assert children[0].team_id == "team-child"

    def test_has_child_team(
        self, registry: TeamRegistry, tmp_path: Path
    ) -> None:
        _seed_execution(tmp_path / "baton.db", "t1")
        registry.create_team(
            task_id="t1", team_id="team-parent", step_id="1.1",
            leader_agent="architect", leader_member_id="1.1.a",
        )
        assert registry.has_child_team("t1", "team-parent") is False
        registry.create_team(
            task_id="t1", team_id="team-child", step_id="1.1.a",
            leader_agent="backend-engineer", leader_member_id="1.1.a.b",
            parent_team_id="team-parent",
        )
        assert registry.has_child_team("t1", "team-parent") is True


class TestTeamStatus:
    def test_set_status_transitions(
        self, registry: TeamRegistry, tmp_path: Path
    ) -> None:
        _seed_execution(tmp_path / "baton.db", "t1")
        registry.create_team(
            task_id="t1", team_id="team-1.1", step_id="1.1",
            leader_agent="architect", leader_member_id="1.1.a",
        )
        registry.set_status("t1", "team-1.1", "complete")
        team = registry.get_team("t1", "team-1.1")
        assert team is not None
        assert team.status == "complete"


class TestSetStatusIfConcurrency:
    """Direct registry-level compare-and-swap coverage (§6.3 of the
    runtime-contract doc). The synthesis state machine relies on this for
    "two concurrent synthesis drivers can't both win a transition."
    """

    def test_matching_expected_status_transitions_and_returns_true(
        self, registry: TeamRegistry, tmp_path: Path,
    ) -> None:
        _seed_execution(tmp_path / "baton.db", "t1")
        registry.create_team(
            task_id="t1", team_id="team-1.1", step_id="1.1",
            leader_agent="architect", leader_member_id="1.1.a",
        )
        assert registry.set_status_if(
            "t1", "team-1.1", expected_status="active", status="complete",
        ) is True
        assert registry.get_team("t1", "team-1.1").status == "complete"

    def test_mismatched_expected_status_is_noop_returns_false(
        self, registry: TeamRegistry, tmp_path: Path,
    ) -> None:
        _seed_execution(tmp_path / "baton.db", "t1")
        registry.create_team(
            task_id="t1", team_id="team-1.1", step_id="1.1",
            leader_agent="architect", leader_member_id="1.1.a",
        )
        assert registry.set_status_if(
            "t1", "team-1.1", expected_status="complete", status="failed",
        ) is False
        assert registry.get_team("t1", "team-1.1").status == "active"

    def test_two_racing_transitions_only_the_first_wins(
        self, registry: TeamRegistry, tmp_path: Path,
    ) -> None:
        """Simulates two concurrent synthesis drivers both trying to move
        the SAME team from 'active' to 'complete' — exactly one call must
        report success; the second sees the already-flipped status and
        no-ops rather than double-applying the transition."""
        _seed_execution(tmp_path / "baton.db", "t1")
        registry.create_team(
            task_id="t1", team_id="team-1.1", step_id="1.1",
            leader_agent="architect", leader_member_id="1.1.a",
        )
        first = registry.set_status_if(
            "t1", "team-1.1", expected_status="active", status="complete",
        )
        second = registry.set_status_if(
            "t1", "team-1.1", expected_status="active", status="complete",
        )
        assert first is True
        assert second is False
        assert registry.get_team("t1", "team-1.1").status == "complete"

    def test_missing_team_row_returns_false(
        self, registry: TeamRegistry, tmp_path: Path,
    ) -> None:
        _seed_execution(tmp_path / "baton.db", "t1")
        assert registry.set_status_if(
            "t1", "team-missing", expected_status="active", status="complete",
        ) is False


class TestRestartPersistence:
    """A brand-new TeamRegistry instance against the SAME db path must see
    everything a prior instance wrote — the durability guarantee a real
    process restart (or a separate `baton team` CLI invocation) relies on.
    """

    def test_team_and_status_survive_new_registry_instance(
        self, tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "baton.db"
        _seed_execution(db_path, "t1")
        reg1 = TeamRegistry(db_path)
        reg1.create_team(
            task_id="t1", team_id="team-1.1", step_id="1.1",
            leader_agent="architect", leader_member_id="1.1.a",
        )
        reg1.set_status_if(
            "t1", "team-1.1", expected_status="active", status="complete",
        )

        # Simulate a restart: a fresh TeamRegistry object, same db file.
        reg2 = TeamRegistry(db_path)
        team = reg2.get_team("t1", "team-1.1")
        assert team is not None
        assert team.status == "complete"

    def test_nested_child_team_survives_new_registry_instance(
        self, tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "baton.db"
        _seed_execution(db_path, "t1")
        reg1 = TeamRegistry(db_path)
        reg1.create_team(
            task_id="t1", team_id="team-parent", step_id="1.1",
            leader_agent="architect", leader_member_id="1.1.a",
        )
        reg1.create_team(
            task_id="t1", team_id="team-child", step_id="1.1.a",
            leader_agent="backend-engineer", leader_member_id="1.1.a.b",
            parent_team_id="team-parent",
        )

        reg2 = TeamRegistry(db_path)
        children = reg2.child_teams("t1", "team-parent")
        assert [c.team_id for c in children] == ["team-child"]


class TestGrandchildNesting:
    """Multi-level nesting: child_teams() returns only the IMMEDIATE
    children of the id it's given, one level at a time — a grandchild is
    reachable only by walking child_teams() again on the child's own id,
    never surfaced directly under the grandparent."""

    def test_child_teams_returns_only_one_level(
        self, registry: TeamRegistry, tmp_path: Path,
    ) -> None:
        _seed_execution(tmp_path / "baton.db", "t1")
        registry.create_team(
            task_id="t1", team_id="team-grandparent", step_id="1.1",
            leader_agent="architect", leader_member_id="1.1.a",
        )
        registry.create_team(
            task_id="t1", team_id="team-parent", step_id="1.1.a",
            leader_agent="backend-engineer", leader_member_id="1.1.a.b",
            parent_team_id="team-grandparent",
        )
        registry.create_team(
            task_id="t1", team_id="team-grandchild", step_id="1.1.a.b",
            leader_agent="test-engineer", leader_member_id="1.1.a.b.c",
            parent_team_id="team-parent",
        )

        grandparent_children = registry.child_teams("t1", "team-grandparent")
        assert [c.team_id for c in grandparent_children] == ["team-parent"]

        parent_children = registry.child_teams("t1", "team-parent")
        assert [c.team_id for c in parent_children] == ["team-grandchild"]

        # The grandchild never appears directly under the grandparent.
        assert "team-grandchild" not in {c.team_id for c in grandparent_children}


class TestTeamSerialization:
    def test_to_dict_from_dict_roundtrip(self) -> None:
        """Dataclass serializer symmetry."""
        original = Team(
            team_id="team-1.1",
            task_id="t1",
            step_id="1.1",
            leader_agent="architect",
            leader_member_id="1.1.a",
            parent_team_id="team-parent",
            status="active",
        )
        restored = Team.from_dict(original.to_dict())
        assert restored.team_id == original.team_id
        assert restored.task_id == original.task_id
        assert restored.step_id == original.step_id
        assert restored.leader_agent == original.leader_agent
        assert restored.leader_member_id == original.leader_member_id
        assert restored.parent_team_id == original.parent_team_id
        assert restored.status == original.status
        assert restored.created_at == original.created_at
