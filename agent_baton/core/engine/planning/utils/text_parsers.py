"""Pure text-parsing helpers — no service dependencies.

Extracted from ``_legacy_planner.IntelligentPlanner`` so that any
pipeline stage (or the runtime replanner) can import these directly.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from agent_baton.core.engine.classifier import _score_task_type
from agent_baton.core.engine.planning.rules.concerns import (
    CONCERN_CONSTRAINT_KEYWORDS,
    CONCERN_MARKER,
    MIN_CONCERNS_FOR_SPLIT,
    SUBTASK_SPLIT,
)

if TYPE_CHECKING:
    from agent_baton.core.routing.agent_registry import AgentRegistry

# Task-type inference keywords.  Each entry is ``(type_name, keywords)``;
# ``_score_task_type`` picks the best match.
_TASK_TYPE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("new-feature", ["add", "build", "create", "implement", "feature", "develop",
                      "introduce", "wire", "integrate", "extend"]),
    ("bug-fix", ["fix", "bug", "broken", "error", "crash", "traceback", "exception",
                  "patch", "regression", "fails", "failing"]),
    ("migration", ["migrate", "migration", "upgrade", "move"]),
    ("refactor", ["refactor", "clean up", "reorganize", "restructure", "rename",
                   "cleanup", "simplify", "decouple", "extract"]),
    ("data-analysis", ["analyze", "analyse", "analytics", "report",
                        "query", "insight", "metric", "kpi", "data exploration",
                        "audit", "assessment", "scorecard", "evaluate"]),
    ("test", ["test suite", "tests for", "testing", "test coverage", "e2e test",
              "unit test", "integration test", "playwright", "pytest"]),
    ("documentation", ["document", "documentation", "readme", "adr", "spec",
                        "wiki", "summarize", "write docs", "review", "explore",
                        "architecture", "overview"]),
]

_AGENT_ALIASES: dict[str, str] = {
    "viz": "visualization-expert",
    "viz expert": "visualization-expert",
    "visualization": "visualization-expert",
    "sme": "subject-matter-expert",
    "subject matter expert": "subject-matter-expert",
    "backend": "backend-engineer",
    "frontend": "frontend-engineer",
    "devops": "devops-engineer",
    "security": "security-reviewer",
    "reviewer": "code-reviewer",
    "tester": "test-engineer",
    "data analyst": "data-analyst",
    "data engineer": "data-engineer",
    "data scientist": "data-scientist",
}

_CODE_EXTENSIONS = frozenset({
    ".py", ".ts", ".md", ".json", ".yaml", ".yml", ".toml",
    ".cfg", ".txt", ".html", ".css", ".js", ".jsx", ".tsx",
    ".rs", ".go", ".java", ".rb", ".sh", ".sql", ".ini",
    ".lock", ".env", ".conf",
})


# ---------------------------------------------------------------------------
# Public API — all functions are stateless
# ---------------------------------------------------------------------------

def generate_task_id(summary: str) -> str:
    """Create a collision-free task ID.

    Format: ``YYYY-MM-DD-<slug>-<8-char-uuid>``
    """
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = re.sub(r"[^a-z0-9]+", "-", summary.lower()).strip("-")
    slug = slug[:50]
    slug = slug.rstrip("-")
    uid = uuid.uuid4().hex[:8]
    base = f"{date_str}-{slug}" if slug else date_str
    return f"{base}-{uid}"


def infer_task_type(summary: str) -> str:
    """Infer task type from summary keywords.

    Returns one of: 'new-feature', 'bug-fix', 'refactor', 'data-analysis',
    'documentation', 'migration', 'test'.  Falls back to 'new-feature'.
    """
    return _score_task_type(summary, _TASK_TYPE_KEYWORDS)


def parse_subtasks(summary: str) -> list[tuple[int, str]]:
    """Parse numbered sub-tasks from a compound task description.

    Returns a list of ``(index, text)`` pairs.  Empty if < 2 sub-tasks.
    """
    parts = SUBTASK_SPLIT.split(summary)
    subtasks: list[tuple[int, str]] = []
    i = 1
    while i + 2 < len(parts):
        index = int(parts[i] or parts[i + 1])
        text = parts[i + 2].strip()
        if text:
            subtasks.append((index, text))
        i += 3
    return subtasks if len(subtasks) >= 2 else []


def parse_concerns(summary: str) -> list[tuple[str, str]]:
    """Parse distinct concerns from a multi-concern task description.

    Returns ``(marker, text)`` pairs.  Empty when fewer than
    ``MIN_CONCERNS_FOR_SPLIT`` concerns are detected.
    """
    lower = summary.lower()
    bound = len(summary)
    for kw in CONCERN_CONSTRAINT_KEYWORDS:
        idx = lower.find(kw)
        if idx != -1 and idx < bound:
            bound = idx
    bounded_summary = summary[:bound]

    matches = list(CONCERN_MARKER.finditer(bounded_summary))
    if len(matches) < MIN_CONCERNS_FOR_SPLIT:
        return []

    concerns: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        marker = m.group(1).strip("().")
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(bounded_summary)
        text = bounded_summary[start:end].strip().rstrip(";,")
        if text:
            concerns.append((marker, text))

    return concerns if len(concerns) >= MIN_CONCERNS_FOR_SPLIT else []


def extract_file_paths(text: str) -> list[str]:
    """Extract file path candidates from task summary text."""
    pattern = r'(?:^|[\s(])([a-zA-Z0-9_./-]+(?:\.[a-zA-Z0-9]+|/))'
    candidates = re.findall(pattern, text)
    seen: set[str] = set()
    result: list[str] = []
    for c in candidates:
        if c.endswith("/"):
            continue
        if c.startswith((".", "/", "-")):
            continue
        last_part = c.split("/")[-1]
        if "." not in last_part:
            continue
        ext = f".{last_part.rsplit('.', 1)[-1].lower()}"
        if ext not in _CODE_EXTENSIONS:
            continue
        if c in seen:
            continue
        seen.add(c)
        result.append(c)
    return result


def parse_structured_description(
    summary: str,
    registry: "AgentRegistry | None" = None,
) -> tuple[list[dict] | None, list[str] | None]:
    """Detect and extract structured phase/agent information from a task summary.

    Returns ``(phases_dicts, agent_hints)`` or ``(None, None)``.
    """
    try:
        known_agents: set[str] = set(registry.names) if registry else set()
    except Exception:
        known_agents = set()

    def _detect_agents_in_text(text: str) -> list[str]:
        lower = text.lower()
        found: list[str] = []
        seen: set[str] = set()
        for name in sorted(known_agents, key=len, reverse=True):
            if name in lower and name not in seen:
                found.append(name)
                seen.add(name)
        for alias, canonical in sorted(
            _AGENT_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True
        ):
            if alias in lower and canonical not in seen:
                found.append(canonical)
                seen.add(canonical)
        return found

    # Pattern 1: "Phase N: ..." or "Step N: ..."
    labelled_pattern = re.compile(
        r"(?:phase|step)\s*\d+\s*:",
        re.IGNORECASE,
    )
    labelled_matches = list(labelled_pattern.finditer(summary))
    if len(labelled_matches) >= 2:
        segments: list[str] = []
        for idx, m in enumerate(labelled_matches):
            start = m.start()
            end = labelled_matches[idx + 1].start() if idx + 1 < len(labelled_matches) else len(summary)
            segments.append(summary[start:end].strip())

        phases_dicts: list[dict] = []
        all_agents: list[str] = []
        seen_agents: set[str] = set()
        for i, seg in enumerate(segments, start=1):
            agents_in_seg = _detect_agents_in_text(seg)
            phase_name = f"Phase {i}"
            phases_dicts.append({"name": phase_name, "agents": agents_in_seg})
            for a in agents_in_seg:
                if a not in seen_agents:
                    all_agents.append(a)
                    seen_agents.add(a)
        if phases_dicts:
            return phases_dicts, all_agents or None

    # Pattern 2: numbered list "1. ... 2. ..."
    numbered_pattern = re.compile(r"(?:^|\s)(\d+)\.\s+(.+?)(?=\s+\d+\.|$)", re.DOTALL)
    numbered_matches = numbered_pattern.findall(summary)
    if len(numbered_matches) >= 2:
        phases_dicts = []
        all_agents = []
        seen_agents = set()
        for num, text in numbered_matches:
            agents_in_seg = _detect_agents_in_text(text)
            phases_dicts.append({"name": f"Phase {num}", "agents": agents_in_seg})
            for a in agents_in_seg:
                if a not in seen_agents:
                    all_agents.append(a)
                    seen_agents.add(a)
        if phases_dicts:
            return phases_dicts, all_agents or None

    # Pattern 3: semicolon- or newline-separated clauses with agent hints
    delimiter_pattern = re.compile(r"[;\n]+")
    clauses = [c.strip() for c in delimiter_pattern.split(summary) if c.strip()]
    if len(clauses) >= 2:
        clause_agents: list[list[str]] = [_detect_agents_in_text(c) for c in clauses]
        clauses_with_agents = sum(1 for ca in clause_agents if ca)
        if clauses_with_agents >= 2:
            phases_dicts = []
            all_agents = []
            seen_agents = set()
            for i, (clause, agents_in_clause) in enumerate(
                zip(clauses, clause_agents), start=1
            ):
                phases_dicts.append({"name": f"Phase {i}", "agents": agents_in_clause})
                for a in agents_in_clause:
                    if a not in seen_agents:
                        all_agents.append(a)
                        seen_agents.add(a)
            if phases_dicts:
                return phases_dicts, all_agents or None

    return None, None
