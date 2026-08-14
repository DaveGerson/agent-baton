"""Test-immutability control (F093).

The engine's ``test``/``build`` gates were historically a bare
``exit_code == 0`` check (see ``gates.py::GateRunner.evaluate_output``).
Nothing inspected the test corpus itself, so a dispatched specialist that
could not make a test pass could simply delete or gut it and the gate would
still report PASS -- every downstream control (``GateResult``, the
hash-chained compliance log, the evidence bundle, the auditor verdict) would
then faithfully record a lie.

This module supplies the missing control:

``capture_baseline(project_root)``
    Snapshot the repository's test corpus -- file digests, per-file case
    IDs, and a total collected-case count -- anchored to the current git
    commit.  The snapshot is taken from the filesystem/git state, **never**
    from an agent's self-reported ``--files`` list (see finding F097: that
    list is not evidence).

``verify_baseline(baseline, project_root, *, collected_count=None)``
    Compare the *current* on-disk state (and, optionally, an externally
    observed collected-test-count) against a previously captured baseline.
    Detects whole-file deletion, case removal within a file, and a run that
    collected fewer cases than the baseline promised.  A file whose digest
    changed but whose case-ID set is unchanged (a case body was rewritten,
    e.g. ``assert True``) is not silently passed -- it is surfaced via
    ``requires_approval`` so a human decides whether the edit is legitimate.

Growth is always allowed: adding new cases to a baselined file, or adding a
whole new test file, never fails verification.

Stack-agnostic: file *detection* covers Python (``test_*.py`` / ``*_test.py``)
and several other common stacks (Go, JS/TS, Rust, C#) by filename
convention.  Case-ID *extraction* is currently implemented for Python (via
``ast``) and Go (via a light regex over ``func TestXxx`` declarations); other
stacks degrade to digest-only tracking, which still detects whole-file
deletion and content changes.
"""
from __future__ import annotations

import ast
import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Test-file detection
# ---------------------------------------------------------------------------

# Directories that are never worth scanning for test files: VCS internals,
# dependency trees, and build/cache output.  Kept short and generic -- this
# is a filesystem-walk exclusion list, not a project-specific config.
_EXCLUDED_DIR_NAMES = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".tox",
    "dist", "build", ".mypy_cache", ".pytest_cache", ".eggs",
    "vendor", "target",
})

# Filename patterns recognised as test files, by stack.  Matched against
# the basename only.
_PY_TEST_RE = re.compile(r"^(test_.+\.py|.+_test\.py)$")
_GO_TEST_RE = re.compile(r"^.+_test\.go$")
_JS_TEST_RE = re.compile(r"^.+\.(test|spec)\.[jt]sx?$")
_RUST_TEST_RE = re.compile(r"^.+_test\.rs$")
_CS_TEST_RE = re.compile(r"^.+Tests?\.cs$")

_TEST_FILE_PATTERNS = (_PY_TEST_RE, _GO_TEST_RE, _JS_TEST_RE, _RUST_TEST_RE, _CS_TEST_RE)


def _is_test_file(basename: str) -> bool:
    return any(p.match(basename) for p in _TEST_FILE_PATTERNS)


# ---------------------------------------------------------------------------
# Case-ID extraction
# ---------------------------------------------------------------------------


def _extract_python_case_ids(relpath: str, source: str) -> list[str]:
    """Return ``relpath::function_name`` for every test function/method.

    Walks module-level and class-level ``def``/``async def`` whose name
    starts with ``test`` -- the same convention pytest's own collector uses
    for plain function-based tests.  Parse failures degrade to an empty
    list (digest-only tracking still catches the file being touched).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    case_ids: list[str] = []

    def _walk(node: ast.AST, prefix: str = "") -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if child.name.startswith("test"):
                    case_ids.append(f"{relpath}::{prefix}{child.name}")
                # Don't descend into function bodies -- nested defs aren't
                # test cases.
            elif isinstance(child, ast.ClassDef):
                if child.name.startswith("Test"):
                    _walk(child, prefix=f"{child.name}::")
                else:
                    _walk(child, prefix=prefix)

    _walk(tree)
    return case_ids


_GO_TEST_FUNC_RE = re.compile(r"^\s*func\s+(Test\w+)\s*\(")


def _extract_go_case_ids(relpath: str, source: str) -> list[str]:
    return [
        f"{relpath}::{m.group(1)}"
        for line in source.splitlines()
        for m in [_GO_TEST_FUNC_RE.match(line)]
        if m
    ]


def _extract_case_ids(relpath: str, source: str) -> list[str]:
    """Dispatch case-ID extraction by file extension.

    Returns ``[]`` (digest-only mode) for stacks without an extractor --
    deletion of the whole file and content changes are still detected via
    the file digest; only per-case removal detection is degraded.
    """
    if relpath.endswith(".py"):
        return _extract_python_case_ids(relpath, source)
    if relpath.endswith(".go"):
        return _extract_go_case_ids(relpath, source)
    return []


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _run_git(project_root: Path, *args: str) -> str | None:
    """Run a git command in *project_root*; return stdout or None on failure."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _git_head_sha(project_root: Path) -> str:
    out = _run_git(project_root, "rev-parse", "HEAD")
    return out.strip() if out else ""


