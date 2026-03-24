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


@pytest.mark.parametrize("description,expected_risk", [
    ("", RiskLevel.LOW),
    ("Add a helper function to format dates", RiskLevel.LOW),
    ("Refactor the widget component", RiskLevel.LOW),
    ("Fix a typo in the README", RiskLevel.LOW),
])
def test_no_signals_risk_level(classifier: DataClassifier, description: str, expected_risk: RiskLevel) -> None:
    result = classifier.classify(description)
    assert result.risk_level == expected_risk


@pytest.mark.parametrize("description", [
    "Refactor the widget component",
    "Fix a typo in the README",
])
def test_no_signals_preset_is_standard(classifier: DataClassifier, description: str) -> None:
    result = classifier.classify(description)
    assert result.guardrail_preset == "Standard Development"


def test_no_signals_confidence_is_high(classifier: DataClassifier) -> None:
    result = classifier.classify("Fix a typo in the README")
    assert result.confidence == "high"


def test_no_signals_explanation_mentions_standard(classifier: DataClassifier) -> None:
    result = classifier.classify("Update sorting logic")
    assert "Standard development" in result.explanation or "No sensitivity" in result.explanation


def test_no_signals_signals_list_is_empty(classifier: DataClassifier) -> None:
    result = classifier.classify("Create a new API endpoint for search")
    assert all(
        not s.startswith(("regulated:", "pii:", "security:", "infra:", "database:"))
        for s in result.signals_found
    )


# ---------------------------------------------------------------------------
# Signal detection — regulated, PII, security, infrastructure, database
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("description,expected_risk,expected_preset,signal_prefix", [
    # regulated signals → HIGH + Regulated Data
    ("Handle compliance reporting for the audit system", RiskLevel.HIGH, "Regulated Data", "regulated:"),
    ("Ensure HIPAA requirements are satisfied", RiskLevel.HIGH, "Regulated Data", "regulated:"),
    ("Implement GDPR data deletion endpoint", RiskLevel.HIGH, "Regulated Data", None),
    ("Build an audit trail for the new feature", RiskLevel.HIGH, "Regulated Data", "regulated:"),
    ("This system handles regulated financial data", RiskLevel.HIGH, "Regulated Data", "regulated:"),
    # PII signals → HIGH + Regulated Data
    ("Anonymise PII before exporting to the data lake", RiskLevel.HIGH, "Regulated Data", "pii:"),
    ("Store SSN in the employee record", RiskLevel.HIGH, "Regulated Data", "pii:"),
    ("Tokenize credit card before persisting", RiskLevel.HIGH, "Regulated Data", "pii:"),
    ("Migrate patient records to new schema", RiskLevel.HIGH, "Regulated Data", "pii:"),
    ("Handle personal data deletion requests", RiskLevel.HIGH, "Regulated Data", "pii:"),
    # security signals → HIGH + Security-Sensitive
    ("Refactor the authentication flow to use JWT", RiskLevel.HIGH, "Security-Sensitive", "security:"),
    ("Add JWT refresh token rotation", RiskLevel.HIGH, "Security-Sensitive", "security:"),
    ("Integrate OAuth 2.0 login flow", RiskLevel.HIGH, "Security-Sensitive", "security:"),
    ("Rotate secrets and update API keys in vault", RiskLevel.HIGH, "Security-Sensitive", "security:"),
    ("Harden SSL/TLS configuration on the gateway", RiskLevel.HIGH, "Security-Sensitive", "security:"),
    # infrastructure signals → HIGH + Infrastructure Changes
    ("Update the Docker base image to python:3.12-slim", RiskLevel.HIGH, "Infrastructure Changes", "infra:"),
    ("Add terraform module for S3 bucket", RiskLevel.HIGH, "Infrastructure Changes", "infra:"),
    ("Configure kubernetes horizontal pod autoscaler", RiskLevel.HIGH, "Infrastructure Changes", "infra:"),
    ("Create deploy script for production release", RiskLevel.HIGH, "Infrastructure Changes", "infra:"),
    ("Set up CI/CD pipeline for the new service", RiskLevel.HIGH, "Infrastructure Changes", "infra:"),
    # database signals → MEDIUM + Standard Development
    ("Write a migration to add the created_at column", RiskLevel.MEDIUM, "Standard Development", "database:"),
    ("Update the database schema for the orders table", RiskLevel.MEDIUM, "Standard Development", "database:"),
    ("Optimise the database index on the users table", RiskLevel.MEDIUM, "Standard Development", "database:"),
    ("Drop the legacy column from the products table", RiskLevel.MEDIUM, "Standard Development", "database:"),
    ("Add foreign key constraint between orders and users", RiskLevel.MEDIUM, "Standard Development", "database:"),
])
def test_signal_detection(
    classifier: DataClassifier,
    description: str,
    expected_risk: RiskLevel,
    expected_preset: str,
    signal_prefix: str | None,
) -> None:
    result = classifier.classify(description)
    assert result.risk_level == expected_risk
    assert result.guardrail_preset == expected_preset
    if signal_prefix is not None:
        assert any(s.startswith(signal_prefix) for s in result.signals_found)


