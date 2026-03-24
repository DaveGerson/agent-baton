"""ForgeSession — consultative plan creation using IntelligentPlanner.

Does NOT call Anthropic API directly. Delegates entirely to
IntelligentPlanner.create_plan() for plan generation.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_baton.core.engine.persistence import StatePersistence
from agent_baton.core.pmo.store import PmoStore
from agent_baton.models.execution import ExecutionState, MachinePlan
from agent_baton.models.pmo import InterviewQuestion, InterviewAnswer, PmoProject


class ForgeSession:
    """Create and save execution plans using baton's own planner."""

    def __init__(
        self,
        planner: object,  # IntelligentPlanner (typed loosely to avoid circular deps)
        store: PmoStore,
    ) -> None:
        self._planner = planner
        self._store = store

    def create_plan(
        self,
        description: str,
        program: str,
        project_id: str,
        *,
        task_type: str | None = None,
        priority: int = 0,
    ) -> MachinePlan:
        """Create an execution plan via IntelligentPlanner.

        Parameters
        ----------
        description:
            Natural-language task description (the PRD).
        program:
            Program code (e.g., "RW").
        project_id:
            ID of the registered project to scope the plan to.
        task_type:
            Optional task type override (e.g., "new-feature", "bug-fix").
        priority:
            0=normal, 1=high, 2=critical.

        Returns
        -------
        MachinePlan ready for review and approval.
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
        """Save an approved plan to the project's team-context.

        Writes both plan.json (for the engine) and plan.md (for humans).
        Does NOT create an ExecutionState — that happens when
        ``baton execute start`` is run.

        Returns the path to the written plan.json.
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

        Examines the plan's structure to identify ambiguities and
        missing context. Returns 3-5 targeted questions. This is
        deterministic rule-based analysis, not an LLM call.
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
        priority: int = 0,
    ) -> MachinePlan:
        """Re-generate a plan incorporating interview answers.

        Builds an enriched description by appending answered questions
        as structured context, then delegates to IntelligentPlanner.
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
        """Triage a signal into a plan via the Forge.

        Looks up the signal, generates a bug-fix plan, and links them.
        Returns None if signal not found.
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
