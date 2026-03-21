"""Backward-compatible shim — canonical location: core/govern/classifier.py"""
from agent_baton.core.govern.classifier import (
    DataClassifier,
    ClassificationResult,
    REGULATED_SIGNALS,
    PII_SIGNALS,
    SECURITY_SIGNALS,
    INFRASTRUCTURE_SIGNALS,
    DATABASE_SIGNALS,
    HIGH_RISK_PATHS,
)

__all__ = [
    "DataClassifier",
    "ClassificationResult",
    "REGULATED_SIGNALS",
    "PII_SIGNALS",
    "SECURITY_SIGNALS",
    "INFRASTRUCTURE_SIGNALS",
    "DATABASE_SIGNALS",
    "HIGH_RISK_PATHS",
]
