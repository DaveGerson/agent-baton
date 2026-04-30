"""Shared context, bead capture, and dependency detection.

Extracted from ``_legacy_planner.IntelligentPlanner``.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_baton.core.govern.classifier import ClassificationResult
    from agent_baton.core.govern.policy import PolicyViolation
    from agent_baton.models.execution import MachinePlan, PlanPhase
    from agent_baton.models.feedback import RetrospectiveFeedback
    from agent_baton.models.taxonomy import ForesightInsight

logger = logging.getLogger(__name__)

# Dependency-detection patterns.
_DEP_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"\bbased on(?:\s+(?:task|the\s+results?\s+of|output\s+of))?\s+([a-z0-9][-a-z0-9]{6,})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bbuilding on(?:\s+(?:task|the\s+results?\s+of|output\s+of))?\s+([a-z0-9][-a-z0-9]{6,})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bcontinuing(?:\s+(?:from|the\s+work\s+of|task))?\s+([a-z0-9][-a-z0-9]{6,})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bfollows?\s+(?:from\s+)?(?:task\s+)?([a-z0-9][-a-z0-9]{6,})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bdepends?\s+on\s+(?:task\s+)?([a-z0-9][-a-z0-9]{6,})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\btask[-_\s]?id\s*[=:]\s*([a-z0-9][-a-z0-9]{6,})",
        re.IGNORECASE,
    ),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_task_dependency(task_summary: str, bead_store: Any) -> str | None:
    """Scan *task_summary* for references to a prior task_id.

    Returns the matched task_id string, or ``None``.
    """
    for pattern in _DEP_PATTERNS:
        m = pattern.search(task_summary)
        if m:
            candidate = m.group(1)
            try:
                beads = bead_store.query(task_id=candidate, limit=1)
                if beads:
                    return candidate
            except Exception:
                pass
    return None


def attach_prior_task_beads(
    plan_phases: "list[PlanPhase]",
    prior_task_id: str,
    bead_store: Any,
    max_beads: int = 5,
) -> None:
    """Attach outcome beads from *prior_task_id* as shared context.  Mutates in place."""
    try:
        beads = bead_store.query(
            task_id=prior_task_id,
            bead_type="decision",
            limit=max_beads,
        )
        if len(beads) < max_beads:
            outcome_beads = bead_store.query(
                task_id=prior_task_id,
                bead_type="outcome",
                limit=max_beads - len(beads),
            )
            existing_ids = {b.bead_id for b in beads}
            beads += [b for b in outcome_beads if b.bead_id not in existing_ids]
        if not beads:
            beads = bead_store.query(task_id=prior_task_id, limit=max_beads)
    except Exception:
        return

    if not beads:
        return

    prior_context_lines = [
        f"Prior task context (from {prior_task_id}):",
    ]
    for bead in beads[:max_beads]:
        snippet = (bead.content or "").replace("\n", " ").strip()
        if len(snippet) > 200:
            snippet = snippet[:197] + "..."
        prior_context_lines.append(f"  - [{bead.bead_type}] {snippet}")

    prior_context_block = "\n".join(prior_context_lines)

    for phase in plan_phases:
        for step in phase.steps:
            step.task_description = (
                f"{step.task_description}\n\n{prior_context_block}"
            )


def capture_planning_bead(
    task_id: str,
    content: str,
    tags: list[str] | None,
    bead_store: Any,
) -> None:
    """Write a planning bead to the bead store.  Silently no-ops on failure."""
    if bead_store is None:
        return
    try:
        from datetime import datetime, timezone
        from agent_baton.models.bead import Bead, _generate_bead_id
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            existing_count = len(
                bead_store.query(task_id=task_id, limit=10000)
            )
        except Exception:
            existing_count = 0
        bead_id = _generate_bead_id(task_id, "planning", content, timestamp, existing_count)
        bead = Bead(
            bead_id=bead_id,
            task_id=task_id,
            step_id="planning",
            agent_name="planner",
            bead_type="planning",
            content=content,
            confidence="high",
            scope="task",
            tags=tags or ["planning"],
            status="open",
            created_at=timestamp,
            source="planning-capture",
        )
        bead_store.write(bead)
    except Exception as exc:
        logger.debug("capture_planning_bead failed (non-fatal): %s", exc)


def build_shared_context(
    plan: "MachinePlan",
    *,
    classification: "ClassificationResult | None" = None,
    policy_violations: "list[PolicyViolation] | None" = None,
    retro_feedback: "RetrospectiveFeedback | None" = None,
    team_cost_estimates: dict[str, int] | None = None,
    foresight_insights: "list[ForesightInsight] | None" = None,
    task_summary: str = "",
) -> str:
    """Build the shared_context string embedded in the plan."""
    agent_list = ", ".join(dict.fromkeys(plan.all_agents))
    lines: list[str] = [
        f"Task: {plan.task_summary}",
        f"Risk: {plan.risk_level} | Budget: {plan.budget_tier}",
    ]
    if agent_list:
        lines.append(f"Team: {agent_list}")

    if classification is not None:
        lines.append(
            f"Guardrail Preset: {classification.guardrail_preset}"
        )
        if classification.signals_found:
            lines.append(
                f"Sensitivity Signals: {', '.join(classification.signals_found)}"
            )

    if policy_violations:
        warn_lines = []
        for v in policy_violations:
            severity_tag = "[WARN]" if v.rule.severity == "warn" else "[POLICY]"
            warn_lines.append(f"  {severity_tag} {v.details}")
        lines.append("Policy Notes:\n" + "\n".join(warn_lines))

    if (
        retro_feedback is not None
        and retro_feedback.knowledge_gaps
    ):
        gap_lines = [
            f"  - {g.description}"
            + (f" (fix: {g.suggested_fix})" if g.suggested_fix else "")
            for g in retro_feedback.knowledge_gaps
        ]
        lines.append(
            "Knowledge Gaps (from recent retrospectives):\n" + "\n".join(gap_lines)
        )

    if team_cost_estimates:
        budget_thresholds = {"lean": 50_000, "standard": 500_000, "full": 2_000_000}
        budget_limit = budget_thresholds.get(plan.budget_tier, 500_000)
        total_team_cost = sum(team_cost_estimates.values())
        budget_pct = (total_team_cost / budget_limit * 100) if budget_limit > 0 else 0
        lines.append(
            f"Team Cost Estimate: ~{total_team_cost:,} tokens "
            f"({budget_pct:.0f}% of {plan.budget_tier} budget)"
        )

    if foresight_insights:
        insight_lines = [
            f"  - [{ins.category}] {ins.description}"
            + (f" (phase: {ins.inserted_phase_name})" if ins.inserted_phase_name else "")
            for ins in foresight_insights
        ]
        lines.append(
            "Foresight (proactive gaps addressed):\n" + "\n".join(insight_lines)
        )

    ext_annotations = _fetch_external_annotations(task_summary or plan.task_summary)
    if ext_annotations:
        lines.append("Relates to: " + ", ".join(ext_annotations))

    return "\n".join(lines)


def _fetch_external_annotations(task_summary: str) -> list[str]:
    """Return matching external item references for the plan."""
    try:
        from pathlib import Path
        central_db = Path.home() / ".baton" / "central.db"
        if not central_db.exists():
            return []

        from agent_baton.core.storage.central import CentralStore
        store = CentralStore(central_db)
        try:
            guard = store.query(
                "SELECT COUNT(*) AS n FROM external_mappings"
            )
            if not guard or guard[0].get("n", 0) == 0:
                return []

            words = [
                w.lower()
                for w in task_summary.split()
                if len(w) >= 4
            ]
            if not words:
                return []

            rows = store.query(
                "SELECT external_id, title FROM external_items LIMIT 200"
            )
            matches: list[str] = []
            for row in rows:
                combined = (
                    (row.get("title") or "") + " " +
                    (row.get("external_id") or "")
                ).lower()
                if any(w in combined for w in words):
                    title = (row.get("title") or "").strip()
                    ext_id = row.get("external_id", "")
                    label = f"{ext_id} ({title})" if title else ext_id
                    matches.append(label)
                    if len(matches) >= 5:
                        break
            return matches
        finally:
            store.close()
    except Exception:
        return []
