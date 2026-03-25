"""CLI command group: improve.

Commands for the closed-loop learning pipeline: scoring agent performance,
detecting patterns, tuning budgets, proposing prompt improvements, and
managing experiments.  The improve group feeds learnings from past
executions back into future plans.

Commands:
    * ``baton scores`` -- Show agent performance scorecards.
    * ``baton evolve`` -- Propose prompt improvements for underperformers.
    * ``baton patterns`` -- Display and refresh learned orchestration patterns.
    * ``baton budget`` -- Show or refresh budget tier recommendations.
    * ``baton changelog`` -- Show agent changelog entries or backups.
    * ``baton anomalies`` -- Detect and display system anomalies.
    * ``baton experiment`` -- Manage improvement experiments.
    * ``baton improve`` -- Run or view improvement cycle reports.
"""
from __future__ import annotations
