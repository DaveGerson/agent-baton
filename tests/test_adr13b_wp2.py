"""ADR-13b WP-2 migration tests.

Covers:

CLI layer (bead_cmd.py)
- clusters reads from DerivedBeadStore (baton-derived.db)
- handoffs reads from DerivedBeadStore (baton-derived.db)
- graph edge query reads from DerivedBeadStore (baton-derived.db)
- synthesize uses synthesize_beads() entry point (writes to derived DB)
- create-exec imports compute_script_sha from core.exec.script_hash (not NotesAdapter)
- _get_bead_store / _get_or_create_bead_store delegate to make_bead_store

API layer (pmo_h3.py)
- list_beads + list_arch_beads use make_bead_store (field parity test)
- BeadResponse field parity under BATON_BD_BACKEND=bd (skipped when bd absent)

Sync layer (sync.py)
- beads and bead_tags are NOT in SYNCABLE_TABLES

Central layer (central.py)
- export_beads_to_central upserts minimal projection into central.db
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

_BD_AVAILABLE = shutil.which("bd") is not None
_FASTAPI_AVAILABLE = shutil.which("python") is not None  # always true; actual check below
try:
    import fastapi as _fastapi_mod  # noqa: F401

    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_project_db(db_path: Path, task_id: str = "task-001") -> None:
    """Create a minimal baton.db with schema applied and a seed execution row."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION

    conn.executescript(PROJECT_SCHEMA_DDL)
    count = conn.execute("SELECT COUNT(*) FROM _schema_version").fetchone()[0]
    if count == 0:
        conn.execute("INSERT INTO _schema_version VALUES (?)", (SCHEMA_VERSION,))
    conn.execute(
        "INSERT OR IGNORE INTO executions "
        "(task_id, status, current_phase, current_step_index, started_at, "
        " created_at, updated_at) "
        "VALUES (?, 'running', 0, 0, '2026-01-01T00:00:00Z', "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (task_id,),
    )
    conn.commit()
    conn.close()


def _make_bead(bead_id: str = "bd-wp2a", task_id: str = "task-001", **kwargs):
    from agent_baton.models.bead import Bead

    defaults = dict(
        bead_id=bead_id,
        task_id=task_id,
        step_id="step-1",
        agent_name="backend-engineer",
        bead_type="discovery",
        content="WP-2 test bead",
        confidence="high",
        scope="task",
        tags=["auth", "infra"],
        affected_files=["app.py"],
        status="open",
        created_at=_utcnow(),
        source="agent-signal",
    )
    defaults.update(kwargs)
    return Bead(**defaults)


# ---------------------------------------------------------------------------
# CLI layer — derived store reads
# ---------------------------------------------------------------------------


