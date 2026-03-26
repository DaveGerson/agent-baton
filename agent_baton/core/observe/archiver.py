"""Data archival and cleanup for execution artifacts.

Provides retention-based cleanup for traces, events, retrospectives,
telemetry, and completed execution states.  Keeps the team-context
directory manageable as task volume grows.

The archiver operates on all data produced by the observe layer:

* Execution state directories (``executions/``)
* Trace JSON files (``traces/``)
* Event JSONL streams (``events/``)
* Retrospective Markdown + JSON sidecars (``retrospectives/``)
* Context profile JSON files (``context-profiles/``)
* JSONL rotation for ``telemetry.jsonl`` (keeps last 10,000 lines)

Files older than the configured retention period (default 90 days) are
eligible for cleanup.  The archiver supports both dry-run scanning and
destructive cleanup.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path


class DataArchiver:
    """Archive and clean up old execution artifacts.

    Supports two modes:
    - **archive**: Move old files to a compressed archive directory
    - **delete**: Permanently remove old files

    Files are selected by age: anything older than ``retention_days``
    is eligible for cleanup.
    """

    def __init__(self, context_root: Path) -> None:
        self._root = context_root

    def scan(self, retention_days: int = 90) -> dict[str, list[Path]]:
        """Scan for files older than *retention_days*.

        Examines five artifact categories (executions, traces, events,
        retrospectives, context_profiles) and identifies files whose
        modification time is older than the retention cutoff.  For
        execution directories, the newest file inside the directory is
        used as the age reference.

        Args:
            retention_days: Number of days to retain.  Files older than
                this are eligible for cleanup.

        Returns:
            Dict mapping category name to list of paths eligible for
            cleanup.  Does NOT modify anything -- read-only scan.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        cutoff_ts = cutoff.timestamp()

        eligible: dict[str, list[Path]] = {
            "executions": [],
            "traces": [],
            "events": [],
            "retrospectives": [],
            "context_profiles": [],
        }

        # Namespaced execution directories
        exec_dir = self._root / "executions"
        if exec_dir.is_dir():
            for child in exec_dir.iterdir():
                if child.is_dir() and self._is_old(child, cutoff_ts):
                    eligible["executions"].append(child)

        # Traces
        traces_dir = self._root / "traces"
        if traces_dir.is_dir():
            for f in traces_dir.glob("*.json"):
                if f.stat().st_mtime < cutoff_ts:
                    eligible["traces"].append(f)

        # Events
        events_dir = self._root / "events"
        if events_dir.is_dir():
            for f in events_dir.glob("*.jsonl"):
                if f.stat().st_mtime < cutoff_ts:
                    eligible["events"].append(f)

        # Retrospectives (both .md and .json sidecars)
        retro_dir = self._root / "retrospectives"
        if retro_dir.is_dir():
            for f in retro_dir.iterdir():
                if f.is_file() and f.stat().st_mtime < cutoff_ts:
                    eligible["retrospectives"].append(f)

        # Context profiles
        profiles_dir = self._root / "context-profiles"
        if profiles_dir.is_dir():
            for f in profiles_dir.glob("*.json"):
                if f.stat().st_mtime < cutoff_ts:
                    eligible["context_profiles"].append(f)

        return eligible

    def cleanup(
        self,
        retention_days: int = 90,
        *,
        dry_run: bool = False,
    ) -> dict[str, int]:
        """Remove files older than retention_days.

        Args:
            retention_days: Keep files newer than this many days.
            dry_run: If True, report what would be removed but don't delete.

        Returns:
            Dict mapping category → count of files removed.
        """
        eligible = self.scan(retention_days)
        counts: dict[str, int] = {}

        for category, paths in eligible.items():
            removed = 0
            for path in paths:
                if not dry_run:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink(missing_ok=True)
                removed += 1
            counts[category] = removed

        # Rotate JSONL logs (telemetry, usage) — keep last N lines
        if not dry_run:
            self._rotate_jsonl(
                self._root / "telemetry.jsonl",
                max_lines=10_000,
            )

        return counts

    def summary(self, retention_days: int = 90) -> str:
        """Human-readable summary of what would be cleaned up."""
        eligible = self.scan(retention_days)
        lines = [f"Data cleanup scan (retention: {retention_days} days):", ""]

        total_files = 0
        total_bytes = 0
        for category, paths in eligible.items():
            if not paths:
                continue
            size = sum(
                self._dir_size(p) if p.is_dir() else p.stat().st_size
                for p in paths
            )
            total_files += len(paths)
            total_bytes += size
            lines.append(
                f"  {category}: {len(paths)} item(s), "
                f"{self._fmt_size(size)}"
            )

        if total_files == 0:
            lines.append("  Nothing to clean up.")
        else:
            lines.append("")
            lines.append(
                f"  Total: {total_files} item(s), "
                f"{self._fmt_size(total_bytes)}"
            )

        return "\n".join(lines)

    # ── JSONL rotation ─────────────────────────────────────────────────────

    @staticmethod
    def _rotate_jsonl(path: Path, max_lines: int = 10_000) -> None:
        """Keep only the last max_lines of a JSONL file."""
        if not path.exists():
            return
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) <= max_lines:
            return
        kept = lines[-max_lines:]
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _is_old(directory: Path, cutoff_ts: float) -> bool:
        """Check if a directory's newest file is older than cutoff."""
        newest = 0.0
        for f in directory.rglob("*"):
            if f.is_file():
                newest = max(newest, f.stat().st_mtime)
        return newest < cutoff_ts if newest > 0 else True

    @staticmethod
    def _dir_size(path: Path) -> int:
        """Total size of all files in a directory."""
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

    @staticmethod
    def _fmt_size(nbytes: int) -> str:
        """Human-readable file size."""
        for unit in ("B", "KB", "MB", "GB"):
            if nbytes < 1024:
                return f"{nbytes:.1f} {unit}"
            nbytes //= 1024
        return f"{nbytes:.1f} TB"
