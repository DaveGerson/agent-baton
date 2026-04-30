"""Historical failure analysis for ``baton lookback``.

:class:`LookbackAnalyzer` reads execution state, beads, traces, and
retrospectives to classify why plans failed and recommend fixes.  All
data accesses are wrapped in try/except so that partially-populated
stores never crash the tool.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_baton.models.lookback import (
    FailureClassification,
    LookbackRecommendation,
    LookbackReport,
    RecurringPattern,
)

# Environment-failure keywords (checked in step error text).
_ENV_KEYWORDS: tuple[str, ...] = (
    "command not found",
    "ModuleNotFoundError",
    "ENOENT",
    "No such file or directory",
    "rate limit",
    "timeout",
    "connection refused",
    "Connection reset",
)

# Context-exhaustion keywords (checked in bead content for warning beads).
_CTX_KEYWORDS: tuple[str, ...] = ("compaction", "context window", "context limit")


class LookbackAnalyzer:
    """Forensic analyzer for past executions.

    Parameters
    ----------
    storage:
        Any object that satisfies the ``StorageBackend`` protocol from
        ``agent_baton.core.storage.protocol``.
    bead_store:
        Optional :class:`~agent_baton.core.engine.bead_store.BeadStore`
        instance.  When provided, beads are included in classification.
    team_context_root:
        Root directory that holds ``learned-overrides.json`` and
        other project-level files.  Defaults to
        ``.claude/team-context`` relative to cwd.
    """

    def __init__(
        self,
        storage: Any,
        bead_store: Any | None = None,
        team_context_root: Path | None = None,
    ) -> None:
        self._storage = storage
        self._bead_store = bead_store
        self._ctx_root = team_context_root or Path(".claude/team-context").resolve()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze_task(self, task_id: str) -> LookbackReport:
        """Full forensic analysis of a single task."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        state = self._load_execution(task_id)
        plan = self._load_plan(task_id)
        retro = self._load_retro(task_id)
        beads = self._load_beads(task_id)

        if state is None and plan is None:
            return LookbackReport(
                task_id=task_id,
                query_range=None,
                executions_analyzed=1,
                failures_found=0,
                generated_at=now,
            )

        classifications = self._classify_failures(
            task_id=task_id,
            state=state,
            plan=plan,
            retro=retro,
            beads=beads,
        )

        token_waste = self._estimate_token_waste(state)
        recommendations = self._build_recommendations(
            classifications=classifications,
            task_ids=[task_id],
            plan=plan,
            retro=retro,
        )

        failures_found = 1 if classifications else 0

        return LookbackReport(
            task_id=task_id,
            query_range=None,
            executions_analyzed=1,
            failures_found=failures_found,
            classifications=classifications,
            recurring_patterns=[],
            recommendations=recommendations,
            token_waste_estimate=token_waste,
            generated_at=now,
        )

    def analyze_range(
        self,
        since: str | None = None,
        until: str | None = None,
        status_filter: str = "failed",
    ) -> LookbackReport:
        """Aggregate analysis across executions in a time range.

        Parameters
        ----------
        since:
            ISO 8601 lower bound (inclusive).  ``None`` means no lower bound.
        until:
            ISO 8601 upper bound (inclusive).  ``None`` means no upper bound.
        status_filter:
            Execution status to select.  Defaults to ``"failed"``.
            Pass ``"all"`` to include every status.
        """
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        task_ids = self._list_executions()
        selected: list[str] = []
        for tid in task_ids:
            state = self._load_execution(tid)
            if state is None:
                continue
            if status_filter != "all":
                if getattr(state, "status", "") != status_filter:
                    continue
            ts = getattr(state, "started_at", "") or ""
            if since and ts and ts < since:
                continue
            if until and ts and ts > until:
                continue
            selected.append(tid)

        all_classifications: list[FailureClassification] = []
        total_token_waste = 0

        for tid in selected:
            state = self._load_execution(tid)
            plan = self._load_plan(tid)
            retro = self._load_retro(tid)
            beads = self._load_beads(tid)
            cls_list = self._classify_failures(
                task_id=tid,
                state=state,
                plan=plan,
                retro=retro,
                beads=beads,
            )
            all_classifications.extend(cls_list)
            total_token_waste += self._estimate_token_waste(state)

        failures_found = len({c for c in selected for _ in [1]}) if all_classifications else 0

        # Recount: task IDs that contributed at least one classification.
        task_ids_with_failure = set()
        for c in all_classifications:
            # We cannot easily recover task_id from classification alone, so
            # we count tasks that had any classifications.
            task_ids_with_failure.add(c.subcategory)  # placeholder — overridden below

        # Better: count selected tasks that produced at least one classification.
        # We'll re-classify per task to get the count right.
        tasks_with_failures: list[str] = []
        tasks_per_id: dict[str, list[FailureClassification]] = {}
        for tid in selected:
            state = self._load_execution(tid)
            plan = self._load_plan(tid)
            retro = self._load_retro(tid)
            beads = self._load_beads(tid)
            cls_list = self._classify_failures(
                task_id=tid,
                state=state,
                plan=plan,
                retro=retro,
                beads=beads,
            )
            tasks_per_id[tid] = cls_list
            if cls_list:
                tasks_with_failures.append(tid)

        # Flatten for the report — merge duplicate classifications from
        # the two passes above.  Start fresh to avoid duplicates.
        all_classifications = []
        for cls_list in tasks_per_id.values():
            all_classifications.extend(cls_list)

        patterns = self._detect_recurring_patterns_from(
            tasks_per_id=tasks_per_id,
            total_executions=len(selected),
        )

        recommendations = self._build_range_recommendations(
            patterns=patterns,
            task_ids=tasks_with_failures,
        )

        return LookbackReport(
            task_id=None,
            query_range=(since or "", until or "") if since or until else None,
            executions_analyzed=len(selected),
            failures_found=len(tasks_with_failures),
            classifications=all_classifications,
            recurring_patterns=patterns,
            recommendations=recommendations,
            token_waste_estimate=total_token_waste,
            generated_at=now,
        )

    def detect_recurring_patterns(
        self,
        min_occurrences: int = 2,
        min_failure_rate: float = 0.3,
    ) -> list[RecurringPattern]:
        """Cross-task pattern detection across all stored executions."""
        task_ids = self._list_executions()
        tasks_per_id: dict[str, list[FailureClassification]] = {}
        for tid in task_ids:
            state = self._load_execution(tid)
            plan = self._load_plan(tid)
            retro = self._load_retro(tid)
            beads = self._load_beads(tid)
            cls_list = self._classify_failures(
                task_id=tid,
                state=state,
                plan=plan,
                retro=retro,
                beads=beads,
            )
            tasks_per_id[tid] = cls_list

        return self._detect_recurring_patterns_from(
            tasks_per_id=tasks_per_id,
            total_executions=len(task_ids),
            min_occurrences=min_occurrences,
            min_failure_rate=min_failure_rate,
        )

    def to_markdown(self, report: LookbackReport) -> str:
        """Render a :class:`LookbackReport` as markdown."""
        lines: list[str] = []

        if report.task_id:
            lines.append(f"# Lookback Report: {report.task_id}")
        else:
            qr = report.query_range
            if qr:
                lines.append(f"# Lookback Report: {qr[0] or 'all'} — {qr[1] or 'now'}")
            else:
                lines.append("# Lookback Report")
        lines.append("")
        lines.append(f"Generated at: {report.generated_at}")
        lines.append("")
        lines.append("## Summary")
        lines.append("")
        lines.append(f"- Executions analyzed: {report.executions_analyzed}")
        lines.append(f"- Failures found: {report.failures_found}")
        lines.append(f"- Token waste estimate: {report.token_waste_estimate:,}")
        lines.append("")

        if report.classifications:
            lines.append("## Failure Classifications")
            lines.append("")
            for fc in report.classifications:
                conf_pct = int(fc.confidence * 100)
                lines.append(f"### {fc.category} — {fc.subcategory} ({conf_pct}% confidence)")
                if fc.affected_steps:
                    lines.append(f"- Steps: {', '.join(fc.affected_steps)}")
                if fc.affected_agents:
                    lines.append(f"- Agents: {', '.join(fc.affected_agents)}")
                if fc.evidence:
                    lines.append("- Evidence:")
                    for ev in fc.evidence:
                        lines.append(f"  - {ev}")
                if fc.recommended_action:
                    lines.append(f"- Recommended: {fc.recommended_action}")
                lines.append("")

        if report.recurring_patterns:
            lines.append("## Recurring Patterns")
            lines.append("")
            for pat in report.recurring_patterns:
                rate_pct = int(pat.failure_rate * 100)
                lines.append(
                    f"### {pat.pattern_type}: {pat.description}"
                )
                lines.append(
                    f"- Frequency: {pat.frequency} tasks "
                    f"({rate_pct}% failure rate, {pat.total_occurrences} total occurrences)"
                )
                if pat.affected_agents:
                    lines.append(f"- Agents: {', '.join(pat.affected_agents)}")
                if pat.affected_task_types:
                    lines.append(f"- Task types: {', '.join(pat.affected_task_types)}")
                if pat.evidence_task_ids:
                    lines.append(f"- Evidence task IDs: {', '.join(pat.evidence_task_ids[:5])}")
                if pat.recommended_action:
                    lines.append(f"- Recommended: {pat.recommended_action}")
                lines.append("")

        if report.recommendations:
            lines.append("## Recommendations")
            lines.append("")
            for rec in report.recommendations:
                auto_tag = " [auto-applicable]" if rec.auto_applicable else ""
                conf_pct = int(rec.confidence * 100)
                lines.append(
                    f"### {rec.action.upper()}: {rec.target}{auto_tag} ({conf_pct}%)"
                )
                lines.append(f"{rec.detail}")
                if rec.evidence_task_ids:
                    lines.append(f"Evidence: {', '.join(rec.evidence_task_ids[:3])}")
                lines.append("")

        if not report.classifications and not report.recurring_patterns:
            lines.append("No failures classified.")
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private: data loaders (all wrapped in try/except)
    # ------------------------------------------------------------------

    def _load_execution(self, task_id: str) -> Any | None:
        try:
            return self._storage.load_execution(task_id)
        except Exception:
            return None

    def _load_plan(self, task_id: str) -> Any | None:
        try:
            return self._storage.load_plan(task_id)
        except Exception:
            return None

    def _load_retro(self, task_id: str) -> Any | None:
        try:
            return self._storage.load_retrospective(task_id)
        except Exception:
            return None

    def _load_beads(self, task_id: str) -> list[Any]:
        if self._bead_store is None:
            return []
        try:
            return self._bead_store.query(task_id=task_id, limit=500)
        except Exception:
            return []

    def _list_executions(self) -> list[str]:
        try:
            return list(self._storage.list_executions())
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Private: failure classifiers
    # ------------------------------------------------------------------

    def _classify_failures(
        self,
        task_id: str,
        state: Any | None,
        plan: Any | None,
        retro: Any | None,
        beads: list[Any],
    ) -> list[FailureClassification]:
        """Run all classifiers and return deduplicated results."""
        results: list[FailureClassification] = []

        step_results = getattr(state, "step_results", []) or []
        gate_results = getattr(state, "gate_results", []) or []
        amendments = getattr(state, "amendments", []) or []

        results.extend(self._detect_gate_failures(gate_results))
        results.extend(self._detect_agent_errors(step_results))
        results.extend(self._detect_env_failures(step_results))
        results.extend(self._detect_scope_overrun(amendments, beads))
        results.extend(self._detect_plan_mismatch(plan, step_results, retro))
        results.extend(self._detect_context_exhaustion(beads))

        return results

    def _detect_gate_failures(
        self, gate_results: list[Any]
    ) -> list[FailureClassification]:
        out: list[FailureClassification] = []
        for gr in gate_results:
            passed = getattr(gr, "passed", True)
            status = getattr(gr, "status", "")
            failed = (not passed) or (status == "failed")
            if not failed:
                continue
            gate_type = getattr(gr, "gate_type", "unknown")
            phase_id = getattr(gr, "phase_id", "?")
            evidence_parts = [f"Gate '{gate_type}' failed at phase {phase_id}"]
            gate_output = getattr(gr, "output", "") or ""
            if gate_output:
                evidence_parts.append(f"Output: {gate_output[:200]}")
            out.append(
                FailureClassification(
                    category="GATE_FAIL",
                    subcategory=f"GATE_FAIL_{gate_type.upper()}",
                    confidence=0.9,
                    evidence=evidence_parts,
                    recommended_action=(
                        f"Review gate '{gate_type}' output and fix the "
                        "underlying test or build issue before retrying."
                    ),
                )
            )
        return out

    def _detect_agent_errors(
        self, step_results: list[Any]
    ) -> list[FailureClassification]:
        out: list[FailureClassification] = []
        for sr in step_results:
            status = getattr(sr, "status", "")
            error = getattr(sr, "error", "") or ""
            if status != "failed" or not error:
                continue
            step_id = getattr(sr, "step_id", "?")
            agent_name = getattr(sr, "agent_name", "unknown")
            # If the error looks like an environment issue, skip — will be
            # caught by _detect_env_failures.
            if any(kw.lower() in error.lower() for kw in _ENV_KEYWORDS):
                continue
            out.append(
                FailureClassification(
                    category="AGENT_ERROR",
                    subcategory="AGENT_ERROR_TRANSIENT",
                    confidence=0.8,
                    affected_steps=[step_id],
                    affected_agents=[agent_name],
                    evidence=[f"Step {step_id} ({agent_name}) failed: {error[:200]}"],
                    recommended_action=(
                        f"Investigate the error for agent '{agent_name}' "
                        "and consider adding a retry or a more specific task description."
                    ),
                )
            )
        return out

    def _detect_env_failures(
        self, step_results: list[Any]
    ) -> list[FailureClassification]:
        out: list[FailureClassification] = []
        for sr in step_results:
            status = getattr(sr, "status", "")
            error = getattr(sr, "error", "") or ""
            outcome = getattr(sr, "outcome", "") or ""
            combined = f"{error} {outcome}"
            matched_kw = next(
                (kw for kw in _ENV_KEYWORDS if kw.lower() in combined.lower()), None
            )
            if matched_kw is None:
                continue
            if status not in ("failed", "complete"):
                # Only flag if there was a problem signal
                pass
            step_id = getattr(sr, "step_id", "?")
            agent_name = getattr(sr, "agent_name", "unknown")
            out.append(
                FailureClassification(
                    category="ENV_FAILURE",
                    subcategory=f"ENV_FAILURE_{matched_kw.upper().replace(' ', '_')}",
                    confidence=0.85,
                    affected_steps=[step_id],
                    affected_agents=[agent_name],
                    evidence=[
                        f"Step {step_id}: environment keyword '{matched_kw}' found "
                        f"in error/outcome"
                    ],
                    recommended_action=(
                        "Verify that required tools, packages, or network endpoints "
                        "are available in the execution environment.  Consider adding "
                        "a setup gate or knowledge pack with env prerequisites."
                    ),
                )
            )
        return out

    def _detect_scope_overrun(
        self,
        amendments: list[Any],
        beads: list[Any],
    ) -> list[FailureClassification]:
        out: list[FailureClassification] = []
        evidence: list[str] = []

        if amendments:
            evidence.append(f"{len(amendments)} plan amendment(s) applied during execution")

        for bead in beads:
            content = getattr(bead, "content", "") or ""
            if any(kw in content.lower() for kw in ("scope", "drift")):
                bead_id = getattr(bead, "bead_id", "?")
                evidence.append(f"Bead {bead_id}: scope/drift signal in content")

        if not evidence:
            return out

        out.append(
            FailureClassification(
                category="SCOPE_OVERRUN",
                subcategory="SCOPE_OVERRUN_AMENDMENTS",
                confidence=0.7,
                evidence=evidence,
                recommended_action=(
                    "Break large tasks into smaller, better-scoped phases. "
                    "Add explicit deliverable constraints to each step."
                ),
            )
        )
        return out

    def _detect_plan_mismatch(
        self,
        plan: Any | None,
        step_results: list[Any],
        retro: Any | None,
    ) -> list[FailureClassification]:
        """Detect when the assigned agent was a poor fit for the task."""
        out: list[FailureClassification] = []
        evidence: list[str] = []
        affected_agents: list[str] = []

        # Check learned-overrides for agent_drops that reference agents in the plan.
        overrides_path = self._ctx_root / "learned-overrides.json"
        dropped_agents: set[str] = set()
        try:
            import json

            if overrides_path.exists():
                data = json.loads(overrides_path.read_text(encoding="utf-8"))
                for drop in data.get("agent_drops", []):
                    agent = drop.get("agent", "") if isinstance(drop, dict) else str(drop)
                    if agent:
                        dropped_agents.add(agent)
        except Exception:
            pass

        if plan is not None:
            for phase in getattr(plan, "phases", []):
                for step in getattr(phase, "steps", []):
                    agent = getattr(step, "agent_name", "") or ""
                    if agent in dropped_agents:
                        evidence.append(
                            f"Agent '{agent}' (step {getattr(step, 'step_id', '?')}) "
                            "is in learned-overrides agent_drops"
                        )
                        if agent not in affected_agents:
                            affected_agents.append(agent)

        # Check retro roster_recommendations for "remove" or "drop" actions.
        if retro is not None:
            for rec in getattr(retro, "roster_recommendations", []):
                action = getattr(rec, "action", "") or ""
                target = getattr(rec, "target", "") or ""
                if action.lower() in ("remove", "drop"):
                    evidence.append(
                        f"Retro recommends '{action}' for agent/pack '{target}'"
                    )
                    if target not in affected_agents:
                        affected_agents.append(target)

        if not evidence:
            return out

        out.append(
            FailureClassification(
                category="PLAN_MISMATCH",
                subcategory="PLAN_MISMATCH_AGENT",
                confidence=0.75,
                affected_agents=affected_agents,
                evidence=evidence,
                recommended_action=(
                    "Update the learned-overrides or route the task to a "
                    "more appropriate specialist agent."
                ),
            )
        )
        return out

    def _detect_context_exhaustion(
        self, beads: list[Any]
    ) -> list[FailureClassification]:
        out: list[FailureClassification] = []
        evidence: list[str] = []
        for bead in beads:
            bead_type = getattr(bead, "bead_type", "") or ""
            content = getattr(bead, "content", "") or ""
            if bead_type != "warning":
                continue
            if any(kw in content.lower() for kw in _CTX_KEYWORDS):
                bead_id = getattr(bead, "bead_id", "?")
                evidence.append(f"Warning bead {bead_id}: context exhaustion signal")
        if not evidence:
            return out
        out.append(
            FailureClassification(
                category="CONTEXT_EXHAUST",
                subcategory="CONTEXT_EXHAUST_COMPACTION",
                confidence=0.8,
                evidence=evidence,
                recommended_action=(
                    "Split the task into smaller phases, use --terse dispatch, "
                    "or add a context-compaction gate between phases."
                ),
            )
        )
        return out

    # ------------------------------------------------------------------
    # Private: pattern detection
    # ------------------------------------------------------------------

    def _detect_recurring_patterns_from(
        self,
        tasks_per_id: dict[str, list[FailureClassification]],
        total_executions: int,
        min_occurrences: int = 2,
        min_failure_rate: float = 0.3,
    ) -> list[RecurringPattern]:
        """Build recurring-pattern records from per-task classification maps."""
        if not tasks_per_id or total_executions == 0:
            return []

        # Group by (category, subcategory) → list of task_ids
        pattern_tasks: dict[tuple[str, str], list[str]] = defaultdict(list)
        pattern_agents: dict[tuple[str, str], Counter] = defaultdict(Counter)
        pattern_occurrences: dict[tuple[str, str], int] = defaultdict(int)

        for tid, cls_list in tasks_per_id.items():
            for cls in cls_list:
                key = (cls.category, cls.subcategory)
                pattern_tasks[key].append(tid)
                pattern_occurrences[key] += 1
                for agent in cls.affected_agents:
                    pattern_agents[key][agent] += 1

        patterns: list[RecurringPattern] = []
        for (category, subcategory), task_ids in pattern_tasks.items():
            frequency = len(set(task_ids))
            total_occ = pattern_occurrences[(category, subcategory)]
            failure_rate = frequency / total_executions
            if frequency < min_occurrences or failure_rate < min_failure_rate:
                continue

            top_agents = [
                a for a, _ in pattern_agents[(category, subcategory)].most_common(5)
            ]
            pattern_type = _category_to_pattern_type(category)
            description = _pattern_description(category, subcategory, frequency)
            rec_action = _pattern_recommendation(category)

            patterns.append(
                RecurringPattern(
                    pattern_type=pattern_type,
                    description=description,
                    frequency=frequency,
                    total_occurrences=total_occ,
                    failure_rate=round(failure_rate, 4),
                    affected_agents=top_agents,
                    affected_task_types=[],
                    evidence_task_ids=list(set(task_ids))[:10],
                    recommended_action=rec_action,
                )
            )

        patterns.sort(key=lambda p: (-p.frequency, -p.total_occurrences))
        return patterns

    # ------------------------------------------------------------------
    # Private: recommendations
    # ------------------------------------------------------------------

    def _build_recommendations(
        self,
        classifications: list[FailureClassification],
        task_ids: list[str],
        plan: Any | None,
        retro: Any | None,
    ) -> list[LookbackRecommendation]:
        recs: list[LookbackRecommendation] = []
        seen_categories: set[str] = set()

        for fc in classifications:
            if fc.category in seen_categories:
                continue
            seen_categories.add(fc.category)
            rec = _classification_to_recommendation(fc, task_ids)
            if rec is not None:
                recs.append(rec)

        return recs

    def _build_range_recommendations(
        self,
        patterns: list[RecurringPattern],
        task_ids: list[str],
    ) -> list[LookbackRecommendation]:
        recs: list[LookbackRecommendation] = []
        for pat in patterns:
            rec = _pattern_to_recommendation(pat)
            if rec is not None:
                recs.append(rec)
        return recs

    # ------------------------------------------------------------------
    # Private: helpers
    # ------------------------------------------------------------------

    def _estimate_token_waste(self, state: Any | None) -> int:
        if state is None:
            return 0
        total = 0
        for sr in getattr(state, "step_results", []) or []:
            if getattr(sr, "status", "") == "failed":
                total += getattr(sr, "estimated_tokens", 0) or 0
        return total


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _category_to_pattern_type(category: str) -> str:
    mapping = {
        "PLAN_MISMATCH": "agent_task_mismatch",
        "GATE_FAIL": "missing_gate",
        "AGENT_ERROR": "agent_task_mismatch",
        "SCOPE_OVERRUN": "scope_creep",
        "ENV_FAILURE": "env_dep",
        "CONTEXT_EXHAUST": "env_dep",
    }
    return mapping.get(category, "agent_task_mismatch")


