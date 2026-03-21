"""Backward-compatible shim — canonical location: core/govern/compliance.py"""
from agent_baton.core.govern.compliance import (
    ComplianceReportGenerator,
    ComplianceReport,
    ComplianceEntry,
)

__all__ = ["ComplianceReportGenerator", "ComplianceReport", "ComplianceEntry"]
