"""Test-immutability control (F093).

The engine's ``test``/``build`` gates are a bare ``exit_code == 0`` check.
Nothing in the pipeline notices when the dispatched specialist *removed* the
tests it could not make pass.  A phase whose gate is
``pytest tests/govern/test_audit_chain.py`` can be "passed" by deleting that
file, and every downstream control -- ``GateResult``, the hash-chained
compliance log, the evidence bundle, the auditor verdict -- then faithfully
records a lie.

These tests pin the *behaviour* of a test-integrity control:

``capture_baseline`` / ``verify_baseline``
    Snapshot the repository's test corpus from **git** (never from an
    agent's self-reported ``--files`` list, see F097) and detect removal of
    whole files or of individual cases within a file.

``GateRunner.evaluate_output``
    When handed a baseline, a ``test`` gate must not pass on ``exit_code
    == 0`` alone if the test corpus shrank underneath it.

``ExecutionEngine.record_gate_result``
    End to end: an orchestrator that reports ``passed=True`` for a phase
    that deleted a baselined test file must have that gate recorded as
    FAIL.  This one needs no new API to express -- it is a pure behavioural
    assertion against today's engine.

Nothing here asserts on source text or on private helpers; every assertion
is on observable behaviour given a repository state.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from agent_baton.core.engine.gates import GateRunner
from agent_baton.models.execution import (
    ActionType,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
)

try:  # pragma: no cover - presence of the module is what is under test
    from agent_baton.core.engine import test_integrity as _integrity_mod
except ImportError:  # pragma: no cover
    _integrity_mod = None


_MISSING = (
    "F093: agent_baton.core.engine.test_integrity does not exist. There is no "
    "test-immutability control, so a dispatched specialist can delete or "
    "weaken the very tests its GATE checks and the gate still reports PASS."
)


def _integrity():
    """Return the test-integrity module, failing loudly when absent."""
    assert _integrity_mod is not None, _MISSING
    return _integrity_mod


# ---------------------------------------------------------------------------
# Git fixture helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    """Run a git command inside *repo* and return stdout (stripped)."""
    proc = subprocess.run(
        [
            "git",
            "-c", "user.email=baton@example.invalid",
            "-c", "user.name=baton-test",
            "-c", "commit.gpgsign=false",
            *args,
        ],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _commit_all(repo: Path, message: str = "wip") -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


ALPHA_SRC = '''\
def test_alpha_one():
    assert 1 + 1 == 2


def test_alpha_two():
    assert "a" < "b"


def test_alpha_three():
    assert [1] + [2] == [1, 2]
'''

# Same file with test_alpha_two and test_alpha_three removed.
ALPHA_SRC_GUTTED = '''\
def test_alpha_one():
    assert 1 + 1 == 2
'''

BETA_SRC = '''\
def test_beta_one():
    assert True


def test_beta_two():
    assert not False
'''

GAMMA_SRC = '''\
def test_gamma_one():
    assert set() == set()
'''


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A committed git repo with 3 test files / 6 test cases.

    Layout::

        <repo>/agent_baton/widget.py
        <repo>/tests/test_alpha.py   (3 cases)
        <repo>/tests/test_beta.py    (2 cases)
        <repo>/tests/test_gamma.py   (1 case)
    """
    if shutil.which("git") is None:  # pragma: no cover - environment guard
        pytest.skip("git is not available")

    root = tmp_path / "proj"
    (root / "tests").mkdir(parents=True)
    (root / "agent_baton").mkdir(parents=True)

    (root / "agent_baton" / "widget.py").write_text("def widget():\n    return 1\n")
    (root / "tests" / "test_alpha.py").write_text(ALPHA_SRC)
    (root / "tests" / "test_beta.py").write_text(BETA_SRC)
    (root / "tests" / "test_gamma.py").write_text(GAMMA_SRC)

    _git(root, "init", "-q", "-b", "main")
    _commit_all(root, "initial")
    return root


def _keys_ending(mapping: dict, suffix: str) -> list[str]:
    """Baseline keys are path-shaped; match on suffix so the control is free
    to store absolute, relative, or posix-normalised paths."""
    return [k for k in mapping if str(k).replace("\\", "/").endswith(suffix)]


def _mentions(strings, needle: str) -> bool:
    return any(needle in str(s) for s in strings)


