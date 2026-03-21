"""Sensitive data classifier — auto-classifies task risk level and guardrail preset.

**Status: Experimental** — built and tested but not yet validated with real usage data.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from agent_baton.models.enums import RiskLevel


@dataclass
class ClassificationResult:
    """Result of classifying a task's data sensitivity."""

    risk_level: RiskLevel
    guardrail_preset: str  # "Standard Development", "Data Analysis", "Infrastructure Changes", "Regulated Data", "Security-Sensitive"
    signals_found: list[str] = field(default_factory=list)  # which keywords/patterns triggered
    confidence: str = "high"  # "high" (multiple signals) or "low" (single signal)
    explanation: str = ""

    def to_markdown(self) -> str:
        lines = [
            "## Data Classification",
            "",
            f"**Risk Level:** {self.risk_level.value}",
            f"**Guardrail Preset:** {self.guardrail_preset}",
            f"**Confidence:** {self.confidence}",
        ]
        if self.signals_found:
            lines.append(f"**Signals:** {', '.join(self.signals_found)}")
        if self.explanation:
            lines.append(f"**Explanation:** {self.explanation}")
        return "\n".join(lines)


# Ordinal helpers for comparing RiskLevel values without relying on string comparison.
_RISK_ORDINAL: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


def _higher(a: RiskLevel, b: RiskLevel) -> RiskLevel:
    """Return whichever risk level is higher."""
    return a if _RISK_ORDINAL[a] >= _RISK_ORDINAL[b] else b


# Signal categories — each maps to a risk level and preset.

REGULATED_SIGNALS = [
    "compliance", "regulated", "audit", "regulatory", "hipaa", "gdpr",
    "sox", "pci", "ferpa", "retention", "audit-trail", "audit trail",
    "reportable", "inspection", "certification",
]

PII_SIGNALS = [
    "pii", "personal data", "ssn", "social security", "email address",
    "phone number", "date of birth", "credit card", "patient",
    "employee record", "user data", "gdpr",
]

SECURITY_SIGNALS = [
    "authentication", "authorization", "auth", "secrets", "credentials",
    "password", "token", "api key", "api-key", "oauth", "jwt", "session",
    "encryption", "certificate", "tls", "ssl", "firewall",
]

INFRASTRUCTURE_SIGNALS = [
    "infrastructure", "terraform", "docker", "kubernetes", "k8s",
    "ci/cd", "cicd", "pipeline", "deploy", "deployment", "production",
    "staging", "load balancer", "dns", "cdn", "monitoring",
]

DATABASE_SIGNALS = [
    "migration", "schema", "database", "table", "column", "index",
    "foreign key", "constraint", "alter table", "drop", "truncate",
]

# File path patterns that elevate risk.
HIGH_RISK_PATHS = [
    ".env", "secrets/", "credentials", "auth/", "migrations/",
    "docker", "dockerfile", ".github/workflows", "terraform",
    "deploy", "infrastructure/",
]


