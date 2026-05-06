"""Shared UTC timestamp utilities.

All timestamp functions in this module are intentionally pure and
side-effect-free so that call sites can substitute a mock without
patching multiple modules.
"""
from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    """Return the current UTC time as a full ISO 8601 string.

    Example: ``2024-01-15T12:34:56.789012+00:00``
    """
    return datetime.now(timezone.utc).isoformat()


def utcnow_seconds() -> str:
    """Return the current UTC time as a seconds-precision ISO 8601 string.

    Example: ``2024-01-15T12:34:56+00:00``
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def utcnow_zulu() -> str:
    """Return the current UTC time formatted as a Zulu-suffix ISO string.

    Example: ``2024-01-15T12:34:56Z``
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