# ---------------------------------------------------------------------------
# (a) capture_baseline -- the snapshot is real and git-derived
# ---------------------------------------------------------------------------


class TestCaptureBaseline:
    def test_baseline_records_every_test_file(self, repo: Path) -> None:
        baseline = _integrity().capture_baseline(repo)

        for name in ("tests/test_alpha.py", "tests/test_beta.py", "tests/test_gamma.py"):
            assert _keys_ending(baseline.test_file_digests, name), (
                f"Baseline is missing a digest for {name}; "
                f"got keys {sorted(baseline.test_file_digests)}"
            )

    def test_baseline_does_not_digest_non_test_sources(self, repo: Path) -> None:
        """Production sources are not part of the immutability contract."""
        baseline = _integrity().capture_baseline(repo)

        assert not _keys_ending(baseline.test_file_digests, "agent_baton/widget.py"), (
            "Baseline should snapshot test files only, not production sources; "
            f"got keys {sorted(baseline.test_file_digests)}"
        )

    def test_baseline_digests_differ_per_file(self, repo: Path) -> None:
        baseline = _integrity().capture_baseline(repo)
        digests = list(baseline.test_file_digests.values())

        assert all(d for d in digests), "Every baselined file needs a non-empty digest"
        assert len(set(digests)) == len(digests), (
            f"Distinct file contents must produce distinct digests; got {digests}"
        )

    def test_baseline_records_case_ids_per_file(self, repo: Path) -> None:
        baseline = _integrity().capture_baseline(repo)

        alpha_keys = _keys_ending(baseline.per_file_case_ids, "tests/test_alpha.py")
        assert alpha_keys, (
            "Baseline must record the case IDs of each test file; "
            f"got keys {sorted(baseline.per_file_case_ids)}"
        )
        cases = baseline.per_file_case_ids[alpha_keys[0]]
        for case in ("test_alpha_one", "test_alpha_two", "test_alpha_three"):
            assert _mentions(cases, case), (
                f"Expected case {case!r} in baselined case IDs; got {cases}"
            )

    def test_baseline_collected_count_matches_corpus(self, repo: Path) -> None:
        baseline = _integrity().capture_baseline(repo)

        assert baseline.collected_count == 6, (
            "Fixture repo has 6 test cases (3 + 2 + 1); "
            f"baseline reported {baseline.collected_count}"
        )

    def test_baseline_is_anchored_to_the_git_commit(self, repo: Path) -> None:
        """Provenance: the snapshot is taken against a real commit SHA.

        F097: an agent's self-reported ``--files`` list is not evidence.  The
        baseline must be anchored to the repository's own history.
        """
        expected = _git(repo, "rev-parse", "HEAD")
        baseline = _integrity().capture_baseline(repo)

        assert baseline.commit_sha, "Baseline must record the commit it was taken at"
        sha = baseline.commit_sha
        assert expected.startswith(sha) or sha.startswith(expected), (
            f"Baseline commit_sha {sha!r} does not match git HEAD {expected!r}"
        )


# ---------------------------------------------------------------------------
# (b) verify_baseline -- removal detection
# ---------------------------------------------------------------------------