def _git_tracked_test_files(project_root: Path) -> list[str]:
    """Return git-tracked file paths (relative, posix) that look like tests.

    Uses ``git ls-files`` rather than a filesystem walk: it is fast
    regardless of repository size (backed by the git index, not a
    directory traversal) and naturally excludes ignored/untracked noise
    such as ``node_modules`` or build output.  Returns ``[]`` when the
    directory is not a git repository (or git is unavailable) -- callers
    treat that as "no baseline to capture", which is a safe/inert default.
    """
    out = _run_git(project_root, "ls-files")
    if out is None:
        return []
    return [
        line for line in out.splitlines()
        if line and _is_test_file(Path(line).name)
        and not any(part in _EXCLUDED_DIR_NAMES for part in Path(line).parts[:-1])
    ]


# ---------------------------------------------------------------------------
# TestBaseline
# ---------------------------------------------------------------------------


@dataclass
class TestBaseline:
    """A snapshot of the repository's test corpus, anchored to a commit.

    Attributes:
        commit_sha: The ``git rev-parse HEAD`` value at capture time.
            Empty when the project root is not a git repository -- the
            baseline is then inert (see ``capture_baseline``).
        test_file_digests: Relative POSIX path -> sha256 hex digest of
            file contents, for every recognised test file.
        per_file_case_ids: Relative POSIX path -> list of case IDs
            (``"<path>::<name>"``) found in that file.  Empty list for
            stacks without a case-ID extractor (digest-only mode).
        collected_count: Total case count across all baselined files.
    """

    commit_sha: str = ""
    test_file_digests: dict[str, str] = field(default_factory=dict)
    per_file_case_ids: dict[str, list[str]] = field(default_factory=dict)
    collected_count: int = 0

    def to_dict(self) -> dict:
        return {
            "commit_sha": self.commit_sha,
            "test_file_digests": dict(self.test_file_digests),
            "per_file_case_ids": {k: list(v) for k, v in self.per_file_case_ids.items()},
            "collected_count": self.collected_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TestBaseline":
        return cls(
            commit_sha=data.get("commit_sha", ""),
            test_file_digests=dict(data.get("test_file_digests", {})),
            per_file_case_ids={
                k: list(v) for k, v in data.get("per_file_case_ids", {}).items()
            },
            collected_count=int(data.get("collected_count", 0)),
        )


def capture_baseline(project_root: Path | str) -> TestBaseline:
    """Snapshot the test corpus under *project_root*, anchored to git HEAD.

    Enumerates git-tracked test files (see ``_git_tracked_test_files``),
    digests each one, and extracts per-file case IDs where a stack-specific
    extractor is available.  When *project_root* is not a git repository
    (or git is unavailable), returns a degenerate/empty baseline rather
    than raising -- ``verify_baseline`` against an empty baseline never
    fails, so callers can capture unconditionally without special-casing
    non-git contexts.

    Args:
        project_root: Repository root to snapshot.

    Returns:
        A populated :class:`TestBaseline`.
    """
    root = Path(project_root)
    commit_sha = _git_head_sha(root)
    digests: dict[str, str] = {}
    case_ids: dict[str, list[str]] = {}
    collected = 0

    for relpath in _git_tracked_test_files(root):
        abspath = root / relpath
        try:
            content = abspath.read_bytes()
        except OSError:
            continue
        digests[relpath] = hashlib.sha256(content).hexdigest()
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = ""
        cases = _extract_case_ids(relpath, text)
        case_ids[relpath] = cases
        collected += len(cases)

    return TestBaseline(
        commit_sha=commit_sha,
        test_file_digests=digests,
        per_file_case_ids=case_ids,
        collected_count=collected,
    )


# Matches pytest's "collected N item(s)" summary line.  Shared by
# GateRunner.evaluate_output and ExecutionEngine.record_gate_result so both
# entry points parse an observed collected-case count the same way.
_COLLECTED_COUNT_RE = re.compile(r"collected\s+(\d+)\s+item")


def parse_collected_count(output: str) -> int | None:
    """Extract the collected-case count from gate command output, if present.

    Returns ``None`` when the output doesn't contain a recognisable
    ``collected N items`` marker (e.g. a non-pytest stack) -- callers then
    skip the collected-count comparison and rely on the file-based checks
    in :func:`verify_baseline` alone.
    """
    match = _COLLECTED_COUNT_RE.search(output or "")
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


@dataclass
class TestIntegrityVerdict:
    """Result of comparing the current worktree against a ``TestBaseline``.

    Attributes:
        passed: ``False`` when a baselined file/case was removed, or a
            reported collected-count dropped below the baseline.
        violations: Human-readable descriptions of each problem found,
            suitable for inclusion in a ``GateResult.output``.
        removed_case_ids: Case IDs present in the baseline but absent from
            the current version of a still-existing file.
        missing_files: Baselined files that no longer exist on disk.
        modified_files: Baselined files whose content changed while their
            case-ID set stayed identical (a body rewrite -- e.g. weakening
            an assertion without deleting the case).  Not a hard failure
            on its own; surfaced via ``requires_approval`` instead.
        requires_approval: ``True`` when a baselined file was edited in a
            way that case-ID counting alone cannot judge (see
            ``modified_files``).  Deliberate, legitimate test edits should
            route through human approval rather than pass silently.
    """

    passed: bool = True
    violations: list[str] = field(default_factory=list)
    removed_case_ids: list[str] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    requires_approval: bool = False


def verify_baseline(
    baseline: TestBaseline,
    project_root: Path | str,
    *,
    collected_count: int | None = None,
) -> TestIntegrityVerdict:
    """Compare the current worktree at *project_root* against *baseline*.

    Iterates only the (small) set of files recorded in *baseline* -- this
    is deliberately O(baselined files), not O(repository size), so it is
    cheap to call on every gate check regardless of repo size.  Comparison
    is against the baseline snapshot, never against ``git HEAD``: a
    specialist that deletes a test file and then commits the deletion must
    still be caught (comparing worktree-to-HEAD would launder the
    deletion).

    Args:
        baseline: A previously captured :class:`TestBaseline`.
        project_root: Repository root to check against the baseline.
        collected_count: An externally observed collected-case count
            (e.g. parsed from ``pytest``'s ``collected N items`` output).
            When supplied and lower than ``baseline.collected_count``,
            verification fails even if every baselined file is untouched
            on disk -- this catches a deselect/filter trick that leaves
            the corpus intact but the *run* incomplete.

    Returns:
        A :class:`TestIntegrityVerdict`.
    """
    root = Path(project_root)
    verdict = TestIntegrityVerdict()

    for relpath, baseline_digest in baseline.test_file_digests.items():
        abspath = root / relpath
        if not abspath.exists():
            verdict.missing_files.append(relpath)
            verdict.removed_case_ids.extend(baseline.per_file_case_ids.get(relpath, []))
            verdict.violations.append(f"test file removed: {relpath}")
            continue

        try:
            content = abspath.read_bytes()
        except OSError:
            verdict.missing_files.append(relpath)
            verdict.violations.append(f"test file unreadable: {relpath}")
            continue

        current_digest = hashlib.sha256(content).hexdigest()
        if current_digest == baseline_digest:
            continue  # untouched

        # Content changed -- work out whether cases were removed, only
        # added, or the set is identical (a rewrite).
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = ""
        baseline_cases = set(baseline.per_file_case_ids.get(relpath, []))
        current_cases = set(_extract_case_ids(relpath, text))
        removed = baseline_cases - current_cases

        if removed:
            verdict.removed_case_ids.extend(sorted(removed))
            verdict.violations.append(
                f"{relpath}: removed case(s) {', '.join(sorted(removed))}"
            )
        elif baseline_cases and current_cases == baseline_cases:
            # Same case names, different bytes: a body was rewritten
            # (e.g. "assert True") without deleting the case.  Not a
            # silent pass -- route to human approval.
            verdict.modified_files.append(relpath)
            verdict.requires_approval = True
            verdict.violations.append(
                f"{relpath}: modified without changing its case IDs "
                "(possible test-weakening edit) -- requires approval"
            )
        # else: cases were only added (current is a superset) -- growth,
        # no violation.

    if collected_count is not None and collected_count < baseline.collected_count:
        verdict.violations.append(
            f"collected_count dropped: {collected_count} < baseline "
            f"{baseline.collected_count}"
        )

    has_hard_failure = bool(verdict.removed_case_ids or verdict.missing_files) or (
        collected_count is not None and collected_count < baseline.collected_count
    )
    verdict.passed = not has_hard_failure and not verdict.requires_approval
    return verdict
