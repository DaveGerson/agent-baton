"""Tests for agent_baton.core.classifier.DataClassifier and ClassificationResult."""
from __future__ import annotations

import pytest

from agent_baton.core.classifier import ClassificationResult, DataClassifier
from agent_baton.models.enums import RiskLevel


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def classifier() -> DataClassifier:
    return DataClassifier()


# ---------------------------------------------------------------------------
# No-signal baseline
# ---------------------------------------------------------------------------


class TestNoSignals:
    def test_empty_description_returns_low(self, classifier: DataClassifier) -> None:
        result = classifier.classify("")
        assert result.risk_level == RiskLevel.LOW

    def test_plain_description_returns_low(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Add a helper function to format dates")
        assert result.risk_level == RiskLevel.LOW

    def test_no_signals_preset_is_standard(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Refactor the widget component")
        assert result.guardrail_preset == "Standard Development"

    def test_no_signals_confidence_is_high(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Fix a typo in the README")
        assert result.confidence == "high"

    def test_no_signals_explanation_mentions_standard(
        self, classifier: DataClassifier
    ) -> None:
        result = classifier.classify("Update sorting logic")
        assert "Standard development" in result.explanation or "No sensitivity" in result.explanation

    def test_no_signals_signals_list_is_empty(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Create a new API endpoint for search")
        # "api" is not in any signal list so this should be empty or have no matches
        assert all(
            not s.startswith(("regulated:", "pii:", "security:", "infra:", "database:"))
            for s in result.signals_found
        )


# ---------------------------------------------------------------------------
# Regulated signals → HIGH + Regulated Data
# ---------------------------------------------------------------------------


class TestRegulatedSignals:
    def test_compliance_keyword(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Handle compliance reporting for the audit system")
        assert result.risk_level == RiskLevel.HIGH
        assert result.guardrail_preset == "Regulated Data"

    def test_hipaa_keyword(self, classifier: DataClassifier) -> None:
        # "hipaa" alone triggers one regulated signal → HIGH.
        result = classifier.classify("Ensure HIPAA requirements are satisfied")
        assert result.risk_level == RiskLevel.HIGH
        assert result.guardrail_preset == "Regulated Data"

    def test_gdpr_triggers_regulated(self, classifier: DataClassifier) -> None:
        # gdpr appears in both PII_SIGNALS and REGULATED_SIGNALS;
        # either way the result should be HIGH + Regulated Data.
        result = classifier.classify("Implement GDPR data deletion endpoint")
        assert result.risk_level == RiskLevel.HIGH
        assert result.guardrail_preset == "Regulated Data"

    def test_audit_trail_keyword(self, classifier: DataClassifier) -> None:
        # "audit trail" triggers both "audit" and "audit trail" signals (2 total) → HIGH.
        result = classifier.classify("Build an audit trail for the new feature")
        assert result.risk_level == RiskLevel.HIGH
        assert result.guardrail_preset == "Regulated Data"

    def test_regulated_signal_captured(self, classifier: DataClassifier) -> None:
        result = classifier.classify("This system handles regulated financial data")
        assert any(s.startswith("regulated:") for s in result.signals_found)


# ---------------------------------------------------------------------------
# PII signals → HIGH + Regulated Data
# ---------------------------------------------------------------------------


class TestPIISignals:
    def test_pii_keyword(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Anonymise PII before exporting to the data lake")
        assert result.risk_level == RiskLevel.HIGH
        assert result.guardrail_preset == "Regulated Data"

    def test_ssn_keyword(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Store SSN in the employee record")
        assert result.risk_level == RiskLevel.HIGH
        assert result.guardrail_preset == "Regulated Data"

    def test_credit_card_keyword(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Tokenize credit card before persisting")
        assert result.risk_level == RiskLevel.HIGH
        assert result.guardrail_preset == "Regulated Data"

    def test_patient_keyword(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Migrate patient records to new schema")
        assert result.risk_level == RiskLevel.HIGH
        assert result.guardrail_preset == "Regulated Data"

    def test_pii_signal_captured(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Handle personal data deletion requests")
        assert any(s.startswith("pii:") for s in result.signals_found)


# ---------------------------------------------------------------------------
# Security signals → HIGH + Security-Sensitive
# ---------------------------------------------------------------------------


class TestSecuritySignals:
    def test_authentication_keyword(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Refactor the authentication flow to use JWT")
        assert result.risk_level == RiskLevel.HIGH
        assert result.guardrail_preset == "Security-Sensitive"

    def test_jwt_keyword(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Add JWT refresh token rotation")
        assert result.risk_level == RiskLevel.HIGH
        assert result.guardrail_preset == "Security-Sensitive"

    def test_oauth_keyword(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Integrate OAuth 2.0 login flow")
        assert result.risk_level == RiskLevel.HIGH
        assert result.guardrail_preset == "Security-Sensitive"

    def test_secrets_keyword(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Rotate secrets and update API keys in vault")
        assert result.risk_level == RiskLevel.HIGH
        assert result.guardrail_preset == "Security-Sensitive"

    def test_security_signal_captured(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Harden SSL/TLS configuration on the gateway")
        assert any(s.startswith("security:") for s in result.signals_found)

    def test_security_does_not_override_regulated(
        self, classifier: DataClassifier
    ) -> None:
        # If regulated fires first, security should not downgrade the preset.
        result = classifier.classify("Compliance audit authentication tokens")
        assert result.guardrail_preset == "Regulated Data"


# ---------------------------------------------------------------------------
# Infrastructure signals → HIGH + Infrastructure Changes
# ---------------------------------------------------------------------------


class TestInfrastructureSignals:
    def test_docker_keyword(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Update the Docker base image to python:3.12-slim")
        assert result.risk_level == RiskLevel.HIGH
        assert result.guardrail_preset == "Infrastructure Changes"

    def test_terraform_keyword(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Add terraform module for S3 bucket")
        assert result.risk_level == RiskLevel.HIGH
        assert result.guardrail_preset == "Infrastructure Changes"

    def test_kubernetes_keyword(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Configure kubernetes horizontal pod autoscaler")
        assert result.risk_level == RiskLevel.HIGH
        assert result.guardrail_preset == "Infrastructure Changes"

    def test_deploy_keyword(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Create deploy script for production release")
        assert result.risk_level == RiskLevel.HIGH
        assert result.guardrail_preset == "Infrastructure Changes"

    def test_infra_signal_captured(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Set up CI/CD pipeline for the new service")
        assert any(s.startswith("infra:") for s in result.signals_found)

    def test_infra_does_not_override_regulated(
        self, classifier: DataClassifier
    ) -> None:
        result = classifier.classify("Deploy the compliance reporting infrastructure")
        # regulated fires first → Regulated Data should win
        assert result.guardrail_preset == "Regulated Data"


# ---------------------------------------------------------------------------
# Database signals → MEDIUM + Standard Development
# ---------------------------------------------------------------------------


class TestDatabaseSignals:
    def test_migration_keyword(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Write a migration to add the created_at column")
        assert result.risk_level == RiskLevel.MEDIUM
        assert result.guardrail_preset == "Standard Development"

    def test_schema_keyword(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Update the database schema for the orders table")
        assert result.risk_level == RiskLevel.MEDIUM

    def test_database_keyword(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Optimise the database index on the users table")
        assert result.risk_level == RiskLevel.MEDIUM

    def test_drop_keyword(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Drop the legacy column from the products table")
        assert result.risk_level == RiskLevel.MEDIUM

    def test_database_signal_captured(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Add foreign key constraint between orders and users")
        assert any(s.startswith("database:") for s in result.signals_found)

    def test_database_does_not_override_high(self, classifier: DataClassifier) -> None:
        # A HIGH signal should remain HIGH even when database signals are also present.
        result = classifier.classify("Migrate the compliance audit schema")
        assert result.risk_level == RiskLevel.HIGH


# ---------------------------------------------------------------------------
# CRITICAL escalation — 3+ regulated/PII signals
# ---------------------------------------------------------------------------


class TestCriticalEscalation:
    def test_three_regulated_signals_escalate_to_critical(
        self, classifier: DataClassifier
    ) -> None:
        # "compliance", "audit", "regulated" are three regulated signals.
        result = classifier.classify(
            "Ensure compliance with HIPAA audit trail requirements in the regulated pipeline"
        )
        assert result.risk_level == RiskLevel.CRITICAL
        assert result.guardrail_preset == "Regulated Data"

    def test_two_regulated_signals_stay_high(
        self, classifier: DataClassifier
    ) -> None:
        result = classifier.classify("compliance audit for the new service")
        assert result.risk_level == RiskLevel.HIGH

    def test_mixed_regulated_and_pii_triggers_critical(
        self, classifier: DataClassifier
    ) -> None:
        # "gdpr" appears in both PII_SIGNALS and REGULATED_SIGNALS.
        # "pii", "compliance" add more.
        result = classifier.classify("GDPR compliance policy for PII handling")
        assert result.risk_level == RiskLevel.CRITICAL


# ---------------------------------------------------------------------------
# File path elevation
# ---------------------------------------------------------------------------


class TestFilePaths:
    def test_env_file_elevates_to_high(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Update config", file_paths=["config/.env"])
        assert result.risk_level == RiskLevel.HIGH
        assert result.guardrail_preset == "Security-Sensitive"

    def test_auth_path_elevates_to_security(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Fix bug", file_paths=["src/auth/middleware.py"])
        assert result.risk_level == RiskLevel.HIGH
        assert result.guardrail_preset == "Security-Sensitive"

    def test_dockerfile_elevates_to_infrastructure(
        self, classifier: DataClassifier
    ) -> None:
        result = classifier.classify("Fix typo", file_paths=["Dockerfile"])
        assert result.risk_level == RiskLevel.HIGH
        assert result.guardrail_preset == "Infrastructure Changes"

    def test_github_workflows_elevates_to_infrastructure(
        self, classifier: DataClassifier
    ) -> None:
        result = classifier.classify("Fix typo", file_paths=[".github/workflows/ci.yml"])
        assert result.risk_level == RiskLevel.HIGH
        assert result.guardrail_preset == "Infrastructure Changes"

    def test_path_signal_captured_in_signals_found(
        self, classifier: DataClassifier
    ) -> None:
        result = classifier.classify("Update settings", file_paths=["secrets/api.json"])
        assert any(s.startswith("path:") for s in result.signals_found)

    def test_none_file_paths_ignored(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Fix a bug", file_paths=None)
        assert result.risk_level == RiskLevel.LOW

    def test_empty_file_paths_list_ignored(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Fix a bug", file_paths=[])
        assert result.risk_level == RiskLevel.LOW

    def test_unrelated_paths_do_not_elevate(self, classifier: DataClassifier) -> None:
        result = classifier.classify(
            "Update documentation",
            file_paths=["docs/guide.md", "README.md"],
        )
        assert result.risk_level == RiskLevel.LOW

    def test_path_does_not_override_regulated_preset(
        self, classifier: DataClassifier
    ) -> None:
        # A Dockerfile path should not downgrade a regulated preset.
        result = classifier.classify(
            "Update compliance audit module",
            file_paths=["Dockerfile"],
        )
        assert result.guardrail_preset == "Regulated Data"


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------


class TestConfidence:
    def test_no_signals_confidence_is_high(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Rename a variable")
        assert result.confidence == "high"

    def test_single_signal_confidence_is_low(self, classifier: DataClassifier) -> None:
        # "firewall" is one security signal; no other keyword in the description matches.
        result = classifier.classify("Update the firewall rules for the new service")
        assert result.confidence == "low"

    def test_two_signals_confidence_is_high(self, classifier: DataClassifier) -> None:
        # "authentication" + "jwt" are two security signals.
        result = classifier.classify("Refactor the JWT authentication middleware")
        assert result.confidence == "high"

    def test_many_signals_confidence_is_high(self, classifier: DataClassifier) -> None:
        result = classifier.classify(
            "compliance audit trail for regulated HIPAA patient data"
        )
        assert result.confidence == "high"


# ---------------------------------------------------------------------------
# Explanation presence
# ---------------------------------------------------------------------------


class TestExplanation:
    def test_elevated_risk_has_explanation(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Deploy the new authentication service")
        assert result.explanation != ""

    def test_no_signals_has_explanation(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Rename a function")
        assert result.explanation != ""

    def test_elevated_explanation_mentions_auditor(
        self, classifier: DataClassifier
    ) -> None:
        result = classifier.classify("Update the compliance reporting pipeline")
        assert "Auditor" in result.explanation or "auditor" in result.explanation


# ---------------------------------------------------------------------------
# ClassificationResult.to_markdown
# ---------------------------------------------------------------------------


class TestToMarkdown:
    def test_heading_present(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Fix a typo")
        md = result.to_markdown()
        assert "## Data Classification" in md

    def test_risk_level_present(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Fix a typo")
        md = result.to_markdown()
        assert "**Risk Level:**" in md
        assert "LOW" in md

    def test_guardrail_preset_present(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Fix a typo")
        md = result.to_markdown()
        assert "**Guardrail Preset:**" in md
        assert "Standard Development" in md

    def test_signals_line_present_when_signals_exist(
        self, classifier: DataClassifier
    ) -> None:
        result = classifier.classify("Update the JWT authentication flow")
        md = result.to_markdown()
        assert "**Signals:**" in md

    def test_no_signals_line_when_no_signals(self, classifier: DataClassifier) -> None:
        result = classifier.classify("Fix a typo")
        # Signals line should only appear when signals_found is non-empty.
        if not result.signals_found:
            assert "**Signals:**" not in result.to_markdown()

    def test_explanation_line_present_when_explanation_exists(
        self, classifier: DataClassifier
    ) -> None:
        result = classifier.classify("Fix a typo")
        if result.explanation:
            assert "**Explanation:**" in result.to_markdown()

    def test_high_risk_result_markdown(self, classifier: DataClassifier) -> None:
        result = ClassificationResult(
            risk_level=RiskLevel.HIGH,
            guardrail_preset="Security-Sensitive",
            signals_found=["security:jwt", "security:authentication"],
            confidence="high",
            explanation="Elevated risk detected (2 signal(s)). Auditor review recommended.",
        )
        md = result.to_markdown()
        assert "HIGH" in md
        assert "Security-Sensitive" in md
        assert "security:jwt" in md
        assert "Auditor review" in md
