"""ContextProfiler -- analyse trace data to compute per-agent context efficiency.

Context efficiency measures how effectively each agent converts the files
it reads into useful output (files written).  Agents that read many files
but write few are flagged as "reading too broadly," which wastes context
window budget and slows execution.

This module sits between the trace layer (which records raw file I/O
events) and the improvement layer (which can use efficiency data to
recommend routing or prompt changes):

* Reads :class:`~agent_baton.models.trace.TaskTrace` objects produced by
  :class:`~agent_baton.core.observe.trace.TraceRecorder`.
* Computes per-agent ``efficiency_score`` and cross-agent ``redundancy_rate``.
* Persists :class:`~agent_baton.models.context_profile.TaskContextProfile`
  objects as JSON for longitudinal analysis.
* :meth:`ContextProfiler.agent_summary` aggregates scores across tasks to
  reveal chronically inefficient agents.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_baton.core.observe.trace import TraceRecorder
from agent_baton.models.context_profile import AgentContextProfile, TaskContextProfile
from agent_baton.utils.time import utcnow_seconds as _utcnow

# Approximate tokens-per-character multiplier used for rough estimation.
# 1 token ≈ 4 characters is the common rule of thumb.
_CHARS_PER_TOKEN = 4
# Assumed average file size in characters when we have no file content.
_AVG_FILE_CHARS = 2_000
# Assumed average output characters per agent when we have no direct measurement.
_AVG_OUTPUT_CHARS = 800


class ContextProfiler:
    """Analyse task traces and compute context efficiency metrics.

    Profiles are persisted to ``<team_context_root>/context-profiles/<task_id>.json``.

    Usage::

        profiler = ContextProfiler()
        profile = profiler.profile_task("my-task-id")
        if profile:
            path = profiler.save_profile(profile)
    """

    _DEFAULT_CONTEXT_ROOT = Path(".claude/team-context")

    def __init__(self, team_context_root: Path | None = None) -> None:
        self._root = (team_context_root or self._DEFAULT_CONTEXT_ROOT).resolve()
        self._recorder = TraceRecorder(self._root)

    @property
    def profiles_dir(self) -> Path:
        return self._root / "context-profiles"

    # ── Core analysis ──────────────────────────────────────────────────────

    def profile_task(self, task_id: str) -> TaskContextProfile | None:
        """Build a :class:`TaskContextProfile` from the trace for *task_id*.

        Returns ``None`` if no trace is found for the given task.

        Algorithm
        ---------
        1. Load the task trace from disk.
        2. Walk events grouped by agent name; collect ``file_read`` and
           ``file_write`` event paths.
        3. Compute per-agent ``efficiency_score`` =
           ``len(files_written) / max(len(files_read), 1)``.
        4. Compute task-level redundancy: total reads vs unique reads across
           all agents.
        """
        trace = self._recorder.load_trace(task_id)
        if trace is None:
            return None

        # Map agent_name → {files_read: [], files_written: []}
        agent_reads: dict[str, list[str]] = {}
        agent_writes: dict[str, list[str]] = {}

        for event in trace.events:
            name = event.agent_name or "__system__"
            path_value = event.details.get("path", "")

            if event.event_type == "file_read":
                agent_reads.setdefault(name, [])
                if path_value:
                    agent_reads[name].append(path_value)

            elif event.event_type == "file_write":
                agent_writes.setdefault(name, [])
                if path_value:
                    agent_writes[name].append(path_value)

        # Collect all agent names that had any file activity.
        all_agent_names: set[str] = set(agent_reads.keys()) | set(agent_writes.keys())

        agent_profiles: list[AgentContextProfile] = []
        all_read_paths: list[str] = []

        for name in sorted(all_agent_names):
            reads = agent_reads.get(name, [])
            writes = agent_writes.get(name, [])

            efficiency = len(writes) / max(len(reads), 1)

            # files_referenced = intersection of reads and writes (files that
            # were read and subsequently modified — proxy for "relevant" reads).
            reads_set = set(reads)
            writes_set = set(writes)
            referenced = sorted(reads_set & writes_set)

            context_tokens = (len(reads) * _AVG_FILE_CHARS) // _CHARS_PER_TOKEN
            output_tokens = (len(writes) * _AVG_OUTPUT_CHARS) // _CHARS_PER_TOKEN

            agent_profiles.append(
                AgentContextProfile(
                    agent_name=name,
                    files_read=reads,
                    files_written=writes,
                    files_referenced=referenced,
                    context_tokens_estimate=context_tokens,
                    output_tokens_estimate=output_tokens,
                    efficiency_score=round(efficiency, 4),
                )
            )

            all_read_paths.extend(reads)

        total_files_read = len(all_read_paths)
        unique_files_read = len(set(all_read_paths))
        redundant_reads = total_files_read - unique_files_read
        redundancy_rate = (
            round(redundant_reads / total_files_read, 4)
            if total_files_read > 0
            else 0.0
        )

        return TaskContextProfile(
            task_id=task_id,
            agent_profiles=agent_profiles,
            total_files_read=total_files_read,
            unique_files_read=unique_files_read,
            redundant_reads=redundant_reads,
            redundancy_rate=redundancy_rate,
            created_at=_utcnow(),
        )

    # ── Persistence ────────────────────────────────────────────────────────

    def save_profile(self, profile: TaskContextProfile) -> Path:
        """Serialise *profile* to ``context-profiles/<task_id>.json`` and return the path."""
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        out_path = self.profiles_dir / f"{profile.task_id}.json"
        out_path.write_text(
            json.dumps(profile.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return out_path

    def load_profile(self, task_id: str) -> TaskContextProfile | None:
        """Load a profile from disk by *task_id*.  Returns ``None`` if not found."""
        path = self.profiles_dir / f"{task_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return TaskContextProfile.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def list_profiles(self, count: int = 10) -> list[Path]:
        """Return the *count* most recently modified profile files."""
        if not self.profiles_dir.exists():
            return []
        files = sorted(
            self.profiles_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return files[:count]

    # ── Aggregation ────────────────────────────────────────────────────────

    def agent_summary(self, agent_name: str) -> dict:
        """Compute aggregate context-efficiency statistics for *agent_name*.

        Scans all saved :class:`TaskContextProfile` objects and collects
        metrics for agent entries matching *agent_name*.

        Scoring methodology:
            An ``efficiency_score`` below 0.3 indicates the agent read more
            than 3x the files it actually modified -- a sign that the agent's
            prompt is too exploratory or that knowledge packs should pre-supply
            the context it needs.  Tasks with scores below this threshold are
            flagged in ``low_efficiency_tasks`` for review.

        Args:
            agent_name: Exact agent name to filter by (case-sensitive).

        Returns:
            A dict with the following keys:

            * ``times_seen`` -- number of tasks the agent appeared in.
            * ``avg_files_read`` -- average number of files read per task.
            * ``avg_efficiency`` -- mean efficiency score across tasks.
            * ``most_read_files`` -- top-5 most frequently read files
              (path to count mapping).
            * ``low_efficiency_tasks`` -- task IDs where efficiency < 0.3.
        """
        all_paths = self.list_profiles(count=1_000)
        times_seen = 0
        total_files_read = 0
        total_efficiency = 0.0
        file_read_counts: dict[str, int] = {}
        low_efficiency_tasks: list[str] = []

        for path in all_paths:
            profile = self.load_profile(path.stem)
            if profile is None:
                continue
            for ap in profile.agent_profiles:
                if ap.agent_name != agent_name:
                    continue
                times_seen += 1
                total_files_read += len(ap.files_read)
                total_efficiency += ap.efficiency_score
                for f in ap.files_read:
                    file_read_counts[f] = file_read_counts.get(f, 0) + 1
                if ap.efficiency_score < 0.3:
                    low_efficiency_tasks.append(profile.task_id)

        if times_seen == 0:
            return {
                "times_seen": 0,
                "avg_files_read": 0.0,
                "avg_efficiency": 0.0,
                "most_read_files": {},
                "low_efficiency_tasks": [],
            }

        avg_files_read = round(total_files_read / times_seen, 2)
        avg_efficiency = round(total_efficiency / times_seen, 4)

        # Top-5 most-read files, sorted by count descending then path ascending.
        sorted_files = sorted(
            file_read_counts.items(),
            key=lambda kv: (-kv[1], kv[0]),
        )
        most_read_files = dict(sorted_files[:5])

        return {
            "times_seen": times_seen,
            "avg_files_read": avg_files_read,
            "avg_efficiency": avg_efficiency,
            "most_read_files": most_read_files,
            "low_efficiency_tasks": low_efficiency_tasks,
        }

    def generate_report(self) -> str:
        """Return a Markdown report of context efficiency across all saved profiles.

        Includes a per-task summary table (agents, reads, redundancy, avg
        efficiency), overall statistics, and a flagged-agents section
        highlighting any agent whose efficiency score fell below 0.3 --
        meaning it read more than three times as many files as it wrote.

        Returns:
            A complete Markdown document string.  Returns a placeholder
            message if no profiles have been saved yet.
        """
        all_paths = self.list_profiles(count=1_000)

        if not all_paths:
            return "# Context Efficiency Report\n\nNo profiles found.\n"

        lines: list[str] = [
            "# Context Efficiency Report",
            "",
            f"Profiles analysed: {len(all_paths)}",
            "",
        ]

        # Per-task summary table.
        lines.append("## Task Summary")
        lines.append("")
        lines.append(
            "| Task ID | Agents | Total Reads | Unique Reads | Redundancy | Avg Efficiency |"
        )
        lines.append(
            "|---------|--------|-------------|--------------|------------|----------------|"
        )

        all_efficiency_scores: list[float] = []
        flagged_agents: list[tuple[str, str, float]] = []  # (task_id, agent, score)

        for path in all_paths:
            profile = self.load_profile(path.stem)
            if profile is None:
                continue

            agent_count = len(profile.agent_profiles)
            task_efficiencies = [ap.efficiency_score for ap in profile.agent_profiles]
            avg_eff = (
                round(sum(task_efficiencies) / len(task_efficiencies), 3)
                if task_efficiencies
                else 0.0
            )
            all_efficiency_scores.extend(task_efficiencies)

            redundancy_pct = f"{profile.redundancy_rate * 100:.1f}%"
            lines.append(
                f"| {profile.task_id} "
                f"| {agent_count} "
                f"| {profile.total_files_read} "
                f"| {profile.unique_files_read} "
                f"| {redundancy_pct} "
                f"| {avg_eff:.3f} |"
            )

            for ap in profile.agent_profiles:
                if ap.efficiency_score < 0.3:
                    flagged_agents.append((profile.task_id, ap.agent_name, ap.efficiency_score))

        lines.append("")

        # Overall stats.
        if all_efficiency_scores:
            overall_avg = sum(all_efficiency_scores) / len(all_efficiency_scores)
            lines.append("## Overall Statistics")
            lines.append("")
            lines.append(f"- Average efficiency score: {overall_avg:.3f}")
            lines.append(f"- Total agent profiles: {len(all_efficiency_scores)}")
            lines.append("")

        # Flagged agents.
        if flagged_agents:
            lines.append("## Flagged Agents (efficiency < 0.3 — reading too broadly)")
            lines.append("")
            lines.append("| Task ID | Agent | Efficiency Score |")
            lines.append("|---------|-------|-----------------|")
            for task_id, agent, score in flagged_agents:
                lines.append(f"| {task_id} | {agent} | {score:.3f} |")
            lines.append("")
        else:
            lines.append("## Flagged Agents")
            lines.append("")
            lines.append("No agents flagged for reading too broadly.")
            lines.append("")

        return "\n".join(lines)


