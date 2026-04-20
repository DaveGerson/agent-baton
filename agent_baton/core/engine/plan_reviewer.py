"""PlanReviewer — post-generation plan quality review.

Analyzes a completed MachinePlan and recommends structural improvements:
step splitting for overly broad single-agent steps, missing dependency
edges, scope imbalance warnings, and same-agent team recommendations
for coupled concerns.

Two review strategies:
1. **Haiku review** — cheap LLM call (~2000 tokens) that analyzes the
   plan structure and returns JSON recommendations.  Used for medium+
   complexity plans when the Anthropic SDK and API key are available.
2. **Heuristic review** — deterministic fallback using file-path clustering
   and task-description analysis.  Always available, catches the most
   common case (single-step work phases spanning 4+ files across 3+
   directories).

The reviewer thinks holistically about each step's scope and chooses
the right coordination strategy:
- **Parallel independent steps** — when concerns are truly independent
  (different files, different directories, no shared state).
- **Same-agent team** — when concerns are coupled (shared imports,
  one layer depends on another, changes need coordinated integration).
  Team members work in parallel with scoped concerns but share
  synthesis and lateral context.

Wired into ``IntelligentPlanner.create_plan()`` at step 12c.5, after
team consolidation but before bead hints.
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field

from agent_baton.models.execution import (
    MachinePlan, PlanPhase, PlanStep, SynthesisSpec, TeamMember,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Phases where step-splitting makes sense (code-producing work phases)
_SPLITTABLE_PHASES = {"implement", "fix", "draft", "migrate", "refactor"}

# Minimum file count and directory spread to trigger heuristic splitting
_MIN_FILES_FOR_SPLIT = 4
_MIN_DIRS_FOR_SPLIT = 3

# Max output tokens for the Haiku review call
_REVIEW_MAX_TOKENS = 512

# Haiku model and timeout (mirrors classifier.py)
_REVIEW_MODEL = "claude-haiku-4-5-20251001"
_REVIEW_TIMEOUT = 8.0  # slightly longer than classifier — plan summaries are bigger

# Common code file extensions for path extraction
_CODE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".json", ".yaml", ".yml",
    ".toml", ".cfg", ".html", ".css", ".sql", ".sh", ".go", ".rs", ".java",
}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class SplitRecommendation:
    """Recommendation to split a step into concern-scoped parallel steps."""
    phase_id: int
    step_id: str
    groups: list[StepGroup]
    reason: str = ""


@dataclass
class StepGroup:
    """A cluster of files/concerns that should become one step."""
    label: str
    files: list[str]
    description_hint: str = ""


@dataclass
class DependencyRecommendation:
    """Recommendation to add a depends_on edge between steps."""
    step_id: str
    depends_on: str
    reason: str = ""


@dataclass
class PlanReviewResult:
    """Result of a plan review pass."""
    splits_applied: int = 0
    teams_created: int = 0
    dependencies_added: int = 0
    warnings: list[str] = field(default_factory=list)
    source: str = "none"  # "haiku", "heuristic", "none"


# ---------------------------------------------------------------------------
# Haiku review prompt
# ---------------------------------------------------------------------------

_REVIEW_PROMPT_TEMPLATE = """\
You are a plan quality reviewer for a software orchestration engine.

Think holistically about each step's scope: is it appropriately sized \
for a single agent dispatch? If a step is too broad, decide the right \
coordination strategy based on how the concerns relate to each other.

Plan summary:
- Task: "{task_summary}"
- Complexity: {complexity}
- Phases: {phase_summary}

Phase details:
{phase_details}

File paths mentioned in task: {file_paths}

Return JSON only (no markdown, no commentary):
{{
  "splits": [
    {{
      "phase_id": <int>,
      "step_id": "<str>",
      "reason": "<why this step is too broad>",
      "coordination": "<parallel or team>",
      "groups": [
        {{
          "label": "<concern name, e.g. 'engine routing'>",
          "files": ["<file1.py>", "<file2.py>"],
          "description_hint": "<what this sub-step should do>",
          "depends_on_groups": ["<label of group this depends on>"]
        }}
      ]
    }}
  ],
  "dependencies": [
    {{
      "step_id": "<step that should depend on another>",
      "depends_on": "<step it depends on>",
      "reason": "<why>"
    }}
  ],
  "warnings": ["<any other plan quality concern>"]
}}

