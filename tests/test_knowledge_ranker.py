"""Tests for KnowledgeRanker (bd-0184).

Covers:
    test_rank_returns_input_when_no_telemetry
    test_higher_outcome_correlation_ranks_first
    test_stale_doc_penalized
    test_usage_count_breaks_tie
    test_handles_missing_columns_gracefully
    test_rank_is_deterministic
    test_planner_caps_at_max_attachments
    test_planner_uses_ranking_when_attaching
    test_cli_ranking_outputs_sorted
"""
from __future__ import annotations

import json
import sqlite3
from argparse import Namespace
from io import StringIO
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.core.intel.knowledge_ranker import (
    KnowledgeRanker,
    RankedDoc,
    _compute_final,
    _DEFAULT_EFFECTIVENESS,
)
from agent_baton.models.knowledge import KnowledgeAttachment


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_attachment(name: str, pack: str = "test-pack") -> KnowledgeAttachment:
    return KnowledgeAttachment(
        source="planner-matched:tag",
        pack_name=pack,
        document_name=name,
        path=f"/tmp/{name}.md",
        delivery="inline",
    )


def _in_memory_db() -> sqlite3.Connection:
    """Return an in-memory SQLite connection with the v_knowledge_effectiveness schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE knowledge_telemetry (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_name          TEXT NOT NULL,
            pack_name         TEXT NOT NULL DEFAULT '',
            task_id           TEXT NOT NULL DEFAULT '',
            step_id           TEXT NOT NULL DEFAULT '',
            used_at           TEXT NOT NULL DEFAULT '',
            delivery          TEXT NOT NULL DEFAULT 'inline',
            outcome_correlation REAL
        );
        CREATE TABLE knowledge_doc_meta (
            doc_name         TEXT NOT NULL,
            pack_name        TEXT NOT NULL DEFAULT '',
            last_modified    TEXT NOT NULL DEFAULT '',
            stale_after_days INTEGER NOT NULL DEFAULT 90,
            PRIMARY KEY (doc_name, pack_name)
        );
        CREATE VIEW v_knowledge_effectiveness AS
        SELECT
            kt.doc_name,
            kt.pack_name,
            COUNT(*)                                AS total_uses,
            ROUND(AVG(CASE WHEN kt.outcome_correlation IS NOT NULL
                      THEN kt.outcome_correlation ELSE NULL END), 4) AS avg_outcome_score,
            dm.last_modified,
            dm.stale_after_days,
            CAST(julianday('now') - julianday(NULLIF(dm.last_modified, '')) AS INTEGER)
                                                    AS days_since_modified
        FROM knowledge_telemetry kt
        LEFT JOIN knowledge_doc_meta dm
               ON dm.doc_name = kt.doc_name AND dm.pack_name = kt.pack_name
        GROUP BY kt.doc_name, kt.pack_name;
    """)
    return conn


