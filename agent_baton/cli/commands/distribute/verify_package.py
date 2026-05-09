"""``baton verify-package`` -- DEPRECATED alias.

Use ``baton sync --verify ARCHIVE`` instead.

This module keeps the old top-level ``baton verify-package`` command working
so that existing scripts and CI pipelines are not broken.  A deprecation
warning is printed to stderr on every invocation.
"""
from __future__ import annotations

import argparse
import sys


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "verify-package",
        help="[DEPRECATED] Use 'baton sync --verify ARCHIVE' instead",
        description=(
            "DEPRECATED: Use 'baton sync --verify ARCHIVE' instead.\n\n"
            "Validates a .tar.gz agent-baton package before distribution."
        ),
    )
    p.add_argument(
        "archive",
        metavar="ARCHIVE",
        help="Path to the .tar.gz package to verify",
    )
    p.add_argument(
        "--checksums",
        action="store_true",
        default=False,
        help="Display per-file SHA-256 checksums alongside validation results",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    print(
        "warning: `baton verify-package` is deprecated; use `baton sync --verify ARCHIVE`"
        " instead. This alias will be removed in a future release.",
        file=sys.stderr,
    )
    from agent_baton.cli.commands.distribute.install import _cmd_verify
    _cmd_verify(args)
