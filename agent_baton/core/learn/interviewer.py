"""LearningInterviewer — structured CLI dialogue for human-directed decisions.

Presents open learning issues one at a time with a multiple-choice menu,
records the user's decision, and updates the ledger accordingly.

Typical usage::

    ledger = LearningLedger(db_path)
    interviewer = LearningInterviewer(ledger)

    issue = interviewer.get_next_issue()
    if issue is None:
        print("No issues to review.")
    else:
        print(interviewer.format_issue(issue))
        for key, description in interviewer.get_options(issue):
            print(f"  ({key}) {description}")
        choice = input("Choice: ")
        reasoning = input("Reasoning (optional): ")
        interviewer.record_decision(issue.issue_id, choice, reasoning)
"""
from __future__ import annotations

import logging

from agent_baton.core.learn.ledger import LearningLedger
from agent_baton.models.learning import LearningIssue

_log = logging.getLogger(__name__)

# Multiple-choice options per issue type
_OPTIONS_BY_TYPE: dict[str, list[tuple[str, str]]] = {
    "routing_mismatch": [
        ("a", "Apply fix — write routing override"),
        ("b", "Investigate further — gather more data"),
        ("c", "Won't fix — suppress future alerts for this target"),
        ("f", "Skip — come back later"),
    ],
    "agent_degradation": [
        ("a", "Evolve agent prompt — generate improved prompt from failure patterns"),
        ("b", "Add knowledge pack — create targeted context for failure scenarios"),
        ("c", "Reduce routing priority — deprioritize for future plans"),
        ("d", "Drop agent — add to persistent drop list"),
        ("e", "Investigate further — gather more data"),
        ("f", "Won't fix — suppress future alerts for this target"),
        ("g", "Skip — come back later"),
    ],
    "knowledge_gap": [
        ("a", "Create knowledge pack — write stub to .claude/knowledge/"),
        ("b", "Update agent prompt — note gap in agent definition"),
        ("c", "Investigate further — gather more data"),
        ("d", "Won't fix — suppress future alerts for this target"),
        ("f", "Skip — come back later"),
    ],
    "pattern_drift": [
        ("a", "Accept new pattern — update learned patterns"),
        ("b", "Revert to old pattern — keep current behavior"),
        ("c", "Investigate further — gather more data"),
        ("f", "Skip — come back later"),
    ],
    "prompt_evolution": [
        ("a", "Review & apply draft — apply generated prompt improvement"),
        ("b", "Edit draft — open for manual editing before applying"),
        ("c", "Reject — discard the draft"),
        ("f", "Skip — come back later"),
    ],
    "roster_bloat": [
        ("a", "Adjust thresholds — increase min_keyword_overlap"),
        ("b", "Lock agent list — prevent automatic agent additions"),
        ("c", "Investigate further — gather more data"),
        ("f", "Skip — come back later"),
    ],
    "gate_mismatch": [
        ("a", "Apply fix — write gate command override"),
        ("b", "Custom command — specify a different gate command"),
        ("c", "Investigate further — gather more data"),
        ("d", "Won't fix — suppress future alerts for this target"),
        ("f", "Skip — come back later"),
    ],
}

_DEFAULT_OPTIONS: list[tuple[str, str]] = [
    ("a", "Apply fix — auto-resolve this issue"),
    ("b", "Investigate further — gather more data"),
    ("c", "Won't fix — suppress future alerts for this target"),
    ("f", "Skip — come back later"),
]

# Map choice keys to status transitions
_CHOICE_STATUS_MAP: dict[str, str] = {
    "a": "applied",
    "b": "investigating",
    "c": "wontfix",
    "d": "applied",   # agent_degradation: drop agent
    "e": "investigating",
    "f": "open",      # skip — stays open
    "g": "open",
}


