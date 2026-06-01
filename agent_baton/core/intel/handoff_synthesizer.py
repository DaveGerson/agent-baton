"""Automated handoff synthesis (Wave 3.2 — resolves bd-65d4 / bd-61a5).

When the dispatcher hands off from agent N (just completed) to agent N+1
(about to dispatch), agent N+1 currently starts as a stranger.  This
module synthesizes a compact "Handoff Bead" — at most ~400 chars — that
summarizes:

* **Files changed** — count + top 5 paths from the prior step.
* **Discoveries** — beads created by the prior step.
* **Blockers** — open ``warning`` beads whose files or tags overlap the
  next step's domain.
* **Outcome** — one-line status ("passed" / "failed").

The dispatcher prepends the handoff text as a ``## Handoff from Prior
Step`` section to the next agent's delegation prompt.

Design constraints:

* Single-task scope only — no cross-task handoff.
* Pure deterministic synthesis: no LLM calls, no embeddings.
* Best-effort: any internal failure returns ``None`` so the dispatch
  pipeline is never blocked.
* Skip when there is no prior step (first step of a phase).

The synthesized text is also persisted to the ``handoff_beads`` table
for audit (see :mod:`agent_baton.cli.commands.bead_cmd` for the read
surface).

ADR-13b WP-1 §A:
  :meth:`HandoffSynthesizer.synthesize_for_dispatch` now accepts a
  ``DerivedBeadStore | sqlite3.Connection | None`` as the ``conn``
  parameter.  When a ``DerivedBeadStore`` is passed, the handoff row is
  persisted there instead of the legacy baton.db connection.  Bead queries
  (discoveries + blockers) are sourced from ``bead_store`` when supplied,
  falling back to the raw ``conn`` SQL path for backward-compatibility with
  the SQLite backend.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any, Iterable

from agent_baton.utils.time import utcnow_zulu as _utcnow

if TYPE_CHECKING:
    from agent_baton.core.storage.derived_bead_store import DerivedBeadStore

_log = logging.getLogger(__name__)


# Hard cap on the synthesized handoff section, in characters.  The
# spec calls for ~400 chars max — anything larger inflates every
# subsequent dispatch prompt.
HANDOFF_MAX_CHARS = 400

# Maximum number of file paths to include in the "Files" line.
HANDOFF_MAX_FILES = 5


def _safe_attr(obj: Any, name: str, default: Any = None) -> Any:
    """Return ``obj.name`` if present and non-error, else *default*.

    Tolerant of dict-shaped fakes used in tests and partially-populated
    StepResult instances.
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _normalize_paths(paths: Iterable[str] | None) -> list[str]:
    if not paths:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for p in paths:
        if not p:
            continue
        s = str(p).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _next_step_domain_tokens(next_step: Any) -> set[str]:
    """Extract a set of file/path tokens that describe the next step's
    domain.  Used for blocker overlap matching.

    Pulls from ``allowed_paths`` first, falling back to ``context_files``,
    and includes top-level path segments (e.g. ``"agent_baton"``,
    ``"pmo-ui"``) as coarse tags.
    """
    tokens: set[str] = set()
    for attr in ("allowed_paths", "context_files"):
        for p in _normalize_paths(_safe_attr(next_step, attr, []) or []):
            tokens.add(p)
            head = p.split("/", 1)[0]
            if head:
                tokens.add(head)
    # Also include the agent name as a coarse domain tag (some warning
    # beads are tagged by author/agent rather than file path).
    agent = _safe_attr(next_step, "agent_name", "") or ""
    if agent:
        tokens.add(str(agent))
    return tokens


def _gate_outcome_line(prior_step_result: Any) -> str:
    """Render a one-line outcome summary for the prior step."""
    status = (_safe_attr(prior_step_result, "status", "") or "").strip()
    if status == "complete":
        return "passed"
    if status == "failed":
        return "failed"
    if status:
        return status
    return "unknown"