class TestCliDerivedStoreReads:
    """baton beads clusters/handoffs/graph read from DerivedBeadStore."""

    @pytest.fixture()
    def project(self, tmp_path: Path):
        """Return (db_path, derived_path) with a project DB."""
        tc = tmp_path / ".claude" / "team-context"
        tc.mkdir(parents=True)
        db = tc / "baton.db"
        _build_project_db(db)
        derived = tc / "baton-derived.db"
        return db, derived

    def _run(self, db_path: Path, argv: list[str]) -> tuple[int, str]:
        """Drive bead_cmd.handler with a monkeypatched _DEFAULT_DB_PATH."""
        import argparse
        import io
        import sys
        from unittest.mock import patch

        from agent_baton.cli.commands import bead_cmd

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        bead_cmd.register(sub)
        args = parser.parse_args(["beads"] + argv)

        captured = io.StringIO()
        exit_code = 0
        with patch("agent_baton.cli.commands.bead_cmd._DEFAULT_DB_PATH", db_path):
            old_stdout = sys.stdout
            sys.stdout = captured
            try:
                bead_cmd.handler(args)
            except SystemExit as exc:
                exit_code = int(exc.code) if exc.code is not None else 0
            finally:
                sys.stdout = old_stdout

        return exit_code, captured.getvalue()

    def test_clusters_empty_derived_db_returns_gracefully(self, project):
        db, _derived = project
        # No derived DB exists — should not crash, just print "no DB" message.
        code, out = self._run(db, ["clusters"])
        assert code == 0
        assert "synthesize" in out.lower() or out == ""

    def test_clusters_reads_from_derived_db(self, project):
        db, derived = project
        from agent_baton.core.storage.derived_bead_store import DerivedBeadStore

        store = DerivedBeadStore(derived)
        with store.connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO bead_clusters "
                "(cluster_id, label, bead_ids, created_at) "
                "VALUES (?, ?, ?, ?)",
                ("bc-test", "auth cluster", json.dumps(["bd-1", "bd-2"]), _utcnow()),
            )

        code, out = self._run(db, ["clusters", "--json"])
        assert code == 0
        clusters = json.loads(out)
        assert isinstance(clusters, list)
        assert len(clusters) == 1
        assert clusters[0]["cluster_id"] == "bc-test"
        assert clusters[0]["label"] == "auth cluster"
        assert clusters[0]["bead_ids"] == ["bd-1", "bd-2"]

    def test_handoffs_empty_derived_db_returns_gracefully(self, project, monkeypatch):
        db, _derived = project
        monkeypatch.setenv("BATON_TASK_ID", "task-001")
        code, out = self._run(db, ["handoffs", "--json"])
        assert code == 0
        data = json.loads(out)
        assert data == []

    def test_handoffs_reads_from_derived_db(self, project, monkeypatch):
        db, derived = project
        from agent_baton.core.storage.derived_bead_store import DerivedBeadStore

        store = DerivedBeadStore(derived)
        with store.connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO handoff_beads "
                "(handoff_id, task_id, from_step_id, to_step_id, content, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "hnd-test",
                    "task-001",
                    "step-1",
                    "step-2",
                    "context for next agent",
                    _utcnow(),
                ),
            )

        monkeypatch.setenv("BATON_TASK_ID", "task-001")
        code, out = self._run(db, ["handoffs", "--json"])
        assert code == 0
        handoffs = json.loads(out)
        assert len(handoffs) == 1
        assert handoffs[0]["handoff_id"] == "hnd-test"
        assert handoffs[0]["from_step_id"] == "step-1"
        assert handoffs[0]["to_step_id"] == "step-2"

    def test_graph_edges_read_from_derived_db(self, project):
        """_query_bead_edges_for should pull from baton-derived.db."""
        db, derived = project
        from agent_baton.core.storage.derived_bead_store import DerivedBeadStore

        store = DerivedBeadStore(derived)
        with store.connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO bead_edges "
                "(src_bead_id, dst_bead_id, edge_type, weight, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("bd-001", "bd-002", "file_overlap", 0.75, _utcnow()),
            )

        from agent_baton.cli.commands.bead_cmd import _query_bead_edges_for
        from unittest.mock import patch

        with patch("agent_baton.cli.commands.bead_cmd._DEFAULT_DB_PATH", db):
            edges = _query_bead_edges_for({"bd-001", "bd-002"})

        assert len(edges) == 1
        src, dst, etype, weight = edges[0]
        assert {src, dst} == {"bd-001", "bd-002"}
        assert etype == "file_overlap"
        assert abs(weight - 0.75) < 0.01

    def test_graph_edges_no_derived_db_returns_empty(self, project):
        db, _derived = project
        from agent_baton.cli.commands.bead_cmd import _query_bead_edges_for
        from unittest.mock import patch

        with patch("agent_baton.cli.commands.bead_cmd._DEFAULT_DB_PATH", db):
            edges = _query_bead_edges_for({"bd-001", "bd-002"})

        assert edges == []

    def test_synthesize_writes_to_derived_db(self, project):
        """baton beads synthesize uses synthesize_beads() and creates baton-derived.db."""
        db, derived = project
        # Seed two beads with shared files so edges will be created.
        from agent_baton.core.engine.bead_store import BeadStore

        store = BeadStore(db)
        store.write(
            _make_bead("bd-a1", affected_files=["shared.py", "auth.py"])
        )
        store.write(
            _make_bead("bd-a2-x", affected_files=["shared.py"])
        )

        code, out = self._run(db, ["synthesize"])
        assert code == 0
        assert "BeadSynthesizer completed" in out
        # The derived DB must now exist.
        assert derived.exists()

    def test_synthesize_json_output(self, project):
        db, _derived = project
        code, out = self._run(db, ["synthesize", "--json"])
        assert code == 0
        data = json.loads(out)
        assert "pairs_examined" in data
        assert "edges_added" in data
        assert "clusters_created" in data


# ---------------------------------------------------------------------------
# CLI layer — make_bead_store delegation
# ---------------------------------------------------------------------------


