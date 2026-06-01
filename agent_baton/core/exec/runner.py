"""Wave 6.1 Part C — Executable Beads: ExecutableBeadRunner (bd-81b9).

Glue layer that ties together the bead store, soul signing, and sandbox
execution into a single ``run()`` / ``store()`` interface.

ADR-13b WP-G: git-notes (NotesAdapter) was removed.  Script bodies are
persisted exclusively in the bead's ``script_body`` metadata field, which is
stored in ``.beads/issues.jsonl`` by the bd backend.

Responsibilities:
- ``store(bead, script_body)``:
    1. Lint the script via :class:`ScriptLinter`.
    2. Compute content SHA and construct the ``script_ref``.
    3. Require a soul signature when ``BATON_SOULS_ENABLED=1``.
    4. Write the bead itself (with ``bead_type="executable"`` and inline
       ``script_body``) to the bead store.
    5. Return the ``script_ref`` string.

- ``run(bead_id)``:
    1. Resolve the :class:`ExecutableBead` from the store.
    2. Load the script body from the bead's inline ``script_body`` field.
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
    from agent_baton.core.engine.bd_bead_store import BdBeadStore
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
        bead_store: The project's bead store (a :class:`BdBeadStore`).
        sandbox: A configured :class:`Sandbox` instance.
        soul_router: Optional :class:`SoulRouter`; required when
            ``BATON_SOULS_ENABLED=1``.
    """

    def __init__(
        self,
        bead_store: "BdBeadStore",
        sandbox: "Sandbox",
        soul_router: "SoulRouter | None" = None,
    ) -> None:
        self._store = bead_store
        self._sandbox = sandbox
        self._soul_router = soul_router

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
            The ``script_ref`` string (content-addressed identifier).

        Raises:
            ValueError: When the lint check fails or soul signature is
                required but unavailable.
        """
        from agent_baton.core.exec.script_lint import ScriptLinter

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
        from agent_baton.core.exec.script_hash import (
            compute_script_sha,
            script_ref_for,
        )
        content_sha = compute_script_sha(script_body)
        script_ref = script_ref_for(content_sha)
        bead.script_sha = content_sha
        bead.script_ref = script_ref
        bead.exec_ref = script_ref
        # ADR-13b WP-G: script body persisted exclusively in bd metadata blob.
        bead.script_body = script_body

        # 3. Soul signature when souls enabled.
        if _is_souls_enabled():
            self._require_soul_signature(bead)
        else:
            _log.warning(
                "BEAD_WARNING: souls disabled — executable bead %s is unsigned.",
                bead.bead_id,
            )

        # 4. Persist the bead (script_body rides in bd metadata).
        written_id = self._store.write(bead)
        if not written_id:
            raise RuntimeError(
                f"BdBeadStore.write failed for executable bead {bead.bead_id}"
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
        """Load script body from the bead's inline ``script_body`` field.

        ADR-13b WP-G: script bodies are stored exclusively in bd metadata.
        The git-notes fallback path has been removed.
        """
        if not bead.script_body:
            raise ValueError(
                f"ExecutableBead {bead.bead_id} has no script_body in bd metadata. "
                "The bead may have been created before ADR-13b WP-G or the body "
                "was lost during migration.  Re-store the bead with a body."
            )

        # Integrity guard: verify body against the SHA recorded at store() time.
        if bead.script_sha:
            from agent_baton.core.exec.script_hash import compute_script_sha
            actual = compute_script_sha(bead.script_body)
            if actual != bead.script_sha:
                raise ValueError(
                    f"ExecutableBead {bead.bead_id} script_body failed integrity "
                    f"check (sha {actual[:8]} != recorded {bead.script_sha[:8]}) — "
                    "the script may have been tampered with; refusing to run."
                )
        return bead.script_body

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