class TestVerifyBaselineDetectsRemoval:
    def test_untouched_worktree_verifies_clean(self, repo: Path) -> None:
        mod = _integrity()
        baseline = mod.capture_baseline(repo)
        verdict = mod.verify_baseline(baseline, repo)

        assert verdict.passed is True, (
            f"Unmodified worktree must verify clean; violations={verdict.violations}"
        )
        assert not verdict.removed_case_ids
        assert not verdict.missing_files

    def test_deleted_cases_are_named_in_the_verdict(self, repo: Path) -> None:
        """The headline case: a specialist guts a test file it cannot satisfy."""
        mod = _integrity()
        baseline = mod.capture_baseline(repo)

        (repo / "tests" / "test_alpha.py").write_text(ALPHA_SRC_GUTTED)

        verdict = mod.verify_baseline(baseline, repo)

        assert verdict.passed is False, (
            "Removing 2 of 3 cases from a baselined test file must fail "
            "verification"
        )
        assert _mentions(verdict.removed_case_ids, "test_alpha_two"), (
            "Verdict must name the removed case test_alpha_two; "
            f"removed_case_ids={verdict.removed_case_ids}"
        )
        assert _mentions(verdict.removed_case_ids, "test_alpha_three"), (
            "Verdict must name the removed case test_alpha_three; "
            f"removed_case_ids={verdict.removed_case_ids}"
        )
        assert not _mentions(verdict.removed_case_ids, "test_alpha_one"), (
            "test_alpha_one survived and must not be reported as removed; "
            f"removed_case_ids={verdict.removed_case_ids}"
        )

    def test_deleted_test_file_is_detected(self, repo: Path) -> None:
        mod = _integrity()
        baseline = mod.capture_baseline(repo)

        (repo / "tests" / "test_gamma.py").unlink()

        verdict = mod.verify_baseline(baseline, repo)

        assert verdict.passed is False, (
            "Deleting a baselined test file must fail verification"
        )
        assert _mentions(verdict.missing_files, "test_gamma.py"), (
            f"Verdict must name the deleted file; missing_files={verdict.missing_files}"
        )

    def test_detection_survives_a_git_commit_of_the_deletion(self, repo: Path) -> None:
        """Committing the sabotage must not launder it.

        A specialist commits its own work; if verification only compared the
        worktree against HEAD the deletion would look like "current state".
        The baseline is the reference, not HEAD.
        """
        mod = _integrity()
        baseline = mod.capture_baseline(repo)

        (repo / "tests" / "test_gamma.py").unlink()
        _commit_all(repo, "remove flaky test")

        verdict = mod.verify_baseline(baseline, repo)

        assert verdict.passed is False, (
            "A committed deletion of a baselined test file must still fail "
            f"verification; violations={verdict.violations}"
        )
        assert _mentions(verdict.missing_files, "test_gamma.py")

    def test_adding_new_cases_is_allowed(self, repo: Path) -> None:
        """The control blocks shrinkage, not growth."""
        mod = _integrity()
        baseline = mod.capture_baseline(repo)

        (repo / "tests" / "test_alpha.py").write_text(
            ALPHA_SRC + "\n\ndef test_alpha_four():\n    assert True\n"
        )

        verdict = mod.verify_baseline(baseline, repo)

        assert verdict.passed is True, (
            "Adding a test case must not fail the integrity check; "
            f"violations={verdict.violations}"
        )

    def test_adding_a_whole_new_test_file_is_allowed(self, repo: Path) -> None:
        mod = _integrity()
        baseline = mod.capture_baseline(repo)

        (repo / "tests" / "test_delta.py").write_text(
            "def test_delta_one():\n    assert True\n"
        )

        verdict = mod.verify_baseline(baseline, repo)

        assert verdict.passed is True, (
            f"Adding a new test file must be allowed; violations={verdict.violations}"
        )

    def test_rewritten_case_body_is_surfaced_for_approval(self, repo: Path) -> None:
        """Weakening (not deleting) a case is the second half of F093.

        ``test_beta_two`` still exists, so case-ID counting alone cannot see
        it.  The edit must at minimum be surfaced as requiring human
        approval rather than sliding through silently.
        """
        mod = _integrity()
        baseline = mod.capture_baseline(repo)

        (repo / "tests" / "test_beta.py").write_text(
            "def test_beta_one():\n    assert True\n\n\n"
            "def test_beta_two():\n    assert True  # was: assert not False\n"
        )

        verdict = mod.verify_baseline(baseline, repo)

        assert verdict.requires_approval is True, (
            "An edit to a baselined test file that keeps the case IDs must "
            "still be surfaced for approval rather than passing silently; "
            f"verdict={verdict}"
        )
        assert _mentions(verdict.modified_files, "test_beta.py"), (
            f"Verdict must name the edited file; modified_files={verdict.modified_files}"
        )

    def test_collected_count_drop_fails_verification(self, repo: Path) -> None:
        """An observed collection count below the baseline is a failure.

        This is the ``collected 0 items / 12 deselected`` shape: the files
        may be untouched but the run under the gate exercised less than the
        baseline promised.
        """
        mod = _integrity()
        baseline = mod.capture_baseline(repo)

        verdict = mod.verify_baseline(baseline, repo, collected_count=2)

        assert verdict.passed is False, (
            f"Observed collection of 2 against a baseline of "
            f"{baseline.collected_count} must fail; violations={verdict.violations}"
        )

    def test_collected_count_at_or_above_baseline_passes(self, repo: Path) -> None:
        mod = _integrity()
        baseline = mod.capture_baseline(repo)

        assert mod.verify_baseline(baseline, repo, collected_count=6).passed is True
        assert mod.verify_baseline(baseline, repo, collected_count=9).passed is True


