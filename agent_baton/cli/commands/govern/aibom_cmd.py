"""``baton govern aibom`` -- generate a per-task / per-PR AI Bill of Materials.

G1.7 (bd-965a). Pure observability: never gates, never writes anywhere
except the explicit ``--output`` PATH (and an opt-in ``--attach-pr``
GitHub comment).

Usage
-----
::

    baton govern aibom [--task-id ID]
                       [--pr N]
                       [--format markdown|json|spdx-json]
                       [--output PATH]
                       [--attach-pr]
                       [--db PATH]
                       [--agents-dir PATH]
                       [--compliance-log PATH]
                       [--repo OWNER/REPO]

When ``--task-id`` is omitted the active task is read from
``.claude/team-context/active-task-id.txt``.

When ``--pr N`` is provided we use ``gh api repos/<owner>/<repo>/pulls/<N>``
to populate the subject section.  Without ``--pr`` we use the current
git branch and a ``master..HEAD`` commit range.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from agent_baton.core.govern.aibom import AIBOMBuilder, PullRequestInfo


# ---------------------------------------------------------------------------
# Argparse plumbing
# ---------------------------------------------------------------------------


# Note: this module registers itself as the top-level subcommand
# ``aibom``; it is grouped under "Governance" via the cli/main.py
# command-groups map.  The govern group does not have its own
# argparse subparser today (see other modules in this package such as
# ``escalations.py`` and ``compliance.py`` which all register at the
# top level).  We follow the same convention.

def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    p = subparsers.add_parser(
        "aibom",
        help="Generate an AI Bill of Materials for a task or PR (G1.7)",
        description=(
            "Generate an AI Bill of Materials (AIBOM) documenting the models, "
            "agents, MCP servers, knowledge attachments, and gates that "
            "contributed to a task. Pure observability -- never gates."
        ),
    )
    p.add_argument(
        "--task-id",
        metavar="ID",
        default=None,
        help="Task ID to generate the AIBOM for (default: active task)",
    )
    p.add_argument(
        "--pr",
        metavar="N",
        type=int,
        default=None,
        help="Associate the AIBOM with GitHub PR #N (uses gh api)",
    )
    p.add_argument(
        "--format",
        choices=["markdown", "json", "spdx-json"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    p.add_argument(
        "--output",
        metavar="PATH",
        default=None,
        help="Write the AIBOM to PATH instead of stdout",
    )
    p.add_argument(
        "--attach-pr",
        action="store_true",
        help=(
            "When --pr is supplied, post the markdown AIBOM as a comment on "
            "the PR (requires gh; off by default)"
        ),
    )
    p.add_argument(
        "--db",
        metavar="PATH",
        default=None,
        help="Override baton.db location (default: .claude/team-context/baton.db)",
    )
    p.add_argument(
        "--agents-dir",
        metavar="PATH",
        default=None,
        help="Directory containing agent .md files for MCP server discovery",
    )
    p.add_argument(
        "--compliance-log",
        metavar="PATH",
        default=None,
        help="Override compliance-audit.jsonl path used for the chain anchor",
    )
    p.add_argument(
        "--repo",
        metavar="OWNER/REPO",
        default=None,
        help="GitHub repo slug for --pr lookups (default: detect from origin)",
    )
    return p


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def handler(args: argparse.Namespace) -> None:
    db_path = _resolve_db_path(args.db)
    if not db_path.exists():
        print(f"baton.db not found at {db_path}", file=sys.stderr)
        sys.exit(2)

    task_id = args.task_id or _read_active_task_id(db_path.parent)
    if not task_id:
        print(
            "No --task-id supplied and active-task-id.txt not found.",
            file=sys.stderr,
        )
        sys.exit(2)

    branch = _current_branch()
    commit_range = "" if args.pr else _master_to_head_range()
    pr_info = _fetch_pr_info(args.pr, args.repo) if args.pr else None

    builder = AIBOMBuilder(
        db_path=db_path,
        agents_dir=Path(args.agents_dir) if args.agents_dir else None,
        compliance_log=(
            Path(args.compliance_log) if args.compliance_log else None
        ),
    )
    try:
        aibom = builder.build(
            task_id,
            branch=branch,
            commit_range=commit_range,
            pull_request=pr_info,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    if args.format == "json":
        rendered = aibom.to_json()
    elif args.format == "spdx-json":
        rendered = aibom.to_spdx()
    else:
        rendered = aibom.to_markdown()

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
        print(f"AIBOM written to {out_path}")
    else:
        print(rendered)

    if args.attach_pr:
        if args.pr is None:
            print(
                "--attach-pr requires --pr; skipping comment.",
                file=sys.stderr,
            )
            return
        if shutil.which("gh") is None:
            print(
                "gh CLI not installed; cannot --attach-pr.",
                file=sys.stderr,
            )
            return
        markdown = aibom.to_markdown() if args.format != "markdown" else rendered
        body = (
            "<details><summary>AIBOM (Agent Baton)</summary>\n\n"
            "```markdown\n" + markdown + "\n```\n"
            "</details>\n"
        )
        cmd = ["gh", "pr", "comment", str(args.pr), "--body", body]
        if args.repo:
            cmd.extend(["--repo", args.repo])
        try:
            subprocess.run(cmd, check=True)
            print(f"AIBOM posted to PR #{args.pr}")
        except subprocess.CalledProcessError as exc:
            print(f"gh pr comment failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _resolve_db_path(override: str | None) -> Path:
    if override:
        return Path(override)
    env = os.environ.get("BATON_DB_PATH")
    if env:
        return Path(env)
    return Path.cwd() / ".claude" / "team-context" / "baton.db"


def _read_active_task_id(team_context: Path) -> str:
    f = team_context / "active-task-id.txt"
    if not f.exists():
        return ""
    try:
        return f.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _current_branch() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _master_to_head_range() -> str:
    # Try master first, then main.
    for base in ("master", "main"):
        try:
            subprocess.check_output(
                ["git", "rev-parse", "--verify", base],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            return f"{base}..HEAD"
        except (OSError, subprocess.CalledProcessError):
            continue
    return ""


def _fetch_pr_info(pr: int, repo: str | None) -> PullRequestInfo | None:
    if shutil.which("gh") is None:
        return PullRequestInfo(number=pr)
    if not repo:
        repo = _detect_repo()
    if not repo:
        return PullRequestInfo(number=pr)
    cmd = ["gh", "api", f"repos/{repo}/pulls/{pr}"]
    try:
        raw = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
    except (OSError, subprocess.CalledProcessError):
        return PullRequestInfo(number=pr)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return PullRequestInfo(number=pr)
    return PullRequestInfo(
        number=pr,
        url=str(data.get("html_url", "")),
        title=str(data.get("title", "")),
        head=str((data.get("head") or {}).get("ref", "")),
        base=str((data.get("base") or {}).get("ref", "")),
    )


def _detect_repo() -> str:
    try:
        out = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""
    # Examples:
    #   git@github.com:owner/repo.git
    #   https://github.com/owner/repo.git
    if out.startswith("git@"):
        _, _, tail = out.partition(":")
    else:
        tail = out.split("github.com/", 1)[-1]
    if tail.endswith(".git"):
        tail = tail[:-4]
    parts = [p for p in tail.split("/") if p]
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return ""
