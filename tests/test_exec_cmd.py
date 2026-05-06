"""Trust-boundary warning tests for ``baton beads exec`` (bd-18f6 lineage).

End-user readiness #10 documents the executable-bead trust boundary in
``references/baton-patterns.md``.  ``_emit_trust_boundary_warning_if_external``
is the small CLI tripwire that prints a one-line ``[security]`` warning
when an executable bead's ``source`` falls outside the locally-produced
set.  These tests pin that contract so the warning cannot regress
silently.

The sandbox itself is intentionally NOT exercised here — the tripwire is
documentation-first and runs *before* execution, so we mock the runner.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.cli.commands import bead_cmd


# ---------------------------------------------------------------------------
# Direct unit tests on the helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "local_source",
    sorted(bead_cmd._LOCAL_BEAD_SOURCES),
)
def test_local_origin_emits_no_warning(
    local_source: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Beads from any local source must remain silent."""
    bead = SimpleNamespace(source=local_source)

    bead_cmd._emit_trust_boundary_warning_if_external(bead)

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


@pytest.mark.parametrize(
    "external_source",
    ["federation", "remote", "external", "imported", "fork-pr", ""],
)
def test_external_origin_emits_trust_warning_helper(
    external_source: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Any non-local source value must trigger the one-line warning."""
    bead = SimpleNamespace(source=external_source)

    bead_cmd._emit_trust_boundary_warning_if_external(bead)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[security]" in captured.err
    assert "executable bead from external origin" in captured.err
    assert (
        "references/baton-patterns.md#executable-beads-trust-boundary"
        in captured.err
    )
    # The actual source value (or its repr for the empty case) is echoed
    # so the operator can see what the bead claimed.
    assert repr(external_source) in captured.err


# ---------------------------------------------------------------------------
# End-to-end CLI test through ``_handle_exec``
# ---------------------------------------------------------------------------


def _make_fake_external_bead() -> SimpleNamespace:
    """A fake bead-row stand-in that mimics ``BeadStore.read()`` output."""
    return SimpleNamespace(
        bead_id="bd-ext1",
        bead_type="executable",
        source="federation",
        content="external repro script",
        # Fields consumed by ExecutableBead.from_dict during the operator
        # confirmation branch.  Not exercised here because we use
        # ``--no-confirm``.
        to_dict=lambda: {
            "bead_id": "bd-ext1",
            "bead_type": "executable",
            "source": "federation",
            "content": "external repro script",
            "interpreter": "bash",
            "script_sha": "deadbeef" * 8,
            "script_ref": "refs/notes/baton-bead-scripts:" + "deadbeef" * 8,
            "exec_ref": "refs/notes/baton-bead-scripts:" + "deadbeef" * 8,
            "runtime_limits": {"timeout_s": 5, "mem_mb": 64, "net": False},
            "status": "open",
        },
    )


def test_external_origin_emits_trust_warning(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``baton beads exec`` on a federation-origin bead emits the warning.

    Mocks the bead store, auditor gate, and runner so the test never
    actually touches sqlite or runs a script — we only assert that the
    trust-boundary tripwire fires before the runner is invoked.
    """
    monkeypatch.setenv("BATON_EXEC_BEADS_ENABLED", "1")

    fake_store = MagicMock()
    fake_store.read.return_value = _make_fake_external_bead()

    fake_gate = MagicMock()
    fake_gate.is_approved.return_value = True

    fake_result = SimpleNamespace(
        exit_code=0,
        duration_ms=12,
        stdout="",
        stderr="",
        full_output_path=None,
    )
    fake_runner = MagicMock()
    fake_runner.run.return_value = fake_result

    args = SimpleNamespace(bead_id="bd-ext1", no_confirm=True)

    with patch.object(bead_cmd, "_get_bead_store", return_value=fake_store), \
         patch(
             "agent_baton.core.exec.auditor_gate.AuditorGate",
             return_value=fake_gate,
         ), \
         patch(
             "agent_baton.core.exec.runner.ExecutableBeadRunner",
             return_value=fake_runner,
         ), \
         patch(
             "agent_baton.core.exec.sandbox.Sandbox",
             return_value=MagicMock(),
         ):
        with pytest.raises(SystemExit) as excinfo:
            bead_cmd._handle_exec(args)

    # Exit code mirrors the (mocked) sandbox exit_code.
    assert excinfo.value.code == 0

    captured = capsys.readouterr()
    # The trust-boundary warning must appear on stderr before the runner
    # is invoked.
    assert "[security] executable bead from external origin" in captured.err
    assert "'federation'" in captured.err
    assert (
        "references/baton-patterns.md#executable-beads-trust-boundary"
        in captured.err
    )
    # And the runner must still have been invoked — the warning is a
    # tripwire, not a hard block.
    fake_runner.run.assert_called_once_with("bd-ext1")


def test_local_origin_does_not_emit_trust_warning(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Default ``source='agent-signal'`` beads run silently."""
    monkeypatch.setenv("BATON_EXEC_BEADS_ENABLED", "1")

    local_bead = _make_fake_external_bead()
    local_bead.source = "agent-signal"

    fake_store = MagicMock()
    fake_store.read.return_value = local_bead

    fake_gate = MagicMock()
    fake_gate.is_approved.return_value = True

    fake_result = SimpleNamespace(
        exit_code=0,
        duration_ms=7,
        stdout="",
        stderr="",
        full_output_path=None,
    )
    fake_runner = MagicMock()
    fake_runner.run.return_value = fake_result

    args = SimpleNamespace(bead_id="bd-loc1", no_confirm=True)

    with patch.object(bead_cmd, "_get_bead_store", return_value=fake_store), \
         patch(
             "agent_baton.core.exec.auditor_gate.AuditorGate",
             return_value=fake_gate,
         ), \
         patch(
             "agent_baton.core.exec.runner.ExecutableBeadRunner",
             return_value=fake_runner,
         ), \
         patch(
             "agent_baton.core.exec.sandbox.Sandbox",
             return_value=MagicMock(),
         ):
        with pytest.raises(SystemExit):
            bead_cmd._handle_exec(args)

    captured = capsys.readouterr()
    assert "[security]" not in captured.err
    assert "external origin" not in captured.err
