"""Tests for the immune Sweeper / FindingTriage → agent-launcher contract (F067).

These tests exercise the immune sweep path against the *real*
:class:`~agent_baton.core.runtime.claude_launcher.ClaudeCodeLauncher` — the
launcher that ``baton daemon-immune`` actually wires in — with only the
subprocess boundary faked.  The existing immune tests inject a bare
``MagicMock()`` launcher, which accepts any call shape and therefore cannot
detect a wiring mismatch between the sweeper and the launcher's real API.

Behaviour pinned here:

1. ``Sweeper.sweep()`` returns a :class:`SweepFinding` when the sweep agent
   reports ``found=true`` — i.e. the launch is actually performed (correct
   arguments, coroutine driven to completion) and its result is parsed.
2. ``Sweeper.sweep()`` still returns ``None`` when the agent reports
   ``found=false`` (a clean target must not be turned into a finding).
3. The keyword arguments the sweeper/triage pass to ``launch()`` remain
   bindable against the launcher's real signature, so a future signature
   change fails the suite instead of silently disabling every sweep.
4. ``FindingTriage`` auto-fix dispatch actually launches the
   ``immune-autofix`` agent, and does *not* book immune spend when the
   launch fails.
"""
from __future__ import annotations

import contextlib
import inspect
import json
import shutil as _real_shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_baton.core.immune.cache import ContextCache
from agent_baton.core.immune.daemon import ImmuneConfig
from agent_baton.core.immune.scheduler import SweepTarget
from agent_baton.core.immune.sweeper import SweepFinding, Sweeper
from agent_baton.core.immune.triage import FindingTriage
from agent_baton.core.runtime import claude_launcher as _launcher_mod
from agent_baton.core.runtime.claude_launcher import ClaudeCodeConfig, ClaudeCodeLauncher
from agent_baton.core.runtime.launcher import LaunchResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeProcess:
    """Stand-in for ``asyncio.subprocess.Process`` (the only faked boundary)."""

    def __init__(self, stdout: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self, input: bytes | None = None):  # noqa: A002
        return self._stdout, b""

    def kill(self) -> None:  # pragma: no cover - not exercised
        return None

    async def wait(self) -> int:  # pragma: no cover - not exercised
        return self.returncode


def _install_real_launcher(
    monkeypatch: pytest.MonkeyPatch,
    working_dir: Path,
    agent_json: str,
) -> tuple[ClaudeCodeLauncher, list[list[str]]]:
    """Build a real ``ClaudeCodeLauncher`` with only the subprocess faked.

    Returns the launcher and a list that records every spawned argv, so a
    test can assert the agent was genuinely launched.
    """
    def _which(name: str, *a: object, **kw: object) -> str | None:
        if name in ("claude", "git"):
            return f"/usr/bin/{name}"
        return _real_shutil.which(name)

    monkeypatch.setattr(_launcher_mod.shutil, "which", _which)
    monkeypatch.setattr(_launcher_mod, "_supports_exclude_flag", lambda: False)

    spawned: list[list[str]] = []

    async def _fake_exec(*cmd: str, **kwargs: object) -> _FakeProcess:
        argv = [str(c) for c in cmd]
        spawned.append(argv)
        if "rev-parse" in argv or "diff" in argv:
            # git probes — no commits, keeps the post-launch git block inert.
            return _FakeProcess(b"")
        envelope = json.dumps(
            {
                "result": agent_json,
                "is_error": False,
                "usage": {"input_tokens": 120, "output_tokens": 40},
                "duration_ms": 1234,
            }
        )
        return _FakeProcess(envelope.encode())

    monkeypatch.setattr(_launcher_mod.asyncio, "create_subprocess_exec", _fake_exec)

    launcher = ClaudeCodeLauncher(ClaudeCodeConfig(working_directory=working_dir))
    return launcher, spawned


class _RecordingLauncher:
    """Launcher double with the real call shape (awaitable result), recording calls.

    The call is recorded synchronously — an ``async def`` body would not run at
    all unless the caller awaited it, which is precisely the bug under test.
    """

    def __init__(self, outcome: str = "") -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self._outcome = outcome

    def launch(self, *args: object, **kwargs: object):
        self.calls.append((args, kwargs))
        return self._result(kwargs)

    async def _result(self, kwargs: dict[str, object]) -> LaunchResult:
        return LaunchResult(
            step_id=str(kwargs.get("step_id", "")),
            agent_name=str(kwargs.get("agent_name", "")),
            status="complete",
            outcome=self._outcome,
        )


def _cache() -> MagicMock:
    cache = MagicMock(spec=ContextCache)
    cache.get_or_build.return_value = json.dumps({"built_at": "2026-04-28T00:00:00Z"})
    return cache


def _target(tmp_path: Path, kind: str = "stale-comment") -> SweepTarget:
    path = tmp_path / "mod.py"
    if not path.exists():
        path.write_text("# TODO: this comment is stale\ndef f():\n    return 1\n")
    return SweepTarget(
        path=path,
        kind=kind,
        last_swept_at="2020-01-01T00:00:00Z",
        priority=1.0,
    )


_FOUND_PAYLOAD = json.dumps(
    {
        "found": True,
        "confidence": 0.93,
        "description": "Comment on line 1 no longer matches the code",
        "affected_lines": [1],
        "kind": "stale-comment",
        "auto_fix_directive": "Delete the stale comment on line 1",
    }
)

_CLEAN_PAYLOAD = json.dumps(
    {
        "found": False,
        "confidence": 0.1,
        "description": "",
        "affected_lines": [],
        "kind": "stale-comment",
        "auto_fix_directive": "",
    }
)


