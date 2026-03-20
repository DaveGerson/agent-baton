"""Shared pytest fixtures for agent_baton tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.registry import AgentRegistry


# ---------------------------------------------------------------------------
# Sample agent markdown content strings
# ---------------------------------------------------------------------------

BACKEND_PYTHON_CONTENT = """\
---
name: backend-engineer--python
description: Python backend specialist. Knows FastAPI, Django, Flask.
model: sonnet
permissionMode: auto-edit
color: blue
tools: Read, Write, Edit, Glob, Grep, Bash
---

# Backend Engineer — Python

You are a senior Python backend engineer.
"""

ARCHITECT_CONTENT = """\
---
name: architect
description: |
  Specialist for system design and architectural planning. Use for data model
  design, API contract definition, and technology selection.
model: opus
permissionMode: default
color: red
tools: Read, Glob, Grep
---

# Software Architect

You are a senior software architect.
"""

FRONTEND_REACT_CONTENT = """\
---
name: frontend-engineer--react
description: React/TypeScript frontend specialist.
model: sonnet
permissionMode: auto-edit
color: green
tools: Read, Write, Edit, Bash
---

# Frontend Engineer — React

You are a senior React frontend engineer.
"""

SECURITY_REVIEWER_CONTENT = """\
---
name: security-reviewer
description: Security-focused code reviewer.
model: opus
permissionMode: default
tools: Read, Glob, Grep
---

# Security Reviewer

You audit code for security vulnerabilities.
"""

BACKEND_NODE_CONTENT = """\
---
name: backend-engineer--node
description: Node.js backend specialist.
model: sonnet
permissionMode: auto-edit
color: yellow
tools: Read, Write, Edit, Bash
---

# Backend Engineer — Node

You are a senior Node.js backend engineer.
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_agent_content() -> str:
    """Raw string content of a valid agent .md file."""
    return BACKEND_PYTHON_CONTENT


@pytest.fixture
def tmp_agents_dir(tmp_path: Path) -> Path:
    """A tmp directory containing 4-5 sample agent .md files."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    (agents_dir / "backend-engineer--python.md").write_text(
        BACKEND_PYTHON_CONTENT, encoding="utf-8"
    )
    (agents_dir / "architect.md").write_text(
        ARCHITECT_CONTENT, encoding="utf-8"
    )
    (agents_dir / "frontend-engineer--react.md").write_text(
        FRONTEND_REACT_CONTENT, encoding="utf-8"
    )
    (agents_dir / "security-reviewer.md").write_text(
        SECURITY_REVIEWER_CONTENT, encoding="utf-8"
    )
    (agents_dir / "backend-engineer--node.md").write_text(
        BACKEND_NODE_CONTENT, encoding="utf-8"
    )

    return agents_dir


@pytest.fixture
def tmp_project_root(tmp_path: Path) -> Path:
    """A tmp directory with fake stack-detection marker files."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "myapp"\n', encoding="utf-8"
    )
    (project / "package.json").write_text(
        '{"name": "myapp"}\n', encoding="utf-8"
    )
    return project


@pytest.fixture
def tmp_team_context(tmp_path: Path) -> Path:
    """A tmp directory to use as the team-context directory root."""
    ctx_dir = tmp_path / "team-context"
    # Deliberately do NOT create the directory — ContextManager.ensure_dir()
    # should create it on first write, which is part of what we test.
    return ctx_dir


@pytest.fixture
def registry_with_agents(tmp_agents_dir: Path) -> AgentRegistry:
    """An AgentRegistry pre-loaded from tmp_agents_dir."""
    registry = AgentRegistry()
    registry.load_directory(tmp_agents_dir)
    return registry
