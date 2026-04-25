"""Tests for G1.7 AIBOM (AI Bill of Materials) generator."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent_baton.core.govern.aibom import (
    AIBOM,
    AIBOMBuilder,
    AIBOM_SCHEMA_VERSION,
    PullRequestInfo,
)
from agent_baton.core.govern.compliance import ComplianceChainWriter
from agent_baton.core.storage.connection import ConnectionManager
from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


TASK_ID = "task-aibom-test"


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Provision a baton.db with a known task, plan, step results, gates."""
    p = tmp_path / "baton.db"
    # Use the project's ConnectionManager so the schema matches production
    # (incl. all migrations applied).
    cm = ConnectionManager(p)
    cm.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
    conn = cm.get_connection()
    now = "2026-04-25T10:00:00Z"

    conn.execute(
        "INSERT INTO executions (task_id, status, started_at) VALUES (?, ?, ?)",
        (TASK_ID, "complete", now),
    )
    conn.execute(
        """
        INSERT INTO plans (
            task_id, task_summary, risk_level, budget_tier, execution_mode,
            git_strategy, plan_markdown, created_at,
            explicit_knowledge_packs, explicit_knowledge_docs,
            intervention_level, task_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            TASK_ID,
            "Implement AIBOM generation",
            "LOW",
            "standard",
            "phased",
            "commit-per-agent",
            "# Plan",
            now,
            json.dumps(["governance-basics"]),
            json.dumps(["spdx-spec.md"]),
            "low",
            "feature",
        ),
    )
    conn.execute(
        """INSERT INTO plan_phases (task_id, phase_id, name) VALUES (?, ?, ?)""",
        (TASK_ID, 1, "Implement"),
    )
    conn.execute(
        """
        INSERT INTO plan_steps (
            task_id, step_id, phase_id, agent_name, model,
            knowledge_attachments, step_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            TASK_ID, "1.1", 1, "backend-engineer--python", "sonnet",
            json.dumps([{"pack": "py-best-practices", "document": "typing.md"}]),
            "developing",
        ),
    )
    conn.execute(
        """
        INSERT INTO plan_steps (
            task_id, step_id, phase_id, agent_name, model,
            knowledge_attachments, step_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            TASK_ID, "1.2", 1, "code-reviewer", "opus",
            json.dumps([]), "reviewing",
        ),
    )

    # Step results (drives model + agent aggregation).
    conn.execute(
        """
        INSERT INTO step_results (
            task_id, step_id, agent_name, status, outcome,
            input_tokens, output_tokens, cache_read_tokens,
            cache_creation_tokens, model_id, completed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            TASK_ID, "1.1", "backend-engineer--python", "complete", "ok",
            10000, 2000, 500, 0, "claude-sonnet-4-6", now,
        ),
    )
    conn.execute(
        """
        INSERT INTO step_results (
            task_id, step_id, agent_name, status, outcome,
            input_tokens, output_tokens, cache_read_tokens,
            cache_creation_tokens, model_id, completed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            TASK_ID, "1.2", "code-reviewer", "complete", "ok",
            5000, 1000, 0, 0, "claude-opus-4-7", now,
        ),
    )
    # A second sonnet step to exercise aggregation.
    conn.execute(
        """
        INSERT INTO step_results (
            task_id, step_id, agent_name, status, outcome,
            input_tokens, output_tokens, cache_read_tokens,
            cache_creation_tokens, model_id, completed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            TASK_ID, "1.3", "backend-engineer--python", "complete", "ok",
            7000, 800, 0, 0, "claude-sonnet-4-6", now,
        ),
    )

    # Gates -- one PASS, one FAIL.
    conn.execute(
        """
        INSERT INTO gate_results (
            task_id, phase_id, gate_type, passed, output, command, checked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (TASK_ID, 1, "build", 1, "ok", "pytest", now),
    )
    conn.execute(
        """
        INSERT INTO gate_results (
            task_id, phase_id, gate_type, passed, output, command, checked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (TASK_ID, 1, "lint", 0, "skipped by config", "ruff check", now),
    )

    conn.commit()
    cm.close()
    return p


@pytest.fixture()
def agents_dir(tmp_path: Path) -> Path:
    """Create a tiny agents/ dir with mcp_servers frontmatter for two agents."""
    d = tmp_path / "agents"
    d.mkdir()
    (d / "backend-engineer--python.md").write_text(
        "---\nname: backend-engineer--python\n"
        "model: sonnet\n"
        "mcp_servers:\n  - filesystem\n  - github\n---\nbody\n",
        encoding="utf-8",
    )
    (d / "code-reviewer.md").write_text(
        "---\nname: code-reviewer\nmodel: opus\n"
        "mcp_servers:\n  - github\n  - postgres\n---\nbody\n",
        encoding="utf-8",
    )
    return d


@pytest.fixture()
def compliance_log(tmp_path: Path) -> Path:
    """Append two entries so we have a real chain head."""
    log = tmp_path / "compliance-audit.jsonl"
    writer = ComplianceChainWriter(log_path=log)
    writer.append({"event": "task_started", "task_id": TASK_ID})
    writer.append({"event": "task_completed", "task_id": TASK_ID})
    return log


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_build_from_stub_task(
    db_path: Path, agents_dir: Path, compliance_log: Path,
) -> None:
    builder = AIBOMBuilder(
        db_path=db_path,
        agents_dir=agents_dir,
        compliance_log=compliance_log,
    )
    aibom = builder.build(TASK_ID, branch="feature/aibom", commit_range="master..HEAD")

    assert isinstance(aibom, AIBOM)
    assert aibom.task_id == TASK_ID
    assert aibom.task_summary == "Implement AIBOM generation"
    assert aibom.branch == "feature/aibom"
    assert aibom.commit_range == "master..HEAD"
    assert aibom.schema_version == AIBOM_SCHEMA_VERSION
    assert aibom.generator.startswith("agent-baton-")

    # Two distinct models: sonnet (2 steps, 20.3k tok) + opus (1 step, 6k tok)
    by_name = {m.name: m for m in aibom.models}
    assert "claude-sonnet-4-6" in by_name
    assert "claude-opus-4-7" in by_name
    sonnet = by_name["claude-sonnet-4-6"]
    assert sonnet.step_count == 2
    assert sonnet.total_tokens == 10000 + 2000 + 500 + 7000 + 800
    assert "backend-engineer--python" in sonnet.agents

    # Two distinct agents.
    agent_names = {a.name for a in aibom.agents}
    assert agent_names == {"backend-engineer--python", "code-reviewer"}

    # Gates -- one PASS, one SKIP (output contains "skip").
    assert {g.outcome for g in aibom.gates} == {"PASS", "SKIP"}

    # Knowledge: from plan + plan_steps deduped.
    assert any(k.pack == "py-best-practices" and k.document == "typing.md"
               for k in aibom.knowledge)
    assert any(k.pack == "governance-basics" for k in aibom.knowledge)

    # Chain anchor matches the writer's last hash.
    head = ComplianceChainWriter(log_path=compliance_log)._last_hash()
    assert aibom.chain_anchor == head
    assert len(aibom.chain_anchor) == 64


def test_markdown_contains_all_sections(
    db_path: Path, agents_dir: Path, compliance_log: Path,
) -> None:
    aibom = AIBOMBuilder(
        db_path=db_path, agents_dir=agents_dir, compliance_log=compliance_log,
    ).build(TASK_ID, branch="feature/x")
    md = aibom.to_markdown()

    for header in (
        f"# AIBOM -- {TASK_ID}",
        "## Subject",
        "## Components -- Models",
        "## Components -- Agents",
        "## Components -- MCP servers",
        "## Knowledge attachments",
        "## Gates run",
        "## Hash anchor",
    ):
        assert header in md, f"missing section header: {header}"

    # Specific data should surface.
    assert "claude-sonnet-4-6" in md
    assert "claude-opus-4-7" in md
    assert "backend-engineer--python" in md
    assert "code-reviewer" in md
    assert "filesystem" in md  # MCP server from sonnet agent
    assert "postgres" in md    # MCP server from opus agent
    assert "[PASS]" in md
    assert "[SKIP]" in md


def test_json_round_trips(
    db_path: Path, agents_dir: Path, compliance_log: Path,
) -> None:
    aibom = AIBOMBuilder(
        db_path=db_path, agents_dir=agents_dir, compliance_log=compliance_log,
    ).build(TASK_ID)
    raw = aibom.to_json()
    data = json.loads(raw)

    assert data["schema_version"] == AIBOM_SCHEMA_VERSION
    assert data["subject"]["task_id"] == TASK_ID
    assert {m["name"] for m in data["components"]["models"]} == {
        "claude-sonnet-4-6", "claude-opus-4-7",
    }
    assert {a["name"] for a in data["components"]["agents"]} == {
        "backend-engineer--python", "code-reviewer",
    }
    assert data["chain_anchor"] == aibom.chain_anchor

    # Round-trip stability: re-emit, re-parse, equal.
    second = json.loads(aibom.to_json())
    assert second == data


def test_spdx_json_has_required_fields(
    db_path: Path, agents_dir: Path, compliance_log: Path,
) -> None:
    aibom = AIBOMBuilder(
        db_path=db_path, agents_dir=agents_dir, compliance_log=compliance_log,
    ).build(TASK_ID)
    spdx = json.loads(aibom.to_spdx())

    assert spdx["spdxVersion"] == "SPDX-2.3"
    assert spdx["dataLicense"] == "CC0-1.0"
    assert spdx["SPDXID"] == "SPDXRef-DOCUMENT"
    assert spdx["documentNamespace"] == f"urn:agent-baton:aibom:{TASK_ID}"

    creation_info = spdx["creationInfo"]
    assert any(c.startswith("Tool: agent-baton-") for c in creation_info["creators"])
    assert "created" in creation_info

    names = {p["name"] for p in spdx["packages"]}
    # one package per model, agent, mcp server
    assert "claude-sonnet-4-6" in names
    assert "claude-opus-4-7" in names
    assert "backend-engineer--python" in names
    assert "code-reviewer" in names
    assert "filesystem" in names
    assert "postgres" in names

    # Every package must declare the required SPDX fields.
    for pkg in spdx["packages"]:
        assert pkg["SPDXID"].startswith("SPDXRef-")
        assert "name" in pkg
        assert "versionInfo" in pkg
        assert "supplier" in pkg
        assert "downloadLocation" in pkg
        assert pkg["licenseConcluded"] == "NOASSERTION"


def test_hash_anchor_matches_chain_head(
    db_path: Path, agents_dir: Path, tmp_path: Path,
) -> None:
    log = tmp_path / "compliance-audit.jsonl"
    writer = ComplianceChainWriter(log_path=log)
    e1 = writer.append({"event": "first"})
    e2 = writer.append({"event": "second"})
    e3 = writer.append({"event": "third"})

    aibom = AIBOMBuilder(
        db_path=db_path, agents_dir=agents_dir, compliance_log=log,
    ).build(TASK_ID)

    assert aibom.chain_anchor == e3["entry_hash"]
    assert aibom.chain_anchor != e1["entry_hash"]
    assert aibom.chain_anchor != e2["entry_hash"]


def test_mcp_server_dedupe_across_agents(
    db_path: Path, agents_dir: Path, compliance_log: Path,
) -> None:
    """The two test agents both reference 'github'; it must appear once
    in mcp_servers with both agents listed under used_by."""
    aibom = AIBOMBuilder(
        db_path=db_path, agents_dir=agents_dir, compliance_log=compliance_log,
    ).build(TASK_ID)

    server_names = [s.name for s in aibom.mcp_servers]
    # No duplicates.
    assert len(server_names) == len(set(server_names))
    by_name = {s.name: s for s in aibom.mcp_servers}
    assert "github" in by_name
    # Both agents that reference github appear in used_by.
    assert set(by_name["github"].used_by) == {
        "backend-engineer--python", "code-reviewer",
    }
    # filesystem only on python agent.
    assert by_name["filesystem"].used_by == ("backend-engineer--python",)
    # postgres only on reviewer agent.
    assert by_name["postgres"].used_by == ("code-reviewer",)


def test_unknown_task_raises(db_path: Path, agents_dir: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        AIBOMBuilder(db_path=db_path, agents_dir=agents_dir).build(
            "nonexistent-task",
        )


def test_pull_request_info_renders(
    db_path: Path, agents_dir: Path, compliance_log: Path,
) -> None:
    pr = PullRequestInfo(
        number=42,
        url="https://github.com/o/r/pull/42",
        title="Add AIBOM",
        head="feature/aibom",
        base="master",
    )
    aibom = AIBOMBuilder(
        db_path=db_path, agents_dir=agents_dir, compliance_log=compliance_log,
    ).build(TASK_ID, pull_request=pr)

    assert aibom.pull_request == pr
    md = aibom.to_markdown()
    assert "#42" in md
    assert "https://github.com/o/r/pull/42" in md
    assert "feature/aibom -> master" in md

    data = json.loads(aibom.to_json())
    assert data["subject"]["pull_request"]["number"] == 42
    assert data["subject"]["pull_request"]["url"] == "https://github.com/o/r/pull/42"
