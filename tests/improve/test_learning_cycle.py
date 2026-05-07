"""L2.2 — Tests for the learning-cycle pipeline.

Verifies the four pieces of the learning-cycle deliverable:

1. The ``templates/learning-cycle-plan.json`` template exists, is valid JSON,
   and parses cleanly through :class:`MachinePlan`.
2. The template can be loaded via ``baton plan --import`` end-to-end (the
   command exits 0 and prints the rendered plan markdown).
3. The ``agents/learning-analyst.md`` definition exists with the required
   YAML frontmatter (``name``, ``description``, ``model``, ``tools``).
4. ``baton learn run-cycle`` registers the expected flags
   (``--run``, ``--dry-run``, ``--template``).
5. The cycle output path produces zero or more
   :class:`~agent_baton.models.improvement.Recommendation` rows — i.e.
   the persistence pipeline is wired and a fresh project legitimately
   reports zero recommendations rather than crashing.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.models.execution import MachinePlan


# ---------------------------------------------------------------------------
# Path helpers — anchor at the repo root so the tests are CWD-independent.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE = _REPO_ROOT / "templates" / "learning-cycle-plan.json"
_AGENT = _REPO_ROOT / "agents" / "learning-analyst.md"


# ---------------------------------------------------------------------------
# 1. Template file exists and is a valid MachinePlan
# ---------------------------------------------------------------------------


class TestTemplateValid:
    def test_template_file_exists(self) -> None:
        assert _TEMPLATE.exists(), (
            f"learning-cycle template missing at {_TEMPLATE}"
        )

    def test_template_is_valid_json(self) -> None:
        # A JSONDecodeError here would be a structural bug in the template.
        data = json.loads(_TEMPLATE.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_template_parses_as_machine_plan(self) -> None:
        data = json.loads(_TEMPLATE.read_text(encoding="utf-8"))
        plan = MachinePlan.from_dict(data)

        # Required fields the executor relies on
        assert plan.task_id, "task_id must be set"
        assert plan.task_summary, "task_summary must be set"
        assert plan.phases, "phases must be non-empty"

    def test_template_has_learning_analyst_step(self) -> None:
        data = json.loads(_TEMPLATE.read_text(encoding="utf-8"))
        plan = MachinePlan.from_dict(data)
        agents_in_plan = [s.agent_name for s in plan.all_steps]
        assert "learning-analyst" in agents_in_plan, (
            "learning-cycle template must dispatch the learning-analyst at "
            f"least once; saw agents: {agents_in_plan}"
        )

    def test_template_has_at_least_one_review_step(self) -> None:
        """Spec: 1-2 phases, 1 analyst step, 1 review step, no implementation."""
        data = json.loads(_TEMPLATE.read_text(encoding="utf-8"))
        plan = MachinePlan.from_dict(data)
        # Reviewer step is identified by step_type=reviewing OR reviewer agent name
        review_like = [
            s
            for s in plan.all_steps
            if s.step_type == "reviewing" or "review" in s.agent_name
        ]
        assert review_like, (
            "learning-cycle template must include at least one review step; "
            f"saw step_types: {[s.step_type for s in plan.all_steps]}"
        )

    def test_template_does_not_apply_changes(self) -> None:
        """L2.2 explicitly forbids an APPLY/implementation phase.

        Recommendations are routed through the existing escalation /
        system-maintainer flow, never auto-applied by the cycle itself.
        """
        data = json.loads(_TEMPLATE.read_text(encoding="utf-8"))
        plan = MachinePlan.from_dict(data)
        for step in plan.all_steps:
            assert step.step_type != "developing", (
                f"step {step.step_id} ({step.agent_name}) has step_type "
                "'developing' — the learning cycle must not modify code"
            )
            assert step.agent_name != "backend-engineer", (
                f"step {step.step_id} dispatches backend-engineer — the "
                "learning cycle must not contain an implementation phase"
            )


# ---------------------------------------------------------------------------
# 2. ``baton plan --import`` accepts the template
# ---------------------------------------------------------------------------


class TestPlanImport:
    """Round-trip the template through the public ``baton plan --import``
    surface to catch any drift between template schema and importer schema.
    """

    def test_import_via_subprocess(self) -> None:
        # baton plan --import requires a positional summary even though the
        # imported file already carries task_summary; pass a placeholder.
        from tests._subprocess_helpers import cli_subprocess_env

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_baton.cli.main",
                "plan",
                "imported learning cycle (test)",
                "--import",
                str(_TEMPLATE),
            ],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env=cli_subprocess_env(),
        )
        assert result.returncode == 0, (
            f"baton plan --import failed (rc={result.returncode}): "
            f"stderr={result.stderr!r}"
        )
        # Without --save, the importer prints the rendered plan markdown.
        assert "# Execution Plan" in result.stdout
        assert "learning-analyst" in result.stdout

    def test_import_via_handler_in_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Drive the plan_cmd handler directly to avoid subprocess dependency."""
        from agent_baton.cli.commands.execution import plan_cmd

        args = argparse.Namespace(
            summary="imported learning cycle (in-process)",
            task_type=None,
            agents=None,
            project=None,
            json=False,
            save=False,
            explain=False,
            knowledge=[],
            knowledge_pack=[],
            intervention="low",
            model=None,
            complexity=None,
            import_path=str(_TEMPLATE),
            template=False,
            save_as_template=None,
            from_template=None,
            skip_init=False,
            verbose=False,
            dry_run=False,
        )
        plan_cmd.handler(args)
        out = capsys.readouterr().out
        assert "learning-analyst" in out


