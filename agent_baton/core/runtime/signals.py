"""POSIX signal handling for graceful daemon shutdown.

Installs SIGTERM/SIGINT handlers that set a cancellation event so the
worker loop can drain in-flight agents before exiting.
"""
from __future__ import annotations

import asyncio
import signal
from typing import Callable


class SignalHandler:
    """Installs POSIX signal handlers and exposes an asyncio shutdown event.

    Used by ``WorkerSupervisor`` to enable graceful daemon shutdown.  When
    SIGTERM or SIGINT is received, the shutdown event is set, which allows
    the worker loop to drain in-flight agents before exiting rather than
    killing them abruptly.

    The handler preserves and restores original signal handlers on
    ``uninstall()``, making it safe to use in contexts where other code
    also installs signal handlers.

    Usage::

        handler = SignalHandler()
        handler.install()          # installs SIGTERM + SIGINT handlers
        await handler.wait()       # blocks until signal received
        handler.uninstall()        # restores original handlers

    Attributes:
        _shutdown: asyncio.Event set when a signal is received.
        _original_handlers: Saved original handlers for restoration.
        _installed: Guard against double-install.
    """

    def __init__(self) -> None:
        self._shutdown = asyncio.Event()
        self._original_handlers: dict[int, object] = {}
        self._installed = False

    @property
    def shutdown_requested(self) -> bool:
        """True once a SIGTERM or SIGINT has been received."""
        return self._shutdown.is_set()

    def install(self) -> None:
        """Install signal handlers for SIGTERM and SIGINT."""
        if self._installed:
            return
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            self._original_handlers[sig] = signal.getsignal(sig)
            loop.add_signal_handler(sig, self._on_signal, sig)
        self._installed = True

    def uninstall(self) -> None:
        """Restore original signal handlers."""
        if not self._installed:
            return
        loop = asyncio.get_running_loop()
        for sig, original in self._original_handlers.items():
            loop.remove_signal_handler(sig)
            signal.signal(sig, original)
        self._original_handlers.clear()
        self._installed = False

    async def wait(self) -> None:
        """Block until a shutdown signal is received."""
        await self._shutdown.wait()

    def _on_signal(self, signum: int) -> None:
        """Handler invoked by asyncio when a signal arrives."""
        self._shutdown.set()
