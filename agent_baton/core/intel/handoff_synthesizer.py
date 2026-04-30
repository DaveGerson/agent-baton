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
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Iterable

from agent_baton.utils.time import utcnow_zulu as _utcnow

_log = logging.getLogger(__name__)


# Hard cap on the synthesized handoff section, in characters.  The
# spec calls for ~400 chars max — anything larger inflates every
# subsequent dispatch prompt.
HANDOFF_MAX_CHARS = 400

# Hard cap on the structured 4KB prompt block (Tier 2).
HANDOFF_STRUCTURED_MAX_CHARS = 4096

# Maximum number of file paths to include in the "Files" line.
HANDOFF_MAX_FILES = 5

# Maximum file paths for the structured block (higher fidelity than compact).
HANDOFF_STRUCTURED_MAX_FILES = 20

# Maximum open-question (warning) beads shown in structured block.
HANDOFF_STRUCTURED_MAX_QUESTIONS = 5

# Maximum chars per open-question message before truncation.
HANDOFF_STRUCTURED_QUESTION_MAX_CHARS = 120


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


def _query_decision_beads_for_step(
    conn: Any, *, task_id: str, step_id: str
) -> list[dict[str, Any]]:
    """Return decision-type beads (or decision-tagged beads) for a step.

    Tries bead_type='decision' first; falls back to tag-based lookup when
    the schema carries them only as tagged non-decision beads.
    """
    if conn is None or not task_id or not step_id:
        return []
    # Primary: explicit bead_type = 'decision'.
    try:
        cur = conn.execute(
            "SELECT bead_id, content FROM beads "
            "WHERE task_id = ? AND step_id = ? AND bead_type = 'decision' "
            "ORDER BY created_at ASC",
            (task_id, step_id),
        )
        rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        _log.debug("_query_decision_beads_for_step (type) failed: %s", exc)
        rows = []

    if rows:
        return [{"bead_id": r[0], "content": r[1] or ""} for r in rows]

    # Fallback: any bead from the step tagged 'decision'.
    try:
        cur = conn.execute(
            "SELECT b.bead_id, b.content FROM beads b "
            "JOIN bead_tags t ON t.bead_id = b.bead_id "
            "WHERE b.task_id = ? AND b.step_id = ? AND t.tag = 'decision' "
            "ORDER BY b.created_at ASC",
            (task_id, step_id),
        )
        rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        _log.debug("_query_decision_beads_for_step (tag) failed: %s", exc)
        return []

    return [{"bead_id": r[0], "content": r[1] or ""} for r in rows]


def _stable_handoff_id(task_id: str, from_step_id: str, to_step_id: str) -> str:
    """Deterministic handoff_id keyed on the (task, from, to) triple."""
    h = hashlib.sha256(
        f"{task_id}:{from_step_id}:{to_step_id}".encode("utf-8")
    ).hexdigest()
    return f"hf-{h[:12]}"


