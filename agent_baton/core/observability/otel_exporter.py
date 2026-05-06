"""Stdlib-only OTLP-shaped JSONL span exporter.

Writes spans to a local newline-delimited JSON file in a shape that is
deliberately compatible with the OTLP/JSON wire format
(`OpenTelemetry Protocol`_) so the file can be replayed later through a
real OTel collector if/when the project decides to take a runtime
dependency on ``opentelemetry-sdk``.  Today, this exporter has zero
external dependencies.

.. _OpenTelemetry Protocol: https://github.com/open-telemetry/opentelemetry-proto

Behaviour
---------

- ``OTelJSONLExporter.record_span`` appends one JSON object per line to
  the configured path.  The file is opened-and-closed per write so the
  exporter is safe to use from short-lived subprocess invocations.
- IDs are generated as random hex strings (32 chars for trace IDs,
  16 chars for span IDs) per the OTLP spec.
- Attribute values use the OTLP variant union shape:
  ``{"key": ..., "value": {"stringValue": ...}}`` (or ``intValue``,
  ``doubleValue``, ``boolValue``).  Unknown types are coerced to string.

Wiring
------

Use :func:`current_exporter` at every call site so the no-op path
stays branchless::

    if (exporter := current_exporter()) is not None:
        exporter.record_span(...)

The exporter is gated on ``BATON_OTEL_ENABLED=1`` and writes to the
path in ``BATON_OTEL_PATH`` (default
``.claude/team-context/otel-spans.jsonl``).
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# Default destination — relative to the project working directory.
_DEFAULT_PATH = Path(".claude/team-context/otel-spans.jsonl")

_ENV_ENABLED = "BATON_OTEL_ENABLED"
_ENV_PATH = "BATON_OTEL_PATH"


# ---------------------------------------------------------------------------
# Attribute serialisation
# ---------------------------------------------------------------------------


def _attr_value(value: Any) -> dict[str, Any]:
    """Convert a Python value to an OTLP ``AnyValue`` dict.

    Supported variants: ``stringValue``, ``intValue``, ``doubleValue``,
    ``boolValue``.  Lists become ``arrayValue`` of ``AnyValue`` objects.
    Anything else is stringified to keep the shape valid.
    """
    if isinstance(value, bool):  # subclass of int — must come first
        return {"boolValue": bool(value)}
    if isinstance(value, int):
        # OTLP intValue is a 64-bit signed integer encoded as a string in
        # JSON to avoid 53-bit precision loss in JS clients.  We follow
        # that convention.
        return {"intValue": str(int(value))}
    if isinstance(value, float):
        return {"doubleValue": float(value)}
    if isinstance(value, (list, tuple)):
        return {"arrayValue": {"values": [_attr_value(v) for v in value]}}
    return {"stringValue": str(value)}


def _attributes_to_otlp(attrs: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert ``{key: value}`` to an OTLP ``KeyValue[]`` list."""
    return [{"key": str(k), "value": _attr_value(v)} for k, v in attrs.items()]


# ---------------------------------------------------------------------------
# Time conversion
# ---------------------------------------------------------------------------


def _datetime_to_unix_nano(dt: datetime) -> int:
    """Convert a :class:`datetime` to integer nanoseconds since epoch.

    Naive datetimes are interpreted as UTC, matching the convention used
    elsewhere in the engine (see ``schema.py`` ``strftime`` calls).
    """
    if dt.tzinfo is None:
        # interpret naive as UTC
        epoch = datetime(1970, 1, 1)
        delta = dt - epoch
    else:
        from datetime import timezone

        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        delta = dt - epoch
    seconds = int(delta.total_seconds())
    micros = delta.microseconds
    return seconds * 1_000_000_000 + micros * 1_000


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


def _new_trace_id() -> str:
    """Return a 32-char lower-case hex trace ID (16 random bytes)."""
    return secrets.token_hex(16)


def _new_span_id() -> str:
    """Return a 16-char lower-case hex span ID (8 random bytes)."""
    return secrets.token_hex(8)


# ---------------------------------------------------------------------------
# Exporter
# ---------------------------------------------------------------------------


@dataclass
class OTelJSONLExporter:
    """JSONL-backed OTLP-shaped span exporter.

    One :class:`OTelJSONLExporter` instance writes to a single output
    file.  Instances are cheap; reuse one per process where convenient
    or rely on :func:`current_exporter` for the configured singleton.
    """

    path: Path

    def record_span(
        self,
        name: str,
        kind: str,
        attributes: dict[str, Any] | None = None,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        *,
        trace_id: str | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> dict[str, Any]:
        """Append one OTLP-shaped span line to :attr:`path`.

        Args:
            name: Span name (e.g. ``"plan.create"``).
            kind: One of ``"INTERNAL"``, ``"SERVER"``, ``"CLIENT"``,
                ``"PRODUCER"``, ``"CONSUMER"``.  Free-form is accepted;
                downstream collectors may reject unknown values.
            attributes: Span attributes; serialised to OTLP
                ``KeyValue[]`` shape.
            started_at: Span start time.  Defaults to *now* when ``None``.
            ended_at: Span end time.  Defaults to ``started_at`` when
                ``None`` (zero-duration span).
            trace_id: Hex trace ID; one is generated when not supplied.
            span_id: Hex span ID; one is generated when not supplied.
            parent_span_id: Optional hex parent span ID.

        Returns:
            The span dict that was written, useful for tests and
            chained instrumentation.
        """
        attrs = attributes or {}
        started_at = started_at or datetime.utcnow()
        ended_at = ended_at or started_at

        span = {
            "traceId": trace_id or _new_trace_id(),
            "spanId": span_id or _new_span_id(),
            "parentSpanId": parent_span_id or "",
            "name": str(name),
            "kind": str(kind),
            "startTimeUnixNano": str(_datetime_to_unix_nano(started_at)),
            "endTimeUnixNano": str(_datetime_to_unix_nano(ended_at)),
            "attributes": _attributes_to_otlp(attrs),
        }

        # Make sure the directory exists; this is cheap and idempotent.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(span, separators=(",", ":")) + "\n")
        return span


# ---------------------------------------------------------------------------
# Module-level helper — env-gated singleton
# ---------------------------------------------------------------------------


def _resolve_path() -> Path:
    """Resolve the JSONL output path from the environment."""
    raw = os.environ.get(_ENV_PATH)
    return Path(raw) if raw else _DEFAULT_PATH


def current_exporter() -> OTelJSONLExporter | None:
    """Return the configured exporter, or ``None`` when disabled.

    The exporter is enabled when ``BATON_OTEL_ENABLED=1`` (any of
    ``"1"``, ``"true"``, ``"yes"`` — case-insensitive).  When unset or
    set to a falsy value, this returns ``None`` and callers should
    skip span emission entirely.

    Implementation note: a fresh :class:`OTelJSONLExporter` is returned
    on each call so tests and subprocesses can mutate the env between
    invocations and see the change immediately.  The cost is a single
    cheap dataclass allocation per call.
    """
    raw = os.environ.get(_ENV_ENABLED, "").strip().lower()
    if raw not in {"1", "true", "yes", "on"}:
        return None
    return OTelJSONLExporter(path=_resolve_path())