class DataClassifier:
    """Classify task sensitivity and select guardrail preset."""

    def classify(
        self,
        task_description: str,
        file_paths: list[str] | None = None,
    ) -> ClassificationResult:
        """Classify a task's data sensitivity from its description and affected files.

        Scans for signal keywords, checks file paths, and returns the
        highest applicable risk level with the matching guardrail preset.
        """
        description_lower = task_description.lower()
        signals: list[str] = []
        max_risk = RiskLevel.LOW
        preset = "Standard Development"

        # Check regulated signals → HIGH
        for signal in REGULATED_SIGNALS:
            if signal in description_lower:
                signals.append(f"regulated:{signal}")
                if _RISK_ORDINAL[max_risk] < _RISK_ORDINAL[RiskLevel.HIGH]:
                    max_risk = RiskLevel.HIGH
                    preset = "Regulated Data"

        # Check PII signals → HIGH
        for signal in PII_SIGNALS:
            if signal in description_lower:
                signals.append(f"pii:{signal}")
                if _RISK_ORDINAL[max_risk] < _RISK_ORDINAL[RiskLevel.HIGH]:
                    max_risk = RiskLevel.HIGH
                    preset = "Regulated Data"

        # Check security signals → HIGH (only if not already classified as regulated)
        for signal in SECURITY_SIGNALS:
            if signal in description_lower:
                signals.append(f"security:{signal}")
                if max_risk == RiskLevel.LOW:
                    max_risk = RiskLevel.HIGH
                    preset = "Security-Sensitive"

        # Check infrastructure signals → HIGH (only if still LOW)
        for signal in INFRASTRUCTURE_SIGNALS:
            if signal in description_lower:
                signals.append(f"infra:{signal}")
                if max_risk == RiskLevel.LOW:
                    max_risk = RiskLevel.HIGH
                    preset = "Infrastructure Changes"

        # Check database signals → MEDIUM (only if still LOW)
        for signal in DATABASE_SIGNALS:
            if signal in description_lower:
                signals.append(f"database:{signal}")
                if max_risk == RiskLevel.LOW:
                    max_risk = RiskLevel.MEDIUM
                    preset = "Standard Development"

        # Check file paths for risk elevation.
        if file_paths:
            for fpath in file_paths:
                fpath_lower = fpath.lower()
                for pattern in HIGH_RISK_PATHS:
                    if pattern in fpath_lower:
                        signals.append(f"path:{pattern}")
                        if _RISK_ORDINAL[max_risk] < _RISK_ORDINAL[RiskLevel.HIGH]:
                            max_risk = RiskLevel.HIGH
                            # Pick preset based on which path pattern matched.
                            if pattern in (".env", "secrets/", "credentials", "auth/"):
                                preset = "Security-Sensitive"
                            elif pattern in (
                                "docker", "dockerfile", "terraform", "deploy",
                                "infrastructure/", ".github/workflows",
                            ):
                                preset = "Infrastructure Changes"
                            elif pattern == "migrations/":
                                # Migrations alone are MEDIUM, not HIGH — only
                                # elevate if we are upgrading from LOW.
                                if max_risk == RiskLevel.HIGH:
                                    pass  # already at HIGH from another signal
                                else:
                                    max_risk = RiskLevel.MEDIUM
                                    preset = "Standard Development"

        # Multiple regulated + PII signals → escalate to CRITICAL.
        regulated_count = sum(
            1 for s in signals if s.startswith(("regulated:", "pii:"))
        )
        if regulated_count >= 3:
            max_risk = RiskLevel.CRITICAL
            preset = "Regulated Data"

        # Confidence: high if 2+ signals, low if exactly 1, high if none.
        if len(signals) >= 2:
            confidence = "high"
        elif len(signals) == 1:
            confidence = "low"
        else:
            confidence = "high"

        explanation = ""
        if not signals:
            explanation = (
                "No sensitivity signals detected. Standard development guardrails apply."
            )
        elif max_risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            explanation = (
                f"Elevated risk detected ({len(signals)} signal(s)). "
                "Auditor review recommended."
            )

        return ClassificationResult(
            risk_level=max_risk,
            guardrail_preset=preset,
            signals_found=signals,
            confidence=confidence,
            explanation=explanation,
        )

    def classify_from_files(
        self,
        task_description: str,
        project_root: Path | None = None,
    ) -> ClassificationResult:
        """Classify with automatic file path discovery.

        Scans git diff or changed files to find affected paths.
        Falls back to description-only classification if no git context is available.
        """
        file_paths: list[str] = []
        root = project_root or Path.cwd()

        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                capture_output=True,
                text=True,
                cwd=root,
                timeout=5,
            )
            if result.returncode == 0:
                file_paths = [f for f in result.stdout.strip().splitlines() if f]
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        return self.classify(task_description, file_paths or None)
