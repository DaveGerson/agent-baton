"""CLI command: ``baton export`` — export task artifacts as a structured bundle.

Assembles all persisted data for a given task (plan, step results, gate
results, approval records, beads) into a single portable file.

Formats
-------
json (default)
    A single JSON file with a nested structure — suitable for programmatic
    consumption and external review tooling.

csv
    One CSV file per data type written into a directory:
    ``steps.csv``, ``gates.csv``, ``approvals.csv``, ``beads.csv``,
    ``summary.csv``.  Auditors can import these directly into spreadsheets
    or BI tools.

Usage
-----
    baton export --task TASK_ID
    baton export --task TASK_ID --format csv --output ./export-dir/
    baton export --task TASK_ID --format json --output bundle.json
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Register the ``export`` subcommand."""
    p = subparsers.add_parser(
        "export",
        help="Export task artifacts as a structured bundle (JSON or CSV)",
    )
    p.add_argument(
        "--task",
        required=True,
        metavar="TASK_ID",
        dest="task_id",
        help="Task ID to export",
    )
    p.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        metavar="FORMAT",
        dest="export_format",
        help="Output format: json (default) or csv",
    )
    p.add_argument(
        "--output",
        default="",
        metavar="PATH",
        help=(
            "Output path. For JSON: path to the output file "
            "(default: <task_id>-export.json). "
            "For CSV: path to the output directory "
            "(default: <task_id>-export/)."
        ),
    )
    return p


def handler(args: argparse.Namespace) -> None:
    """Execute the export command."""
    task_id: str = args.task_id
    export_format: str = args.export_format
    output_path: str = args.output

    bundle = _assemble_bundle(task_id)
    if bundle is None:
        print(f"error: task '{task_id}' not found in storage", file=sys.stderr)
        sys.exit(1)

    if export_format == "json":
        _write_json(bundle, task_id, output_path)
    else:
        _write_csv(bundle, task_id, output_path)


# ---------------------------------------------------------------------------
# Bundle assembly
# ---------------------------------------------------------------------------


def _resolve_db_path() -> Path | None:
    """Locate the project's baton.db by searching from the current directory.

    Walks up from ``cwd`` looking for ``.claude/team-context/baton.db``.
    Falls back to ``~/.baton/baton.db`` as a last resort.

    Returns:
        An existing :class:`~pathlib.Path`, or ``None`` if not found.
    """
    cwd = Path.cwd()
    for ancestor in [cwd, *cwd.parents]:
        candidate = ancestor / ".claude" / "team-context" / "baton.db"
        if candidate.exists():
            return candidate
    # Global fallback
    global_path = Path.home() / ".baton" / "baton.db"
    if global_path.exists():
        return global_path
    return None