def _query_beads_for_step(
    conn: Any, *, task_id: str, step_id: str
) -> list[dict[str, Any]]:
    """Return raw bead rows created during a particular step of a task.

    Uses raw SQL (rather than the BeadStore facade) so the synthesizer
    has zero engine-import-cycle risk and can be invoked from anywhere
    a sqlite3 connection is available.
    """
    if conn is None or not task_id or not step_id:
        return []
    try:
        cur = conn.execute(
            "SELECT bead_id, bead_type, content, status, agent_name "
            "FROM beads WHERE task_id = ? AND step_id = ? "
            "ORDER BY created_at ASC",
            (task_id, step_id),
        )
        rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        _log.debug("_query_beads_for_step failed: %s", exc)
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            out.append({
                "bead_id": r[0],
                "bead_type": r[1],
                "content": r[2] or "",
                "status": r[3],
                "agent_name": r[4],
            })
        except Exception:
            continue
    return out


def _query_open_warnings(conn: Any, *, task_id: str) -> list[dict[str, Any]]:
    """Return open ``warning`` beads for a task with their tags + files.

    Joined with ``bead_tags`` so callers can perform tag-overlap matching
    without a second round-trip.
    """
    if conn is None or not task_id:
        return []
    try:
        cur = conn.execute(
            "SELECT bead_id, content, affected_files FROM beads "
            "WHERE task_id = ? AND bead_type = 'warning' AND status = 'open'",
            (task_id,),
        )
        rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        _log.debug("_query_open_warnings failed: %s", exc)
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        bead_id = r[0]
        # affected_files is stored as a JSON-encoded list (see BeadStore).
        # Tolerate the legacy CSV form just in case some older data leaks
        # through.
        files_raw = r[2] or ""
        files: list[str] = []
        if files_raw:
            try:
                parsed = json.loads(files_raw)
                if isinstance(parsed, list):
                    files = [str(p).strip() for p in parsed if p]
            except Exception:
                files = [f.strip() for f in str(files_raw).split(",") if f.strip()]
        # Pull tags via a second mini-query — cheap, and avoids a join
        # that would require GROUP_CONCAT (not available in older SQLite
        # without compile flags on every platform).
        try:
            tag_rows = conn.execute(
                "SELECT tag FROM bead_tags WHERE bead_id = ?", (bead_id,)
            ).fetchall()
            tags = [t[0] for t in tag_rows if t and t[0]]
        except Exception:
            tags = []
        out.append({
            "bead_id": bead_id,
            "content": r[1] or "",
            "files": files,
            "tags": tags,
        })
    return out


def _stable_handoff_id(task_id: str, from_step_id: str, to_step_id: str) -> str:
    """Deterministic handoff_id keyed on the (task, from, to) triple."""
    h = hashlib.sha256(
        f"{task_id}:{from_step_id}:{to_step_id}".encode("utf-8")
    ).hexdigest()
    return f"hf-{h[:12]}"


def _is_derived_bead_store(obj: Any) -> bool:
    """Return True when *obj* is a DerivedBeadStore instance.

    Uses duck-typing / class-name check to avoid a circular import — the
    synthesizer lives in ``core/intel/`` while ``DerivedBeadStore`` lives in
    ``core/storage/``.
    """
    return type(obj).__name__ == "DerivedBeadStore"


def _query_beads_for_step_via_store(
    store: Any,
    *,
    task_id: str,
    step_id: str,
) -> list[dict[str, Any]]:
    """Query discoveries for *step_id* via a bead store's ``query()`` method.

    Returns the same dict shape as :func:`_query_beads_for_step`.
    """
    if store is None or not task_id or not step_id:
        return []
    try:
        beads = store.query(task_id=task_id, limit=500)
        out: list[dict[str, Any]] = []
        for b in beads:
            if getattr(b, "step_id", "") == step_id:
                out.append({
                    "bead_id": b.bead_id,
                    "bead_type": b.bead_type,
                    "content": b.content or "",
                    "status": b.status,
                    "agent_name": b.agent_name,
                })
        return out
    except Exception as exc:  # noqa: BLE001
        _log.debug("_query_beads_for_step_via_store failed: %s", exc)
        return []


def _query_open_warnings_via_store(
    store: Any,
    *,
    task_id: str,
) -> list[dict[str, Any]]:
    """Query open warning beads via a bead store's ``query()`` method.

    Returns the same dict shape as :func:`_query_open_warnings`.
    """
    if store is None or not task_id:
        return []
    try:
        beads = store.query(task_id=task_id, bead_type="warning", status="open", limit=200)
        out: list[dict[str, Any]] = []
        for b in beads:
            out.append({
                "bead_id": b.bead_id,
                "content": b.content or "",
                "files": list(b.affected_files or []),
                "tags": list(b.tags or []),
            })
        return out
    except Exception as exc:  # noqa: BLE001
        _log.debug("_query_open_warnings_via_store failed: %s", exc)
        return []