# ---------------------------------------------------------------------------
# (c) Stack-agnostic: the control is not pytest-only
# ---------------------------------------------------------------------------


class TestStackAgnostic:
    def test_go_test_files_are_baselined_and_deletion_detected(
        self, tmp_path: Path
    ) -> None:
        if shutil.which("git") is None:  # pragma: no cover - environment guard
            pytest.skip("git is not available")
        mod = _integrity()

        root = tmp_path / "goproj"
        root.mkdir()
        (root / "go.mod").write_text("module example.com/thing\n\ngo 1.21\n")
        (root / "thing.go").write_text("package thing\n\nfunc Thing() int { return 1 }\n")
        (root / "thing_test.go").write_text(
            'package thing\n\nimport "testing"\n\n'
            "func TestThingOne(t *testing.T) {}\n\n"
            "func TestThingTwo(t *testing.T) {}\n"
        )
        _git(root, "init", "-q", "-b", "main")
        _commit_all(root, "initial")

        baseline = mod.capture_baseline(root)

        assert _keys_ending(baseline.test_file_digests, "thing_test.go"), (
            "Test-file detection must not be Python-only; "
            f"got keys {sorted(baseline.test_file_digests)}"
        )

        (root / "thing_test.go").unlink()
        verdict = mod.verify_baseline(baseline, root)

        assert verdict.passed is False, (
            "Deleting a baselined Go test file must fail verification"
        )
        assert _mentions(verdict.missing_files, "thing_test.go")


# ---------------------------------------------------------------------------
# (d) GateRunner -- the gate itself must consult the baseline
# ---------------------------------------------------------------------------


def _test_gate(command: str = "pytest -q") -> PlanGate:
    return PlanGate(gate_type="test", command=command, description="Tests pass.")


class TestGateRunnerHonoursBaseline:
    def test_green_exit_still_fails_when_collection_shrank(self, repo: Path) -> None:
        """The assertion that fails today.

        ``pytest`` exited 0 but collected 2 of the 6 cases the baseline
        recorded.  Exit code alone says PASS; the gate must say FAIL.
        """
        baseline = _integrity().capture_baseline(repo)
        runner = GateRunner()

        result = runner.evaluate_output(
            _test_gate(),
            "collected 2 items\n\n2 passed in 0.11s\n",
            0,
            test_baseline=baseline,
            project_root=repo,
        )

        assert result.passed is False, (
            "A test gate that exits 0 while collecting 2 of a baselined 6 "
            f"cases must FAIL; got GateResult(passed={result.passed})"
        )

    def test_deselect_everything_trick_fails(self, repo: Path) -> None:
        baseline = _integrity().capture_baseline(repo)
        runner = GateRunner()

        result = runner.evaluate_output(
            _test_gate("pytest -q -k nothing_matches"),
            "collected 0 items / 6 deselected in 0.02s\n",
            0,
            test_baseline=baseline,
            project_root=repo,
        )

        assert result.passed is False, (
            "A test gate that collected nothing must not pass"
        )

    def test_deleted_test_file_fails_the_gate(self, repo: Path) -> None:
        """Git-derived, not ``files_changed``-derived.

        ``evaluate_output`` is never told which files the agent claims to
        have touched; the deletion is found on disk.
        """
        baseline = _integrity().capture_baseline(repo)
        (repo / "tests" / "test_beta.py").unlink()
        runner = GateRunner()

        result = runner.evaluate_output(
            _test_gate(),
            "collected 6 items\n\n6 passed in 0.30s\n",
            0,
            test_baseline=baseline,
            project_root=repo,
        )

        assert result.passed is False, (
            "Deleting a baselined test file must fail the test gate even when "
            "the reported command output looks green"
        )
        assert "test_beta.py" in result.output, (
            "The gate result must name what was removed so the failure is "
            f"actionable; output={result.output!r}"
        )

    def test_intact_corpus_still_passes(self, repo: Path) -> None:
        """Control: the baseline must not make honest gates fail."""
        baseline = _integrity().capture_baseline(repo)
        runner = GateRunner()

        result = runner.evaluate_output(
            _test_gate(),
            "collected 6 items\n\n6 passed in 0.30s\n",
            0,
            test_baseline=baseline,
            project_root=repo,
        )

        assert result.passed is True, (
            f"An intact test corpus must still pass; output={result.output!r}"
        )

    def test_failing_exit_code_still_fails(self, repo: Path) -> None:
        baseline = _integrity().capture_baseline(repo)
        runner = GateRunner()

        result = runner.evaluate_output(
            _test_gate(),
            "collected 6 items\n\n1 failed, 5 passed in 0.30s\n",
            1,
            test_baseline=baseline,
            project_root=repo,
        )

        assert result.passed is False

    def test_no_baseline_preserves_legacy_behaviour(self) -> None:
        """Backward compatibility: without a baseline nothing changes."""
        runner = GateRunner()

        assert runner.evaluate_output(_test_gate(), "collected 0 items", 0).passed is True
        assert runner.evaluate_output(_test_gate(), "boom", 1).passed is False


