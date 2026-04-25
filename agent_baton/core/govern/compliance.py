"""Compliance report generation and persistence for auditable agent workflows.

This module produces structured compliance artifacts for tasks that involve
regulated data, PII, or other sensitive domains. Each compliance report
records the full chain of agent actions, gate results, business rules
validated, and auditor verdicts so that external auditors can trace every
change back to the responsible agent and its authorization checkpoint.

Reports are persisted as individual markdown files under
``.claude/team-context/compliance-reports/`` (configurable). The markdown
format is designed to be human-readable and version-control friendly.

Typical usage in the execution engine:

1. The planner classifies a task as HIGH or CRITICAL risk.
2. The executor creates ``ComplianceEntry`` objects for each agent dispatch.
3. After all gates pass, ``ComplianceReportGenerator.generate()`` assembles
   the report and ``save()`` writes it to disk.
4. The auditor agent reviews the report and sets ``auditor_verdict``.

For tamper-evident audit trails, :class:`ComplianceChainWriter` appends
hash-chained JSONL entries with process-safe locking via ``fcntl.flock``.

"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from agent_baton.models.usage import TaskUsageRecord
from agent_baton.models.enums import RiskLevel


@dataclass
class ComplianceEntry:
    """A single auditable change within a compliance report.

    Each entry corresponds to one agent's contribution during the execution
    of a regulated task. Entries are ordered chronologically and together
    form the change log section of the compliance report.

    Attributes:
        agent_name: Name of the agent that performed the action.
        action: What the agent did -- typically ``"created"``,
            ``"modified"``, or ``"reviewed"``.
        files: List of file paths touched by this agent action.
        business_rules_validated: Domain-specific rules that the agent
            or gate confirmed (e.g. ``"append-only historical records"``).
        commit_hash: Git commit SHA associated with this change, if any.
        gate_result: Outcome of the gate check for this step.
            One of ``"PASS"``, ``"FAIL"``, or ``"PASS WITH NOTES"``.
        notes: Free-text notes from the agent or gate about this entry.
    """

    agent_name: str
    action: str  # "created", "modified", "reviewed"
    files: list[str] = field(default_factory=list)
    business_rules_validated: list[str] = field(default_factory=list)
    commit_hash: str = ""
    gate_result: str = ""  # "PASS", "FAIL", "PASS WITH NOTES"
    notes: str = ""


@dataclass
class ComplianceReport:
    """Structured compliance artifact for regulated-data tasks.

    A compliance report is the top-level audit document for a single
    orchestrated task. It aggregates all ``ComplianceEntry`` objects,
    records gate pass/fail statistics, and captures the auditor's final
    verdict.

    Attributes:
        task_id: Unique identifier for the orchestrated task.
        task_description: Human-readable description of what was done.
        risk_level: The risk classification applied (e.g. ``"HIGH"``).
        classification: Name of the guardrail preset that governed
            this task (e.g. ``"Regulated Data"``).
        timestamp: ISO-8601 timestamp of report generation.
        entries: Ordered list of agent actions that make up the change log.
        auditor_verdict: Final auditor decision. One of ``"SHIP"``,
            ``"SHIP WITH NOTES"``, ``"REVISE"``, or ``"BLOCK"``.
        auditor_notes: Free-text auditor commentary.
        total_gates_passed: Count of gate checks that passed.
        total_gates_failed: Count of gate checks that failed.
    """

    task_id: str
    task_description: str
    risk_level: str = "HIGH"
    classification: str = ""  # guardrail preset applied
    timestamp: str = ""
    entries: list[ComplianceEntry] = field(default_factory=list)
    auditor_verdict: str = ""  # "SHIP", "SHIP WITH NOTES", "REVISE", "BLOCK"
    auditor_notes: str = ""
    total_gates_passed: int = 0
    total_gates_failed: int = 0

    def to_markdown(self) -> str:
        """Render as audit-ready markdown."""
        lines = [
            f"# Compliance Report: {self.task_id}",
            "",
            f"**Task:** {self.task_description}",
            f"**Risk Level:** {self.risk_level}",
            f"**Classification:** {self.classification}",
            f"**Date:** {self.timestamp or datetime.now().isoformat()}",
            f"**Auditor Verdict:** {self.auditor_verdict or 'Pending'}",
            "",
        ]
        if self.auditor_notes:
            lines.extend(["## Auditor Notes", self.auditor_notes, ""])

        lines.extend([
            "## Change Log",
            "",
            "| Agent | Action | Files | Gate | Commit |",
            "|-------|--------|-------|------|--------|",
        ])
        for e in self.entries:
            files_str = ", ".join(e.files[:3])
            if len(e.files) > 3:
                files_str += f" (+{len(e.files) - 3})"
            lines.append(
                f"| {e.agent_name} | {e.action} | {files_str} |"
                f" {e.gate_result} | {e.commit_hash[:7] if e.commit_hash else '-'} |"
            )
        lines.append("")

        # Business rules section
        all_rules: list[str] = []
        for e in self.entries:
            all_rules.extend(e.business_rules_validated)
        if all_rules:
            lines.extend(["## Business Rules Validated", ""])
            for rule in sorted(set(all_rules)):
                lines.append(f"- {rule}")
            lines.append("")

        lines.extend([
            "## Gate Summary",
            f"- Gates passed: {self.total_gates_passed}",
            f"- Gates failed: {self.total_gates_failed}",
            "",
        ])

        # Notes from individual entries
        entry_notes = [(e.agent_name, e.notes) for e in self.entries if e.notes]
        if entry_notes:
            lines.extend(["## Agent Notes", ""])
            for agent, note in entry_notes:
                lines.append(f"- **{agent}:** {note}")
            lines.append("")

        return "\n".join(lines)


class ComplianceReportGenerator:
    """Generate, persist, and retrieve compliance reports.

    The generator assembles ``ComplianceReport`` objects from task execution
    data and writes them as markdown files to a reports directory. Reports
    can be listed, loaded by task ID, and filtered to recent entries.

    The default storage location is
    ``.claude/team-context/compliance-reports/``, which is created on first
    write. Each report is named ``<task_id>.md`` with path-unsafe characters
    replaced by hyphens.
    """

    def __init__(self, reports_dir: Path | None = None) -> None:
        self._dir = (reports_dir or Path(".claude/team-context/compliance-reports")).resolve()

    @property
    def reports_dir(self) -> Path:
        return self._dir

    def generate(
        self,
        task_id: str,
        task_description: str,
        risk_level: str = "HIGH",
        classification: str = "",
        entries: list[ComplianceEntry] | None = None,
        auditor_verdict: str = "",
        auditor_notes: str = "",
        usage: TaskUsageRecord | None = None,
    ) -> ComplianceReport:
        """Generate a compliance report from task execution data.

        Assembles a ``ComplianceReport`` by combining task metadata,
        agent entries, auditor findings, and gate statistics from the
        usage record.

        Args:
            task_id: Unique identifier for the task.
            task_description: Human-readable description of the task.
            risk_level: Risk tier applied to this task (e.g. ``"HIGH"``).
            classification: Name of the guardrail preset.
            entries: List of ``ComplianceEntry`` objects recording each
                agent's contribution. Defaults to an empty list.
            auditor_verdict: Final auditor decision, if available.
            auditor_notes: Free-text auditor commentary.
            usage: Optional ``TaskUsageRecord`` from which gate pass/fail
                counts are extracted.

        Returns:
            A fully populated ``ComplianceReport`` ready for rendering
            or persistence.
        """
        gates_passed = 0
        gates_failed = 0
        if usage is not None:
            gates_passed = usage.gates_passed
            gates_failed = usage.gates_failed

        return ComplianceReport(
            task_id=task_id,
            task_description=task_description,
            risk_level=risk_level,
            classification=classification,
            timestamp=datetime.now().isoformat(),
            entries=entries or [],
            auditor_verdict=auditor_verdict,
            auditor_notes=auditor_notes,
            total_gates_passed=gates_passed,
            total_gates_failed=gates_failed,
        )

    def save(self, report: ComplianceReport) -> Path:
        """Write a compliance report to disk as a markdown file.

        Creates the reports directory if it does not exist. The filename
        is derived from ``report.task_id`` with slashes and spaces replaced
        by hyphens.

        Args:
            report: The ``ComplianceReport`` to persist.

        Returns:
            The ``Path`` to the written markdown file.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        safe_id = report.task_id.replace("/", "-").replace(" ", "-")
        path = self._dir / f"{safe_id}.md"
        path.write_text(report.to_markdown(), encoding="utf-8")
        return path

    def load(self, task_id: str) -> str | None:
        """Read a compliance report by task ID.

        Args:
            task_id: Identifier of the task whose report to load.

        Returns:
            The raw markdown content of the report, or ``None`` if no
            report exists for the given task ID.
        """
        safe_id = task_id.replace("/", "-").replace(" ", "-")
        path = self._dir / f"{safe_id}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def list_reports(self) -> list[Path]:
        """List all compliance report file paths, sorted by name.

        Returns:
            A sorted list of ``Path`` objects pointing to ``*.md`` files
            in the reports directory. Returns an empty list if the
            directory does not exist.
        """
        if not self._dir.is_dir():
            return []
        return sorted(self._dir.glob("*.md"))

    def list_recent(self, count: int = 5) -> list[Path]:
        """Return the N most recently created reports.

        Reports are sorted alphabetically by filename, so "most recent"
        refers to the last entries in that sort order.

        Args:
            count: Maximum number of reports to return. Defaults to 5.

        Returns:
            A list of up to ``count`` report file paths.
        """
        return self.list_reports()[-count:]


