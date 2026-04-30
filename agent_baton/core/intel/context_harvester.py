"""ContextHarvester (Wave 2.2) — eliminate agent cold-start re-discovery.

After every successful step, ``ContextHarvester.harvest`` writes a compact
3-5 line summary of what the dispatched agent touched into the
``agent_context`` table, keyed by ``(agent_name, domain)``.  On the next
dispatch to the same pair, :func:`PromptDispatcher.build_delegation_prompt`
prepends a "Prior Context" block so the agent sees its own prior work
rather than starting cold.

Design choices
--------------
* **Best-effort.** Every public method swallows exceptions and logs at
  ``debug`` level.  The harvester must never block step recording.
* **Deterministic by default.** The summary is composed from the
  StepResult fields directly.  When ``ANTHROPIC_API_KEY`` is set the
  caller MAY route through Haiku via :class:`HeadlessClaude`, but the
  default path is sync + zero-LLM-cost so it can run on the hot path.
* **Feature-flagged.** ``BATON_HARVEST_CONTEXT=0`` disables harvesting.
  Default is on (any other value, or unset).
* **Domain derivation.** ``StepResult`` has no ``domain`` field, so the
  harvester derives one from the first segment of ``allowed_paths[0]``
  (or ``files_changed[0]``), falling back to ``"general"``.
* **One row per pair.** Upsert semantics — only the most recent harvest
  per ``(agent_name, domain)`` is retained.

Non-goals
---------
No semantic similarity, no embeddings, no cross-agent learning.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any

from agent_baton.utils.time import utcnow_zulu as _utcnow

logger = logging.getLogger(__name__)

# Hard cap on the prepended Prior Context block (chars, not tokens).
# Keep small: prepended on every dispatch.
_MAX_SUMMARY_CHARS = 400

_ENV_FLAG = "BATON_HARVEST_CONTEXT"


def is_enabled() -> bool:
    """Return ``True`` when harvesting is enabled via env flag.

    Default: enabled. Set ``BATON_HARVEST_CONTEXT=0`` to disable.
    """
    return os.environ.get(_ENV_FLAG, "1").strip() != "0"


def derive_domain(step_result: Any, plan_step: Any | None = None) -> str:
    """Derive a coarse domain label from a step.

    Tries, in order:

    1. ``plan_step.allowed_paths[0]`` first segment
    2. ``step_result.files_changed[0]`` first segment

    Falls back to ``"general"``.  The "first segment" is the leading
    path component (e.g. ``"agent_baton/core/foo.py"`` → ``"agent_baton"``).
    """
    candidates: list[str] = []
    if plan_step is not None:
        ap = getattr(plan_step, "allowed_paths", None) or []
        if ap:
            candidates.append(ap[0])
    fc = getattr(step_result, "files_changed", None) or []
    if fc:
        candidates.append(fc[0])

    for raw in candidates:
        if not raw:
            continue
        # Normalize: strip leading slashes / dots, take first segment
        clean = raw.lstrip("./").lstrip("/")
        if not clean:
            continue
        head = clean.split("/", 1)[0]
        if head:
            return head
    return "general"


def _compose_summary(
    step_result: Any,
    *,
    files_touched: list[str],
    gates_passed: list[str],
    gates_failed: list[str],
) -> str:
    """Compose a deterministic 3-5 line summary string.

    Format::

        Touched N files in <domain-hint>. Gates: passed=[...]. Failures: [...].
        Recent step: <step_id> (<status>): <first 80 chars of outcome>
    """
    n = len(files_touched)
    file_hint = ""
    if files_touched:
        # Show first two file basenames as a hint
        from os.path import basename
        sample = ", ".join(basename(f) for f in files_touched[:2])
        file_hint = f" ({sample})"

    passed = ", ".join(gates_passed) if gates_passed else "none"
    failed = ", ".join(gates_failed) if gates_failed else "none"

    outcome_snippet = ""
    raw_outcome = (getattr(step_result, "outcome", "") or "").strip()
    if raw_outcome:
        outcome_snippet = raw_outcome.split("\n", 1)[0][:140]

    step_id = getattr(step_result, "step_id", "") or ""
    status = getattr(step_result, "status", "") or ""

    lines = [
        f"Touched {n} file(s){file_hint}. Gates passed: {passed}. Failures: {failed}.",
    ]
    if outcome_snippet:
        lines.append(f"Last step {step_id} ({status}): {outcome_snippet}")
    summary = "\n".join(lines)
    if len(summary) > _MAX_SUMMARY_CHARS:
        summary = summary[: _MAX_SUMMARY_CHARS - 3] + "..."
    return summary


def _parse_files_from_outcome(outcome: str) -> list[str]:
    """Best-effort: extract path-looking tokens from an outcome string.

    Used as a fallback when ``files_changed`` is empty.  Looks for tokens
    that contain a ``/`` and end with a recognized source extension.
    """
    if not outcome:
        return []
    exts = (".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".json", ".sql", ".sh")
    found: list[str] = []
    for raw_tok in outcome.replace(",", " ").replace("`", " ").split():
        tok = raw_tok.strip(".,;:()[]<>\"'")
        if "/" in tok and tok.endswith(exts) and len(tok) < 256:
            if tok not in found:
                found.append(tok)
        if len(found) >= 8:
            break
    return found


class ContextHarvester:
    """Harvest per-step learnings into ``agent_context``.

    The class is stateless; every call to :meth:`harvest` operates purely
    on its arguments.  Connection management is the caller's responsibility
    (the executor passes its open SQLite connection).

    Attributes:
        max_summary_chars: Hard cap on the composed summary string.
    """

    def __init__(self, max_summary_chars: int = _MAX_SUMMARY_CHARS) -> None:
        self.max_summary_chars = max_summary_chars

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def harvest(
        self,
        step_result: Any,
        conn: sqlite3.Connection,
        *,
        plan_step: Any | None = None,
        task_id: str = "",
        gate_outcomes: dict[str, str] | None = None,
    ) -> None:
        """Write a compact learning row for ``step_result`` into agent_context.

        This is a best-effort, fire-and-forget operation.  Any exception is
        swallowed and logged at ``debug`` level so that step recording never
        breaks because of harvest failures.

        Args:
            step_result: A ``StepResult``-shaped object with ``agent_name``,
                ``status``, ``outcome``, ``files_changed`` attributes.
            conn: An open SQLite connection bound to the project ``baton.db``.
            plan_step: The originating ``PlanStep`` (used to derive domain
                from ``allowed_paths``).  Optional.
            task_id: The task that produced this step.  Stored as
                ``last_task_id``.
            gate_outcomes: Optional ``{gate_id: "pass"|"fail"}`` map for the
                phase containing this step.  When omitted the harvest writes
                empty pass/fail lists.
        """
        if not is_enabled():
            return

        try:
            self._harvest_inner(
                step_result=step_result,
                conn=conn,
                plan_step=plan_step,
                task_id=task_id,
                gate_outcomes=gate_outcomes or {},
            )
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.debug("ContextHarvester.harvest failed (non-fatal): %s", exc)

    def _harvest_inner(
        self,
        *,
        step_result: Any,
        conn: sqlite3.Connection,
        plan_step: Any | None,
        task_id: str,
        gate_outcomes: dict[str, str],
    ) -> None:
        agent_name = getattr(step_result, "agent_name", "") or ""
        if not agent_name:
            return  # Nothing to key on

        # Only harvest when the step actually completed cleanly.
        status = getattr(step_result, "status", "") or ""
        if status != "complete":
            return

        domain = derive_domain(step_result, plan_step)

        # Files: prefer step_result.files_changed; fall back to outcome scrape
        files_changed = list(getattr(step_result, "files_changed", None) or [])
        if not files_changed:
            files_changed = _parse_files_from_outcome(
                getattr(step_result, "outcome", "") or ""
            )

        gates_passed = sorted(g for g, v in gate_outcomes.items() if v == "pass")
        gates_failed = sorted(g for g, v in gate_outcomes.items() if v == "fail")

        summary = _compose_summary(
            step_result,
            files_touched=files_changed,
            gates_passed=gates_passed,
            gates_failed=gates_failed,
        )

        strategies_worked = "; ".join(gates_passed) if gates_passed else ""
        strategies_failed = "; ".join(gates_failed) if gates_failed else ""

        conn.execute(
            """
            INSERT INTO agent_context
                (agent_name, domain, expertise_summary, strategies_worked,
                 strategies_failed, last_task_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_name, domain) DO UPDATE SET
                expertise_summary  = excluded.expertise_summary,
                strategies_worked  = excluded.strategies_worked,
                strategies_failed  = excluded.strategies_failed,
                last_task_id       = excluded.last_task_id,
                updated_at         = excluded.updated_at
            """,
            (
                agent_name,
                domain,
                summary,
                strategies_worked,
                strategies_failed,
                task_id,
                _utcnow(),
            ),
        )
        conn.commit()
        logger.debug(
            "ContextHarvester: upserted (%s, %s) — %d file(s), %d/%d gates pass/fail",
            agent_name,
            domain,
            len(files_changed),
            len(gates_passed),
            len(gates_failed),
        )

    # ------------------------------------------------------------------
    # Read side (used by dispatcher and CLI)
    # ------------------------------------------------------------------

    @staticmethod
    def fetch_one(
        conn: sqlite3.Connection,
        agent_name: str,
        domain: str,
    ) -> dict[str, str] | None:
        """Return the agent_context row for ``(agent_name, domain)`` or None.

        Best-effort: returns None on any error (e.g. table missing on an
        older DB).
        """
        try:
            cur = conn.execute(
                """
                SELECT agent_name, domain, expertise_summary,
                       strategies_worked, strategies_failed,
                       last_task_id, updated_at
                  FROM agent_context
                 WHERE agent_name = ? AND domain = ?
                 LIMIT 1
                """,
                (agent_name, domain),
            )
            row = cur.fetchone()
            if row is None:
                return None
            # sqlite3.Row supports keys()
            return {k: row[k] for k in row.keys()}
        except sqlite3.Error as exc:
            logger.debug("ContextHarvester.fetch_one failed: %s", exc)
            return None

    @staticmethod
    def fetch_all_for_agent(
        conn: sqlite3.Connection,
        agent_name: str,
    ) -> list[dict[str, str]]:
        """Return all agent_context rows for ``agent_name`` ordered by domain."""
        try:
            cur = conn.execute(
                """
                SELECT agent_name, domain, expertise_summary,
                       strategies_worked, strategies_failed,
                       last_task_id, updated_at
                  FROM agent_context
                 WHERE agent_name = ?
                 ORDER BY domain ASC
                """,
                (agent_name,),
            )
            return [{k: r[k] for k in r.keys()} for r in cur.fetchall()]
        except sqlite3.Error as exc:
            logger.debug("ContextHarvester.fetch_all_for_agent failed: %s", exc)
            return []

    @staticmethod
    def render_prior_context_block(row: dict[str, str]) -> str:
        """Render an agent_context row as a compact "## Prior Context" block.

        The block is bounded by ``_MAX_SUMMARY_CHARS`` so it stays small on
        every dispatch.  Returns an empty string when the row carries no
        useful summary text.
        """
        if not row:
            return ""
        summary = (row.get("expertise_summary") or "").strip()
        if not summary:
            return ""
        domain = row.get("domain") or "general"
        worked = (row.get("strategies_worked") or "").strip()

        lines = [
            "## Prior Context",
            f"You have worked in `{domain}` before. Recent learnings:",
            summary,
        ]
        if worked:
            lines.append(f"Strategies that worked: {worked}")
        block = "\n".join(lines)
        if len(block) > _MAX_SUMMARY_CHARS:
            block = block[: _MAX_SUMMARY_CHARS - 3] + "..."
        return block
