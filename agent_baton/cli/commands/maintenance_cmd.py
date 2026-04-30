"""``baton maintenance`` -- accelerate development by automating repository hygiene.

Provides tools for cleaning up merged branches, stale worktrees, and
stray artifacts, following the "do no harm" principle.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "maintenance",
        help="Repository hygiene and developer acceleration (branches, worktrees, cache)",
    )
    sub = p.add_subparsers(dest="subcommand", required=True)

    # status: what needs cleaning?
    status_p = sub.add_parser("status", help="Show repository hygiene status")
    
    # cleanup: perform the cleanup
    clean_p = sub.add_parser("cleanup", help="Clean up merged branches and stale worktrees")
    clean_p.add_argument("--branches", action="store_true", help="Clean up merged local branches")
    clean_p.add_argument("--worktrees", action="store_true", help="Clean up stale worktrees")
    clean_p.add_argument("--remote-prune", action="store_true", help="Prune stale remote tracking branches")
    clean_p.add_argument("--force", action="store_true", help="Skip confirmation (use with caution)")
    clean_p.add_argument("--dry-run", action="store_true", help="Show what would be done")

    return p

def handler(args: argparse.Namespace) -> None:
    from agent_baton.core.git_manager import GitManager
    from agent_baton.core.engine.worktree_manager import WorktreeManager
    
    project_root = Path.cwd()
    git_mgr = GitManager(project_root)
    wt_mgr = WorktreeManager(project_root)

    if args.subcommand == "status":
        _handle_status(git_mgr, wt_mgr)
    elif args.subcommand == "cleanup":
        _handle_cleanup(args, git_mgr, wt_mgr)

def _handle_status(git_mgr: any, wt_mgr: any) -> None:
    merged = git_mgr.list_merged_branches()
    stale_wt = wt_mgr.gc_stale(dry_run=True)
    
    print("Repository Hygiene Status:")
    print(f"  - Merged local branches: {len(merged)}")
    for b in merged[:5]:
        print(f"      {b}")
    if len(merged) > 5:
        print(f"      ... and {len(merged) - 5} more")
        
    print(f"  - Stale worktrees: {len(stale_wt)}")
    for wt in stale_wt[:5]:
        print(f"      {wt.step_id} ({wt.path.name})")
    
    print("\nRun 'baton maintenance cleanup --dry-run' to preview specific actions.")

def _handle_cleanup(args: argparse.Namespace, git_mgr: any, wt_mgr: any) -> None:
    if not any([args.branches, args.worktrees, args.remote_prune]):
        print("Error: Specify what to clean up (e.g., --branches, --worktrees, --remote-prune)")
        return

    if args.branches:
        merged = git_mgr.list_merged_branches()
        if not merged:
            print("No merged branches to clean up.")
        else:
            print(f"Found {len(merged)} merged branches.")
            if args.dry_run:
                for b in merged:
                    print(f"  [DRY RUN] Would delete branch: {b}")
            else:
                if not args.force:
                    resp = input(f"Delete {len(merged)} merged local branches? [y/N] ")
                    if resp.lower() != 'y':
                        print("Aborted.")
                        return
                for b in merged:
                    if git_mgr.delete_local_branch(b):
                        print(f"  Deleted branch: {b}")
                    else:
                        print(f"  Failed to delete branch: {b}")

    if args.worktrees:
        if args.dry_run:
            wt_mgr.gc_stale(dry_run=True)
        else:
            reclaimed = wt_mgr.gc_stale(dry_run=False)
            print(f"Reclaimed {len(reclaimed)} stale worktrees.")

    if args.remote_prune:
        if args.dry_run:
            print("  [DRY RUN] Would prune remote tracking branches.")
        else:
            out = git_mgr.prune_remotes()
            if out:
                print(f"Remote prune output:\n{out}")
            else:
                print("Remote tracking branches already clean.")