Rules:
- Only recommend splitting steps in work phases (Implement, Fix, Draft, \
Migrate, Refactor).
- Split when a single step touches 4+ files across 3+ different \
directories/concerns.
- Do NOT split steps that are already scoped to one concern.
- Do NOT split steps in Design, Test, or Review phases.
- For existing team steps (steps with team members), do NOT split.

Choosing coordination strategy (the "coordination" field):
- "parallel": Use when concern groups are INDEPENDENT — different files, \
different directories, no shared state, each group can be implemented \
without knowing what the others did. Example: adding a CLI flag \
(cli/) and updating docs (docs/) are independent.
- "team": Use when concern groups are COUPLED — one group's changes \
affect another, they share imports or data models, or integration \
between layers matters. Example: changing an engine API (core/engine/) \
and updating the CLI that calls it (cli/commands/) are coupled — the \
CLI author needs to know the new API signature. Teams of the same \
agent type are valid and preferred when the work needs coordination \
across layers (e.g. 3 backend engineers at engine, runtime, and CLI \
layers communicating via team synthesis).

- For "team" coordination, use depends_on_groups to express ordering \
between groups (e.g. engine group before CLI group).
- Keep groups to 2-5 per split.
- If the plan looks fine, return empty arrays.
- For light complexity plans, return empty arrays (nothing to split)."""


# ---------------------------------------------------------------------------
# Core reviewer
# ---------------------------------------------------------------------------

class PlanReviewer:
    """Review and improve plan structure after generation.

    Usage::

        reviewer = PlanReviewer()
        result = reviewer.review(plan, task_summary, file_paths)
        # plan.phases are mutated in place with splits/dependencies applied
    """

    def review(
        self,
        plan: MachinePlan,
        task_summary: str,
        file_paths: list[str] | None = None,
        complexity: str = "medium",
    ) -> PlanReviewResult:
        """Review plan and apply improvements.

        For light complexity, skips review entirely. For medium+, attempts
        Haiku review with heuristic fallback.

        Args:
            plan: The MachinePlan to review (mutated in place).
            task_summary: Original task description.
            file_paths: File paths extracted from the task summary.
            complexity: Complexity tier from classification.

        Returns:
            PlanReviewResult describing what was changed.
        """
        if complexity == "light":
            logger.debug("Skipping plan review for light complexity plan")
            return PlanReviewResult(source="skipped-light")

        file_paths = file_paths or []

        # Try Haiku review first, fall back to heuristic
        result = self._try_haiku_review(plan, task_summary, file_paths, complexity)
        if result is not None:
            return result

        return self._heuristic_review(plan, task_summary, file_paths)

    # ------------------------------------------------------------------
    # Haiku review path
    # ------------------------------------------------------------------

    def _try_haiku_review(
        self,
        plan: MachinePlan,
        task_summary: str,
        file_paths: list[str],
        complexity: str,
    ) -> PlanReviewResult | None:
        """Attempt Haiku-powered plan review. Returns None if unavailable."""
        from agent_baton.core.engine.classifier import _haiku_available

        available, reason = _haiku_available()
        if not available:
            logger.debug("Haiku unavailable for plan review: %s", reason)
            return None

        try:
            prompt = self._build_review_prompt(plan, task_summary, file_paths, complexity)
            raw = self._call_haiku(prompt)
            recommendations = self._parse_review_response(raw)
            result = self._apply_recommendations(plan, recommendations)
            result.source = "haiku"
            return result
        except Exception as exc:
            logger.warning(
                "Haiku plan review failed — falling back to heuristic. Reason: %s",
                exc,
            )
            return None

    @staticmethod
    def _call_haiku(prompt: str) -> str:
        """Call Haiku for plan review. Separated for easy test mocking."""
        import anthropic  # type: ignore[import-untyped]

        client = anthropic.Anthropic()
        response = client.messages.create(
            model=_REVIEW_MODEL,
            max_tokens=_REVIEW_MAX_TOKENS,
            timeout=_REVIEW_TIMEOUT,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    def _build_review_prompt(
        self,
        plan: MachinePlan,
        task_summary: str,
        file_paths: list[str],
        complexity: str,
    ) -> str:
        """Build a compact plan summary for Haiku review."""
        phase_summary = ", ".join(
            f"{p.name} ({len(p.steps)} step{'s' if len(p.steps) != 1 else ''})"
            for p in plan.phases
        )

        detail_lines: list[str] = []
        for phase in plan.phases:
            detail_lines.append(f"Phase {phase.phase_id}: {phase.name}")
            for step in phase.steps:
                team_note = f" [TEAM: {len(step.team)} members]" if step.team else ""
                files_note = ""
                if step.context_files:
                    files_note = f" files=[{', '.join(step.context_files[:10])}]"
                detail_lines.append(
                    f"  Step {step.step_id}: {step.agent_name}{team_note}"
                    f" — {step.task_description[:200]}{files_note}"
                )
        phase_details = "\n".join(detail_lines)

        safe_summary = task_summary.replace("{", "{{").replace("}", "}}")
        safe_details = phase_details.replace("{", "{{").replace("}", "}}")
        file_list = ", ".join(file_paths[:20]) if file_paths else "none extracted"
        safe_files = file_list.replace("{", "{{").replace("}", "}}")

        return _REVIEW_PROMPT_TEMPLATE.format(
            task_summary=safe_summary,
            complexity=complexity,
            phase_summary=phase_summary,
            phase_details=safe_details,
            file_paths=safe_files,
        )

    @staticmethod
    def _parse_review_response(raw: str) -> dict:
        """Parse Haiku's JSON review response."""
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            cleaned = "\n".join(lines)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise ValueError(f"Haiku plan review returned invalid JSON: {e}") from e

        return data

    # ------------------------------------------------------------------
    # Heuristic review path (fallback)
    # ------------------------------------------------------------------

    def _heuristic_review(
        self,
        plan: MachinePlan,
        task_summary: str,
        file_paths: list[str],
    ) -> PlanReviewResult:
        """Deterministic plan review using file-path clustering.

        Scans single-step work phases and splits them when the extracted
        file paths span enough distinct directories to indicate multiple
        concerns.  Chooses between parallel independent steps (uncoupled)
        and same-agent team steps (coupled) based on coupling signals.
        """
        result = PlanReviewResult(source="heuristic")

        # Collect all file paths: from task summary + from step context_files
        all_paths = list(file_paths)
        for phase in plan.phases:
            for step in phase.steps:
                for f in step.context_files:
                    if f not in all_paths and f != "CLAUDE.md":
                        all_paths.append(f)

        # Also extract paths from the task summary itself
        extracted = _extract_file_paths(task_summary)
        for p in extracted:
            if p not in all_paths:
                all_paths.append(p)

        if len(all_paths) < _MIN_FILES_FOR_SPLIT:
            return result

        # Group files by parent directory (concern cluster)
        dir_groups = _cluster_by_directory(all_paths)
        if len(dir_groups) < _MIN_DIRS_FOR_SPLIT:
            return result

        # Scan phases for splitting candidates
        for phase in plan.phases:
            if phase.name.lower() not in _SPLITTABLE_PHASES:
                continue
            if len(phase.steps) != 1:
                continue
            step = phase.steps[0]
            if step.team:
                # Team steps already have internal parallelism
                continue

            # Check if this step's scope spans multiple concern groups
            step_files = set(step.context_files) - {"CLAUDE.md"}
            relevant_groups = {
                d: files for d, files in dir_groups.items()
                if step_files & set(files) or not step_files
            }

            # If step has no specific context_files, use all groups
            if not step_files:
                relevant_groups = dir_groups

            if len(relevant_groups) >= _MIN_DIRS_FOR_SPLIT:
                # Decide: parallel independent steps or same-agent team?
                # Use step description (not full task_summary) to avoid
                # coupling keywords from unrelated items tainting the score.
                coupled = _detect_coupling(relevant_groups, step.task_description)

                if coupled:
                    # Coupled concerns → same-agent team with synthesis
                    team_step = _build_team_step(phase, step, relevant_groups)
                    if team_step:
                        phase.steps = [team_step]
                        result.teams_created += 1
                else:
                    # Independent concerns → parallel steps
                    new_steps = _build_parallel_steps(phase, step, relevant_groups)
                    if new_steps:
                        phase.steps = new_steps
                        result.splits_applied += 1

        return result

    # ------------------------------------------------------------------
    # Apply Haiku recommendations
    # ------------------------------------------------------------------

    def _apply_recommendations(
        self, plan: MachinePlan, recommendations: dict
    ) -> PlanReviewResult:
        """Apply parsed Haiku recommendations to the plan."""
        result = PlanReviewResult()

        # Build step lookup
        step_map: dict[str, tuple[PlanPhase, PlanStep]] = {}
        for phase in plan.phases:
            for step in phase.steps:
                step_map[step.step_id] = (phase, step)

        # Apply splits (parallel or team based on coordination field)
        for split_rec in recommendations.get("splits", []):
            step_id = split_rec.get("step_id", "")
            if step_id not in step_map:
                continue
            phase, step = step_map[step_id]
            if step.team:
                continue  # don't split existing team steps
            if phase.name.lower() not in _SPLITTABLE_PHASES:
                continue

            groups = split_rec.get("groups", [])
            if len(groups) < 2 or len(groups) > 5:
                continue

            coordination = split_rec.get("coordination", "parallel")

            if coordination == "team":
                # Build a same-agent team step
                team_step = _build_team_step_from_haiku(phase, step, groups)
                if team_step:
                    # Replace the original step with the team step
                    phase.steps = [
                        team_step if s.step_id == step_id else s
                        for s in phase.steps
                    ]
                    result.teams_created += 1
            else:
                # Build parallel independent steps
                new_steps = _build_parallel_steps_from_haiku(phase, step, groups)
                if new_steps:
                    phase.steps = [
                        s if s.step_id != step_id else new_steps[0]
                        for s in phase.steps
                    ]
                    idx = next(
                        i for i, s in enumerate(phase.steps)
                        if s.step_id == new_steps[0].step_id
                    )
                    phase.steps[idx:idx + 1] = new_steps
                    result.splits_applied += 1

        # Apply dependency recommendations
        # Rebuild step_map after splits
        step_map = {}
        for phase in plan.phases:
            for step in phase.steps:
                step_map[step.step_id] = (phase, step)

        for dep_rec in recommendations.get("dependencies", []):
            step_id = dep_rec.get("step_id", "")
            depends_on = dep_rec.get("depends_on", "")
            if step_id in step_map and depends_on in step_map:
                _, step = step_map[step_id]
                if depends_on not in step.depends_on:
                    step.depends_on.append(depends_on)
                    result.dependencies_added += 1

        # Collect warnings
        result.warnings = recommendations.get("warnings", [])

        return result


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _extract_file_paths(text: str) -> list[str]:
    """Extract file path candidates from text.

    Mirrors ``IntelligentPlanner._extract_file_paths`` but is a standalone
    function for use by the reviewer without requiring a planner instance.
    """
    pattern = r'(?:^|[\s(])([a-zA-Z0-9_./-]+(?:\.[a-zA-Z0-9]+|/))'
    candidates = re.findall(pattern, text)
    seen: set[str] = set()
    result: list[str] = []
    for c in candidates:
        last_part = c.split("/")[-1]
        ext_match = (
            "." in last_part
            and f".{last_part.rsplit('.', 1)[-1]}" in _CODE_EXTENSIONS
        )
        if ("/" in c or ext_match) and c not in seen:
            seen.add(c)
            result.append(c)
    return result