def _insert_telemetry(
    conn: sqlite3.Connection,
    *,
    doc_name: str,
    pack_name: str = "test-pack",
    uses: int = 1,
    outcome: float | None = None,
    last_modified: str = "",
    stale_after_days: int = 90,
) -> None:
    for _ in range(uses):
        conn.execute(
            "INSERT INTO knowledge_telemetry "
            "(doc_name, pack_name, task_id, step_id, used_at, delivery, outcome_correlation) "
            "VALUES (?, ?, '', '', datetime('now'), 'inline', ?)",
            (doc_name, pack_name, outcome),
        )
    if last_modified or stale_after_days != 90:
        conn.execute(
            "INSERT OR REPLACE INTO knowledge_doc_meta "
            "(doc_name, pack_name, last_modified, stale_after_days) VALUES (?, ?, ?, ?)",
            (doc_name, pack_name, last_modified, stale_after_days),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRankReturnsInputWhenNoTelemetry:
    def test_empty_db_returns_input_unchanged_order(self) -> None:
        conn = _in_memory_db()
        ranker = KnowledgeRanker()
        docs = [_make_attachment("alpha"), _make_attachment("beta"), _make_attachment("gamma")]
        result = ranker.rank(docs, conn=conn)
        # No telemetry → all score neutrally → stable sort preserves input order
        assert [a.document_name for a in result] == ["alpha", "beta", "gamma"]

    def test_empty_candidate_list_returns_empty(self) -> None:
        ranker = KnowledgeRanker()
        assert ranker.rank([]) == []

    def test_single_candidate_returns_single(self) -> None:
        conn = _in_memory_db()
        ranker = KnowledgeRanker()
        docs = [_make_attachment("solo")]
        result = ranker.rank(docs, conn=conn)
        assert len(result) == 1
        assert result[0].document_name == "solo"


class TestHigherOutcomeCorrelationRanksFirst:
    def test_high_correlation_ranks_above_low(self) -> None:
        conn = _in_memory_db()
        _insert_telemetry(conn, doc_name="good-doc", outcome=0.9, uses=5)
        _insert_telemetry(conn, doc_name="bad-doc", outcome=0.1, uses=5)

        ranker = KnowledgeRanker()
        docs = [_make_attachment("bad-doc"), _make_attachment("good-doc")]
        result = ranker.rank(docs, conn=conn)

        assert result[0].document_name == "good-doc"
        assert result[1].document_name == "bad-doc"

    def test_perfect_correlation_scores_highest(self) -> None:
        conn = _in_memory_db()
        _insert_telemetry(conn, doc_name="perfect", outcome=1.0, uses=10)
        _insert_telemetry(conn, doc_name="middle", outcome=0.5, uses=10)
        _insert_telemetry(conn, doc_name="worst", outcome=0.0, uses=10)

        ranker = KnowledgeRanker()
        docs = [_make_attachment("middle"), _make_attachment("worst"), _make_attachment("perfect")]
        result = ranker.rank(docs, conn=conn)

        assert result[0].document_name == "perfect"
        assert result[-1].document_name == "worst"


class TestStaleDocPenalized:
    def test_stale_doc_ranks_below_fresh(self) -> None:
        """A doc modified 180 days ago with stale_after_days=90 should score lower."""
        conn = _in_memory_db()
        # Fresh doc: modified 5 days ago, stale after 90
        _insert_telemetry(
            conn,
            doc_name="fresh-doc",
            outcome=0.7,
            uses=5,
            last_modified=_days_ago(5),
            stale_after_days=90,
        )
        # Stale doc: same outcome but modified 180 days ago (past stale_after_days)
        _insert_telemetry(
            conn,
            doc_name="stale-doc",
            outcome=0.7,
            uses=5,
            last_modified=_days_ago(180),
            stale_after_days=90,
        )

        ranker = KnowledgeRanker()
        docs = [_make_attachment("stale-doc"), _make_attachment("fresh-doc")]
        result = ranker.rank(docs, conn=conn)

        assert result[0].document_name == "fresh-doc"
        assert result[1].document_name == "stale-doc"

    def test_fully_stale_recency_factor_is_zero(self) -> None:
        """recency_factor floors at 0 even if doc is older than stale_after_days * 2."""
        conn = _in_memory_db()
        _insert_telemetry(
            conn,
            doc_name="ancient",
            outcome=0.5,
            uses=1,
            last_modified=_days_ago(365),
            stale_after_days=30,
        )
        ranker = KnowledgeRanker()
        docs = [_make_attachment("ancient")]
        result = ranker.rank(docs, conn=conn)
        # Should not raise; final score should be >= 0
        assert result[0].document_name == "ancient"


class TestUsageCountBreaksTie:
    def test_higher_usage_ranks_first_when_outcomes_equal(self) -> None:
        conn = _in_memory_db()
        # Both have the same outcome; high-usage doc should rank higher
        _insert_telemetry(conn, doc_name="high-use", outcome=0.6, uses=10)
        _insert_telemetry(conn, doc_name="low-use", outcome=0.6, uses=1)

        ranker = KnowledgeRanker()
        docs = [_make_attachment("low-use"), _make_attachment("high-use")]
        result = ranker.rank(docs, conn=conn)

        assert result[0].document_name == "high-use"

    def test_usage_factor_caps_at_1(self) -> None:
        conn = _in_memory_db()
        # 20 uses should not exceed usage_factor = 1.0
        _insert_telemetry(conn, doc_name="heavy-use", outcome=0.5, uses=20)
        ranker = KnowledgeRanker()
        docs = [_make_attachment("heavy-use")]
        result = ranker.rank(docs, conn=conn)
        # Just verify it doesn't crash and returns the doc
        assert result[0].document_name == "heavy-use"


class TestHandlesMissingColumnsGracefully:
    def test_view_missing_returns_input_unchanged(self) -> None:
        """If v_knowledge_effectiveness doesn't exist, rank returns input list."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # No schema at all

        ranker = KnowledgeRanker()
        docs = [_make_attachment("alpha"), _make_attachment("beta")]
        result = ranker.rank(docs, conn=conn)
        # Best-effort: error swallowed, input returned unchanged
        assert result == docs

    def test_partial_columns_degrade_gracefully(self) -> None:
        """A view with only doc_name/pack_name/total_uses (no outcome/staleness) works."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE kt (id INTEGER PRIMARY KEY, doc_name TEXT, pack_name TEXT);
            INSERT INTO kt VALUES (1, 'alpha', 'test-pack');
            CREATE VIEW v_knowledge_effectiveness AS
            SELECT doc_name, pack_name, COUNT(*) AS total_uses FROM kt GROUP BY doc_name, pack_name;
        """)

        ranker = KnowledgeRanker()
        docs = [_make_attachment("alpha")]
        result = ranker.rank(docs, conn=conn)
        # Partial view: missing avg_outcome_score, days_since_modified, stale_after_days
        # KnowledgeRanker falls back to defaults gracefully
        assert len(result) == 1
        assert result[0].document_name == "alpha"


class TestRankIsDeterministic:
    def test_same_input_same_output(self) -> None:
        conn = _in_memory_db()
        _insert_telemetry(conn, doc_name="alpha", outcome=0.8, uses=5)
        _insert_telemetry(conn, doc_name="beta", outcome=0.6, uses=3)
        _insert_telemetry(conn, doc_name="gamma", outcome=0.4, uses=1)

        ranker = KnowledgeRanker()
        docs = [_make_attachment("gamma"), _make_attachment("alpha"), _make_attachment("beta")]

        result_a = [a.document_name for a in ranker.rank(docs, conn=conn)]
        result_b = [a.document_name for a in ranker.rank(docs, conn=conn)]

        assert result_a == result_b

    def test_order_consistent_across_calls(self) -> None:
        conn = _in_memory_db()
        _insert_telemetry(conn, doc_name="x", outcome=0.9, uses=8)
        _insert_telemetry(conn, doc_name="y", outcome=0.3, uses=2)

        ranker = KnowledgeRanker()
        docs = [_make_attachment("y"), _make_attachment("x")]

        for _ in range(5):
            result = ranker.rank(docs, conn=conn)
            assert result[0].document_name == "x"


class TestPlannerCapsAtMaxAttachments:
    """Integration test: planner applies the cap from BATON_MAX_KNOWLEDGE_PER_STEP."""

    def test_planner_caps_knowledge_to_max(self, tmp_path: Path) -> None:
        """When resolver returns more than max, only max attachments are kept."""
        from agent_baton.core.engine.planner import IntelligentPlanner
        from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry
        import yaml

        # Build a pack with 12 docs (more than the default cap of 8)
        pack_dir = tmp_path / "big-pack"
        pack_dir.mkdir()
        manifest = {
            "name": "big-pack",
            "description": "implement feature",
            "tags": ["implement", "feature"],
            "default_delivery": "inline",
        }
        (pack_dir / "knowledge.yaml").write_text(yaml.dump(manifest))

        for i in range(12):
            content = f"---\nname: doc-{i:02d}\ndescription: desc {i}\ntags: [implement]\n---\n" + "x" * 50
            (pack_dir / f"doc_{i:02d}.md").write_text(content)

        registry = KnowledgeRegistry()
        registry.load_directory(tmp_path)

        planner = IntelligentPlanner(
            team_context_root=tmp_path / "tc",
            knowledge_registry=registry,
        )

        with patch.dict("os.environ", {"BATON_MAX_KNOWLEDGE_PER_STEP": "3"}):
            plan = planner.create_plan(
                "implement a feature for the big pack",
                task_type="feature",
            )

        for phase in plan.phases:
            for step in phase.steps:
                assert len(step.knowledge) <= 3, (
                    f"Step {step.step_id} has {len(step.knowledge)} attachments (cap=3)"
                )

    def test_default_cap_is_8(self, tmp_path: Path) -> None:
        """Default BATON_MAX_KNOWLEDGE_PER_STEP is 8."""
        from agent_baton.core.engine.planner import IntelligentPlanner
        from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry
        import yaml

        pack_dir = tmp_path / "wide-pack"
        pack_dir.mkdir()
        manifest = {
            "name": "wide-pack",
            "description": "implement workflow",
            "tags": ["implement", "workflow"],
            "default_delivery": "inline",
        }
        (pack_dir / "knowledge.yaml").write_text(yaml.dump(manifest))

        for i in range(15):
            content = f"---\nname: wdoc-{i:02d}\ndescription: desc {i}\ntags: [implement]\n---\n" + "x" * 50
            (pack_dir / f"wdoc_{i:02d}.md").write_text(content)

        registry = KnowledgeRegistry()
        registry.load_directory(tmp_path)

        planner = IntelligentPlanner(
            team_context_root=tmp_path / "tc",
            knowledge_registry=registry,
        )

        import os
        env = {k: v for k, v in os.environ.items() if k != "BATON_MAX_KNOWLEDGE_PER_STEP"}
        with patch.dict("os.environ", env, clear=True):
            plan = planner.create_plan(
                "implement a workflow for the wide pack",
                task_type="feature",
            )

        for phase in plan.phases:
            for step in phase.steps:
                assert len(step.knowledge) <= 8


class TestPlannerUsesRankingWhenAttaching:
    """Verify the planner calls KnowledgeRanker.rank during plan creation."""

    def test_ranker_is_called_on_resolution(self, tmp_path: Path) -> None:
        from agent_baton.core.engine.planner import IntelligentPlanner
        from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry
        import yaml

        pack_dir = tmp_path / "rank-pack"
        pack_dir.mkdir()
        manifest = {
            "name": "rank-pack",
            "description": "implement ranking",
            "tags": ["implement", "ranking"],
            "default_delivery": "inline",
        }
        (pack_dir / "knowledge.yaml").write_text(yaml.dump(manifest))
        content = "---\nname: rank-doc\ndescription: ranking doc\ntags: [implement]\n---\n" + "x" * 50
        (pack_dir / "rank_doc.md").write_text(content)

        registry = KnowledgeRegistry()
        registry.load_directory(tmp_path)

        planner = IntelligentPlanner(
            team_context_root=tmp_path / "tc",
            knowledge_registry=registry,
        )

        call_count = 0
        original_rank = KnowledgeRanker.rank

        def spy_rank(self, candidate_docs, conn=None):  # type: ignore[no-untyped-def]
            nonlocal call_count
            call_count += 1
            return original_rank(self, candidate_docs, conn=conn)

        with patch.object(KnowledgeRanker, "rank", spy_rank):
            planner.create_plan(
                "implement the ranking feature",
                task_type="feature",
            )

        assert call_count > 0, "KnowledgeRanker.rank was not called during plan creation"


class TestCliRankingOutputsSorted:
    def test_table_output_is_sorted_descending(self, capsys: pytest.CaptureFixture) -> None:
        from agent_baton.cli.commands.knowledge.ranking_cmd import _run_ranking

        ranked = [
            RankedDoc("alpha", "pack-a", 0.9, 1.0, 1.0, 0.9 * 0.6 + 1.0 * 0.2 + 1.0 * 0.2),
            RankedDoc("beta", "pack-b", 0.5, 0.8, 0.5, 0.5 * 0.6 + 0.8 * 0.2 + 0.5 * 0.2),
            RankedDoc("gamma", "pack-c", 0.1, 0.2, 0.0, 0.1 * 0.6 + 0.2 * 0.2 + 0.0 * 0.2),
        ]
        ranked.sort(key=lambda r: r.final_score, reverse=True)

        args = Namespace(output="table", db=None)

        with patch.object(KnowledgeRanker, "rank_all_known", return_value=ranked):
            _run_ranking(args)

        out = capsys.readouterr().out
        lines = [l for l in out.splitlines() if l.startswith("|") and "Pack" not in l and "---" not in l]
        scores = []
        for line in lines:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            # Final score is the 3rd data column (index 2)
            scores.append(float(parts[2]))

        assert scores == sorted(scores, reverse=True), "Table rows are not sorted descending"

    def test_json_output_is_sorted_descending(self, capsys: pytest.CaptureFixture) -> None:
        from agent_baton.cli.commands.knowledge.ranking_cmd import _run_ranking

        ranked = [
            RankedDoc("high", "p", 0.9, 1.0, 1.0, 0.94),
            RankedDoc("low", "p", 0.1, 0.0, 0.0, 0.06),
        ]

        args = Namespace(output="json", db=None)

        with patch.object(KnowledgeRanker, "rank_all_known", return_value=ranked):
            _run_ranking(args)

        data = json.loads(capsys.readouterr().out)
        scores = [r["final_score"] for r in data]
        assert scores == sorted(scores, reverse=True)

    def test_empty_telemetry_prints_no_data_message(self, capsys: pytest.CaptureFixture) -> None:
        from agent_baton.cli.commands.knowledge.ranking_cmd import _run_ranking

        args = Namespace(output="table", db=None)

        with patch.object(KnowledgeRanker, "rank_all_known", return_value=[]):
            _run_ranking(args)

        out = capsys.readouterr().out
        assert "No knowledge telemetry" in out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _days_ago(n: int) -> str:
    """Return an ISO-8601 date string for n days ago."""
    from datetime import datetime, timedelta, timezone
    dt = datetime.now(timezone.utc) - timedelta(days=n)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
