"""Tests for PagerDutyNotifier (O1.8)."""
from __future__ import annotations

import argparse
import json
import os
from io import BytesIO
from unittest.mock import MagicMock

import pytest

from agent_baton.core.observe.pagerduty import PagerDutyNotifier
from agent_baton.core.observe.incidents import IncidentStore
from agent_baton.cli.commands.observe.pagerduty_cmd import handler, register


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(dedup_key: str = "abc-123") -> MagicMock:
    """Return a mock HTTP client that simulates a successful PD response."""
    response_body = json.dumps(
        {"status": "success", "message": "Event processed", "dedup_key": dedup_key}
    ).encode()
    mock_response = MagicMock()
    mock_response.read.return_value = response_body

    client = MagicMock()
    client.urlopen.return_value = mock_response
    return client


def _make_failing_client(exc: Exception) -> MagicMock:
    client = MagicMock()
    client.urlopen.side_effect = exc
    return client


# ---------------------------------------------------------------------------
# Test 1: No routing key → notify_incident returns None
# ---------------------------------------------------------------------------

def test_no_routing_key_returns_none(monkeypatch):
    monkeypatch.delenv("BATON_PAGERDUTY_KEY", raising=False)
    notifier = PagerDutyNotifier(routing_key=None)
    result = notifier.notify_incident("INC-001", "error", "Something failed")
    assert result is None


# ---------------------------------------------------------------------------
# Test 2: Mock client success → returns dedup_key
# ---------------------------------------------------------------------------

def test_mock_client_success_returns_dedup_key():
    client = _make_client("xyz-789")
    notifier = PagerDutyNotifier(routing_key="fake-key", client=client)
    result = notifier.notify_incident(
        "INC-002", "critical", "DB down", {"host": "db-primary"}
    )
    assert result == "xyz-789"
    client.urlopen.assert_called_once()


# ---------------------------------------------------------------------------
# Test 3: Mock client raises → returns None (no exception escapes)
# ---------------------------------------------------------------------------

def test_mock_client_raises_returns_none():
    client = _make_failing_client(OSError("connection refused"))
    notifier = PagerDutyNotifier(routing_key="fake-key", client=client)
    result = notifier.notify_incident("INC-003", "warning", "Slow response")
    assert result is None  # no exception propagated


# ---------------------------------------------------------------------------
# Test 4: Invalid severity raises ValueError
# ---------------------------------------------------------------------------

def test_invalid_severity_raises_value_error():
    notifier = PagerDutyNotifier(routing_key="fake-key")
    with pytest.raises(ValueError, match="Invalid severity"):
        notifier.notify_incident("INC-004", "CRITICAL", "wrong case")

    with pytest.raises(ValueError, match="Invalid severity"):
        notifier.notify_incident("INC-005", "high", "not a valid level")


# ---------------------------------------------------------------------------
# Test 5: Env var fallback works
# ---------------------------------------------------------------------------

def test_env_var_routing_key_fallback(monkeypatch):
    monkeypatch.setenv("BATON_PAGERDUTY_KEY", "env-routing-key")
    client = _make_client("env-dedup")
    notifier = PagerDutyNotifier(client=client)  # no routing_key arg
    assert notifier._routing_key == "env-routing-key"
    result = notifier.notify_incident("INC-006", "info", "All OK")
    assert result == "env-dedup"


# ---------------------------------------------------------------------------
# Test 6: CLI test command runs without crashing in mock mode
# ---------------------------------------------------------------------------

def test_cli_test_command_no_crash(monkeypatch, capsys):
    monkeypatch.delenv("BATON_PAGERDUTY_KEY", raising=False)
    # Simulate args with no routing key → should print error, not raise
    args = argparse.Namespace(pd_command="test", routing_key=None)
    handler(args)
    captured = capsys.readouterr()
    assert "routing key" in captured.out.lower()


def test_cli_test_command_with_key(monkeypatch, capsys):
    """CLI test command sends event when routing key is provided via mock."""
    client = _make_client("cli-dedup")

    # Patch PagerDutyNotifier inside the CLI module
    import agent_baton.cli.commands.observe.pagerduty_cmd as cmd_mod
    original_cls = cmd_mod.PagerDutyNotifier

    class _PatchedNotifier(PagerDutyNotifier):
        def __init__(self, routing_key=None, **kwargs):
            super().__init__(routing_key=routing_key, client=client)

    monkeypatch.setattr(cmd_mod, "PagerDutyNotifier", _PatchedNotifier)
    try:
        args = argparse.Namespace(pd_command="test", routing_key="test-key")
        handler(args)
        captured = capsys.readouterr()
        assert "cli-dedup" in captured.out
    finally:
        monkeypatch.setattr(cmd_mod, "PagerDutyNotifier", original_cls)


# ---------------------------------------------------------------------------
# Test 7: IncidentStore forwards to notifier for warning/error/critical
# ---------------------------------------------------------------------------

def test_incident_store_calls_notifier_for_actionable_severities():
    notifier = MagicMock(spec=PagerDutyNotifier)
    store = IncidentStore(notifier=notifier)

    store.record_incident("INC-010", "error", "Disk full")
    notifier.notify_incident.assert_called_once_with(
        incident_id="INC-010",
        severity="error",
        summary="Disk full",
        details=None,
    )


def test_incident_store_skips_notifier_for_info():
    notifier = MagicMock(spec=PagerDutyNotifier)
    store = IncidentStore(notifier=notifier)

    store.record_incident("INC-011", "info", "Health check OK")
    notifier.notify_incident.assert_not_called()


def test_incident_store_no_notifier_does_not_crash():
    store = IncidentStore()  # no notifier
    store.record_incident("INC-012", "critical", "Everything on fire")
    assert len(store.list_incidents()) == 1


def test_incident_store_notification_failure_does_not_propagate():
    notifier = MagicMock(spec=PagerDutyNotifier)
    notifier.notify_incident.side_effect = RuntimeError("network gone")
    store = IncidentStore(notifier=notifier)

    # Must not raise
    store.record_incident("INC-013", "critical", "Boom")
    assert len(store.list_incidents()) == 1


# ---------------------------------------------------------------------------
# Test 8: register() wires up the pagerduty subparser
# ---------------------------------------------------------------------------

def test_register_creates_pagerduty_parser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    sp = register(sub)
    assert sp is not None
    assert "pagerduty" in sp.prog