def _cluster_by_directory(file_paths: list[str]) -> dict[str, list[str]]:
    """Group file paths by their parent directory.

    For paths without a directory component (bare filenames), groups them
    under a synthetic ``"root"`` key.  Returns only groups with at least
    one file.
    """
    clusters: dict[str, list[str]] = defaultdict(list)
    for path in file_paths:
        parts = path.rsplit("/", 1)
        if len(parts) == 2:
            directory = parts[0]
        else:
            directory = "root"
        # Normalize directory to the most specific component
        # e.g. "agent_baton/core/engine" -> "engine"
        # This groups files by their immediate concern area
        dir_key = directory.rstrip("/").rsplit("/", 1)[-1] if "/" in directory else directory
        clusters[dir_key].append(path)
    return dict(clusters)


def _humanize_directory(dir_name: str) -> str:
    """Convert a directory name into a human-readable concern label."""
    replacements = {
        "engine": "engine core",
        "cli": "CLI layer",
        "commands": "CLI commands",
        "execution": "execution commands",
        "models": "data models",
        "storage": "storage layer",
        "runtime": "runtime system",
        "orchestration": "orchestration layer",
        "observe": "observability",
        "govern": "governance",
        "improve": "improvement pipeline",
        "learn": "learning system",
        "distribute": "distribution",
        "pmo": "PMO subsystem",
        "events": "event system",
        "root": "project root",
    }
    return replacements.get(dir_name.lower(), dir_name)


