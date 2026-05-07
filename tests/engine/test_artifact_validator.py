"""Tests for ``agent_baton.core.engine.artifact_validator``.

Covers the regression for the "trusted-but-unverified" gate bug: when an
agent creates a runnable artifact (CI workflow, npm script, Playwright
config) during a phase, the derived commands must be emitted so the
phase gate can exercise them.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.core.engine.artifact_validator import (
    ArtifactValidator,
    DerivedCommand,
    _common_dir_prefix,
    _extract_workflow_run_lines,
    _first_command_line,
    _is_gate_script,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(root: Path, rel: str, content: str) -> str:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return rel


# ---------------------------------------------------------------------------
# CI workflow extraction
# ---------------------------------------------------------------------------


def test_workflow_run_lines_yaml(tmp_path: Path) -> None:
    rel = _write(
        tmp_path,
        ".github/workflows/ci-fast.yml",
        """\
name: ci-fast
on: [push]
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Audit
        run: npm audit --audit-level=high
      - name: Test
        run: |
          npm ci
          npm test
""",
    )
    derived = ArtifactValidator(tmp_path).derive_commands([rel])
    cmds = [d.command for d in derived]
    assert "npm audit --audit-level=high" in cmds
    # Multi-line "run: |" blocks: only the first non-empty line is lifted.
    assert "npm ci" in cmds


def test_workflow_skips_runner_only_expressions(tmp_path: Path) -> None:
    rel = _write(
        tmp_path,
        ".github/workflows/deploy.yml",
        """\
jobs:
  job:
    runs-on: ubuntu-latest
    steps:
      - run: echo "${{ secrets.TOKEN }}"
      - run: echo $GITHUB_SHA
      - run: npm run lint
