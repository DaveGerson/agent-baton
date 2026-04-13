"""LearningEngine — orchestrates the detect → log → analyze → apply cycle.

The engine is the top-level coordinator for the learning automation system.
It is called automatically at execution completion (``detect``) and
periodically during improvement cycles (``analyze``).  Manual application
is driven by the ``baton learn apply`` CLI command.

All methods are designed to be fault-tolerant: any exception is caught and
logged so that a learning failure never blocks the primary execution flow.
"""
from __future__ import annotations

import logging
from pathlib import Path

from agent_baton.core.learn.ledger import LearningLedger
from agent_baton.core.learn.overrides import LearnedOverrides
from agent_baton.models.learning import LearningEvidence, LearningIssue

_log = logging.getLogger(__name__)

# Auto-apply thresholds by issue type: apply when occurrence_count >= threshold
_AUTO_APPLY_THRESHOLDS: dict[str, int] = {
    "routing_mismatch": 3,
    "agent_degradation": 5,
    "knowledge_gap": 3,
    "gate_mismatch": 2,
    "roster_bloat": 3,
}

# Issue types that require human review — never auto-applied
_INTERVIEW_ONLY_TYPES: frozenset[str] = frozenset({
    "pattern_drift",
    "prompt_evolution",
})

_DEFAULT_DB_NAME = "baton.db"
_DEFAULT_OVERRIDES_NAME = "learned-overrides.json"


