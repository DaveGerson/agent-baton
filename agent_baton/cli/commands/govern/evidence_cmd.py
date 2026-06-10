"""``baton evidence`` -- build and verify per-task evidence bundles.

Sub-commands
------------
bundle
    Build a verifiable evidence bundle for a completed task.
verify
    Check an existing bundle directory or ``.tar.gz`` archive.

Usage
-----
::

    baton evidence bundle <task_id>
                         [--output DIR]
                         [--sign]
                         [--tar]
                         [--db PATH]
                         [--compliance-log PATH]
                         [--packs-dir PATH]
                         [--soul-db PATH]

    baton evidence verify <path>
                          [--strict]

007 Phase H (bd-TODO).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from agent_baton.core.govern.evidence_bundle import EvidenceBundleBuilder, verify_bundle


# ---------------------------------------------------------------------------
# Argparse registration
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    p = subparsers.add_parser(
        "evidence",
        help="Build and verify per-task evidence bundles (007 Phase H)",
        description=(
            "Build a verifiable, self-contained evidence bundle for a completed "
            "task (``bundle``) or check the integrity of an existing one "
            "(``verify``).  Bundles include the AIBOM, compliance segment, "
            "gate results, auditor verdicts, approvals, and assurance packs."
        ),
    )

    sub = p.add_subparsers(dest="evidence_subcmd", metavar="SUBCOMMAND")
    sub.required = True

    # ---- bundle ------------------------------------------------------------
    pb = sub.add_parser(
        "bundle",
        help="Build an evidence bundle for a task",
        description=(
            "Collect AIBOM, compliance segment, gate results, verdicts, "
            "approvals, and assurance packs into a signed, hash-verified "
            "bundle directory (or .tar.gz with --tar)."
        ),
    )
    pb.add_argument(
        "task_id",
        metavar="TASK_ID",
        help="Task ID to build the evidence bundle for",
    )
    pb.add_argument(
        "--output",
        metavar="DIR",
        default=None,
        help=(
            "Directory to write the bundle under (default: .claude/team-context/). "
            "Bundle is written to <DIR>/evidence/<TASK_ID>/"
        ),
    )
    pb.add_argument(
        "--sign",
        action="store_true",
        help=(
            "Sign the manifest with a soul key "
            "(requires BATON_SOULS_ENABLED=1 and the cryptography package)"
        ),
    )
    pb.add_argument(
        "--tar",
        action="store_true",
        help="Package the bundle as a .tar.gz and remove the directory",
    )
    pb.add_argument(
        "--db",
        metavar="PATH",
        default=None,
        help="Override baton.db location (default: .claude/team-context/baton.db)",
    )
    pb.add_argument(
        "--compliance-log",
        metavar="PATH",
        default=None,
        dest="compliance_log",
        help="Override compliance-audit.jsonl path",
    )
    pb.add_argument(
        "--packs-dir",
        metavar="PATH",
        default=None,
        dest="packs_dir",
        help="Override assurance packs directory (default: .claude/packs/)",
    )
    pb.add_argument(
        "--soul-db",
        metavar="PATH",
        default=None,
        dest="soul_db",
        help="Override central.db path for soul signing",
    )

    # ---- verify ------------------------------------------------------------
    pv = sub.add_parser(
        "verify",
        help="Verify an evidence bundle directory or .tar.gz",
        description=(
            "Check SHA-256 hashes, compliance segment chain, and optional "
            "soul signature in an evidence bundle.  CI-runnable; needs no "
            "network or database access."
        ),
    )
    pv.add_argument(
        "path",
        metavar="PATH",
        help="Path to the bundle directory or .tar.gz archive",
    )
    pv.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 1 on the first failure (default: report all)",
    )

    return p


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def handler(args: argparse.Namespace) -> None:
    if args.evidence_subcmd == "bundle":
        _handle_bundle(args)
    elif args.evidence_subcmd == "verify":
        _handle_verify(args)
    else:
        print(f"Unknown evidence sub-command: {args.evidence_subcmd}", file=sys.stderr)
        sys.exit(2)


# ---------------------------------------------------------------------------
# Sub-handlers
# ---------------------------------------------------------------------------


def _handle_bundle(args: argparse.Namespace) -> None:
    db_path = _resolve_db_path(getattr(args, "db", None))
    if not db_path.exists():
        print(f"baton.db not found at {db_path}", file=sys.stderr)
        sys.exit(2)

    output_dir = (
        Path(args.output) if args.output else db_path.parent
    )

    compliance_log = (
        Path(args.compliance_log) if args.compliance_log else None
    )
    packs_dir = Path(args.packs_dir) if args.packs_dir else None
    soul_db = Path(args.soul_db) if args.soul_db else None

    builder = EvidenceBundleBuilder(
        db_path=db_path,
        compliance_log=compliance_log,
        packs_dir=packs_dir,
        central_db_path=soul_db,
    )

    try:
        bundle_path = builder.build(
            args.task_id,
            output_dir=output_dir,
            sign=args.sign,
            tar=args.tar,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    print(f"Evidence bundle written to: {bundle_path}")


def _handle_verify(args: argparse.Namespace) -> None:
    path = Path(args.path)
    if not path.exists():
        print(f"Path does not exist: {path}", file=sys.stderr)
        sys.exit(2)

    ok, messages, exit_code = verify_bundle(path)

    if ok and not messages:
        print("Bundle OK — all checks passed.")
    elif ok:
        for msg in messages:
            print(msg)
        print("Bundle OK (with warnings).")
    else:
        for msg in messages:
            print(msg)
            if args.strict and not ok:
                sys.exit(1)
        print("Bundle FAILED verification.")

    sys.exit(exit_code)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_db_path(override: str | None) -> Path:
    if override:
        return Path(override)
    env = os.environ.get("BATON_DB_PATH")
    if env:
        return Path(env)
    return Path.cwd() / ".claude" / "team-context" / "baton.db"
