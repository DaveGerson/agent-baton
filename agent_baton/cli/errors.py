"""Standardized CLI error handling with semantic exit codes."""
from __future__ import annotations

import sys
from typing import NoReturn

# Semantic exit codes
EXIT_SUCCESS = 0
EXIT_RUNTIME = 1    # Runtime error (file I/O, backend failure, system error)
EXIT_VALIDATION = 2  # Validation error (bad input, missing required arg, invalid format)


class BatonError(RuntimeError):
    """Structured runtime error for agent-baton subsystems.

    Raised in preference to bare ``RuntimeError`` or ``ImportError`` so that
    callers (CLI, API, tests) can distinguish user-facing errors from
    unexpected programming faults.

    Attributes:
        message: Human-readable description of the failure.
        hint: Optional recovery suggestion (e.g. an install command).
    """

    def __init__(self, message: str, *, hint: str = "") -> None:
        self.message = message
        self.hint = hint
        full = message if not hint else f"{message}\n  hint: {hint}"
        super().__init__(full)


def user_error(
    msg: str,
    *,
    hint: str = "",
    docs: str = "",
    exit_code: int = EXIT_RUNTIME,
) -> NoReturn:
    """Print a standardized error message and exit.

    Args:
        msg: The error message (will be prefixed with "error: ").
        hint: Optional recovery hint (printed on next line).
        docs: Optional documentation reference.
        exit_code: Exit code (EXIT_RUNTIME=1, EXIT_VALIDATION=2).
    """
    print(f"error: {msg}", file=sys.stderr)
    if hint:
        print(f"  {hint}", file=sys.stderr)
    if docs:
        print(f"  See: {docs}", file=sys.stderr)
    sys.exit(exit_code)


def validation_error(msg: str, *, hint: str = "", docs: str = "") -> NoReturn:
    """Shortcut for user_error with EXIT_VALIDATION."""
    user_error(msg, hint=hint, docs=docs, exit_code=EXIT_VALIDATION)
