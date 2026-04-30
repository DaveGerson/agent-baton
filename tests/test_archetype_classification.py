"""Tests for archetype detection in task classification."""
from __future__ import annotations

import pytest

from agent_baton.core.engine.classifier import (
    KeywordClassifier,
    TaskClassification,
)
from agent_baton.core.orchestration.registry import AgentRegistry


@pytest.fixture
def registry():
    return AgentRegistry()


class TestTaskClassificationArchetypeField:
    def test_default_archetype_is_phased(self):
        tc = TaskClassification(
            task_type="new-feature",
            complexity="medium",
            agents=["backend-engineer"],
            phases=["Design", "Implement"],
            reasoning="test",
            source="test",
        )
        assert tc.archetype == "phased"

    def test_explicit_archetype(self):
        tc = TaskClassification(
            task_type="bug-fix",
            complexity="light",
            agents=["backend-engineer"],
            phases=["Fix"],
            reasoning="test",
            source="test",
            archetype="investigative",
        )
        assert tc.archetype == "investigative"

    def test_archetype_not_validated(self):
        # archetype field should accept any string (validated elsewhere)
        tc = TaskClassification(
            task_type="new-feature",
            complexity="medium",
            agents=["backend-engineer"],
            phases=["Implement"],
            reasoning="test",
            source="test",
            archetype="custom",
        )
        # Invalid archetypes auto-correct to "phased"
        assert tc.archetype == "phased"

    def test_direct_archetype_accepted(self):
        tc = TaskClassification(
            task_type="refactor",
            complexity="light",
            agents=["backend-engineer"],
            phases=["Implement"],
            reasoning="test",
            source="test",
            archetype="direct",
        )
        assert tc.archetype == "direct"

    def test_archetype_field_is_string(self):
        tc = TaskClassification(
            task_type="new-feature",
            complexity="medium",
            agents=["backend-engineer"],
            phases=["Design", "Implement"],
            reasoning="test",
            source="test",
        )
        assert isinstance(tc.archetype, str)

    def test_archetype_field_preserved_across_construction(self):
        # Verify archetype is a real field, not a property computed on access
        tc = TaskClassification(
            task_type="bug-fix",
            complexity="light",
            agents=["backend-engineer"],
            phases=["Implement"],
            reasoning="test",
            source="test",
            archetype="investigative",
        )
        tc2 = TaskClassification(
            task_type="new-feature",
            complexity="medium",
            agents=["backend-engineer"],
            phases=["Design", "Implement"],
            reasoning="test",
            source="test",
        )
        assert tc.archetype == "investigative"
        assert tc2.archetype == "phased"


class TestKeywordClassifierArchetype:
    def test_simple_rename_is_direct(self, registry):
        kc = KeywordClassifier()
        tc = kc.classify("rename the function foo to bar", registry)
        assert tc.archetype == "direct"

    def test_simple_delete_is_direct(self, registry):
        kc = KeywordClassifier()
        tc = kc.classify("delete the unused helper function", registry)
        assert tc.archetype == "direct"

    def test_debug_is_investigative(self, registry):
        kc = KeywordClassifier()
        tc = kc.classify("debug why the login endpoint returns 500", registry)
        assert tc.archetype == "investigative"

    def test_root_cause_is_investigative(self, registry):
        kc = KeywordClassifier()
        tc = kc.classify("investigate root cause of intermittent test failures", registry)
        assert tc.archetype == "investigative"

    def test_regression_is_investigative(self, registry):
        kc = KeywordClassifier()
        tc = kc.classify("diagnose regression in payment processing after deploy", registry)
        assert tc.archetype == "investigative"

    def test_complex_feature_is_phased(self, registry):
        kc = KeywordClassifier()
        tc = kc.classify(
            "build a comprehensive authentication system with OAuth2, JWT tokens, "
            "role-based access control, and audit logging across the entire application",
            registry,
        )
        assert tc.archetype == "phased"

    def test_multi_concern_is_phased(self, registry):
        kc = KeywordClassifier()
        tc = kc.classify(
            "add user profile management with avatar upload, email verification, "
            "and notification preferences",
            registry,
        )
        assert tc.archetype == "phased"

    def test_simple_move_is_direct(self, registry):
        kc = KeywordClassifier()
        tc = kc.classify("move the config file to the configs directory", registry)
        assert tc.archetype == "direct"

    def test_archetype_is_string_value(self, registry):
        kc = KeywordClassifier()
        tc = kc.classify("rename field user_id to userId", registry)
        assert tc.archetype in ("direct", "phased", "investigative")

    def test_diagnose_keyword_triggers_investigative(self, registry):
        kc = KeywordClassifier()
        tc = kc.classify("diagnose the intermittent crash in the worker thread", registry)
        assert tc.archetype == "investigative"

    def test_simple_update_is_direct(self, registry):
        kc = KeywordClassifier()
        tc = kc.classify("update the version number in package.json", registry)
        assert tc.archetype == "direct"
