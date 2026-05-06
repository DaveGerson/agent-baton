"""Smoke test for bd-8944: execute handler emits exactly one validation_error
with a message that includes every registered subcommand.

The test introspects the argparse subparsers registered by register()
and verifies the single handler error message covers them all.
"""
from __future__ import annotations

import argparse

import pytest

from agent_baton.cli.commands.execution.execute import register


def _registered_subcommands() -> list[str]:
    """Return the subcommand names registered by register()."""
    root = argparse.ArgumentParser(prog="baton")
    root_subs = root.add_subparsers()
    execute_parser = register(root_subs)
    for action in execute_parser._subparsers._group_actions:
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return list(action.choices.keys())
    return []


class TestHandlerRegistersSubcommandsCorrectly:
    """bd-8944: handler emits exactly one validation_error and its message
    includes every registered subcommand name."""

    def test_single_validation_error_on_missing_subcommand(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: list[str] = []

        def _capturing_ve(msg: str, *args: object, **kwargs: object) -> None:  # type: ignore[misc]
            captured.append(msg)
            raise SystemExit(1)

        monkeypatch.setattr(
            "agent_baton.cli.commands.execution.execute.validation_error",
            _capturing_ve,
        )

        ns = argparse.Namespace(subcommand=None)
        from agent_baton.cli.commands.execution.execute import handler

        with pytest.raises(SystemExit):
            handler(ns)

        assert len(captured) == 1, (
            f"bd-8944: handler must emit exactly ONE validation_error when "
            f"subcommand is None; got {len(captured)}: {captured}"
        )

    def test_error_message_covers_registered_subcommands(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: list[str] = []

        def _capturing_ve(msg: str, *args: object, **kwargs: object) -> None:  # type: ignore[misc]
            captured.append(msg)
            raise SystemExit(1)

        monkeypatch.setattr(
            "agent_baton.cli.commands.execution.execute.validation_error",
            _capturing_ve,
        )

        ns = argparse.Namespace(subcommand=None)
        from agent_baton.cli.commands.execution.execute import handler

        with pytest.raises(SystemExit):
            handler(ns)

        msg = captured[0]
        registered = _registered_subcommands()
        missing = [sc for sc in registered if sc not in msg]
        assert not missing, (
            f"These registered subcommands are absent from the validation_error "
            f"message: {missing}\nMessage was: {msg!r}"
        )
