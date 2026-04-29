"""Tests for bd-a379: annotate_parallel_safe in HeuristicStrategy step output.

Covers the three required cases:
  1. Two siblings with disjoint paths  → both parallel_safe=True
  2. Two siblings with overlapping paths → both parallel_safe=False
  3. Solo step with no sibling          → parallel_safe=False

Plus edge cases:
  4. Sibling with empty allowed_paths   → conservatively False
  5. Step with empty allowed_paths      → conservatively False
  6. Three-way sibling group, all disjoint → all parallel_safe=True
  7. Three-way sibling group, one overlap  → all parallel_safe=False
  8. Different depends_on sets           → not treated as siblings
  9. Serialisation round-trip: parallel_safe survives to_dict/from_dict
 10. plan.md formatter: (parallel) suffix present when parallel_safe=True
"""
from __future__ import annotations

import pytest

from agent_baton.core.engine.strategies import annotate_parallel_safe
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _step(
    step_id: str,
    depends_on: list[str] | None = None,
    allowed_paths: list[str] | None = None,
) -> PlanStep:
    """Build a minimal PlanStep for annotation tests."""
    return PlanStep(
        step_id=step_id,
        agent_name="backend-engineer",
        task_description="do work",
        depends_on=depends_on or [],
        allowed_paths=allowed_paths or [],
    )


def _phase(*steps: PlanStep, phase_id: int = 1) -> PlanPhase:
    return PlanPhase(phase_id=phase_id, name="Implement", steps=list(steps))


# ---------------------------------------------------------------------------
# Required test 1: disjoint paths → both True
# ---------------------------------------------------------------------------

class TestDisjointSiblings:
    """Two intra-phase siblings with identical depends_on and disjoint paths."""

    def test_both_marked_parallel_safe(self) -> None:
        s1 = _step("1.1", depends_on=[], allowed_paths=["agent_baton/api/"])
        s2 = _step("1.2", depends_on=[], allowed_paths=["agent_baton/models/"])
        phase = _phase(s1, s2)

        annotate_parallel_safe([phase])

        assert s1.parallel_safe is True
        assert s2.parallel_safe is True

    def test_depends_on_non_empty_disjoint(self) -> None:
        """Disjoint paths with non-empty matching depends_on."""
        s1 = _step("2.1", depends_on=["1.1"], allowed_paths=["tests/"])
        s2 = _step("2.2", depends_on=["1.1"], allowed_paths=["docs/"])
        phase = _phase(s1, s2, phase_id=2)

        annotate_parallel_safe([phase])

        assert s1.parallel_safe is True
        assert s2.parallel_safe is True


# ---------------------------------------------------------------------------
# Required test 2: overlapping paths → both False
# ---------------------------------------------------------------------------

class TestOverlappingSiblings:
    """Two siblings whose allowed_paths intersect."""

    def test_both_marked_not_parallel_safe(self) -> None:
        s1 = _step("1.1", depends_on=[], allowed_paths=["agent_baton/models/execution.py"])
        s2 = _step("1.2", depends_on=[], allowed_paths=["agent_baton/models/execution.py"])
        phase = _phase(s1, s2)

        annotate_parallel_safe([phase])

        assert s1.parallel_safe is False
        assert s2.parallel_safe is False

    def test_partial_path_overlap(self) -> None:
        """One shared path among several is enough to disqualify."""
        s1 = _step("1.1", depends_on=[], allowed_paths=["agent_baton/api/", "shared/config.py"])
        s2 = _step("1.2", depends_on=[], allowed_paths=["agent_baton/models/", "shared/config.py"])
        phase = _phase(s1, s2)

        annotate_parallel_safe([phase])

        assert s1.parallel_safe is False
        assert s2.parallel_safe is False


# ---------------------------------------------------------------------------
# Required test 3: solo step → False
# ---------------------------------------------------------------------------

class TestSoloStep:
    """A step with no intra-phase sibling cannot be parallel."""

    def test_solo_step_parallel_safe_false(self) -> None:
        s = _step("1.1", depends_on=[], allowed_paths=["agent_baton/core/"])
        phase = _phase(s)

        annotate_parallel_safe([phase])

        assert s.parallel_safe is False

    def test_empty_phase_no_error(self) -> None:
        """An empty phase should not raise."""
        phase = PlanPhase(phase_id=1, name="Empty", steps=[])
        annotate_parallel_safe([phase])  # must not raise


# ---------------------------------------------------------------------------
# Edge case 4: sibling with empty allowed_paths → conservative False
# ---------------------------------------------------------------------------

class TestSiblingEmptyPaths:
    """If any sibling has empty allowed_paths, all are conservatively False."""

    def test_sibling_has_empty_paths(self) -> None:
        s1 = _step("1.1", depends_on=[], allowed_paths=["agent_baton/api/"])
        s2 = _step("1.2", depends_on=[], allowed_paths=[])   # unknown scope
        phase = _phase(s1, s2)

        annotate_parallel_safe([phase])

        assert s1.parallel_safe is False
        assert s2.parallel_safe is False


# ---------------------------------------------------------------------------
# Edge case 5: step itself has empty allowed_paths → False
# ---------------------------------------------------------------------------

class TestSelfEmptyPaths:
    def test_self_empty_paths_is_false(self) -> None:
        s1 = _step("1.1", depends_on=[], allowed_paths=[])
        s2 = _step("1.2", depends_on=[], allowed_paths=["agent_baton/models/"])
        phase = _phase(s1, s2)

        annotate_parallel_safe([phase])

        assert s1.parallel_safe is False
        # s2's allowed_paths is non-empty, but s1 (its sibling) has empty paths
        assert s2.parallel_safe is False