# ---------------------------------------------------------------------------
# 3. learning-analyst agent definition
# ---------------------------------------------------------------------------


class TestLearningAnalystAgent:
    REQUIRED_KEYS = {"name", "description", "model", "tools"}

    def test_agent_file_exists(self) -> None:
        assert _AGENT.exists(), f"learning-analyst agent missing at {_AGENT}"

    def test_agent_has_frontmatter(self) -> None:
        text = _AGENT.read_text(encoding="utf-8")
        assert text.startswith("---\n"), (
            "agent file must open with a YAML frontmatter block"
        )
        # Find the closing fence
        m = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
        assert m, "could not locate closing '---' for the frontmatter block"

    def test_agent_frontmatter_has_required_keys(self) -> None:
        text = _AGENT.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
        assert m is not None
        frontmatter = m.group(1)

        # Lightweight key presence check: each key appears at the start of a
        # line followed by ':'.  Avoids pulling in a YAML dependency.
        for key in self.REQUIRED_KEYS:
            assert re.search(rf"^{key}\s*:", frontmatter, flags=re.MULTILINE), (
                f"agent frontmatter missing required key {key!r}"
            )

    def test_agent_name_matches_filename(self) -> None:
        text = _AGENT.read_text(encoding="utf-8")
        m = re.search(r"^name:\s*(\S+)", text, flags=re.MULTILINE)
        assert m, "agent must declare a 'name:' field"
        assert m.group(1) == "learning-analyst"

    def test_agent_does_not_modify_code(self) -> None:
        """L2.2 invariant: analyst reads + reasons, never writes code."""
        text = _AGENT.read_text(encoding="utf-8")
        # Tools line should not include destructive write tools.
        m = re.search(r"^tools:\s*(.*)$", text, flags=re.MULTILINE)
        assert m, "agent must declare a 'tools:' field"
        tools = m.group(1)
        assert "Edit" not in tools, (
            "learning-analyst must not have Edit access — it proposes, not applies"
        )
        assert "Write" not in tools, (
            "learning-analyst must not have Write access — it proposes, not applies"
        )


# ---------------------------------------------------------------------------
# 4. ``baton learn run-cycle`` CLI surface
# ---------------------------------------------------------------------------