class LearningEngine:
    """Orchestrates learning signal detection, analysis, and application.

    Args:
        team_context_root: Path to the ``.claude/team-context/`` directory.
            Defaults to ``.claude/team-context`` relative to cwd.
    """

    def __init__(self, team_context_root: Path | None = None) -> None:
        default = Path(".claude/team-context")
        self._root = (team_context_root or default).resolve()
        self._db_path = self._root / _DEFAULT_DB_NAME
        self._overrides_path = self._root / _DEFAULT_OVERRIDES_NAME

    # ------------------------------------------------------------------
    # Properties (lazy so we don't fail if DB doesn't exist yet)
    # ------------------------------------------------------------------

    @property
    def _ledger(self) -> LearningLedger:
        return LearningLedger(self._db_path)

    @property
    def _overrides(self) -> LearnedOverrides:
        return LearnedOverrides(self._overrides_path)

    # ------------------------------------------------------------------
    # detect()
    # ------------------------------------------------------------------

    def detect(self, state: object) -> list[LearningIssue]:
        """Scan an ExecutionState for learning signals and write to the ledger.

        Called automatically at the end of every execution via
        ``ExecutionEngine.complete()``.  All signal detection is wrapped in
        try/except so a detection failure never blocks the caller.

        Args:
            state: An ``ExecutionState`` instance (typed as object to avoid
                a circular import; duck-typed against known attributes).

        Returns:
            List of LearningIssue records created or updated during this scan.
        """
        recorded: list[LearningIssue] = []

        if not self._db_path.exists():
            _log.debug("LearningEngine.detect: db not found at %s — skipping", self._db_path)
            return recorded

        task_id: str = getattr(state, "task_id", "") or ""
        step_results: list = getattr(state, "step_results", []) or []
        gate_results: list = getattr(state, "gate_results", []) or []
        pending_gaps: list = getattr(state, "pending_gaps", []) or []

        # ---- Routing mismatches (agent flavor vs detected stack) -------
        try:
            plan = getattr(state, "plan", None)
            detected_stack = None
            if plan is not None:
                detected_stack = getattr(plan, "detected_stack", None)
            if detected_stack is not None and step_results:
                language = getattr(detected_stack, "language", None) or ""
                framework = getattr(detected_stack, "framework", None) or ""
                for sr in step_results:
                    agent_name: str = getattr(sr, "agent_name", "") or ""
                    if "--" in agent_name and language:
                        base, flavor = agent_name.split("--", 1)
                        # Flag if flavor language doesn't match detected language
                        if language not in flavor and flavor not in language:
                            stack_key = (
                                f"{language}/{framework}" if framework else language
                            )
                            target = f"{stack_key}:{base}=detected_language_mismatch"
                            ev = LearningEvidence(
                                timestamp=_utcnow(),
                                source_task_id=task_id,
                                detail=(
                                    f"Agent '{agent_name}' has flavor '{flavor}' "
                                    f"but detected stack is '{language}/{framework}'"
                                ),
                                data={
                                    "agent_name": agent_name,
                                    "flavor": flavor,
                                    "detected_stack": f"{language}/{framework}",
                                    "suggested_flavor": language,
                                },
                            )
                            issue = self._ledger.record_issue(
                                issue_type="routing_mismatch",
                                target=target,
                                severity="medium",
                                title=f"Agent flavor mismatch: {agent_name} on {language} stack",
                                evidence=ev,
                            )
                            recorded.append(issue)
        except Exception as exc:
            _log.debug("LearningEngine.detect: routing mismatch scan failed: %s", exc)

        # ---- Agent degradation (failures and high retries) -------------
        try:
            for sr in step_results:
                agent_name = getattr(sr, "agent_name", "") or ""
                status = getattr(sr, "status", "") or ""
                retries = int(getattr(sr, "retries", 0) or 0)

                if status == "failed" or retries >= 2:
                    severity = "high" if status == "failed" else "medium"
                    detail = (
                        f"Agent '{agent_name}' failed with status '{status}'"
                        if status == "failed"
                        else f"Agent '{agent_name}' had {retries} retries"
                    )
                    ev = LearningEvidence(
                        timestamp=_utcnow(),
                        source_task_id=task_id,
                        detail=detail,
                        data={
                            "agent_name": agent_name,
                            "status": status,
                            "retries": retries,
                        },
                    )
                    issue = self._ledger.record_issue(
                        issue_type="agent_degradation",
                        target=agent_name,
                        severity=severity,
                        title=f"Agent degradation: {agent_name}",
                        evidence=ev,
                    )
                    recorded.append(issue)
        except Exception as exc:
            _log.debug("LearningEngine.detect: agent degradation scan failed: %s", exc)

        # ---- Gate mismatches (gate language vs detected stack) ---------
        try:
            plan = getattr(state, "plan", None)
            detected_stack = None
            if plan is not None:
                detected_stack = getattr(plan, "detected_stack", None)
            if detected_stack is not None and gate_results:
                language = getattr(detected_stack, "language", None) or ""
                for gr in gate_results:
                    gate_command: str = getattr(gr, "command", "") or ""
                    gate_type: str = getattr(gr, "gate_type", "") or ""
                    passed: bool = bool(getattr(gr, "passed", True))
                    if not passed and gate_command and language:
                        # Heuristic: if pytest is used but stack is TypeScript
                        if language in ("typescript", "javascript") and "pytest" in gate_command:
                            ev = LearningEvidence(
                                timestamp=_utcnow(),
                                source_task_id=task_id,
                                detail=(
                                    f"Gate '{gate_type}' used '{gate_command}' "
                                    f"but detected stack is '{language}'"
                                ),
                                data={
                                    "gate_type": gate_type,
                                    "gate_command": gate_command,
                                    "language": language,
                                    "suggested_command": "npm test",
                                },
                            )
                            target = f"{language}:{gate_type}"
                            issue = self._ledger.record_issue(
                                issue_type="gate_mismatch",
                                target=target,
                                severity="medium",
                                title=f"Gate command mismatch for {language}: {gate_command}",
                                evidence=ev,
                            )
                            recorded.append(issue)
        except Exception as exc:
            _log.debug("LearningEngine.detect: gate mismatch scan failed: %s", exc)

        # ---- Knowledge gaps from pending_gaps -------------------------
        try:
            for gap in pending_gaps:
                if isinstance(gap, dict):
                    description = gap.get("description", "") or str(gap)
                    agent_name = gap.get("agent_name", "") or gap.get("affected_agent", "")
                    gap_type = gap.get("gap_type", "factual")
                elif hasattr(gap, "description"):
                    description = gap.description
                    agent_name = getattr(gap, "agent_name", "") or ""
                    gap_type = getattr(gap, "gap_type", "factual")
                else:
                    description = str(gap)
                    agent_name = ""
                    gap_type = "factual"

                if not description:
                    continue

                target = f"{agent_name}:{description[:60]}" if agent_name else description[:60]
                ev = LearningEvidence(
                    timestamp=_utcnow(),
                    source_task_id=task_id,
                    detail=f"Knowledge gap: {description}",
                    data={
                        "description": description,
                        "agent_name": agent_name,
                        "gap_type": gap_type,
                    },
                )
                issue = self._ledger.record_issue(
                    issue_type="knowledge_gap",
                    target=target,
                    severity="low",
                    title=f"Knowledge gap: {description[:80]}",
                    evidence=ev,
                )
                recorded.append(issue)
        except Exception as exc:
            _log.debug("LearningEngine.detect: knowledge gap scan failed: %s", exc)

        # ---- Classifier fallback (roster_bloat signal) ----------------
        try:
            plan = getattr(state, "plan", None)
            if plan is not None:
                classification_source = getattr(plan, "classification_source", "") or ""
                if classification_source == "keyword-fallback":
                    task_type = getattr(plan, "task_type", "") or "unknown"
                    ev = LearningEvidence(
                        timestamp=_utcnow(),
                        source_task_id=task_id,
                        detail=(
                            f"Classifier fell back to keyword-based routing "
                            f"for task_type='{task_type}'"
                        ),
                        data={
                            "classification_source": classification_source,
                            "task_type": task_type,
                        },
                    )
                    issue = self._ledger.record_issue(
                        issue_type="roster_bloat",
                        target=f"keyword-fallback:{task_type}",
                        severity="low",
                        title=f"Classifier fallback for task type: {task_type}",
                        evidence=ev,
                    )
                    recorded.append(issue)
        except Exception as exc:
            _log.debug("LearningEngine.detect: roster bloat scan failed: %s", exc)

        # ---- Auto-apply if any recorded issue crossed threshold -------
        seen_ids: set[str] = set()
        for issue in recorded:
            if issue.issue_id in seen_ids:
                continue
            seen_ids.add(issue.issue_id)
            threshold = _AUTO_APPLY_THRESHOLDS.get(issue.issue_type)
            if (
                threshold is not None
                and issue.issue_type not in _INTERVIEW_ONLY_TYPES
                and issue.occurrence_count >= threshold
                and issue.status == "open"
            ):
                try:
                    self.apply(issue.issue_id, resolution_type="auto")
                    _log.info(
                        "Auto-applied fix for issue %s (%s, %d occurrences)",
                        issue.issue_id,
                        issue.issue_type,
                        issue.occurrence_count,
                    )
                except Exception as exc:
                    _log.debug(
                        "LearningEngine: auto-apply failed for %s: %s",
                        issue.issue_id, exc,
                    )

        return recorded

    # ------------------------------------------------------------------
    # analyze()
    # ------------------------------------------------------------------

    def analyze(self) -> list[LearningIssue]:
        """Read open issues, compute confidence, and flag candidates for apply.

        Returns issues whose occurrence count equals or exceeds the auto-apply
        threshold and updates their status to ``"proposed"``.

        Returns:
            List of open LearningIssue records with computed confidence attached
            (stored transiently in ``proposed_fix`` field as a confidence note).
        """
        if not self._db_path.exists():
            return []

        ledger = self._ledger
        open_issues = ledger.get_open_issues()
        candidates: list[LearningIssue] = []

        for issue in open_issues:
            threshold = _AUTO_APPLY_THRESHOLDS.get(issue.issue_type)
            if threshold is None:
                # Interview-only types — include in list but don't auto-propose
                candidates.append(issue)
                continue

            confidence = min(1.0, issue.occurrence_count / threshold)
            if issue.occurrence_count >= threshold and issue.status == "open":
                # Mark as proposed so CLI can highlight it
                ledger.update_status(
                    issue.issue_id,
                    status="proposed",
                    proposed_fix=(
                        f"Auto-resolve candidate (confidence={confidence:.0%}, "
                        f"occurrences={issue.occurrence_count}/{threshold})"
                    ),
                )
                # Reload to get updated state
                updated = ledger.get_issue(issue.issue_id)
                if updated is not None:
                    candidates.append(updated)
            else:
                candidates.append(issue)

        return candidates

    # ------------------------------------------------------------------
    # apply()
    # ------------------------------------------------------------------

    def apply(
        self, issue_id: str, resolution_type: str = "auto"
    ) -> str:
        """Execute the type-specific fix for an issue.

        Dispatches to the appropriate resolver in ``core.learn.resolvers``,
        then marks the issue as ``"applied"`` in the ledger.

        Args:
            issue_id: The issue to resolve.
            resolution_type: ``"auto"``, ``"human"``, or ``"interview"``.

        Returns:
            Human-readable description of the resolution applied.

        Raises:
            ValueError: If no issue with ``issue_id`` is found.
        """
        from agent_baton.core.learn import resolvers  # avoid circular imports at module level

        ledger = self._ledger
        issue = ledger.get_issue(issue_id)
        if issue is None:
            raise ValueError(f"Issue not found: {issue_id}")

        overrides = self._overrides

        if issue.issue_type in _INTERVIEW_ONLY_TYPES:
            return (
                f"Issue type '{issue.issue_type}' requires human review via "
                "'baton learn interview'. Auto-apply is not supported."
            )

        resolver_map = {
            "routing_mismatch": resolvers.resolve_routing_mismatch,
            "agent_degradation": resolvers.resolve_agent_degradation,
            "gate_mismatch": resolvers.resolve_gate_mismatch,
            "roster_bloat": resolvers.resolve_roster_bloat,
        }

        if issue.issue_type == "knowledge_gap":
            resolution = resolvers.resolve_knowledge_gap(
                issue, overrides, knowledge_root=self._root.parent / "knowledge"
            )
        elif issue.issue_type in resolver_map:
            resolution = resolver_map[issue.issue_type](issue, overrides)
        else:
            resolution = f"No resolver defined for issue type '{issue.issue_type}'."

        ledger.update_status(
            issue_id,
            status="applied",
            resolution=resolution,
            resolution_type=resolution_type,
        )
        _log.info("Applied fix for issue %s: %s", issue_id, resolution)
        return resolution


def _utcnow() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
