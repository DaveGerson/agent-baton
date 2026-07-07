"""Fast smoke tests for the developer-facing ``baton`` CLI surface (bd-rm-ux-p2).

These exist because CI historically ran only a narrow slice of the pytest
suite (a handful of bead-related files), so a broken top-level command
(``baton --help`` raising an exception, ``baton doctor --json`` emitting
malformed JSON, ``baton plan`` crashing without an API key) could land on
``master`` without any CI signal. This file is intentionally NOT exhaustive
-- each command already has its own deep test module elsewhere under
``tests/cli/``. This module answers one narrow question per command: "does
the happy path work for a brand-new user with nothing configured?"

Kept deliberately fast and hermetic:
- No real network calls.
- No real Anthropic API calls (``ANTHROPIC_API_KEY`` is unset where relevant
  to exercise the deterministic fallback path).
- Filesystem writes are confined to ``tmp_path``.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def _run_cli(argv: list[str]) -> int:
    """Invoke ``agent_baton.cli.main.main`` in-process and return its exit code.

    Mirrors the helper used by ``tests/cli/test_doctor.py`` so behaviour stays
    consistent across CLI-level tests.
    """
    from agent_baton.cli.main import main

    try:
        main(argv)
        return 0
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 0


# ---------------------------------------------------------------------------
# `baton --help`
# ---------------------------------------------------------------------------


class TestHelp:
    def test_top_level_help_succeeds(self, capsys: pytest.CaptureFixture) -> None:
        rc = _run_cli(["--help"])
        out = capsys.readouterr().out

        assert rc == 0
        assert "baton" in out.lower()
        # Sanity: at least one real subcommand shows up in the grouped help.
        assert "plan" in out

    def test_help_via_real_entry_point_subprocess(self) -> None:
        """Exercise ``python -m agent_baton.cli.main --help`` in a fresh
        interpreter.

        The in-process ``_run_cli`` helper reuses modules already imported by
        the test process, which can mask import-time breakage (e.g. a
        top-level import that only fails from a clean interpreter/venv, the
        exact failure mode a packaged wheel install would hit). Running as a
        subprocess catches that class of bug.
        """
        result = subprocess.run(
            [sys.executable, "-m", "agent_baton.cli.main", "--help"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert "baton" in result.stdout.lower()


# ---------------------------------------------------------------------------
# `baton doctor --json`
# ---------------------------------------------------------------------------


class TestDoctorJson:
    def test_doctor_json_parses_with_expected_shape(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """``baton doctor --json`` must always produce parseable JSON with the
        documented shape, regardless of environment.

        Deliberately does NOT assert ``payload["ok"] is True``: a parallel
        change this wave makes a missing ``bd`` binary a hard failure
        (``ok=False``, check status ``"error"``) instead of a warning, and a
        fresh/empty PATH (as used here to force a clean-machine scenario)
        will trigger exactly that. Asserting the payload *parses* and has the
        expected top-level keys and check IDs lets both changes land without
        one test breaking the other.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))

        # rc may be 0 (all checks ok/warning) or 1 (a check reports "error");
        # both are legitimate outcomes on a bare machine -- see docstring.
        _run_cli(["doctor", "--json"])
        out = capsys.readouterr().out

        payload = json.loads(out)
        assert payload["schema_version"] == 1
        assert isinstance(payload["ok"], bool)
        assert "project_root" in payload
        assert "summary" in payload
        assert isinstance(payload["checks"], list) and payload["checks"]

        check_ids = {check["id"] for check in payload["checks"]}
        assert {"python", "package_version", "bundled_agents", "bd"} <= check_ids

        for check in payload["checks"]:
            assert check["status"] in ("ok", "warning", "error")


# ---------------------------------------------------------------------------
# `baton validate` -- agent definition validation
# ---------------------------------------------------------------------------


class TestValidateAgents:
    def test_validate_bundled_agent_directory_succeeds(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """``baton validate agents/`` must pass cleanly against the repo's own
        distributable agent definitions -- the same command
        ``make validate`` runs."""
        repo_root = Path(__file__).resolve().parents[2]
        agents_dir = repo_root / "agents"
        assert agents_dir.is_dir(), f"expected {agents_dir} to exist"

        rc = _run_cli(["validate", str(agents_dir)])
        out = capsys.readouterr().out

        assert rc == 0, out
        assert "0 errors" in out


# ---------------------------------------------------------------------------
# `baton plan` -- deterministic fallback (no ANTHROPIC_API_KEY)
# ---------------------------------------------------------------------------


class TestPlanDeterministicFallback:
    def test_simple_plan_builds_without_anthropic_api_key(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """A brand-new project with no API key configured must still be able
        to run ``baton plan --save``. The planner degrades to its
        keyword-heuristic classifier when no key/CLI is available -- this
        smoke test proves that deterministic path produces a usable,
        saved plan rather than crashing."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        project = tmp_path / "project"
        project.mkdir()
        (project / "pyproject.toml").write_text(
            '[project]\nname = "demo"\n', encoding="utf-8"
        )
        monkeypatch.chdir(project)

        rc = _run_cli(
            ["plan", "Add a health-check endpoint", "--save", "--skip-init"]
        )
        out = capsys.readouterr().out
        assert rc == 0, out

        plan_path = project / ".claude" / "team-context" / "plan.json"
        assert plan_path.exists(), out

        payload = json.loads(plan_path.read_text(encoding="utf-8"))
        assert payload["task_summary"] == "Add a health-check endpoint"
        assert payload["phases"], "deterministic plan should have at least one phase"
        assert payload["risk_level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
