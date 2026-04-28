"""Tests for agent_baton.core.predict.classifier (Wave 6.2 Part C, bd-03b0).

Covers:
- test_classifier_outputs_strict_json_schema
- test_classifier_low_confidence_does_not_fire
- IntentClassifier auto-disable on low accept rate
- IntentKind enum values
- JSON extraction utilities
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_baton.core.predict.classifier import (
    IntentClassification,
    IntentClassifier,
    IntentKind,
    _extract_json_block,
    _parse_intent,
    _unknown_classification,
)
from agent_baton.core.predict.watcher import FileEvent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_event(tmp_path: Path, filename: str = "module.py") -> FileEvent:
    p = tmp_path / filename
    p.write_text("# code")
    return FileEvent(path=p, op="modified", ts=1.0, snapshot_hash="abc123")


def _mock_launcher(json_output: str) -> MagicMock:
    """Build a mock launcher whose async launch() returns the given text."""
    launcher = MagicMock()
    result = MagicMock()
    result.output = json_output

    async def _launch(**kwargs: Any) -> Any:
        return result

    launcher.launch = _launch
    return launcher


def _valid_directive_json(
    intent: str = "add-feature",
    confidence: float = 0.85,
    kind: str = "implement",
) -> str:
    return json.dumps({
        "intent": intent,
        "confidence": confidence,
        "scope": ["src/feature.py"],
        "summary": "Developer is adding a new authentication feature",
        "speculation_directive": {
            "kind": kind,
            "prompt": "Implement JWT authentication in src/feature.py",
            "estimated_files_changed": 2,
        },
    })


def _low_confidence_json() -> str:
    return json.dumps({
        "intent": "refactor",
        "confidence": 0.40,
        "scope": ["src/old.py"],
        "summary": "Minor refactor",
        "speculation_directive": {
            "kind": "implement",
            "prompt": "Refactor old module",
            "estimated_files_changed": 1,
        },
    })


def _none_kind_json() -> str:
    return json.dumps({
        "intent": "unknown",
        "confidence": 0.90,
        "scope": [],
        "summary": "Not sure",
        "speculation_directive": {
            "kind": "none",
            "prompt": "",
            "estimated_files_changed": 0,
        },
    })


# ---------------------------------------------------------------------------
# test_classifier_outputs_strict_json_schema
# ---------------------------------------------------------------------------


class TestClassifierJsonSchema:
    def test_valid_schema_parsed_correctly(self, tmp_path: Path) -> None:
        """Classifier parses valid JSON output into IntentClassification."""
        launcher = _mock_launcher(_valid_directive_json())
        clf = IntentClassifier(launcher=launcher, project_root=tmp_path)
        event = _make_event(tmp_path)

        result = clf.classify(event)

        assert isinstance(result, IntentClassification)
        assert result.intent == IntentKind.ADD_FEATURE
        assert result.confidence == pytest.approx(0.85)
        assert result.scope == [Path("src/feature.py")]
        assert "authentication" in result.summary
        assert result.speculation_directive is not None
        assert result.speculation_directive["kind"] == "implement"
        assert "JWT" in result.speculation_directive["prompt"]
        assert result.speculation_directive["estimated_files_changed"] == 2

    def test_fenced_code_block_json_extracted(self, tmp_path: Path) -> None:
        """JSON wrapped in ```json ... ``` fences is correctly extracted."""
        wrapped = "```json\n" + _valid_directive_json() + "\n```"
        launcher = _mock_launcher(wrapped)
        clf = IntentClassifier(launcher=launcher, project_root=tmp_path)
        event = _make_event(tmp_path)
        result = clf.classify(event)
        assert result.intent == IntentKind.ADD_FEATURE

    def test_summary_truncated_to_120_chars(self, tmp_path: Path) -> None:
        """Summary field is capped at 120 characters."""
        long_summary = "x" * 200
        data = json.dumps({
            "intent": "refactor",
            "confidence": 0.80,
            "scope": [],
            "summary": long_summary,
            "speculation_directive": {
                "kind": "implement",
                "prompt": "Do something",
                "estimated_files_changed": 1,
            },
        })
        launcher = _mock_launcher(data)
        clf = IntentClassifier(launcher=launcher, project_root=tmp_path)
        result = clf.classify(_make_event(tmp_path))
        assert len(result.summary) <= 120

    def test_prompt_truncated_to_500_chars(self, tmp_path: Path) -> None:
        """Prompt in speculation_directive is capped at 500 characters."""
        long_prompt = "P" * 600
        data = json.dumps({
            "intent": "add-feature",
            "confidence": 0.90,
            "scope": ["a.py"],
            "summary": "Short",
            "speculation_directive": {
                "kind": "implement",
                "prompt": long_prompt,
                "estimated_files_changed": 1,
            },
        })
        launcher = _mock_launcher(data)
        clf = IntentClassifier(launcher=launcher, project_root=tmp_path)
        result = clf.classify(_make_event(tmp_path))
        if result.speculation_directive:
            assert len(result.speculation_directive["prompt"]) <= 500

    def test_invalid_json_returns_unknown(self, tmp_path: Path) -> None:
        """Invalid JSON output returns UNKNOWN classification."""
        launcher = _mock_launcher("this is not json")
        clf = IntentClassifier(launcher=launcher, project_root=tmp_path)
        result = clf.classify(_make_event(tmp_path))
        assert result.intent == IntentKind.UNKNOWN
        assert result.speculation_directive is None

    def test_empty_output_returns_unknown(self, tmp_path: Path) -> None:
        """Empty launcher output returns UNKNOWN classification."""
        launcher = _mock_launcher("")
        clf = IntentClassifier(launcher=launcher, project_root=tmp_path)
        result = clf.classify(_make_event(tmp_path))
        assert result.intent == IntentKind.UNKNOWN

    def test_all_intent_kinds_parsed(self, tmp_path: Path) -> None:
        """All IntentKind values are correctly parsed from JSON."""
        for kind in IntentKind:
            launcher = _mock_launcher(_valid_directive_json(intent=kind.value))
            clf = IntentClassifier(launcher=launcher, project_root=tmp_path)
            result = clf.classify(_make_event(tmp_path))
            assert result.intent == kind


# ---------------------------------------------------------------------------
# test_classifier_low_confidence_does_not_fire
# ---------------------------------------------------------------------------


class TestClassifierFireThreshold:
    def test_low_confidence_no_directive(self, tmp_path: Path) -> None:
        """When confidence < 0.75, speculation_directive must be None."""
        launcher = _mock_launcher(_low_confidence_json())
        clf = IntentClassifier(
            launcher=launcher,
            project_root=tmp_path,
            confidence_threshold=0.75,
        )
        result = clf.classify(_make_event(tmp_path))
        assert result.speculation_directive is None

    def test_none_kind_no_directive(self, tmp_path: Path) -> None:
        """When directive kind == 'none', speculation_directive is None."""
        launcher = _mock_launcher(_none_kind_json())
        clf = IntentClassifier(launcher=launcher, project_root=tmp_path)
        result = clf.classify(_make_event(tmp_path))
        assert result.speculation_directive is None

    def test_too_many_files_no_directive(self, tmp_path: Path) -> None:
        """When estimated_files_changed > max_files_changed, no directive."""
        data = json.dumps({
            "intent": "add-feature",
            "confidence": 0.90,
            "scope": ["a.py"],
            "summary": "Big change",
            "speculation_directive": {
                "kind": "implement",
                "prompt": "Do big change",
                "estimated_files_changed": 10,
            },
        })
        launcher = _mock_launcher(data)
        clf = IntentClassifier(
            launcher=launcher,
            project_root=tmp_path,
            max_files_changed=5,
        )
        result = clf.classify(_make_event(tmp_path))
        assert result.speculation_directive is None

    def test_exactly_at_threshold_fires(self, tmp_path: Path) -> None:
        """Confidence exactly at threshold (0.75) fires a speculation."""
        data = _valid_directive_json(confidence=0.75)
        launcher = _mock_launcher(data)
        clf = IntentClassifier(
            launcher=launcher,
            project_root=tmp_path,
            confidence_threshold=0.75,
        )
        result = clf.classify(_make_event(tmp_path))
        assert result.speculation_directive is not None


# ---------------------------------------------------------------------------
# Auto-disable on low accept rate
# ---------------------------------------------------------------------------


class TestClassifierAutoDisable:
    def test_auto_disable_after_low_accept_rate(self, tmp_path: Path) -> None:
        """After 50 rejections, classifier auto-disables for 24 h."""
        launcher = _mock_launcher(_valid_directive_json())
        clf = IntentClassifier(
            launcher=launcher,
            project_root=tmp_path,
            accept_rate_window=50,
            accept_rate_min=0.20,
        )
        # Record 50 rejections.
        for _ in range(50):
            clf.record_outcome(accepted=False)

        assert not clf.is_enabled()

        # After disable, classify returns UNKNOWN.
        result = clf.classify(_make_event(tmp_path))
        assert result.intent == IntentKind.UNKNOWN
        assert result.speculation_directive is None

    def test_no_disable_when_accept_rate_above_threshold(self, tmp_path: Path) -> None:
        """Classifier stays enabled when accept-rate >= 20%."""
        launcher = _mock_launcher(_valid_directive_json())
        clf = IntentClassifier(
            launcher=launcher,
            project_root=tmp_path,
            accept_rate_window=50,
            accept_rate_min=0.20,
        )
        # 40 rejections + 10 accepts = 20% rate → just at limit.
        for _ in range(40):
            clf.record_outcome(accepted=False)
        for _ in range(10):
            clf.record_outcome(accepted=True)

        assert clf.is_enabled()

    def test_no_disable_before_full_window(self, tmp_path: Path) -> None:
        """Auto-disable does not trigger before the full window is filled."""
        launcher = _mock_launcher(_valid_directive_json())
        clf = IntentClassifier(
            launcher=launcher,
            project_root=tmp_path,
            accept_rate_window=50,
            accept_rate_min=0.20,
        )
        # Only 10 rejections — window not full yet.
        for _ in range(10):
            clf.record_outcome(accepted=False)

        assert clf.is_enabled()

    def test_no_launcher_returns_unknown(self, tmp_path: Path) -> None:
        """Classifier with no launcher always returns UNKNOWN."""
        clf = IntentClassifier(launcher=None, project_root=tmp_path)
        result = clf.classify(_make_event(tmp_path))
        assert result.intent == IntentKind.UNKNOWN


# ---------------------------------------------------------------------------
# _extract_json_block
# ---------------------------------------------------------------------------


class TestExtractJsonBlock:
    def test_bare_json(self) -> None:
        text = '{"a": 1}'
        assert _extract_json_block(text) == '{"a": 1}'

    def test_fenced_json(self) -> None:
        text = '```json\n{"a": 1}\n```'
        result = _extract_json_block(text)
        assert result == '{"a": 1}'

    def test_fenced_without_lang(self) -> None:
        text = '```\n{"b": 2}\n```'
        result = _extract_json_block(text)
        assert result == '{"b": 2}'

    def test_no_json_returns_empty(self) -> None:
        assert _extract_json_block("no json here") == ""

    def test_nested_json(self) -> None:
        text = '{"a": {"b": 1}}'
        result = _extract_json_block(text)
        assert result == '{"a": {"b": 1}}'


# ---------------------------------------------------------------------------
# _parse_intent
# ---------------------------------------------------------------------------


class TestParseIntent:
    def test_all_valid_kinds(self) -> None:
        for kind in IntentKind:
            assert _parse_intent(kind.value) == kind

    def test_unknown_fallback(self) -> None:
        assert _parse_intent("garbage") == IntentKind.UNKNOWN

    def test_empty_string_fallback(self) -> None:
        assert _parse_intent("") == IntentKind.UNKNOWN
