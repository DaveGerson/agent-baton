"""Tests for the baton.yaml workflow (Wave 1.2 — bd-2da7).

Covers :class:`agent_baton.core.config.project_config.ProjectConfig`
parsing/loading/merge semantics, planner application of those
defaults, and the ``baton config`` CLI surface.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_baton.core.config import ProjectConfig
from agent_baton.core.config.project_config import CONFIG_FILENAME


# ---------------------------------------------------------------------------
# ProjectConfig — loading and parsing
# ---------------------------------------------------------------------------


def test_load_returns_empty_default_when_no_yaml(tmp_path: Path) -> None:
    """When no baton.yaml is found, load() returns an empty ProjectConfig."""
    cfg = ProjectConfig.load(tmp_path)
    assert cfg.is_empty()
    assert cfg.source_path is None
    assert cfg.default_agents == {}
    assert cfg.default_gates == []
    assert cfg.default_isolation == ""


def test_from_yaml_parses_all_fields(tmp_path: Path) -> None:
    """A fully populated baton.yaml is parsed into matching dataclass fields."""
    yaml_path = tmp_path / CONFIG_FILENAME
    yaml_path.write_text(
        """
default_agents:
  backend: backend-engineer--python
  frontend: frontend-engineer--react
default_gates:
  - pytest
  - lint
default_risk_level: HIGH
default_isolation: worktree
auto_route_rules:
  - path_glob: "tests/**"
    agent: test-engineer
excluded_paths:
  - "node_modules/**"
