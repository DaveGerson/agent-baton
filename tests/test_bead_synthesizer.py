"""Tests for agent_baton.core.intel.bead_synthesizer.BeadSynthesizer.

Coverage:
- schema migration: v28 creates bead_edges + bead_clusters tables
- file overlap edges are inferred between beads with shared affected_files
- no edge when there is no overlap
- synthesize is idempotent (re-running does not double the edges)
- connected components over file_overlap edges become clusters
- conflict detection flags two warnings about the same primary tag with
  divergent content
- empty database (no beads) is handled without errors
- baton beads synthesize CLI prints non-zero counts when wired up
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import agent_baton  # noqa: E402  (need its path before pytest fixture defs)

# Path to *this* worktree's package — used to force subprocess imports
# to resolve here even when the editable install points elsewhere.
_PKG_PARENT = str(Path(agent_baton.__file__).resolve().parent.parent)

import pytest

from agent_baton.core.engine.bead_store import BeadStore
from agent_baton.core.intel.bead_synthesizer import (
    SynthesisResult,
    BeadSynthesizer,
)
from agent_baton.core.storage.schema import SCHEMA_VERSION
from agent_baton.models.bead import Bead


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_TASK = "task-synth"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_execution(db_path: Path, task_id: str = _TASK) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT OR IGNORE INTO executions "
            "(task_id, status, current_phase, current_step_index, started_at, "
            " created_at, updated_at) "
            "VALUES (?, 'running', 0, 0, '2026-01-01T00:00:00Z', "
            "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (task_id,),
        )
        conn.commit()
    finally:
        conn.close()


def _make_bead(
    bead_id: str,
    *,
    bead_type: str = "discovery",
    content: str = "Some content",
    tags: list[str] | None = None,
    files: list[str] | None = None,
) -> Bead:
    return Bead(
        bead_id=bead_id,
        task_id=_TASK,
        step_id="1.1",
        agent_name="test-agent",
        bead_type=bead_type,
        content=content,
        tags=tags or [],
        affected_files=files or [],
        created_at=_utcnow(),
    )


@pytest.fixture
def store(tmp_path: Path) -> BeadStore:
    """Fresh BeadStore at schema v28 with a seeded execution row."""
    db_path = tmp_path / "baton.db"
    s = BeadStore(db_path)
    s._table_exists()  # forces schema apply
    _seed_execution(db_path, _TASK)
    return s


@pytest.fixture
def synth() -> BeadSynthesizer:
    return BeadSynthesizer()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestMigration:
    def test_migration_creates_tables(self, store: BeadStore) -> None:
        assert SCHEMA_VERSION >= 28
        conn = store._conn()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('bead_edges', 'bead_clusters')"
        ).fetchall()
        names = {r[0] for r in rows}
        assert names == {"bead_edges", "bead_clusters"}

    def test_bead_edges_columns(self, store: BeadStore) -> None:
        conn = store._conn()
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(bead_edges)"
        ).fetchall()}
        assert {"src_bead_id", "dst_bead_id", "edge_type",
                "weight", "created_at"} <= cols


# ---------------------------------------------------------------------------
# Edge inference
# ---------------------------------------------------------------------------


class TestEdgeInference:
    def test_file_overlap_edge_inferred(
        self, store: BeadStore, synth: BeadSynthesizer
    ) -> None:
        store.write(_make_bead("bd-a001", files=["src/auth.py", "src/util.py"]))
        store.write(_make_bead("bd-a002", files=["src/auth.py", "src/db.py"]))

        result = synth.synthesize(store._conn())
        assert result.pairs_examined == 1
        assert result.edges_added >= 1

        rows = store._conn().execute(
            "SELECT src_bead_id, dst_bead_id, edge_type, weight "
            "FROM bead_edges WHERE edge_type='file_overlap'"
        ).fetchall()
        assert len(rows) == 1
        src, dst, etype, weight = rows[0]
        # Endpoints stored in lex order.
        assert src == "bd-a001"
        assert dst == "bd-a002"
        # jaccard({auth, util}, {auth, db}) = 1/3.
        assert weight == pytest.approx(1.0 / 3.0, rel=1e-3)

    def test_no_edge_when_no_overlap(
        self, store: BeadStore, synth: BeadSynthesizer
    ) -> None:
        store.write(_make_bead("bd-b001", files=["src/auth.py"], tags=["auth"]))
        store.write(_make_bead("bd-b002", files=["docs/intro.md"], tags=["docs"]))

        result = synth.synthesize(store._conn())
        # One pair examined; zero edges (no file or tag overlap).
        assert result.pairs_examined == 1
        rows = store._conn().execute(
            "SELECT COUNT(*) FROM bead_edges"
        ).fetchone()
        assert rows[0] == 0

    def test_idempotent_synthesize(
        self, store: BeadStore, synth: BeadSynthesizer
    ) -> None:
        store.write(_make_bead("bd-c001", files=["a.py", "b.py"], tags=["x"]))
        store.write(_make_bead("bd-c002", files=["a.py"], tags=["x"]))

        synth.synthesize(store._conn())
        first = store._conn().execute(
            "SELECT COUNT(*) FROM bead_edges"
        ).fetchone()[0]
        assert first >= 1

        # Second pass — must not duplicate.
        synth.synthesize(store._conn())
        second = store._conn().execute(
            "SELECT COUNT(*) FROM bead_edges"
        ).fetchone()[0]
        assert first == second

    def test_tag_overlap_edge_inferred(
        self, store: BeadStore, synth: BeadSynthesizer
    ) -> None:
        store.write(_make_bead("bd-d001", tags=["security", "auth"]))
        store.write(_make_bead("bd-d002", tags=["security", "rbac"]))

        synth.synthesize(store._conn())
        rows = store._conn().execute(
            "SELECT edge_type, weight FROM bead_edges "
            "WHERE edge_type='tag_overlap'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][1] == pytest.approx(1.0 / 3.0, rel=1e-3)


# ---------------------------------------------------------------------------
# Cluster discovery
# ---------------------------------------------------------------------------


class TestClusters:
    def test_cluster_from_connected_component(
        self, store: BeadStore, synth: BeadSynthesizer
    ) -> None:
        # Three beads form a connected component via heavy file overlap.
        store.write(_make_bead(
            "bd-e001", files=["src/auth.py", "src/util.py"], tags=["auth"]
        ))
        store.write(_make_bead(
            "bd-e002", files=["src/auth.py", "src/util.py"], tags=["auth"]
        ))
        store.write(_make_bead(
            "bd-e003", files=["src/auth.py"], tags=["auth"]
        ))
        # An isolated bead with no overlap → not in any cluster.
        store.write(_make_bead("bd-e004", files=["docs/x.md"], tags=["docs"]))

        result = synth.synthesize(store._conn())
        assert result.clusters_created >= 1

        rows = store._conn().execute(
            "SELECT cluster_id, label, bead_ids FROM bead_clusters"
        ).fetchall()
        assert len(rows) >= 1
        members = json.loads(rows[0][2])
        assert "bd-e001" in members and "bd-e002" in members
        # The isolated bead must not appear.
        assert "bd-e004" not in members
        # Label should fall back to the shared tag "auth".
        assert rows[0][1] == "auth"

    def test_cluster_idempotent(
        self, store: BeadStore, synth: BeadSynthesizer
    ) -> None:
        store.write(_make_bead("bd-f001", files=["a.py"], tags=["t"]))
        store.write(_make_bead("bd-f002", files=["a.py"], tags=["t"]))
        synth.synthesize(store._conn())
        first = store._conn().execute(
            "SELECT COUNT(*) FROM bead_clusters"
        ).fetchone()[0]
        synth.synthesize(store._conn())
        second = store._conn().execute(
            "SELECT COUNT(*) FROM bead_clusters"
        ).fetchone()[0]
        assert first == second


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------


class TestConflictDetection:
    def test_conflict_detection_same_tag_different_titles(
        self, store: BeadStore, synth: BeadSynthesizer
    ) -> None:
        store.write(_make_bead(
            "bd-g001",
            bead_type="warning",
            tags=["auth"],
            content="Token expiry should be 15 minutes",
        ))
        store.write(_make_bead(
            "bd-g002",
            bead_type="warning",
            tags=["auth"],
            content="Refresh policy must use sliding window of 30 days",
        ))
        result = synth.synthesize(store._conn())
        assert result.conflicts_flagged >= 1
        rows = store._conn().execute(
            "SELECT edge_type FROM bead_edges WHERE edge_type='conflict'"
        ).fetchall()
        assert len(rows) >= 1

    def test_no_conflict_when_one_is_not_warning(
        self, store: BeadStore, synth: BeadSynthesizer
    ) -> None:
        store.write(_make_bead(
            "bd-h001",
            bead_type="warning",
            tags=["auth"],
            content="Token expiry should be 15 minutes",
        ))
        store.write(_make_bead(
            "bd-h002",
            bead_type="discovery",
            tags=["auth"],
            content="Refresh policy must use sliding window",
        ))
        result = synth.synthesize(store._conn())
        assert result.conflicts_flagged == 0

    def test_no_conflict_when_titles_overlap(
        self, store: BeadStore, synth: BeadSynthesizer
    ) -> None:
        # High token overlap — they are saying the same thing.
        store.write(_make_bead(
            "bd-i001",
            bead_type="warning",
            tags=["auth"],
            content="Token expiry should be enforced rigorously always now",
        ))
        store.write(_make_bead(
            "bd-i002",
            bead_type="warning",
            tags=["auth"],
            content="Token expiry should be enforced rigorously always today",
        ))
        result = synth.synthesize(store._conn())
        assert result.conflicts_flagged == 0


# ---------------------------------------------------------------------------
# Empty / edge cases
# ---------------------------------------------------------------------------


class TestEmptyDatabase:
    def test_synthesize_handles_empty_db(
        self, store: BeadStore, synth: BeadSynthesizer
    ) -> None:
        # No beads at all.
        result = synth.synthesize(store._conn())
        assert isinstance(result, SynthesisResult)
        assert result.edges_added == 0
        assert result.clusters_created == 0
        assert result.conflicts_flagged == 0
        assert result.errors == []

    def test_synthesize_handles_single_bead(
        self, store: BeadStore, synth: BeadSynthesizer
    ) -> None:
        store.write(_make_bead("bd-j001", files=["a.py"]))
        result = synth.synthesize(store._conn())
        assert result.pairs_examined == 0
        assert result.edges_added == 0
        assert result.clusters_created == 0

    def test_synthesize_handles_none_connection(
        self, synth: BeadSynthesizer
    ) -> None:
        result = synth.synthesize(None)
        assert result.errors  # populated, but does not raise


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCli:
    def test_cli_synthesize_reports_counts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Lay out the project skeleton expected by bead_cmd.py
        # (.claude/team-context/baton.db relative to CWD).
        project = tmp_path / "proj"
        team_ctx = project / ".claude" / "team-context"
        team_ctx.mkdir(parents=True)
        db_path = team_ctx / "baton.db"

        store = BeadStore(db_path)
        store._table_exists()
        _seed_execution(db_path, _TASK)
        store.write(_make_bead("bd-k001", files=["a.py", "b.py"], tags=["t"]))
        store.write(_make_bead("bd-k002", files=["a.py"], tags=["t"]))

        monkeypatch.chdir(project)

        # Invoke CLI as a subprocess so we exercise argparse + handler wiring.
        # Force PYTHONPATH to this worktree so the subprocess uses *this*
        # package even when an editable install points elsewhere.
        env = dict(os.environ)
        env["PYTHONPATH"] = (
            _PKG_PARENT + os.pathsep + env.get("PYTHONPATH", "")
        )
        result = subprocess.run(
            [sys.executable, "-m", "agent_baton.cli.main", "beads",
             "synthesize", "--json"],
            capture_output=True,
            text=True,
            cwd=str(project),
            env=env,
        )
        assert result.returncode == 0, (
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        )
        # The JSON line should be in stdout.
        # Find the first { ... } block.
        start = result.stdout.find("{")
        end = result.stdout.rfind("}")
        assert start != -1 and end != -1
        payload = json.loads(result.stdout[start:end + 1])
        assert payload["edges_added"] >= 1
        assert payload["pairs_examined"] == 1
