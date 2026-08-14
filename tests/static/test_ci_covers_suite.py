"""Meta-test — CI must actually execute the whole pytest suite.

Both ``CLAUDE.md`` and ``tests/CLAUDE.md`` tell agents *not* to run the full
suite locally, on the stated grounds that "the full suite is gated to
maintainers + CI".  That instruction is only safe if CI really does execute
every test.  If CI executes a hand-maintained allowlist instead, whole
subsystems (engine state machine, governance/evidence bundles, planner, API,
CLI) are never exercised by any automated actor and regressions merge green.

This test closes that loop.  It asserts a *behavioural* property of the
repository's CI configuration:

    every test file that ``pytest`` actually collects must be executed by at
    least one pull-request-gating GitHub Actions job.

The check is deliberately not a source-substring assertion.  It:

1. asks pytest itself which test files exist (a real ``--collect-only`` run,
   so files excluded via ``collect_ignore_glob`` are correctly not required);
2. parses every workflow YAML under ``.github/workflows/`` that runs on
   ``pull_request``, expands ``strategy.matrix`` placeholders, and extracts the
   path arguments / ``--ignore`` arguments of every *executing* pytest
   invocation (``--collect-only`` steps do not count — they never run a test);
3. reports which top-level entries under ``tests/`` no job covers.

Consequently any of these fixes satisfy it: one bare ``pytest`` job, a
matrix-sharded set of jobs, or an explicit list of directories that happens to
be exhaustive.  And it fails the moment somebody adds ``tests/<newdir>/``
without wiring it into CI, or narrows an existing job with ``--ignore=``.
"""
from __future__ import annotations

import functools
import itertools
import re
import shlex
import subprocess
import sys
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW_DIR = _REPO_ROOT / ".github" / "workflows"
_TESTS_ROOT_NAME = "tests"

# Top-level entries under tests/ that CI is permitted to leave unexecuted.
#
# This MUST stay empty, or hold at most a couple of entries with a written
# justification right here.  It is not a dumping ground: emptying it is the
# whole point of the check.  Widening it to silence a failure defeats the
# guarantee that CLAUDE.md's "the full suite is gated to CI" claim depends on.
CI_EXEMPT_TEST_PATHS: frozenset[str] = frozenset()

# Placeholder substituted for GitHub Actions expressions we cannot evaluate
# (``${{ env.X }}``, ``${{ secrets.Y }}``).  Chosen so it can never look like a
# path under tests/.
_UNRESOLVED_EXPR = "__GHA_EXPR__"

# pytest flags that mean "collect but do not execute".
_COLLECT_ONLY_FLAGS = {"--co", "--collect-only"}


# ---------------------------------------------------------------------------
# What the suite actually contains (asked of pytest, not of the filesystem)
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _collected_test_files() -> frozenset[str]:
    """Return every test file pytest collects, as repo-relative posix paths.

    Uses a real collection run so that files pytest deliberately skips (e.g.
    ``tests/fixtures/medium_project_repo`` via ``collect_ignore_glob``) are not
    demanded of CI.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    files: set[str] = set()
    for raw in proc.stdout.splitlines():
        line = raw.strip()
        if "::" not in line:
            continue
        candidate = line.split("::", 1)[0]
        if candidate.startswith(_TESTS_ROOT_NAME + "/") and candidate.endswith(".py"):
            files.add(candidate)
    if not files:
        raise AssertionError(
            "Could not determine the collected test set — `pytest --collect-only` "
            f"returned no node ids (exit {proc.returncode}).\n"
            f"stdout tail:\n{proc.stdout[-2000:]}\n"
            f"stderr tail:\n{proc.stderr[-2000:]}"
        )
    return frozenset(files)


def _top_level_entry(test_file: str) -> str:
    """``tests/engine/test_x.py`` -> ``tests/engine``; ``tests/test_y.py`` -> itself."""
    parts = test_file.split("/")
    return "/".join(parts[:2])


# ---------------------------------------------------------------------------
# Workflow parsing
# ---------------------------------------------------------------------------


class PytestInvocation:
    """One pytest command found in a workflow's ``run:`` script."""

    __slots__ = ("workflow", "job", "paths", "ignores", "collect_only", "command")

    def __init__(
        self,
        workflow: str,
        job: str,
        paths: frozenset[str],
        ignores: frozenset[str],
        collect_only: bool,
        command: str,
    ) -> None:
        self.workflow = workflow
        self.job = job
        self.paths = paths
        self.ignores = ignores
        self.collect_only = collect_only
        self.command = command

    @property
    def executes(self) -> bool:
        return not self.collect_only

    @property
    def is_whole_suite(self) -> bool:
        """True when no explicit path narrows the run (pytest walks testpaths)."""
        return not self.paths

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"<{self.workflow}:{self.job} paths={sorted(self.paths) or ['<all>']} "
            f"ignores={sorted(self.ignores)} collect_only={self.collect_only}>"
        )