# ---------------------------------------------------------------------------
# Coupling detection
# ---------------------------------------------------------------------------

# Pairs of directory concerns that are typically coupled — changes in one
# often require awareness of the other.
_COUPLED_PAIRS: set[frozenset[str]] = {
    frozenset({"engine", "execution"}),   # engine internals ↔ CLI execution commands
    frozenset({"engine", "runtime"}),     # engine ↔ runtime worker
    frozenset({"engine", "models"}),      # engine ↔ data models it consumes
    frozenset({"models", "storage"}),     # data models ↔ storage layer
    frozenset({"models", "cli"}),         # data models ↔ CLI output
    frozenset({"engine", "cli"}),         # engine ↔ CLI layer
    frozenset({"engine", "commands"}),    # engine ↔ CLI commands
    frozenset({"storage", "commands"}),   # storage ↔ CLI commands
    frozenset({"runtime", "engine"}),     # runtime ↔ engine
    frozenset({"orchestration", "engine"}),  # orchestration ↔ engine
}

# Keywords in task descriptions that signal coupled work across layers
_COUPLING_KEYWORDS = [
    "wire", "integrate", "connect", "plumb", "thread through",
    "end-to-end", "e2e", "propagate", "flow",
    "api change", "interface change", "contract", "protocol",
    "schema change", "model change",
]


