"""ANSI color support with automatic terminal detection."""
from __future__ import annotations

import os
import sys

# Global color state — disabled by --no-color flag or non-TTY output
_enabled: bool | None = None


def _detect_color() -> bool:
    """Detect whether to use color output."""
    # Explicit override via environment
    if os.environ.get("NO_COLOR"):  # https://no-color.org/
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    # Check if stdout is a terminal
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def set_color_enabled(enabled: bool) -> None:
    """Override color detection (used by --no-color flag)."""
    global _enabled
    _enabled = enabled


def is_color_enabled() -> bool:
    """Check whether color output is enabled."""
    global _enabled
    if _enabled is None:
        _enabled = _detect_color()
    return _enabled


def _wrap(code: str, text: str) -> str:
    if not is_color_enabled():
        return text
    return f"\033[{code}m{text}\033[0m"


# Semantic color functions
def success(text: str) -> str:
    """Green text for success indicators."""
    return _wrap("32", text)


def error(text: str) -> str:
    """Red text for error indicators."""
    return _wrap("31", text)


def warning(text: str) -> str:
    """Yellow text for warnings."""
    return _wrap("33", text)


def info(text: str) -> str:
    """Cyan text for informational highlights."""
    return _wrap("36", text)


def dim(text: str) -> str:
    """Dim/gray text for secondary information."""
    return _wrap("2", text)


def bold(text: str) -> str:
    """Bold text for emphasis."""
    return _wrap("1", text)
