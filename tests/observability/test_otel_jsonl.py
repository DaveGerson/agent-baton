"""Tests for the OTLP-shaped JSONL span exporter (O1.4)."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_baton.core.observability.otel_exporter import (
    OTelJSONLExporter,
    current_exporter,
)

_HEX_TRACE = re.compile(r"^[0-9a-f]{32}$")
_HEX_SPAN = re.compile(r"^[0-9a-f]{16}$")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset OTel env vars between tests so leakage can't mask bugs."""
    for var in ("BATON_OTEL_ENABLED", "BATON_OTEL_PATH"):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Direct exporter behaviour
# ---------------------------------------------------------------------------


class TestExporterShape:
    def test_record_span_writes_one_jsonl_line(self, tmp_path: Path) -> None:
        path = tmp_path / "spans.jsonl"
        exporter = OTelJSONLExporter(path=path)

        exporter.record_span(
            name="plan.create",
            kind="INTERNAL",
            attributes={"task_id": "abc"},
            started_at=datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc),
            ended_at=datetime(2026, 4, 25, 12, 0, 1, tzinfo=timezone.utc),
        )

        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        # Each line must be self-contained valid JSON (NDJSON / JSONL).
        json.loads(lines[0])

    def test_two_spans_append_two_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "spans.jsonl"
        exporter = OTelJSONLExporter(path=path)

        for i in range(2):
            exporter.record_span(
                name="step.dispatch",
                kind="INTERNAL",
                attributes={"i": i},
            )

        assert len(path.read_text().splitlines()) == 2

    def test_trace_id_is_32_char_hex(self, tmp_path: Path) -> None:
        path = tmp_path / "spans.jsonl"
        span = OTelJSONLExporter(path=path).record_span(
            name="x", kind="INTERNAL"
        )
        assert _HEX_TRACE.match(span["traceId"]), span["traceId"]

    def test_span_id_is_16_char_hex(self, tmp_path: Path) -> None:
        path = tmp_path / "spans.jsonl"
        span = OTelJSONLExporter(path=path).record_span(
            name="x", kind="INTERNAL"
        )
        assert _HEX_SPAN.match(span["spanId"]), span["spanId"]

    def test_parent_span_id_passthrough(self, tmp_path: Path) -> None:
        path = tmp_path / "spans.jsonl"
        parent = "0123456789abcdef"
        span = OTelJSONLExporter(path=path).record_span(
            name="x", kind="INTERNAL", parent_span_id=parent
        )
        assert span["parentSpanId"] == parent


# ---------------------------------------------------------------------------
# OTLP attribute shape
# ---------------------------------------------------------------------------


class TestOtlpAttributeShape:
    def test_string_attribute_uses_string_value(self, tmp_path: Path) -> None:
        path = tmp_path / "spans.jsonl"
        exporter = OTelJSONLExporter(path=path)
        exporter.record_span(
            name="plan.create",
            kind="INTERNAL",
            attributes={"task_id": "abc-123"},
        )

        line = path.read_text().splitlines()[0]
        payload = json.loads(line)
        attrs = {kv["key"]: kv["value"] for kv in payload["attributes"]}
        assert attrs["task_id"] == {"stringValue": "abc-123"}

    def test_int_attribute_uses_int_value_as_string(self, tmp_path: Path) -> None:
        # OTLP encodes intValue as a JSON string to preserve 64-bit precision.
        path = tmp_path / "spans.jsonl"
        exporter = OTelJSONLExporter(path=path)
        exporter.record_span(
            name="step.dispatch",
            kind="INTERNAL",
            attributes={"agent_count": 3},
        )

        line = path.read_text().splitlines()[0]
        payload = json.loads(line)
        attrs = {kv["key"]: kv["value"] for kv in payload["attributes"]}
        assert attrs["agent_count"] == {"intValue": "3"}

    def test_bool_attribute_uses_bool_value(self, tmp_path: Path) -> None:
        path = tmp_path / "spans.jsonl"
        exporter = OTelJSONLExporter(path=path)
        exporter.record_span(
            name="gate.run",
            kind="INTERNAL",
            attributes={"passed": True},
        )

        line = path.read_text().splitlines()[0]
        payload = json.loads(line)
        attrs = {kv["key"]: kv["value"] for kv in payload["attributes"]}
        assert attrs["passed"] == {"boolValue": True}

    def test_float_attribute_uses_double_value(self, tmp_path: Path) -> None:
        path = tmp_path / "spans.jsonl"
        exporter = OTelJSONLExporter(path=path)
        exporter.record_span(
            name="step.dispatch",
            kind="INTERNAL",
            attributes={"duration": 1.25},
        )

        line = path.read_text().splitlines()[0]
        payload = json.loads(line)
        attrs = {kv["key"]: kv["value"] for kv in payload["attributes"]}
        assert attrs["duration"] == {"doubleValue": 1.25}


# ---------------------------------------------------------------------------
# Time conversion
# ---------------------------------------------------------------------------


class TestUnixNano:
    def test_start_and_end_are_unix_nano_strings(self, tmp_path: Path) -> None:
        path = tmp_path / "spans.jsonl"
        exporter = OTelJSONLExporter(path=path)
        started = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        ended = datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc)

        span = exporter.record_span(
            name="x", kind="INTERNAL", started_at=started, ended_at=ended,
        )
        # 2026-01-01T00:00:00Z = 1767225600 seconds since epoch.
        assert span["startTimeUnixNano"] == str(1767225600 * 10**9)
        # End is exactly 1 second later.
        assert int(span["endTimeUnixNano"]) - int(span["startTimeUnixNano"]) == 10**9


# ---------------------------------------------------------------------------
# current_exporter() — env gating
# ---------------------------------------------------------------------------


class TestCurrentExporter:
    def test_returns_none_when_disabled(self) -> None:
        # _clean_env fixture guarantees the env var is unset.
        assert current_exporter() is None

    def test_returns_none_when_value_is_falsy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BATON_OTEL_ENABLED", "0")
        assert current_exporter() is None
        monkeypatch.setenv("BATON_OTEL_ENABLED", "false")
        assert current_exporter() is None

    def test_returns_exporter_when_enabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("BATON_OTEL_ENABLED", "1")
        monkeypatch.setenv("BATON_OTEL_PATH", str(tmp_path / "out.jsonl"))

        exporter = current_exporter()
        assert exporter is not None
        assert exporter.path == tmp_path / "out.jsonl"

    def test_disabled_exporter_writes_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # With BATON_OTEL_ENABLED unset, current_exporter returns None and
        # no file should ever be created at the default or custom path.
        monkeypatch.setenv("BATON_OTEL_PATH", str(tmp_path / "must-not-exist.jsonl"))
        exporter = current_exporter()
        assert exporter is None
        assert not (tmp_path / "must-not-exist.jsonl").exists()

    def test_accepts_truthy_aliases(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        for value in ("1", "true", "TRUE", "yes", "on"):
            monkeypatch.setenv("BATON_OTEL_ENABLED", value)
            assert current_exporter() is not None, value