class HandoffSynthesizer:
    """Synthesize a compact handoff document between consecutive steps."""

    def synthesize_for_dispatch(
        self,
        prior_step_result: Any,
        next_step: Any,
        conn: Any,
        *,
        task_id: str | None = None,
        bead_store: Any = None,
        derived: "DerivedBeadStore | None" = None,
    ) -> str | None:
        """Build a ~400-char handoff text from prior_step_result for next_step.

        Args:
            prior_step_result: The most recent completed StepResult-shaped
                object.  Pass ``None`` to indicate "no prior step" — the
                method returns ``None`` immediately in that case.
            next_step: The PlanStep about to be dispatched.  Used to
                derive the domain (allowed_paths / context_files) for
                blocker filtering.
            conn: Open sqlite3 connection on the project baton.db, a
                :class:`~agent_baton.core.storage.derived_bead_store.DerivedBeadStore`,
                or ``None``.  When a raw sqlite3 connection is supplied,
                discoveries + blockers are queried directly via SQL
                (SQLite backend path).  When a ``DerivedBeadStore`` is
                supplied, *bead_store* is used for bead queries and the
                handoff row is persisted to the derived DB.  Pass ``None``
                to skip bead queries (file-only handoff).
            task_id: Task scope for bead queries.  When omitted, falls
                back to ``prior_step_result.task_id`` if present.
            bead_store: Optional bead store (``BeadStore`` or
                ``BdBeadStore``) used to query discoveries and blockers
                when *conn* is a ``DerivedBeadStore`` or ``None``.
                Ignored when *conn* is a raw sqlite3 connection.
            derived: Optional
                :class:`~agent_baton.core.storage.derived_bead_store.DerivedBeadStore`
                used to persist the handoff row.  When omitted and *conn*
                is a ``DerivedBeadStore``, *conn* itself is used.
                Ignored when *conn* is a raw sqlite3 connection (the row
                is written to that connection instead).

        Returns:
            Handoff text (already capped at ``HANDOFF_MAX_CHARS``) or
            ``None`` when there is no prior step / nothing to say / on
            any internal error.
        """
        if prior_step_result is None or next_step is None:
            return None
        try:
            return self._synthesize_inner(
                prior_step_result, next_step, conn,
                task_id=task_id,
                bead_store=bead_store,
                derived=derived,
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug("HandoffSynthesizer.synthesize_for_dispatch failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _synthesize_inner(
        self,
        prior_step_result: Any,
        next_step: Any,
        conn: Any,
        *,
        task_id: str | None,
        bead_store: Any = None,
        derived: "DerivedBeadStore | None" = None,
    ) -> str | None:
        prior_step_id = _safe_attr(prior_step_result, "step_id", "") or ""
        next_step_id = _safe_attr(next_step, "step_id", "") or ""

        # Defensive: if we have neither step_id, there is no useful handoff.
        if not prior_step_id and not next_step_id:
            return None

        files_changed = _normalize_paths(
            _safe_attr(prior_step_result, "files_changed", []) or []
        )
        outcome = _gate_outcome_line(prior_step_result)

        resolved_task_id = task_id or _safe_attr(prior_step_result, "task_id", "") or ""

        # Determine whether conn is a raw sqlite3 connection or a DerivedBeadStore.
        _is_derived_store = _is_derived_bead_store(conn)

        # Discoveries and blockers: prefer bead_store query; fall back to
        # raw SQL when only a sqlite3 connection is available.
        if bead_store is not None or _is_derived_store:
            discoveries = _query_beads_for_step_via_store(
                bead_store,
                task_id=resolved_task_id,
                step_id=prior_step_id,
            )
            all_warnings = _query_open_warnings_via_store(
                bead_store,
                task_id=resolved_task_id,
            )
        else:
            # Legacy path: raw sqlite3 connection (SQLite backend).
            discoveries = _query_beads_for_step(
                conn, task_id=resolved_task_id, step_id=prior_step_id
            )
            all_warnings = _query_open_warnings(conn, task_id=resolved_task_id)

        # Blockers — open warnings whose files/tags overlap the next step.
        next_tokens = _next_step_domain_tokens(next_step)
        blockers: list[dict[str, Any]] = []
        for w in all_warnings:
            wf = set(w.get("files") or [])
            wt = set(w.get("tags") or [])
            if (wf & next_tokens) or (wt & next_tokens):
                blockers.append(w)

        # If literally nothing is worth saying AND outcome is unknown, skip.
        if (
            not files_changed
            and not discoveries
            and not blockers
            and outcome == "unknown"
        ):
            return None

        # Render compact lines.  Cap each line independently to keep the
        # final body close to HANDOFF_MAX_CHARS without requiring a
        # second-pass truncation that could chop mid-token.
        lines: list[str] = []

        # Files line
        if files_changed:
            top = files_changed[:HANDOFF_MAX_FILES]
            extra = len(files_changed) - len(top)
            files_str = ", ".join(top)
            if extra > 0:
                files_str += f" (+{extra} more)"
            lines.append(f"Files ({len(files_changed)}): {files_str}")
        else:
            lines.append("Files: none")

        # Discoveries line
        if discoveries:
            ids = [d["bead_id"] for d in discoveries[:5] if d.get("bead_id")]
            extra = len(discoveries) - len(ids)
            disc_str = ", ".join(ids)
            if extra > 0:
                disc_str += f" (+{extra} more)"
            lines.append(f"Discoveries: {disc_str}")

        # Blockers line
        if blockers:
            ids = [b["bead_id"] for b in blockers[:3] if b.get("bead_id")]
            extra = len(blockers) - len(ids)
            blk_str = ", ".join(ids)
            if extra > 0:
                blk_str += f" (+{extra} more)"
            lines.append(f"Blockers (open warnings overlapping your domain): {blk_str}")

        # Outcome line
        prior_label = prior_step_id or "prior step"
        lines.append(f"Outcome of {prior_label}: {outcome}.")

        body = "\n".join(lines)

        # Final hard cap.  Truncate-with-ellipsis if we somehow exceeded.
        if len(body) > HANDOFF_MAX_CHARS:
            body = body[: HANDOFF_MAX_CHARS - 3].rstrip() + "..."

        # Persist (best-effort).  Persistence failure does NOT suppress
        # the returned text — the prompt-side benefit must not depend on
        # the audit-side write succeeding.
        if resolved_task_id:
            # Resolve the write target: DerivedBeadStore > explicit derived >
            # raw sqlite3 conn.
            persist_target: Any = None
            if derived is not None:
                persist_target = derived
            elif _is_derived_store:
                persist_target = conn
            elif conn is not None:
                persist_target = conn
            if persist_target is not None:
                try:
                    self._persist(
                        persist_target,
                        task_id=resolved_task_id,
                        from_step_id=prior_step_id,
                        to_step_id=next_step_id,
                        content=body,
                    )
                except Exception as exc:  # noqa: BLE001
                    _log.debug("HandoffSynthesizer._persist failed: %s", exc)

        return body

    @staticmethod
    def _persist(
        target: Any,
        *,
        task_id: str,
        from_step_id: str,
        to_step_id: str,
        content: str,
    ) -> None:
        """Idempotent INSERT-OR-REPLACE into handoff_beads.

        *target* may be a raw ``sqlite3.Connection`` (legacy SQLite path)
        or a :class:`~agent_baton.core.storage.derived_bead_store.DerivedBeadStore`
        (bd / derived-DB path).
        """
        handoff_id = _stable_handoff_id(task_id, from_step_id, to_step_id)
        now = _utcnow()

        if _is_derived_bead_store(target):
            # Persist via DerivedBeadStore — use its connection() context manager.
            with target.connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO handoff_beads "
                    "(handoff_id, task_id, from_step_id, to_step_id, content, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (handoff_id, task_id, from_step_id, to_step_id, content, now),
                )
        else:
            # Legacy path: raw sqlite3 connection.
            target.execute(
                "INSERT OR REPLACE INTO handoff_beads "
                "(handoff_id, task_id, from_step_id, to_step_id, content, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (handoff_id, task_id, from_step_id, to_step_id, content, now),
            )
            try:
                target.commit()
            except Exception:
                # Some test fixtures pass autocommit connections.
                pass