def _assemble_bundle(task_id: str) -> dict[str, Any] | None:
    """Load all persisted data for *task_id* and return a plain dict bundle.

    Reads from the project's ``baton.db`` via ``SqliteStorage`` and from
    the bead store.  Returns ``None`` when the task is not found.

    Args:
        task_id: The execution task identifier.

    Returns:
        A nested dict with keys ``task_id``, ``plan``, ``steps``, ``gates``,
        ``approvals``, ``beads``, and ``summary``.  Returns ``None`` if the
        task does not exist.
    """
    from agent_baton.core.storage.sqlite_backend import SqliteStorage

    db_path = _resolve_db_path()
    if db_path is None:
        return None

    storage = SqliteStorage(db_path)
    state = storage.load_execution(task_id)
    if state is None:
        return None

    # Plan
    plan_dict: dict[str, Any] = {}
    if state.plan:
        plan_dict = {
            "task_id": state.plan.task_id,
            "task_description": getattr(state.plan, "task_description", ""),
            "risk_level": getattr(state.plan, "risk_level", ""),
            "phases": [
                {
                    "phase_id": ph.phase_id,
                    "phase_name": ph.phase_name,
                    "steps": [
                        {
                            "step_id": s.step_id,
                            "description": getattr(s, "description", ""),
                            "agent": getattr(s, "agent", getattr(s, "agent_name", "")),
                            "step_type": getattr(s, "step_type", ""),
                            "depends_on": list(s.depends_on or []),
                        }
                        for s in ph.steps
                    ],
                }
                for ph in state.plan.phases
            ],
        }

    # Step results
    steps: list[dict[str, Any]] = [
        {
            "step_id": sr.step_id,
            "agent_name": sr.agent_name,
            "status": sr.status,
            "outcome": sr.outcome,
            "files_changed": sr.files_changed,
            "commit_hash": sr.commit_hash,
            "estimated_tokens": sr.estimated_tokens,
            "duration_seconds": sr.duration_seconds,
            "retries": sr.retries,
            "error": sr.error,
            "completed_at": sr.completed_at,
            "step_type": getattr(sr, "step_type", ""),
        }
        for sr in state.step_results
    ]

    # Gate results
    gates: list[dict[str, Any]] = [
        {
            "phase_id": gr.phase_id,
            "gate_type": gr.gate_type,
            "passed": gr.passed,
            "output": gr.output,
            "checked_at": gr.checked_at,
        }
        for gr in state.gate_results
    ]

    # Approval results
    approvals: list[dict[str, Any]] = [
        {
            "phase_id": ar.phase_id,
            "result": ar.result,
            "feedback": ar.feedback,
            "decided_at": ar.decided_at,
        }
        for ar in state.approval_results
    ]

    # Beads
    beads: list[dict[str, Any]] = []
    try:
        from agent_baton.core.engine.bead_store import BeadStore

        bead_store = BeadStore(db_path)
        raw_beads = bead_store.query(task_id=task_id, limit=500)
        beads = [
            {
                "bead_id": b.bead_id,
                "step_id": b.step_id,
                "agent_name": b.agent_name,
                "bead_type": b.bead_type,
                "content": b.content,
                "confidence": b.confidence,
                "scope": b.scope,
                "tags": b.tags,
                "affected_files": b.affected_files,
                "status": b.status,
                "created_at": b.created_at,
                "summary": b.summary,
            }
            for b in raw_beads
        ]
    except Exception:
        pass  # Bead store is optional — missing beads don't block export.

    # Token cost summary
    total_tokens = sum(sr.get("estimated_tokens") or 0 for sr in steps)
    steps_completed = sum(1 for sr in steps if sr["status"] == "completed")
    steps_failed = sum(1 for sr in steps if sr["status"] == "failed")
    gates_passed = sum(1 for gr in gates if gr["passed"])
    gates_failed_count = sum(1 for gr in gates if not gr["passed"])

    summary: dict[str, Any] = {
        "task_id": task_id,
        "status": state.status,
        "started_at": state.started_at,
        "completed_at": state.completed_at,
        "total_steps": len(steps),
        "steps_completed": steps_completed,
        "steps_failed": steps_failed,
        "total_gates": len(gates),
        "gates_passed": gates_passed,
        "gates_failed": gates_failed_count,
        "total_approvals": len(approvals),
        "total_beads": len(beads),
        "estimated_tokens": total_tokens,
    }

    return {
        "task_id": task_id,
        "plan": plan_dict,
        "steps": steps,
        "gates": gates,
        "approvals": approvals,
        "beads": beads,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# JSON writer
# ---------------------------------------------------------------------------


def _write_json(bundle: dict[str, Any], task_id: str, output_path: str) -> None:
    """Serialise *bundle* to a single JSON file.

    Args:
        bundle: The assembled export bundle.
        task_id: Used to derive the default file name.
        output_path: Explicit output path, or empty to use the default.
    """
    path = Path(output_path) if output_path else Path(f"{task_id}-export.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle, indent=2, default=str), encoding="utf-8")
    print(f"Exported task '{task_id}' to {path}")
    _print_summary(bundle["summary"])


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------


def _write_csv(bundle: dict[str, Any], task_id: str, output_path: str) -> None:
    """Write one CSV file per data type into an output directory.

    Files written:
    - ``steps.csv``
    - ``gates.csv``
    - ``approvals.csv``
    - ``beads.csv``
    - ``summary.csv``

    Args:
        bundle: The assembled export bundle.
        task_id: Used to derive the default directory name.
        output_path: Explicit output directory path, or empty to use default.
    """
    out_dir = Path(output_path) if output_path else Path(f"{task_id}-export")
    out_dir.mkdir(parents=True, exist_ok=True)

    _write_csv_file(
        out_dir / "steps.csv",
        bundle["steps"],
        [
            "step_id", "agent_name", "status", "outcome", "files_changed",
            "commit_hash", "estimated_tokens", "duration_seconds",
            "retries", "error", "completed_at", "step_type",
        ],
    )

    _write_csv_file(
        out_dir / "gates.csv",
        bundle["gates"],
        ["phase_id", "gate_type", "passed", "output", "checked_at"],
    )

    _write_csv_file(
        out_dir / "approvals.csv",
        bundle["approvals"],
        ["phase_id", "result", "feedback", "decided_at"],
    )

    _write_csv_file(
        out_dir / "beads.csv",
        bundle["beads"],
        [
            "bead_id", "step_id", "agent_name", "bead_type", "content",
            "confidence", "scope", "tags", "affected_files", "status",
            "created_at", "summary",
        ],
    )

    # summary.csv is a two-column key/value file.
    summary_rows = [{"key": k, "value": str(v)} for k, v in bundle["summary"].items()]
    _write_csv_file(out_dir / "summary.csv", summary_rows, ["key", "value"])

    print(f"Exported task '{task_id}' to {out_dir}/")
    print(f"  steps.csv ({len(bundle['steps'])} rows)")
    print(f"  gates.csv ({len(bundle['gates'])} rows)")
    print(f"  approvals.csv ({len(bundle['approvals'])} rows)")
    print(f"  beads.csv ({len(bundle['beads'])} rows)")
    print(f"  summary.csv")
    _print_summary(bundle["summary"])


def _write_csv_file(
    path: Path,
    rows: list[dict[str, Any]],
    fieldnames: list[str],
) -> None:
    """Write *rows* to *path* as a CSV file with the given *fieldnames*.

    List-valued cells are serialised as JSON strings so that fields like
    ``files_changed`` and ``tags`` round-trip cleanly.

    Args:
        path: Destination file path.
        rows: List of row dicts.
        fieldnames: Column order and header names.
    """
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            # Serialise list/dict values to JSON strings for CSV compatibility.
            clean: dict[str, Any] = {}
            for k in fieldnames:
                val = row.get(k, "")
                if isinstance(val, (list, dict)):
                    val = json.dumps(val, default=str)
                clean[k] = val
            writer.writerow(clean)


def _print_summary(summary: dict[str, Any]) -> None:
    """Print a compact summary line to stdout."""
    print(
        f"  {summary.get('steps_completed', 0)}/{summary.get('total_steps', 0)} steps completed, "
        f"{summary.get('gates_passed', 0)}/{summary.get('total_gates', 0)} gates passed, "
        f"{summary.get('estimated_tokens', 0):,} tokens"
    )