""",
    )
    cmds = [d.command for d in ArtifactValidator(tmp_path).derive_commands([rel])]
    assert "npm run lint" in cmds
    assert not any("${{" in c for c in cmds)
    assert not any("$GITHUB_" in c for c in cmds)


def test_workflow_regex_fallback_when_yaml_invalid(tmp_path: Path) -> None:
    rel = _write(
        tmp_path,
        ".github/workflows/broken.yml",
        # Intentionally malformed YAML — yaml.safe_load raises and we
        # fall back to the regex sweep.
        "jobs:\n  job:\n    steps:\n      - run: pytest -q\n  : not valid",
    )
    cmds = [d.command for d in ArtifactValidator(tmp_path).derive_commands([rel])]
    assert "pytest -q" in cmds


def test_workflow_caps_commands_per_file(tmp_path: Path) -> None:
    steps = "\n".join(f"      - run: echo step-{i}" for i in range(20))
    rel = _write(
        tmp_path,
        ".github/workflows/big.yml",
        f"jobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n{steps}\n",
    )
    derived = ArtifactValidator(tmp_path).derive_commands([rel])
    assert len(derived) <= 8  # _MAX_COMMANDS_PER_FILE


# ---------------------------------------------------------------------------
# package.json
# ---------------------------------------------------------------------------


def test_package_json_emits_gate_scripts(tmp_path: Path) -> None:
    rel = _write(
        tmp_path,
        "package.json",
        json.dumps(
            {
                "scripts": {
                    "test": "vitest",
                    "test:e2e": "playwright test",
                    "lint": "eslint .",
                    "typecheck": "tsc --noEmit",
                    "audit": "npm audit --audit-level=high",
                    "build": "tsc",  # not gate-worthy
                    "dev": "vite",  # not gate-worthy
                }
            }
        ),
    )
    cmds = [d.command for d in ArtifactValidator(tmp_path).derive_commands([rel])]
    assert "npm run test" in cmds
    assert "npm run test:e2e" in cmds
    assert "npm run lint" in cmds
    assert "npm run typecheck" in cmds
    assert "npm run audit" in cmds
    assert "npm run build" not in cmds
    assert "npm run dev" not in cmds


def test_package_json_invalid_returns_no_commands(tmp_path: Path) -> None:
    rel = _write(tmp_path, "package.json", "{ not json")
    assert ArtifactValidator(tmp_path).derive_commands([rel]) == []


# ---------------------------------------------------------------------------
# Playwright
# ---------------------------------------------------------------------------


def test_playwright_emits_e2e_when_script_exists(tmp_path: Path) -> None:
    pkg = _write(
        tmp_path,
        "package.json",
        json.dumps({"scripts": {"test:e2e": "playwright test"}}),
    )
    cfg = _write(tmp_path, "playwright.config.ts", "export default {};")
    cmds = [d.command for d in ArtifactValidator(tmp_path).derive_commands([cfg, pkg])]
    assert "npm run test:e2e" in cmds


def test_playwright_silent_when_script_missing(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "package.json",
        json.dumps({"scripts": {"test": "vitest"}}),
    )
    cfg = _write(tmp_path, "playwright.config.ts", "export default {};")
    derived = ArtifactValidator(tmp_path).derive_commands([cfg])
    # No test:e2e in package.json — so we must not emit a command that
    # would fail for the wrong reason.
    assert not any(d.command == "npm run test:e2e" for d in derived)


# ---------------------------------------------------------------------------
# General behaviour
# ---------------------------------------------------------------------------


def test_unrecognised_files_are_ignored(tmp_path: Path) -> None:
    _write(tmp_path, "src/index.ts", "console.log('hello');")
    _write(tmp_path, "README.md", "# hi")
    derived = ArtifactValidator(tmp_path).derive_commands(
        ["src/index.ts", "README.md"]
    )
    assert derived == []


def test_empty_input_returns_empty(tmp_path: Path) -> None:
    assert ArtifactValidator(tmp_path).derive_commands(None) == []
    assert ArtifactValidator(tmp_path).derive_commands([]) == []


def test_disabled_via_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rel = _write(
        tmp_path,
        ".github/workflows/ci.yml",
        "jobs:\n  j:\n    steps:\n      - run: pytest -q\n",
    )
    monkeypatch.setenv("BATON_ARTIFACT_VALIDATION", "0")
    assert ArtifactValidator(tmp_path).derive_commands([rel]) == []


def test_normalises_windows_paths(tmp_path: Path) -> None:
    rel = _write(
        tmp_path,
        ".github/workflows/ci.yml",
        "jobs:\n  j:\n    steps:\n      - run: pytest -q\n",
    )
    win_path = rel.replace("/", "\\")
    cmds = [d.command for d in ArtifactValidator(tmp_path).derive_commands([win_path])]
    assert "pytest -q" in cmds


def test_no_root_skips_file_inspection() -> None:
    # Without a project root, content-based derivations cannot run —
    # but path-only derivations also cannot, since Playwright depends
    # on package.json.  The validator must simply return empty rather
    # than emit broken commands.
    derived = ArtifactValidator(None).derive_commands(
        [".github/workflows/ci.yml", "package.json", "playwright.config.ts"]
    )
    assert derived == []


def test_derived_command_dataclass_carries_attribution(tmp_path: Path) -> None:
    rel = _write(
        tmp_path,
        ".github/workflows/ci.yml",
        "jobs:\n  j:\n    steps:\n      - run: pytest -q\n",
    )
    derived = ArtifactValidator(tmp_path).derive_commands([rel])
    assert all(isinstance(d, DerivedCommand) for d in derived)
    assert all(d.source_file == rel for d in derived)
    assert all(d.rationale for d in derived)


def test_derive_deduplicates_across_files(tmp_path: Path) -> None:
    a = _write(
        tmp_path,
        ".github/workflows/a.yml",
        "jobs:\n  j:\n    steps:\n      - run: pytest -q\n",
    )
    b = _write(
        tmp_path,
        ".github/workflows/b.yml",
        "jobs:\n  j:\n    steps:\n      - run: pytest -q\n",
    )
    derived = ArtifactValidator(tmp_path).derive_commands([a, b])
    assert sum(1 for d in derived if d.command == "pytest -q") == 1


def test_path_traversal_is_refused(tmp_path: Path) -> None:
    # Even when the path resolves outside the project root, the
    # validator must not read it.  No commands derive.
    outside = tmp_path.parent / "outside.yml"
    outside.write_text(
        "jobs:\n  j:\n    steps:\n      - run: rm -rf /\n",
        encoding="utf-8",
    )
    rel = "../" + outside.name
    assert ArtifactValidator(tmp_path).derive_commands([rel]) == []


# ---------------------------------------------------------------------------
# Free helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("test", True),
        ("test:e2e", True),
        ("test:unit", True),
        ("lint", True),
        ("typecheck", True),
        ("audit", True),
        ("build", False),
        ("dev", False),
        ("start", False),
        ("Test", False),  # case-sensitive on purpose
    ],
)
def test_is_gate_script(name: str, expected: bool) -> None:
    assert _is_gate_script(name) is expected


def test_first_command_line_skips_blank_and_comments() -> None:
    block = "\n# preamble\n\nnpm ci\nnpm test\n"
    assert _first_command_line(block) == "npm ci"


def test_common_dir_prefix() -> None:
    assert _common_dir_prefix("a/b/c.txt", "a/b/d.txt") == "a/b"
    assert _common_dir_prefix("a/b/c.txt", "x/y/z.txt") == ""
    assert _common_dir_prefix("a/b/c.txt", "a/x/y.txt") == "a"


def test_extract_workflow_run_lines_handles_empty_yaml() -> None:
    assert _extract_workflow_run_lines("") == []
    assert _extract_workflow_run_lines("name: foo\n") == []
