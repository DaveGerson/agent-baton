"""PlanReviewer — post-generation plan quality review.

Analyzes a completed MachinePlan and recommends structural improvements:
step splitting for overly broad single-agent steps, missing dependency
edges, and scope imbalance warnings.

Two review strategies:
1. **Haiku review** — cheap LLM call (~2000 tokens) that analyzes the
   plan structure and returns JSON recommendations.  Used for medium+
   complexity plans when the Anthropic SDK and API key are available.
2. **Heuristic review** — deterministic fallback using file-path clustering
   and task-description analysis.  Always available, catches the most
   common case (single-step work phases spanning 4+ files across 3+
   directories).

Wired into ``IntelligentPlanner.create_plan()`` at step 12c.5, after
team consolidation but before bead hints.
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field

from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep

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
    dependencies_added: int = 0
    warnings: list[str] = field(default_factory=list)
    source: str = "none"  # "haiku", "heuristic", "none"


# ---------------------------------------------------------------------------
# Haiku review prompt
# ---------------------------------------------------------------------------

_REVIEW_PROMPT_TEMPLATE = """\
You are a plan quality reviewer for a software orchestration engine.

Review this execution plan and return JSON recommendations.

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
      "groups": [
        {{
          "label": "<concern name, e.g. 'engine routing'>",
          "files": ["<file1.py>", "<file2.py>"],
          "description_hint": "<what this sub-step should do>"
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
directories/concerns. Each concern group should be independently \
implementable.
- Do NOT split steps that are already scoped to one concern.
- Do NOT split steps in Design, Test, or Review phases.
- For team steps (steps with team members), do NOT split — they already \
have internal parallelism.
- Do NOT recommend converting same-agent parallel steps into a team step. \
Teams are for multi-agent diversity, not parallelized identical work.
- Add depends_on only when a step genuinely reads output from another \
step (e.g. CLI depends on engine changes).
- Keep groups to 2-5 per split. More than 5 groups means the task itself \
should be decomposed, not just the step.
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
        independent concerns.
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
                new_steps = self._split_step_by_concerns(
                    phase, step, relevant_groups, task_summary
                )
                if new_steps:
                    phase.steps = new_steps
                    result.splits_applied += 1

        return result

    @staticmethod
    def _split_step_by_concerns(
        phase: PlanPhase,
        original_step: PlanStep,
        dir_groups: dict[str, list[str]],
        _task_summary: str,
    ) -> list[PlanStep] | None:
        """Split a single step into parallel concern-scoped steps.

        Returns the new step list, or None if splitting isn't warranted.
        """
        # Cap groups to avoid excessive fragmentation
        groups = list(dir_groups.items())
        if len(groups) > 5:
            # Merge smallest groups
            groups.sort(key=lambda x: len(x[1]), reverse=True)
            groups = groups[:5]

        if len(groups) < 2:
            return None

        new_steps: list[PlanStep] = []
        for idx, (dir_name, files) in enumerate(groups, start=1):
            step_id = f"{phase.phase_id}.{idx}"
            # Build a scoped description
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
                depends_on=[],  # parallel by default
                deliverables=list(original_step.deliverables),
                allowed_paths=list(original_step.allowed_paths),
                blocked_paths=list(original_step.blocked_paths),
                context_files=list(files),
                knowledge=list(original_step.knowledge),
                mcp_servers=list(original_step.mcp_servers),
            ))

        return new_steps

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

        # Apply splits
        for split_rec in recommendations.get("splits", []):
            step_id = split_rec.get("step_id", "")
            if step_id not in step_map:
                continue
            phase, step = step_map[step_id]
            if step.team:
                continue  # don't split team steps
            if phase.name.lower() not in _SPLITTABLE_PHASES:
                continue

            groups = split_rec.get("groups", [])
            if len(groups) < 2:
                continue

            new_steps = self._build_steps_from_haiku_groups(
                phase, step, groups
            )
            if new_steps:
                phase.steps = [
                    s if s.step_id != step_id else new_steps[0]
                    for s in phase.steps
                ]
                # Replace the original step with all new steps
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

    @staticmethod
    def _build_steps_from_haiku_groups(
        phase: PlanPhase,
        original_step: PlanStep,
        groups: list[dict],
    ) -> list[PlanStep] | None:
        """Build split steps from Haiku's group recommendations."""
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