# ---------------------------------------------------------------------------
# (e) End to end through the engine
#     These need no new API to express -- they are behavioural assertions
#     against today's ExecutionEngine.
# ---------------------------------------------------------------------------


def _plan(task_id: str = "task-test-integrity") -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Implement the widget",
        risk_level="LOW",
        phases=[
            PlanPhase(
                phase_id=0,
                name="phase-0",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="implement the widget",
                        model="sonnet",
                    )
                ],
                gate=PlanGate(
                    gate_type="test",
                    command="pytest tests/ -q",
                    description="Tests pass.",
                ),
            )
        ],
    )


def _engine_for(repo: Path):
    from agent_baton.core.engine.executor import ExecutionEngine  # noqa: PLC0415

    ctx = repo / ".claude" / "team-context"
    ctx.mkdir(parents=True, exist_ok=True)
    return ExecutionEngine(team_context_root=ctx)


def _drive_to_gate(engine, files_changed: list[str]):
    action = engine.start(_plan())
    assert action.action_type == ActionType.DISPATCH, (
        f"Expected DISPATCH first; got {action.action_type}"
    )
    engine.record_step_result(
        step_id=action.step_id,
        agent_name=action.agent_name,
        status="complete",
        outcome="Done.",
        files_changed=files_changed,
        estimated_tokens=1000,
        duration_seconds=5.0,
    )
    gate_action = engine.next_action()
    assert gate_action.action_type == ActionType.GATE, (
        f"Expected GATE; got {gate_action.action_type}"
    )
    return gate_action


class TestEngineRecordsTamperedGateAsFailed:
    def test_deleted_test_file_forces_gate_fail(self, repo: Path) -> None:
        """The full F093 scenario.

        The specialist deletes ``tests/test_beta.py``, reports only
        ``agent_baton/widget.py`` in ``--files``, and the orchestrator
        dutifully records ``passed=True`` because pytest exited 0.  The
        engine must overrule it.
        """
        engine = _engine_for(repo)
        gate_action = _drive_to_gate(engine, ["agent_baton/widget.py"])

        # The specialist's actual (unreported) worktree change.
        (repo / "tests" / "test_beta.py").unlink()

        engine.record_gate_result(
            phase_id=gate_action.phase_id,
            passed=True,
            output="collected 4 items\n\n4 passed in 0.20s\n",
            command="pytest tests/ -q",
            exit_code=0,
        )

        state = engine._load_state()
        assert state is not None
        assert state.gate_results, "A gate result should have been recorded"
        recorded = state.gate_results[-1]
        assert recorded.passed is False, (
            "The engine must record FAIL for a gate whose phase removed a "
            "baselined test file, regardless of the reported pass and "
            "regardless of files_changed"
        )

    def test_honest_phase_still_records_pass(self, repo: Path) -> None:
        """Control: no tampering, no false alarm."""
        engine = _engine_for(repo)
        gate_action = _drive_to_gate(engine, ["agent_baton/widget.py"])

        (repo / "agent_baton" / "widget.py").write_text("def widget():\n    return 2\n")

        engine.record_gate_result(
            phase_id=gate_action.phase_id,
            passed=True,
            output="collected 6 items\n\n6 passed in 0.30s\n",
            command="pytest tests/ -q",
            exit_code=0,
        )

        state = engine._load_state()
        assert state is not None
        recorded = state.gate_results[-1]
        assert recorded.passed is True, (
            "An untampered phase must still record a passing gate; "
            f"output={recorded.output!r}"
        )