""",
        encoding="utf-8",
    )

    cfg = ProjectConfig.from_yaml(yaml_path)

    assert cfg.default_agents == {
        "backend": "backend-engineer--python",
        "frontend": "frontend-engineer--react",
    }
    assert cfg.default_gates == ["pytest", "lint"]
    assert cfg.default_risk_level == "HIGH"
    assert cfg.default_isolation == "worktree"
    assert cfg.auto_route_rules == [
        {"path_glob": "tests/**", "agent": "test-engineer"}
    ]
    assert cfg.excluded_paths == ["node_modules/**"]
    assert cfg.source_path == yaml_path.resolve()
    assert not cfg.is_empty()


def test_from_yaml_missing_fields_use_defaults(tmp_path: Path) -> None:
    """Partial configs work — missing fields fall back to dataclass defaults."""
    yaml_path = tmp_path / CONFIG_FILENAME
    yaml_path.write_text("default_gates:\n  - pytest\n", encoding="utf-8")

    cfg = ProjectConfig.from_yaml(yaml_path)

    assert cfg.default_gates == ["pytest"]
    assert cfg.default_agents == {}
    assert cfg.default_isolation == ""
    assert cfg.default_risk_level == ""
    assert cfg.auto_route_rules == []
    assert cfg.excluded_paths == []


def test_load_walks_up_to_find_yaml(tmp_path: Path) -> None:
    """load(start_dir) discovers a baton.yaml in an ancestor directory."""
    yaml_path = tmp_path / CONFIG_FILENAME
    yaml_path.write_text("default_gates: [pytest]\n", encoding="utf-8")

    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)

    cfg = ProjectConfig.load(deep)

    assert cfg.source_path == yaml_path.resolve()
    assert cfg.default_gates == ["pytest"]


def test_merge_combines_two_configs(tmp_path: Path) -> None:
    """merge() applies workspace overrides additively, with the right semantics."""
    base = ProjectConfig(
        default_agents={"backend": "backend-engineer", "test": "test-engineer"},
        default_gates=["pytest"],
        default_risk_level="LOW",
        auto_route_rules=[{"path_glob": "src/**", "agent": "backend-engineer"}],
        excluded_paths=["node_modules/**"],
        default_isolation="",
    )
    override = ProjectConfig(
        default_agents={"backend": "backend-engineer--python"},  # wins
        default_gates=["lint"],                                   # appended
        default_risk_level="HIGH",                                # wins
        auto_route_rules=[{"path_glob": "tests/**", "agent": "test-engineer"}],
        excluded_paths=["node_modules/**", ".venv/**"],           # dedup
        default_isolation="worktree",                             # wins (was empty)
    )

    merged = base.merge(override)

    assert merged.default_agents == {
        "backend": "backend-engineer--python",
        "test": "test-engineer",
    }
    assert merged.default_gates == ["pytest", "lint"]
    assert merged.default_risk_level == "HIGH"
    assert merged.default_isolation == "worktree"
    # Both rules preserved in order (base first, override second).
    assert len(merged.auto_route_rules) == 2
    assert merged.auto_route_rules[0]["path_glob"] == "src/**"
    assert merged.auto_route_rules[1]["path_glob"] == "tests/**"
    assert merged.excluded_paths == ["node_modules/**", ".venv/**"]


def test_auto_route_rule_matches_path_glob() -> None:
    """route_agent_for_paths() returns the agent for the first matching rule."""
    cfg = ProjectConfig(
        auto_route_rules=[
            {"path_glob": "docs/**", "agent": "documentation-architect"},
            {"path_glob": "tests/**", "agent": "test-engineer"},
        ],
    )

    assert cfg.route_agent_for_paths(["tests/test_foo.py"]) == "test-engineer"
    assert cfg.route_agent_for_paths(["docs/index.md"]) == "documentation-architect"
    assert cfg.route_agent_for_paths(["src/app.py"]) is None
    # First-match-wins when both rules could match.
    cfg2 = ProjectConfig(
        auto_route_rules=[
            {"path_glob": "tests/**", "agent": "first"},
            {"path_glob": "tests/**", "agent": "second"},
        ],
    )
    assert cfg2.route_agent_for_paths(["tests/foo.py"]) == "first"


# ---------------------------------------------------------------------------
# Planner integration
# ---------------------------------------------------------------------------

# Reuse the planner fixture from test_engine_planner.py via conftest-style
# duplicates so we don't introduce a cross-module dependency.


@pytest.fixture
def _agents_dir(tmp_path: Path) -> Path:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    for name in (
        "backend-engineer",
        "backend-engineer--python",
        "frontend-engineer",
        "frontend-engineer--react",
        "test-engineer",
        "documentation-architect",
        "architect",
        "code-reviewer",
    ):
        (agents_dir / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: {name}.\nmodel: sonnet\n"
            f"permissionMode: default\ntools: Read, Write\n---\n\n# {name}\n",
            encoding="utf-8",
        )
    return agents_dir


@pytest.fixture
def _planner(tmp_path: Path, _agents_dir: Path):
    from agent_baton.core.engine.planner import IntelligentPlanner
    from agent_baton.core.orchestration.registry import AgentRegistry
    from agent_baton.core.orchestration.router import AgentRouter

    ctx = tmp_path / "team-context"
    ctx.mkdir()
    p = IntelligentPlanner(team_context_root=ctx)
    reg = AgentRegistry()
    reg.load_directory(_agents_dir)
    p._registry = reg
    p._router = AgentRouter(reg)
    return p


def test_planner_applies_default_agents(_planner) -> None:
    """default_agents substitutes a step's generic agent for the configured one."""
    from agent_baton.models.execution import PlanPhase, PlanStep

    cfg = ProjectConfig(
        default_agents={"backend": "backend-engineer--python"},
    )
    _planner._project_config = cfg

    phase = PlanPhase(
        phase_id=1,
        name="Implementation",
        steps=[PlanStep(step_id="1.1", agent_name="backend-engineer", task_description="x")],
    )
    _planner._apply_project_config([phase])

    assert phase.steps[0].agent_name == "backend-engineer--python"


def test_planner_applies_default_gates(_planner) -> None:
    """default_gates extends each phase's gate list (deduped by gate_type)."""
    from agent_baton.models.execution import PlanGate, PlanPhase, PlanStep

    cfg = ProjectConfig(default_gates=["pytest", "lint"])
    _planner._project_config = cfg

    phase = PlanPhase(
        phase_id=1,
        name="Implementation",
        steps=[PlanStep(step_id="1.1", agent_name="backend-engineer", task_description="x")],
        gate=None,
    )
    _planner._apply_project_config([phase])

    # First config gate becomes the phase gate when none existed.
    assert phase.gate is not None
    assert phase.gate.gate_type == "pytest"
    # Second gate concatenates into the description (single-gate slot today).
    assert "lint" in phase.gate.description.lower()


def test_planner_applies_default_isolation(_planner) -> None:
    """default_isolation is recorded per-step and exposed via isolation_for_step."""
    from agent_baton.models.execution import PlanPhase, PlanStep

    cfg = ProjectConfig(default_isolation="worktree")
    _planner._project_config = cfg

    phase = PlanPhase(
        phase_id=1,
        name="Implementation",
        steps=[
            PlanStep(step_id="1.1", agent_name="backend-engineer", task_description="x"),
            PlanStep(step_id="1.2", agent_name="test-engineer", task_description="y"),
        ],
    )
    _planner._apply_project_config([phase])

    assert _planner.isolation_for_step("1.1") == "worktree"
    assert _planner.isolation_for_step("1.2") == "worktree"
    assert _planner.isolation_for_step("nonexistent") == ""