def _detect_coupling(
    dir_groups: dict[str, list[str]],
    task_summary: str,
) -> bool:
    """Detect whether concern groups are coupled or independent.

    Returns True when the groups should use team coordination (coupled),
    False when they should use parallel independent steps.

    Coupling signals:
    1. Directory pairs that are known to be tightly coupled
    2. Task description keywords suggesting cross-layer integration
    3. Multiple groups touching the same package (shared import base)
    """
    group_names = set(dir_groups.keys())

    # Signal 1: known coupled directory pairs
    coupled_pair_count = sum(
        1 for pair in _COUPLED_PAIRS
        if pair <= group_names  # both members of the pair are present
    )

    # Signal 2: coupling keywords in task description
    summary_lower = task_summary.lower()
    keyword_hits = sum(1 for kw in _COUPLING_KEYWORDS if kw in summary_lower)

    # Signal 3: groups sharing a common parent package (e.g., both under core/)
    # Detected by checking if 2+ groups have files with the same grandparent dir
    grandparents: dict[str, int] = defaultdict(int)
    for files in dir_groups.values():
        for f in files:
            parts = f.split("/")
            if len(parts) >= 3:
                # e.g., "agent_baton/core/engine/x.py" → grandparent is "core"
                gp = parts[-3] if len(parts) >= 3 else parts[0]
                grandparents[gp] += 1
                break  # one file per group is enough
    shared_parent = any(count >= 2 for count in grandparents.values())

    # Decision: directory pairs alone don't indicate coupling — many bug-fix
    # plans touch engine+runtime without the items being related.  Pairs only
    # count when reinforced by integration keywords in the description.
    # This prevents multi-bug-fix plans from being falsely coupled while
    # still detecting genuine cross-layer integration work.
    if keyword_hits == 0:
        # No integration language → directories are just co-present, not coupled
        return False
    score = coupled_pair_count + keyword_hits + (1 if shared_parent else 0)
    # When many concern groups are present (4+), a single keyword in a long
    # multi-item description is likely incidental (applies to one item, not
    # the whole step).  Require keyword density proportional to group count.
    if len(group_names) >= 4 and keyword_hits < 2:
        return False
    return score >= 3


