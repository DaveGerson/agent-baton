"""Typed query functions for baton.db — the Python API for execution data.

These functions provide structured, read-only access to execution history,
agent performance, and learning data without requiring raw SQL. All public
methods are read-only; use SqliteStorage for writes.

Usage::

    from agent_baton.core.storage.queries import QueryEngine
    from pathlib import Path

    engine = QueryEngine(Path(".claude/team-context/baton.db"))
    stats = engine.agent_reliability(days=30)
    engine.close()
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from agent_baton.core.storage.connection import ConnectionManager
from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AgentStats:
    """Aggregated performance metrics for one agent."""

    agent_name: str
    total_steps: int = 0
    successes: int = 0
    failures: int = 0
    success_rate: float = 0.0
    total_retries: int = 0
    total_tokens: int = 0
    avg_duration: float = 0.0


@dataclass
class TaskSummary:
    """High-level view of a single task."""

    task_id: str
    task_summary: str
    status: str
    risk_level: str
    agents: list[str] = field(default_factory=list)
    steps_completed: int = 0
    steps_total: int = 0
    started_at: str = ""
    completed_at: str = ""


@dataclass
class KnowledgeGapReport:
    """A knowledge gap that recurs across tasks."""

    description: str
    affected_agent: str
    frequency: int = 1
    tasks: list[str] = field(default_factory=list)


@dataclass
class GateStats:
    """Pass/fail summary for one gate type."""

    gate_type: str
    total: int = 0
    passed: int = 0
    pass_rate: float = 0.0


@dataclass
class CostReport:
    """Token cost summary grouped by task type / sequencing mode."""

    task_type: str
    task_count: int = 0
    total_tokens: int = 0
    avg_tokens: int = 0


# ---------------------------------------------------------------------------
# Write-operation detection
# ---------------------------------------------------------------------------

_WRITE_PATTERN = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|ATTACH|DETACH)",
    re.IGNORECASE,
)


def _is_write_statement(sql: str) -> bool:
    """Return True if *sql* contains a write operation keyword."""
    return bool(_WRITE_PATTERN.match(sql.lstrip()))


# ---------------------------------------------------------------------------
# QueryEngine
# ---------------------------------------------------------------------------


class QueryEngine:
    """Typed, read-only query interface for baton.db.

    Works with both per-project ``baton.db`` and the central ``central.db``
    — just pass the appropriate ``db_path``.  All methods are read-only;
    ``raw_query`` enforces this by rejecting write statements.
    """

    def __init__(self, db_path: Path) -> None:
        self._conn_mgr = ConnectionManager(db_path)
        self._conn_mgr.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)

    @property
    def db_path(self) -> Path:
        return self._conn_mgr.db_path

    def close(self) -> None:
        """Close the SQLite connection for the current thread."""
        self._conn_mgr.close()

    def _conn(self) -> sqlite3.Connection:
        return self._conn_mgr.get_connection()

    # ── helpers ────────────────────────────────────────────────────────────

    def _fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute *sql* and return rows as plain dicts."""
        conn = self._conn()
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def _fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        """Execute *sql* and return a single row as a dict, or None."""
        conn = self._conn()
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    # ── Agent Performance ──────────────────────────────────────────────────

    def agent_reliability(self, days: int = 30) -> list[AgentStats]:
        """Agent success rates and token costs over the last *days* days.

        Reads from ``step_results`` joined to ``executions`` for date
        filtering.  Steps whose ``completed_at`` is blank (pre-v1 data)
        are still included via the fallback JOIN path.
        """
        sql = """
        SELECT
            sr.agent_name,
            COUNT(*)                                      AS total_steps,
            SUM(CASE WHEN sr.status = 'complete' THEN 1 ELSE 0 END) AS successes,
            SUM(CASE WHEN sr.status = 'failed'   THEN 1 ELSE 0 END) AS failures,
            SUM(sr.retries)                               AS total_retries,
            SUM(sr.estimated_tokens)                      AS total_tokens,
            AVG(sr.duration_seconds)                      AS avg_duration
        FROM step_results sr
        JOIN executions e ON e.task_id = sr.task_id
        WHERE
            e.started_at >= datetime('now', ? || ' days')
            OR e.started_at = ''
        GROUP BY sr.agent_name
        ORDER BY total_steps DESC
        """
        rows = self._fetchall(sql, (f"-{days}",))
        result: list[AgentStats] = []
        for r in rows:
            total = r["total_steps"] or 0
            succ = r["successes"] or 0
            result.append(
                AgentStats(
                    agent_name=r["agent_name"],
                    total_steps=total,
                    successes=succ,
                    failures=r["failures"] or 0,
                    success_rate=round(succ / total, 4) if total else 0.0,
                    total_retries=r["total_retries"] or 0,
                    total_tokens=r["total_tokens"] or 0,
                    avg_duration=round(r["avg_duration"] or 0.0, 2),
                )
            )
        return result

    def agent_history(self, agent_name: str, limit: int = 20) -> list[dict]:
        """Recent step results for a specific agent.

        Returns dicts with keys: task_id, step_id, status, outcome, error,
        tokens, duration, retries, completed_at.
        """
        sql = """
        SELECT
            sr.task_id,
            sr.step_id,
            sr.status,
            sr.outcome,
            sr.error,
            sr.estimated_tokens  AS tokens,
            sr.duration_seconds  AS duration,
            sr.retries,
            sr.completed_at
        FROM step_results sr
        WHERE sr.agent_name = ?
        ORDER BY sr.completed_at DESC, sr.rowid DESC
        LIMIT ?
        """
        return self._fetchall(sql, (agent_name, limit))

    # ── Task History ───────────────────────────────────────────────────────

    def task_list(
        self, status: str | None = None, limit: int = 20
    ) -> list[TaskSummary]:
        """List recent tasks with summary info.

        Joins ``executions`` → ``plans`` to retrieve the task summary and
        risk level, and ``step_results`` to count completed steps.
        """
        status_clause = "WHERE e.status = ?" if status else ""
        params: tuple = (status, limit) if status else (limit,)

        sql = f"""
        SELECT
            e.task_id,
            COALESCE(p.task_summary, '')  AS task_summary,
            e.status,
            COALESCE(p.risk_level, '')    AS risk_level,
            e.started_at,
            COALESCE(e.completed_at, '')  AS completed_at,
            COUNT(sr.step_id)             AS steps_completed,
            COUNT(ps.step_id)             AS steps_total
        FROM executions e
        LEFT JOIN plans p ON p.task_id = e.task_id
        LEFT JOIN step_results sr ON sr.task_id = e.task_id
                                  AND sr.status  = 'complete'
        LEFT JOIN plan_steps ps ON ps.task_id = e.task_id
        {status_clause}
        GROUP BY e.task_id
        ORDER BY e.started_at DESC
        LIMIT ?
        """
        rows = self._fetchall(sql, params)

        # Collect distinct agents per task in a second pass (avoids GROUP_CONCAT
        # compatibility concerns).
        task_ids = [r["task_id"] for r in rows]
        agents_by_task: dict[str, list[str]] = {}
        if task_ids:
            placeholders = ",".join("?" * len(task_ids))
            agent_rows = self._fetchall(
                f"""
                SELECT DISTINCT task_id, agent_name
                FROM step_results
                WHERE task_id IN ({placeholders})
                ORDER BY task_id, agent_name
                """,
                tuple(task_ids),
            )
            for ar in agent_rows:
                agents_by_task.setdefault(ar["task_id"], []).append(
                    ar["agent_name"]
                )

        return [
            TaskSummary(
                task_id=r["task_id"],
                task_summary=r["task_summary"],
                status=r["status"],
                risk_level=r["risk_level"],
                agents=agents_by_task.get(r["task_id"], []),
                steps_completed=r["steps_completed"] or 0,
                steps_total=r["steps_total"] or 0,
                started_at=r["started_at"] or "",
                completed_at=r["completed_at"] or "",
            )
            for r in rows
        ]

    def task_detail(self, task_id: str) -> dict | None:
        """Full task detail: plan, steps, step results, and gates.

        Returns a nested dict::

            {
                "task_id": ...,
                "status": ...,
                "plan": { "task_summary": ..., "risk_level": ..., ... },
                "steps": [ { "step_id": ..., "agent_name": ..., ... }, ... ],
                "step_results": [ { "step_id": ..., "status": ..., ... }, ... ],
                "gates": [ { "phase_id": ..., "gate_type": ..., ... }, ... ],
            }

        Returns None if *task_id* does not exist in executions.
        """
        exec_row = self._fetchone(
            "SELECT * FROM executions WHERE task_id = ?", (task_id,)
        )
        if exec_row is None:
            return None

        plan_row = self._fetchone(
            "SELECT * FROM plans WHERE task_id = ?", (task_id,)
        )
        steps = self._fetchall(
            "SELECT * FROM plan_steps WHERE task_id = ? ORDER BY phase_id, rowid",
            (task_id,),
        )
        step_results = self._fetchall(
            "SELECT * FROM step_results WHERE task_id = ? ORDER BY rowid",
            (task_id,),
        )
        gates = self._fetchall(
            "SELECT * FROM gate_results WHERE task_id = ? ORDER BY id",
            (task_id,),
        )

        return {
            "task_id": exec_row["task_id"],
            "status": exec_row["status"],
            "current_phase": exec_row["current_phase"],
            "current_step_index": exec_row["current_step_index"],
            "started_at": exec_row["started_at"],
            "completed_at": exec_row.get("completed_at") or "",
            "plan": plan_row or {},
            "steps": steps,
            "step_results": step_results,
            "gates": gates,
        }

    # ── Knowledge & Learning ───────────────────────────────────────────────

    def knowledge_gaps(
        self, min_frequency: int = 1
    ) -> list[KnowledgeGapReport]:
        """Knowledge gaps that appear across tasks, ranked by frequency.

        Groups ``knowledge_gaps`` rows by (description, affected_agent) and
        only returns groups whose occurrence count >= *min_frequency*.
        """
        sql = """
        SELECT
            kg.description,
            kg.affected_agent,
            COUNT(*)          AS frequency,
            GROUP_CONCAT(kg.task_id, '|') AS task_ids
        FROM knowledge_gaps kg
        GROUP BY kg.description, kg.affected_agent
        HAVING COUNT(*) >= ?
        ORDER BY frequency DESC
        """
        rows = self._fetchall(sql, (min_frequency,))
        return [
            KnowledgeGapReport(
                description=r["description"],
                affected_agent=r["affected_agent"] or "",
                frequency=r["frequency"],
                tasks=(r["task_ids"] or "").split("|") if r["task_ids"] else [],
            )
            for r in rows
        ]

    def roster_recommendations(self) -> list[dict]:
        """Roster recommendations consensus across retrospectives.

        Returns dicts with keys: action, target, count, reason_sample, tasks.
        Groups by (action, target) and aggregates vote counts.
        """
        sql = """
        SELECT
            action,
            target,
            COUNT(*)                       AS count,
            MAX(reason)                    AS reason_sample,
            GROUP_CONCAT(task_id, '|')     AS task_ids
        FROM roster_recommendations
        GROUP BY action, target
        ORDER BY count DESC
        """
        rows = self._fetchall(sql)
        return [
            {
                "action": r["action"],
                "target": r["target"],
                "count": r["count"],
                "reason_sample": r["reason_sample"] or "",
                "tasks": (r["task_ids"] or "").split("|") if r["task_ids"] else [],
            }
            for r in rows
        ]

    def patterns(self) -> list[dict]:
        """Learned patterns with confidence and success rates.

        Returns dicts with keys: pattern_id, task_type, stack,
        recommended_template, recommended_agents, confidence, sample_size,
        success_rate, avg_token_cost, created_at, updated_at.
        """
        sql = """
        SELECT
            pattern_id,
            task_type,
            stack,
            recommended_template,
            recommended_agents,
            confidence,
            sample_size,
            success_rate,
            avg_token_cost,
            created_at,
            updated_at
        FROM learned_patterns
        ORDER BY confidence DESC, success_rate DESC
        """
        return self._fetchall(sql)

    # ── Gates ──────────────────────────────────────────────────────────────

    def gate_stats(self) -> list[GateStats]:
        """Gate pass rates by type.

        Reads all ``gate_results`` rows and groups by ``gate_type``.
        """
        sql = """
        SELECT
            gate_type,
            COUNT(*)                                 AS total,
            SUM(CASE WHEN passed = 1 THEN 1 ELSE 0 END) AS passed_count
        FROM gate_results
        GROUP BY gate_type
        ORDER BY total DESC
        """
        rows = self._fetchall(sql)
        result: list[GateStats] = []
        for r in rows:
            total = r["total"] or 0
            passed = r["passed_count"] or 0
            result.append(
                GateStats(
                    gate_type=r["gate_type"],
                    total=total,
                    passed=passed,
                    pass_rate=round(passed / total, 4) if total else 0.0,
                )
            )
        return result

    # ── Cost Analysis ──────────────────────────────────────────────────────

    def cost_by_task_type(self) -> list[CostReport]:
        """Token costs grouped by task type (sequencing_mode from usage_records).

        Joins ``usage_records`` with ``agent_usage`` to sum tokens per
        sequencing mode, which is the closest proxy for "task type" in the
        schema.
        """
        sql = """
        SELECT
            ur.sequencing_mode              AS task_type,
            COUNT(DISTINCT ur.task_id)      AS task_count,
            SUM(au.estimated_tokens)        AS total_tokens
        FROM usage_records ur
        JOIN agent_usage au ON au.task_id = ur.task_id
        GROUP BY ur.sequencing_mode
        ORDER BY total_tokens DESC
        """
        rows = self._fetchall(sql)
        return [
            CostReport(
                task_type=r["task_type"] or "unknown",
                task_count=r["task_count"] or 0,
                total_tokens=r["total_tokens"] or 0,
                avg_tokens=(
                    (r["total_tokens"] or 0) // r["task_count"]
                    if (r["task_count"] or 0) > 0
                    else 0
                ),
            )
            for r in rows
        ]

    def cost_by_agent(self, days: int = 30) -> list[dict]:
        """Token costs grouped by agent over the last *days* days.

        Returns dicts with keys: agent_name, total_tokens, total_steps,
        avg_tokens_per_step, total_duration.
        """
        sql = """
        SELECT
            sr.agent_name,
            SUM(sr.estimated_tokens)  AS total_tokens,
            COUNT(*)                  AS total_steps,
            AVG(sr.estimated_tokens)  AS avg_tokens_per_step,
            SUM(sr.duration_seconds)  AS total_duration
        FROM step_results sr
        JOIN executions e ON e.task_id = sr.task_id
        WHERE
            e.started_at >= datetime('now', ? || ' days')
            OR e.started_at = ''
        GROUP BY sr.agent_name
        ORDER BY total_tokens DESC
        """
        rows = self._fetchall(sql, (f"-{days}",))
        return [
            {
                "agent_name": r["agent_name"],
                "total_tokens": r["total_tokens"] or 0,
                "total_steps": r["total_steps"] or 0,
                "avg_tokens_per_step": round(r["avg_tokens_per_step"] or 0.0, 1),
                "total_duration": round(r["total_duration"] or 0.0, 2),
            }
            for r in rows
        ]

    # ── Context for Agents ─────────────────────────────────────────────────

    def current_context(self) -> dict:
        """What's currently running: active task, current step, agent.

        Reads the ``active_task`` singleton and joins through to
        ``executions``, ``plans``, and ``step_results`` to provide a
        complete picture of in-flight work.

        Returns a dict with keys: has_active_task, task_id, status,
        task_summary, current_phase, current_step_index, current_agent,
        started_at.  Returns ``has_active_task=False`` if nothing is active.
        """
        active = self._fetchone("SELECT task_id FROM active_task WHERE id = 1")
        if active is None:
            return {"has_active_task": False}

        task_id = active["task_id"]
        exec_row = self._fetchone(
            "SELECT * FROM executions WHERE task_id = ?", (task_id,)
        )
        if exec_row is None:
            return {"has_active_task": False}

        plan_row = self._fetchone(
            "SELECT task_summary, risk_level FROM plans WHERE task_id = ?",
            (task_id,),
        )

        # Determine the current agent from plan_steps using current_phase
        # and current_step_index.
        current_phase = exec_row["current_phase"]
        step_index = exec_row["current_step_index"]
        step_rows = self._fetchall(
            """
            SELECT agent_name, step_id
            FROM plan_steps
            WHERE task_id = ? AND phase_id = ?
            ORDER BY rowid
            """,
            (task_id, current_phase),
        )
        current_agent = ""
        if 0 <= step_index < len(step_rows):
            current_agent = step_rows[step_index]["agent_name"]

        return {
            "has_active_task": True,
            "task_id": task_id,
            "status": exec_row["status"],
            "task_summary": (plan_row or {}).get("task_summary", ""),
            "risk_level": (plan_row or {}).get("risk_level", ""),
            "current_phase": current_phase,
            "current_step_index": step_index,
            "current_agent": current_agent,
            "started_at": exec_row["started_at"],
        }

    def agent_briefing(self, agent_name: str) -> str:
        """Generate a text briefing for an agent about to be dispatched.

        Includes: recent performance summary, known knowledge gaps, and
        any relevant learned patterns.  Returns markdown text suitable for
        inclusion in delegation prompts.
        """
        lines: list[str] = [f"## Agent Briefing: {agent_name}", ""]

        # Performance summary (last 30 days)
        stats_list = self.agent_reliability(days=30)
        stats_map = {s.agent_name: s for s in stats_list}
        stats = stats_map.get(agent_name)
        if stats and stats.total_steps > 0:
            lines += [
                "### Recent Performance (30 days)",
                f"- Steps completed: {stats.total_steps}",
                f"- Success rate: {stats.success_rate:.0%}",
                f"- Total retries: {stats.total_retries}",
                f"- Total tokens used: {stats.total_tokens:,}",
                f"- Avg step duration: {stats.avg_duration:.1f}s",
                "",
            ]
        else:
            lines += [
                "### Recent Performance",
                "- No performance data available for this agent.",
                "",
            ]

        # Knowledge gaps specific to this agent
        all_gaps = self.knowledge_gaps(min_frequency=1)
        agent_gaps = [g for g in all_gaps if g.affected_agent == agent_name]
        if agent_gaps:
            lines += ["### Known Knowledge Gaps", ""]
            for gap in agent_gaps[:5]:
                freq_label = f"(seen {gap.frequency}x)"
                lines.append(f"- {gap.description} {freq_label}")
            lines.append("")

        # Relevant patterns for guidance
        all_patterns = self.patterns()
        if all_patterns:
            lines += ["### Relevant Patterns", ""]
            for pat in all_patterns[:3]:
                agents_str = pat.get("recommended_agents", "[]")
                conf = pat.get("confidence", 0.0)
                lines.append(
                    f"- **{pat['task_type']}** "
                    f"(confidence {conf:.0%}): {agents_str}"
                )
            lines.append("")

        return "\n".join(lines)

    # ── Ad-hoc ─────────────────────────────────────────────────────────────

    def raw_query(self, sql: str, params: tuple = ()) -> list[dict]:
        """Run arbitrary read-only SQL. For advanced users and debugging.

        Raises ``ValueError`` if *sql* contains a write operation keyword
        (INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, REPLACE, TRUNCATE,
        ATTACH, DETACH).  This is a best-effort guard; avoid exposing
        this method to untrusted input.
        """
        if _is_write_statement(sql):
            raise ValueError(
                "raw_query only accepts read-only SQL. "
                "Detected a write operation keyword in the statement."
            )
        return self._fetchall(sql, params)


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def _default_db_path() -> Path:
    """Resolve the default baton.db path from the current working directory."""
    return Path.cwd() / ".claude" / "team-context" / "baton.db"


def open_query_engine(
    db_path: Path | None = None,
    *,
    central: bool = False,
) -> QueryEngine:
    """Open a ``QueryEngine`` for *db_path*.

    Args:
        db_path: Explicit path to ``baton.db`` or ``central.db``.
                 If None, resolves to ``.claude/team-context/baton.db``
                 in the current working directory (or ``~/.baton/central.db``
                 when *central* is True).
        central: When True and *db_path* is not given, opens the central
                 database at ``~/.baton/central.db``.
    """
    if db_path is not None:
        return QueryEngine(db_path)
    if central:
        return QueryEngine(Path.home() / ".baton" / "central.db")
    return QueryEngine(_default_db_path())
