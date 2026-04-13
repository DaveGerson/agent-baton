"""MaintainerSpawner -- spawn the system-maintainer agent after an improvement cycle.

After :meth:`ImprovementLoop.run_cycle` produces a report, the spawner
decides whether to invoke the ``system-maintainer`` agent.  The agent reads
escalated recommendations and auto-applied changes, then conservatively mutates
``learned-overrides.json``.

Spawn criteria (both must hold):

* The report is not skipped (i.e., a real cycle ran).
* The report has at least one escalated recommendation OR at least one
  auto-applied change.

The spawn is always **best-effort** — all errors are caught and logged.
A failure here must never block the caller's execution completion flow.

Decision log format (one JSON object per line)::

    {
      "timestamp": "...",
      "rec_id": "...",
      "action": "applied|rejected|deferred",
      "reasoning": "...",
      "changes": {...}
    }

The log is appended to
``.claude/team-context/improvements/maintainer-decisions.jsonl``.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_baton.models.improvement import ImprovementReport

_log = logging.getLogger(__name__)

_DEFAULT_IMPROVEMENTS_DIR = Path(".claude/team-context/improvements")
_AGENT_NAME = "system-maintainer"
_MODEL = "sonnet"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _build_prompt(
    report: ImprovementReport,
    report_path: Path,
    overrides_path: Path,
    recent_rollback_count: int,
) -> str:
    """Construct the delegation prompt for the system-maintainer agent.

    Embeds the full report JSON inline so the agent has all context without
    needing to locate or parse multiple files.  The overrides path is passed
    explicitly so the agent writes to the correct location.

    Args:
        report: The improvement report from the completed cycle.
        report_path: Absolute path to the persisted report JSON file.
        overrides_path: Absolute path to learned-overrides.json.
        recent_rollback_count: Number of rollbacks in the last 7 days,
            passed as context for the agent's risk assessment.

    Returns:
        Complete delegation prompt string.
    """
    # Serialize the escalated recommendations by extracting them from the report
    escalated_ids: set[str] = set(report.escalated)
    auto_applied_ids: set[str] = set(report.auto_applied)

    escalated_recs = [
        r for r in report.recommendations if r.get("rec_id") in escalated_ids
    ]
    auto_applied_recs = [
        r for r in report.recommendations if r.get("rec_id") in auto_applied_ids
    ]

    prompt_parts = [
        "You are the system-maintainer agent. An improvement cycle has completed.",
        "",
        f"## Context",
        f"- Report ID: {report.report_id}",
        f"- Report path: {report_path}",
        f"- Overrides path: {overrides_path}",
        f"- Recent rollbacks (last 7 days): {recent_rollback_count}",
        f"- Escalated recommendations: {len(escalated_recs)}",
        f"- Auto-applied changes (to validate): {len(auto_applied_recs)}",
        "",
        "## Decision Log",
        f"Append all decisions to: {overrides_path.parent / 'maintainer-decisions.jsonl'}",
        "",
        "## Escalated Recommendations",
        "These were NOT auto-applied by the improvement loop and require your assessment:",
        "",
    ]

    if escalated_recs:
        prompt_parts.append("```json")
        prompt_parts.append(
            json.dumps(escalated_recs, indent=2, ensure_ascii=False)
        )
        prompt_parts.append("```")
    else:
        prompt_parts.append("(none)")

    prompt_parts += [
        "",
        "## Auto-Applied Changes (Validate)",
        "These were applied automatically. Review for correctness and flag any concerns:",
        "",
    ]

    if auto_applied_recs:
        prompt_parts.append("```json")
        prompt_parts.append(
            json.dumps(auto_applied_recs, indent=2, ensure_ascii=False)
        )
        prompt_parts.append("```")
    else:
        prompt_parts.append("(none)")

    prompt_parts += [
        "",
        "## Instructions",
        "1. Read the current learned-overrides.json (the path is given above).",
        "2. For each escalated recommendation, apply your decision process.",
        "3. For each auto-applied change, confirm it looks correct or flag it as",
        "   deferred if something looks wrong.",
        "4. Write every decision to the maintainer-decisions.jsonl log.",
        "5. Apply approved changes to learned-overrides.json.",
        "6. Return a structured summary following your output format.",
        "",
        "IMPORTANT: Never modify source code. Only mutate learned-overrides.json.",
        "Never apply prompt evolution changes (category=agent_prompt).",
    ]

    return "\n".join(prompt_parts)


class MaintainerSpawner:
    """Spawn the system-maintainer agent after an improvement cycle.

    Encapsulates the logic for deciding when to spawn, building the delegation
    prompt, launching the agent asynchronously, and logging the result.

    All public methods are sync entry points that run the async logic via
    :func:`asyncio.run` so callers do not need an event loop.  The underlying
    async method can be awaited directly in async contexts.

    Args:
        improvements_dir: Directory where reports and the decision log live.
            Defaults to ``.claude/team-context/improvements``.
        overrides_path: Path to ``learned-overrides.json``.  Defaults to
            ``.claude/team-context/learned-overrides.json``.
        launcher: Optional pre-configured launcher.  When ``None``,
            :class:`~agent_baton.core.runtime.claude_launcher.ClaudeCodeLauncher`
            is instantiated lazily on first use.
    """

    def __init__(
        self,
        improvements_dir: Path | None = None,
        overrides_path: Path | None = None,
        launcher: Any | None = None,
    ) -> None:
        self._dir = (improvements_dir or _DEFAULT_IMPROVEMENTS_DIR).resolve()
        self._overrides_path = (
            overrides_path
            or Path(".claude/team-context/learned-overrides.json")
        ).resolve()
        self._launcher = launcher  # Injected for tests; lazily loaded otherwise

    # ------------------------------------------------------------------
    # Public sync entry point
    # ------------------------------------------------------------------

    def maybe_spawn(
        self,
        report: ImprovementReport,
        recent_rollback_count: int = 0,
        report_path: Path | None = None,
    ) -> None:
        """Synchronously spawn the system-maintainer if the report warrants it.

        Wraps :meth:`maybe_spawn_async` in :func:`asyncio.run` so the caller
        does not need an event loop.  Errors are caught and logged; they never
        propagate to the caller.

        Args:
            report: The completed improvement report.
            recent_rollback_count: Number of rollbacks in the last 7 days,
                used as context in the agent prompt.
            report_path: Absolute path to the saved report JSON.  When
                ``None``, the path is derived from the report ID.
        """
        try:
            asyncio.run(
                self.maybe_spawn_async(
                    report=report,
                    recent_rollback_count=recent_rollback_count,
                    report_path=report_path,
                )
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "MaintainerSpawner.maybe_spawn failed (non-fatal): %s", exc
            )

    # ------------------------------------------------------------------
    # Async implementation
    # ------------------------------------------------------------------

    async def maybe_spawn_async(
        self,
        report: ImprovementReport,
        recent_rollback_count: int = 0,
        report_path: Path | None = None,
    ) -> None:
        """Async implementation of the spawn logic.

        Checks whether spawning is warranted, builds the prompt, launches the
        agent, and logs the result.  All errors are caught; a failure here
        must never block the caller.

        Args:
            report: The completed improvement report.
            recent_rollback_count: Number of rollbacks in the last 7 days.
            report_path: Path to the persisted report file.
        """
        if not self._should_spawn(report):
            _log.debug(
                "MaintainerSpawner: skipping spawn for report %s "
                "(no escalations or auto-applied changes)",
                report.report_id,
            )
            return

        resolved_report_path = report_path or (
            self._dir / "reports" / f"{report.report_id}.json"
        )

        prompt = _build_prompt(
            report=report,
            report_path=resolved_report_path,
            overrides_path=self._overrides_path,
            recent_rollback_count=recent_rollback_count,
        )

        launcher = self._get_launcher()
        if launcher is None:
            _log.warning(
                "MaintainerSpawner: no launcher available, skipping agent spawn"
            )
            return

        _log.info(
            "MaintainerSpawner: spawning %s for report %s "
            "(%d escalated, %d auto-applied)",
            _AGENT_NAME,
            report.report_id,
            len(report.escalated),
            len(report.auto_applied),
        )

        try:
            result = await launcher.launch(
                agent_name=_AGENT_NAME,
                model=_MODEL,
                prompt=prompt,
                step_id=f"maintainer-{report.report_id}",
            )

            _log.info(
                "MaintainerSpawner: agent finished with status=%s for report %s",
                result.status,
                report.report_id,
            )

            if result.status == "failed":
                _log.warning(
                    "MaintainerSpawner: agent reported failure: %s",
                    result.error,
                )

        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "MaintainerSpawner: launcher.launch raised (non-fatal): %s", exc
            )

    # ------------------------------------------------------------------
    # Decision log
    # ------------------------------------------------------------------

    def log_decision(
        self,
        rec_id: str,
        action: str,
        reasoning: str,
        changes: dict | None = None,
        category: str = "",
        target: str = "",
    ) -> None:
        """Append a single decision record to the JSONL log.

        Intended for use by the agent itself (via a tool call) or by tests
        that want to verify the log format.  The log is append-only; existing
        entries are never modified.

        Args:
            rec_id: The recommendation ID this decision concerns.
            action: One of ``"applied"``, ``"rejected"``, or ``"deferred"``.
            reasoning: Human-readable explanation of the decision.
            changes: The exact dict diff applied to learned-overrides.json.
                Pass an empty dict ``{}`` for rejected/deferred entries.
            category: Recommendation category (informational).
            target: Recommendation target (informational).
        """
        entry: dict = {
            "timestamp": _utcnow(),
            "rec_id": rec_id,
            "category": category,
            "target": target,
            "action": action,
            "reasoning": reasoning,
            "changes": changes or {},
        }
        self._dir.mkdir(parents=True, exist_ok=True)
        log_path = self._dir / "maintainer-decisions.jsonl"
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def load_decisions(self) -> list[dict]:
        """Load all decision log entries.

        Returns:
            List of decision dicts in chronological order.  Returns an empty
            list if the log does not exist yet.
        """
        log_path = self._dir / "maintainer-decisions.jsonl"
        if not log_path.exists():
            return []
        entries: list[dict] = []
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _should_spawn(self, report: ImprovementReport) -> bool:
        """Return True if the report warrants spawning the maintainer.

        Spawn conditions:
        - Report is not skipped (a real cycle completed).
        - At least one escalated recommendation OR at least one auto-applied
          change is present.
        """
        if report.skipped:
            return False
        return bool(report.escalated) or bool(report.auto_applied)

    def _get_launcher(self) -> Any | None:
        """Return the launcher, lazily constructing ClaudeCodeLauncher if needed.

        Returns None if ClaudeCodeLauncher cannot be instantiated (e.g., the
        ``claude`` binary is not on PATH).  The caller handles the None case
        by skipping the spawn gracefully.
        """
        if self._launcher is not None:
            return self._launcher
        try:
            from agent_baton.core.runtime.claude_launcher import ClaudeCodeLauncher
            self._launcher = ClaudeCodeLauncher()
        except (RuntimeError, OSError) as exc:
            _log.warning(
                "MaintainerSpawner: cannot construct ClaudeCodeLauncher "
                "(claude binary missing?): %s",
                exc,
            )
            return None
        return self._launcher
