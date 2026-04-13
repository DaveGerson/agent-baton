"""``baton learn`` — learning automation commands.

Provides visibility into, and control over, the learning automation system.
Issues are stored in the project-level ``baton.db`` and surfaced here with
filtering, analysis, and interactive resolution capabilities.

Subcommands:
    status      Dashboard of open issues by type/severity and auto-apply stats.
    issues      List issues with optional --type, --severity, --status filters.
    analyze     Run analysis: detect patterns, mark auto-apply candidates.
    apply       Apply a specific fix or all auto-applicable (proposed) issues.
    interview   Interactive structured dialogue for human-directed decisions.
    history     Resolution history with outcomes.
    reset       Reopen an issue / rollback an applied fix.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def _db_path() -> Path:
    """Resolve the project baton.db path."""
    return Path(".claude/team-context/baton.db").resolve()


def _team_context_root() -> Path:
    return Path(".claude/team-context").resolve()


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "learn",
        help="Learning automation — issue tracking, analysis, and resolution",
    )
    sub = p.add_subparsers(dest="learn_command", metavar="COMMAND")

    # ---- status --------------------------------------------------------
    sub.add_parser("status", help="Dashboard: open issues, auto-apply stats")

    # ---- issues --------------------------------------------------------
    issues_p = sub.add_parser("issues", help="List learning issues with filters")
    issues_p.add_argument(
        "--type",
        dest="issue_type",
        help="Filter by issue type (e.g. routing_mismatch, agent_degradation)",
    )
    issues_p.add_argument(
        "--severity",
        help="Filter by severity (low, medium, high, critical)",
    )
    issues_p.add_argument(
        "--status",
        help="Filter by status (open, investigating, proposed, applied, resolved, wontfix)",
    )

    # ---- analyze -------------------------------------------------------
    sub.add_parser(
        "analyze",
        help="Run analysis: compute confidence, mark auto-apply candidates",
    )

    # ---- apply ---------------------------------------------------------
    apply_p = sub.add_parser("apply", help="Apply a specific fix or all proposed issues")
    apply_grp = apply_p.add_mutually_exclusive_group()
    apply_grp.add_argument("--issue", metavar="ID", help="Apply fix for a specific issue ID")
    apply_grp.add_argument(
        "--all-safe",
        action="store_true",
        help="Apply all issues currently in 'proposed' status",
    )

    # ---- interview -----------------------------------------------------
    interview_p = sub.add_parser(
        "interview",
        help="Interactive structured dialogue for human-directed decisions",
    )
    interview_p.add_argument(
        "--type",
        dest="issue_type",
        help="Focus on a specific issue type",
    )
    interview_p.add_argument(
        "--severity",
        help="Focus on a specific severity",
    )

    # ---- history -------------------------------------------------------
    history_p = sub.add_parser("history", help="Show resolution history")
    history_p.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of resolved issues to show (default: 20)",
    )

    # ---- reset ---------------------------------------------------------
    reset_p = sub.add_parser(
        "reset",
        help="Reopen an issue and rollback its applied override",
    )
    reset_p.add_argument("--issue", metavar="ID", required=True, help="Issue ID to reset")

    return p


def handler(args: argparse.Namespace) -> None:
    cmd = getattr(args, "learn_command", None) or "status"

    db = _db_path()
    if not db.exists():
        print("No baton.db found. Run 'baton execute start' to initialise the database.")
        return

    from agent_baton.core.learn.ledger import LearningLedger
    from agent_baton.core.learn.engine import LearningEngine

    ledger = LearningLedger(db)

    if cmd == "status":
        _cmd_status(ledger)

    elif cmd == "issues":
        _cmd_issues(ledger, args)

    elif cmd == "analyze":
        engine = LearningEngine(_team_context_root())
        issues = engine.analyze()
        if not issues:
            print("No open issues found.")
            return
        print(f"Analysis complete. {len(issues)} issue(s) reviewed.")
        proposed = [i for i in issues if i.status == "proposed"]
        if proposed:
            print(f"  {len(proposed)} proposed for auto-apply:")
            for issue in proposed:
                print(f"    [{issue.issue_id[:8]}] {issue.issue_type}: {issue.title}")
        print()
        print("Run 'baton learn apply --all-safe' to apply proposed fixes.")

    elif cmd == "apply":
        engine = LearningEngine(_team_context_root())
        if getattr(args, "issue", None):
            try:
                resolution = engine.apply(args.issue)
                print(f"Applied: {resolution}")
            except ValueError as exc:
                print(f"Error: {exc}")
        elif getattr(args, "all_safe", False):
            proposed = ledger.get_all_issues(status="proposed")
            if not proposed:
                print("No issues in 'proposed' status. Run 'baton learn analyze' first.")
                return
            applied = 0
            for issue in proposed:
                try:
                    resolution = engine.apply(issue.issue_id, resolution_type="auto")
                    print(f"[{issue.issue_id[:8]}] {issue.issue_type}: {resolution}")
                    applied += 1
                except Exception as exc:
                    print(f"[{issue.issue_id[:8]}] Failed: {exc}")
            print(f"\nApplied {applied}/{len(proposed)} issues.")
        else:
            print("Specify --issue ID or --all-safe.")

    elif cmd == "interview":
        from agent_baton.core.learn.interviewer import LearningInterviewer
        interviewer = LearningInterviewer(ledger)
        type_filter = getattr(args, "issue_type", None)
        severity_filter = getattr(args, "severity", None)
        decisions = interviewer.run_interactive(
            type_filter=type_filter,
            severity_filter=severity_filter,
        )
        print(f"\nInterview complete. {decisions} decision(s) recorded.")

    elif cmd == "history":
        limit = getattr(args, "limit", 20)
        resolved = ledger.get_history(limit=limit)
        if not resolved:
            print("No resolved issues yet.")
            return
        print(f"Resolution History ({len(resolved)} entries):")
        print()
        for issue in resolved:
            print(f"  [{issue.issue_id[:8]}] [{issue.status.upper()}] {issue.issue_type}: {issue.title}")
            if issue.resolution:
                print(f"    Resolution: {issue.resolution}")
            if issue.resolution_type:
                print(f"    Via: {issue.resolution_type}")
            print()

    elif cmd == "reset":
        issue_id = args.issue
        issue = ledger.get_issue(issue_id)
        if issue is None:
            # Try prefix match
            all_issues = ledger.get_all_issues()
            matches = [i for i in all_issues if i.issue_id.startswith(issue_id)]
            if len(matches) == 1:
                issue = matches[0]
                issue_id = issue.issue_id
            elif len(matches) > 1:
                print(f"Ambiguous issue ID prefix '{issue_id}'. Matching IDs:")
                for m in matches:
                    print(f"  {m.issue_id}")
                return
            else:
                print(f"Issue not found: {issue_id}")
                return

        from agent_baton.core.learn.overrides import LearnedOverrides
        overrides = LearnedOverrides(_team_context_root() / "learned-overrides.json")
        overrides.remove_override(issue_id)

        ledger.update_status(
            issue.issue_id,
            status="open",
            resolution=None,
            resolution_type=None,
        )
        print(f"Issue {issue.issue_id[:8]} reset to 'open'.")
        print("Note: if an override file entry was created, remove it manually from:")
        print(f"  {_team_context_root() / 'learned-overrides.json'}")

    else:
        print(f"Unknown learn subcommand: {cmd!r}")
        print("Available: status, issues, analyze, apply, interview, history, reset")


# ---------------------------------------------------------------------------
# Sub-handlers
# ---------------------------------------------------------------------------


def _cmd_status(ledger: LearningLedger) -> None:
    from agent_baton.models.learning import VALID_ISSUE_TYPES, VALID_SEVERITIES

    open_issues = ledger.get_open_issues()
    proposed = ledger.get_all_issues(status="proposed")
    applied = ledger.get_all_issues(status="applied")
    resolved = ledger.get_all_issues(status="resolved")

    print("Learning Automation Status")
    print("=" * 50)
    print()
    print(f"  Open issues:     {len(open_issues)}")
    print(f"  Proposed fixes:  {len(proposed)}")
    print(f"  Applied fixes:   {len(applied)}")
    print(f"  Resolved:        {len(resolved)}")
    print()

    if open_issues or proposed:
        combined = {i.issue_id: i for i in open_issues}
        combined.update({i.issue_id: i for i in proposed})

        # Group by type
        by_type: dict[str, list] = {}
        for issue in combined.values():
            by_type.setdefault(issue.issue_type, []).append(issue)

        print("Open Issues by Type:")
        for issue_type in sorted(by_type):
            issues = by_type[issue_type]
            high_sev = sum(1 for i in issues if i.severity in ("high", "critical"))
            print(f"  {issue_type}: {len(issues)} issue(s)", end="")
            if high_sev:
                print(f" ({high_sev} high/critical)", end="")
            print()
        print()

    if proposed:
        print(f"Ready to apply ({len(proposed)}):")
        for issue in proposed[:5]:
            print(f"  [{issue.issue_id[:8]}] {issue.issue_type}: {issue.title}")
        if len(proposed) > 5:
            print(f"  ... and {len(proposed) - 5} more")
        print()
        print("Run 'baton learn apply --all-safe' to apply all proposed fixes.")
        print()

    if not open_issues and not proposed:
        print("No open issues. System is healthy.")


def _cmd_issues(ledger: LearningLedger, args: argparse.Namespace) -> None:
    issue_type = getattr(args, "issue_type", None)
    severity = getattr(args, "severity", None)
    status = getattr(args, "status", None)

    if status is not None:
        issues = ledger.get_all_issues(
            status=status, issue_type=issue_type, severity=severity
        )
    else:
        issues = ledger.get_open_issues(issue_type=issue_type, severity=severity)
        # Also include proposed
        proposed = ledger.get_all_issues(
            status="proposed", issue_type=issue_type, severity=severity
        )
        combined = {i.issue_id: i for i in issues}
        combined.update({i.issue_id: i for i in proposed})
        issues = sorted(combined.values(), key=lambda i: (-i.occurrence_count, i.first_seen))

    if not issues:
        print("No matching issues found.")
        return

    print(f"Learning Issues ({len(issues)} found):")
    print()
    for issue in issues:
        print(
            f"  [{issue.issue_id[:8]}] [{issue.severity.upper()}] "
            f"[{issue.status}] {issue.issue_type}"
        )
        print(f"    Title:  {issue.title}")
        print(f"    Target: {issue.target}")
        print(f"    Occurrences: {issue.occurrence_count}  |  Last seen: {issue.last_seen[:10]}")
        if issue.proposed_fix:
            print(f"    Proposed: {issue.proposed_fix}")
        print()
