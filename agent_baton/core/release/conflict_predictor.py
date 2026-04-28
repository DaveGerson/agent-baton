"""Plan-time file-level conflict prediction (R3.7).

``ConflictPredictor`` walks a :class:`MachinePlan`, identifies which
steps within each phase are eligible to run in parallel, and flags
file-level overlaps that are likely to cause merge friction.  Results
are returned as a :class:`PlanConflictReport`.

Detection heuristics (all per-pair within a parallel group):

* **write_write (conf 0.9)** — both steps declare the *same* concrete
  path in ``allowed_paths`` or ``deliverables``.
* **read_write (conf 0.7)** — one step writes a path that appears in
  the other step's ``context_files``.
* **write_write (conf 0.6)** — one step has a *broad* allowlist
  (a directory or the literal ``"any"``/``"*"``) that contains a
  concrete write target of the other step.

This is a velocity-positive Tier 2 tool: it produces warnings, never
blocks.  The engine integration is opt-in via ``BATON_CONFLICT_PREDICT``
or the ``--predict-conflicts`` flag on ``baton execute start``.
"""
from __future__ import annotations

from itertools import combinations
from typing import Iterable

from agent_baton.models.conflict_prediction import (
    FileConflict,
    PlanConflictReport,
)
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep


# Tokens that indicate a step's allowlist is "broad" — i.e. it does not
# pin to a specific file and may overlap with siblings' writes.
_BROAD_ALLOWLIST_TOKENS: frozenset[str] = frozenset({"any", "*", "**", "."})