def test_get_bead_store_returns_none_when_no_db(tmp_path, monkeypatch):
    """_get_bead_store returns None when baton.db does not exist."""
    from agent_baton.cli.commands import bead_cmd

    monkeypatch.setattr(bead_cmd, "_DEFAULT_DB_PATH", tmp_path / "no-db.db")
    store = bead_cmd._get_bead_store()
    assert store is None


def test_get_bead_store_returns_sqlite_by_default(tmp_path, monkeypatch):
    """_get_bead_store returns a BeadStore (sqlite) under default backend."""
    from agent_baton.cli.commands import bead_cmd
    from agent_baton.core.engine.bead_store import BeadStore

    db = tmp_path / "baton.db"
    _build_project_db(db)
    monkeypatch.setattr(bead_cmd, "_DEFAULT_DB_PATH", db)
    monkeypatch.delenv("BATON_BD_BACKEND", raising=False)

    store = bead_cmd._get_bead_store()
    assert isinstance(store, BeadStore)


def test_get_or_create_bead_store_creates_dir(tmp_path, monkeypatch):
    """_get_or_create_bead_store creates the parent directory if needed."""
    from agent_baton.cli.commands import bead_cmd

    db = tmp_path / "new" / "nested" / "baton.db"
    monkeypatch.setattr(bead_cmd, "_DEFAULT_DB_PATH", db)
    monkeypatch.delenv("BATON_BD_BACKEND", raising=False)

    store = bead_cmd._get_or_create_bead_store()
    assert db.parent.exists()
    assert store is not None


# ---------------------------------------------------------------------------
# CLI layer — create-exec uses script_hash canonical import
# ---------------------------------------------------------------------------


