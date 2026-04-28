"""Velocity-zero observability primitives (O1.4 / bd-91c7).

Two read-side capabilities, both with stdlib-only implementations and
zero new external dependencies:

- :mod:`agent_baton.core.observability.prometheus` — text-exposition
  helpers used by the ``GET /metrics`` route.
- :mod:`agent_baton.core.observability.otel_exporter` — OTLP-shaped
  JSONL span exporter used as a drop-in stand-in for an
  ``opentelemetry-sdk``-backed BatchSpanExporter.

Both features are designed to impose zero overhead until enabled:

- ``/metrics`` is always exposed (Prometheus expects it) but lazily
  computes its values from baton.db / central.db on demand.
- The OTel exporter is OFF by default; ``current_exporter()`` returns
  ``None`` unless ``BATON_OTEL_ENABLED=1`` is set in the environment.
"""
from __future__ import annotations

from agent_baton.core.observability.otel_exporter import (
    OTelJSONLExporter,
    current_exporter,
)
from agent_baton.core.observability.prometheus import (
    MetricFamily,
    MetricSample,
    to_text_exposition,
)

__all__ = [
    "MetricFamily",
    "MetricSample",
    "OTelJSONLExporter",
    "current_exporter",
    "to_text_exposition",
]
