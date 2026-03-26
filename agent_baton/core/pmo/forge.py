"""ForgeSession — Smart Forge consultative plan creation.

The Smart Forge provides an interactive, interview-driven workflow for
creating execution plans.  It wraps ``IntelligentPlanner.create_plan()``
and adds:

- **Interview generation** — deterministic rule-based analysis of a
  draft plan to identify ambiguities (missing tests, no gates, high risk,
  multi-agent coordination concerns).  Returns structured questions.
- **Plan refinement** — re-generates the plan incorporating the user's
  interview answers as additional context.
- **Signal triage** — converts a PMO signal (e.g. a production incident)
  into a bug-fix plan and links them.

This module does NOT call the Anthropic API directly.  All plan generation
is delegated to ``IntelligentPlanner.create_plan()``, which handles agent
routing, risk assessment, and phase sequencing.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from agent_baton.core.pmo.store import PmoStore
from agent_baton.models.execution import MachinePlan
from agent_baton.models.pmo import InterviewQuestion, InterviewAnswer, PmoProject

if TYPE_CHECKING:
    from agent_baton.core.engine.planner import IntelligentPlanner


class ForgeSession:
    """Smart Forge session — create and refine execution plans.

    Orchestrates the consultative plan creation flow:

    1. ``create_plan`` — generate an initial plan from a description.
    2. ``generate_interview`` — produce 3-5 targeted questions about
       the plan's quality and completeness.
    3. ``regenerate_plan`` — re-generate incorporating user answers.
    4. ``save_plan`` — persist the approved plan to the project.

    Attributes:
        _planner: An ``IntelligentPlanner`` instance (typed as ``object``
            to avoid circular imports).
        _store: A ``PmoStore`` (or ``PmoSqliteStore``) used to look up
            projects and signals.
    """

    def __init__(
        self,
        planner: IntelligentPlanner,
        store: PmoStore,
    ) -> None:
        self._planner = planner
        self._store = store

    def create_plan(
        self,
        description: str,
        program: str,  # noqa: ARG002
        project_id: str,
        *,
        task_type: str | None = None,
        priority: int = 0,  # noqa: ARG002
    ) -> MachinePlan:
        """Create an execution plan via ``IntelligentPlanner``.

        Looks up the project by ``project_id`` in the PMO store to
        determine the project root path, then delegates to
        ``IntelligentPlanner.create_plan()`` for agent routing, risk
        assessment, and phase sequencing.

        Args:
            description: Natural-language task description (the PRD).
            program: Program code (e.g. ``"RW"``).
            project_id: ID of the registered project to scope the plan to.
            task_type: Optional task type override (e.g. ``"new-feature"``,
                ``"bug-fix"``).
            priority: 0=normal, 1=high, 2=critical.

        Returns:
            A ``MachinePlan`` ready for review, interview, and approval.
        """
        project = self._store.get_project(project_id)
        project_root = Path(project.path) if project else None

        plan: MachinePlan = self._planner.create_plan(
            task_summary=description,
            task_type=task_type,
            project_root=project_root,
        )
        return plan

    def save_plan(
        self,
        plan: MachinePlan,
        project: PmoProject,
    ) -> Path:
        """Save an approved plan to the project's team-context directory.

        Writes both ``plan.json`` (consumed by the execution engine) and
        ``plan.md`` (human-readable) into the task-scoped subdirectory
        under ``.claude/team-context/executions/<task_id>/``.

        Does NOT create an ``ExecutionState`` — that happens when
        ``baton execute start`` is run.

        Args:
            plan: The approved plan to persist.
            project: The target project (provides the filesystem path).

        Returns:
            The absolute path to the written ``plan.json`` file.
        """
        from agent_baton.core.orchestration.context import ContextManager
        context_root = Path(project.path) / ".claude" / "team-context"
        # Write into task-scoped directory
        ctx = ContextManager(
            team_context_dir=context_root,
            task_id=plan.task_id,
        )
        ctx.write_plan(plan)

        return ctx.plan_json_path

    def generate_interview(
        self,
        plan: MachinePlan,
        feedback: str | None = None,
    ) -> list[InterviewQuestion]:
        """Generate structured interview questions from plan analysis.

        Examines the plan's structure to identify ambiguities and missing
        context using deterministic rule-based analysis (no LLM call).
        Checks for missing testing steps, high risk without review gates,
        multi-agent coordination concerns, missing QA gates, and scope
        concerns on large plans.

        Args:
            plan: The draft plan to analyze.
            feedback: Optional user feedback from a previous iteration.
                If provided, a follow-up question is generated.

        Returns:
            A list of 3-5 ``InterviewQuestion`` instances with
            ``answer_type`` of ``'choice'`` or ``'text'``.
        """
        questions: list[InterviewQuestion] = []
        all_agents = {
            s.agent_name for p in plan.phases for s in p.steps
        }
        all_step_descs = [
            s.task_description.lower() for p in plan.phases for s in p.steps
        ]
        has_test_step = any("test" in d for d in all_step_descs)
        has_gate = any(p.gate is not None for p in plan.phases)
        phase_count = len(plan.phases)

        # Q: Testing strategy
        if not has_test_step:
            questions.append(InterviewQuestion(
                id="q-testing",
                question="No testing step was included. What testing strategy should be used?",
                context="Plans without explicit test steps risk shipping untested code.",
                answer_type="choice",
                choices=["Add unit tests", "Add integration tests", "Add both", "Skip testing"],
            ))

        # Q: Risk acknowledgement for HIGH/CRITICAL
        if plan.risk_level in ("HIGH", "CRITICAL"):
            questions.append(InterviewQuestion(
                id="q-risk",
                question=f"This plan is classified as {plan.risk_level} risk. Should additional review gates be added?",
                context="High-risk plans benefit from human checkpoints between phases.",
                answer_type="choice",
                choices=["Add review gate after each phase", "Add review gate before final phase only", "No additional gates"],
            ))

        # Q: Multi-agent coordination
        if len(all_agents) > 2:
            agents_str = ", ".join(sorted(all_agents))
            questions.append(InterviewQuestion(
                id="q-coordination",
                question=f"This plan involves {len(all_agents)} agents ({agents_str}). How should handoffs work?",
                context="Multi-agent plans need clear handoff points to avoid conflicts.",
                answer_type="choice",
                choices=["Sequential phases (strict order)", "Parallel where possible", "Let the planner decide"],
            ))

        # Q: No gates at all
        if not has_gate and phase_count > 1:
            questions.append(InterviewQuestion(
                id="q-gates",
                question="No QA gates are defined. Should validation be added between phases?",
                context="Gates catch issues early before downstream phases build on broken foundations.",
                answer_type="choice",
                choices=["Add test gate after each phase", "Add gate before final phase", "No gates needed"],
            ))

        # Q: Scope / priority clarification
        if feedback:
            questions.append(InterviewQuestion(
                id="q-feedback",
                question="You mentioned: \"" + feedback[:200] + "\". Can you elaborate on what specifically should change?",
                context="Your feedback will be used to guide the re-generation.",
                answer_type="text",
            ))
        elif phase_count >= 3:
            questions.append(InterviewQuestion(
                id="q-scope",
                question=f"This plan has {phase_count} phases. Is the scope correct, or should any phases be removed or consolidated?",
                context="Larger plans take longer to execute and have more failure points.",
                answer_type="text",
            ))

        # Always ask about priorities if not already at max
        if len(questions) < 3:
            questions.append(InterviewQuestion(
                id="q-priority",
                question="Are there specific steps that should be prioritized or reordered?",
                context="Reordering can front-load the most valuable work.",
                answer_type="text",
            ))

        return questions[:5]

    def regenerate_plan(
        self,
        description: str,
        project_id: str,
        answers: list[InterviewAnswer],
        *,
        task_type: str | None = None,
        priority: int = 0,  # noqa: ARG002
    ) -> MachinePlan:
        """Re-generate a plan incorporating interview answers.

        Builds an enriched description by appending the user's answered
        questions as structured refinement context (one bullet per answer),
        then delegates to ``IntelligentPlanner.create_plan()`` which treats
        the additional context as planning constraints.

        Args:
            description: Original task description.
            project_id: Target project ID.
            answers: User responses to the interview questions.
            task_type: Optional task type override.
            _priority: Priority level (currently unused by the planner).

        Returns:
            A new ``MachinePlan`` reflecting the user's refinements.
        """
        enriched_parts = [description, "\n\n--- Refinement Context ---"]
        for ans in answers:
            enriched_parts.append(f"- {ans.question_id}: {ans.answer}")
        enriched = "\n".join(enriched_parts)

        project = self._store.get_project(project_id)
        project_root = Path(project.path) if project else None

        plan: MachinePlan = self._planner.create_plan(
            task_summary=enriched,
            task_type=task_type,
            project_root=project_root,
        )
        return plan

    def signal_to_plan(
        self,
        signal_id: str,
        project_id: str,
    ) -> MachinePlan | None:
        """Triage a PMO signal into a bug-fix plan via the Forge.

        Looks up the signal by ID, constructs a bug-fix description from
        the signal's title and body, generates a plan via
        ``create_plan(task_type="bug-fix")``, and links the signal to
        the resulting plan by setting ``signal.forge_task_id`` and
        ``signal.status = "triaged"``.

        Args:
            signal_id: The signal to triage.
            project_id: The project to scope the bug-fix plan to.

        Returns:
            The generated ``MachinePlan``, or ``None`` if the signal or
            project was not found.
        """
        config = self._store.load_config()
        signal = next(
            (s for s in config.signals if s.signal_id == signal_id), None
        )
        if signal is None:
            return None

        project = self._store.get_project(project_id)
        if project is None:
            return None

        description = (
            f"Bug fix: {signal.title}"
            + (f"\n\n{signal.description}" if signal.description else "")
        )

        plan = self.create_plan(
            description=description,
            program=project.program,
            project_id=project_id,
            task_type="bug-fix",
        )

        # Link the signal to the plan
        signal.forge_task_id = plan.task_id
        signal.status = "triaged"
        self._store.save_config(config)

        return plan
