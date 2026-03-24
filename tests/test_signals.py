"""Unit tests for SignalHandler (agent_baton.core.runtime.signals)."""
from __future__ import annotations

import asyncio
import signal

import pytest

from agent_baton.core.runtime.signals import SignalHandler


# ===========================================================================
# SignalHandler — unit tests
# ===========================================================================

class TestSignalHandlerInitialState:
    # DECISION: merged test_initial_state_not_shutdown + test_not_installed_by_default
    # into one test — both assert properties of a freshly-constructed handler and
    # there is no benefit to splitting a two-assertion factory check.
    def test_fresh_handler_not_shutdown_and_not_installed(self) -> None:
        """A freshly-constructed handler has shutdown_requested=False and _installed=False."""
        async def _run():
            handler = SignalHandler()
            assert handler.shutdown_requested is False
            assert handler._installed is False
        asyncio.run(_run())


class TestSignalHandlerOnSignal:
    def test_shutdown_requested_after_on_signal(self) -> None:
        """Calling _on_signal() directly marks shutdown_requested True."""
        async def _run():
            handler = SignalHandler()
            assert handler.shutdown_requested is False
            handler._on_signal(signal.SIGTERM)
            assert handler.shutdown_requested is True
        asyncio.run(_run())

    def test_on_signal_with_sigint(self) -> None:
        """SIGINT (Ctrl-C equivalent) also triggers shutdown."""
        async def _run():
            handler = SignalHandler()
            handler._on_signal(signal.SIGINT)
            assert handler.shutdown_requested is True
        asyncio.run(_run())

    def test_on_signal_idempotent(self) -> None:
        """Calling _on_signal() multiple times does not raise."""
        async def _run():
            handler = SignalHandler()
            handler._on_signal(signal.SIGTERM)
            handler._on_signal(signal.SIGTERM)
            assert handler.shutdown_requested is True
        asyncio.run(_run())


class TestSignalHandlerWait:
    def test_wait_completes_after_on_signal(self) -> None:
        """wait() returns immediately if shutdown has already been requested."""
        async def _run():
            handler = SignalHandler()
            handler._on_signal(signal.SIGTERM)
            # Should not time out because the event is already set.
            await asyncio.wait_for(handler.wait(), timeout=1.0)
            assert handler.shutdown_requested is True
        asyncio.run(_run())

    def test_wait_unblocks_after_signal_fired_concurrently(self) -> None:
        """wait() blocks until _on_signal() fires from another coroutine."""
        async def _run():
            handler = SignalHandler()

            async def _fire_signal():
                await asyncio.sleep(0.05)
                handler._on_signal(signal.SIGTERM)

            fire_task = asyncio.create_task(_fire_signal())
            await asyncio.wait_for(handler.wait(), timeout=2.0)
            assert handler.shutdown_requested is True
            await fire_task
        asyncio.run(_run())


class TestSignalHandlerInstallUninstall:
    def test_install_sets_installed_flag(self) -> None:
        """install() marks the handler as installed."""
        async def _run():
            handler = SignalHandler()
            handler.install()
            try:
                assert handler._installed is True
            finally:
                handler.uninstall()
        asyncio.run(_run())

    def test_uninstall_clears_installed_flag(self) -> None:
        """uninstall() after install() clears the installed flag."""
        async def _run():
            handler = SignalHandler()
            handler.install()
            handler.uninstall()
            assert handler._installed is False
        asyncio.run(_run())

    # DECISION: parameterized install_is_idempotent + uninstall_is_idempotent into
    # one test. Both follow the exact same pattern (call twice, assert final state).
    @pytest.mark.parametrize("action,expected_state", [
        ("install", True),
        ("uninstall", False),
    ])
    def test_double_call_is_idempotent(self, action: str, expected_state: bool) -> None:
        """Calling install() or uninstall() twice does not raise and leaves a consistent state."""
        async def _run():
            handler = SignalHandler()
            handler.install()
            if action == "install":
                handler.install()  # second call: no-op
                try:
                    assert handler._installed is expected_state
                finally:
                    handler.uninstall()
            else:
                handler.uninstall()
                handler.uninstall()  # second call: no-op
                assert handler._installed is expected_state
        asyncio.run(_run())

    def test_install_restores_original_handlers_on_uninstall(self) -> None:
        """Original signal handlers are restored when uninstall() is called."""
        original_sigterm = signal.getsignal(signal.SIGTERM)
        original_sigint = signal.getsignal(signal.SIGINT)
        async def _run():
            handler = SignalHandler()
            handler.install()
            handler.uninstall()
            assert signal.getsignal(signal.SIGTERM) == original_sigterm
            assert signal.getsignal(signal.SIGINT) == original_sigint
        asyncio.run(_run())

    def test_original_handlers_stored_on_install(self) -> None:
        """install() saves the pre-existing handlers for restoration."""
        original_sigterm = signal.getsignal(signal.SIGTERM)
        async def _run():
            handler = SignalHandler()
            handler.install()
            try:
                assert signal.SIGTERM in handler._original_handlers
                assert handler._original_handlers[signal.SIGTERM] == original_sigterm
            finally:
                handler.uninstall()
        asyncio.run(_run())