def test_handle_create_exec_imports_script_hash(tmp_path, monkeypatch):
    """_handle_create_exec must import compute_script_sha from core.exec.script_hash."""
    import ast
    import inspect

    from agent_baton.cli.commands import bead_cmd

    source = inspect.getsource(bead_cmd._handle_create_exec)
    # Parse to an AST to find the import
    tree = ast.parse(source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imports.append(f"{node.module}:{[n.name for n in node.names]}")
    # Must import from core.exec.script_hash, not from notes_adapter.
    assert any(
        "script_hash" in imp and "compute_script_sha" in imp for imp in imports
    ), f"Expected import from script_hash; found: {imports}"
    assert not any(
        "notes_adapter" in imp and "compute_script_sha" in imp for imp in imports
    ), "Should not import compute_script_sha from notes_adapter"


# ---------------------------------------------------------------------------
# API layer — field parity (sqlite default)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _FASTAPI_AVAILABLE, reason="fastapi not installed")
class TestPMOBeadResponseFieldParity:
    """BeadResponse field parity tests."""

    @pytest.fixture()
    def populated_client(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from fastapi.testclient import TestClient

        monkeypatch.chdir(tmp_path)
        db_dir = tmp_path / ".claude" / "team-context"
        db_dir.mkdir(parents=True)
        db_path = db_dir / "baton.db"

        from agent_baton.core.engine.bead_store import BeadStore
        from agent_baton.models.bead import Bead, BeadLink

        store = BeadStore(db_path)
        store._table_exists()

        # Insert a parent execution row.
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT OR IGNORE INTO executions "
            "(task_id, status, current_phase, current_step_index, started_at, "
            " created_at, updated_at) "
            "VALUES ('wp2-task', 'running', 0, 0, '2026-01-01T00:00:00Z', "
            "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )
        conn.commit()
        conn.close()

        store.write(
            Bead(
                bead_id="bd-wp2-parity",
                task_id="wp2-task",
                step_id="step-1",
                agent_name="backend-engineer",
                bead_type="decision",
                content="Use JWT auth",
                confidence="high",
                scope="task",
                tags=["auth", "security"],
                affected_files=["auth.py"],
                status="open",
                created_at="2026-06-01T10:00:00Z",
                source="agent-signal",
                token_estimate=150,
                links=[BeadLink(target_bead_id="bd-other", link_type="relates_to")],
            )
        )

        from agent_baton.api.server import create_app

        app = create_app(team_context_root=db_dir)
        return TestClient(app)

    _REQUIRED_FIELDS = [
        "bead_id",
        "task_id",
        "step_id",
        "agent_name",
        "bead_type",
        "content",
        "confidence",
        "scope",
        "tags",
        "affected_files",
        "status",
        "created_at",
        "closed_at",
        "summary",
        "links",
        "source",
        "token_estimate",
        "quality_score",
        "retrieval_count",
    ]

    def test_bead_response_field_parity_sqlite(self, populated_client):
        """All required BeadResponse fields must be present under sqlite backend."""
        res = populated_client.get("/api/v1/pmo/beads", params={"status": "all"})
        assert res.status_code == 200
        data = res.json()
        assert data["total"] >= 1
        bead = next(b for b in data["beads"] if b["bead_id"] == "bd-wp2-parity")
        for field in self._REQUIRED_FIELDS:
            assert field in bead, f"BeadResponse missing field: {field}"
        assert bead["bead_type"] == "decision"
        assert "auth" in bead["tags"]
        assert bead["affected_files"] == ["auth.py"]
        assert bead["confidence"] == "high"
        assert len(bead["links"]) == 1
        assert bead["links"][0]["target_bead_id"] == "bd-other"
        assert bead["links"][0]["link_type"] == "relates_to"

    def test_arch_beads_endpoint_returns_decision_type(self, populated_client):
        """list_arch_beads must return 'decision' type beads via make_bead_store."""
        res = populated_client.get("/api/v1/pmo/arch-beads")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        bead = data[0]
        assert bead["bead_type"] in ("architecture", "decision")
        # All ArchBeadResponse fields must be present.
        for field in ("bead_id", "bead_type", "agent_name", "content",
                      "affected_files", "status", "created_at", "tags"):
            assert field in bead, f"ArchBeadResponse missing field: {field}"

    def test_list_beads_degrades_gracefully_when_no_db(self, tmp_path, monkeypatch):
        """list_beads must return empty envelope when baton.db is absent."""
        from fastapi.testclient import TestClient

        monkeypatch.chdir(tmp_path)
        from agent_baton.api.server import create_app

        tc = tmp_path / ".claude" / "team-context"
        app = create_app(team_context_root=tc)
        client = TestClient(app)
        res = client.get("/api/v1/pmo/beads")
        assert res.status_code == 200
        assert res.json() == {"beads": [], "total": 0}


# ---------------------------------------------------------------------------
# API layer — field parity under BATON_BD_BACKEND=bd
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _BD_AVAILABLE or not _FASTAPI_AVAILABLE,
    reason="bd binary or fastapi not installed",
)
class TestPMOBeadResponseFieldParityBd:
    """BeadResponse field parity under the bd backend (requires bd binary + fastapi)."""

    def _bd_client_and_store(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("BATON_BD_BACKEND", "bd")
        monkeypatch.setenv("BATON_BD_PREFIX", "bd")
        from agent_baton.core.engine.bead_backend import make_bead_store

        db_path = tmp_path / ".claude" / "team-context" / "baton.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        store = make_bead_store(db_path, repo_root=tmp_path)
        return store

    def test_bead_response_field_parity_bd_backend(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All required BeadResponse fields must be present under bd backend."""
        from fastapi.testclient import TestClient

        store = self._bd_client_and_store(tmp_path, monkeypatch)
        from agent_baton.models.bead import Bead, BeadLink

        bead = Bead(
            bead_id="bd-parity-bd",
            task_id="task-bd",
            step_id="s1",
            agent_name="architect",
            bead_type="decision",
            content="bd backend parity test bead",
            confidence="high",
            scope="task",
            tags=["auth", "bd-backend"],
            affected_files=["auth.py"],
            status="open",
            created_at="2026-06-01T12:00:00Z",
            source="agent-signal",
            token_estimate=200,
        )
        store.write(bead)

        monkeypatch.chdir(tmp_path)
        from agent_baton.api.server import create_app

        tc = tmp_path / ".claude" / "team-context"
        app = create_app(team_context_root=tc)
        client = TestClient(app)

        res = client.get("/api/v1/pmo/beads", params={"status": "all"})
        assert res.status_code == 200
        data = res.json()
        assert data["total"] >= 1

        bead_resp = next(
            (b for b in data["beads"] if b["bead_id"] == "bd-parity-bd"), None
        )
        assert bead_resp is not None, "bd-parity-bd not found in response"

        required = [
            "bead_id", "task_id", "step_id", "agent_name", "bead_type",
            "content", "confidence", "scope", "tags", "affected_files",
            "status", "created_at", "closed_at", "summary", "links",
            "source", "token_estimate", "quality_score", "retrieval_count",
        ]
        for field in required:
            assert field in bead_resp, f"BeadResponse (bd) missing field: {field}"

        assert bead_resp["bead_type"] == "decision"
        assert "auth" in bead_resp["tags"]
        assert bead_resp["confidence"] == "high"
        assert bead_resp["affected_files"] == ["auth.py"]

    def test_arch_beads_field_parity_bd_backend(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """list_arch_beads must return correct fields under bd backend."""
        from fastapi.testclient import TestClient

        store = self._bd_client_and_store(tmp_path, monkeypatch)
        from agent_baton.models.bead import Bead

        bead = Bead(
            bead_id="bd-arch-bd",
            task_id="task-arch",
            step_id="s1",
            agent_name="architect",
            bead_type="decision",
            content="architectural decision via bd",
            confidence="high",
            scope="task",
            tags=["architecture"],
            affected_files=["design.md"],
            status="open",
            created_at="2026-06-01T13:00:00Z",
            source="agent-signal",
        )
        store.write(bead)

        monkeypatch.chdir(tmp_path)
        from agent_baton.api.server import create_app

        tc = tmp_path / ".claude" / "team-context"
        app = create_app(team_context_root=tc)
        client = TestClient(app)

        res = client.get("/api/v1/pmo/arch-beads")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)
        arch = next(
            (b for b in data if b["bead_id"] == "bd-arch-bd"), None
        )
        assert arch is not None, "bd-arch-bd not found in arch-beads response"
        for field in ("bead_id", "bead_type", "agent_name", "content",
                      "affected_files", "status", "created_at", "tags"):
            assert field in arch, f"ArchBeadResponse (bd) missing field: {field}"


# ---------------------------------------------------------------------------
# Sync layer — beads/bead_tags removed from SYNCABLE_TABLES
# ---------------------------------------------------------------------------


def test_syncable_tables_no_beads_or_bead_tags():
    """ADR-13b WP-2: beads and bead_tags must not be in SYNCABLE_TABLES."""
    from agent_baton.core.storage.sync import SYNCABLE_TABLES

    names = {s.name for s in SYNCABLE_TABLES}
    assert "beads" not in names, (
        "beads SyncTableSpec must be removed (ADR-13b WP-2 §5)"
    )
    assert "bead_tags" not in names, (
        "bead_tags SyncTableSpec must be removed (ADR-13b WP-2 §5)"
    )


def test_syncable_tables_still_has_executions():
    """Regression: other tables must not have been accidentally removed."""
    from agent_baton.core.storage.sync import SYNCABLE_TABLES

    names = {s.name for s in SYNCABLE_TABLES}
    for expected in ("executions", "plans", "step_results", "agent_usage"):
        assert expected in names, f"{expected} missing from SYNCABLE_TABLES"


# ---------------------------------------------------------------------------
# Central layer — export_beads_to_central
# ---------------------------------------------------------------------------


class TestExportBeatsToCentral:
    """export_beads_to_central upserts minimal projection into central.db."""

    def _make_central(self, tmp_path: Path) -> Path:
        """Create a minimal central.db at the given location with schema applied."""
        central_dir = tmp_path / "central"
        central_dir.mkdir()
        central_path = central_dir / "central.db"
        from agent_baton.core.storage.central import CentralStore

        # _conn() forces the ConnectionManager to apply the schema DDL.
        store = CentralStore(central_path)
        _ = store._conn()
        return central_path

    def test_export_skips_when_central_missing(self, tmp_path: Path) -> None:
        """Returns 0 without crashing when central.db does not exist."""
        from agent_baton.core.storage.central import export_beads_to_central

        count = export_beads_to_central(
            project_id="proj-1",
            project_root=tmp_path / "no-project",
            central_db_path=tmp_path / "nonexistent-central.db",
        )
        assert count == 0

    def test_export_skips_when_project_has_no_beads(self, tmp_path: Path) -> None:
        """Returns 0 when the bead store is empty."""
        central_path = self._make_central(tmp_path)
        project_root = tmp_path / "empty-project"
        db = project_root / ".claude" / "team-context" / "baton.db"
        _build_project_db(db)

        from agent_baton.core.storage.central import export_beads_to_central

        count = export_beads_to_central(
            project_id="proj-empty",
            project_root=project_root,
            central_db_path=central_path,
        )
        assert count == 0

    def test_export_upserts_minimal_projection(self, tmp_path: Path) -> None:
        """Beads written to the project store appear in central.db after export."""
        central_path = self._make_central(tmp_path)
        project_root = tmp_path / "my-project"
        db = project_root / ".claude" / "team-context" / "baton.db"
        _build_project_db(db)

        from agent_baton.core.engine.bead_store import BeadStore

        store = BeadStore(db)
        store.write(_make_bead("bd-central-1", bead_type="warning", status="open"))
        store.write(_make_bead("bd-central-2", bead_type="decision"))

        from agent_baton.core.storage.central import export_beads_to_central

        count = export_beads_to_central(
            project_id="proj-mine",
            project_root=project_root,
            central_db_path=central_path,
        )
        assert count == 2

        # Verify the rows appear in central.db with the correct project_id.
        conn = sqlite3.connect(str(central_path))
        rows = conn.execute(
            "SELECT bead_id, bead_type, status, agent_name "
            "FROM beads WHERE project_id = ?",
            ("proj-mine",),
        ).fetchall()
        conn.close()

        bead_ids = {r[0] for r in rows}
        assert "bd-central-1" in bead_ids
        assert "bd-central-2" in bead_ids

        warn_row = next(r for r in rows if r[0] == "bd-central-1")
        assert warn_row[1] == "warning"
        assert warn_row[2] == "open"

    def test_export_is_idempotent(self, tmp_path: Path) -> None:
        """Calling export twice doesn't duplicate rows."""
        central_path = self._make_central(tmp_path)
        project_root = tmp_path / "idempotent-project"
        db = project_root / ".claude" / "team-context" / "baton.db"
        _build_project_db(db)

        from agent_baton.core.engine.bead_store import BeadStore
        from agent_baton.core.storage.central import export_beads_to_central

        store = BeadStore(db)
        store.write(_make_bead("bd-idem-1"))

        export_beads_to_central("proj-idem", project_root, central_path)
        export_beads_to_central("proj-idem", project_root, central_path)

        conn = sqlite3.connect(str(central_path))
        count = conn.execute(
            "SELECT COUNT(*) FROM beads WHERE project_id = ?", ("proj-idem",)
        ).fetchone()[0]
        conn.close()
        assert count == 1  # not 2

    def test_export_updates_existing_row(self, tmp_path: Path) -> None:
        """A re-export updates the status in central.db if the bead was closed."""
        central_path = self._make_central(tmp_path)
        project_root = tmp_path / "update-project"
        db = project_root / ".claude" / "team-context" / "baton.db"
        _build_project_db(db)

        from agent_baton.core.engine.bead_store import BeadStore
        from agent_baton.core.storage.central import export_beads_to_central

        store = BeadStore(db)
        store.write(_make_bead("bd-update-1", status="open"))

        export_beads_to_central("proj-update", project_root, central_path)

        # Close the bead and re-export.
        store.close("bd-update-1", summary="done")
        export_beads_to_central("proj-update", project_root, central_path)

        conn = sqlite3.connect(str(central_path))
        row = conn.execute(
            "SELECT status FROM beads WHERE project_id = ? AND bead_id = ?",
            ("proj-update", "bd-update-1"),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "closed"

    def test_noc_incidents_query_works_after_export(self, tmp_path: Path) -> None:
        """NOC aggregate/incidents query returns correct counts from export projection."""
        central_path = self._make_central(tmp_path)
        project_root = tmp_path / "noc-project"
        db = project_root / ".claude" / "team-context" / "baton.db"
        _build_project_db(db)

        from agent_baton.core.engine.bead_store import BeadStore
        from agent_baton.core.storage.central import CentralStore, export_beads_to_central

        store = BeadStore(db)
        store.write(_make_bead("bd-warn-1", bead_type="warning"))
        store.write(_make_bead("bd-warn-2", bead_type="warning"))
        store.write(_make_bead("bd-disc-1", bead_type="discovery"))

        export_beads_to_central("noc-proj", project_root, central_path)

        # The NOC query is a plain SELECT — use CentralStore.query to avoid
        # test coupling to the HTTP layer.
        central = CentralStore(central_path)
        rows = central.query(
            "SELECT project_id, COUNT(*) AS warning_count "
            "FROM beads WHERE bead_type = 'warning' "
            "GROUP BY project_id"
        )
        assert any(r["project_id"] == "noc-proj" and r["warning_count"] == 2 for r in rows)
