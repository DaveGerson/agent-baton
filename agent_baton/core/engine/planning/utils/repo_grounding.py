"""Repository-grounded decomposition for heavy-complexity tasks.

For LIGHT/MEDIUM-complexity work the generic phase/step templates in
``phase_builder.py`` are adequate — the task is small enough that a
one-line description plus a role-appropriate default deliverable list
gives an agent enough to act on. HEAVY tasks are different: the plan
already spans multiple phases and steps, and a template-only description
("Implement: &lt;task summary&gt; (as backend-engineer)") repeated across N
steps gives every implementer the *same* underspecified brief — context
rot baked in at plan time, before a single agent has even started.

This module grounds heavy-task steps in the actual repository: it scans
for files/tests/symbols relevant to each step's concern and only then
sets concrete ``context_files`` / ``allowed_paths`` / ``deliverables`` /
``expected_outcome`` and augments ``task_description`` with what it
found, plus wires cross-phase ``depends_on`` edges between steps that
touch the same grounded file. Everything here is deterministic (no LLM,
no network, stdlib only) and strictly additive over what
``phase_builder`` already assigned:

* a field is only ever set/appended when it was empty or the file wasn't
  already present — an explicit or template value set upstream always
  wins;
* when the repository yields no relevant evidence (unknown
  ``project_root``, empty repo, no keyword overlap), every function here
  is a no-op — ``phase_builder.enrich_phases``'s existing generic-
  template fallback is what actually runs, so behavior for a repo-less
  or hermetic plan is unchanged from before this module existed. That is
  the "keep deterministic fallback behavior" contract: there is no LLM
  in this module to be unavailable, and no filesystem to scan is treated
  identically to no matches found.
"""
from __future__ import annotations

import ast
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from agent_baton.core.engine.planning.rules.concerns import CROSS_CONCERN_SIGNALS
from agent_baton.core.engine.planning.scope_contract import derive_allowed_paths
from agent_baton.core.engine.planning.utils.text_parsers import extract_file_paths

if TYPE_CHECKING:
    from agent_baton.models.execution import PlanPhase, PlanStep

logger = logging.getLogger(__name__)

__all__ = [
    "RepoFindings",
    "gather_repo_findings",
    "ground_phases_in_repository",
]

# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

_IGNORED_DIR_NAMES = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv", "dist",
    "build", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    "site-packages", ".next", "target", "coverage", "htmlcov", ".egg-info",
})

_CODE_EXTENSIONS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".rb",
})

_TEST_PATH_HINTS = ("test_", "_test.", ".test.", ".spec.", "/tests/", "/test/", "/__tests__/")

_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into", "add",
    "implement", "fix", "update", "ensure", "support", "when", "should",
    "must", "task", "feature", "also", "will", "make", "sure", "have",
    "each", "all", "any", "not", "can", "use", "used", "new", "then",
    "step", "steps", "phase", "phases", "plan", "please", "need", "needs",
    "including", "across", "comprehensive", "entire",
})

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")
_CAMEL_RE = re.compile(r"[A-Za-z][a-z0-9]*|[A-Z]+(?![a-z])")

# Cap on files scanned per gather_repo_findings call — bounds worst-case
# walk time on very large repositories without needing external indexing.
_DEFAULT_MAX_SCANNED = 4000
_DEFAULT_MAX_MATCHED_FILES = 25
_MAX_SYMBOL_FILES = 15
_MAX_SYMBOLS = 20


def _tokenize(text: str) -> set[str]:
    """Lowercase word + sub-word (snake/camel split) token set, stopword-filtered."""
    tokens: set[str] = set()
    for match in _TOKEN_RE.finditer(text or ""):
        word = match.group(0).lower()
        if word not in _STOPWORDS:
            tokens.add(word)
        for part in re.split(r"[_-]", word):
            if len(part) >= 3 and part not in _STOPWORDS:
                tokens.add(part)
    return tokens


def _basename_tokens(path: str) -> set[str]:
    """Token set derived from a path's filename (snake_case/camelCase split)."""
    stem = Path(path).stem
    tokens: set[str] = set()
    for chunk in re.split(r"[_\-.]", stem):
        for sub in _CAMEL_RE.findall(chunk):
            if len(sub) >= 3:
                tokens.add(sub.lower())
    return tokens


@dataclass
class RepoFindings:
    """Result of a single :func:`gather_repo_findings` scan."""

    available: bool = False
    root: "Path | None" = None
    matched_files: list[str] = field(default_factory=list)
    matched_tests: list[str] = field(default_factory=list)
    matched_symbols: list[tuple[str, str]] = field(default_factory=list)  # (path, symbol)
    existing_dirs: "frozenset[str]" = frozenset()
    topology_areas: list[str] = field(default_factory=list)