def _assert_bindable(args: tuple[object, ...], kwargs: dict[str, object]) -> None:
    """Fail if ``args``/``kwargs`` cannot be bound to the real ``launch``."""
    sig = inspect.signature(ClaudeCodeLauncher.launch)
    sentinel_self = object()
    try:
        sig.bind(sentinel_self, *args, **kwargs)
    except TypeError as exc:
        pytest.fail(
            "immune launch call is not compatible with "
            f"ClaudeCodeLauncher.launch{sig}: {exc} "
            f"(call was args={args!r}, kwargs={sorted(kwargs)})"
        )


# ---------------------------------------------------------------------------
# Sweeper
# ---------------------------------------------------------------------------


def test_sweep_returns_finding_against_real_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sweep agent reporting found=true must yield a SweepFinding.

    Regression for F067: the sweeper called ``launch()`` without the required
    ``model`` argument and never awaited the coroutine, so every real sweep
    was swallowed into ``None`` — indistinguishable from a clean codebase.
    """
    launcher, spawned = _install_real_launcher(monkeypatch, tmp_path, _FOUND_PAYLOAD)
    sweeper = Sweeper(_cache(), launcher)

    finding = sweeper.sweep(_target(tmp_path))

    assert finding is not None, (
        "Sweeper.sweep() returned None even though the sweep agent reported "
        "found=true — the launch never reached the agent (or its result was "
        "not parsed), so every target is silently reported clean."
    )
    assert isinstance(finding, SweepFinding)
    assert finding.kind == "stale-comment"
    assert finding.confidence == pytest.approx(0.93)
    assert finding.description == "Comment on line 1 no longer matches the code"
    assert finding.affected_lines == [1]
    assert finding.auto_fix_directive == "Delete the stale comment on line 1"
    assert any(argv and argv[0].endswith("claude") for argv in spawned), (
        "the sweep agent subprocess was never spawned"
    )


def test_sweep_returns_none_when_agent_reports_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean target must still produce no finding."""
    launcher, _ = _install_real_launcher(monkeypatch, tmp_path, _CLEAN_PAYLOAD)
    sweeper = Sweeper(_cache(), launcher)

    assert sweeper.sweep(_target(tmp_path)) is None


def test_sweep_launch_call_binds_to_real_launcher_signature(tmp_path: Path) -> None:
    """The kwargs the sweeper passes must bind to ``ClaudeCodeLauncher.launch``.

    Guards against a future signature drift silently disabling immune sweeps.
    """
    recorder = _RecordingLauncher(outcome=_FOUND_PAYLOAD)
    sweeper = Sweeper(_cache(), recorder)

    sweeper.sweep(_target(tmp_path))

    assert recorder.calls, "Sweeper never called launcher.launch()"
    args, kwargs = recorder.calls[-1]
    _assert_bindable(args, kwargs)


# ---------------------------------------------------------------------------
# FindingTriage
# ---------------------------------------------------------------------------


def _triage_parts(launcher: object) -> tuple[FindingTriage, MagicMock, MagicMock]:
    budget = MagicMock()
    budget.has_headroom_for_auto_fix.return_value = True
    bead_store = MagicMock()
    bead_store.write.return_value = "bd-abc123"
    config = ImmuneConfig(
        enabled=True,
        daily_cap_usd=5.0,
        sweep_kinds=["stale-comment"],
        auto_fix=True,
        auto_fix_threshold=0.85,
        tick_interval_sec=0,
    )
    triage = FindingTriage(
        bead_store=bead_store,
        budget=budget,
        config=config,
        launcher=launcher,
    )
    return triage, budget, bead_store


def _finding(tmp_path: Path) -> SweepFinding:
    return SweepFinding(
        target=_target(tmp_path),
        confidence=0.95,
        description="Comment on line 1 no longer matches the code",
        affected_lines=[1],
        auto_fix_directive="Delete the stale comment on line 1",
        kind="stale-comment",
    )


def test_triage_autofix_actually_launches_micro_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auto-fix dispatch must really launch the immune-autofix agent.

    Regression for F067: ``_dispatch_autofix_micro_agent`` called ``launch()``
    with the wrong signature, the ``TypeError`` was swallowed, and no agent
    ever ran.
    """
    launcher, spawned = _install_real_launcher(monkeypatch, tmp_path, "{}")
    triage, budget, bead_store = _triage_parts(launcher)

    bead_id = triage.handle(_finding(tmp_path))

    assert bead_id == "bd-abc123"
    assert any(argv and argv[0].endswith("claude") for argv in spawned), (
        "immune-autofix micro-agent was never launched — the auto-fix "
        "dispatch failed silently"
    )
    budget.record_immune_spend.assert_called_once()


def test_triage_autofix_launch_call_binds_to_real_launcher_signature(
    tmp_path: Path,
) -> None:
    """Triage's ``launch()`` call must bind to the real launcher signature."""
    recorder = _RecordingLauncher()
    triage, _budget, _beads = _triage_parts(recorder)

    triage.handle(_finding(tmp_path))

    assert recorder.calls, "FindingTriage never called launcher.launch()"
    args, kwargs = recorder.calls[-1]
    _assert_bindable(args, kwargs)


def test_triage_does_not_book_spend_when_launch_fails(tmp_path: Path) -> None:
    """A failed auto-fix launch must not consume the immune daily budget."""

    class _BrokenLauncher:
        async def launch(self, *args: object, **kwargs: object) -> LaunchResult:
            raise RuntimeError("claude CLI unavailable")

    triage, budget, _beads = _triage_parts(_BrokenLauncher())

    with contextlib.suppress(Exception):
        triage.handle(_finding(tmp_path))

    budget.record_immune_spend.assert_not_called()
