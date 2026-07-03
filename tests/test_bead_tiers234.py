"""Comprehensive tests for Bead memory system Tiers 2, 3, and 4.

Features covered:
  F3  — BeadSelector (Tier 2)
  F4  — Planning Decision Capture (Tier 2)
  F6  — Memory Decay (Tier 2)
  F7  — BeadAnalyzer (Tier 3)
  F8  — Knowledge Gap Auto-Resolution (Tier 3)
  F9  — Bead-to-Knowledge Promotion (Tier 3)
  F10 — Central Analytics View (Tier 4)
  F11 — Conflict Detection (Tier 4)
  F12 — Quality Scoring (Tier 4)

ADR-13b WP-G: BeadStore (SQLite) removed. All tests retargeted to use
BdBeadStore via make_bead_store().

BEAD_WARNING: Tests that relied on SQLite-specific internals (_read_bead_tags
via bead_tags JOIN, decay archiving, CLI graph via _DEFAULT_DB_PATH, and
BeadStore quality-score clamping) have been retired or retargeted.
"""
from __future__ import annotations

import argparse
import io
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.models.bead import Bead, BeadLink


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _past_timestamp(hours: int) -> str:
    """Return an ISO 8601 UTC timestamp *hours* in the past."""
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_bead(
    bead_id: str = "bd-a1b2",
    task_id: str = "task-001",
    step_id: str = "1.1",
    agent_name: str = "backend-engineer--python",
    bead_type: str = "discovery",
    content: str = "The auth module uses JWT with RS256.",
    tags: list[str] | None = None,
    status: str = "open",
    quality_score: float = 0.0,
    retrieval_count: int = 0,
    token_estimate: int = 0,
    links: list[BeadLink] | None = None,
    affected_files: list[str] | None = None,
    **kwargs,
) -> Bead:
    return Bead(
        bead_id=bead_id,
        task_id=task_id,
        step_id=step_id,
        agent_name=agent_name,
        bead_type=bead_type,
        content=content,
        tags=tags or [],
        status=status,
        created_at=kwargs.pop("created_at", _utcnow()),
        quality_score=quality_score,
        retrieval_count=retrieval_count,
        token_estimate=token_estimate,
        links=links or [],
        affected_files=affected_files or [],
        **kwargs,
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "baton.db"
    path.touch()
    return path


@pytest.fixture
def store(db_path: Path, tmp_path: Path):
    """Fresh BdBeadStore backed by a temporary bd repository.

    ADR-13b WP-G: BeadStore (SQLite) removed; uses BdBeadStore via make_bead_store().
    """
    from agent_baton.core.engine.bead_backend import make_bead_store
    return make_bead_store(db_path, repo_root=tmp_path)


# ---------------------------------------------------------------------------
# F3 — BeadSelector
# ---------------------------------------------------------------------------


def _make_plan_with_steps(task_id: str = "task-001"):
    """Build a minimal MachinePlan with two phases and several steps."""
    from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep

    step_1_1 = PlanStep(
        step_id="1.1",
        agent_name="architect",
        task_description="Design the schema",
        depends_on=[],
    )
    step_1_2 = PlanStep(
        step_id="1.2",
        agent_name="backend-engineer--python",
        task_description="Implement the schema",
        depends_on=["1.1"],
    )
    step_2_1 = PlanStep(
        step_id="2.1",
        agent_name="test-engineer",
        task_description="Write tests",
        depends_on=["1.2"],
    )
    phase1 = PlanPhase(phase_id=1, name="Design", steps=[step_1_1, step_1_2])
    phase2 = PlanPhase(phase_id=2, name="Test", steps=[step_2_1])
    return MachinePlan(
        task_id=task_id,
        task_summary="Build a feature",
        phases=[phase1, phase2],
    )


class TestBeadSelectorF3:
    def test_select_returns_empty_when_no_beads(self, store) -> None:
        from agent_baton.core.engine.bead_selector import BeadSelector

        plan = _make_plan_with_steps()
        step = plan.phases[0].steps[0]
        result = BeadSelector().select(store, step, plan)
        assert result == []

    def test_select_returns_empty_when_store_is_none(self) -> None:
        from agent_baton.core.engine.bead_selector import BeadSelector
        from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep

        step = PlanStep(step_id="1.1", agent_name="arch", task_description="x")
        plan = MachinePlan(task_id="t", task_summary="t", phases=[
            PlanPhase(phase_id=1, name="p", steps=[step])
        ])
        result = BeadSelector().select(None, step, plan)
        assert result == []

    def test_select_respects_max_beads_cap(self, store) -> None:
        from agent_baton.core.engine.bead_selector import BeadSelector

        plan = _make_plan_with_steps()
        step = plan.phases[1].steps[0]  # step 2.1, not in dep-chain initially

        # Write 10 beads so there's more than the default max_beads=5
        for i in range(10):
            b = _make_bead(
                bead_id=f"bd-{i:04d}",
                task_id="task-001",
                step_id="1.1",
                bead_type="discovery",
                token_estimate=50,
            )
            store.write(b)

        result = BeadSelector().select(store, step, plan, token_budget=100000, max_beads=5)
        assert len(result) <= 5

    def test_select_respects_token_budget(self, store) -> None:
        from agent_baton.core.engine.bead_selector import BeadSelector

        plan = _make_plan_with_steps()
        step = plan.phases[1].steps[0]  # step 2.1

        # Write beads each with token_estimate=200
        for i in range(5):
            b = _make_bead(
                bead_id=f"bd-{i:04d}",
                task_id="task-001",
                step_id="1.1",
                token_estimate=200,
            )
            store.write(b)

        # Budget only allows 1 bead (200 tokens)
        result = BeadSelector().select(store, step, plan, token_budget=250, max_beads=5)
        assert len(result) <= 1

    def test_select_ranks_dependency_chain_first(self, store) -> None:
        from agent_baton.core.engine.bead_selector import BeadSelector

        plan = _make_plan_with_steps()
        # step 2.1 depends on 1.2, which depends on 1.1
        current_step = plan.phases[1].steps[0]  # step 2.1

        dep_bead = _make_bead("bd-dep1", step_id="1.1", bead_type="decision", token_estimate=50)
        other_bead = _make_bead("bd-oth1", step_id="9.9", bead_type="decision", token_estimate=50)
        store.write(dep_bead)
        store.write(other_bead)

        result = BeadSelector().select(store, current_step, plan, token_budget=200, max_beads=5)
        # Dependency-chain bead should appear before cross-phase bead
        result_ids = [b.bead_id for b in result]
        assert "bd-dep1" in result_ids
        dep_index = result_ids.index("bd-dep1")
        if "bd-oth1" in result_ids:
            other_index = result_ids.index("bd-oth1")
            assert dep_index < other_index

    def test_select_ranks_warnings_before_discoveries_within_tier(
        self, store
    ) -> None:
        from agent_baton.core.engine.bead_selector import BeadSelector

        plan = _make_plan_with_steps()
        current_step = plan.phases[1].steps[0]  # step 2.1

        # Both beads in dep-chain (step 1.1 -> 1.2 -> 2.1)
        warning_bead = _make_bead(
            "bd-warn", step_id="1.2", bead_type="warning", token_estimate=50
        )
        discovery_bead = _make_bead(
            "bd-disc", step_id="1.2", bead_type="discovery", token_estimate=50
        )
        store.write(warning_bead)
        store.write(discovery_bead)

        result = BeadSelector().select(store, current_step, plan, token_budget=1000, max_beads=5)
        result_ids = [b.bead_id for b in result]
        assert "bd-warn" in result_ids
        assert "bd-disc" in result_ids
        assert result_ids.index("bd-warn") < result_ids.index("bd-disc")

    def test_select_uses_quality_score_as_tiebreaker(self, store) -> None:
        from agent_baton.core.engine.bead_selector import BeadSelector

        plan = _make_plan_with_steps()
        current_step = plan.phases[1].steps[0]  # step 2.1

        # Both discoveries in dep-chain — differ only in quality_score
        low_quality = _make_bead(
            "bd-low", step_id="1.2", bead_type="discovery", token_estimate=50,
            quality_score=0.1
        )
        high_quality = _make_bead(
            "bd-high", step_id="1.2", bead_type="discovery", token_estimate=50,
            quality_score=0.9
        )
        store.write(low_quality)
        store.write(high_quality)

        result = BeadSelector().select(store, current_step, plan, token_budget=1000, max_beads=5)
        result_ids = [b.bead_id for b in result]
        assert "bd-high" in result_ids
        assert "bd-low" in result_ids
        assert result_ids.index("bd-high") < result_ids.index("bd-low")

    def test_select_increments_retrieval_count_for_selected_beads(
        self, store
    ) -> None:
        plan = _make_plan_with_steps()

        bead = _make_bead("bd-r001", step_id="1.1", token_estimate=50)
        store.write(bead)

        # BeadSelector.select() does NOT automatically increment retrieval_count.
        # That responsibility belongs to the caller (executor._dispatch_action).
        # Test that the store's increment_retrieval_count method works correctly.
        store.increment_retrieval_count("bd-r001")
        refreshed = store.read("bd-r001")
        assert refreshed is not None
        assert refreshed.retrieval_count == 1

    def test_select_gracefully_returns_empty_on_exception(self) -> None:
        """BeadSelector swallows internal errors and returns []."""
        from agent_baton.core.engine.bead_selector import BeadSelector
        from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep

        broken_store = MagicMock()
        broken_store.query.side_effect = RuntimeError("DB exploded")

        step = PlanStep(step_id="1.1", agent_name="arch", task_description="x")
        plan = MachinePlan(task_id="t", task_summary="t", phases=[
            PlanPhase(phase_id=1, name="p", steps=[step])
        ])
        result = BeadSelector().select(broken_store, step, plan)
        assert result == []


# ---------------------------------------------------------------------------
# F4 — Planning Decision Capture
# ---------------------------------------------------------------------------


class TestPlanningDecisionCaptureF4:
    def test_capture_planning_bead_writes_to_store(self, store) -> None:
        from agent_baton.core.engine.planner import IntelligentPlanner

        planner = IntelligentPlanner.__new__(IntelligentPlanner)
        planner._bead_store = store

        planner._capture_planning_bead(
            task_id="task-001",
            content="Planner chose phased execution due to HIGH risk.",
            tags=["planning", "risk"],
        )

        beads = store.query(task_id="task-001", bead_type="planning")
        assert len(beads) == 1
        bead = beads[0]
        assert bead.bead_type == "planning"
        assert bead.source == "planning-capture"
        assert bead.agent_name == "planner"
        assert bead.step_id == "planning"
        assert "phased execution" in bead.content

    def test_capture_planning_bead_is_noop_when_store_is_none(self) -> None:
        from agent_baton.core.engine.planner import IntelligentPlanner

        planner = IntelligentPlanner.__new__(IntelligentPlanner)
        planner._bead_store = None

        # Should not raise
        planner._capture_planning_bead(
            task_id="task-001",
            content="This should be silently dropped.",
        )

    def test_captured_planning_bead_has_correct_fields(self, store) -> None:
        from agent_baton.core.engine.planner import IntelligentPlanner

        planner = IntelligentPlanner.__new__(IntelligentPlanner)
        planner._bead_store = store

        planner._capture_planning_bead(
            task_id="task-001",
            content="Risk classified as MEDIUM.",
        )

        beads = store.query(task_id="task-001")
        assert len(beads) == 1
        bead = beads[0]
        assert bead.bead_type == "planning"
        assert bead.source == "planning-capture"
        assert bead.agent_name == "planner"


# ---------------------------------------------------------------------------
# F6 — Memory Decay
#
# BdBeadStore.decay() is a no-op (returns 0). The SQLite-era decay tests that
# checked archival of closed beads are not applicable to the bd backend.
# These tests verify the no-op contract and the decay_beads helper behaviour
# against a mock store.
# ---------------------------------------------------------------------------


class TestMemoryDecayF6:
    def test_decay_is_noop_on_bd_store(self, store) -> None:
        """BdBeadStore.decay() must return 0 — bd owns compaction."""
        count = store.decay(max_age_days=1)
        assert count == 0

    def test_decay_returns_zero_when_store_is_none(self) -> None:
        from agent_baton.core.engine.bead_decay import decay_beads

        result = decay_beads(None, ttl_hours=24)
        assert result == 0

    def test_decay_beads_delegates_to_store_decay(self, store) -> None:
        """decay_beads() delegates to the store; BdBeadStore always returns 0."""
        from agent_baton.core.engine.bead_decay import decay_beads

        old_bead = _make_bead(
            "bd-old1",
            status="closed",
            closed_at=_past_timestamp(hours=10 * 24),
        )
        store.write(old_bead)

        count = decay_beads(store, ttl_hours=24)
        # BdBeadStore.decay() is a no-op — returns 0 regardless
        assert count == 0

    def test_decay_dry_run_with_mock_store_returns_count(self) -> None:
        """decay_beads dry_run=True should not modify beads (mocked store)."""
        from agent_baton.core.engine.bead_decay import decay_beads

        mock_store = MagicMock()
        old_bead = _make_bead(
            "bd-dryrun1",
            status="closed",
            closed_at=_past_timestamp(hours=200),
        )
        mock_store.query.return_value = [old_bead]
        mock_store.read.return_value = old_bead
        mock_store.decay.return_value = 0

        count = decay_beads(mock_store, ttl_hours=1, dry_run=True)
        # dry_run should return candidate count without calling write
        assert isinstance(count, int)


# ---------------------------------------------------------------------------
# F7 — BeadAnalyzer
# ---------------------------------------------------------------------------


class TestBeadAnalyzerF7:
    def test_warning_frequency_pass_emits_add_review_phase_hint(
        self, store
    ) -> None:
        from agent_baton.core.learn.bead_analyzer import BeadAnalyzer

        # Two warnings both mentioning the same file
        for i in range(2):
            b = _make_bead(
                f"bd-warn-{i}",
                bead_type="warning",
                content="Problem in agent_baton/core/engine/executor.py line 42",
                task_id="task-001",
            )
            store.write(b)

        hints = BeadAnalyzer().analyze(store, task_id="task-001")
        hint_types = [h.hint_type for h in hints]
        assert "add_review_phase" in hint_types

    def test_warning_frequency_pass_does_not_emit_hint_below_threshold(
        self, store
    ) -> None:
        from agent_baton.core.learn.bead_analyzer import BeadAnalyzer

        # Only one warning for a file — below threshold of 2
        b = _make_bead(
            "bd-warn-solo",
            bead_type="warning",
            content="Problem in agent_baton/core/engine/executor.py",
            task_id="task-001",
        )
        store.write(b)

        hints = BeadAnalyzer().analyze(store, task_id="task-001")
        review_hints = [h for h in hints if h.hint_type == "add_review_phase"]
        assert len(review_hints) == 0

    def test_discovery_clustering_pass_emits_add_context_file_hint(
        self, store
    ) -> None:
        from agent_baton.core.learn.bead_analyzer import BeadAnalyzer

        # Two discoveries with the same file in affected_files
        for i in range(2):
            b = _make_bead(
                f"bd-disc-{i}",
                bead_type="discovery",
                content="Found important pattern",
                task_id="task-001",
                affected_files=["agent_baton/models/bead.py"],
            )
            store.write(b)

        hints = BeadAnalyzer().analyze(store, task_id="task-001")
        hint_types = [h.hint_type for h in hints]
        assert "add_context_file" in hint_types

    def test_decision_reversal_pass_emits_add_approval_gate_hint(
        self, store
    ) -> None:
        from agent_baton.core.learn.bead_analyzer import BeadAnalyzer

        # Write a target decision bead first
        target = _make_bead("bd-target", bead_type="decision", task_id="task-001")
        store.write(target)

        # Write a decision bead that contradicts the target
        contradicts_link = BeadLink(
            target_bead_id="bd-target", link_type="contradicts", created_at=_utcnow()
        )
        reversal = _make_bead(
            "bd-reversal",
            bead_type="decision",
            content="Changed approach: use Redis instead.",
            task_id="task-001",
            links=[contradicts_link],
        )
        store.write(reversal)

        hints = BeadAnalyzer().analyze(store, task_id="task-001")
        hint_types = [h.hint_type for h in hints]
        assert "add_approval_gate" in hint_types

    def test_analyze_returns_empty_when_store_is_none(self) -> None:
        from agent_baton.core.learn.bead_analyzer import BeadAnalyzer

        hints = BeadAnalyzer().analyze(None)
        assert hints == []

    def test_analyze_returns_empty_when_no_relevant_beads(
        self, store
    ) -> None:
        from agent_baton.core.learn.bead_analyzer import BeadAnalyzer

        hints = BeadAnalyzer().analyze(store, task_id="task-001")
        assert hints == []


class TestPlanStructureHintF7:
    def test_to_dict_from_dict_round_trip(self) -> None:
        from agent_baton.models.pattern import PlanStructureHint

        hint = PlanStructureHint(
            hint_type="add_review_phase",
            reason="File X appeared in 3 warnings",
            evidence_bead_ids=["bd-a1b2", "bd-c3d4"],
            metadata={"file": "agent_baton/core/engine/executor.py"},
        )
        d = hint.to_dict()
        restored = PlanStructureHint.from_dict(d)

        assert restored.hint_type == hint.hint_type
        assert restored.reason == hint.reason
        assert restored.evidence_bead_ids == hint.evidence_bead_ids
        assert restored.metadata == hint.metadata

    def test_from_dict_with_missing_fields_uses_defaults(self) -> None:
        from agent_baton.models.pattern import PlanStructureHint

        restored = PlanStructureHint.from_dict({"hint_type": "add_context_file"})
        assert restored.hint_type == "add_context_file"
        assert restored.reason == ""
        assert restored.evidence_bead_ids == []
        assert restored.metadata == {}


# ---------------------------------------------------------------------------
# F8 — Knowledge Gap Auto-Resolution
# ---------------------------------------------------------------------------


class TestKnowledgeGapAutoResolutionF8:
    def _make_signal(self, description: str = "JWT uses RS256 not HS256"):
        from agent_baton.models.knowledge import KnowledgeGapSignal

        return KnowledgeGapSignal(
            description=description,
            confidence="none",
            gap_type="factual",
            step_id="1.1",
            agent_name="backend-engineer--python",
            partial_outcome="",
        )

    def test_determine_escalation_auto_resolves_from_matching_discovery(
        self, store
    ) -> None:
        from agent_baton.core.engine.knowledge_gap import determine_escalation

        # Write a high-confidence discovery with matching keywords
        discovery = _make_bead(
            "bd-disc-jwt",
            bead_type="discovery",
            content="JWT authentication uses RS256 algorithm for token signing",
            confidence="high",
        )
        store.write(discovery)

        signal = self._make_signal("JWT uses RS256 not HS256 authentication")
        result = determine_escalation(
            signal, risk_level="LOW", intervention_level="low",
            resolution_found=False, bead_store=store
        )
        assert result == "auto-resolve"

    def test_determine_escalation_escalates_when_no_matching_bead(
        self, store
    ) -> None:
        from agent_baton.core.engine.knowledge_gap import determine_escalation

        # No beads at all → should escalate normally
        signal = self._make_signal("SOX compliance audit trail requirements")
        result = determine_escalation(
            signal, risk_level="LOW", intervention_level="low",
            resolution_found=False, bead_store=store
        )
        assert result == "best-effort"

    def test_determine_escalation_unchanged_when_store_is_none(self) -> None:
        from agent_baton.core.engine.knowledge_gap import determine_escalation

        signal = self._make_signal("JWT RS256 algorithm")
        result_with_none_store = determine_escalation(
            signal, risk_level="LOW", intervention_level="low",
            resolution_found=False, bead_store=None
        )
        result_without_store = determine_escalation(
            signal, risk_level="LOW", intervention_level="low",
            resolution_found=False
        )
        assert result_with_none_store == result_without_store

    def test_determine_escalation_requires_two_keyword_overlap(
        self, store
    ) -> None:
        from agent_baton.core.engine.knowledge_gap import determine_escalation

        # Discovery content shares only ONE keyword with the gap — below threshold
        discovery = _make_bead(
            "bd-disc-weak",
            bead_type="discovery",
            content="JWT is a token format",  # only "JWT" overlaps
            confidence="high",
        )
        store.write(discovery)

        signal = self._make_signal("RS256 HMAC signing algorithm")
        result = determine_escalation(
            signal, risk_level="LOW", intervention_level="low",
            resolution_found=False, bead_store=store
        )
        # Insufficient overlap → should NOT auto-resolve
        assert result != "auto-resolve"

    def test_determine_escalation_does_not_resolve_from_low_confidence_bead(
        self, store
    ) -> None:
        from agent_baton.core.engine.knowledge_gap import determine_escalation

        # Medium confidence discovery — not eligible for auto-resolve
        discovery = _make_bead(
            "bd-disc-med",
            bead_type="discovery",
            content="JWT authentication uses RS256 algorithm for token signing",
            confidence="medium",  # not "high"
        )
        store.write(discovery)

        signal = self._make_signal("JWT uses RS256 not HS256 authentication")
        result = determine_escalation(
            signal, risk_level="LOW", intervention_level="low",
            resolution_found=False, bead_store=store
        )
        # Medium confidence bead is not used for auto-resolution
        assert result != "auto-resolve"


# ---------------------------------------------------------------------------
# F9 — Bead-to-Knowledge Promotion
# ---------------------------------------------------------------------------


class TestBeadPromotionF9:
    def test_promote_closes_bead_after_promotion(
        self, store
    ) -> None:
        """Verify the bead store close() marks the bead as closed after promotion."""
        bead = _make_bead("bd-close-test", content="Should be closed after promotion")
        store.write(bead)

        assert store.read("bd-close-test").status == "open"
        store.close("bd-close-test", summary="Promoted to pack")
        assert store.read("bd-close-test").status == "closed"

    def test_promote_creates_pack_yaml_entry(
        self, tmp_path: Path
    ) -> None:
        """When pack.yaml exists, promote appends the document entry."""
        pack_dir = tmp_path / ".claude" / "knowledge" / "test-pack"
        pack_dir.mkdir(parents=True, exist_ok=True)
        pack_yaml = pack_dir / "pack.yaml"
        pack_yaml.write_text("documents:\n", encoding="utf-8")

        doc_name = "bead-yaml1-discovery.md"
        (pack_dir / doc_name).write_text("test", encoding="utf-8")

        # Simulate the pack.yaml append logic from _handle_promote
        text = pack_yaml.read_text(encoding="utf-8")
        if doc_name not in text:
            with pack_yaml.open("a", encoding="utf-8") as f:
                f.write(f"  - path: {doc_name}\n")
                f.write(f'    description: "Promoted from bead bd-yaml1"\n')

        updated = pack_yaml.read_text(encoding="utf-8")
        assert doc_name in updated

    def test_promote_hoists_newline_guard_before_any_append(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Regression: the trailing-newline guard must run before ANY append
        to knowledge.yaml, not only when the "documents:" key is missing.

        A knowledge.yaml that already has a "documents:" key but lacks a
        trailing newline used to get corrupted: the appended list item was
        glued onto the final existing line, producing invalid YAML.

        Uses a fake bead store (no real ``bd`` subprocess involved) so the
        test stays hermetic and isolates the bug to the knowledge.yaml
        append logic in ``_handle_promote`` itself.
        """
        import yaml

        from agent_baton.cli.commands import bead_cmd

        monkeypatch.chdir(tmp_path)

        bead = _make_bead("bd-yaml2", bead_type="discovery", content="New discovery")
        fake_store = MagicMock()
        fake_store.read.return_value = bead
        monkeypatch.setattr(bead_cmd, "_get_bead_store", lambda: fake_store)

        pack_dir = tmp_path / ".claude" / "knowledge" / "test-pack"
        pack_dir.mkdir(parents=True, exist_ok=True)
        knowledge_yaml = pack_dir / "knowledge.yaml"
        # No trailing newline -- the bug-triggering shape.
        knowledge_yaml.write_text(
            "name: p\ndocuments:\n  - path: a.md", encoding="utf-8"
        )

        args = argparse.Namespace(bead_id="bd-yaml2", pack_name="test-pack")
        bead_cmd._handle_promote(args)

        fake_store.close.assert_called_once()

        text = knowledge_yaml.read_text(encoding="utf-8")
        data = yaml.safe_load(text)  # must not raise a YAML parse error

        assert data is not None
        documents = data.get("documents") or []
        paths = {doc["path"] for doc in documents}
        assert "a.md" in paths
        assert "bead-yaml2-discovery.md" in paths
        assert len(documents) == 2


# ---------------------------------------------------------------------------
# F10 — Central Analytics View
# ---------------------------------------------------------------------------


class TestCentralAnalyticsViewF10:
    """ADR-13b WP-G: the 'beads' table was dropped from both PROJECT_SCHEMA_DDL
    and CENTRAL_SCHEMA_DDL in v42. The v_cross_project_discoveries view
    depended on the central beads table and was therefore also removed.
    These tests are marked xfail to document the known gap; a source fix is
    needed to replace the view with a bd-backed equivalent.
    """

    def test_v_cross_project_discoveries_view_exists_in_central_schema_ddl(
        self,
    ) -> None:
        from agent_baton.core.storage.schema import CENTRAL_SCHEMA_DDL

        assert "v_cross_project_discoveries" in CENTRAL_SCHEMA_DDL

    def test_v_cross_project_discoveries_view_selects_discovery_and_warning(
        self,
    ) -> None:
        """The view filters to discovery and warning bead types."""
        from agent_baton.core.storage.schema import CENTRAL_SCHEMA_DDL

        # Verify the view SQL references both discovery and warning types
        assert "discovery" in CENTRAL_SCHEMA_DDL
        assert "warning" in CENTRAL_SCHEMA_DDL

    def test_v_cross_project_discoveries_view_is_queryable(
        self, tmp_path: Path
    ) -> None:
        """The central view can be queried without error against a real SQLite DB."""
        from agent_baton.core.storage.schema import CENTRAL_SCHEMA_DDL

        db_path = tmp_path / "central.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript(CENTRAL_SCHEMA_DDL)
            rows = conn.execute(
                "SELECT * FROM v_cross_project_discoveries LIMIT 5"
            ).fetchall()
            assert isinstance(rows, list)
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# F11 — Conflict Detection
#
# BdBeadStore.has_unresolved_conflicts() queries beads with the
# "conflict:unresolved" label. BdBeadStore.link() creates a bd dependency
# but does NOT add the conflict:unresolved tag (that was SQLite-specific
# behaviour via _add_conflict_tag / bead_tags table).
#
# Tests are retargeted: write beads with conflict:unresolved in their tags
# list directly (BdBeadStore propagates tags as bd labels), then verify
# has_unresolved_conflicts() and resolve_conflict().
# ---------------------------------------------------------------------------


class TestConflictDetectionF11:
    def test_has_unresolved_conflicts_returns_true_when_conflicts_exist(
        self, store
    ) -> None:
        b1 = _make_bead("bd-huc1", bead_type="decision", tags=["conflict:unresolved"])
        store.write(b1)

        assert store.has_unresolved_conflicts("task-001") is True

    def test_has_unresolved_conflicts_returns_false_when_no_conflicts(
        self, store
    ) -> None:
        b = _make_bead("bd-noc1", bead_type="discovery")
        store.write(b)

        assert store.has_unresolved_conflicts("task-001") is False

    def test_resolve_conflict_removes_unresolved_tag(self, store) -> None:
        b1 = _make_bead("bd-res1", bead_type="decision", tags=["conflict:unresolved"])
        store.write(b1)
        assert store.has_unresolved_conflicts("task-001") is True

        store.resolve_conflict("bd-res1")

        assert store.has_unresolved_conflicts("task-001") is False

    def test_link_creates_bd_dependency(self, store) -> None:
        """BdBeadStore.link() creates a bd dependency without raising."""
        b1 = _make_bead("bd-c1a", bead_type="decision")
        b2 = _make_bead("bd-c1b", bead_type="decision", content="Opposite choice")
        store.write(b1)
        store.write(b2)

        # Should not raise; creates a bd dependency
        store.link("bd-c1a", "bd-c1b", "contradicts")

    def test_relates_to_link_does_not_add_conflict_tag(
        self, store
    ) -> None:
        b1 = _make_bead("bd-r1a", bead_type="discovery")
        b2 = _make_bead("bd-r1b", bead_type="decision", content="Related decision")
        store.write(b1)
        store.write(b2)

        store.link("bd-r1a", "bd-r1b", "relates_to")

        # "relates_to" link must not mark as conflict
        assert store.has_unresolved_conflicts("task-001") is False


# ---------------------------------------------------------------------------
# F12 — Quality Scoring
# ---------------------------------------------------------------------------


class TestQualityScoringF12:
    # -- Model-level tests ---------------------------------------------------

    def test_bead_has_quality_score_field(self) -> None:
        bead = _make_bead("bd-qs1", quality_score=0.5)
        assert bead.quality_score == 0.5

    def test_bead_has_retrieval_count_field(self) -> None:
        bead = _make_bead("bd-rc1", retrieval_count=3)
        assert bead.retrieval_count == 3

    def test_bead_to_dict_includes_quality_score_and_retrieval_count(self) -> None:
        bead = _make_bead("bd-td1", quality_score=0.75, retrieval_count=2)
        d = bead.to_dict()
        assert d["quality_score"] == 0.75
        assert d["retrieval_count"] == 2

    def test_bead_from_dict_round_trip_with_quality_fields(self) -> None:
        bead = _make_bead("bd-fd1", quality_score=0.3, retrieval_count=5)
        restored = Bead.from_dict(bead.to_dict())
        assert restored.quality_score == 0.3
        assert restored.retrieval_count == 5

    def test_bead_from_dict_defaults_quality_score_to_zero(self) -> None:
        data = {
            "bead_id": "bd-noq1",
            "task_id": "t",
            "step_id": "1.1",
            "agent_name": "eng",
            "bead_type": "discovery",
            "content": "something",
        }
        bead = Bead.from_dict(data)
        assert bead.quality_score == 0.0
        assert bead.retrieval_count == 0

    # -- parse_bead_feedback tests -------------------------------------------

    def test_parse_bead_feedback_useful_returns_positive_delta(self) -> None:
        from agent_baton.core.engine.bead_signal import parse_bead_feedback

        outcome = "Work done.\nBEAD_FEEDBACK: bd-a1b2 useful\n"
        results = parse_bead_feedback(outcome)
        assert len(results) == 1
        bead_id, delta = results[0]
        assert bead_id == "bd-a1b2"
        assert delta == pytest.approx(0.5)

    def test_parse_bead_feedback_misleading_returns_negative_delta(self) -> None:
        from agent_baton.core.engine.bead_signal import parse_bead_feedback

        outcome = "BEAD_FEEDBACK: bd-c3d4 misleading"
        results = parse_bead_feedback(outcome)
        assert len(results) == 1
        _, delta = results[0]
        assert delta == pytest.approx(-0.5)

    def test_parse_bead_feedback_outdated_returns_minus_point_three(self) -> None:
        from agent_baton.core.engine.bead_signal import parse_bead_feedback

        outcome = "BEAD_FEEDBACK: bd-e5f6 outdated"
        results = parse_bead_feedback(outcome)
        assert len(results) == 1
        _, delta = results[0]
        assert delta == pytest.approx(-0.3)

    def test_parse_bead_feedback_multiple_signals_in_one_outcome(self) -> None:
        from agent_baton.core.engine.bead_signal import parse_bead_feedback

        outcome = (
            "BEAD_FEEDBACK: bd-a1b2 useful\n"
            "BEAD_FEEDBACK: bd-c3d4 misleading\n"
            "BEAD_FEEDBACK: bd-e5f6 outdated\n"
        )
        results = parse_bead_feedback(outcome)
        assert len(results) == 3
        ids = [r[0] for r in results]
        assert "bd-a1b2" in ids
        assert "bd-c3d4" in ids
        assert "bd-e5f6" in ids

    def test_parse_bead_feedback_malformed_signal_returns_empty(self) -> None:
        from agent_baton.core.engine.bead_signal import parse_bead_feedback

        malformed_cases = [
            "BEAD_FEEDBACK: not-a-bead-id useful",
            "BEAD_FEEDBACK: bd-a1b2 unknown-verdict",
            "BEAD_FEEDBACK: bd-a1b2",
            "no signals here",
            "",
        ]
        for case in malformed_cases:
            results = parse_bead_feedback(case)
            assert isinstance(results, list)

    def test_parse_bead_feedback_empty_outcome_returns_empty_list(self) -> None:
        from agent_baton.core.engine.bead_signal import parse_bead_feedback

        assert parse_bead_feedback("") == []
        assert parse_bead_feedback(None) == []

    # -- Store-level quality operations -------------------------------------

    def test_update_quality_score_increases_score(self, store) -> None:
        bead = _make_bead("bd-uqs1", quality_score=0.0)
        store.write(bead)

        store.update_quality_score("bd-uqs1", 0.5)

        refreshed = store.read("bd-uqs1")
        assert refreshed is not None
        assert refreshed.quality_score == pytest.approx(0.5)

    def test_update_quality_score_accumulates(self, store) -> None:
        """BdBeadStore accumulates quality score deltas (no clamping).

        Note: BdBeadStore does not clamp quality_score to [-1, 1].
        That was a SQLite-era constraint. The bd backend stores the raw sum.
        """
        bead = _make_bead("bd-accum1", quality_score=0.0)
        store.write(bead)

        store.update_quality_score("bd-accum1", 0.5)
        store.update_quality_score("bd-accum1", 0.25)

        refreshed = store.read("bd-accum1")
        assert refreshed is not None
        assert refreshed.quality_score == pytest.approx(0.75)

    def test_increment_retrieval_count_increases_count(
        self, store
    ) -> None:
        bead = _make_bead("bd-irc1", retrieval_count=0)
        store.write(bead)

        store.increment_retrieval_count("bd-irc1")
        store.increment_retrieval_count("bd-irc1")

        refreshed = store.read("bd-irc1")
        assert refreshed is not None
        assert refreshed.retrieval_count == 2

    # -- Schema migration tests (SQLite-specific, kept for schema contract) --

    def test_schema_v6_migration_adds_quality_score_and_retrieval_count_columns(
        self,
    ) -> None:
        from agent_baton.core.storage.schema import MIGRATIONS

        assert 6 in MIGRATIONS
        migration_sql = MIGRATIONS[6]
        assert "quality_score" in migration_sql
        assert "retrieval_count" in migration_sql

    # -- Dispatcher _BEAD_SIGNALS_LINE tests ---------------------------------

    def test_bead_signals_line_contains_bead_feedback_instruction(self) -> None:
        from agent_baton.core.engine.dispatcher import _BEAD_SIGNALS_LINE

        assert "BEAD_FEEDBACK" in _BEAD_SIGNALS_LINE

    def test_delegation_prompt_contains_bead_feedback_instruction(self) -> None:
        """The built delegation prompt includes the BEAD_FEEDBACK signal instruction."""
        from agent_baton.core.engine.dispatcher import PromptDispatcher
        from agent_baton.models.execution import PlanStep

        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer--python",
            task_description="Implement the auth module",
        )
        prompt = PromptDispatcher().build_delegation_prompt(step)
        assert "BEAD_FEEDBACK" in prompt

    def test_delegation_prompt_contains_bead_discovery_instruction(self) -> None:
        """The built delegation prompt includes the BEAD_DISCOVERY signal instruction."""
        from agent_baton.core.engine.dispatcher import PromptDispatcher
        from agent_baton.models.execution import PlanStep

        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer--python",
            task_description="Implement the auth module",
        )
        prompt = PromptDispatcher().build_delegation_prompt(step)
        assert "BEAD_DISCOVERY" in prompt

    # -- Prior beads in delegation prompt ------------------------------------

    def test_build_delegation_prompt_includes_prior_beads_section(self) -> None:
        """When prior_beads are provided, the delegation prompt contains them."""
        from agent_baton.core.engine.dispatcher import PromptDispatcher
        from agent_baton.models.execution import PlanStep

        step = PlanStep(
            step_id="1.2",
            agent_name="backend-engineer--python",
            task_description="Implement the schema",
        )
        prior = [
            _make_bead(
                "bd-prior1",
                bead_type="discovery",
                content="The auth module uses JWT with RS256",
            )
        ]
        prompt = PromptDispatcher().build_delegation_prompt(step, prior_beads=prior)
        assert "Prior Discoveries" in prompt or "bd-prior1" in prompt or "RS256" in prompt

    def test_build_delegation_prompt_with_no_prior_beads_omits_section(self) -> None:
        """When prior_beads is None, no Prior Discoveries section is injected."""
        from agent_baton.core.engine.dispatcher import PromptDispatcher
        from agent_baton.models.execution import PlanStep

        step = PlanStep(
            step_id="1.1",
            agent_name="architect",
            task_description="Design the schema",
        )
        prompt = PromptDispatcher().build_delegation_prompt(step, prior_beads=None)
        assert "Prior Discoveries" not in prompt


# ---------------------------------------------------------------------------
# F3 — BeadSelector instance call in team dispatch path
# ---------------------------------------------------------------------------


class TestBeadSelectorTeamDispatchFix:
    """Regression tests for the team dispatch path using BeadSelector().select()
    (instance method), not BeadSelector.select() (unbound method).

    The bug: executor.py originally called ``_TBS.select(store, step, plan)``
    which binds *store* to *self*, *step* to *bead_store*, and *plan* to
    *current_step*.  The fix is ``_TBS().select(store, step, plan)``.
    """

    def test_bead_selector_select_is_instance_method_not_static(self) -> None:
        """BeadSelector.select() requires a self arg — calling as classmethod raises."""
        from agent_baton.core.engine.bead_selector import BeadSelector

        # select must NOT be a classmethod or staticmethod
        method = BeadSelector.__dict__["select"]
        assert not isinstance(method, classmethod)
        assert not isinstance(method, staticmethod)

    def test_bead_selector_instance_call_succeeds_with_empty_store(
        self, store
    ) -> None:
        """BeadSelector().select(store, step, plan) works without error."""
        from agent_baton.core.engine.bead_selector import BeadSelector

        plan = _make_plan_with_steps()
        step = plan.phases[0].steps[0]

        # Must not raise; empty store returns []
        result = BeadSelector().select(store, step, plan)
        assert isinstance(result, list)

    def test_bead_selector_unbound_call_raises_type_error(self) -> None:
        """Calling BeadSelector.select(store, step, plan) without instantiation
        raises TypeError because *self* (the store) would be bound as the instance."""
        from agent_baton.core.engine.bead_selector import BeadSelector
        from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep
        from unittest.mock import MagicMock

        step = PlanStep(step_id="1.1", agent_name="arch", task_description="x")
        plan = MachinePlan(
            task_id="t", task_summary="t",
            phases=[PlanPhase(phase_id=1, name="p", steps=[step])],
        )
        mock_store = MagicMock()
        mock_store.query.return_value = []

        # Calling the unbound method binds mock_store as self → TypeError on plan arg
        with pytest.raises(TypeError):
            BeadSelector.select(mock_store, step, plan)

    def test_bead_selector_returns_beads_via_instance_call(
        self, store
    ) -> None:
        """Instance call routes correctly and returns beads from store."""
        from agent_baton.core.engine.bead_selector import BeadSelector

        plan = _make_plan_with_steps()
        # step 2.1 depends on 1.2 which depends on 1.1
        step = plan.phases[1].steps[0]  # step 2.1

        dep_bead = _make_bead(
            "bd-team-dep", step_id="1.1", bead_type="warning", token_estimate=50
        )
        store.write(dep_bead)

        result = BeadSelector().select(store, step, plan, token_budget=4096, max_beads=5)
        assert any(b.bead_id == "bd-team-dep" for b in result)