def gather_repo_findings(
    project_root: "Path | None",
    task_summary: str,
    *,
    max_scanned: int = _DEFAULT_MAX_SCANNED,
    max_matched_files: int = _DEFAULT_MAX_MATCHED_FILES,
) -> RepoFindings:
    """Deterministic, hermetic repository scan — no LLM, no network.

    Returns an "unavailable" (all-empty) :class:`RepoFindings` when
    *project_root* is falsy or not a real directory, so callers on a
    dry-run/hermetic plan (no filesystem to scan) get a clean no-op
    rather than an exception.
    """
    if not project_root:
        return RepoFindings(available=False)
    root = Path(project_root)
    if not root.is_dir():
        return RepoFindings(available=False)

    keywords = _tokenize(task_summary)
    extracted = extract_file_paths(task_summary)

    matched_files: list[str] = []
    seen: set[str] = set()

    # Tier 1: extracted path-like tokens from the task summary, confirmed
    # to exist on disk — the strongest possible evidence (the director
    # named the file).
    for raw in extracted:
        candidate = root / raw
        if candidate.exists():
            rel = raw.replace("\\", "/")
            if rel not in seen:
                seen.add(rel)
                matched_files.append(rel)

    # Tier 2: keyword-token overlap against basenames, bounded walk.
    existing_dirs: set[str] = set()
    scanned = 0
    if keywords:
        stop_walk = False
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                d for d in dirnames
                if d not in _IGNORED_DIR_NAMES and not d.startswith(".")
            )
            rel_dir = os.path.relpath(dirpath, root)
            if rel_dir == ".":
                existing_dirs.update(dirnames)
            for fname in sorted(filenames):
                scanned += 1
                if scanned > max_scanned:
                    stop_walk = True
                    break
                if Path(fname).suffix not in _CODE_EXTENSIONS:
                    continue
                rel_path = os.path.normpath(os.path.join(rel_dir, fname)).replace("\\", "/")
                if rel_path in seen:
                    continue
                if _basename_tokens(rel_path) & keywords:
                    seen.add(rel_path)
                    matched_files.append(rel_path)
                    if len(matched_files) >= max_matched_files:
                        stop_walk = True
                        break
            if stop_walk:
                break
    else:
        try:
            for entry in root.iterdir():
                if (
                    entry.is_dir()
                    and entry.name not in _IGNORED_DIR_NAMES
                    and not entry.name.startswith(".")
                ):
                    existing_dirs.add(entry.name)
        except OSError:
            pass

    matched_tests = [
        p for p in matched_files
        if any(hint in f"/{p.lower()}" for hint in _TEST_PATH_HINTS)
    ]

    matched_symbols: list[tuple[str, str]] = []
    for rel_path in matched_files[:_MAX_SYMBOL_FILES]:
        if not rel_path.endswith(".py"):
            continue
        full = root / rel_path
        try:
            source = full.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=rel_path)
        except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
            continue
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if _basename_tokens(node.name) & keywords:
                    matched_symbols.append((rel_path, node.name))
        if len(matched_symbols) >= _MAX_SYMBOLS:
            break

    topology_areas = sorted(d for d in existing_dirs if d.lower() in keywords)

    return RepoFindings(
        available=True,
        root=root,
        matched_files=matched_files,
        matched_tests=matched_tests,
        matched_symbols=matched_symbols[:_MAX_SYMBOLS],
        existing_dirs=frozenset(existing_dirs),
        topology_areas=topology_areas,
    )


# ---------------------------------------------------------------------------
# Grounding
# ---------------------------------------------------------------------------


def _score_file_for_step(path: str, step_tokens: set[str], agent_keywords: list[str]) -> int:
    score = len(_basename_tokens(path) & step_tokens)
    lower = path.lower()
    for kw in agent_keywords:
        if kw in lower:
            score += 1
    return score


def ground_phases_in_repository(
    plan_phases: "list[PlanPhase]",
    task_summary: str,
    findings: RepoFindings,
) -> None:
    """Populate concrete grounding on every step of *plan_phases*.

    Mutates steps in place. Only ever fills fields that are currently
    empty and only ever appends files not already present — never
    overrides an explicit or template-derived value set earlier in the
    pipeline. No-op entirely when *findings* has no evidence
    (``findings.available`` False, or no matched files/topology at all)
    — callers rely on ``phase_builder.enrich_phases``'s existing
    generic-template fallback in that case.
    """
    if not findings.available or not (findings.matched_files or findings.topology_areas):
        return

    summary_tokens = _tokenize(task_summary)

    for phase in plan_phases:
        for step in phase.steps:
            _ground_step(step, summary_tokens, findings)

    _wire_cross_phase_dependencies(plan_phases)


