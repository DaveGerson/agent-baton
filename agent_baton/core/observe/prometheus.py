"""Prometheus exposition for Agent Baton observability metrics.

This module is the integration point between Agent Baton's internal
observability (telemetry, SLOs, error budgets) and external monitoring
infrastructure that scrapes Prometheus-format metrics.

The full O1.6 exposition surface lives elsewhere; this file provides the
SLO-specific gauges used by O1.5 plus a thin text-format exposition
helper for the case when ``prometheus_client`` is not installed.

Usage with ``prometheus_client``::

    from prometheus_client import CollectorRegistry, generate_latest
    from agent_baton.core.observe.prometheus import register_slo_metrics

    registry = CollectorRegistry()
    register_slo_metrics(registry, db_path)
    print(generate_latest(registry).decode("utf-8"))

Usage without ``prometheus_client`` (fallback)::

    from agent_baton.core.observe.prometheus import render_slo_metrics_text
    print(render_slo_metrics_text(db_path))

Both forms emit three SLI / SLO gauges per SLO:

* ``agent_baton_slo_sli{name="..."}``                     -- current SLI value
* ``agent_baton_slo_target{name="..."}``                  -- configured target
* ``agent_baton_slo_error_budget_remaining{name="..."}``  -- remaining budget
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_baton.core.storage.slo_store import SLOStore


# ---------------------------------------------------------------------------
# Metric names -- exported as constants so other O1.6 helpers can reference
# them without hard-coding the strings.
# ---------------------------------------------------------------------------

METRIC_SLI = "agent_baton_slo_sli"
METRIC_TARGET = "agent_baton_slo_target"
METRIC_BUDGET_REMAINING = "agent_baton_slo_error_budget_remaining"


def _slo_snapshots(db_path: Path) -> list[dict[str, Any]]:
    """Read every SLO and join with its latest measurement.

    Returns rows shaped ``{"name", "target", "sli", "budget"}``.  When an
    SLO has never been measured, ``sli`` and ``budget`` default to ``0.0``
    so the Prometheus series still appears (with a value of zero) -- this
    avoids "missing series" alerts after fresh installation.
    """
    store = SLOStore(db_path)
    out: list[dict[str, Any]] = []
    for d in store.list_definitions():
        latest = store.latest_measurement(d.name)
        out.append(
            {
                "name": d.name,
                "target": float(d.target),
                "sli": float(latest.sli_value) if latest else 0.0,
                "budget": float(latest.error_budget_remaining_pct) if latest else 0.0,
            }
        )
    return out


# ---------------------------------------------------------------------------
# prometheus_client integration
# ---------------------------------------------------------------------------


def register_slo_metrics(registry: Any, db_path: Path) -> None:
    """Register the three SLO gauges against a ``prometheus_client`` registry.

    The function lazily imports ``prometheus_client`` so that the rest of
    Agent Baton remains importable on installations without that
    dependency.  When the import fails, a clear ``ImportError`` is raised
    -- callers without ``prometheus_client`` should use
    :func:`render_slo_metrics_text` instead.

    Args:
        registry: A ``prometheus_client.CollectorRegistry`` (or any
            registry implementing ``register``).
        db_path: Path to the project ``baton.db`` from which to read SLO
            definitions and measurements.
    """
    try:
        from prometheus_client import Gauge  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - exercised only without dep
        raise ImportError(
            "prometheus_client is required for register_slo_metrics(); "
            "install it or use render_slo_metrics_text() for fallback text output."
        ) from exc

    sli_gauge = Gauge(
        METRIC_SLI,
        "Current SLI value for an SLO (0..1)",
        ["name"],
        registry=registry,
    )
    target_gauge = Gauge(
        METRIC_TARGET,
        "Configured target for an SLO (0..1)",
        ["name"],
        registry=registry,
    )
    budget_gauge = Gauge(
        METRIC_BUDGET_REMAINING,
        "Remaining error budget for an SLO (0..1)",
        ["name"],
        registry=registry,
    )

    for snap in _slo_snapshots(db_path):
        sli_gauge.labels(name=snap["name"]).set(snap["sli"])
        target_gauge.labels(name=snap["name"]).set(snap["target"])
        budget_gauge.labels(name=snap["name"]).set(snap["budget"])


# ---------------------------------------------------------------------------
# Text-format fallback (no dependencies)
# ---------------------------------------------------------------------------


def render_slo_metrics_text(db_path: Path) -> str:
    """Render the SLO metrics as a Prometheus text-exposition payload.

    Equivalent to what ``generate_latest`` would produce for the gauges
    registered by :func:`register_slo_metrics`, but implemented without
    importing ``prometheus_client`` so it works on minimal installs.

    Args:
        db_path: Path to the project ``baton.db``.

    Returns:
        The text-format payload (trailing newline included) suitable for
        serving from an HTTP endpoint at ``/metrics``.
    """
    snaps = _slo_snapshots(db_path)
    lines: list[str] = []

    def _emit(metric: str, help_text: str) -> None:
        lines.append(f"# HELP {metric} {help_text}")
        lines.append(f"# TYPE {metric} gauge")

    _emit(METRIC_SLI, "Current SLI value for an SLO (0..1)")
    for s in snaps:
        lines.append(f'{METRIC_SLI}{{name="{s["name"]}"}} {s["sli"]}')

    _emit(METRIC_TARGET, "Configured target for an SLO (0..1)")
    for s in snaps:
        lines.append(f'{METRIC_TARGET}{{name="{s["name"]}"}} {s["target"]}')

    _emit(METRIC_BUDGET_REMAINING, "Remaining error budget for an SLO (0..1)")
    for s in snaps:
        lines.append(f'{METRIC_BUDGET_REMAINING}{{name="{s["name"]}"}} {s["budget"]}')

    return "\n".join(lines) + "\n"