# ---------------------------------------------------------------------------
# Step builders — parallel independent steps
# ---------------------------------------------------------------------------

def _build_parallel_steps(
    phase: PlanPhase,
    original_step: PlanStep,
    dir_groups: dict[str, list[str]],
) -> list[PlanStep] | None:
    """Build parallel independent steps from directory-concern groups."""
    groups = list(dir_groups.items())
    if len(groups) > 5:
        groups.sort(key=lambda x: len(x[1]), reverse=True)
        groups = groups[:5]
    if len(groups) < 2:
        return None

    new_steps: list[PlanStep] = []
    for idx, (dir_name, files) in enumerate(groups, start=1):
        step_id = f"{phase.phase_id}.{idx}"
        concern = _humanize_directory(dir_name)
        scoped_desc = (
            f"{original_step.task_description.split('.')[0]} "
            f"— focus on {concern} ({', '.join(files[:3])}"
            f"{'...' if len(files) > 3 else ''})."
        )
        new_steps.append(PlanStep(
            step_id=step_id,
            agent_name=original_step.agent_name,
            task_description=scoped_desc,
            model=original_step.model,
            depends_on=[],
            deliverables=list(original_step.deliverables),
            allowed_paths=list(original_step.allowed_paths),
            blocked_paths=list(original_step.blocked_paths),
            context_files=list(files),
            knowledge=list(original_step.knowledge),
            mcp_servers=list(original_step.mcp_servers),
        ))
    return new_steps


def _build_parallel_steps_from_haiku(
    phase: PlanPhase,
    original_step: PlanStep,
    groups: list[dict],
) -> list[PlanStep] | None:
    """Build parallel independent steps from Haiku group recommendations."""
    if len(groups) < 2 or len(groups) > 5:
        return None

    new_steps: list[PlanStep] = []
    for idx, grp in enumerate(groups, start=1):
        step_id = f"{phase.phase_id}.{idx}"
        label = grp.get("label", f"Group {idx}")
        files = grp.get("files", [])
        hint = grp.get("description_hint", "")

        desc = hint if hint else (
            f"{original_step.task_description.split('.')[0]} "
            f"— focus on {label}."
        )
        new_steps.append(PlanStep(
            step_id=step_id,
            agent_name=original_step.agent_name,
            task_description=desc,
            model=original_step.model,
            depends_on=[],
            deliverables=list(original_step.deliverables),
            allowed_paths=list(original_step.allowed_paths),
            blocked_paths=list(original_step.blocked_paths),
            context_files=files if files else list(original_step.context_files),
            knowledge=list(original_step.knowledge),
            mcp_servers=list(original_step.mcp_servers),
        ))
    return new_steps


# ---------------------------------------------------------------------------
# Step builders — same-agent team steps
# ---------------------------------------------------------------------------

