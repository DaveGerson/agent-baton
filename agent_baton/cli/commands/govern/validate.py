"""baton validate — validate agent definition .md files."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_baton.core.govern.validator import AgentValidator, ValidationResult


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("validate", help="Validate agent .md files")
    p.add_argument(
        "paths",
        nargs="+",
        help="File or directory paths to validate",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors (exit code 1 if any warnings)",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    strict: bool = args.strict
    validator = AgentValidator()

    all_results: list[ValidationResult] = []
    for raw_path in args.paths:
        target = Path(raw_path)
        if target.is_dir():
            all_results.extend(validator.validate_directory(target))
        elif target.is_file():
            all_results.append(validator.validate_file(target))
        else:
            all_results.append(
                ValidationResult(
                    path=target,
                    valid=False,
                    errors=[f"'{target}' does not exist"],
                )
            )

    valid_count = 0
    warn_count = 0
    error_count = 0

    for result in all_results:
        has_errors = bool(result.errors)
        # In strict mode, warnings are treated as errors for exit-code purposes
        has_warnings = bool(result.warnings)
        effective_fail = has_errors or (strict and has_warnings)

        if effective_fail:
            print(f"  {result.path}")
            for msg in result.errors:
                print(f"    error: {msg}")
            if strict:
                for msg in result.warnings:
                    print(f"    warning (strict): {msg}")
            elif has_warnings:
                for msg in result.warnings:
                    print(f"    warning: {msg}")
            error_count += 1
        elif has_warnings:
            print(f"  {result.path}")
            for msg in result.warnings:
                print(f"    warning: {msg}")
            warn_count += 1
        else:
            print(f"  {result.path}")
            valid_count += 1

    total = len(all_results)
    print(
        f"\nValidated {total} file{'s' if total != 1 else ''}: "
        f"{valid_count} valid, {warn_count} warnings, {error_count} errors"
    )

    if error_count > 0:
        sys.exit(1)
