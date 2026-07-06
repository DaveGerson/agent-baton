"""Helpers for exposing planner validation failures through the API."""
from __future__ import annotations

from typing import Any

from agent_baton.core.engine.planning.stages.validation import PlanQualityError


def plan_quality_error_detail(exc: PlanQualityError) -> dict[str, Any]:
    """Build a structured HTTP error payload for a plan-quality rejection."""
    defects: list[dict[str, str]] = []
    for defect in getattr(exc, "defects", []) or []:
        message = str(getattr(defect, "message", "") or "")
        remediation = ""
        marker = "Remediation:"
        if marker in message:
            remediation = message.split(marker, 1)[1].strip()
        defects.append({
            "code": str(getattr(defect, "code", "") or ""),
            "severity": str(getattr(defect, "severity", "") or ""),
            "message": message,
            "remediation": remediation,
        })

    return {
        "error": "plan_quality_error",
        "message": str(exc),
        "defects": defects,
    }
