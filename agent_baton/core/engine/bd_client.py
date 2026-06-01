"""Thin subprocess wrapper around the external ``bd`` CLI (gastownhall/beads).

ADR-13b ("all in on beads"): agent-baton uses the real ``bd`` tool as the
system of record for bead/issue memory, replacing the native SQLite bead
store.  This module is the single seam through which all ``bd`` invocations
pass — every other module talks to :class:`BdClient`, never to ``subprocess``
directly, so the shell-out surface stays auditable and testable.

Design
------
- All reads use ``--json`` and are parsed into Python objects.
- Writes (``create``/``update``/``close``/``note``/``dep``) return the
  affected issue dict when ``--json`` is available.
- The client is *stateless* apart from the resolved binary path and the
  working directory (the project root that owns the ``.beads/`` database).
- Errors raise typed exceptions (:class:`BdError`, :class:`BdNotAvailable`)
  so callers can degrade or surface a clear message rather than seeing a
  raw ``CalledProcessError``.

Configuration (env)
-------------------
- ``BATON_BD_BIN``    — path/name of the ``bd`` binary (default ``bd``).
- ``BATON_BD_PREFIX`` — issue prefix used at ``bd init`` (default ``bd``),
  chosen so generated IDs match baton's historical ``bd-<hash>`` scheme.
- ``BATON_BD_ENABLED`` — master switch for the beads backend (default ``1``).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

_log = logging.getLogger(__name__)

_BIN_ENV = "BATON_BD_BIN"
_PREFIX_ENV = "BATON_BD_PREFIX"
_ENABLED_ENV = "BATON_BD_ENABLED"

_DEFAULT_BIN = "bd"
_DEFAULT_PREFIX = "bd"

# bd's built-in issue types (``bd types``).  baton bead_types that are not in
# this set are stored as ``task`` on the bd side and recovered losslessly from
# metadata on read.
BD_BUILTIN_TYPES: frozenset[str] = frozenset({
    "task", "bug", "feature", "chore", "epic",
    "decision", "spike", "story", "milestone",
})


class BdError(RuntimeError):
    """A ``bd`` command exited non-zero or returned an error payload."""


class BdNotAvailable(BdError):
    """The ``bd`` binary could not be found on PATH / at ``BATON_BD_BIN``."""


def bd_enabled() -> bool:
    """Return True when the beads (``bd``) backend is enabled (default ON)."""
    return os.environ.get(_ENABLED_ENV, "1").strip().lower() not in (
        "0", "false", "no", "",
    )


def bd_prefix() -> str:
    """Return the configured issue prefix (default ``bd``)."""
    return (os.environ.get(_PREFIX_ENV, "").strip() or _DEFAULT_PREFIX)


class BdClient:
    """Stateless wrapper around the ``bd`` CLI for one project workspace.

    Args:
        repo_root: Working directory that owns (or will own) the ``.beads/``
            database.  All ``bd`` invocations run with this as ``cwd``.
        binary: Override the ``bd`` binary path (defaults to ``$BATON_BD_BIN``
            or ``bd`` on PATH).
        timeout: Per-command timeout in seconds.
    """

    def __init__(
        self,
        repo_root: Path | str,
        *,
        binary: str | None = None,
        timeout: int = 60,
    ) -> None:
        self._cwd = Path(repo_root)
        self._bin = binary or os.environ.get(_BIN_ENV, "").strip() or _DEFAULT_BIN
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    def available(self) -> bool:
        """Return True if the ``bd`` binary is resolvable."""
        return shutil.which(self._bin) is not None or Path(self._bin).exists()

    def version(self) -> str:
        """Return the ``bd version`` string (raises if unavailable)."""
        return self._run(["version"], json_output=False).strip()

    def db_exists(self) -> bool:
        """Return True if a ``.beads/`` database already exists under cwd."""
        return (self._cwd / ".beads").is_dir()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def init(self, prefix: str | None = None) -> None:
        """Initialise a ``.beads/`` database in cwd (idempotent).

        No-op when a database already exists.  Uses ``BATON_BD_PREFIX`` (default
        ``bd``) so generated IDs match baton's ``bd-<hash>`` convention.
        """
        if self.db_exists():
            return
        self._cwd.mkdir(parents=True, exist_ok=True)
        self._run(
            ["init", "--prefix", prefix or bd_prefix()],
            json_output=False,
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def create(
        self,
        title: str,
        *,
        issue_type: str = "task",
        priority: int = 2,
        description: str = "",
        design: str = "",
        notes: str = "",
        labels: list[str] | None = None,
        metadata: dict | None = None,
        deps: list[str] | None = None,
        parent: str = "",
        bead_id: str = "",
        force: bool = False,
    ) -> dict:
        """Create an issue and return its JSON dict.

        ``metadata`` is serialised to a JSON string for ``--metadata`` so
        arbitrary baton fields round-trip losslessly.
        """
        args = ["create", title, "-t", issue_type, "-p", str(priority)]
        if description:
            args += ["-d", description]
        if design:
            args += ["--design", design]
        if notes:
            args += ["--notes", notes]
        if labels:
            args += ["-l", ",".join(labels)]
        if metadata:
            args += ["--metadata", json.dumps(metadata, sort_keys=True)]
        if deps:
            args += ["--deps", ",".join(deps)]
        if parent:
            args += ["--parent", parent]
        if bead_id:
            args += ["--id", bead_id]
        if force:
            args += ["--force"]
        return self._run_json_object(args)

    def update(
        self,
        bead_id: str,
        *,
        status: str = "",
        priority: int | None = None,
        metadata: dict | None = None,
        set_labels: list[str] | None = None,
        add_labels: list[str] | None = None,
    ) -> dict:
        """Update fields on an existing issue; returns the updated dict."""
        args = ["update", bead_id]
        if status:
            args += ["-s", status]
        if priority is not None:
            args += ["-p", str(priority)]
        if metadata is not None:
            args += ["--metadata", json.dumps(metadata, sort_keys=True)]
        if set_labels is not None:
            args += ["--set-labels", ",".join(set_labels)]
        if add_labels:
            args += ["--add-label", ",".join(add_labels)]
        return self._run_json_object(args)

    def close(self, bead_id: str, reason: str = "") -> dict:
        """Close an issue; returns the closed dict."""
        args = ["close", bead_id]
        if reason:
            args += ["--reason", reason]
        return self._run_json_object(args)

    def note(self, bead_id: str, text: str) -> None:
        """Append a note/comment to an issue."""
        self._run(["note", bead_id, text], json_output=False)

    def dep_add(self, child_id: str, depends_on_id: str, dep_type: str = "blocks") -> None:
        """Add a dependency: *child_id* depends on *depends_on_id*."""
        # ``bd dep add CHILD PARENT`` — child depends on parent.
        args = ["dep", "add", child_id, depends_on_id]
        if dep_type and dep_type != "blocks":
            args += ["--type", dep_type]
        self._run(args, json_output=False)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def show(self, bead_id: str) -> dict | None:
        """Return a single issue dict, or ``None`` if not found."""
        try:
            result = self._run_json(["show", bead_id])
        except BdError as exc:
            if "no issue" in str(exc).lower() or "not found" in str(exc).lower():
                return None
            raise
        if isinstance(result, list):
            return result[0] if result else None
        if isinstance(result, dict) and result.get("error"):
            return None
        return result if isinstance(result, dict) else None

    def list(
        self,
        *,
        status: str = "",
        labels: list[str] | None = None,
        limit: int = 0,
    ) -> list[dict]:
        """List issues (optionally filtered by status/labels)."""
        args = ["list"]
        if status:
            args += ["--status", status]
        if labels:
            for lbl in labels:
                args += ["--label", lbl]
        if limit:
            args += ["--limit", str(limit)]
        result = self._run_json(args)
        return result if isinstance(result, list) else []

    def ready(self) -> list[dict]:
        """Return issues whose dependencies are all satisfied (``bd ready``)."""
        result = self._run_json(["ready"])
        return result if isinstance(result, list) else []

    # ------------------------------------------------------------------
    # Internal command runners
    # ------------------------------------------------------------------

    def _run(self, args: list[str], *, json_output: bool) -> str:
        """Run ``bd <args>`` in cwd and return stdout (raises on failure)."""
        if not self.available():
            raise BdNotAvailable(
                f"'{self._bin}' not found. Install beads (see install.sh) or set "
                f"{_BIN_ENV} to the bd binary path."
            )
        cmd = [self._bin, *args]
        if json_output:
            cmd.append("--json")
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self._cwd),
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except FileNotFoundError as exc:
            raise BdNotAvailable(f"'{self._bin}' not found: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise BdError(f"bd {' '.join(args)} timed out after {self._timeout}s") from exc
        if proc.returncode != 0:
            raise BdError(
                f"bd {' '.join(args)} failed (exit {proc.returncode}): "
                f"{(proc.stderr or proc.stdout).strip()[:500]}"
            )
        return proc.stdout

    def _run_json(self, args: list[str]):
        """Run a read command with ``--json`` and parse the payload."""
        out = self._run(args, json_output=True)
        out = out.strip()
        if not out:
            return []
        try:
            return json.loads(out)
        except json.JSONDecodeError as exc:
            raise BdError(f"bd {' '.join(args)} returned non-JSON: {out[:200]}") from exc

    def _run_json_object(self, args: list[str]) -> dict:
        """Run a write command with ``--json`` and return the object payload.

        Raises :class:`BdError` when bd reports an ``error`` field.
        """
        payload = self._run_json(args)
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        if isinstance(payload, dict) and payload.get("error"):
            raise BdError(str(payload["error"]))
        return payload if isinstance(payload, dict) else {}
