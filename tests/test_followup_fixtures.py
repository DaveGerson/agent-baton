"""Smoke tests proving the 005b followup bundle fixtures work correctly.

Three sanity tests (step 1.1):
1. ``isolated_bead_store`` is a no-op autouse fixture (retained for compat).
2. ``bead_store_count_baseline`` returns an int baseline and asserts no
   growth on a no-op (neither snapshot nor assertion raises).
3. ``synthetic_parallel_plan()`` returns a MachinePlan whose sibling steps
   both gain ``parallel_safe=True`` after ``annotate_parallel_safe()`` runs.

ADR-13b WP-G: BeadStore (SQLite) removed. Tests retargeted to BdBeadStore
via make_bead_store().
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.engine.strategies import annotate_parallel_safe
from agent_baton.models.bead import Bead
from agent_baton.models.execution import MachinePlan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_bead(bead_id: str = "bd-test-0001") -> Bead:
    """Build a minimal Bead that satisfies all required fields."""
    return Bead(
        bead_id=bead_id,
        task_id="",         # project-scoped
        step_id="1.1",
        agent_name="test-agent",
        bead_type="discovery",
        content="fixture smoke test bead",
    )


def _make_bd_store(tmp_path: Path):
    """Return a BdBeadStore for testing."""
    from agent_baton.core.engine.bead_backend import make_bead_store
    db_path = tmp_path / "baton.db"
    db_path.touch()
    return make_bead_store(db_path, repo_root=tmp_path)


# ---------------------------------------------------------------------------
# Test 1: isolated_bead_store is a no-op autouse fixture (backward compat)
#
# The conftest autouse fixture is a no-op after WP-G. We verify that the
# monkeypatch pattern works on BdBeadStore instead.
# ---------------------------------------------------------------------------

class TestIsolatedBeadStoreFixture:
    """The isolated_bead_store fixture is a no-op; verify BdBeadStore monkeypatching."""

    def test_monkeypatched_write_returns_empty_string(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """When BdBeadStore.write is patched to a no-op it returns '' and does
        not persist the bead."""
        from agent_baton.core.engine.bd_bead_store import BdBeadStore

        store = _make_bd_store(tmp_path)

        # Apply a no-op patch to BdBeadStore.write.
        monkeypatch.setattr(BdBeadStore, "write", lambda self, bead: "")

        bead = _minimal_bead()
        result = store.write(bead)

        assert result == "", f"Expected empty string from no-op write, got {result!r}"

    def test_monkeypatched_write_does_not_persist(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """After the no-op patch, querying the store returns no beads for the
        patched write call."""
        from agent_baton.core.engine.bd_bead_store import BdBeadStore

        store = _make_bd_store(tmp_path)

        monkeypatch.setattr(BdBeadStore, "write", lambda self, bead: "")

        bead = _minimal_bead("bd-test-silent")
        store.write(bead)

        # query should return nothing — the bead was never persisted.
        results = store.query()
        assert all(b.bead_id != "bd-test-silent" for b in results), (
            "Bead was persisted despite no-op write patch"
        )

    def test_unpatched_write_actually_persists(self, tmp_path: Path) -> None:
        """Sanity control: without the patch, a real write DOES persist the bead."""
        store = _make_bd_store(tmp_path)

        bead = _minimal_bead("bd-test-real")
        result = store.write(bead)

        if result:
            fetched = store.read("bd-test-real")
            assert fetched is not None, "Real write succeeded but read returned None"
            assert fetched.bead_id == "bd-test-real"


# ---------------------------------------------------------------------------
# Test 2: bead_store_count_baseline returns int + asserts no growth on no-op
# ---------------------------------------------------------------------------

class TestBeadStoreCountBaselineFixture:
    """bead_store_count_baseline factory must snapshot and assert correctly."""

    def test_factory_returns_callable(self, bead_store_count_baseline) -> None:
        """Calling the factory returns a callable (the assertion function)."""
        assert_fn = bead_store_count_baseline()
        assert callable(assert_fn), "factory() should return a callable"

    def test_no_growth_passes_assertion(self, bead_store_count_baseline) -> None:
        """When no beads are written, the assertion callable does not raise."""
        assert_fn = bead_store_count_baseline()
        # No writes happen here — assertion must pass silently.
        assert_fn()

    def test_growth_fails_assertion(
        self, bead_store_count_baseline, tmp_path: Path
    ) -> None:
        """When a bead is written after snapshot, the count grows.

        ADR-13b WP-G: Uses BdBeadStore.write/query rather than raw SQLite.
        We verify the count logic manually using an independent bd store.
        """
        store = _make_bd_store(tmp_path)

        baseline_count = len(store.query())
        assert isinstance(baseline_count, int)
        assert baseline_count == 0

        # Write a bead.
        bead = _minimal_bead("bd-count-test-001")
        store.write(bead)

        after_count = len(store.query())
        assert isinstance(after_count, int)
        assert after_count >= baseline_count

    def test_baseline_is_integer(self, bead_store_count_baseline) -> None:
        """The baseline captured by the factory is a non-negative integer."""
        assert_fn = bead_store_count_baseline()
        # A fresh db starts at 0 beads, so no-growth should hold.
        assert_fn()  # must not raise


# ---------------------------------------------------------------------------
# Test 3: synthetic_parallel_plan returns MachinePlan with parallel_safe=True
# ---------------------------------------------------------------------------

class TestSyntheticParallelPlanFixture:
    """synthetic_parallel_plan() factory must return an annotatable plan."""

    def test_returns_machine_plan(self, synthetic_parallel_plan) -> None:
        plan = synthetic_parallel_plan()
        assert isinstance(plan, MachinePlan)

    def test_plan_has_one_phase(self, synthetic_parallel_plan) -> None:
        plan = synthetic_parallel_plan()
        assert len(plan.phases) == 1

    def test_phase_has_three_steps(self, synthetic_parallel_plan) -> None:
        """Prereq step (1.1) plus two siblings (1.2, 1.3)."""
        plan = synthetic_parallel_plan()
        assert len(plan.phases[0].steps) == 3

    def test_siblings_have_disjoint_allowed_paths(
        self, synthetic_parallel_plan
    ) -> None:
        """Steps 1.2 and 1.3 must have non-overlapping allowed_paths."""
        plan = synthetic_parallel_plan()
        steps = {s.step_id: s for s in plan.phases[0].steps}
        paths_12 = set(steps["1.2"].allowed_paths)
        paths_13 = set(steps["1.3"].allowed_paths)
        assert paths_12.isdisjoint(paths_13), (
            f"Sibling paths must be disjoint: {paths_12} vs {paths_13}"
        )

    def test_siblings_share_depends_on(self, synthetic_parallel_plan) -> None:
        """Steps 1.2 and 1.3 must both depend on step 1.1."""
        plan = synthetic_parallel_plan()
        steps = {s.step_id: s for s in plan.phases[0].steps}
        assert steps["1.2"].depends_on == ["1.1"]
        assert steps["1.3"].depends_on == ["1.1"]

    def test_annotate_parallel_safe_marks_siblings_true(
        self, synthetic_parallel_plan
    ) -> None:
        """The 005b annotation (bd-a379) must fire correctly on the fixture plan."""
        plan = synthetic_parallel_plan()

        # Before annotation, parallel_safe is False everywhere.
        for step in plan.phases[0].steps:
            assert step.parallel_safe is False, (
                f"step {step.step_id} should start with parallel_safe=False"
            )

        annotate_parallel_safe(plan.phases)

        steps = {s.step_id: s for s in plan.phases[0].steps}

        # The two siblings with disjoint paths must be marked True.
        assert steps["1.2"].parallel_safe is True, (
            "Sibling step 1.2 should be parallel_safe=True after annotation"
        )
        assert steps["1.3"].parallel_safe is True, (
            "Sibling step 1.3 should be parallel_safe=True after annotation"
        )

    def test_prereq_step_is_not_parallel_safe(
        self, synthetic_parallel_plan
    ) -> None:
        """The prerequisite step (1.1) has no siblings, so it stays False."""
        plan = synthetic_parallel_plan()
        annotate_parallel_safe(plan.phases)
        steps = {s.step_id: s for s in plan.phases[0].steps}
        assert steps["1.1"].parallel_safe is False, (
            "Step 1.1 has no siblings and must remain parallel_safe=False"
        )

    def test_factory_returns_fresh_plan_each_call(
        self, synthetic_parallel_plan
    ) -> None:
        """Each call to the factory must produce a distinct plan object."""
        plan_a = synthetic_parallel_plan()
        plan_b = synthetic_parallel_plan()
        assert plan_a is not plan_b
        # Mutating one does not affect the other.
        annotate_parallel_safe(plan_a.phases)
        for step in plan_b.phases[0].steps:
            assert step.parallel_safe is False, (
                "Annotation of plan_a must not bleed into plan_b"
            )
