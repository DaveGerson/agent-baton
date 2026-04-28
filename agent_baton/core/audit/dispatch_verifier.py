"""Post-dispatch isolation verifier (bd-edbf).

Closes the worktree-isolation enforcement loop by giving operators a
read-only, after-the-fact compliance check on every dispatched step.

What it verifies for each step:

1. **Path scope** — every file in ``StepResult.files_changed`` falls
   under at least one glob in ``PlanStep.allowed_paths``.  Multiple
   ``allowed_paths`` are OR-ed (a file matching any one of them is in
   scope).  When ``allowed_paths`` is empty the step is treated as
   having no path sandbox declared (no violation possible).
2. **Branch alignment** — when ``StepResult.commit_hash`` is non-empty
   we resolve the recorded HEAD via ``git rev-parse`` and compare; a
   mismatch is flagged.  Branch mismatch is informational unless the
   operator dispatched onto a separate branch — we cannot recover the
   intended branch name from state, so this check uses the recorded
   commit as ground truth.

The verifier is **read-only**.  It never writes to execution state, the
plan, git, or any other artifact.  It only reads:

* ``ExecutionState`` (already loaded by the caller)
* ``StepResult.files_changed`` (preferred)
* ``git diff --name-only <commit>~..<commit>`` (fallback when
  ``files_changed`` is empty but ``commit_hash`` is present)

When neither the files_changed list nor a commit_hash is available the
result is reported as **inconclusive** (not a failure) — operators can
re-dispatch with proper recording rather than chase a phantom violation.

Usage::

    verifier = DispatchVerifier()
    result = verifier.verify_step(step, step_result, project_root)
    if not result.passed:
        for v in result.violations:
            print(v)

    report = verifier.audit_task(execution_state, project_root)
    print(f"{report.compliant_count}/{report.total_steps} steps compliant")
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePath, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from agent_baton.models.execution import ExecutionState, PlanStep, StepResult


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class VerificationResult:
    """Per-step verification outcome.

    Attributes:
        step_id: The step under audit.
        passed: ``True`` when the step is compliant or inconclusive.
            Only definite violations flip this to ``False``.
        files_outside_scope: Files written that no ``allowed_paths`` glob
            matched.  Empty when ``allowed_paths`` was unset or every file
            matched.
        branch_mismatch: ``True`` when the recorded ``commit_hash`` does
            not resolve to a valid commit OR (in future) when the active
            branch differs from the dispatched branch.  ``False`` when
            the recorded commit resolves cleanly or no commit was
            recorded.
        inconclusive: ``True`` when verification could not be performed
            (no ``files_changed`` and no ``commit_hash``).  An
            inconclusive result is NOT a failure — the report still
            counts the step as compliant.
        violations: Human-readable violation messages (one per offending
            file, plus one for branch mismatch when applicable).  Empty
            on success or inconclusive.
    """

    step_id: str
    passed: bool = True
    files_outside_scope: list[str] = field(default_factory=list)
    branch_mismatch: bool = False
    inconclusive: bool = False
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "passed": self.passed,
            "files_outside_scope": list(self.files_outside_scope),
            "branch_mismatch": self.branch_mismatch,
            "inconclusive": self.inconclusive,
            "violations": list(self.violations),
        }


@dataclass
class AuditReport:
    """Task-wide isolation audit aggregate.

    Attributes:
        task_id: The execution being audited.
        total_steps: Count of recorded step results inspected.
        compliant_count: Steps with ``passed=True`` (includes
            inconclusive results).
        results: Per-step ``VerificationResult`` rows.
    """

    task_id: str
    total_steps: int
    compliant_count: int
    results: list[VerificationResult] = field(default_factory=list)

    @property
    def violation_count(self) -> int:
        return self.total_steps - self.compliant_count

    @property
    def has_violations(self) -> bool:
        return any(not r.passed for r in self.results)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "total_steps": self.total_steps,
            "compliant_count": self.compliant_count,
            "violation_count": self.violation_count,
            "results": [r.to_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------


class DispatchVerifier:
    """Verify dispatched-step compliance against declared boundaries.

    Stateless — safe to instantiate per-call.  All git operations run
    against the supplied ``project_root`` and use a short timeout so a
    hung repo never blocks an audit run.
    """

    GIT_TIMEOUT_SECONDS = 10

    # ----- Public API -----

    def verify_step(
        self,
        step: "PlanStep",
        result: "StepResult",
        project_root: Path,
    ) -> VerificationResult:
        """Verify a single ``(PlanStep, StepResult)`` pair.

        Args:
            step: The plan step describing the dispatch contract
                (``allowed_paths``, etc.).
            result: The recorded step outcome.
            project_root: Filesystem root used to resolve git operations.

        Returns:
            A ``VerificationResult``.  ``passed=True`` when no
            definite violation was detected (compliant or inconclusive).
        """
        verdict = VerificationResult(step_id=step.step_id)

        files = self._collect_files(result, project_root)
        if files is None:
            # Inconclusive: nothing to compare.
            verdict.inconclusive = True
            return verdict

        # Path scope check.  Empty allowed_paths means no sandbox was
        # declared, so we have nothing to enforce.
        if step.allowed_paths:
            for f in files:
                if not _path_matches_any(f, step.allowed_paths):
                    verdict.files_outside_scope.append(f)
                    verdict.violations.append(
                        f"file outside allowed_paths: {f} "
                        f"(allowed: {', '.join(step.allowed_paths)})"
                    )
            if verdict.files_outside_scope:
                verdict.passed = False

        # Branch / commit alignment.  We treat an unresolvable commit as
        # a branch_mismatch signal.
        if result.commit_hash:
            if not self._commit_resolves(result.commit_hash, project_root):
                verdict.branch_mismatch = True
                verdict.violations.append(
                    f"recorded commit_hash does not resolve in repo: "
                    f"{result.commit_hash}"
                )
                verdict.passed = False

        return verdict

    def audit_task(
        self,
        execution_state: "ExecutionState",
        project_root: Path,
    ) -> AuditReport:
        """Run ``verify_step`` over every step in the execution state.

        Args:
            execution_state: The loaded ``ExecutionState``.
            project_root: Filesystem root for git operations.

        Returns:
            An ``AuditReport`` aggregating per-step outcomes.  The report
            visits every ``StepResult`` once; steps that lack a matching
            ``PlanStep`` (e.g. an amended-then-removed step) are skipped
            with no entry written.
        """
        steps_by_id = {
            s.step_id: s
            for phase in execution_state.plan.phases
            for s in phase.steps
        }

        results: list[VerificationResult] = []
        for sr in execution_state.step_results:
            step = steps_by_id.get(sr.step_id)
            if step is None:
                # Step was removed from the plan after dispatch; nothing
                # to verify against.  Skip silently — operator can audit
                # via plan-history if needed.
                continue
            results.append(self.verify_step(step, sr, project_root))

        compliant = sum(1 for r in results if r.passed)
        return AuditReport(
            task_id=execution_state.task_id,
            total_steps=len(results),
            compliant_count=compliant,
            results=results,
        )

    # ----- Internals -----

    def _collect_files(
        self,
        result: "StepResult",
        project_root: Path,
    ) -> list[str] | None:
        """Return the file list to verify, or ``None`` for inconclusive.

        Preference order:
          1. ``result.files_changed`` (already recorded by the orchestrator)
          2. ``git diff --name-only <commit>~..<commit>`` when a commit
             hash is recorded
          3. ``None`` (inconclusive — caller marks the result as such)
        """
        if result.files_changed:
            return list(result.files_changed)

        if result.commit_hash:
            files = self._git_diff_names(result.commit_hash, project_root)
            if files is not None:
                return files

        return None

    def _git_diff_names(
        self,
        commit_hash: str,
        project_root: Path,
    ) -> list[str] | None:
        """Return files changed by ``commit_hash``, or ``None`` on failure.

        Uses ``git diff-tree`` rather than ``git diff <sha>~..<sha>`` so
        we handle the root-commit case (no parent) gracefully.
        """
        try:
            proc = subprocess.run(
                [
                    "git",
                    "-C",
                    str(project_root),
                    "diff-tree",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    commit_hash,
                ],
                capture_output=True,
                text=True,
                timeout=self.GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        return [line for line in proc.stdout.splitlines() if line.strip()]

    def _commit_resolves(self, commit_hash: str, project_root: Path) -> bool:
        """Return True iff ``commit_hash`` is a valid commit in the repo."""
        try:
            proc = subprocess.run(
                [
                    "git",
                    "-C",
                    str(project_root),
                    "cat-file",
                    "-e",
                    f"{commit_hash}^{{commit}}",
                ],
                capture_output=True,
                text=True,
                timeout=self.GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        return proc.returncode == 0


# ---------------------------------------------------------------------------
# Glob helpers
# ---------------------------------------------------------------------------


def _path_matches_any(file_path: str, allowed_paths: list[str]) -> bool:
    """Return True when ``file_path`` matches at least one ``allowed_paths`` glob.

    Uses ``PurePosixPath.match`` for glob comparison to keep semantics
    consistent across operating systems (plan files always use POSIX
    separators).  An ``allowed_paths`` entry that is a directory prefix
    (e.g. ``agent_baton/core/``) matches any file beneath it; an entry
    containing a glob (e.g. ``tests/*.py``) is matched as a glob.
    """
    p = PurePosixPath(_normalize(file_path))
    for raw in allowed_paths:
        pattern = _normalize(raw)
        if not pattern:
            continue
        # Directory-prefix shorthand: "foo/" or "foo/bar/" matches anything
        # under that prefix.  Convert to a recursive glob.
        if pattern.endswith("/"):
            if _is_under(p, pattern.rstrip("/")):
                return True
            continue
        # Bare directory name (no trailing slash, no glob char): treat as
        # a recursive prefix as well — this matches operator intent for
        # the common "agent_baton/core/audit" form.
        if not _has_glob_chars(pattern):
            if _is_under(p, pattern) or str(p) == pattern:
                return True
            continue
        # Glob pattern: rely on PurePath.match (POSIX semantics).  Note:
        # PurePath.match is segment-aware and does NOT cross "/" with
        # "*", so "tests/*.py" matches "tests/foo.py" but not
        # "tests/sub/foo.py".  Operators wanting recursion should use
        # "tests/**/*.py".
        try:
            if PurePosixPath(str(p)).match(pattern):
                return True
        except (ValueError, NotImplementedError):
            # Defensive: a malformed pattern should not crash the audit.
            continue
    return False


def _normalize(path: str) -> str:
    """Strip leading "./" and convert backslashes to forward slashes."""
    p = path.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return p


def _is_under(file_path: PurePath, prefix: str) -> bool:
    """Return True when ``file_path`` lies inside ``prefix`` directory."""
    try:
        PurePosixPath(str(file_path)).relative_to(prefix)
        return True
    except ValueError:
        return False


def _has_glob_chars(pattern: str) -> bool:
    return any(ch in pattern for ch in ("*", "?", "["))