def _build_team_step(
    phase: PlanPhase,
    original_step: PlanStep,
    dir_groups: dict[str, list[str]],
) -> PlanStep | None:
    """Build a same-agent team step from directory-concern groups.

    Creates a team where each member handles a different concern layer,
    all using the same agent type.  The first member is the lead;
    remaining members are implementers.
    """
    groups = list(dir_groups.items())
    if len(groups) > 5:
        groups.sort(key=lambda x: len(x[1]), reverse=True)
        groups = groups[:5]
    if len(groups) < 2:
        return None

    letters = "abcdefghijklmnopqrstuvwxyz"
    step_id = f"{phase.phase_id}.1"
    members: list[TeamMember] = []

    for idx, (dir_name, files) in enumerate(groups):
        member_id = f"{step_id}.{letters[idx]}"
        concern = _humanize_directory(dir_name)
        role = "lead" if idx == 0 else "implementer"
        scoped_desc = (
            f"{original_step.task_description.split('.')[0]} "
            f"— focus on {concern} ({', '.join(files[:3])}"
            f"{'...' if len(files) > 3 else ''})."
        )
        members.append(TeamMember(
            member_id=member_id,
            agent_name=original_step.agent_name,
            role=role,
            task_description=scoped_desc,
            model=original_step.model,
            deliverables=list(original_step.deliverables),
        ))

    combined_desc = (
        f"Team implementation: {original_step.task_description.split('.')[0]} "
        f"across {len(members)} concern areas "
        f"({', '.join(_humanize_directory(g[0]) for g in groups)})."
    )

    return PlanStep(
        step_id=step_id,
        agent_name="team",
        task_description=combined_desc,
        team=members,
        deliverables=list(original_step.deliverables),
        knowledge=list(original_step.knowledge),
        mcp_servers=list(original_step.mcp_servers),
        synthesis=SynthesisSpec(
            strategy="merge_files",
            conflict_handling="auto_merge",
        ),
    )


def _build_team_step_from_haiku(
    phase: PlanPhase,
    original_step: PlanStep,
    groups: list[dict],
) -> PlanStep | None:
    """Build a same-agent team step from Haiku group recommendations."""
    if len(groups) < 2 or len(groups) > 5:
        return None

    letters = "abcdefghijklmnopqrstuvwxyz"
    step_id = f"{phase.phase_id}.1"

    # Build label→member_id map for dependency resolution
    label_to_id: dict[str, str] = {}
    for idx, grp in enumerate(groups):
        label = grp.get("label", f"Group {idx + 1}")
        label_to_id[label] = f"{step_id}.{letters[idx]}"

    members: list[TeamMember] = []
    for idx, grp in enumerate(groups):
        member_id = f"{step_id}.{letters[idx]}"
        label = grp.get("label", f"Group {idx + 1}")
        hint = grp.get("description_hint", "")
        role = "lead" if idx == 0 else "implementer"

        desc = hint if hint else (
            f"{original_step.task_description.split('.')[0]} "
            f"— focus on {label}."
        )

        # Resolve inter-member dependencies from depends_on_groups
        dep_labels = grp.get("depends_on_groups", [])
        member_deps = [
            label_to_id[dl] for dl in dep_labels
            if dl in label_to_id
        ]

        members.append(TeamMember(
            member_id=member_id,
            agent_name=original_step.agent_name,
            role=role,
            task_description=desc,
            model=original_step.model,
            depends_on=member_deps,
            deliverables=list(original_step.deliverables),
        ))

    labels = [grp.get("label", f"Group {i+1}") for i, grp in enumerate(groups)]
    combined_desc = (
        f"Team implementation: {original_step.task_description.split('.')[0]} "
        f"across {len(members)} concern areas ({', '.join(labels)})."
    )

    return PlanStep(
        step_id=step_id,
        agent_name="team",
        task_description=combined_desc,
        team=members,
        deliverables=list(original_step.deliverables),
        knowledge=list(original_step.knowledge),
        mcp_servers=list(original_step.mcp_servers),
        synthesis=SynthesisSpec(
            strategy="merge_files",
            conflict_handling="auto_merge",
        ),
    )
