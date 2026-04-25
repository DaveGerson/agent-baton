"""AI Bill of Materials (AIBOM) generation -- G1.7.

Pure observability: produces a per-task / per-PR provenance document
listing every model, agent, MCP server, knowledge attachment, and gate
that contributed to the work.  No friction is added to execution; this
module is read-side only.

Output formats
--------------
- ``markdown``  -- human-readable report (default)
- ``json``      -- structured JSON for machine consumers
- ``spdx-json`` -- SPDX 2.3-compatible SBOM JSON, ingestable by SBOM
  tooling.

Data sources
------------
- ``baton.db`` ``plans``, ``plan_phases``, ``plan_steps``, ``step_results``,
  ``agent_usage``, ``usage_records``, ``gate_results`` tables.
- Bundled agent definitions under ``agents/<name>.md`` (frontmatter
  ``mcp_servers:``).
- ``compliance-audit.jsonl`` chain head (last ``entry_hash``).

The builder is a strictly pure function of these inputs: it never
writes anywhere except an explicit ``--output`` PATH (handled in the
CLI command, not here).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import agent_baton as _pkg
from agent_baton.utils.frontmatter import parse_frontmatter


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelComponent:
    """A unique model that contributed to the task."""

    name: str
    total_tokens: int
    step_count: int
    agents: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentComponent:
    """A unique agent that participated in the task."""

    name: str
    step_count: int
    role: str = ""
    model: str = ""


@dataclass(frozen=True)
class McpServerComponent:
    """An MCP server attached to one or more participating agents."""

    name: str
    used_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class KnowledgeAttachment:
    """A knowledge pack/document attached during planning."""

    pack: str
    document: str = ""


@dataclass(frozen=True)
class GateRecord:
    """A gate that was executed during the task."""

    gate_type: str
    command: str
    outcome: str  # PASS | FAIL | SKIP


@dataclass(frozen=True)
class PullRequestInfo:
    """GitHub PR metadata (when --pr is supplied)."""

    number: int
    url: str = ""
    title: str = ""
    head: str = ""
    base: str = ""


@dataclass
class AIBOM:
    """A complete AI Bill of Materials for one task / PR."""

    schema_version: str
    generated_at: str
    generator: str
    task_id: str
    task_summary: str
    branch: str = ""
    commit_range: str = ""
    pull_request: PullRequestInfo | None = None
    models: list[ModelComponent] = field(default_factory=list)
    agents: list[AgentComponent] = field(default_factory=list)
    mcp_servers: list[McpServerComponent] = field(default_factory=list)
    knowledge: list[KnowledgeAttachment] = field(default_factory=list)
    gates: list[GateRecord] = field(default_factory=list)
    chain_anchor: str = ""

    # ----- serialization ----------------------------------------------------

    def to_json(self) -> str:
        """Serialize as the native JSON document."""
        return json.dumps(_aibom_to_dict(self), indent=2, sort_keys=False)

    def to_markdown(self) -> str:
        return _aibom_to_markdown(self)

    def to_spdx(self) -> str:
        return json.dumps(_aibom_to_spdx_dict(self), indent=2, sort_keys=False)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


AIBOM_SCHEMA_VERSION = "agent-baton-aibom/1.0"


class AIBOMBuilder:
    """Build an :class:`AIBOM` from a task's persisted state.

    Parameters
    ----------
    db_path:
        Path to the project's ``baton.db``.
    agents_dir:
        Directory containing distributable agent ``.md`` files.  Used for
        MCP server discovery.  When ``None`` we fall back to the bundled
        ``agents/`` next to the package and ``.claude/agents/``.
    compliance_log:
        Path to the ``compliance-audit.jsonl`` whose chain head will be
        the AIBOM's ``chain_anchor``.  When ``None`` we look at
        ``.claude/team-context/compliance-audit.jsonl`` next to ``db_path``.
    plan_json_path:
        Optional path to a ``plan.json`` file from which knowledge
        attachments are read.  When ``None`` we look next to ``baton.db``
        for ``plan.json``.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        agents_dir: Path | None = None,
        compliance_log: Path | None = None,
        plan_json_path: Path | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._agents_dir = agents_dir
        self._compliance_log = compliance_log
        self._plan_json_path = plan_json_path

    # ----- public ---------------------------------------------------------

    def build(
        self,
        task_id: str,
        *,
        branch: str = "",
        commit_range: str = "",
        pull_request: PullRequestInfo | None = None,
    ) -> AIBOM:
        """Build the AIBOM for *task_id*.

        Raises
        ------
        ValueError
            If the task does not exist in the database.
        """
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            plan_row = conn.execute(
                "SELECT task_summary, explicit_knowledge_packs, explicit_knowledge_docs "
                "FROM plans WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if plan_row is None:
                # Tolerate a task that has step_results but no plan (e.g. an
                # external execution).  Fall back to executions table if any.
                exec_row = conn.execute(
                    "SELECT 1 FROM executions WHERE task_id = ?", (task_id,),
                ).fetchone()
                if exec_row is None:
                    raise ValueError(
                        f"task_id {task_id!r} not found in plans or executions"
                    )
                task_summary = ""
                packs_raw = "[]"
                docs_raw = "[]"
            else:
                task_summary = plan_row["task_summary"] or ""
                packs_raw = plan_row["explicit_knowledge_packs"] or "[]"
                docs_raw = plan_row["explicit_knowledge_docs"] or "[]"

            models = self._collect_models(conn, task_id)
            agents = self._collect_agents(conn, task_id)
            mcp_servers = self._collect_mcp_servers(agents)
            knowledge = self._collect_knowledge(
                conn, task_id, packs_raw, docs_raw,
            )
            gates = self._collect_gates(conn, task_id)
        finally:
            conn.close()

        chain_anchor = self._chain_head_hash()

        return AIBOM(
            schema_version=AIBOM_SCHEMA_VERSION,
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            generator=f"agent-baton-{_pkg.__version__}",
            task_id=task_id,
            task_summary=task_summary,
            branch=branch,
            commit_range=commit_range,
            pull_request=pull_request,
            models=models,
            agents=agents,
            mcp_servers=mcp_servers,
            knowledge=knowledge,
            gates=gates,
            chain_anchor=chain_anchor,
        )

    # ----- collectors -----------------------------------------------------

    def _collect_models(
        self, conn: sqlite3.Connection, task_id: str,
    ) -> list[ModelComponent]:
        """Aggregate model usage from ``step_results`` and ``agent_usage``.

        ``step_results.model_id`` (v13) is the source of truth for per-step
        token accounting.  When that is missing (older rows, mid-flight
        execution) we fall back to ``agent_usage.model``.
        """
        rows = conn.execute(
            """
            SELECT
                COALESCE(NULLIF(model_id, ''), '') AS model,
                agent_name,
                COALESCE(input_tokens, 0)
                  + COALESCE(output_tokens, 0)
                  + COALESCE(cache_read_tokens, 0)
                  + COALESCE(cache_creation_tokens, 0) AS tokens,
                COALESCE(estimated_tokens, 0) AS est_tokens
            FROM step_results
            WHERE task_id = ?
            """,
            (task_id,),
        ).fetchall()

        # Group by model. Ignore rows with empty model unless that's all we have.
        per_model: dict[str, dict[str, Any]] = {}
        for r in rows:
            model = r["model"]
            tok = int(r["tokens"]) or int(r["est_tokens"])
            if not model:
                # Try to backfill from agent_usage by agent_name.
                au = conn.execute(
                    "SELECT model FROM agent_usage WHERE task_id = ? AND agent_name = ? LIMIT 1",
                    (task_id, r["agent_name"]),
                ).fetchone()
                if au is not None and au["model"]:
                    model = au["model"]
                else:
                    model = "unknown"
            entry = per_model.setdefault(
                model, {"tokens": 0, "steps": 0, "agents": set()},
            )
            entry["tokens"] += tok
            entry["steps"] += 1
            entry["agents"].add(r["agent_name"])

        # If step_results is empty but agent_usage has rows, fall back to that.
        if not per_model:
            au_rows = conn.execute(
                """
                SELECT model, agent_name,
                       COALESCE(estimated_tokens, 0) AS tokens,
                       COALESCE(steps, 1) AS steps
                FROM agent_usage WHERE task_id = ?
                """,
                (task_id,),
            ).fetchall()
            for r in au_rows:
                model = r["model"] or "unknown"
                entry = per_model.setdefault(
                    model, {"tokens": 0, "steps": 0, "agents": set()},
                )
                entry["tokens"] += int(r["tokens"])
                entry["steps"] += int(r["steps"])
                entry["agents"].add(r["agent_name"])

        return sorted(
            (
                ModelComponent(
                    name=name,
                    total_tokens=v["tokens"],
                    step_count=v["steps"],
                    agents=tuple(sorted(v["agents"])),
                )
                for name, v in per_model.items()
            ),
            key=lambda m: (-m.total_tokens, m.name),
        )

    def _collect_agents(
        self, conn: sqlite3.Connection, task_id: str,
    ) -> list[AgentComponent]:
        rows = conn.execute(
            """
            SELECT
                sr.agent_name AS name,
                COUNT(*) AS step_count,
                COALESCE(MAX(NULLIF(sr.model_id, '')), '') AS model
            FROM step_results sr
            WHERE sr.task_id = ?
            GROUP BY sr.agent_name
            """,
            (task_id,),
        ).fetchall()

        # Pick role from plan_steps.step_type when available.
        agents: list[AgentComponent] = []
        for r in rows:
            role_row = conn.execute(
                "SELECT step_type FROM plan_steps WHERE task_id = ? AND agent_name = ? LIMIT 1",
                (task_id, r["name"]),
            ).fetchone()
            role = role_row["step_type"] if role_row else ""
            model = r["model"]
            if not model:
                au = conn.execute(
                    "SELECT model FROM agent_usage WHERE task_id = ? AND agent_name = ? LIMIT 1",
                    (task_id, r["name"]),
                ).fetchone()
                if au is not None:
                    model = au["model"] or ""
            agents.append(
                AgentComponent(
                    name=r["name"],
                    step_count=int(r["step_count"]),
                    role=role,
                    model=model,
                )
            )

        # Fallback to plan_steps when no step_results yet (mid-execution).
        if not agents:
            ps = conn.execute(
                "SELECT agent_name, step_type, model FROM plan_steps WHERE task_id = ?",
                (task_id,),
            ).fetchall()
            grouped: dict[str, dict[str, Any]] = {}
            for r in ps:
                e = grouped.setdefault(
                    r["agent_name"],
                    {"steps": 0, "role": r["step_type"] or "", "model": r["model"] or ""},
                )
                e["steps"] += 1
            agents = [
                AgentComponent(
                    name=name, step_count=v["steps"], role=v["role"], model=v["model"],
                )
                for name, v in grouped.items()
            ]

        return sorted(agents, key=lambda a: (-a.step_count, a.name))

    def _collect_mcp_servers(
        self, agents: list[AgentComponent],
    ) -> list[McpServerComponent]:
        """Read frontmatter ``mcp_servers:`` from each participating agent."""
        agents_dirs = self._candidate_agents_dirs()
        per_server: dict[str, set[str]] = {}
        for agent in agents:
            servers = self._mcp_servers_for_agent(agent.name, agents_dirs)
            for s in servers:
                per_server.setdefault(s, set()).add(agent.name)
        return sorted(
            (
                McpServerComponent(name=name, used_by=tuple(sorted(used)))
                for name, used in per_server.items()
            ),
            key=lambda s: s.name,
        )

    def _collect_knowledge(
        self,
        conn: sqlite3.Connection,
        task_id: str,
        packs_raw: str,
        docs_raw: str,
    ) -> list[KnowledgeAttachment]:
        """Pull knowledge attachments from plan + plan_steps + plan.json."""
        attachments: dict[tuple[str, str], None] = {}

        # 1. plan-level explicit packs/docs
        for pack in _safe_json_list(packs_raw):
            attachments[(str(pack), "")] = None
        for doc in _safe_json_list(docs_raw):
            attachments[("", str(doc))] = None

        # 2. per-step knowledge_attachments
        rows = conn.execute(
            "SELECT knowledge_attachments FROM plan_steps WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        for r in rows:
            for item in _safe_json_list(r["knowledge_attachments"]):
                if isinstance(item, dict):
                    pack = str(item.get("pack", ""))
                    doc = str(item.get("document", "") or item.get("doc", ""))
                    attachments[(pack, doc)] = None
                else:
                    attachments[("", str(item))] = None

        # 3. plan.json (sidecar) -- best-effort, optional.
        plan_json = self._resolve_plan_json()
        if plan_json is not None and plan_json.exists():
            try:
                payload = json.loads(plan_json.read_text(encoding="utf-8"))
                for key in ("explicit_knowledge_packs", "knowledge_packs"):
                    for pack in payload.get(key, []) or []:
                        attachments[(str(pack), "")] = None
                for key in ("explicit_knowledge_docs", "knowledge_docs"):
                    for doc in payload.get(key, []) or []:
                        attachments[("", str(doc))] = None
            except (OSError, json.JSONDecodeError):
                pass

        return [
            KnowledgeAttachment(pack=pack, document=doc)
            for (pack, doc) in sorted(attachments.keys())
        ]

    def _collect_gates(
        self, conn: sqlite3.Connection, task_id: str,
    ) -> list[GateRecord]:
        rows = conn.execute(
            """
            SELECT gate_type, command, passed, output
            FROM gate_results
            WHERE task_id = ?
            ORDER BY id ASC
            """,
            (task_id,),
        ).fetchall()
        gates: list[GateRecord] = []
        for r in rows:
            output = (r["output"] or "").lower()
            if "skip" in output and not r["passed"]:
                outcome = "SKIP"
            else:
                outcome = "PASS" if int(r["passed"]) else "FAIL"
            gates.append(
                GateRecord(
                    gate_type=r["gate_type"] or "",
                    command=r["command"] or "",
                    outcome=outcome,
                )
            )
        return gates

    # ----- helpers --------------------------------------------------------

    def _candidate_agents_dirs(self) -> list[Path]:
        if self._agents_dir is not None:
            return [Path(self._agents_dir)]
        cwd = Path.cwd()
        candidates = [
            cwd / ".claude" / "agents",
            cwd / "agents",
            Path(_pkg.__file__).parent / "_bundled_agents",
        ]
        return [c for c in candidates if c.is_dir()]

    @staticmethod
    def _mcp_servers_for_agent(
        agent_name: str, dirs: list[Path],
    ) -> list[str]:
        for d in dirs:
            md = d / f"{agent_name}.md"
            if not md.exists():
                continue
            try:
                meta, _ = parse_frontmatter(md.read_text(encoding="utf-8"))
            except OSError:
                continue
            servers = meta.get("mcp_servers")
            if servers is None:
                return []
            if isinstance(servers, str):
                # comma-separated string
                return [s.strip() for s in servers.split(",") if s.strip()]
            if isinstance(servers, (list, tuple)):
                return [str(s) for s in servers if s]
            return []
        return []

    def _resolve_plan_json(self) -> Path | None:
        if self._plan_json_path is not None:
            return Path(self._plan_json_path)
        # baton.db lives at .claude/team-context/baton.db -> sibling plan.json
        sib = self._db_path.parent / "plan.json"
        return sib if sib.exists() else None

    def _chain_head_hash(self) -> str:
        log = self._compliance_log
        if log is None:
            log = self._db_path.parent / "compliance-audit.jsonl"
        if not log.exists():
            return ""
        try:
            with log.open("rb") as fh:
                raw = fh.read()
        except OSError:
            return ""
        for line in reversed(raw.splitlines()):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            h = obj.get("entry_hash")
            if isinstance(h, str) and len(h) == 64:
                return h
        return ""


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _safe_json_list(raw: Any) -> list[Any]:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        v = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return v if isinstance(v, list) else []


def _aibom_to_dict(b: AIBOM) -> dict[str, Any]:
    """Serialize AIBOM to a JSON-friendly dict, preserving order."""
    return {
        "schema_version": b.schema_version,
        "generated_at": b.generated_at,
        "generator": b.generator,
        "subject": {
            "task_id": b.task_id,
            "task_summary": b.task_summary,
            "branch": b.branch,
            "commit_range": b.commit_range,
            "pull_request": asdict(b.pull_request) if b.pull_request else None,
        },
        "components": {
            "models": [
                {
                    "name": m.name,
                    "total_tokens": m.total_tokens,
                    "step_count": m.step_count,
                    "agents": list(m.agents),
                }
                for m in b.models
            ],
            "agents": [
                {
                    "name": a.name,
                    "step_count": a.step_count,
                    "role": a.role,
                    "model": a.model,
                }
                for a in b.agents
            ],
            "mcp_servers": [
                {"name": s.name, "used_by": list(s.used_by)}
                for s in b.mcp_servers
            ],
        },
        "knowledge": [
            {"pack": k.pack, "document": k.document} for k in b.knowledge
        ],
        "gates": [asdict(g) for g in b.gates],
        "chain_anchor": b.chain_anchor,
    }


def _aibom_to_markdown(b: AIBOM) -> str:
    lines: list[str] = []
    lines.append(f"# AIBOM -- {b.task_id}")
    lines.append("")
    lines.append(f"- **Schema**: {b.schema_version}")
    lines.append(f"- **Generated at**: {b.generated_at}")
    lines.append(f"- **Generator**: {b.generator}")
    lines.append("")

    lines.append("## Subject")
    lines.append("")
    lines.append(f"- **Task ID**: {b.task_id}")
    summary = b.task_summary or "(no summary recorded)"
    lines.append(f"- **Summary**: {summary}")
    if b.branch:
        lines.append(f"- **Branch**: {b.branch}")
    if b.commit_range:
        lines.append(f"- **Commit range**: {b.commit_range}")
    if b.pull_request is not None:
        pr = b.pull_request
        lines.append(
            f"- **Pull request**: #{pr.number} -- {pr.title or '(no title)'}"
        )
        if pr.url:
            lines.append(f"  - URL: {pr.url}")
        if pr.head and pr.base:
            lines.append(f"  - {pr.head} -> {pr.base}")
    lines.append("")

    lines.append("## Components -- Models")
    lines.append("")
    if not b.models:
        lines.append("_No model usage recorded._")
    else:
        for m in b.models:
            agents_str = "/".join(m.agents) if m.agents else "(no agent)"
            lines.append(
                f"- {m.name}  -> {m.total_tokens:,} tokens "
                f"· {m.step_count} steps · {agents_str}"
            )
    lines.append("")

    lines.append("## Components -- Agents")
    lines.append("")
    if not b.agents:
        lines.append("_No agent participation recorded._")
    else:
        for a in b.agents:
            extras: list[str] = []
            if a.role:
                extras.append(f"role={a.role}")
            if a.model:
                extras.append(f"model={a.model}")
            tail = f" ({', '.join(extras)})" if extras else ""
            lines.append(f"- {a.name}  -> {a.step_count} steps{tail}")
    lines.append("")

    lines.append("## Components -- MCP servers")
    lines.append("")
    if not b.mcp_servers:
        lines.append("_No MCP servers attached to participating agents._")
    else:
        for s in b.mcp_servers:
            users = ", ".join(s.used_by) if s.used_by else ""
            lines.append(f"- {s.name}  (used by: {users})")
    lines.append("")

    lines.append("## Knowledge attachments")
    lines.append("")
    if not b.knowledge:
        lines.append("_No knowledge attachments._")
    else:
        for k in b.knowledge:
            if k.pack and k.document:
                lines.append(f"- {k.pack}::{k.document}")
            elif k.pack:
                lines.append(f"- pack: {k.pack}")
            else:
                lines.append(f"- doc: {k.document}")
    lines.append("")

    lines.append("## Gates run")
    lines.append("")
    if not b.gates:
        lines.append("_No gates recorded._")
    else:
        for g in b.gates:
            cmd = g.command or "(no command)"
            lines.append(f"- [{g.outcome}] {g.gate_type}: `{cmd}`")
    lines.append("")

    lines.append("## Hash anchor")
    lines.append("")
    if b.chain_anchor:
        lines.append(f"`{b.chain_anchor}`")
        lines.append("")
        lines.append(
            "_SHA-256 of the compliance-audit chain head at the time of "
            "AIBOM generation._"
        )
    else:
        lines.append("_No compliance-audit chain found._")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# SPDX 2.3 emitter (hand-rolled; no external dependency)
# ---------------------------------------------------------------------------


_SPDX_NOASSERTION = "NOASSERTION"


def _spdx_id(prefix: str, name: str) -> str:
    """Return an SPDX-compliant identifier (alphanumeric + dot/hyphen)."""
    safe = "".join(c if c.isalnum() or c in "-." else "-" for c in name)
    return f"SPDXRef-{prefix}-{safe}"


def _aibom_to_spdx_dict(b: AIBOM) -> dict[str, Any]:
    """Render the AIBOM as an SPDX 2.3 JSON document.

    Includes only the fields needed to satisfy SBOM-aware tooling:
    ``spdxVersion``, ``dataLicense``, ``SPDXID``, ``name``,
    ``documentNamespace``, ``creationInfo`` and ``packages``.
    """
    namespace = f"urn:agent-baton:aibom:{b.task_id}"
    packages: list[dict[str, Any]] = []

    for m in b.models:
        packages.append(
            {
                "SPDXID": _spdx_id("Model", m.name),
                "name": m.name,
                "versionInfo": _SPDX_NOASSERTION,
                "supplier": _SPDX_NOASSERTION,
                "downloadLocation": _SPDX_NOASSERTION,
                "filesAnalyzed": False,
                "licenseConcluded": _SPDX_NOASSERTION,
                "licenseDeclared": _SPDX_NOASSERTION,
                "copyrightText": _SPDX_NOASSERTION,
                "primaryPackagePurpose": "APPLICATION",
                "comment": (
                    f"AI model component. tokens={m.total_tokens}, "
                    f"steps={m.step_count}, agents={','.join(m.agents)}"
                ),
            }
        )

    for a in b.agents:
        packages.append(
            {
                "SPDXID": _spdx_id("Agent", a.name),
                "name": a.name,
                "versionInfo": _SPDX_NOASSERTION,
                "supplier": "Tool: agent-baton",
                "downloadLocation": _SPDX_NOASSERTION,
                "filesAnalyzed": False,
                "licenseConcluded": _SPDX_NOASSERTION,
                "licenseDeclared": _SPDX_NOASSERTION,
                "copyrightText": _SPDX_NOASSERTION,
                "primaryPackagePurpose": "APPLICATION",
                "comment": (
                    f"Agent component. role={a.role}, model={a.model}, "
                    f"steps={a.step_count}"
                ),
            }
        )

    for s in b.mcp_servers:
        packages.append(
            {
                "SPDXID": _spdx_id("McpServer", s.name),
                "name": s.name,
                "versionInfo": _SPDX_NOASSERTION,
                "supplier": _SPDX_NOASSERTION,
                "downloadLocation": _SPDX_NOASSERTION,
                "filesAnalyzed": False,
                "licenseConcluded": _SPDX_NOASSERTION,
                "licenseDeclared": _SPDX_NOASSERTION,
                "copyrightText": _SPDX_NOASSERTION,
                "primaryPackagePurpose": "APPLICATION",
                "comment": f"MCP server. used_by={','.join(s.used_by)}",
            }
        )

    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"agent-baton-aibom-{b.task_id}",
        "documentNamespace": namespace,
        "creationInfo": {
            "created": b.generated_at,
            "creators": [f"Tool: {b.generator}"],
            "comment": (
                f"AIBOM for task {b.task_id}. "
                f"chain_anchor={b.chain_anchor or 'none'}"
            ),
        },
        "packages": packages,
        "documentDescribes": ["SPDXRef-DOCUMENT"],
        "comment": (
            f"task_summary={b.task_summary[:120]} | "
            f"branch={b.branch} | commit_range={b.commit_range}"
        ),
    }


# ---------------------------------------------------------------------------
# Convenience -- module level
# ---------------------------------------------------------------------------


def sha256_text(text: str) -> str:
    """Return the SHA-256 hex digest of *text*. Exposed for tests."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
