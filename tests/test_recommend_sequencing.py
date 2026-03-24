"""Tests for PatternLearner.recommend_sequencing() enhancement."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.core.learn.pattern_learner import PatternLearner
from agent_baton.models.pattern import LearnedPattern


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pattern(
    pattern_id: str,
    task_type: str,
    agents: list[str],
    confidence: float = 0.9,
    success_rate: float = 0.95,
) -> LearnedPattern:
    return LearnedPattern(
        pattern_id=pattern_id,
        task_type=task_type,
        stack=None,
        recommended_template="test workflow",
        recommended_agents=agents,
        confidence=confidence,
        sample_size=10,
        success_rate=success_rate,
        avg_token_cost=50_000,
        evidence=["t1", "t2"],
        created_at="2026-03-01",
        updated_at="2026-03-01",
    )


def _write_patterns(root: Path, patterns: list[LearnedPattern]) -> None:
    path = root / "learned-patterns.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps([p.to_dict() for p in patterns], indent=2)
    path.write_text(payload + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRecommendSequencing:
    def test_returns_none_when_no_patterns(self, tmp_path: Path):
        learner = PatternLearner(team_context_root=tmp_path)
        assert learner.recommend_sequencing("unknown_type") is None

    def test_returns_best_agents_and_confidence(self, tmp_path: Path):
        patterns = [
            _pattern("p1", "phased", ["architect", "backend"], confidence=0.95),
            _pattern("p2", "phased", ["backend", "test"], confidence=0.8),
        ]
        _write_patterns(tmp_path, patterns)
        learner = PatternLearner(team_context_root=tmp_path)

        result = learner.recommend_sequencing("phased")
        assert result is not None
        agents, confidence = result
        assert agents == ["architect", "backend"]  # Highest confidence
        assert confidence == 0.95

    def test_returns_none_for_unmatched_task_type(self, tmp_path: Path):
        patterns = [
            _pattern("p1", "phased", ["architect"], confidence=0.9),
        ]
        _write_patterns(tmp_path, patterns)
        learner = PatternLearner(team_context_root=tmp_path)
        assert learner.recommend_sequencing("waterfall") is None

    def test_single_pattern_returns_its_agents(self, tmp_path: Path):
        patterns = [
            _pattern("p1", "simple", ["backend"], confidence=0.85),
        ]
        _write_patterns(tmp_path, patterns)
        learner = PatternLearner(team_context_root=tmp_path)

        result = learner.recommend_sequencing("simple")
        assert result is not None
        agents, confidence = result
        assert agents == ["backend"]
        assert confidence == 0.85