def _pattern_description(category: str, subcategory: str, frequency: int) -> str:
    base = subcategory.replace("_", " ").title()
    return f"{base} — seen in {frequency} execution(s)"


def _pattern_recommendation(category: str) -> str:
    recs = {
        "PLAN_MISMATCH": "Add a learned-override to drop the mismatched agent.",
        "GATE_FAIL": "Add or tighten a gate to catch failures earlier.",
        "AGENT_ERROR": "Add a retry gate or split the task into smaller steps.",
        "SCOPE_OVERRUN": "Split tasks into smaller, better-scoped phases.",
        "ENV_FAILURE": "Add an environment-setup gate or knowledge pack.",
        "CONTEXT_EXHAUST": "Use --terse dispatch and add compaction checkpoints.",
    }
    return recs.get(category, "Review execution history for this pattern.")


def _classification_to_recommendation(
    fc: FailureClassification,
    task_ids: list[str],
) -> LookbackRecommendation | None:
    action_map: dict[str, tuple[str, bool]] = {
        "PLAN_MISMATCH": ("add_override", True),
        "GATE_FAIL": ("add_gate", False),
        "AGENT_ERROR": ("change_agent", False),
        "SCOPE_OVERRUN": ("split_task", False),
        "ENV_FAILURE": ("add_knowledge_pack", True),
        "CONTEXT_EXHAUST": ("split_task", False),
    }
    mapping = action_map.get(fc.category)
    if mapping is None:
        return None
    action, auto = mapping
    target = fc.affected_agents[0] if fc.affected_agents else fc.subcategory
    return LookbackRecommendation(
        action=action,
        target=target,
        detail=fc.recommended_action,
        confidence=fc.confidence,
        auto_applicable=auto,
        evidence_task_ids=task_ids[:5],
    )


def _pattern_to_recommendation(
    pat: RecurringPattern,
) -> LookbackRecommendation | None:
    action_map = {
        "agent_task_mismatch": ("add_override", True),
        "missing_gate": ("add_gate", False),
        "scope_creep": ("split_task", False),
        "env_dep": ("add_knowledge_pack", True),
    }
    mapping = action_map.get(pat.pattern_type)
    if mapping is None:
        return None
    action, auto = mapping
    target = pat.affected_agents[0] if pat.affected_agents else pat.pattern_type
    return LookbackRecommendation(
        action=action,
        target=target,
        detail=pat.recommended_action,
        confidence=min(0.95, 0.5 + pat.failure_rate),
        auto_applicable=auto,
        evidence_task_ids=pat.evidence_task_ids[:5],
    )
