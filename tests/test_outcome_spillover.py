"""Tests for outcome spillover-on-truncate (bd-8028 / bd-f57d).

When an agent produces an outcome longer than ``max_outcome_length``, the
launcher must:

1. Persist the **full** redacted text to ``<execution_dir>/<spillover_dir>/``.
2. Replace the inline ``LaunchResult.outcome`` with a breadcrumb header
   plus the first ``max_outcome_length - 200`` chars (so downstream tooling
   that ignores the spillover field still sees the leading content).
3. Populate ``LaunchResult.outcome_spillover_path`` with the relative path.

Downstream, ``ExecutionEngine`` must read the spillover file when building
the next step's "Previous Step Output" section so the implementer agent
sees the substantive design rather than the breadcrumb.

Test scope:
- Round-trip: a 30 KB stdout produces breadcrumb + on-disk file; a 100-char
  stdout produces legacy behavior with no file written.
- Dispatcher uses spillover content (not the truncated breadcrumb) when
  the prior StepResult records a spillover path.
- Idempotency: re-running the same step writes a *new* timestamped file
  (no overwrite, no error).
- Best-effort cleanup: spillover writes failing (read-only dir) fall back
  to legacy truncation without raising.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

import agent_baton.core.runtime.claude_launcher as _launcher_mod
from agent_baton.core.engine.executor import (
    _HANDOFF_SPILLOVER_MAX_BYTES,
    _SPILLOVER_BREADCRUMB_RE,
    ExecutionEngine,
)
from agent_baton.core.runtime.claude_launcher import (
    ClaudeCodeConfig,
    ClaudeCodeLauncher,
    _SPILLOVER_BREADCRUMB_HEADROOM,
    _truncate_or_spillover,
)
from agent_baton.models.execution import StepResult


# ---------------------------------------------------------------------------
# Patch helpers (mirror tests/test_claude_launcher.py)
# ---------------------------------------------------------------------------


class _FakeProcess:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:  # noqa: A002
        return self.stdout, self.stderr

    def kill(self) -> None: ...

    async def wait(self) -> None: ...


def _patch_which(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/claude")


def _patch_subprocess(monkeypatch: pytest.MonkeyPatch, proc: _FakeProcess) -> None:
    async def fake_exec(*args, **kwargs):
        return proc
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)


def _ok_json(result_text: str) -> bytes:
    return json.dumps({
        "result": result_text,
        "is_error": False,
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "duration_ms": 1000,
    }).encode()


# ---------------------------------------------------------------------------
# _truncate_or_spillover unit tests
# ---------------------------------------------------------------------------


class TestTruncateOrSpillover:
    def test_short_text_no_spillover(self, tmp_path: Path) -> None:
        cfg = ClaudeCodeConfig(execution_dir=tmp_path, max_outcome_length=4000)
        outcome, rel = _truncate_or_spillover(
            raw_text="hello world", step_id="1.1", config=cfg
        )
        assert outcome == "hello world"
        assert rel == ""
        # No spillover dir should have been created.
        assert not (tmp_path / cfg.outcome_spillover_dir_relative).exists()

    def test_just_under_threshold_no_spillover(self, tmp_path: Path) -> None:
        cfg = ClaudeCodeConfig(execution_dir=tmp_path, max_outcome_length=4000)
        threshold = cfg.max_outcome_length - _SPILLOVER_BREADCRUMB_HEADROOM
        text = "x" * threshold
        outcome, rel = _truncate_or_spillover(
            raw_text=text, step_id="1.1", config=cfg
        )
        assert outcome == text
        assert rel == ""

    def test_large_text_writes_spillover_and_breadcrumb(self, tmp_path: Path) -> None:
        cfg = ClaudeCodeConfig(execution_dir=tmp_path, max_outcome_length=4000)
        big = "A" * 30_000  # ~30 KB
        outcome, rel = _truncate_or_spillover(
            raw_text=big, step_id="1.1", config=cfg
        )
        # Breadcrumb structure
        assert outcome.startswith("[TRUNCATED — full output:")
        assert f"({len(big.encode('utf-8'))} bytes total)]" in outcome
        # Length: breadcrumb prefix + head chars (max_outcome_length - 200).
        head_chars = cfg.max_outcome_length - _SPILLOVER_BREADCRUMB_HEADROOM
        assert outcome.endswith("A" * 16) or (head_chars > 16 and "A" * 100 in outcome)
        # Relative path matches breadcrumb regex.
        m = _SPILLOVER_BREADCRUMB_RE.match(outcome)
        assert m is not None
        assert m.group(1) == rel
        # File exists with the FULL untruncated text.
        spill_file = tmp_path / rel
        assert spill_file.exists()
        assert spill_file.read_text(encoding="utf-8") == big

    def test_step_id_unsafe_chars_sanitized(self, tmp_path: Path) -> None:
        cfg = ClaudeCodeConfig(execution_dir=tmp_path, max_outcome_length=4000)
        outcome, rel = _truncate_or_spillover(
            raw_text="Z" * 30_000, step_id="phase 0/step:1", config=cfg
        )
        assert rel != ""
        # No raw '/' or ':' or ' ' in the file portion of the path.
        fname = rel.split("/")[-1]
        assert "/" not in fname and ":" not in fname and " " not in fname

    def test_idempotent_distinct_files_per_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = ClaudeCodeConfig(execution_dir=tmp_path, max_outcome_length=4000)
        # Drive the timestamp clock forward between calls so the filenames differ.
        # time.gmtime is what the launcher reads (via time.strftime).
        seq = [
            time.struct_time((2026, 4, 25, 12, 0, 0, 5, 115, 0)),
            time.struct_time((2026, 4, 25, 12, 0, 1, 5, 115, 0)),
        ]
        idx = {"i": 0}

        def fake_gmtime(_=None):
            t = seq[idx["i"]]
            idx["i"] += 1
            return t

        monkeypatch.setattr(_launcher_mod.time, "gmtime", fake_gmtime)

        out1, rel1 = _truncate_or_spillover(
            raw_text="Q" * 30_000, step_id="2.0", config=cfg
        )
        out2, rel2 = _truncate_or_spillover(
            raw_text="R" * 30_000, step_id="2.0", config=cfg
        )
        assert rel1 != rel2  # distinct filenames per call
        assert (tmp_path / rel1).read_text(encoding="utf-8") == "Q" * 30_000
        assert (tmp_path / rel2).read_text(encoding="utf-8") == "R" * 30_000

    def test_unwritable_dir_falls_back_to_truncation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = ClaudeCodeConfig(execution_dir=tmp_path, max_outcome_length=4000)

        def boom(self, *args, **kwargs):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(Path, "mkdir", boom)
        outcome, rel = _truncate_or_spillover(
            raw_text="X" * 30_000, step_id="3.1", config=cfg
        )
        # Best-effort: legacy hard truncation, no spillover path returned.
        assert rel == ""
        assert len(outcome) == cfg.max_outcome_length
        assert outcome == "X" * cfg.max_outcome_length

    def test_no_execution_dir_skips_spillover(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Strip env so _resolve_execution_dir returns None.
        monkeypatch.delenv("BATON_TASK_ID", raising=False)
        monkeypatch.delenv("BATON_TEAM_CONTEXT_ROOT", raising=False)
        cfg = ClaudeCodeConfig(execution_dir=None, max_outcome_length=4000)
        outcome, rel = _truncate_or_spillover(
            raw_text="Y" * 30_000, step_id="4.0", config=cfg
        )
        assert rel == ""
        assert len(outcome) == cfg.max_outcome_length


# ---------------------------------------------------------------------------
# Launcher integration: 30 KB stdout end-to-end
# ---------------------------------------------------------------------------


class TestLauncherEndToEnd:
    def test_30kb_stdout_produces_spillover_and_breadcrumb(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch_which(monkeypatch)
        big = "DESIGN " * 5000  # ~35 KB
        _patch_subprocess(monkeypatch, _FakeProcess(stdout=_ok_json(big)))

        cfg = ClaudeCodeConfig(
            execution_dir=tmp_path,
            max_outcome_length=4000,
            working_directory=tmp_path,
        )
        launcher = ClaudeCodeLauncher(cfg)
        result = asyncio.run(launcher.launch(
            agent_name="architect",
            model="opus",
            prompt="Design Phase 0 schema migration.",
            step_id="0.1",
        ))

        assert result.status == "complete"
        assert result.outcome_spillover_path != ""
        assert result.outcome.startswith("[TRUNCATED — full output:")
        # Full file written with full text.
        spill = tmp_path / result.outcome_spillover_path
        assert spill.exists()
        assert spill.read_text(encoding="utf-8") == big

    def test_short_stdout_no_spillover(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch_which(monkeypatch)
        _patch_subprocess(monkeypatch, _FakeProcess(stdout=_ok_json("hi")))
        cfg = ClaudeCodeConfig(
            execution_dir=tmp_path,
            max_outcome_length=4000,
            working_directory=tmp_path,
        )
        launcher = ClaudeCodeLauncher(cfg)
        result = asyncio.run(launcher.launch(
            agent_name="architect",
            model="opus",
            prompt="Tiny task.",
            step_id="0.2",
        ))
        assert result.status == "complete"
        assert result.outcome == "hi"
        assert result.outcome_spillover_path == ""
        # No spillover dir created.
        assert not (tmp_path / cfg.outcome_spillover_dir_relative).exists()


# ---------------------------------------------------------------------------
# Dispatcher / handoff round-trip
# ---------------------------------------------------------------------------


class TestHandoffSpilloverRoundTrip:
    def test_engine_loads_spillover_file_for_handoff(self, tmp_path: Path) -> None:
        # Set up the canonical execution dir layout.
        task_id = "task-abc"
        team_root = tmp_path / "team-context"
        exec_dir = team_root / "executions" / task_id
        spill_rel = "outcome-spillover/step-0.1-design.md"
        spill_file = exec_dir / spill_rel
        spill_file.parent.mkdir(parents=True, exist_ok=True)
        full_design = "## Phase 0 Schema Migration Design\n\n" + ("Detail line.\n" * 500)
        spill_file.write_text(full_design, encoding="utf-8")

        # Build engine pointed at this team root.
        engine = ExecutionEngine(team_context_root=team_root, task_id=task_id)

        # Construct a StepResult with truncated outcome + spillover_path.
        breadcrumb = (
            f"[TRUNCATED — full output: {spill_rel} "
            f"({len(full_design.encode('utf-8'))} bytes total)]\n\n"
            f"--- First 100 chars ---\n## Phase 0 Schema..."
        )
        sr = StepResult(
            step_id="0.1",
            agent_name="architect",
            status="complete",
            outcome=breadcrumb,
            outcome_spillover_path=spill_rel,
        )

        loaded = engine._load_handoff_outcome(sr)
        # Must contain the substantive design, NOT the breadcrumb.
        assert "Phase 0 Schema Migration Design" in loaded
        assert "Detail line." in loaded
        assert "TRUNCATED" not in loaded.split("\n", 1)[0]

    def test_engine_falls_back_when_spillover_missing(self, tmp_path: Path) -> None:
        task_id = "task-xyz"
        team_root = tmp_path / "team-context"
        (team_root / "executions" / task_id).mkdir(parents=True)
        engine = ExecutionEngine(team_context_root=team_root, task_id=task_id)
        sr = StepResult(
            step_id="0.1",
            agent_name="architect",
            status="complete",
            outcome="legacy outcome",
            outcome_spillover_path="outcome-spillover/missing.md",
        )
        # Missing file → fall back to inline outcome silently.
        assert engine._load_handoff_outcome(sr) == "legacy outcome"

    def test_engine_passes_through_when_no_spillover(self, tmp_path: Path) -> None:
        engine = ExecutionEngine(
            team_context_root=tmp_path, task_id="t1"
        )
        sr = StepResult(
            step_id="0.1",
            agent_name="architect",
            status="complete",
            outcome="all the work",
        )
        assert engine._load_handoff_outcome(sr) == "all the work"

    def test_engine_caps_oversize_spillover(self, tmp_path: Path) -> None:
        task_id = "task-big"
        team_root = tmp_path / "team-context"
        exec_dir = team_root / "executions" / task_id
        spill_rel = "outcome-spillover/huge.md"
        (exec_dir / "outcome-spillover").mkdir(parents=True)
        # Twice the handoff cap.
        big = "Z" * (_HANDOFF_SPILLOVER_MAX_BYTES * 2)
        (exec_dir / spill_rel).write_text(big, encoding="utf-8")

        engine = ExecutionEngine(team_context_root=team_root, task_id=task_id)
        sr = StepResult(
            step_id="0.1",
            agent_name="architect",
            status="complete",
            outcome="(breadcrumb)",
            outcome_spillover_path=spill_rel,
        )
        loaded = engine._load_handoff_outcome(sr)
        # Capped: starts with the cap-note, then content.
        assert loaded.startswith("[Spillover capped at")
        # Does not exceed cap by more than the leading note's length.
        assert len(loaded.encode("utf-8")) <= _HANDOFF_SPILLOVER_MAX_BYTES + 256


# ---------------------------------------------------------------------------
# StepResult model: serialization
# ---------------------------------------------------------------------------


class TestStepResultSpilloverField:
    def test_to_dict_round_trip_carries_spillover(self) -> None:
        sr = StepResult(
            step_id="1.1",
            agent_name="architect",
            outcome="(truncated)",
            outcome_spillover_path="outcome-spillover/step-1.1-foo.md",
        )
        d = sr.to_dict()
        assert d["outcome_spillover_path"] == "outcome-spillover/step-1.1-foo.md"
        sr2 = StepResult.from_dict(d)
        assert sr2.outcome_spillover_path == "outcome-spillover/step-1.1-foo.md"

    def test_legacy_dict_without_field_deserializes(self) -> None:
        # Backwards-compat: existing rows have no spillover key.
        legacy = {
            "step_id": "1.1",
            "agent_name": "architect",
            "status": "complete",
            "outcome": "old outcome",
            "files_changed": [],
            "commit_hash": "",
            "estimated_tokens": 100,
        }
        sr = StepResult.from_dict(legacy)
        assert sr.outcome_spillover_path == ""


# ---------------------------------------------------------------------------
# record_step_result auto-detect path from breadcrumb
# ---------------------------------------------------------------------------


class TestRecordStepResultAutoDetect:
    def test_record_extracts_spillover_path_from_breadcrumb(
        self, tmp_path: Path
    ) -> None:
        """Legacy callers that don't pass outcome_spillover_path still get
        the spillover wired up, because record_step_result parses the
        breadcrumb prefix."""
        from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep

        plan = MachinePlan(
            task_id="t-auto",
            task_summary="auto-detect test",
            phases=[
                PlanPhase(
                    phase_id="p0",
                    name="phase",
                    steps=[
                        PlanStep(
                            step_id="0.1",
                            agent_name="architect",
                            task_description="design",
                        )
                    ],
                )
            ],
        )

        engine = ExecutionEngine(team_context_root=tmp_path, task_id="t-auto")
        engine.start(plan)

        breadcrumb_outcome = (
            "[TRUNCATED — full output: outcome-spillover/step-0.1-foo.md "
            "(30000 bytes total)]\n\n--- First 3800 chars ---\n## Design..."
        )
        engine.record_step_result(
            step_id="0.1",
            agent_name="architect",
            status="complete",
            outcome=breadcrumb_outcome,
        )
        state = engine._require_execution("test")
        sr = state.get_step_result("0.1")
        assert sr is not None
        assert sr.outcome_spillover_path == "outcome-spillover/step-0.1-foo.md"
