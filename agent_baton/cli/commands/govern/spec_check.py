"""``baton spec-check`` -- run spec validation checks against agent outputs.

Validates that agent outputs conform to a specification.  Three validation
modes are supported:

* ``--json DATA --schema SCHEMA`` -- Validate a JSON file against a
  JSON Schema.
* ``--files ROOT --expect file1,file2,...`` -- Check that expected files
  exist under a directory root.
* ``--exports MODULE --expect name1,name2,...`` -- Check that a Python
  module exports expected names.

Each check is reported as PASS or FAIL with a message.  Exit code 1
if any checks fail.

Delegates to:
    :class:`~agent_baton.core.govern.spec_validator.SpecValidator`
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_baton.core.govern.spec_validator import SpecValidator


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "spec-check", help="Validate agent output against a spec"
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--json",
        metavar="DATA_FILE",
        help="JSON data file to validate",
    )
    mode.add_argument(
        "--files",
        metavar="ROOT",
        help="Directory root to check for expected files",
    )
    mode.add_argument(
        "--exports",
        metavar="MODULE",
        help="Python module file to check for expected exports",
    )
    p.add_argument(
        "--schema",
        metavar="SCHEMA_FILE",
        help="JSON Schema file (used with --json)",
    )
    p.add_argument(
        "--expect",
        metavar="NAMES",
        help="Comma-separated list of expected files or names (used with --files / --exports)",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    validator = SpecValidator()

    if args.json and args.schema:
        result = validator.validate_json_against_schema(
            Path(args.json), Path(args.schema)
        )
    elif args.files and args.expect:
        expected = [f.strip() for f in args.expect.split(",") if f.strip()]
        result = validator.validate_file_structure(Path(args.files), expected)
    elif args.exports and args.expect:
        expected = [n.strip() for n in args.expect.split(",") if n.strip()]
        result = validator.validate_exports(Path(args.exports), expected)
    else:
        print(
            "error: supply one of:\n"
            "  --json DATA --schema SCHEMA\n"
            "  --files ROOT --expect file1,file2,...\n"
            "  --exports MODULE --expect name1,name2,...",
        )
        sys.exit(1)

    for check in result.checks:
        status = "PASS" if check.passed else "FAIL"
        line = f"  [{status}] {check.name}"
        if check.message and not check.passed:
            line += f": {check.message}"
        print(line)

    print(f"\n{result.summary}")

    if not result.passed:
        sys.exit(1)