def _workflow_triggers(doc: dict) -> set[str]:
    """Return the trigger names of a workflow.

    YAML 1.1 parses a bare ``on:`` key as the boolean ``True``, so accept both.
    """
    raw = doc.get("on", doc.get(True))
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list):
        return {str(item) for item in raw}
    if isinstance(raw, dict):
        return {str(key) for key in raw}
    return set()


def _matrix_substitutions(job: dict) -> list[dict[str, str]]:
    """Expand ``strategy.matrix`` scalar-list axes into placeholder mappings.

    A job sharded as ``matrix: {target: [tests/engine, tests/api]}`` running
    ``pytest ${{ matrix.target }}`` covers both directories; without expansion
    we would see an opaque expression and credit nothing.
    """
    strategy = job.get("strategy")
    if not isinstance(strategy, dict):
        return [{}]
    matrix = strategy.get("matrix")
    if not isinstance(matrix, dict):
        return [{}]

    axes: dict[str, list[str]] = {}
    for key, values in matrix.items():
        if key in {"include", "exclude"} or not isinstance(values, list):
            continue
        scalars = [str(v) for v in values if isinstance(v, (str, int, float, bool))]
        if scalars:
            axes[str(key)] = scalars

    # `include:` entries can also supply values used by the run script.
    include = matrix.get("include")
    extra_combos: list[dict[str, str]] = []
    if isinstance(include, list):
        for entry in include:
            if isinstance(entry, dict):
                extra_combos.append(
                    {
                        str(k): str(v)
                        for k, v in entry.items()
                        if isinstance(v, (str, int, float, bool))
                    }
                )

    combos: list[dict[str, str]] = []
    if axes:
        keys = sorted(axes)
        for values in itertools.product(*(axes[k] for k in keys)):
            combos.append(dict(zip(keys, values)))
    combos.extend(extra_combos)
    return combos or [{}]


_EXPR_RE = re.compile(r"\$\{\{([^}]*)\}\}")


