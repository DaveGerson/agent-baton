"""``baton verify-package`` -- validate a ``.tar.gz`` package before distribution.

Runs structural and content checks on a package archive: verifies the
manifest is present and valid, checks agent frontmatter, validates
references, and optionally displays per-file SHA-256 checksums.

Exit code 1 if validation fails.

Delegates to:
    :class:`~agent_baton.core.distribute.packager.PackageVerifier`
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_baton.core.distribute.packager import PackageVerifier


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "verify-package",
        help="Validate a .tar.gz agent-baton package before distribution",
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
    archive_path = Path(args.archive)
    verifier = PackageVerifier()

    result = verifier.validate_package(archive_path)

    # Header
    status_label = "PASS" if result.valid else "FAIL"
    print(f"Package: {archive_path.name}  [{status_label}]")
    print(
        f"Contents: {result.agent_count} agent(s), "
        f"{result.reference_count} reference(s), "
        f"{result.knowledge_count} knowledge pack(s)"
    )

    # Errors
    if result.errors:
        print(f"\nErrors ({len(result.errors)}):")
        for err in result.errors:
            print(f"  [ERROR] {err}")

    # Warnings
    if result.warnings:
        print(f"\nWarnings ({len(result.warnings)}):")
        for warn in result.warnings:
            print(f"  [WARN]  {warn}")

    # Checksums (optional)
    if args.checksums and result.checksums:
        print(f"\nChecksums ({len(result.checksums)} files):")
        for member_name, digest in sorted(result.checksums.items()):
            print(f"  {digest}  {member_name}")

    if not result.errors and not result.warnings:
        print("\nAll checks passed.")

    if not result.valid:
        sys.exit(1)