def test_planner_default_excluded_paths(_planner) -> None:
    """excluded_paths appends to step.blocked_paths (deduplicated)."""
    from agent_baton.models.execution import PlanPhase, PlanStep

    cfg = ProjectConfig(excluded_paths=["node_modules/**", ".venv/**"])
    _planner._project_config = cfg

    phase = PlanPhase(
        phase_id=1,
        name="Implementation",
        steps=[
            PlanStep(
                step_id="1.1",
                agent_name="backend-engineer",
                task_description="x",
                blocked_paths=["node_modules/**"],  # already present — dedup
            ),
        ],
    )
    _planner._apply_project_config([phase])

    assert phase.steps[0].blocked_paths == ["node_modules/**", ".venv/**"]


def test_planner_empty_config_is_noop(_planner) -> None:
    """Empty ProjectConfig leaves phases unchanged (additive, never destructive)."""
    from agent_baton.models.execution import PlanPhase, PlanStep

    _planner._project_config = ProjectConfig()
    phase = PlanPhase(
        phase_id=1,
        name="Implementation",
        steps=[
            PlanStep(step_id="1.1", agent_name="backend-engineer", task_description="x"),
        ],
    )
    _planner._apply_project_config([phase])

    assert phase.steps[0].agent_name == "backend-engineer"
    assert phase.gate is None
    assert phase.steps[0].blocked_paths == []
    assert _planner.isolation_for_step("1.1") == ""


# ---------------------------------------------------------------------------
# CLI: baton config show / init / validate
# ---------------------------------------------------------------------------


def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Invoke the baton CLI in a subprocess and capture stdout/stderr.

    Sets ``PYTHONPATH`` to this checkout so the subprocess loads the
    worktree's ``agent_baton`` package rather than any pip-installed
    copy that may live elsewhere on the system.
    """
    import os
    # tests/ is a sibling of agent_baton/ in this checkout.
    repo_root = str(Path(__file__).resolve().parent.parent)
    env = {**os.environ, "PYTHONPATH": repo_root}
    return subprocess.run(
        [sys.executable, "-m", "agent_baton.cli.main", "config", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_cli_show_prints_yaml(tmp_path: Path) -> None:
    """`baton config show` prints the discovered config path and JSON body."""
    yaml_path = tmp_path / CONFIG_FILENAME
    yaml_path.write_text(
        "default_gates: [pytest]\ndefault_isolation: worktree\n",
        encoding="utf-8",
    )

    result = _run_cli("show", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Loaded" in result.stdout
    assert str(yaml_path.resolve()) in result.stdout
    # Pull the JSON block off the second line onward and validate.
    json_block = result.stdout.split("\n", 1)[1]
    parsed = json.loads(json_block)
    assert parsed["default_gates"] == ["pytest"]
    assert parsed["default_isolation"] == "worktree"


def test_cli_init_writes_starter(tmp_path: Path) -> None:
    """`baton config init` writes a starter baton.yaml that re-parses cleanly."""
    result = _run_cli("init", cwd=tmp_path)
    assert result.returncode == 0, result.stderr

    target = tmp_path / CONFIG_FILENAME
    assert target.exists()

    # Re-parse — the starter must validate against ProjectConfig.from_yaml.
    cfg = ProjectConfig.from_yaml(target)
    assert "backend" in cfg.default_agents
    assert cfg.default_gates  # non-empty
    assert cfg.default_isolation == "worktree"

    # Refusing to overwrite without --force.
    result_again = _run_cli("init", cwd=tmp_path)
    assert result_again.returncode != 0
    assert "already exists" in (result_again.stdout + result_again.stderr).lower()


def test_cli_validate_rejects_bad_yaml(tmp_path: Path) -> None:
    """`baton config validate` exits non-zero on malformed configs."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("default_agents: [oops, this, is, a, list]\n", encoding="utf-8")

    result = _run_cli("validate", str(bad), cwd=tmp_path)
    assert result.returncode != 0
    assert "invalid" in (result.stdout + result.stderr).lower()

    # And a good file passes.
    good = tmp_path / "good.yaml"
    good.write_text("default_gates: [pytest]\n", encoding="utf-8")
    ok = _run_cli("validate", str(good), cwd=tmp_path)
    assert ok.returncode == 0, ok.stderr
