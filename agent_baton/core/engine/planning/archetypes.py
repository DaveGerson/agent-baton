"""Archetype definitions — phase templates and gate policies per planning archetype."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GatePolicy:
    """Gate configuration for a specific risk level within an archetype."""

    automated_gates: list[str] = field(default_factory=list)
    code_review: bool = False
    spec_compliance: bool = False
    approval_required: bool = False
    auditor_required: bool = False


@dataclass(frozen=True)
class ArchetypeConfig:
    """Configuration for a single planning archetype."""

    name: str
    phase_template: list[str] = field(default_factory=list)
    gate_policies: dict[str, GatePolicy] = field(default_factory=dict)
    max_step_minutes: int = 15
    supports_retry: bool = False
    max_retry_count: int = 0
    decision_capture: bool = False
    skip_stages: list[str] = field(default_factory=list)


DIRECT_CONFIG = ArchetypeConfig(
    name="direct",
    phase_template=["Implement", "Review"],
    gate_policies={
        "LOW": GatePolicy(automated_gates=["build", "test"]),
        "MEDIUM": GatePolicy(automated_gates=["build", "test"], code_review=True),
        "HIGH": GatePolicy(
            automated_gates=["build", "test"],
            code_review=True,
            approval_required=True,
        ),
        "CRITICAL": GatePolicy(
            automated_gates=["build", "test"],
            code_review=True,
            approval_required=True,
            auditor_required=True,
        ),
    },
    max_step_minutes=10,
)

PHASED_CONFIG = ArchetypeConfig(
    name="phased",
    phase_template=["Design", "Implement", "Test", "Review"],
    gate_policies={
        "LOW": GatePolicy(automated_gates=["build", "test"]),
        "MEDIUM": GatePolicy(
            automated_gates=["build", "test"],
            code_review=True,
            spec_compliance=True,
        ),
        "HIGH": GatePolicy(
            automated_gates=["build", "test"],
            code_review=True,
            spec_compliance=True,
            approval_required=True,
        ),
        "CRITICAL": GatePolicy(
            automated_gates=["build", "test"],
            code_review=True,
            spec_compliance=True,
            approval_required=True,
            auditor_required=True,
        ),
    },
    max_step_minutes=15,
    decision_capture=True,
)

INVESTIGATIVE_CONFIG = ArchetypeConfig(
    name="investigative",
    phase_template=["Investigate", "Hypothesize", "Fix", "Verify"],
    gate_policies={
        "LOW": GatePolicy(automated_gates=["test"]),
        "MEDIUM": GatePolicy(automated_gates=["build", "test"], code_review=True),
        "HIGH": GatePolicy(
            automated_gates=["build", "test"],
            code_review=True,
            approval_required=True,
        ),
        "CRITICAL": GatePolicy(
            automated_gates=["build", "test"],
            code_review=True,
            approval_required=True,
            auditor_required=True,
        ),
    },
    max_step_minutes=10,
    supports_retry=True,
    max_retry_count=3,
)

ARCHETYPE_CONFIGS: dict[str, ArchetypeConfig] = {
    "direct": DIRECT_CONFIG,
    "phased": PHASED_CONFIG,
    "investigative": INVESTIGATIVE_CONFIG,
}


def get_archetype_config(archetype: str) -> ArchetypeConfig:
    """Look up archetype config, defaulting to PHASED for unknown values."""
    return ARCHETYPE_CONFIGS.get(archetype, PHASED_CONFIG)
