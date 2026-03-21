"""Backward-compatible shim — canonical location: core/govern/spec_validator.py"""
from agent_baton.core.govern.spec_validator import (
    SpecValidator,
    SpecValidationResult,
    SpecCheck,
)

__all__ = ["SpecValidator", "SpecValidationResult", "SpecCheck"]
