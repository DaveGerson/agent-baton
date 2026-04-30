"""Wave 6.1 Part C — Executable Beads: ExecutableBeadRunner (bd-81b9).

Glue layer that ties together the bead store, script notes, soul signing,
and sandbox execution into a single ``run()`` / ``store()`` interface.

Responsibilities:
- ``store(bead, script_body)``:
    1. Lint the script via :class:`ScriptLinter`.
    2. Compute content SHA and construct the ``script_ref``.
    3. Require a soul signature when ``BATON_SOULS_ENABLED=1``.
    4. Write the script body to ``refs/notes/baton-bead-scripts`` via
       :class:`NotesAdapter`.
    5. Write the bead itself (with ``bead_type="executable"``) to the
       :class:`BeadStore`.
    6. Return the ``script_ref`` string.

- ``run(bead_id)``:
    1. Resolve the :class:`ExecutableBead` from the store.
    2. Load the script body from notes (or raise).
    3. Execute via :class:`Sandbox`.
    4. Record a child ``discovery`` bead linked via ``"validates"`` or
       ``"contradicts"`` based on exit code.
    5. Update ``last_run_at``, ``last_exit_code``, ``last_run_bead_id``
       on the parent bead.
    6. Return the :class:`ExecutionResult`.
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import TYPE_CHECKING

from agent_baton.utils.time import utcnow_zulu as _utcnow

if TYPE_CHECKING:
    from agent_baton.core.engine.bead_store import BeadStore
    from agent_baton.core.engine.notes_adapter import NotesAdapter
    from agent_baton.core.engine.soul_router import SoulRouter
    from agent_baton.core.exec.sandbox import ExecutionResult, Sandbox
    from agent_baton.models.bead import ExecutableBead

_log = logging.getLogger(__name__)

_SOULS_ENABLED_ENV = "BATON_SOULS_ENABLED"
_EXEC_ENABLED_ENV = "BATON_EXEC_BEADS_ENABLED"


def _is_souls_enabled() -> bool:
    return os.environ.get(_SOULS_ENABLED_ENV, "0").strip() not in ("0", "false", "False", "")


class ExecutableBeadRunner:
    """Coordinates storage and execution of executable beads.

    Args:
        bead_store: The project's :class:`BeadStore` instance.
        sandbox: A configured :class:`Sandbox` instance.
        soul_router: Optional :class:`SoulRouter`; required when
            ``BATON_SOULS_ENABLED=1``.
        notes_adapter: Optional :class:`NotesAdapter`; constructed from the
            current working directory when ``None``.
    """

    def __init__(
        self,
        bead_store: "BeadStore",
        sandbox: "Sandbox",
        soul_router: "SoulRouter | None" = None,
        notes_adapter: "NotesAdapter | None" = None,
    ) -> None:
        self._store = bead_store
        self._sandbox = sandbox
        self._soul_router = soul_router
        if notes_adapter is None:
            from agent_baton.core.engine.notes_adapter import NotesAdapter
            self._notes = NotesAdapter()
        else:
            self._notes = notes_adapter

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store(self, bead: "ExecutableBead", script_body: str) -> str:
        """Validate, sign (if souls enabled), and persist an executable bead.

        Args:
            bead: The :class:`ExecutableBead` to persist.  ``bead.bead_type``
                must be ``"executable"``.
            script_body: The full script text.

        Returns:
            The ``script_ref`` string (``refs/notes/baton-bead-scripts:<sha>``).

        Raises:
            ValueError: When the lint check fails or soul signature is
                required but unavailable.
        """
        from agent_baton.core.exec.script_lint import ScriptLinter
        from agent_baton.core.engine.notes_adapter import NotesAdapter

        # 1. Lint.
        linter = ScriptLinter()
        result = linter.lint(script_body, bead.interpreter)
        if not result.safe:
            details = "; ".join(
                f"line {ln}: [{pid}] {msg}"
                for pid, msg, ln in result.findings
            )
            raise ValueError(
                f"Script lint failed for bead {bead.bead_id}: {details}"
            )

        # 2. Compute content SHA and script_ref.
        content_sha = NotesAdapter.compute_script_sha(script_body)
        script_ref = NotesAdapter.script_ref_for(content_sha)
        bead.script_sha = content_sha
        bead.script_ref = script_ref
        bead.exec_ref = script_ref

        # 3. Soul signature when souls enabled.
        if _is_souls_enabled():
            self._require_soul_signature(bead)
        else:
            _log.warning(
                "BEAD_WARNING: souls disabled — executable bead %s is unsigned.",
                bead.bead_id,
            )

        # 4. Write script body to git notes (best-effort; store proceeds even
        #    if notes write fails, because SQLite is the primary store for v1).
        notes_ok = self._notes.write_script(content_sha, script_body)
        if not notes_ok:
            _log.warning(
                "BEAD_WARNING: notes-write-pending for script %s — "
                "body not in git notes, only in BeadStore.",
                content_sha[:8],
            )

        # 5. Persist the bead.
        written_id = self._store.write(bead)
        if not written_id:
            raise RuntimeError(
                f"BeadStore.write failed for executable bead {bead.bead_id}"
            )

        _log.debug(
            "ExecutableBeadRunner.store: stored %s (script=%s)",
            bead.bead_id, content_sha[:8],
        )
        return script_ref

    def run(self, bead_id: str) -> "ExecutionResult":
        """Execute a stored executable bead.

        Args:
            bead_id: The ``bead_id`` of an :class:`ExecutableBead` with
                ``status != 'quarantine'``.

        Returns:
            :class:`ExecutionResult` from the sandbox.

        Raises:
            ValueError: When the bead is not found, not executable, or is
                still in quarantine.
        """
        from agent_baton.core.exec.sandbox import SandboxConfig

        # 1. Resolve bead.
        bead = self._resolve_executable_bead(bead_id)

        # 2. Load script body.
        script_body = self._load_script(bead)

        # 3. Execute.
        config = SandboxConfig(
            timeout_s=bead.runtime_limits.get("timeout_s", 30),
            mem_mb=bead.runtime_limits.get("mem_mb", 256),
            net=bead.runtime_limits.get("net", False),
        )
        from agent_baton.core.exec.sandbox import Sandbox
        sandbox = Sandbox(config=config, spill_dir=self._sandbox._spill_dir)
        exec_result = sandbox.run(script_body, bead.interpreter)

        # 4. Record child discovery bead.
        child_bead_id = self._record_result_bead(bead, exec_result)

        # 5. Update parent bead run metadata.
        self._update_run_metadata(bead, exec_result, child_bead_id)

        return exec_result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_executable_bead(self, bead_id: str) -> "ExecutableBead":
        from agent_baton.models.bead import ExecutableBead

        raw = self._store.read(bead_id)
        if raw is None:
            raise ValueError(f"Bead not found: {bead_id}")
        if raw.bead_type != "executable":
            raise ValueError(
                f"Bead {bead_id} has type={raw.bead_type!r}, expected 'executable'"
            )
        if raw.status == "quarantine":
            raise ValueError(
                f"Bead {bead_id} is still in quarantine — "
                "obtain auditor approval before running."
            )
        # Re-hydrate as ExecutableBead.
        return ExecutableBead.from_dict(raw.to_dict())

    def _load_script(self, bead: "ExecutableBead") -> str:
        """Load script body from git notes or raise."""
        if not bead.script_sha:
            raise ValueError(
                f"ExecutableBead {bead.bead_id} has no script_sha — cannot load script."
            )
        body = self._notes.read_script(bead.script_sha)
        if body is None:
            raise ValueError(
                f"Script body for bead {bead.bead_id} (sha={bead.script_sha[:8]}) "
                "not found in refs/notes/baton-bead-scripts. "
                "Run `baton beads sync` to rebuild notes from the SQLite mirror."
            )
        return body

    def _record_result_bead(
        self,
        parent: "ExecutableBead",
        result: "ExecutionResult",
    ) -> str:
        """Create a child discovery bead linked to *parent* via validates/contradicts."""
        from agent_baton.models.bead import Bead, BeadLink

        link_type = "validates" if result.exit_code == 0 else "contradicts"
        now = _utcnow()

        # Generate child bead ID from parent + timestamp.
        child_sha = hashlib.sha256(
            f"{parent.bead_id}:{now}:{result.exit_code}".encode()
        ).hexdigest()
        child_bead_id = f"bd-{child_sha[:4]}"

        summary_lines = [
            f"exec result for {parent.bead_id}",
            f"interpreter={parent.interpreter}",
            f"exit_code={result.exit_code}",
            f"duration_ms={result.duration_ms}",
        ]
        if result.stdout.strip():
            summary_lines.append(f"stdout_tail={result.stdout[-200:]!r}")
        if result.stderr.strip():
            summary_lines.append(f"stderr_tail={result.stderr[-200:]!r}")
        if result.full_output_path:
            summary_lines.append(f"full_output={result.full_output_path}")

        child = Bead(
            bead_id=child_bead_id,
            task_id=parent.task_id,
            step_id=parent.step_id,
            agent_name=parent.agent_name,
            bead_type="discovery",
            content="\n".join(summary_lines),
            confidence="high" if result.exit_code == 0 else "low",
            scope=parent.scope,
            tags=["exec-result", f"exit:{result.exit_code}"],
            affected_files=parent.affected_files,
            status="open",
            created_at=now,
            source="agent-signal",
            links=[
                BeadLink(
                    target_bead_id=parent.bead_id,
                    link_type=link_type,
                    created_at=now,
                )
            ],
        )

        written = self._store.write(child)
        if not written:
            _log.warning(
                "ExecutableBeadRunner: failed to write result bead for %s",
                parent.bead_id,
            )
        return child_bead_id

    def _update_run_metadata(
        self,
        bead: "ExecutableBead",
        result: "ExecutionResult",
        child_bead_id: str,
    ) -> None:
        """Update last_run_at, last_exit_code, last_run_bead_id on the parent."""
        bead.last_run_at = _utcnow()
        bead.last_exit_code = result.exit_code
        bead.last_run_bead_id = child_bead_id
        self._store.write(bead)

    def _require_soul_signature(self, bead: "ExecutableBead") -> None:
        """Sign the bead with the current soul.  Raises if signing fails."""
        if self._soul_router is None:
            raise ValueError(
                f"BATON_SOULS_ENABLED=1 but no SoulRouter provided — "
                f"cannot sign executable bead {bead.bead_id}."
            )
        import json as _json

        affected = [bead.affected_files[0]] if bead.affected_files else []
        from pathlib import Path
        soul_candidates = self._soul_router.recommend(
            bead.agent_name,
            [Path(f) for f in affected],
        )
        if not soul_candidates:
            raise ValueError(
                f"No soul available to sign executable bead {bead.bead_id}. "
                "Mint a soul with `baton souls mint` first."
            )
        soul, _score = soul_candidates[0]
        canonical = _json.dumps(bead.to_dict(), separators=(",", ":"), sort_keys=True)
        try:
            bead.signed_by = soul.soul_id
            bead.signature = soul.sign(canonical.encode())
        except RuntimeError as exc:
            raise ValueError(
                f"Soul {soul.soul_id} cannot sign bead {bead.bead_id}: {exc}"
            ) from exc