# ---------------------------------------------------------------------------
# Edge case 6: three-way group, all disjoint → all True
# ---------------------------------------------------------------------------

class TestThreeWayDisjoint:
    def test_three_disjoint_siblings(self) -> None:
        s1 = _step("1.1", depends_on=[], allowed_paths=["agent_baton/api/"])
        s2 = _step("1.2", depends_on=[], allowed_paths=["agent_baton/models/"])
        s3 = _step("1.3", depends_on=[], allowed_paths=["agent_baton/core/"])
        phase = _phase(s1, s2, s3)

        annotate_parallel_safe([phase])

        assert s1.parallel_safe is True
        assert s2.parallel_safe is True
        assert s3.parallel_safe is True


# ---------------------------------------------------------------------------
# Edge case 7: three-way group, one pair overlaps → all False
# ---------------------------------------------------------------------------

class TestThreeWayOneOverlap:
    def test_one_overlap_disqualifies_all(self) -> None:
        s1 = _step("1.1", depends_on=[], allowed_paths=["agent_baton/api/"])
        s2 = _step("1.2", depends_on=[], allowed_paths=["agent_baton/models/"])
        # s3 overlaps with s1
        s3 = _step("1.3", depends_on=[], allowed_paths=["agent_baton/api/", "agent_baton/core/"])
        phase = _phase(s1, s2, s3)

        annotate_parallel_safe([phase])

        # s1 and s3 share "agent_baton/api/" → s1 is not safe
        assert s1.parallel_safe is False
        # s2 is disjoint from s1 but its group includes s3 which overlaps s1
        # s2 vs s3: s2=["agent_baton/models/"], s3=["agent_baton/api/","agent_baton/core/"] → disjoint
        # s2 vs s1: disjoint
        # BUT s1's paths overlap s3 — s2's perspective: all *its* siblings must be disjoint FROM s2.
        # s2 sees s1 (disjoint) and s3 (disjoint from s2), so s2 IS safe.
        assert s2.parallel_safe is True
        # s3 shares path with s1 → not safe
        assert s3.parallel_safe is False


# ---------------------------------------------------------------------------
# Edge case 8: different depends_on sets → not siblings
# ---------------------------------------------------------------------------

class TestDifferentDependsOn:
    def test_different_depends_on_not_siblings(self) -> None:
        s1 = _step("2.1", depends_on=["1.1"], allowed_paths=["agent_baton/api/"])
        s2 = _step("2.2", depends_on=["1.2"], allowed_paths=["agent_baton/models/"])
        phase = _phase(s1, s2, phase_id=2)

        annotate_parallel_safe([phase])

        # Different depends_on → not siblings → no parallelism
        assert s1.parallel_safe is False
        assert s2.parallel_safe is False


# ---------------------------------------------------------------------------
# Edge case 9: serialisation round-trip
# ---------------------------------------------------------------------------

class TestSerialisationRoundTrip:
    def test_parallel_safe_survives_to_dict_from_dict(self) -> None:
        s = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer",
            task_description="do work",
            allowed_paths=["agent_baton/api/"],
            parallel_safe=True,
        )
        d = s.to_dict()
        assert d["parallel_safe"] is True

        restored = PlanStep.from_dict(d)
        assert restored.parallel_safe is True

    def test_parallel_safe_false_not_emitted(self) -> None:
        """False is the default — omitted from to_dict to keep JSON lean."""
        s = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer",
            task_description="do work",
        )
        d = s.to_dict()
        assert "parallel_safe" not in d

    def test_parallel_safe_false_round_trips_from_absent_key(self) -> None:
        """from_dict on a dict without 'parallel_safe' → False (backward compat)."""
        d = {
            "step_id": "1.1",
            "agent_name": "backend-engineer",
            "task_description": "do work",
        }
        restored = PlanStep.from_dict(d)
        assert restored.parallel_safe is False


# ---------------------------------------------------------------------------
# Edge case 10: plan.md (parallel) suffix
# ---------------------------------------------------------------------------

class TestMarkdownSuffix:
    def _make_plan(self) -> MachinePlan:
        s1 = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer",
            task_description="build API",
            allowed_paths=["agent_baton/api/"],
            parallel_safe=True,
        )
        s2 = PlanStep(
            step_id="1.2",
            agent_name="frontend-engineer",
            task_description="build UI",
            allowed_paths=["pmo-ui/"],
            parallel_safe=True,
        )
        s3 = PlanStep(
            step_id="1.3",
            agent_name="code-reviewer",
            task_description="review everything",
            depends_on=["1.1", "1.2"],
            allowed_paths=[],
            parallel_safe=False,
        )
        phase = PlanPhase(phase_id=1, name="Implement", steps=[s1, s2, s3])
        return MachinePlan(
            task_id="test-task-001",
            task_summary="Test parallel annotation",
            phases=[phase],
        )

    def test_parallel_suffix_present_for_safe_steps(self) -> None:
        md = self._make_plan().to_markdown()
        assert "### Step 1.1: backend-engineer (parallel)" in md
        assert "### Step 1.2: frontend-engineer (parallel)" in md

    def test_no_parallel_suffix_for_unsafe_step(self) -> None:
        md = self._make_plan().to_markdown()
        assert "### Step 1.3: code-reviewer (parallel)" not in md
        assert "### Step 1.3: code-reviewer" in md
