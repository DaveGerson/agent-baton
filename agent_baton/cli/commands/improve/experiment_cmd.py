"""``baton experiment`` -- DEPRECATED alias.

Use ``baton learn`` subcommands instead.

This module keeps the old top-level ``baton experiment`` command working so that
existing scripts are not broken.  A deprecation warning is printed to stderr
on every invocation (including --help, via main.py pre-parse hook).
"""
from __future__ import annotations

import argparse
import sys


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "experiment",
        help="[DEPRECATED] Use 'baton learn' subcommands instead",
        description=(
            "DEPRECATED: Use 'baton learn' subcommands instead.\n\n"
            "The 'experiment' command has been consolidated into 'baton learn'."
        ),
    )
    # Accept any unknown flags/positionals so old scripts using e.g.
    # `baton experiment --id foo` get the deprecation message instead of an
    # argparse "unrecognized arguments" error (bd-ed80).
    p.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help=argparse.SUPPRESS,
    )
    return p


def handler(args: argparse.Namespace) -> None:
    print(
        "warning: `baton experiment` is deprecated; use `baton learn` subcommands instead."
        " This alias will be removed in a future release.",
        file=sys.stderr,
    )
    print("Run 'baton learn --help' to see available subcommands.")