class HandoffSynthesizer:
    """Synthesize a compact handoff document between consecutive steps."""

    def synthesize_structured_for_dispatch(
        self,
        prior_step_result: Any,
        next_step: Any,
        conn: Any,
        *,
        task_id: str | None = None,
    ) -> str | None:
        """Build a structured 4KB handoff block for injection into the next agent's prompt.

        This is Tier 2: a richer Markdown block rendered fresh per-dispatch,
        never persisted.  On any internal failure returns None so the dispatch
        pipeline can fall back gracefully to the compact Tier 1 text.

        Args:
            prior_step_result: Completed StepResult-shaped object (or None).
            next_step: PlanStep about to be dispatched.
            conn: Open sqlite3 connection on baton.db (may be None).
            task_id: Task scope for bead queries.

        Returns:
            Structured Markdown string (capped at HANDOFF_STRUCTURED_MAX_CHARS)
            or None when there is no prior step / nothing useful to render.
        """
        if prior_step_result is None or next_step is None:
            return None
        try:
            return self._synthesize_structured_inner(
                prior_step_result, next_step, conn, task_id=task_id
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug(
                "HandoffSynthesizer.synthesize_structured_for_dispatch failed: %s", exc
            )
            return None

    def _synthesize_structured_inner(
        self,
        prior_step_result: Any,
        next_step: Any,
        conn: Any,
        *,
        task_id: str | None,
    ) -> str | None:
        prior_step_id = _safe_attr(prior_step_result, "step_id", "") or ""

        # Nothing to say without a step identity.
        if not prior_step_id:
            return None

        resolved_task_id = task_id or _safe_attr(prior_step_result, "task_id", "") or ""

        files_changed = _normalize_paths(
            _safe_attr(prior_step_result, "files_changed", []) or []
        )
        outcome = _gate_outcome_line(prior_step_result)

        # Key decisions — beads with bead_type='decision' (or tag fallback).
        decisions = _query_decision_beads_for_step(
            conn, task_id=resolved_task_id, step_id=prior_step_id
        )

        # Open questions — open warning beads overlapping the next step.
        next_tokens = _next_step_domain_tokens(next_step)
        all_warnings = _query_open_warnings(conn, task_id=resolved_task_id)
        blockers: list[dict[str, Any]] = []
        for w in all_warnings:
            wf = set(w.get("files") or [])
            wt = set(w.get("tags") or [])
            if (wf & next_tokens) or (wt & next_tokens):
                blockers.append(w)

        # Skip entirely when there is nothing useful to render.
        if (
            not files_changed
            and not decisions
            and not blockers
            and outcome == "unknown"
        ):
            return None

        # Build each section individually so proportional capping is clean.
        sections: list[str] = ["### Handoff from Prior Step", ""]

        # --- Files section ---
        if files_changed:
            top = files_changed[:HANDOFF_STRUCTURED_MAX_FILES]
            extra = len(files_changed) - len(top)
            files_str = ", ".join(top)
            if extra > 0:
                files_str += f", ... (+{extra} more)"
            sections.append(f"**Files changed ({len(files_changed)})**: {files_str}")
            sections.append("")

        # --- Key decisions section ---
        if decisions:
            sections.append("**Key decisions**:")
            for d in decisions:
                msg = (d.get("content") or "").strip()
                if not msg:
                    continue
                # One-line: collapse newlines and cap per-item length.
                one_line = msg.replace("\n", " ")
                if len(one_line) > 120:
                    one_line = one_line[:117] + "..."
                sections.append(f"- {one_line}")
            sections.append("")

        # --- Open questions section ---
        if blockers:
            sections.append("**Open questions**:")
            for w in blockers[:HANDOFF_STRUCTURED_MAX_QUESTIONS]:
                msg = (w.get("content") or "").strip()
                if not msg:
                    continue
                one_line = msg.replace("\n", " ")
                if len(one_line) > HANDOFF_STRUCTURED_QUESTION_MAX_CHARS:
                    one_line = one_line[:HANDOFF_STRUCTURED_QUESTION_MAX_CHARS - 3] + "..."
                sections.append(f"- {one_line}")
            sections.append("")

        # --- Outcome section ---
        sections.append(
            f"**Outcome of {prior_step_id}**: {outcome}"
        )

        body = "\n".join(sections)

        # Enforce the 4KB cap.  If we exceed it, truncate at the last complete
        # newline before the cap so we never chop mid-line.
        if len(body) > HANDOFF_STRUCTURED_MAX_CHARS:
            cut = body[: HANDOFF_STRUCTURED_MAX_CHARS - 3].rfind("\n")
            if cut > 0:
                body = body[:cut].rstrip() + "\n..."
            else:
                body = body[: HANDOFF_STRUCTURED_MAX_CHARS - 3] + "..."

        return body

    def synthesize_for_dispatch(
        self,
        prior_step_result: Any,
        next_step: Any,
        conn: Any,
        *,
        task_id: str | None = None,
    ) -> str | None:
        """Build a ~400-char handoff text from prior_step_result for next_step.

        Args:
            prior_step_result: The most recent completed StepResult-shaped
                object.  Pass ``None`` to indicate "no prior step" — the
                method returns ``None`` immediately in that case.
            next_step: The PlanStep about to be dispatched.  Used to
                derive the domain (allowed_paths / context_files) for
                blocker filtering.
            conn: Open sqlite3 connection on the project baton.db.  Used
                to query the ``beads`` and ``bead_tags`` tables for
                discoveries + blockers.  Pass ``None`` to skip both
                queries (file-only handoff).
            task_id: Task scope for bead queries.  When omitted, falls
                back to ``prior_step_result.task_id`` if present.

        Returns:
            Handoff text (already capped at ``HANDOFF_MAX_CHARS``) or
            ``None`` when there is no prior step / nothing to say / on
            any internal error.
        """
        if prior_step_result is None or next_step is None:
            return None
        try:
            return self._synthesize_inner(
                prior_step_result, next_step, conn, task_id=task_id
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

        # Discoveries — beads created by the prior step.
        resolved_task_id = task_id or _safe_attr(prior_step_result, "task_id", "") or ""
        discoveries = _query_beads_for_step(
            conn, task_id=resolved_task_id, step_id=prior_step_id
        )

        # Blockers — open warnings whose files/tags overlap the next step.
        next_tokens = _next_step_domain_tokens(next_step)
        all_warnings = _query_open_warnings(conn, task_id=resolved_task_id)
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
        if conn is not None and resolved_task_id:
            try:
                self._persist(
                    conn,
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
        conn: Any,
        *,
        task_id: str,
        from_step_id: str,
        to_step_id: str,
        content: str,
    ) -> None:
        """Idempotent INSERT-OR-REPLACE into handoff_beads."""
        handoff_id = _stable_handoff_id(task_id, from_step_id, to_step_id)
        conn.execute(
            "INSERT OR REPLACE INTO handoff_beads "
            "(handoff_id, task_id, from_step_id, to_step_id, content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (handoff_id, task_id, from_step_id, to_step_id, content, _utcnow()),
        )
        try:
            conn.commit()
        except Exception:
            # Some test fixtures pass autocommit connections.
            pass
