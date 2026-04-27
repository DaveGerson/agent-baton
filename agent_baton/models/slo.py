"""Data models for Service-Level Objectives (SLOs) and error-budget tracking.

An SLO defines a reliability target for an underlying SLI (service-level
indicator) over a rolling time window.  Each SLO has an *error budget* --
the share of failures that the system is allowed to absorb before the
operator is considered to be "out of budget" for that window.

This module owns three plain dataclasses that travel together:

``SLODefinition``
    Operator-authored configuration: name + SLI source + target + window.

``SLOMeasurement``
    A point-in-time computation of the SLI against the target.  Persisted
    so that operators can graph SLI / error-budget trends without having
    to recompute from raw events on every read.

``ErrorBudgetBurn``
    A burn event -- a span of time during which the error budget was
    consumed at an elevated rate (typically tied to an incident).

All three follow the project convention of exposing ``to_dict`` /
``from_dict`` helpers that mirror the SQLite column names used by
``agent_baton.core.storage.slo_store.SLOStore``.

This module is **observation only** -- nothing here adds gates, alerts,
or prompts.  It provides the data shapes that the SLO computer, store,
CLI, and Prometheus exporter share.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# SLO Definition
# ---------------------------------------------------------------------------


@dataclass
class SLODefinition:
    """Operator-authored SLO configuration.

    Attributes:
        name: Stable identifier for the SLO (e.g. ``dispatch_success_rate``).
            Used as the primary key in storage and as the Prometheus label.
        sli_query: Identifier of the underlying SLI computation.  One of
            the keys recognised by ``SLOComputer`` -- currently
            ``"dispatch_success_rate"``, ``"gate_pass_rate"`` or
            ``"engine_uptime"``.
        target: Target ratio in ``[0.0, 1.0]``.  ``0.99`` means "99% of
            measurements must succeed".
        window_days: Rolling window in days over which the SLI is computed.
        description: Human-readable summary -- shown in ``baton slo list``.
    """

    name: str
    sli_query: str
    target: float
    window_days: int = 28
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "sli_query": self.sli_query,
            "target": self.target,
            "window_days": self.window_days,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SLODefinition:
        return cls(
            name=data["name"],
            sli_query=data["sli_query"],
            target=float(data.get("target", 0.0)),
            window_days=int(data.get("window_days", 28)),
            description=data.get("description", ""),
        )


# ---------------------------------------------------------------------------
# SLO Measurement
# ---------------------------------------------------------------------------


@dataclass
class SLOMeasurement:
    """A point-in-time computation of an SLO.

    Attributes:
        slo_name: Foreign key to ``SLODefinition.name``.
        window_start: ISO 8601 UTC timestamp of the rolling window start.
        window_end: ISO 8601 UTC timestamp of the rolling window end
            (typically the time the measurement was computed).
        sli_value: The observed SLI in ``[0.0, 1.0]``.
        target: Snapshot of the SLO target at compute time -- copied so
            the measurement remains meaningful if the operator later edits
            the SLO definition.
        is_meeting: ``True`` iff ``sli_value >= target``.
        error_budget_remaining_pct: Remaining error budget in
            ``[0.0, 1.0]``.  ``1.0`` = full budget, ``0.0`` = exhausted.
        computed_at: ISO 8601 UTC timestamp -- when this row was written.
        sample_size: Number of underlying events that contributed to the
            SLI (e.g. number of step results for ``dispatch_success_rate``).
            Zero is allowed and means "no data" -- consumers should treat
            the measurement as informational only in that case.
    """

    slo_name: str
    window_start: str
    window_end: str
    sli_value: float
    target: float
    is_meeting: bool
    error_budget_remaining_pct: float
    computed_at: str
    sample_size: int = 0

    def to_dict(self) -> dict:
        return {
            "slo_name": self.slo_name,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "sli_value": self.sli_value,
            "target": self.target,
            "is_meeting": self.is_meeting,
            "error_budget_remaining_pct": self.error_budget_remaining_pct,
            "computed_at": self.computed_at,
            "sample_size": self.sample_size,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SLOMeasurement:
        return cls(
            slo_name=data["slo_name"],
            window_start=data.get("window_start", ""),
            window_end=data.get("window_end", ""),
            sli_value=float(data.get("sli_value", 0.0)),
            target=float(data.get("target", 0.0)),
            is_meeting=bool(data.get("is_meeting", False)),
            error_budget_remaining_pct=float(
                data.get("error_budget_remaining_pct", 0.0)
            ),
            computed_at=data.get("computed_at", ""),
            sample_size=int(data.get("sample_size", 0)),
        )


# ---------------------------------------------------------------------------
# Error-budget burn
# ---------------------------------------------------------------------------


@dataclass
class ErrorBudgetBurn:
    """A span of elevated error-budget consumption.

    Attributes:
        slo_name: Foreign key to ``SLODefinition.name``.
        burn_rate: Budget consumed per hour -- ``1.0`` means the entire
            error budget would be exhausted in one hour at this rate.
        budget_consumed_pct: Total share of the error budget consumed
            during this span, in ``[0.0, 1.0]``.
        started_at: ISO 8601 UTC timestamp -- when the elevated burn
            began.
        ended_at: ISO 8601 UTC timestamp -- when the burn span closed.
            Empty string while the burn is still ongoing.
        incident_id: Optional pointer to a related incident record.
            ``None`` (or empty string in storage) when the burn is not
            yet attributed to an incident.
        id: Auto-assigned row id from storage.  ``None`` for in-memory
            instances that have not been persisted yet.
    """

    slo_name: str
    burn_rate: float
    budget_consumed_pct: float
    started_at: str
    ended_at: str = ""
    incident_id: str | None = None
    id: int | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "slo_name": self.slo_name,
            "burn_rate": self.burn_rate,
            "budget_consumed_pct": self.budget_consumed_pct,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }
        if self.incident_id is not None:
            d["incident_id"] = self.incident_id
        if self.id is not None:
            d["id"] = self.id
        return d

    @classmethod
    def from_dict(cls, data: dict) -> ErrorBudgetBurn:
        return cls(
            slo_name=data["slo_name"],
            burn_rate=float(data.get("burn_rate", 0.0)),
            budget_consumed_pct=float(data.get("budget_consumed_pct", 0.0)),
            started_at=data.get("started_at", ""),
            ended_at=data.get("ended_at", ""),
            incident_id=data.get("incident_id") or None,
            id=data.get("id"),
        )


# ---------------------------------------------------------------------------
# Canonical default SLOs
# ---------------------------------------------------------------------------


DEFAULT_SLOS: list[SLODefinition] = [
    SLODefinition(
        name="dispatch_success_rate",
        sli_query="dispatch_success_rate",
        target=0.99,
        window_days=28,
        description="Share of DISPATCH actions that ended in step status 'complete'.",
    ),
    SLODefinition(
        name="gate_pass_rate",
        sli_query="gate_pass_rate",
        target=0.95,
        window_days=28,
        description="Share of GATE checks that passed on first attempt.",
    ),
    SLODefinition(
        name="engine_uptime",
        sli_query="engine_uptime",
        target=0.999,
        window_days=28,
        description="Share of execution-state checkpoints that did not transition to 'failed'.",
    ),
]
"""Canonical SLO definitions seeded by ``baton slo seed-defaults``.

Operators can override any of these by re-defining an SLO with the same
name (the store uses INSERT OR REPLACE semantics on ``name``).
"""

# Field decorator import is unused but kept for callers that subclass these
# dataclasses; suppress the unused-import warning via __all__.
_unused_field = field  # noqa: F841 -- exported for convenience