def _apply_substitutions(script: str, mapping: dict[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        if expr.startswith("matrix."):
            key = expr.split(".", 1)[1].strip()
            if key in mapping:
                return mapping[key]
        return _UNRESOLVED_EXPR

    return _EXPR_RE.sub(repl, script)


def _split_shell_commands(script: str) -> list[str]:
    """Split a ``run:`` block into individual shell commands."""
    joined = re.sub(r"\\\s*\n", " ", script)
    parts = re.split(r"\n|;|&&|\|\|", joined)
    return [p.strip() for p in parts if p.strip()]


def _normalise_path_arg(arg: str) -> str:
    """Strip node-id suffixes and trailing slashes; return a repo-relative path."""
    path = arg.split("::", 1)[0].strip()
    path = path.rstrip("/")
    if path.startswith("./"):
        path = path[2:]
    return path


def _looks_like_test_path(arg: str) -> bool:
    path = _normalise_path_arg(arg)
    return path == _TESTS_ROOT_NAME or path.startswith(_TESTS_ROOT_NAME + "/")


def _parse_pytest_command(command: str) -> tuple[frozenset[str], frozenset[str], bool] | None:
    """Return ``(paths, ignores, collect_only)`` for a pytest command, else None."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    pytest_idx: int | None = None
    for idx, token in enumerate(tokens):
        if token == "pytest" or token.endswith("/pytest"):
            pytest_idx = idx
            break
    if pytest_idx is None:
        return None

    args = tokens[pytest_idx + 1 :]
    paths: set[str] = set()
    ignores: set[str] = set()
    collect_only = False

    idx = 0
    while idx < len(args):
        arg = args[idx]
        if arg in _COLLECT_ONLY_FLAGS:
            collect_only = True
        elif arg.startswith("--ignore=") or arg.startswith("--ignore-glob="):
            ignores.add(_normalise_path_arg(arg.split("=", 1)[1]))
        elif arg in {"--ignore", "--ignore-glob"} and idx + 1 < len(args):
            idx += 1
            ignores.add(_normalise_path_arg(args[idx]))
        elif arg == ".":
            paths.add("")  # rootdir — same reach as a bare invocation
        elif _looks_like_test_path(arg):
            normalised = _normalise_path_arg(arg)
            paths.add("" if normalised == _TESTS_ROOT_NAME else normalised)
        idx += 1

    if "" in paths:
        paths = {""}
    return frozenset(paths), frozenset(ignores), collect_only


def _invocations_from_doc(doc: object, source_name: str) -> list[PytestInvocation]:
    if not isinstance(doc, dict):
        return []
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return []

    found: list[PytestInvocation] = []
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        combos = _matrix_substitutions(job)
        for step in steps:
            if not isinstance(step, dict):
                continue
            script = step.get("run")
            if not isinstance(script, str) or "pytest" not in script:
                continue
            for mapping in combos:
                resolved = _apply_substitutions(script, mapping)
                for command in _split_shell_commands(resolved):
                    parsed = _parse_pytest_command(command)
                    if parsed is None:
                        continue
                    found.append(
                        PytestInvocation(
                            workflow=source_name,
                            job=str(job_name),
                            paths=parsed[0],
                            ignores=parsed[1],
                            collect_only=parsed[2],
                            command=command,
                        )
                    )
    return found


def _invocations_from_workflow(path: Path) -> list[PytestInvocation]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _invocations_from_doc(doc, path.name)


def _gating_workflow_files() -> list[Path]:
    """Workflow files that run on ``pull_request`` — i.e. that gate a merge."""
    if not _WORKFLOW_DIR.is_dir():
        return []
    gating: list[Path] = []
    for path in sorted(_WORKFLOW_DIR.glob("*.y*ml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and "pull_request" in _workflow_triggers(doc):
            gating.append(path)
    return gating


@functools.lru_cache(maxsize=1)
def _gating_pytest_invocations() -> tuple[PytestInvocation, ...]:
    invocations: list[PytestInvocation] = []
    for path in _gating_workflow_files():
        invocations.extend(_invocations_from_workflow(path))
    return tuple(invocations)


# ---------------------------------------------------------------------------
# Coverage model
# ---------------------------------------------------------------------------


def _selects(selector: str, test_file: str) -> bool:
    """Does path argument *selector* select *test_file*?

    An empty selector is a bare invocation (the whole ``testpaths`` tree).
    A directory selects everything beneath it; a file selects only itself.
    """
    if selector == "":
        return True
    return test_file == selector or test_file.startswith(selector + "/")


def _files_covered(
    invocations: tuple[PytestInvocation, ...], all_files: frozenset[str]
) -> set[str]:
    """Union of test files actually executed by the given invocations."""
    covered: set[str] = set()
    for inv in invocations:
        if not inv.executes:
            continue
        selectors = inv.paths or {""}
        for test_file in all_files:
            if test_file in covered:
                continue
            if not any(_selects(sel, test_file) for sel in selectors):
                continue
            if any(_selects(ign, test_file) for ign in inv.ignores):
                continue
            covered.add(test_file)
    return covered


def _uncovered_top_level_entries() -> tuple[set[str], set[str]]:
    all_files = _collected_test_files()
    covered = _files_covered(_gating_pytest_invocations(), all_files)
    uncovered_files = set(all_files) - covered
    return {_top_level_entry(f) for f in uncovered_files}, uncovered_files


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCiExecutesTheWholeSuite:
    """CLAUDE.md forbids local full-suite runs because 'CI gates it'. Verify that."""

    def test_at_least_one_gating_workflow_executes_tests(self) -> None:
        """A ``--collect-only`` step is not a test run — something must execute."""
        invocations = _gating_pytest_invocations()
        assert invocations, (
            "No pull_request-triggered workflow under .github/workflows/ invokes "
            "pytest at all — nothing gates a merge."
        )
        executing = [inv for inv in invocations if inv.executes]
        assert executing, (
            "Every pytest step in the gating workflows is collection-only "
            "(--co/--collect-only); no test is actually executed on a pull request.\n"
            "Found:\n  " + "\n  ".join(repr(inv) for inv in invocations)
        )

    def test_every_collected_test_directory_is_executed_by_ci(self) -> None:
        """No top-level entry under tests/ may be left out of pull-request CI.

        Fails when CI runs a hand-maintained allowlist, when a new
        ``tests/<dir>/`` is added without wiring it into a job, or when a job is
        narrowed with ``--ignore=``.
        """
        uncovered_entries, uncovered_files = _uncovered_top_level_entries()
        offenders = uncovered_entries - CI_EXEMPT_TEST_PATHS
        all_files = _collected_test_files()
        executed = len(all_files) - len(uncovered_files)
        shown = sorted(offenders)[:40]
        elided = len(offenders) - len(shown)
        assert not offenders, (
            "CI does not execute the whole test suite.\n"
            f"Test files executed by pull-request CI: {executed} of {len(all_files)} "
            f"({100.0 * executed / len(all_files):.1f}%).\n"
            f"{len(offenders)} top-level entries under tests/ are never executed:\n  "
            + "\n  ".join(shown)
            + (f"\n  ... and {elided} more" if elided else "")
            + "\n\nCLAUDE.md and tests/CLAUDE.md instruct agents not to run the full "
            "suite locally because 'the full suite is gated to maintainers + CI'. "
            "Make that true: run the whole suite in a pull-request job (shard it "
            "with a matrix or xdist if runtime is the concern), or correct the docs "
            "and justify each entry in CI_EXEMPT_TEST_PATHS.\n"
            "Gating pytest invocations seen:\n  "
            + "\n  ".join(repr(inv) for inv in _gating_pytest_invocations())
        )

    def test_exemption_list_stays_small(self) -> None:
        """Guard the escape hatch: it must not become the new allowlist."""
        assert len(CI_EXEMPT_TEST_PATHS) <= 2, (
            "CI_EXEMPT_TEST_PATHS has grown into a de-facto allowlist "
            f"({len(CI_EXEMPT_TEST_PATHS)} entries). Exempting directories from CI "
            "re-creates the defect this test exists to prevent."
        )


class TestWorkflowParser:
    """Self-tests — prove the parser credits and discredits the right things."""

    @staticmethod
    def _parse(script: str, job: dict | None = None) -> list[PytestInvocation]:
        doc = {
            "on": {"pull_request": None},
            "jobs": {"j": {**(job or {}), "steps": [{"run": script}]}},
        }
        return _invocations_from_doc(doc, "synthetic.yml")

    def test_bare_invocation_covers_every_test_file(self) -> None:
        invocations = tuple(self._parse("python -m pytest -q"))
        files = frozenset({"tests/engine/test_a.py", "tests/test_b.py"})
        assert _files_covered(invocations, files) == set(files)

    def test_collect_only_step_covers_nothing(self) -> None:
        invocations = tuple(self._parse("python -m pytest --co -q"))
        files = frozenset({"tests/engine/test_a.py"})
        assert invocations and all(not inv.executes for inv in invocations)
        assert _files_covered(invocations, files) == set()

    def test_explicit_allowlist_covers_only_listed_paths(self) -> None:
        invocations = tuple(
            self._parse(
                "python -m pytest -q \\\n  tests/storage/ \\\n  tests/test_b.py\n"
            )
        )
        files = frozenset(
            {
                "tests/storage/test_s.py",
                "tests/test_b.py",
                "tests/engine/test_a.py",
            }
        )
        assert _files_covered(invocations, files) == {
            "tests/storage/test_s.py",
            "tests/test_b.py",
        }

    def test_file_level_selector_does_not_cover_its_whole_directory(self) -> None:
        invocations = tuple(self._parse("pytest -q tests/static/test_one.py"))
        files = frozenset({"tests/static/test_one.py", "tests/static/test_two.py"})
        assert _files_covered(invocations, files) == {"tests/static/test_one.py"}

    def test_ignore_flag_removes_coverage(self) -> None:
        invocations = tuple(self._parse("pytest -q --ignore=tests/e2e"))
        files = frozenset({"tests/e2e/test_x.py", "tests/engine/test_a.py"})
        assert _files_covered(invocations, files) == {"tests/engine/test_a.py"}

    def test_xdist_and_filter_flags_are_not_mistaken_for_paths(self) -> None:
        invocations = tuple(self._parse("pytest -q -n auto -p no:cacheprovider"))
        assert invocations[0].is_whole_suite
        files = frozenset({"tests/engine/test_a.py"})
        assert _files_covered(invocations, files) == set(files)

    def test_matrix_sharding_by_directory_is_credited(self) -> None:
        invocations = tuple(
            self._parse(
                "pytest -q ${{ matrix.target }}",
                job={"strategy": {"matrix": {"target": ["tests/engine", "tests/api"]}}},
            )
        )
        files = frozenset(
            {"tests/engine/test_a.py", "tests/api/test_b.py", "tests/cli/test_c.py"}
        )
        assert _files_covered(invocations, files) == {
            "tests/engine/test_a.py",
            "tests/api/test_b.py",
        }

    def test_unresolvable_expression_is_not_treated_as_a_test_path(self) -> None:
        invocations = tuple(self._parse("pytest -q ${{ env.TARGETS }}"))
        # An opaque expression must not silently count as "the whole suite"
        # being narrowed, nor as covering an arbitrary directory.
        assert invocations[0].paths == frozenset()

    def test_non_pull_request_workflows_are_not_credited(self, tmp_path: Path) -> None:
        wf = tmp_path / "nightly.yml"
        wf.write_text(
            yaml.safe_dump(
                {
                    "on": {"schedule": [{"cron": "0 0 * * *"}]},
                    "jobs": {"j": {"steps": [{"run": "pytest -q"}]}},
                }
            ),
            encoding="utf-8",
        )
        doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
        assert "pull_request" not in _workflow_triggers(doc)

    def test_yaml_boolean_on_key_is_understood(self) -> None:
        doc = yaml.safe_load("on:\n  pull_request:\n")
        assert "pull_request" in _workflow_triggers(doc)


def test_collected_test_set_is_plausible() -> None:
    """Sanity check on the collection probe so a silent empty set can't pass."""
    files = _collected_test_files()
    assert len(files) > 200, f"suspiciously few test files collected: {len(files)}"
    assert any(f.startswith("tests/engine/") for f in files)
    assert any(f.startswith("tests/govern/") for f in files)