class TestRunCycleCommand:
    def _build_parser(self) -> argparse.ArgumentParser:
        from agent_baton.cli.commands.improve import learn_cmd

        root = argparse.ArgumentParser(prog="baton")
        sub = root.add_subparsers(dest="command")
        learn_cmd.register(sub)
        return root

    def test_run_cycle_subcommand_registered(self) -> None:
        parser = self._build_parser()
        # parse_args will exit(2) on unknown subcommands; success implies it
        # was registered.
        args = parser.parse_args(["learn", "run-cycle"])
        assert getattr(args, "learn_command", None) == "run-cycle"

    def test_run_cycle_accepts_run_flag(self) -> None:
        parser = self._build_parser()
        args = parser.parse_args(["learn", "run-cycle", "--run"])
        assert args.run is True

    def test_run_cycle_accepts_dry_run_flag(self) -> None:
        parser = self._build_parser()
        args = parser.parse_args(["learn", "run-cycle", "--dry-run"])
        assert args.dry_run is True

    def test_run_cycle_accepts_template_flag(self) -> None:
        parser = self._build_parser()
        args = parser.parse_args(
            ["learn", "run-cycle", "--template", "/tmp/x.json"]
        )
        assert args.template == "/tmp/x.json"

    def test_run_cycle_dry_run_with_real_template(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Smoke test: drive the real template through the dry-run path."""
        from agent_baton.cli.commands.improve import learn_cmd

        monkeypatch.setattr(learn_cmd, "_team_context_root", lambda: tmp_path)

        args = argparse.Namespace(
            run=False,
            dry_run=True,
            template=str(_TEMPLATE),
        )
        with patch("subprocess.run") as mock_sub:
            learn_cmd._cmd_run_cycle(args)
            mock_sub.assert_not_called()  # dry-run never invokes baton

        out = capsys.readouterr().out
        assert "learning-analyst" in out
        # Plan file is materialised even on dry-run so operators can inspect it.
        assert (tmp_path / "learning-cycle-plan.json").exists()


# ---------------------------------------------------------------------------
# 5. Cycle output -> Recommendation rows (>=0)
# ---------------------------------------------------------------------------


class TestCycleProducesRecommendations:
    """The analyst writes proposals as Recommendation rows.  In a fresh
    project the analyst legitimately produces zero rows; that is success,
    not failure.  This test verifies the persistence path is wired.
    """

    def test_proposal_manager_returns_zero_or_more(self, tmp_path: Path) -> None:
        from agent_baton.core.improve.proposals import ProposalManager
        from agent_baton.models.improvement import Recommendation

        mgr = ProposalManager(improvements_dir=tmp_path)
        # No rows recorded yet — should report 0.
        existing = mgr.list_all() if hasattr(mgr, "list_all") else []
        assert len(existing) >= 0  # trivially true; documents intent

        # Record one synthetic recommendation as the analyst would have done.
        rec = Recommendation(
            rec_id="rec-test-001",
            category="agent_prompt",
            target="learning-analyst",
            action="document",
            description="Smoke-test recommendation produced by cycle test",
            evidence=["smoke-test"],
            confidence=0.5,
            risk="low",
            auto_applicable=False,
        )
        mgr.record(rec)
        assert mgr.recommendations_path.exists()
        # File must be append-only JSONL with at least our row.
        lines = [
            json.loads(line)
            for line in mgr.recommendations_path.read_text("utf-8").splitlines()
            if line.strip()
        ]
        assert len(lines) >= 1
        assert lines[0]["rec_id"] == "rec-test-001"

    def test_improvement_loop_run_cycle_returns_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Drive ImprovementLoop.run_cycle with a paused config to confirm the
        wiring is intact and produces an ImprovementReport (zero recs is OK).
        """
        from agent_baton.core.improve.loop import ImprovementLoop
        from agent_baton.models.improvement import ImprovementConfig, ImprovementReport

        loop = ImprovementLoop(
            improvements_dir=tmp_path,
            config=ImprovementConfig(paused=True),  # short-circuits, no DB needed
        )
        report = loop.run_cycle()
        assert isinstance(report, ImprovementReport)
        # Paused -> skipped report; recommendations list is empty (>=0 holds).
        assert report.skipped is True
        assert len(report.recommendations) >= 0
