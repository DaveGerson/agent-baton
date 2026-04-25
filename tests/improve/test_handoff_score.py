"""Tests for ``agent_baton.core.improve.handoff_score`` (DX.3 / bd-d136).

Coverage:
- Each of the five heuristics scored individually:
  * length_and_specificity (length floor + path/symbol token)
  * next_step (forward-looking cue)
  * blocker (mentions blocker OR explicit "no blockers")
  * branch_state (clean tree OR explicit "uncommitted")
  * test_state (mentions test status)
- Per-heuristic max contribution is 0.2.
- Empty/short notes score 0.0 with all suggestions populated.
- A "perfect" note clears 1.0.
- Suggestions list is empty when total == 1.0.
- PlanState parameter is accepted but does not affect score (currently).
"""
from __future__ import annotations

import pytest

from agent_baton.core.improve.handoff_score import (
    PER_HEURISTIC_MAX,
    BranchState,
    HandoffScore,
    PlanState,
    score_handoff,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CLEAN_BRANCH = BranchState(branch="master", commits_ahead=0, dirty=False)
DIRTY_BRANCH = BranchState(branch="feature/xyz", commits_ahead=2, dirty=True)


def _padding(extra_chars: int = 110) -> str:
    """Return at least *extra_chars* of bland filler so a note clears the
    100-char length floor without accidentally tripping any heuristic."""
    return "x" * extra_chars


# ---------------------------------------------------------------------------
# length_and_specificity
# ---------------------------------------------------------------------------


def test_length_and_specificity_short_note_scores_zero():
    score = score_handoff("too short", CLEAN_BRANCH)
    assert score.breakdown["length_and_specificity"] == 0.0
    assert any("100 characters" in s for s in score.suggestions)


def test_length_and_specificity_long_but_no_path_or_symbol_scores_zero():
    note = "the quick brown fox jumps over the lazy dog " * 4  # >100 chars, no symbols
    score = score_handoff(note, CLEAN_BRANCH)
    assert score.breakdown["length_and_specificity"] == 0.0


def test_length_and_specificity_with_path_scores_full():
    note = "Did work on agent_baton/core/foo.py and surrounding modules. " + _padding(60)
    score = score_handoff(note, CLEAN_BRANCH)
    assert score.breakdown["length_and_specificity"] == PER_HEURISTIC_MAX


def test_length_and_specificity_with_symbol_scores_full():
    note = "Refactored some_helper_function in the engine package today. " + _padding(60)
    score = score_handoff(note, CLEAN_BRANCH)
    assert score.breakdown["length_and_specificity"] == PER_HEURISTIC_MAX


# ---------------------------------------------------------------------------
# next_step
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cue", ["next", "then", "remaining", "todo", "tomorrow", "continue"])
def test_next_step_recognises_cues(cue: str):
    note = f"Did work; {cue}: write tests. " + _padding(80)
    score = score_handoff(note, CLEAN_BRANCH)
    assert score.breakdown["next_step"] == PER_HEURISTIC_MAX


def test_next_step_missing_cue_scores_zero():
    note = "Implemented the feature, committed, pushed. " + _padding(80)
    score = score_handoff(note, CLEAN_BRANCH)
    assert score.breakdown["next_step"] == 0.0


# ---------------------------------------------------------------------------
# blocker
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kw", ["blocker", "stuck", "fail", "error", "issue"])
def test_blocker_recognises_blocker_keywords(kw: str):
    note = f"Hit a {kw} when wiring the storage layer. " + _padding(80)
    score = score_handoff(note, CLEAN_BRANCH)
    assert score.breakdown["blocker"] == PER_HEURISTIC_MAX


@pytest.mark.parametrize("kw", ["none", "no blockers", "clean"])
def test_blocker_recognises_explicit_no_blockers(kw: str):
    note = f"All paths exercised; blockers: {kw}. " + _padding(80)
    score = score_handoff(note, CLEAN_BRANCH)
    assert score.breakdown["blocker"] == PER_HEURISTIC_MAX


def test_blocker_missing_scores_zero():
    note = "Wrote the schema migration and the store module. " + _padding(80)
    score = score_handoff(note, CLEAN_BRANCH)
    assert score.breakdown["blocker"] == 0.0


# ---------------------------------------------------------------------------
# branch_state
# ---------------------------------------------------------------------------


def test_branch_state_clean_scores_full_regardless_of_note():
    score = score_handoff("anything " * 30, CLEAN_BRANCH)
    assert score.breakdown["branch_state"] == PER_HEURISTIC_MAX


def test_branch_state_dirty_without_acknowledgement_scores_zero():
    note = "Implemented the feature. " + _padding(80)
    score = score_handoff(note, DIRTY_BRANCH)
    assert score.breakdown["branch_state"] == 0.0


def test_branch_state_dirty_with_acknowledgement_scores_full():
    note = "Implemented the feature; uncommitted changes remain. " + _padding(60)
    score = score_handoff(note, DIRTY_BRANCH)
    assert score.breakdown["branch_state"] == PER_HEURISTIC_MAX


# ---------------------------------------------------------------------------
# test_state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phrase", [
    "tests passing",
    "all tests passed",
    "10 pass",
    "3 failures remaining",
    "some failing tests",
    "tests green",
])
def test_test_state_recognises_status_phrases(phrase: str):
    note = f"Did work; {phrase}. " + _padding(80)
    score = score_handoff(note, CLEAN_BRANCH)
    assert score.breakdown["test_state"] == PER_HEURISTIC_MAX


def test_test_state_missing_scores_zero():
    note = "Wrote the migration and ran the linter. " + _padding(80)
    score = score_handoff(note, CLEAN_BRANCH)
    assert score.breakdown["test_state"] == 0.0


# ---------------------------------------------------------------------------
# Aggregate behaviour
# ---------------------------------------------------------------------------


def test_empty_note_scores_zero_with_all_suggestions():
    score = score_handoff("", DIRTY_BRANCH)
    assert score.total == 0.0
    # Every heuristic should produce a suggestion.
    assert len(score.suggestions) == 5


def test_perfect_note_scores_one_with_no_suggestions():
    note = (
        "Wired HandoffStore and the v18 migration in agent_baton/core/storage/handoff_store.py. "
        "Tests passing locally; no blockers. Next: finish the CLI list/show formatting tomorrow."
    )
    score = score_handoff(note, CLEAN_BRANCH)
    assert score.total == pytest.approx(1.0)
    assert score.suggestions == []
    assert all(v == PER_HEURISTIC_MAX for v in score.breakdown.values())


def test_total_is_sum_of_breakdown():
    note = "Did work on foo_bar.py. Tests passing. " + _padding(80)
    score = score_handoff(note, CLEAN_BRANCH)
    assert score.total == pytest.approx(sum(score.breakdown.values()))


def test_score_returns_handoff_score_dataclass():
    score = score_handoff("anything", CLEAN_BRANCH)
    assert isinstance(score, HandoffScore)
    # All five heuristic names are present in the breakdown.
    assert set(score.breakdown.keys()) == {
        "length_and_specificity", "next_step", "blocker",
        "branch_state", "test_state",
    }


def test_plan_state_argument_is_accepted_and_ignored():
    note = "Did work on foo_bar.py. Next: continue. No blockers. Tests passing. " + _padding(80)
    plan = PlanState(task_id="t1", phase_id=1, steps_total=4, steps_complete=2)
    a = score_handoff(note, CLEAN_BRANCH, plan_state=plan)
    b = score_handoff(note, CLEAN_BRANCH, plan_state=None)
    assert a.total == b.total
    assert a.breakdown == b.breakdown