# ---------------------------------------------------------------------------
# Priority ordering — regulated preset wins over security / infra
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("description,expected_preset", [
    # security should not override regulated
    ("Compliance audit authentication tokens", "Regulated Data"),
    # infra should not override regulated
    ("Deploy the compliance reporting infrastructure", "Regulated Data"),
    # database should not override HIGH
    ("Migrate the compliance audit schema", None),  # risk checked separately
])
def test_preset_priority(
    classifier: DataClassifier,
    description: str,
    expected_preset: str | None,
) -> None:
    result = classifier.classify(description)
    if expected_preset is not None:
        assert result.guardrail_preset == expected_preset


def test_database_does_not_override_high_risk_level(classifier: DataClassifier) -> None:
    result = classifier.classify("Migrate the compliance audit schema")
    assert result.risk_level == RiskLevel.HIGH


# ---------------------------------------------------------------------------
# CRITICAL escalation — 3+ regulated/PII signals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("description,expected_risk", [
    (
        "Ensure compliance with HIPAA audit trail requirements in the regulated pipeline",
        RiskLevel.CRITICAL,
    ),
    ("compliance audit for the new service", RiskLevel.HIGH),
    ("GDPR compliance policy for PII handling", RiskLevel.CRITICAL),
])
def test_critical_escalation(
    classifier: DataClassifier,
    description: str,
    expected_risk: RiskLevel,
) -> None:
    result = classifier.classify(description)
    assert result.risk_level == expected_risk


def test_critical_escalation_preset_is_regulated(classifier: DataClassifier) -> None:
    result = classifier.classify(
        "Ensure compliance with HIPAA audit trail requirements in the regulated pipeline"
    )
    assert result.guardrail_preset == "Regulated Data"


# ---------------------------------------------------------------------------
# File path elevation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("description,file_paths,expected_risk,expected_preset", [
    ("Update config", ["config/.env"], RiskLevel.HIGH, "Security-Sensitive"),
    ("Fix bug", ["src/auth/middleware.py"], RiskLevel.HIGH, "Security-Sensitive"),
    ("Fix typo", ["Dockerfile"], RiskLevel.HIGH, "Infrastructure Changes"),
    ("Fix typo", [".github/workflows/ci.yml"], RiskLevel.HIGH, "Infrastructure Changes"),
    ("Fix a bug", None, RiskLevel.LOW, None),
    ("Fix a bug", [], RiskLevel.LOW, None),
    ("Update documentation", ["docs/guide.md", "README.md"], RiskLevel.LOW, None),
])
def test_file_path_elevation(
    classifier: DataClassifier,
    description: str,
    file_paths: list[str] | None,
    expected_risk: RiskLevel,
    expected_preset: str | None,
) -> None:
    result = classifier.classify(description, file_paths=file_paths)
    assert result.risk_level == expected_risk
    if expected_preset is not None:
        assert result.guardrail_preset == expected_preset


def test_path_signal_captured_in_signals_found(classifier: DataClassifier) -> None:
    result = classifier.classify("Update settings", file_paths=["secrets/api.json"])
    assert any(s.startswith("path:") for s in result.signals_found)


def test_path_does_not_override_regulated_preset(classifier: DataClassifier) -> None:
    result = classifier.classify(
        "Update compliance audit module",
        file_paths=["Dockerfile"],
    )
    assert result.guardrail_preset == "Regulated Data"


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("description,expected_confidence", [
    ("Rename a variable", "high"),
    ("Update the firewall rules for the new service", "low"),
    ("Refactor the JWT authentication middleware", "high"),
    ("compliance audit trail for regulated HIPAA patient data", "high"),
])
def test_confidence(
    classifier: DataClassifier,
    description: str,
    expected_confidence: str,
) -> None:
    result = classifier.classify(description)
    assert result.confidence == expected_confidence


# ---------------------------------------------------------------------------
# Explanation presence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("description,check_auditor", [
    ("Deploy the new authentication service", False),
    ("Rename a function", False),
    ("Update the compliance reporting pipeline", True),
])
def test_explanation(
    classifier: DataClassifier,
    description: str,
    check_auditor: bool,
) -> None:
    result = classifier.classify(description)
    assert result.explanation != ""
    if check_auditor:
        assert "Auditor" in result.explanation or "auditor" in result.explanation


# ---------------------------------------------------------------------------
# ClassificationResult.to_markdown
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("description,check_signals_present,check_signals_absent", [
    ("Fix a typo", False, True),
    ("Update the JWT authentication flow", True, False),
])
def test_to_markdown_structure(
    classifier: DataClassifier,
    description: str,
    check_signals_present: bool,
    check_signals_absent: bool,
) -> None:
    result = classifier.classify(description)
    md = result.to_markdown()
    assert "## Data Classification" in md
    assert "**Risk Level:**" in md
    assert "**Guardrail Preset:**" in md
    if check_signals_present:
        assert "**Signals:**" in md
    if check_signals_absent and not result.signals_found:
        assert "**Signals:**" not in md
    if result.explanation:
        assert "**Explanation:**" in md


def test_to_markdown_low_risk_values(classifier: DataClassifier) -> None:
    result = classifier.classify("Fix a typo")
    md = result.to_markdown()
    assert "LOW" in md
    assert "Standard Development" in md


def test_to_markdown_high_risk_result() -> None:
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
