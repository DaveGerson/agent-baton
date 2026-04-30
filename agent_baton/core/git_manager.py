"""Git management utility for repository maintenance and hygiene.

Consolidates logic for branch and worktree lifecycle management.
Follows the "do no harm" principle by prioritizing visibility and 
explicit user approval for destructive operations.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

@dataclass
class BranchStatus:
    name: str
    is_merged: bool
    is_remote: bool
    last_commit_date: str
    last_author: str

class GitManager:
    def __init__(self, project_root: Path):
        self.root = project_root

    def list_merged_branches(self, base: str = "master", include_remote: bool = False) -> list[str]:
        """List local (and optionally remote) branches already merged into base."""
        args = ["branch", "--merged", base]
        if include_remote:
            args = ["branch", "-a", "--merged", base]
        
        result = self._run(args)
        branches = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("*") or " -> " in line:
                continue
            # Remove remote prefix if present
            if line.startswith("remotes/origin/"):
                line = line[len("remotes/origin/"):]
            if line not in branches:
                branches.append(line)
        return branches

    def delete_local_branch(self, name: str, force: bool = False) -> bool:
        """Delete a local branch."""
        flag = "-D" if force else "-d"
        try:
            self._run(["branch", flag, name])
            return True
        except subprocess.CalledProcessError:
            return False

    def prune_remotes(self) -> str:
        """Prune stale remote-tracking branches."""
        result = self._run(["remote", "prune", "origin"])
        return result.stdout.strip()

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            cwd=str(self.root),
            check=True
        )
