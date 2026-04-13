"""Sensitive data classifier -- auto-classifies task risk level and guardrail preset.

Scans task descriptions and file paths for signal keywords that indicate
sensitivity, then assigns a risk level and the corresponding guardrail
preset. The classification cascade is:

1. **Regulated / PII signals** (compliance, HIPAA, GDPR, SSN, ...) --> HIGH,
   preset "Regulated Data".
2. **Security signals** (auth, secrets, credentials, ...) --> HIGH,
   preset "Security-Sensitive".
3. **Infrastructure signals** (terraform, docker, deploy, ...) --> HIGH,
   preset "Infrastructure Changes".
4. **Database signals** (migration, schema, alter table, ...) --> MEDIUM,
   preset "Standard Development".
5. **File-path patterns** (e.g. ``.env``, ``secrets/``, ``auth/``,
   ``migrations/``) can elevate risk independently of description text.
6. **Escalation to CRITICAL**: 3 or more regulated/PII signals in a single
   task automatically raise the risk to CRITICAL.

When no signals are detected, the task defaults to LOW risk with the
"Standard Development" guardrail preset.

"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from agent_baton.models.enums import RiskLevel


@dataclass
class ClassificationResult:
    """Result of classifying a task's data sensitivity.

    Attributes:
        risk_level: The assessed risk tier (LOW, MEDIUM, HIGH, or CRITICAL).
        guardrail_preset: Name of the guardrail preset that should be applied.
            One of "Standard Development", "Data Analysis",
            "Infrastructure Changes", "Regulated Data", or
            "Security-Sensitive".
        signals_found: List of keyword matches that contributed to the
            classification, formatted as ``"category:keyword"``
            (e.g. ``"regulated:hipaa"``, ``"path:.env"``).
        confidence: ``"high"`` when two or more signals were found or when
            no signals were found (default is safe); ``"low"`` when exactly
            one signal was found.
        explanation: Human-readable summary of the classification reasoning.
    """

    risk_level: RiskLevel
    guardrail_preset: str  # "Standard Development", "Data Analysis", "Infrastructure Changes", "Regulated Data", "Security-Sensitive"
    signals_found: list[str] = field(default_factory=list)  # which keywords/patterns triggered
    confidence: str = "high"  # "high" (multiple signals) or "low" (single signal)
    explanation: str = ""

    def to_markdown(self) -> str:
        """Render the classification result as a human-readable markdown block.

        Returns:
            A markdown string with risk level, guardrail preset, confidence,
            detected signals, and explanation.
        """
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
    """Classify task sensitivity and select the appropriate guardrail preset.

    The classifier uses a keyword-matching approach to scan task descriptions
    and file paths for sensitivity signals. It applies a deterministic
    cascade to resolve the final risk level when multiple signal categories
    are present:

    Risk escalation order (highest precedence first):
        CRITICAL -- 3+ regulated/PII signals in a single task.
        HIGH     -- Any regulated, PII, security, or infrastructure signal,
                    or a file-path match against known sensitive paths.
        MEDIUM   -- Database signals (schema migrations, DDL statements)
                    when no higher signals are present.
        LOW      -- No signals detected; standard development guardrails.

    The classifier is stateless and safe to reuse across multiple calls.
    """

    def classify(
        self,
        task_description: str,
        file_paths: list[str] | None = None,
    ) -> ClassificationResult:
        """Classify a task's data sensitivity from its description and affected files.

        Scans the task description for signal keywords across five categories
        (regulated, PII, security, infrastructure, database) and checks
        file paths against known sensitive path patterns. Returns the
        highest applicable risk level with the matching guardrail preset.

        The classification cascade is evaluated in priority order so that
        regulated/PII signals always dominate security/infrastructure, and
        database signals are only applied when no higher category matches.

        Args:
            task_description: Free-text description of the task to classify.
                Matching is case-insensitive.
            file_paths: Optional list of file paths that the task will touch.
                Paths are matched against ``HIGH_RISK_PATHS`` patterns
                (e.g. ``.env``, ``secrets/``, ``auth/``, ``migrations/``).

        Returns:
            A ``ClassificationResult`` containing the risk level, guardrail
            preset name, matched signals, confidence, and explanation.
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
        """Classify with automatic file path discovery via ``git diff``.

        Runs ``git diff --name-only HEAD`` in the project root to discover
        changed files, then delegates to :meth:`classify` with both the
        task description and the discovered paths. Falls back to
        description-only classification if git is unavailable, the command
        times out (5 s), or the working directory is not a git repository.

        Args:
            task_description: Free-text description of the task to classify.
            project_root: Root directory of the git repository. Defaults to
                the current working directory.

        Returns:
            A ``ClassificationResult`` (same as :meth:`classify`).
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
