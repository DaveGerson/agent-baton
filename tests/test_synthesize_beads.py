"""Tests for the backend-agnostic synthesize_beads() entry point (ADR-13b WP-1 §A).

Coverage:
- synthesize_beads() with a fake store that returns Bead objects (exercises the
  new store-based path independent of BeadStore / BdBeadStore).
- File-overlap edges are inferred and written to the DerivedBeadStore.
- Tag-overlap edges are inferred.
- Conflict edges are flagged.
- Clusters are created from strongly-connected components.
- synthesize_beads() is idempotent (second call does not double edges).
- Empty store (zero beads) returns a zero-count result without errors.
- Store query failure is surfaced via errors field (no raise).
- HandoffSynthesizer can use a DerivedBeadStore as the persistence target.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_baton.core.intel.bead_synthesizer import (
    SynthesisResult,
    synthesize_beads,
)
from agent_baton.core.storage.derived_bead_store import DerivedBeadStore
from agent_baton.models.bead import Bead


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_bead(
    bead_id: str,
    *,
    bead_type: str = "discovery",
    content: str = "Some content",
    tags: list[str] | None = None,
    files: list[str] | None = None,
    status: str = "open",
) -> Bead:
    return Bead(
        bead_id=bead_id,
        task_id="T-synth",
        step_id="1.1",
        agent_name="test-agent",
        bead_type=bead_type,
        content=content,
        tags=tags or [],
        affected_files=files or [],
        status=status,
        created_at=_utcnow(),
    )


class FakeBeadStore:
    """Minimal bead store stub: exposes only query()."""

    def __init__(self, beads: list[Bead]) -> None:
        self._beads = beads
        self.query_failed = False

    def query(self, *, status: str | None = None, limit: int = 100, **_kw) -> list[Bead]:
        if self.query_failed:
            raise RuntimeError("FakeBeadStore: simulated query failure")
        result = self._beads
        if status is not None:
            result = [b for b in result if b.status == status]
        return result[:limit]


@pytest.fixture
def derived(tmp_path: Path) -> DerivedBeadStore:
    return DerivedBeadStore(tmp_path / "baton-derived.db")


# ---------------------------------------------------------------------------
# Edge inference
# ---------------------------------------------------------------------------


class TestEdgeInference:
    def test_file_overlap_edge_written_to_derived(
        self, derived: DerivedBeadStore
    ) -> None:
        store = FakeBeadStore([
            _make_bead("bd-a1", files=["src/auth.py", "src/util.py"]),
            _make_bead("bd-a2", files=["src/auth.py", "src/db.py"]),
        ])
        result = synthesize_beads(store, derived)
        assert result.pairs_examined == 1
        assert result.edges_added >= 1
        edges = derived.edges_for(["bd-a1"])
        assert len(edges) >= 1
        edge = edges[0]
        assert edge["edge_type"] == "file_overlap"
        assert edge["weight"] == pytest.approx(1.0 / 3.0, rel=1e-3)

    def test_no_edge_without_overlap(self, derived: DerivedBeadStore) -> None:
        store = FakeBeadStore([
            _make_bead("bd-b1", files=["src/auth.py"], tags=["auth"]),
            _make_bead("bd-b2", files=["docs/readme.md"], tags=["docs"]),
        ])
        result = synthesize_beads(store, derived)
        assert result.edges_added == 0

    def test_tag_overlap_edge_written(self, derived: DerivedBeadStore) -> None:
        store = FakeBeadStore([
            _make_bead("bd-c1", tags=["security", "auth"]),
            _make_bead("bd-c2", tags=["security", "rbac"]),
        ])
        result = synthesize_beads(store, derived)
        edges = derived.edges_for(["bd-c1", "bd-c2"])
        types = {e["edge_type"] for e in edges}
        assert "tag_overlap" in types

    def test_conflict_edge_written(self, derived: DerivedBeadStore) -> None:
        store = FakeBeadStore([
            _make_bead("bd-d1", bead_type="warning", tags=["auth"],
                       content="Token expiry should be 15 minutes maximum"),
            _make_bead("bd-d2", bead_type="warning", tags=["auth"],
                       content="Refresh policy uses sliding window of 30 days always"),
        ])
        result = synthesize_beads(store, derived)
        assert result.conflicts_flagged >= 1
        edges = derived.edges_for(["bd-d1", "bd-d2"])
        types = {e["edge_type"] for e in edges}
        assert "conflict" in types

    def test_idempotent(self, derived: DerivedBeadStore) -> None:
        store = FakeBeadStore([
            _make_bead("bd-e1", files=["a.py", "b.py"], tags=["x"]),
            _make_bead("bd-e2", files=["a.py"], tags=["x"]),
        ])
        synthesize_beads(store, derived)
        count_after_first = len(derived.edges_for(["bd-e1", "bd-e2"]))
        synthesize_beads(store, derived)
        count_after_second = len(derived.edges_for(["bd-e1", "bd-e2"]))
        assert count_after_first == count_after_second


# ---------------------------------------------------------------------------
# Cluster creation
# ---------------------------------------------------------------------------


class TestClusters:
    def test_cluster_created_for_connected_component(
        self, derived: DerivedBeadStore
    ) -> None:
        store = FakeBeadStore([
            _make_bead("bd-f1", files=["src/auth.py", "src/util.py"], tags=["auth"]),
            _make_bead("bd-f2", files=["src/auth.py", "src/util.py"], tags=["auth"]),
            _make_bead("bd-f3", files=["src/auth.py"], tags=["auth"]),
            _make_bead("bd-f4", files=["docs/x.md"], tags=["docs"]),
        ])
        result = synthesize_beads(store, derived)
        assert result.clusters_created >= 1
        clusters = derived.clusters()
        assert len(clusters) >= 1
        bead_ids_set = set()
        for c in clusters:
            bead_ids_set.update(json.loads(c["bead_ids"]))
        assert "bd-f1" in bead_ids_set
        assert "bd-f2" in bead_ids_set
        assert "bd-f4" not in bead_ids_set


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_store_returns_zero_counts(self, derived: DerivedBeadStore) -> None:
        store = FakeBeadStore([])
        result = synthesize_beads(store, derived)
        assert isinstance(result, SynthesisResult)
        assert result.edges_added == 0
        assert result.clusters_created == 0
        assert result.errors == []

    def test_single_bead_no_pairs(self, derived: DerivedBeadStore) -> None:
        store = FakeBeadStore([_make_bead("bd-g1", files=["a.py"])])
        result = synthesize_beads(store, derived)
        assert result.pairs_examined == 0
        assert result.edges_added == 0

    def test_store_query_failure_returns_error_not_raise(
        self, derived: DerivedBeadStore
    ) -> None:
        store = FakeBeadStore([])
        store.query_failed = True
        result = synthesize_beads(store, derived)
        assert result.errors
        assert any("store_query_failed" in e for e in result.errors)

    def test_closed_beads_excluded(self, derived: DerivedBeadStore) -> None:
        """Closed beads must not contribute to synthesis."""
        store = FakeBeadStore([
            _make_bead("bd-h1", files=["src/auth.py"], status="open"),
            _make_bead("bd-h2", files=["src/auth.py"], status="closed"),
        ])
        result = synthesize_beads(store, derived)
        # Only one open bead → no pairs.
        assert result.pairs_examined == 0


# ---------------------------------------------------------------------------
# HandoffSynthesizer with DerivedBeadStore target
# ---------------------------------------------------------------------------


class TestHandoffWithDerivedStore:
    def test_handoff_persisted_to_derived_store(
        self, tmp_path: Path
    ) -> None:
        from agent_baton.core.intel.handoff_synthesizer import HandoffSynthesizer

        derived = DerivedBeadStore(tmp_path / "baton-derived.db")
        bead_store = FakeBeadStore([
            _make_bead("bd-disc1", bead_type="discovery",
                       content="Found the issue", status="open"),
        ])
        # Minimal step / result stubs.
        prior_step_result = {
            "step_id": "step-A",
            "task_id": "T-handoff",
            "files_changed": ["src/foo.py"],
            "status": "complete",
        }
        next_step = {
            "step_id": "step-B",
            "agent_name": "engineer",
            "allowed_paths": ["src/"],
            "context_files": [],
        }
        synth = HandoffSynthesizer()
        text = synth.synthesize_for_dispatch(
            prior_step_result,
            next_step,
            derived,
            task_id="T-handoff",
            bead_store=bead_store,
        )
        # Handoff text should mention the changed file.
        assert text is not None
        assert "src/foo.py" in text

        # The row should be persisted in the derived store.
        rows = derived.handoffs("T-handoff")
        assert len(rows) == 1
        assert rows[0]["from_step_id"] == "step-A"
        assert rows[0]["to_step_id"] == "step-B"
        assert "src/foo.py" in rows[0]["content"]

    def test_handoff_none_when_prior_is_none(
        self, tmp_path: Path
    ) -> None:
        from agent_baton.core.intel.handoff_synthesizer import HandoffSynthesizer

        derived = DerivedBeadStore(tmp_path / "baton-derived.db")
        synth = HandoffSynthesizer()
        result = synth.synthesize_for_dispatch(None, {"step_id": "s"}, derived)
        assert result is None