class ConflictPredictor:
    """Predict file-level conflicts between parallel-eligible plan steps."""

    def __init__(self, plan: MachinePlan) -> None:
        self._plan = plan

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def predict(self) -> PlanConflictReport:
        conflicts: list[FileConflict] = []
        parallel_groups = 0
        total_steps = 0

        for phase in self._plan.phases:
            total_steps += len(phase.steps)
            group = self._parallel_group(phase)
            if len(group) < 2:
                continue
            parallel_groups += 1
            for a, b in combinations(group, 2):
                conflicts.extend(self._compare_pair(a, b))

        # Stable, useful ordering: highest confidence first, then by step IDs.
        conflicts.sort(
            key=lambda c: (-c.confidence, c.step_a_id, c.step_b_id, c.file_path)
        )

        return PlanConflictReport(
            plan_id=self._plan.task_id,
            conflicts=conflicts,
            total_steps_analyzed=total_steps,
            parallel_groups_analyzed=parallel_groups,
        )

    @staticmethod
    def summarize(report: PlanConflictReport) -> str:
        """Render *report* as a short markdown block for CLI output."""
        header = (
            f"### Plan conflict report — `{report.plan_id}`\n"
            f"_Computed at {report.computed_at}_\n\n"
            f"- Steps analyzed: **{report.total_steps_analyzed}**\n"
            f"- Parallel groups analyzed: **{report.parallel_groups_analyzed}**\n"
            f"- Conflicts predicted: **{len(report.conflicts)}**\n"
        )
        if not report.conflicts:
            return header + "\nNo conflicts predicted.\n"

        lines = [
            header,
            "",
            "| Step A | Step B | File | Type | Confidence | Reason |",
            "|--------|--------|------|------|-----------:|--------|",
        ]
        for c in report.conflicts:
            lines.append(
                f"| {c.step_a_id} | {c.step_b_id} | `{c.file_path}` | "
                f"{c.conflict_type} | {c.confidence:.2f} | {c.reason} |"
            )
        lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _parallel_group(phase: PlanPhase) -> list[PlanStep]:
        """Return the steps in *phase* eligible to run in parallel.

        A step is parallel-eligible when its ``depends_on`` references no
        other step in the same phase.  This matches how the executor's
        dispatcher batches phase steps.
        """
        phase_step_ids = {s.step_id for s in phase.steps}
        eligible: list[PlanStep] = []
        for step in phase.steps:
            intra_phase_deps = [
                d for d in step.depends_on if d in phase_step_ids
            ]
            if not intra_phase_deps:
                eligible.append(step)
        return eligible

    @staticmethod
    def _writes(step: PlanStep) -> set[str]:
        """Concrete file paths *step* is expected to write.

        Broad tokens (``"any"``, ``"*"``) and bare directory entries are
        excluded — those are handled separately in
        :meth:`_broad_allowlist_paths`.
        """
        out: set[str] = set()
        for raw in (*step.allowed_paths, *step.deliverables):
            p = raw.strip()
            if not p:
                continue
            if p in _BROAD_ALLOWLIST_TOKENS:
                continue
            if p.endswith("/"):
                continue  # directory, not a concrete file
            out.add(p)
        return out

    @staticmethod
    def _reads(step: PlanStep) -> set[str]:
        return {p.strip() for p in step.context_files if p.strip()}

    @staticmethod
    def _broad_allowlist_paths(step: PlanStep) -> set[str]:
        """Broad write scopes (directories or wildcards) declared by *step*."""
        out: set[str] = set()
        for raw in step.allowed_paths:
            p = raw.strip()
            if not p:
                continue
            if p in _BROAD_ALLOWLIST_TOKENS or p.endswith("/"):
                out.add(p)
        return out

    @classmethod
    def _broad_covers(cls, broad: str, concrete: str) -> bool:
        """Return True when a broad scope *broad* contains a concrete path."""
        if broad in _BROAD_ALLOWLIST_TOKENS:
            return True
        if broad.endswith("/"):
            return concrete.startswith(broad)
        return False

    @classmethod
    def _compare_pair(
        cls, a: PlanStep, b: PlanStep
    ) -> Iterable[FileConflict]:
        # Normalize ordering so step_a_id <= step_b_id; makes dedupe trivial.
        if a.step_id > b.step_id:
            a, b = b, a

        a_writes = cls._writes(a)
        b_writes = cls._writes(b)
        a_reads = cls._reads(a)
        b_reads = cls._reads(b)
        a_broad = cls._broad_allowlist_paths(a)
        b_broad = cls._broad_allowlist_paths(b)

        seen: set[tuple[str, str]] = set()  # (file_path, conflict_type)
        results: list[FileConflict] = []

        def _emit(
            file_path: str,
            conflict_type: str,
            confidence: float,
            reason: str,
        ) -> None:
            key = (file_path, conflict_type)
            if key in seen:
                return
            seen.add(key)
            results.append(
                FileConflict(
                    step_a_id=a.step_id,
                    step_b_id=b.step_id,
                    file_path=file_path,
                    conflict_type=conflict_type,
                    confidence=confidence,
                    reason=reason,
                )
            )

        # 1. write/write — both steps name the same concrete path.
        for path in sorted(a_writes & b_writes):
            _emit(
                path,
                "write_write",
                0.9,
                f"Both steps declare write access to {path}",
            )

        # 2. read/write — A writes a path B reads, or vice versa.
        for path in sorted(a_writes & b_reads):
            _emit(
                path,
                "read_write",
                0.7,
                f"{a.step_id} writes {path}; {b.step_id} reads it",
            )
        for path in sorted(b_writes & a_reads):
            _emit(
                path,
                "read_write",
                0.7,
                f"{b.step_id} writes {path}; {a.step_id} reads it",
            )

        # 3. broad/specific write_write — broad allowlist covers other's write.
        for broad in sorted(a_broad):
            for path in sorted(b_writes):
                if cls._broad_covers(broad, path):
                    _emit(
                        path,
                        "write_write",
                        0.6,
                        (
                            f"{a.step_id} has broad allowlist '{broad}' "
                            f"that covers {b.step_id}'s write to {path}"
                        ),
                    )
        for broad in sorted(b_broad):
            for path in sorted(a_writes):
                if cls._broad_covers(broad, path):
                    _emit(
                        path,
                        "write_write",
                        0.6,
                        (
                            f"{b.step_id} has broad allowlist '{broad}' "
                            f"that covers {a.step_id}'s write to {path}"
                        ),
                    )

        return results
