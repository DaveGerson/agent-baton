"""Wave 6.2 Part A — Sequential rebase coalescer (bd-707d).

Merges N chunk branches into a single coalesce branch via deterministic
sequential rebase.  Conflicts are handed to the ConflictReconciler; if
reconciliation fails the conflicting chunk is reverted (excluded) and
coalescing continues with the remaining chunks.

Algorithm:
  1. Sort chunks by chunk_id (lexicographic).
  2. Create a coalesce branch from base_sha.
  3. For each chunk: git rebase --onto coalesce-branch base_sha chunk-branch.
  4. Conflict → abort rebase → pass to reconciler.  Continue with rest.
  5. Final coalesce branch is the single fold-back commit.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.core.engine.worktree_manager import WorktreeManager
    from agent_baton.core.swarm.partitioner import CodeChunk

_log = logging.getLogger(__name__)

__all__ = ["Coalescer", "CoalesceResult"]


@dataclass
class CoalesceResult:
    """Outcome of a coalesce operation.

    Attributes:
        coalesce_branch: Name of the final branch after successful rebases.
        succeeded_chunks: chunk_id values that coalesced cleanly.
        conflicted_chunks: chunk_id values that had rebase conflicts.
        reverted_chunks: chunk_id values reverted after reconciler failure.
    """

    coalesce_branch: str
    succeeded_chunks: list[str] = field(default_factory=list)
    conflicted_chunks: list[str] = field(default_factory=list)
    reverted_chunks: list[str] = field(default_factory=list)


class Coalescer:
    """Merge chunk branches into a single coalesce branch.

    Args:
        repo_root: Absolute path to the parent git repository.
        worktree_mgr: WorktreeManager instance (used to resolve worktree paths).
    """

    def __init__(
        self,
        repo_root: Path,
        worktree_mgr: WorktreeManager,
    ) -> None:
        self._repo = repo_root.resolve()
        self._worktree_mgr = worktree_mgr

    # ── Public API ────────────────────────────────────────────────────────────

    def coalesce(
        self,
        chunks: list[CodeChunk],
        chunk_branches: dict[str, str],
        base_sha: str,
    ) -> CoalesceResult:
        """Sequentially rebase chunk branches onto a coalesce branch.

        Args:
            chunks: All chunks produced by the swarm (in any order).
            chunk_branches: Mapping of chunk_id → git branch name.
            base_sha: SHA the swarm branched from.

        Returns:
            :class:`CoalesceResult` describing what succeeded/failed.
        """
        # Create the coalesce branch from base_sha
        coalesce_branch = f"swarm-coalesce-{base_sha[:8]}"
        self._create_coalesce_branch(coalesce_branch, base_sha)

        result = CoalesceResult(coalesce_branch=coalesce_branch)

        # Sort chunks deterministically by chunk_id
        ordered = sorted(chunks, key=lambda c: c.chunk_id)

        for chunk in ordered:
            if chunk.chunk_id not in chunk_branches:
                _log.debug(
                    "Coalescer: skipping chunk %s (no branch registered)",
                    chunk.chunk_id[:8],
                )
                continue

            branch = chunk_branches[chunk.chunk_id]
            ok = self._rebase_chunk_onto(branch, coalesce_branch, base_sha)

            if ok:
                result.succeeded_chunks.append(chunk.chunk_id)
                _log.info(
                    "Coalescer: chunk %s coalesced cleanly onto %s",
                    chunk.chunk_id[:8], coalesce_branch,
                )
            else:
                result.conflicted_chunks.append(chunk.chunk_id)
                result.reverted_chunks.append(chunk.chunk_id)
                _log.warning(
                    "Coalescer: chunk %s has rebase conflict — skipping "
                    "(retained for forensics); continuing with remaining chunks",
                    chunk.chunk_id[:8],
                )

        _log.info(
            "Coalescer: complete — succeeded=%d conflicted=%d coalesce_branch=%s",
            len(result.succeeded_chunks),
            len(result.conflicted_chunks),
            coalesce_branch,
        )
        return result

    # ── Private helpers ───────────────────────────────────────────────────────

    def _create_coalesce_branch(self, branch: str, base_sha: str) -> None:
        """Create or reset the coalesce branch at base_sha."""
        # Check if branch already exists; if so reset it
        check = subprocess.run(
            ["git", "rev-parse", "--verify", branch],
            capture_output=True,
            cwd=str(self._repo),
        )
        if check.returncode == 0:
            # Reset existing branch to base_sha
            _run_git(
                ["branch", "-f", branch, base_sha],
                cwd=self._repo,
            )
        else:
            # Create new branch at base_sha
            _run_git(
                ["branch", branch, base_sha],
                cwd=self._repo,
            )
        _log.debug(
            "Coalescer: coalesce branch %s created/reset at %s",
            branch, base_sha[:8],
        )

    def _rebase_chunk_onto(
        self,
        chunk_branch: str,
        target_branch: str,
        base_sha: str,
    ) -> bool:
        """Rebase chunk_branch onto target_branch and fast-forward target.

        Returns:
            True on success, False on conflict (conflict state is aborted).
        """
        # Step 1: ensure chunk branch ref is accessible in parent repo
        # (it may live in a worktree — fetch it)
        # Try a no-op fetch to make the ref available
        _run_git(
            ["fetch", ".", f"{chunk_branch}:{chunk_branch}"],
            cwd=self._repo,
            check=False,
        )

        # Step 2: rebase chunk onto coalesce branch tip
        rebase_result = subprocess.run(
            [
                "git", "rebase",
                "--onto", target_branch,
                base_sha,
                chunk_branch,
            ],
            capture_output=True,
            text=True,
            cwd=str(self._repo),
        )

        if rebase_result.returncode != 0:
            # Abort rebase to leave repo in clean state
            subprocess.run(
                ["git", "rebase", "--abort"],
                capture_output=True,
                cwd=str(self._repo),
            )
            _log.warning(
                "Coalescer._rebase_chunk_onto: rebase conflict on branch=%s: %s",
                chunk_branch,
                (rebase_result.stderr or rebase_result.stdout)[:500].strip(),
            )
            return False

        # Step 3: resolve new tip of rebased branch
        tip_r = subprocess.run(
            ["git", "rev-parse", chunk_branch],
            capture_output=True,
            text=True,
            cwd=str(self._repo),
        )
        if tip_r.returncode != 0:
            return False
        new_tip = tip_r.stdout.strip()

        # Step 4: fast-forward coalesce branch to new tip
        _run_git(
            ["update-ref", f"refs/heads/{target_branch}", new_tip],
            cwd=self._repo,
        )
        return True


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _run_git(
    args: list[str],
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result
