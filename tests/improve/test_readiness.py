"""Tests for the H3.6 ``baton assess readiness`` assessor."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from agent_baton.core.improve.readiness import (
    DimensionScore,
    ReadinessAssessor,
    ReadinessReport,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_repo(tmp_path: Path) -> Path:
    """Return a fresh empty directory representing an unprepared repo."""
    root = tmp_path / "empty"
    root.mkdir()
    return root


def _write(path: Path, content: str = "") -> Path:
    """Create ``path`` and any intermediate dirs, writing ``content``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _build_full_repo(tmp_path: Path) -> Path:
    """Construct a repo that should score >=90 across every dimension."""
    root = tmp_path / "full"
    root.mkdir()

    # 1. Spec discipline: CLAUDE.md, docs/architecture, specs.
    _write(
        root / "CLAUDE.md",
        "# Project guide\n\nWe document our import conventions, style, "
        "and lint rules here.\n",
    )
    _write(root / "docs" / "architecture" / "high-level.md", "# Arch\n")
    _write(root / "docs" / "architecture" / "design.md", "# Design\n")
    _write(root / "docs" / "superpowers" / "specs" / "spec1.md", "# Spec 1\n")

    # 2. Test coverage: 100+ test files + coverage config.
    tests_dir = root / "tests"
    tests_dir.mkdir()
    for i in range(110):
        _write(tests_dir / f"test_unit_{i}.py", "def test_x():\n    assert True\n")
    _write(
        root / "pyproject.toml",
        "[tool.pytest.ini_options]\naddopts = '--cov=mypkg'\n",
    )

    # 3. Conventions: pre-commit + .editorconfig (CLAUDE.md already mentions
    #    convention/style/lint via the spec discipline write above).
    _write(root / ".pre-commit-config.yaml", "repos: []\n")
    _write(root / ".editorconfig", "root = true\n")

    # 4. Knowledge: pack with >=3 docs.
    pack_dir = root / ".claude" / "knowledge" / "core"
    _write(pack_dir / "knowledge.yaml", "name: core\n")
    _write(pack_dir / "doc1.md", "# Doc 1\n")
    _write(pack_dir / "doc2.md", "# Doc 2\n")
    _write(pack_dir / "doc3.md", "# Doc 3\n")
    _write(pack_dir / "doc4.md", "# Doc 4\n")

    # 5. Agent roster: 12 agents covering all four roles.
    agents_dir = root / ".claude" / "agents"
    agent_names = [
        "backend-engineer",
        "frontend-engineer",
        "code-reviewer",
        "spec-reviewer",
        "test-engineer",
        "qa",
        "architect",
        "designer",
        "auditor",
        "planner",
        "orchestrator",
        "researcher",
    ]
    for name in agent_names:
        _write(agents_dir / f"{name}.md", f"# {name}\n")

    # 6. Audit chain: >=10 entries + verify signal.
    chain = root / ".claude" / "team-context" / "compliance-audit.jsonl"
    chain.parent.mkdir(parents=True, exist_ok=True)
    chain.write_text(
        "\n".join(
            json.dumps({"i": i, "event": "decision"}) for i in range(15)
        )
        + "\n",
        encoding="utf-8",
    )
    _write(chain.parent / "audit-verify.log", "verified at 2026-04-25\n")

    # 7. Bead memory: baton.db with >=5 beads + a closed bead with summary.
    db_path = root / ".claude" / "team-context" / "baton.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE beads (
                bead_id TEXT PRIMARY KEY,
                status TEXT,
                summary TEXT
            )
            """
        )
        for i in range(8):
            conn.execute(
                "INSERT INTO beads(bead_id, status, summary) VALUES(?, ?, ?)",
                (f"bd-{i:04d}", "open", None),
            )
        conn.execute(
            "INSERT INTO beads(bead_id, status, summary) VALUES(?, ?, ?)",
            ("bd-done", "closed", "Resolved successfully."),
        )
        conn.commit()
    finally:
        conn.close()

    # 8. CI: GitHub workflow that runs pytest.
    _write(
        root / ".github" / "workflows" / "ci.yml",
        "name: ci\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: pytest --cov\n",
    )

    return root


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_empty_repo_scores_low(tmp_path: Path) -> None:
    """An entirely empty repo should land in the lowest tier."""
    root = _empty_repo(tmp_path)
    report = ReadinessAssessor().assess(root)
    assert report.total < 25
    assert report.tier == "Not ready -- invest in foundation"
    # Every dimension should produce a recommendation when score < 8.
    assert len(report.recommendations) == len(report.dimensions)


def test_claude_md_only_scores_higher_than_empty(tmp_path: Path) -> None:
    """Adding just CLAUDE.md must measurably move the needle upward."""
    empty = _empty_repo(tmp_path)
    empty_score = ReadinessAssessor().assess(empty).total

    with_claude = tmp_path / "with-claude"
    with_claude.mkdir()
    _write(with_claude / "CLAUDE.md", "# Guide\n")
    with_score = ReadinessAssessor().assess(with_claude).total

    assert with_score > empty_score


def test_full_repo_scores_at_least_ninety(tmp_path: Path) -> None:
    """A repo with all eight dimensions present should score >=90."""
    root = _build_full_repo(tmp_path)
    report = ReadinessAssessor().assess(root)
    assert report.total >= 90, (
        f"Expected >=90, got {report.total}.\n"
        + "\n".join(
            f"  {d.name}: {d.score}/{d.max_score} -- {'; '.join(d.signals)}"
            for d in report.dimensions
        )
    )
    assert report.tier == "Production-ready -- full delegation"


def test_json_format_round_trips(tmp_path: Path) -> None:
    """``to_json`` output should parse back to a dict equal to ``to_dict``."""
    root = _build_full_repo(tmp_path)
    report = ReadinessAssessor().assess(root)

    payload = json.loads(report.to_json())
    assert payload == report.to_dict()
    # Spot-check structure.
    assert payload["total"] == report.total
    assert payload["tier"] == report.tier
    assert len(payload["dimensions"]) == 8
    for dim in payload["dimensions"]:
        assert set(dim.keys()) >= {
            "name",
            "title",
            "score",
            "max_score",
            "signals",
            "recommendation",
        }


def test_recommendations_present_for_low_dimensions(tmp_path: Path) -> None:
    """Every dimension scoring <8 must yield a one-line recommendation."""
    root = _empty_repo(tmp_path)
    report = ReadinessAssessor().assess(root)

    for dim in report.dimensions:
        if dim.score < 8:
            assert dim.recommendation, f"{dim.name} missing recommendation"
            assert "\n" not in dim.recommendation, (
                f"{dim.name} recommendation must be a single line"
            )

    # Also make sure full repo produces no (or few) recommendations.
    full_root = _build_full_repo(tmp_path)
    full_report = ReadinessAssessor().assess(full_root)
    for dim in full_report.dimensions:
        if dim.score >= 8:
            assert dim.recommendation is None


def test_markdown_output_is_well_formatted(tmp_path: Path) -> None:
    """Markdown rendering should include the headline sections + a table."""
    root = _build_full_repo(tmp_path)
    md = ReadinessAssessor().assess(root).to_markdown()

    assert md.startswith("# Org Readiness Assessment")
    assert "**Total:**" in md
    assert "## Dimension breakdown" in md
    assert "| # | Dimension | Score | Signals |" in md
    assert "## Recommended next steps" in md
    # Every dimension should appear by title.
    for title in (
        "Spec discipline",
        "Test coverage",
        "Conventions documented",
        "Knowledge stocked",
        "Agent roster",
        "Audit chain",
        "Bead memory",
        "CI integration",
    ):
        assert title in md, f"missing dimension title in markdown: {title}"


def test_assessment_completes_quickly(tmp_path: Path) -> None:
    """The assessor should run well under two seconds on a typical repo."""
    root = _build_full_repo(tmp_path)
    start = time.perf_counter()
    ReadinessAssessor().assess(root)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"assessment took {elapsed:.2f}s (>= 2s budget)"


def test_report_dataclass_shape() -> None:
    """Sanity check the dataclass contracts the spec calls out."""
    dim = DimensionScore(
        name="x",
        title="X",
        score=5,
        max_score=10,
        signals=["a"],
        recommendation="do thing",
    )
    report = ReadinessReport(
        project_root="/tmp/x",
        total=42,
        tier="Early -- pilot small tasks first",
        dimensions=[dim],
        recommendations=["do thing"],
    )
    assert report.total == 42
    assert report.tier.startswith("Early")
    assert isinstance(report.dimensions, list)
    assert isinstance(report.recommendations, list)
