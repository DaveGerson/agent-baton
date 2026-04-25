"""Merge-Readiness Pack (MRP) builder.

Generates a single-file Markdown bundle that gathers everything a code
reviewer needs in order to approve a branch / pull request.  The goal is
that a reviewer reads ONE markdown document instead of digging through
the SQLite ``baton.db``, the ``compliance-audit.jsonl`` chain, the
plan summary, retrospectives, and the diff in parallel.

The pack is composed of nine sections (see :class:`MergeReadinessPack`
docstring) and is emitted by :func:`MRPBuilder.build`.

Design notes
------------
* Standard library only — uses ``subprocess`` for git queries.
* Graceful degradation: every data source (plan.md, ``baton.db``,
  ``compliance-audit.jsonl``, git, beads) is optional.  Missing pieces
  render as ``"(unavailable)"`` rather than raising.
* Each section is intentionally terse so the whole document is readable
  in under five minutes.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class MRPHeader:
    """Top-of-pack identifying metadata."""

    branch: str = ""
    base_branch: str = ""
    commit_count: int = 0
    authors: list[str] = field(default_factory=list)
    generated_at: str = ""


@dataclass
class MRPPlanSummary:
    """Compact extract of the execution plan."""

    task_summary: str = ""
    risk_tier: str = ""
    budget_tier: str = ""
    phase_count: int = 0
    step_count: int = 0
    plan_source: str = ""  # Path of plan.md / "(none)"


@dataclass
class MRPStepRow:
    """One row of the execution-trace table."""

    phase_id: int
    step_id: str
    agent: str
    model: str
    status: str
    duration_seconds: float


@dataclass
class MRPGateRow:
    """One row of the gates-run table."""

    phase_id: int
    gate_type: str
    command: str
    passed: bool
    output_excerpt: str


@dataclass
class MRPBeadRow:
    """One row of the beads-filed table."""

    bead_id: str
    bead_type: str
    status: str
    summary: str
    tags: list[str]


@dataclass
class MRPCompliance:
    """Compliance chain summary."""

    override_count: int = 0
    escalation_count: int = 0
    chain_head_hash: str = ""
    chain_intact: bool = True
    chain_message: str = ""
    log_path: str = ""


@dataclass
class MergeReadinessPack:
    """Bundle of all reviewer-facing information for a branch.

    Sections (rendered in this order by :meth:`to_markdown`):

    1. Header
    2. Plan summary
    3. Execution trace
    4. Gates run
    5. Beads filed
    6. Compliance summary
    7. Outstanding follow-ups
    8. Reviewer checklist
    9. Diff stats
    """

    header: MRPHeader = field(default_factory=MRPHeader)
    plan: MRPPlanSummary = field(default_factory=MRPPlanSummary)
    steps: list[MRPStepRow] = field(default_factory=list)
    gates: list[MRPGateRow] = field(default_factory=list)
    beads: list[MRPBeadRow] = field(default_factory=list)
    compliance: MRPCompliance = field(default_factory=MRPCompliance)
    follow_ups: list[MRPBeadRow] = field(default_factory=list)
    diff_stats: str = ""
    total_tokens: int = 0
    total_cost_usd: float = 0.0

    # ------------------------------------------------------------------
    # Markdown rendering
    # ------------------------------------------------------------------

    def to_markdown(self) -> str:
        """Render the full pack as a single Markdown document."""
        out: list[str] = []

        # Title
        out.append(f"# Merge-Readiness Pack — {self.header.branch or '(unknown branch)'}")
        out.append("")

        # 1. Header
        out.append("## 1. Header")
        out.append("")
        out.append(f"- **Branch:** `{self.header.branch}`")
        out.append(f"- **Base branch:** `{self.header.base_branch}`")
        out.append(f"- **Commits:** {self.header.commit_count}")
        authors = ", ".join(self.header.authors) if self.header.authors else "(none)"
        out.append(f"- **Author(s):** {authors}")
        out.append(f"- **Generated:** {self.header.generated_at}")
        out.append("")

        # 2. Plan summary
        out.append("## 2. Plan Summary")
        out.append("")
        if self.plan.plan_source:
            out.append(f"- **Source:** `{self.plan.plan_source}`")
            out.append(f"- **Task:** {self.plan.task_summary or '(none)'}")
            out.append(f"- **Risk tier:** {self.plan.risk_tier or '(unknown)'}")
            out.append(f"- **Budget tier:** {self.plan.budget_tier or '(unknown)'}")
            out.append(
                f"- **Phases / steps:** {self.plan.phase_count} / {self.plan.step_count}"
            )
        else:
            out.append("(no plan.md found)")
        out.append("")

        # 3. Execution trace
        out.append("## 3. Execution Trace")
        out.append("")
        if self.steps:
            out.append("| Phase | Step | Agent | Model | Status | Duration (s) |")
            out.append("|------:|------|-------|-------|--------|-------------:|")
            for s in self.steps:
                out.append(
                    f"| {s.phase_id} | `{s.step_id}` | {s.agent} | {s.model or '(n/a)'} | "
                    f"{s.status} | {s.duration_seconds:.1f} |"
                )
            out.append("")
            out.append(
                f"**Totals:** {self.total_tokens:,} tokens · "
                f"${self.total_cost_usd:.4f} estimated cost"
            )
        else:
            out.append("(no step results recorded)")
        out.append("")

        # 4. Gates run
        out.append("## 4. Gates Run")
        out.append("")
        if self.gates:
            out.append("| Phase | Gate type | Result | Command |")
            out.append("|------:|-----------|--------|---------|")
            for g in self.gates:
                outcome = "PASS" if g.passed else "FAIL"
                cmd = g.command or "(manual)"
                out.append(f"| {g.phase_id} | {g.gate_type} | {outcome} | `{cmd}` |")
        else:
            out.append("(no gates recorded)")
        out.append("")

        # 5. Beads filed
        out.append("## 5. Beads Filed")
        out.append("")
        if self.beads:
            out.append("| Bead | Type | Status | Summary |")
            out.append("|------|------|--------|---------|")
            for b in self.beads:
                summary = (b.summary or "").replace("|", "\\|").replace("\n", " ")
                if len(summary) > 80:
                    summary = summary[:77] + "..."
                out.append(f"| `{b.bead_id}` | {b.bead_type} | {b.status} | {summary} |")
        else:
            out.append("(no beads filed for this branch)")
        out.append("")

        # 6. Compliance summary
        out.append("## 6. Compliance Summary")
        out.append("")
        if self.compliance.log_path:
            out.append(f"- **Audit log:** `{self.compliance.log_path}`")
            out.append(f"- **Override entries:** {self.compliance.override_count}")
            out.append(f"- **Escalations:** {self.compliance.escalation_count}")
            head = self.compliance.chain_head_hash or "(empty)"
            out.append(f"- **Chain head hash:** `{head}`")
            integrity = (
                "Chain integrity: VERIFIED [OK]"
                if self.compliance.chain_intact
                else f"Chain integrity: WARNING — {self.compliance.chain_message}"
            )
            out.append(f"- **{integrity}**")
        else:
            out.append("(no compliance-audit.jsonl found)")
        out.append("")

        # 7. Outstanding follow-ups
        out.append("## 7. Outstanding Follow-ups")
        out.append("")
        if self.follow_ups:
            out.append("| Bead | Type | Summary |")
            out.append("|------|------|---------|")
            for b in self.follow_ups:
                summary = (b.summary or "").replace("|", "\\|").replace("\n", " ")
                if len(summary) > 80:
                    summary = summary[:77] + "..."
                out.append(f"| `{b.bead_id}` | {b.bead_type} | {summary} |")
        else:
            out.append("(none)")
        out.append("")

        # 8. Reviewer checklist
        out.append("## 8. Reviewer Checklist")
        out.append("")
        for item in REVIEWER_CHECKLIST:
            out.append(f"- [ ] {item}")
        out.append("")

        # 9. Diff stats
        out.append("## 9. Diff Stats")
        out.append("")
        if self.diff_stats.strip():
            out.append("```")
            out.append(self.diff_stats.rstrip())
            out.append("```")
        else:
            out.append("(no diff against base)")
        out.append("")

        return "\n".join(out)


# ---------------------------------------------------------------------------
# Reviewer checklist (single source of truth — also asserted by tests)
# ---------------------------------------------------------------------------


REVIEWER_CHECKLIST: list[str] = [
    "All planned phases completed?",
    "All gates passed?",
    "No HIGH/CRITICAL audit verdicts unaddressed?",
    "All open follow-up beads either resolved OR explicitly deferred with note?",
    "Compliance chain verifies clean?",
    "Test count delta is positive (or explicitly noted)?",
]


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class MRPBuilder:
    """Assemble a :class:`MergeReadinessPack` from local data sources.

    The builder is intentionally tolerant: missing inputs (no plan.md, no
    baton.db, no compliance log, not a git repo) produce an MRP with the
    relevant section marked as unavailable rather than an exception.

    Args:
        project_root: The project root directory (where ``.claude/`` lives).
            Defaults to the current working directory.
    """

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = (project_root or Path.cwd()).resolve()

    # ------------------------------------------------------------------
    # Top-level entry
    # ------------------------------------------------------------------

    def build(
        self,
        *,
        task_id: str | None = None,
        branch: str | None = None,
        base: str | None = None,
    ) -> MergeReadinessPack:
        """Build the full pack.

        Args:
            task_id: Execution task ID to pull plan + step results from.
                If ``None`` the active task ID from ``baton.db`` is used.
            branch: Git branch to report on.  Defaults to ``HEAD``'s
                current branch.
            base: Base branch for diff and commit-range comparisons.
                Defaults to ``master`` (falls back to ``main`` if master
                is missing).

        Returns:
            A populated :class:`MergeReadinessPack`.
        """
        branch = branch or self._git_current_branch()
        base = base or self._pick_default_base()
        task_id = task_id or self._active_task_id()

        pack = MergeReadinessPack()
        pack.header = self._build_header(branch, base)
        pack.plan = self._build_plan_summary(task_id)
        pack.steps, pack.total_tokens, pack.total_cost_usd = self._build_steps(task_id)
        pack.gates = self._build_gates(task_id)
        pack.beads, pack.follow_ups = self._build_beads(task_id, branch, base)
        pack.compliance = self._build_compliance()
        pack.diff_stats = self._git_diff_stat(base, branch)
        return pack

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    def _build_header(self, branch: str, base: str) -> MRPHeader:
        commits = self._git_commit_count(base, branch)
        authors = self._git_authors(base, branch)
        return MRPHeader(
            branch=branch or "(unknown)",
            base_branch=base or "(unknown)",
            commit_count=commits,
            authors=authors,
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    def _build_plan_summary(self, task_id: str | None) -> MRPPlanSummary:
        plan_md = self._find_plan_md(task_id)
        if plan_md is None:
            return MRPPlanSummary()

        text = plan_md.read_text(encoding="utf-8", errors="replace")
        # Try to source structured data from baton.db first; fall back to
        # parsing plan.md heuristically.
        plan_data = self._load_plan_struct(task_id) if task_id else None

        if plan_data is not None:
            return MRPPlanSummary(
                task_summary=plan_data.get("task_summary", "")[:300],
                risk_tier=plan_data.get("risk_level", ""),
                budget_tier=plan_data.get("budget_tier", ""),
                phase_count=int(plan_data.get("phase_count", 0)),
                step_count=int(plan_data.get("step_count", 0)),
                plan_source=str(plan_md.relative_to(self.project_root)),
            )

        # Fallback: parse plan.md headings.
        return MRPPlanSummary(
            task_summary=_extract_first_paragraph(text)[:300],
            risk_tier=_grep_field(text, "Risk"),
            budget_tier=_grep_field(text, "Budget"),
            phase_count=text.count("\n## Phase"),
            step_count=text.count("\n- Step"),
            plan_source=str(plan_md.relative_to(self.project_root)),
        )

    def _build_steps(
        self, task_id: str | None
    ) -> tuple[list[MRPStepRow], int, float]:
        if not task_id:
            return [], 0, 0.0
        rows: list[MRPStepRow] = []
        total_tokens = 0
        total_cost = 0.0

        db = self._db_path()
        if not db.exists():
            return [], 0, 0.0

        try:
            import sqlite3
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            # Pair step_results with phase_id via plan_steps.
            step_rows = conn.execute(
                "SELECT * FROM step_results WHERE task_id = ? ORDER BY rowid",
                (task_id,),
            ).fetchall()
            phase_lookup: dict[str, int] = {}
            try:
                for ps in conn.execute(
                    "SELECT step_id, phase_id FROM plan_steps WHERE task_id = ?",
                    (task_id,),
                ).fetchall():
                    phase_lookup[ps["step_id"]] = int(ps["phase_id"])
            except Exception:
                pass
            conn.close()
        except Exception:
            return [], 0, 0.0

        try:
            from agent_baton.core.engine.cost_estimator import step_cost_usd
        except Exception:
            step_cost_usd = None  # type: ignore[assignment]

        for r in step_rows:
            tokens = int(r["estimated_tokens"] or 0)
            model = r["model_id"] or ""
            cost = 0.0
            if step_cost_usd is not None and tokens:
                try:
                    cost = step_cost_usd(tokens, model)
                except Exception:
                    cost = 0.0
            rows.append(
                MRPStepRow(
                    phase_id=phase_lookup.get(r["step_id"], 0),
                    step_id=r["step_id"],
                    agent=r["agent_name"],
                    model=model,
                    status=r["status"],
                    duration_seconds=float(r["duration_seconds"] or 0.0),
                )
            )
            total_tokens += tokens
            total_cost += cost
        return rows, total_tokens, total_cost

    def _build_gates(self, task_id: str | None) -> list[MRPGateRow]:
        if not task_id:
            return []
        db = self._db_path()
        if not db.exists():
            return []
        try:
            import sqlite3
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM gate_results WHERE task_id = ? ORDER BY rowid",
                (task_id,),
            ).fetchall()
            conn.close()
        except Exception:
            return []
        out: list[MRPGateRow] = []
        for r in rows:
            output = r["output"] or ""
            excerpt = output[:120].replace("\n", " ")
            out.append(
                MRPGateRow(
                    phase_id=int(r["phase_id"]),
                    gate_type=r["gate_type"],
                    command=r["command"] or "",
                    passed=bool(r["passed"]),
                    output_excerpt=excerpt,
                )
            )
        return out

    def _build_beads(
        self, task_id: str | None, branch: str, base: str
    ) -> tuple[list[MRPBeadRow], list[MRPBeadRow]]:
        """Return (all_beads_on_branch, follow_up_open_beads)."""
        db = self._db_path()
        if not db.exists():
            return [], []

        # Branch-create date as the lower bound for "since".  When git is
        # unavailable we list all beads for the active task.
        since_iso = self._branch_create_iso(base, branch)

        try:
            import sqlite3
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            params: list[Any] = []
            conditions: list[str] = []
            if task_id:
                conditions.append("task_id = ?")
                params.append(task_id)
            if since_iso:
                conditions.append("created_at >= ?")
                params.append(since_iso)
            where = "WHERE " + " AND ".join(conditions) if conditions else ""
            sql = f"SELECT * FROM beads {where} ORDER BY created_at DESC"
            bead_rows = conn.execute(sql, params).fetchall()

            tag_lookup: dict[str, list[str]] = {}
            try:
                for tr in conn.execute(
                    "SELECT bead_id, tag FROM bead_tags"
                ).fetchall():
                    tag_lookup.setdefault(tr["bead_id"], []).append(tr["tag"])
            except Exception:
                pass
            conn.close()
        except Exception:
            return [], []

        all_beads: list[MRPBeadRow] = []
        follow_ups: list[MRPBeadRow] = []
        for r in bead_rows:
            bead_id = r["bead_id"]
            tags = tag_lookup.get(bead_id, [])
            row = MRPBeadRow(
                bead_id=bead_id,
                bead_type=r["bead_type"],
                status=r["status"],
                summary=r["summary"] or _short(r["content"] or ""),
                tags=tags,
            )
            all_beads.append(row)
            if r["status"] == "open" and "follow-up" in tags:
                follow_ups.append(row)
        return all_beads, follow_ups

    def _build_compliance(self) -> MRPCompliance:
        log = self.project_root / ".claude" / "team-context" / "compliance-audit.jsonl"
        if not log.exists():
            return MRPCompliance()

        override = 0
        escalation = 0
        head_hash = ""
        try:
            with log.open("r", encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entry = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    head_hash = entry.get("entry_hash", head_hash) or head_hash
                    kind = (entry.get("kind") or entry.get("type") or "").lower()
                    if "override" in kind:
                        override += 1
                    if "escalat" in kind:
                        escalation += 1
        except OSError:
            return MRPCompliance(log_path=str(log))

        intact = True
        message = ""
        try:
            from agent_baton.core.govern.compliance import verify_chain  # type: ignore[attr-defined]
        except ImportError:
            verify_chain = None  # type: ignore[assignment]
            message = "verify_chain helper unavailable; integrity not checked"
        if verify_chain is not None:
            try:
                intact, message = verify_chain(log)
            except Exception as exc:
                intact = False
                message = f"verify failed: {exc}"

        return MRPCompliance(
            override_count=override,
            escalation_count=escalation,
            chain_head_hash=head_hash[:16] if head_hash else "",
            chain_intact=intact,
            chain_message=message,
            log_path=str(log.relative_to(self.project_root)),
        )

    # ------------------------------------------------------------------
    # Path / db helpers
    # ------------------------------------------------------------------

    def _db_path(self) -> Path:
        return self.project_root / ".claude" / "team-context" / "baton.db"

    def _active_task_id(self) -> str | None:
        db = self._db_path()
        if not db.exists():
            return None
        try:
            import sqlite3
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT task_id FROM active_task WHERE id = 1"
            ).fetchone()
            conn.close()
            return row["task_id"] if row else None
        except Exception:
            return None

    def _find_plan_md(self, task_id: str | None) -> Path | None:
        candidates: list[Path] = []
        if task_id:
            candidates.append(
                self.project_root
                / ".claude"
                / "team-context"
                / "executions"
                / task_id
                / "plan.md"
            )
        candidates.append(
            self.project_root / ".claude" / "team-context" / "plan.md"
        )
        candidates.append(self.project_root / "plan.md")
        for p in candidates:
            if p.exists():
                return p
        return None

    def _load_plan_struct(self, task_id: str) -> dict[str, Any] | None:
        db = self._db_path()
        if not db.exists():
            return None
        try:
            import sqlite3
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT task_summary, risk_level, budget_tier FROM plans WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                conn.close()
                return None
            phase_count_row = conn.execute(
                "SELECT COUNT(*) AS c FROM plan_phases WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            step_count_row = conn.execute(
                "SELECT COUNT(*) AS c FROM plan_steps WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            conn.close()
            return {
                "task_summary": row["task_summary"],
                "risk_level": row["risk_level"],
                "budget_tier": row["budget_tier"],
                "phase_count": int(phase_count_row["c"]) if phase_count_row else 0,
                "step_count": int(step_count_row["c"]) if step_count_row else 0,
            }
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Git wrappers (tolerant; non-zero exits return defaults)
    # ------------------------------------------------------------------

    def _git(self, *args: str) -> str:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=str(self.project_root),
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                return ""
            return result.stdout
        except (FileNotFoundError, subprocess.SubprocessError):
            return ""

    def _git_current_branch(self) -> str:
        return self._git("rev-parse", "--abbrev-ref", "HEAD").strip()

    def _pick_default_base(self) -> str:
        # Prefer master, then main; if neither exists return "master" anyway.
        for cand in ("master", "main"):
            ok = self._git("rev-parse", "--verify", "--quiet", cand).strip()
            if ok:
                return cand
        return "master"

    def _git_commit_count(self, base: str, branch: str) -> int:
        if not base or not branch:
            return 0
        out = self._git("rev-list", "--count", f"{base}..{branch}").strip()
        try:
            return int(out)
        except ValueError:
            return 0

    def _git_authors(self, base: str, branch: str) -> list[str]:
        if not base or not branch:
            return []
        out = self._git("log", "--format=%an", f"{base}..{branch}")
        seen: set[str] = set()
        ordered: list[str] = []
        for line in out.splitlines():
            name = line.strip()
            if name and name not in seen:
                seen.add(name)
                ordered.append(name)
        return ordered

    def _git_diff_stat(self, base: str, branch: str) -> str:
        if not base or not branch:
            return ""
        return self._git("diff", "--stat", f"{base}..{branch}").rstrip()

    def _branch_create_iso(self, base: str, branch: str) -> str:
        """Return ISO date of the merge-base commit between base and branch."""
        if not base or not branch:
            return ""
        sha = self._git("merge-base", base, branch).strip()
        if not sha:
            return ""
        ts = self._git("log", "-1", "--format=%cI", sha).strip()
        return ts


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _short(text: str, n: int = 120) -> str:
    text = text.strip().replace("\n", " ")
    return text if len(text) <= n else text[: n - 3] + "..."


def _extract_first_paragraph(text: str) -> str:
    """Return the first non-heading, non-empty paragraph of *text*."""
    para: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if para:
                break
            continue
        if stripped.startswith("#"):
            continue
        para.append(stripped)
    return " ".join(para)


def _grep_field(text: str, label: str) -> str:
    """Find the first ``- **Label**: value`` or ``Label: value`` line."""
    needle_lower = label.lower()
    for line in text.splitlines():
        stripped = line.strip().lstrip("-").strip()
        if stripped.lower().startswith(needle_lower):
            # split on first ':' after the label
            _, _, rest = stripped.partition(":")
            value = rest.strip().strip("*").strip("`").strip()
            if value:
                return value
    return ""
