"""Governance sub-package for risk classification, compliance, policy enforcement,
escalation management, and validation.

This package provides the safety and compliance layer for the Agent Baton
orchestration engine. It ensures that agent assignments respect guardrail
policies, that sensitive data tasks receive appropriate risk classification,
and that compliance artifacts are generated for auditable workflows.

Modules:
    classifier: Automatic risk classification based on task description and
        file paths. Maps tasks to risk levels (LOW, MEDIUM, HIGH, CRITICAL)
        and guardrail presets.
    compliance: Generation and persistence of audit-ready compliance reports
        for regulated-data tasks.
    policy: Declarative guardrail policy rules evaluated against agent
        assignments. Includes five standard presets covering standard
        development, data analysis, infrastructure, regulated data, and
        security-sensitive work.
    escalation: Read/write management of human escalation requests that
        agents raise when they need human input.
    validator: Structural validation of agent definition markdown files
        (frontmatter, naming, tools, permissions).
    spec_validator: Output validation against JSON Schema, file structure
        expectations, Python export contracts, and API contracts.
"""
from __future__ import annotations

from agent_baton.core.govern.classifier import DataClassifier, ClassificationResult
from agent_baton.core.govern.compliance import (
    ComplianceReportGenerator,
    ComplianceReport,
    ComplianceEntry,
)
from agent_baton.core.govern.policy import PolicyEngine, PolicySet, PolicyRule, PolicyViolation
from agent_baton.core.govern.escalation import EscalationManager
from agent_baton.core.govern.validator import AgentValidator
from agent_baton.core.govern.spec_validator import SpecValidator, SpecValidationResult, SpecCheck

__all__ = [
    "DataClassifier",
    "ClassificationResult",
    "ComplianceReportGenerator",
    "ComplianceReport",
    "ComplianceEntry",
    "PolicyEngine",
    "PolicySet",
    "PolicyRule",
    "PolicyViolation",
    "EscalationManager",
    "AgentValidator",
    "SpecValidator",
    "SpecValidationResult",
    "SpecCheck",
]