class LearningInterviewer:
    """Structured dialogue system for human-directed learning decisions.

    Args:
        ledger: A LearningLedger instance for reading and updating issues.
    """

    def __init__(self, ledger: LearningLedger) -> None:
        self._ledger = ledger

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_next_issue(
        self,
        type_filter: str | None = None,
        severity_filter: str | None = None,
    ) -> LearningIssue | None:
        """Return the next issue that needs human review.

        Prioritizes ``"proposed"`` status first, then ``"open"`` issues
        with high severity or interview-only types (pattern_drift,
        prompt_evolution).

        Args:
            type_filter: Only return issues of this type, or None for all.
            severity_filter: Only return issues of this severity, or None for all.

        Returns:
            The highest-priority issue needing review, or None if queue is empty.
        """
        all_issues = self._ledger.get_open_issues(
            issue_type=type_filter,
            severity=severity_filter,
        )
        # Add proposed issues too
        proposed = self._ledger.get_all_issues(
            status="proposed",
            issue_type=type_filter,
            severity=severity_filter,
        )
        combined = {i.issue_id: i for i in all_issues}
        combined.update({i.issue_id: i for i in proposed})

        if not combined:
            return None

        # Sort: proposed first, then by severity, then by occurrence_count
        _severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        _interview_types = {"pattern_drift", "prompt_evolution"}

        def _sort_key(issue: LearningIssue) -> tuple:
            status_priority = 0 if issue.status == "proposed" else 1
            interview_priority = 0 if issue.issue_type in _interview_types else 1
            severity_priority = _severity_order.get(issue.severity, 4)
            return (status_priority, interview_priority, severity_priority, -issue.occurrence_count)

        sorted_issues = sorted(combined.values(), key=_sort_key)
        return sorted_issues[0] if sorted_issues else None

    def format_issue(self, issue: LearningIssue) -> str:
        """Return a formatted string describing the issue for CLI display.

        Args:
            issue: The issue to format.

        Returns:
            Multi-line string with issue header, metrics, and evidence summary.
        """
        lines: list[str] = []

        # Header
        lines.append(
            f"Issue {issue.issue_id[:8]}: {_type_label(issue.issue_type)} — {issue.target}"
        )
        lines.append(
            f"Severity: {issue.severity} | "
            f"Status: {issue.status} | "
            f"Occurrences: {issue.occurrence_count} | "
            f"First seen: {issue.first_seen[:10] if issue.first_seen else 'unknown'}"
        )
        lines.append("")

        # Title
        lines.append(f"  {issue.title}")
        lines.append("")

        # Evidence summary (up to 5 entries)
        if issue.evidence:
            lines.append("Evidence summary:")
            for ev in issue.evidence[:5]:
                lines.append(f"  - {ev.detail}")
            if len(issue.evidence) > 5:
                lines.append(f"  ... and {len(issue.evidence) - 5} more")
            lines.append("")

        # Proposed fix if available
        if issue.proposed_fix:
            lines.append(f"Proposed fix: {issue.proposed_fix}")
            lines.append("")

        lines.append("What would you like to do?")
        return "\n".join(lines)

    def get_options(self, issue: LearningIssue) -> list[tuple[str, str]]:
        """Return the list of (key, description) action options for this issue.

        Args:
            issue: The issue being reviewed.

        Returns:
            List of ``(key, description)`` tuples for display.
        """
        return list(_OPTIONS_BY_TYPE.get(issue.issue_type, _DEFAULT_OPTIONS))

    def record_decision(
        self,
        issue_id: str,
        choice: str,
        reasoning: str = "",
    ) -> bool:
        """Record the human's decision and update the ledger.

        Args:
            issue_id: The issue being decided on.
            choice: Single-character choice key from get_options().
            reasoning: Optional free-text reasoning for the decision.

        Returns:
            True if the issue was updated successfully.
        """
        choice = choice.strip().lower()
        new_status = _CHOICE_STATUS_MAP.get(choice, "open")

        resolution: str | None = None
        if new_status not in ("open", "investigating"):
            resolution = reasoning or f"Human decision: choice={choice!r}"

        return self._ledger.update_status(
            issue_id,
            status=new_status,
            resolution=resolution,
            resolution_type="interview" if new_status not in ("open", "investigating") else None,
        )

    def run_interactive(
        self,
        type_filter: str | None = None,
        severity_filter: str | None = None,
    ) -> int:
        """Run the full interactive interview loop in the terminal.

        Presents issues one at a time until the queue is empty or the user
        quits.

        Args:
            type_filter: Only show issues of this type.
            severity_filter: Only show issues of this severity.

        Returns:
            Number of decisions recorded.
        """
        decisions = 0
        while True:
            issue = self.get_next_issue(type_filter, severity_filter)
            if issue is None:
                print("No more issues to review.")
                break

            print()
            print("=" * 70)
            print(self.format_issue(issue))
            for key, description in self.get_options(issue):
                print(f"  ({key}) {description}")
            print()

            try:
                choice = input("Choice [f=skip]: ").strip() or "f"
                if choice.lower() == "q":
                    print("Interview session ended.")
                    break
                reasoning = ""
                if choice.lower() not in ("f", "g"):
                    reasoning = input("Reasoning (optional, press Enter to skip): ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nInterview interrupted.")
                break

            if self.record_decision(issue.issue_id, choice, reasoning):
                decisions += 1
                _status = _CHOICE_STATUS_MAP.get(choice.lower(), "open")
                print(f"Recorded: issue {issue.issue_id[:8]} -> {_status}")
            else:
                print(f"Warning: could not update issue {issue.issue_id[:8]}")

        return decisions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _type_label(issue_type: str) -> str:
    """Return a human-readable label for an issue type."""
    labels = {
        "routing_mismatch": "Routing Mismatch",
        "agent_degradation": "Agent Degradation",
        "knowledge_gap": "Knowledge Gap",
        "roster_bloat": "Roster Bloat",
        "gate_mismatch": "Gate Mismatch",
        "pattern_drift": "Pattern Drift",
        "prompt_evolution": "Prompt Evolution",
    }
    return labels.get(issue_type, issue_type.replace("_", " ").title())