# ---------------------------------------------------------------------------
# Tamper-evident hash-chained append log
# ---------------------------------------------------------------------------

# Sentinel hash used as the "previous hash" for the very first entry. Stable
# string so chain validation can always find a deterministic genesis value.
_CHAIN_GENESIS_HASH = "0" * 64


def _hash_entry(prev_hash: str, payload: dict[str, Any]) -> str:
    """Compute a SHA-256 hash linking *payload* to *prev_hash*.

    The hash input is ``prev_hash + canonical_json(payload)`` where
    ``canonical_json`` uses sorted keys and no whitespace. This guarantees
    identical bytes across processes regardless of dict insertion order.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha256()
    h.update(prev_hash.encode("ascii"))
    h.update(canonical.encode("utf-8"))
    return h.hexdigest()


class ChainIntegrityError(RuntimeError):
    """Raised when the persisted chain fails hash validation on read."""


class ComplianceChainWriter:
    """Append entries to a tamper-evident hash-chained JSONL log.

    Each line of the log is a JSON object of the form::

        {"prev_hash": "<sha256>", "hash": "<sha256>", "payload": {...}}

    where ``hash = sha256(prev_hash || canonical_json(payload))``. The
    chain is anchored at :data:`_CHAIN_GENESIS_HASH` for the first entry.

    Concurrency model
    -----------------
    ``append()`` acquires an exclusive ``fcntl.flock`` on a sidecar lock
    file for the full read-tail / hash / write window. This makes the
    writer **process-safe**: multiple processes can append concurrently
    to the same chain without forking the hash sequence.

    Within a single process, callers MUST serialize threads themselves —
    ``flock`` is advisory and does not prevent two threads in the same
    process from racing inside the lock window. Use a
    :class:`threading.Lock` if you need thread safety.

    Recovery
    --------
    The cached ``_last_hash`` is re-read from disk under the lock on
    every append, so a crash mid-append (between hash compute and
    fsync) is recoverable: the next process simply reads the last
    successfully written line and continues from there.
    """

    def __init__(
        self,
        chain_path: Path | str,
        *,
        fsync: bool = True,
    ) -> None:
        """Initialise the writer for *chain_path*.

        Args:
            chain_path: Path to the JSONL chain file. Parent directories
                are created if missing. A sidecar lock file at
                ``<chain_path>.lock`` is used for ``flock`` coordination.
            fsync: If True (default), call ``os.fsync`` after every
                append for crash durability. Disable only for benchmarks
                or for buffered append patterns where the caller will
                fsync explicitly at checkpoints. The flock guarantees
                inter-process ordering regardless of this setting.
        """
        self._path = Path(chain_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        self._fsync = fsync
        # Cached last hash; refreshed under lock on every append.
        self._last_hash: str = _CHAIN_GENESIS_HASH

    @property
    def path(self) -> Path:
        """The JSONL chain file path."""
        return self._path

    @contextmanager
    def _flock(self) -> Iterator[None]:
        """Acquire an exclusive advisory lock on the sidecar lock file.

        Uses ``fcntl.flock(LOCK_EX)`` so other processes block until we
        release. Released via the ``with`` exit even if the body raises.
        """
        # Open in 'a+' so the lock file exists across processes; we never
        # write to it. Reopen each call so we don't leak fds.
        fd = os.open(str(self._lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _read_tail_hash(self) -> str:
        """Return the hash of the last entry on disk (or the genesis hash).

        Reads the chain file from the end and decodes the final non-empty
        line as JSON. Robust to a trailing newline / partial last line:
        if the last line cannot be parsed it is treated as a crashed
        write and the *previous* parseable line's hash is returned.
        """
        if not self._path.is_file() or self._path.stat().st_size == 0:
            return _CHAIN_GENESIS_HASH
        # Small chain files: read all lines. For very large chains this
        # could be replaced with a reverse-seek scan, but JSONL audit
        # logs are typically capped or rotated.
        try:
            with self._path.open("rb") as fh:
                raw = fh.read()
        except OSError:
            return _CHAIN_GENESIS_HASH
        lines = raw.splitlines()
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                # Likely a torn write from a crashed process; skip it.
                continue
            h = obj.get("hash")
            if isinstance(h, str) and len(h) == 64:
                return h
        return _CHAIN_GENESIS_HASH

    def append(self, payload: dict[str, Any]) -> str:
        """Append *payload* as a new chain entry and return its hash.

        Process-safe via ``fcntl.flock``: the read-tail / hash / write
        sequence is atomic across processes. Not thread-safe within a
        single process — callers must serialize threads externally.

        The on-disk ``_last_hash`` is always re-read under the lock to
        guard against a stale in-memory value (e.g. if another process
        appended between calls).

        Args:
            payload: JSON-serialisable dict to record. Keys are sorted
                during hashing so the chain is independent of insertion
                order.

        Returns:
            The SHA-256 hash of the newly appended entry.
        """
        with self._flock():
            # Always refresh from disk: another process may have appended.
            prev = self._read_tail_hash()
            entry_hash = _hash_entry(prev, payload)
            line = json.dumps(
                {"prev_hash": prev, "hash": entry_hash, "payload": payload},
                sort_keys=True,
                separators=(",", ":"),
            )
            # Open + write + fsync inside the lock so a crash leaves at
            # most one torn line that the next reader will skip. If the
            # file ends mid-line (a previous process was killed before
            # writing its newline), prepend our own newline so our new
            # entry starts on a fresh line — this is what makes torn-line
            # recovery work.
            needs_leading_newline = False
            if self._path.is_file() and self._path.stat().st_size > 0:
                with self._path.open("rb") as rfh:
                    rfh.seek(-1, os.SEEK_END)
                    if rfh.read(1) != b"\n":
                        needs_leading_newline = True
            with self._path.open("ab") as fh:
                if needs_leading_newline:
                    fh.write(b"\n")
                fh.write(line.encode("utf-8"))
                fh.write(b"\n")
                fh.flush()
                if self._fsync:
                    try:
                        os.fsync(fh.fileno())
                    except OSError:
                        # fsync may fail on some filesystems; the flock
                        # still guarantees ordering for the next reader.
                        pass
            self._last_hash = entry_hash
            return entry_hash

    def verify(self) -> int:
        """Walk the chain from genesis and verify every link.

        Returns:
            Number of entries verified.

        Raises:
            ChainIntegrityError: If any link's hash does not match
                ``sha256(prev_hash || canonical_json(payload))`` or if
                ``prev_hash`` does not match the previous entry's hash.
        """
        if not self._path.is_file():
            return 0
        prev = _CHAIN_GENESIS_HASH
        count = 0
        with self._path.open("r", encoding="utf-8") as fh:
            for lineno, raw_line in enumerate(fh, start=1):
                if not raw_line.strip():
                    continue
                try:
                    obj = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    raise ChainIntegrityError(
                        f"line {lineno}: invalid JSON: {exc}"
                    ) from exc
                claimed_prev = obj.get("prev_hash")
                claimed_hash = obj.get("hash")
                payload = obj.get("payload")
                if claimed_prev != prev:
                    raise ChainIntegrityError(
                        f"line {lineno}: prev_hash {claimed_prev!r} does not "
                        f"match expected {prev!r}"
                    )
                if not isinstance(payload, dict):
                    raise ChainIntegrityError(
                        f"line {lineno}: payload missing or not a dict"
                    )
                expected = _hash_entry(prev, payload)
                if claimed_hash != expected:
                    raise ChainIntegrityError(
                        f"line {lineno}: hash {claimed_hash!r} does not "
                        f"match recomputed {expected!r}"
                    )
                prev = expected
                count += 1
        return count