def _ground_step(
    step: "PlanStep",
    summary_tokens: set[str],
    findings: RepoFindings,
) -> None:
    base_agent = (step.agent_name or "").split("--")[0]
    agent_keywords = CROSS_CONCERN_SIGNALS.get(base_agent, [])
    step_tokens = summary_tokens | _tokenize(step.task_description)

    scored = sorted(
        findings.matched_files,
        key=lambda p: _score_file_for_step(p, step_tokens, agent_keywords),
        reverse=True,
    )
    relevant = [
        p for p in scored
        if _score_file_for_step(p, step_tokens, agent_keywords) > 0
    ]
    chosen = (relevant or findings.matched_files)[:3]
    if not chosen:
        return

    chosen_set = set(chosen)
    chosen_tests = [t for t in findings.matched_tests if t in chosen_set] or findings.matched_tests[:1]
    chosen_symbols = [(p, s) for (p, s) in findings.matched_symbols if p in chosen_set][:5]

    # context_files — append, never replace.
    for f in chosen:
        if f not in step.context_files:
            step.context_files.append(f)

    # allowed_paths — deterministic evidence pipeline; never invents a
    # path, and never overrides an already-populated (explicit) value.
    if not step.allowed_paths:
        derived, _source = derive_allowed_paths(
            explicit_paths=None,
            deliverables=step.deliverables,
            context_files=step.context_files,
            likely_repo_areas=findings.topology_areas,
            agent_base=step.agent_name,
            existing_dirs=findings.existing_dirs,
        )
        if derived:
            step.allowed_paths = derived

    # deliverables — concrete, file-anchored.
    if not step.deliverables:
        deliverables = [f"Concrete change in {f}" for f in chosen[:2]]
        if chosen_tests and step.step_type in ("developing", "testing"):
            deliverables.append(f"Passing coverage in {chosen_tests[0]}")
        step.deliverables = deliverables

    # task_description — append a grounding sentence naming the concrete
    # evidence, once (idempotent against re-grounding the same step).
    grounding_bits: list[str] = [f"files: {', '.join(chosen)}"]
    if chosen_symbols:
        grounding_bits.append(
            "relevant symbols: "
            + ", ".join(f"{sym}() in {p}" for p, sym in chosen_symbols)
        )
    if chosen_tests:
        grounding_bits.append(f"existing tests: {', '.join(chosen_tests[:2])}")
    suffix = " Repository scope — " + "; ".join(grounding_bits) + "."
    if suffix.strip() not in step.task_description:
        step.task_description = step.task_description.rstrip() + suffix

    # expected_outcome — concrete behavioral statement.
    if not step.expected_outcome:
        target = chosen[0]
        if chosen_tests:
            step.expected_outcome = (
                f"After this step, the change in `{target}` is observable in "
                f"the running system and `{chosen_tests[0]}` exercises it and "
                f"passes."
            )
        elif chosen_symbols:
            sym_path, sym_name = chosen_symbols[0]
            step.expected_outcome = (
                f"After this step, `{sym_name}` in `{sym_path}` behaves as "
                f"described and is observably working."
            )
        else:
            step.expected_outcome = (
                f"After this step, `{target}` reflects the described change "
                f"and is observably working in the running system."
            )


def _wire_cross_phase_dependencies(plan_phases: "list[PlanPhase]") -> None:
    """Link a later-phase step to the earliest earlier-phase step that
    claimed the same grounded file, when it doesn't already declare a
    dependency on it.

    Keeps the plan's explicit dependency graph in sync with the concrete
    file overlap grounding just established, instead of relying solely on
    implicit phase-sequential ordering. Only ever *adds* an edge to an
    earlier phase's step — never removes or overrides an existing
    ``depends_on`` entry, and never creates a same-phase or backward edge.
    """
    file_owner: dict[str, tuple[int, str]] = {}
    for phase in plan_phases:
        for step in phase.steps:
            for f in step.context_files:
                if f == "CLAUDE.md":
                    continue
                if f not in file_owner:
                    file_owner[f] = (phase.phase_id, step.step_id)

    for phase in plan_phases:
        for step in phase.steps:
            for f in step.context_files:
                if f == "CLAUDE.md":
                    continue
                owner_phase_id, owner_step_id = file_owner.get(f, (None, None))
                if owner_step_id is None or owner_step_id == step.step_id:
                    continue
                if owner_phase_id is None or owner_phase_id >= phase.phase_id:
                    continue
                if owner_step_id not in step.depends_on:
                    step.depends_on.append(owner_step_id)
