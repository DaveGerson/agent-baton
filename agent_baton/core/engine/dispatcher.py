"""Prompt dispatcher — generates delegation prompts for agent subagents."""
from __future__ import annotations

from pathlib import Path

from agent_baton.models.execution import (
    ActionType,
    ExecutionAction,
    PlanGate,
    PlanStep,
    TeamMember,
)
from agent_baton.models.knowledge import KnowledgeAttachment

_KNOWLEDGE_GAPS_BLOCK = """\
## Knowledge Gaps

If you lack sufficient context to complete this task correctly:
- Output `KNOWLEDGE_GAP: <description>` with what you need
- Include `CONFIDENCE: none | low | partial` and `TYPE: factual | contextual`
- Stop and report your partial progress

Do not guess through gaps on HIGH/CRITICAL risk tasks.
Resolved decisions (provided above) are final — do not revisit them."""

# Success criteria by task type — shown in the delegation prompt to make the
# definition of done concrete.  Selected by the caller via the task_type arg.
_SUCCESS_CRITERIA: dict[str, str] = {
    "bug-fix": "The bug no longer reproduces and a regression test prevents recurrence.",
    "new-feature": "The feature works as specified and has test coverage.",
    "refactor": "Behavior is unchanged, code is cleaner, and tests still pass.",
    "test": "Test coverage meaningfully improved with no false positives.",
    "documentation": "Documentation is accurate, complete, and matches current code.",
    "migration": "Data is migrated correctly with rollback capability verified.",
    "data-analysis": "Analysis answers the stated question with supporting evidence.",
}


class PromptDispatcher:
    """Generates delegation prompts for agent subagents.

    Turns a PlanStep + shared context into a complete delegation prompt
    following the comms-protocols template.  The class is stateless; every
    method operates purely on its arguments.
    """

    # ------------------------------------------------------------------
    # Knowledge section builder
    # ------------------------------------------------------------------

    def _build_knowledge_section(self, attachments: list[KnowledgeAttachment]) -> str:
        """Render a knowledge section string from resolved attachments.

        Inline attachments are rendered under '## Knowledge Context' with
        their full document content loaded lazily from source_path.
        Referenced attachments are listed under '## Knowledge References'
        with a retrieval hint.

        Returns an empty string when *attachments* is empty so there is zero
        overhead for steps without knowledge.
        """
        if not attachments:
            return ""

        inline_parts: list[str] = []
        reference_parts: list[str] = []

        for attachment in attachments:
            pack_label = attachment.pack_name or "standalone"

            if attachment.delivery == "inline":
                # Lazy-load content from source_path if content is not already cached
                content = self._load_attachment_content(attachment)
                grounding_line = f"\n{attachment.grounding}" if attachment.grounding else ""
                inline_parts.append(
                    f"### {attachment.document_name} ({pack_label})"
                    + grounding_line
                    + f"\n\n{content}"
                )
            else:
                # Reference delivery: path + summary + retrieval hint
                retrieval_hint = self._build_retrieval_hint(attachment)
                reference_parts.append(
                    f"- **{attachment.document_name}** ({pack_label}): {attachment.grounding or ''}"
                    + f"\n  {retrieval_hint}"
                )

        sections: list[str] = []

        if inline_parts:
            sections.append("## Knowledge Context\n")
            sections.append("\n\n".join(inline_parts))

        if reference_parts:
            sections.append("## Knowledge References\n")
            sections.append("\n".join(reference_parts))

        return "\n".join(sections)

    @staticmethod
    def _load_attachment_content(attachment: KnowledgeAttachment) -> str:
        """Return the document content for an inline attachment.

        Reads from source_path on disk if the attachment's content string is
        empty (lazy loading).  Returns a placeholder when the path is absent or
        unreadable so that prompt assembly never raises.
        """
        if attachment.path:
            path = Path(attachment.path)
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                return f"_Content unavailable: {attachment.path}_"
        return f"_Content unavailable: no path for {attachment.document_name}_"

    @staticmethod
    def _build_retrieval_hint(attachment: KnowledgeAttachment) -> str:
        """Return the retrieval hint line for a reference attachment."""
        if attachment.retrieval == "mcp-rag":
            # RAG instructions only when MCP RAG server is available
            return (
                f'Retrieve via: query RAG server for '
                f'"{attachment.document_name}: {attachment.grounding or attachment.document_name}"'
            )
        return f"Retrieve via: `Read {attachment.path}`"

    # ------------------------------------------------------------------
    # Delegation prompt builders
    # ------------------------------------------------------------------

    def build_delegation_prompt(
        self,
        step: PlanStep,
        *,
        shared_context: str = "",
        handoff_from: str = "",
        project_description: str = "",
        task_summary: str = "",
        task_type: str = "",
    ) -> str:
        """Build a complete delegation prompt for an agent.

        Args:
            step: The PlanStep describing what this agent should do.
            shared_context: Pre-built context document text (context.md content).
            handoff_from: Free-text summary from the previous step's output.
            project_description: One-line description of the overall project.
            task_summary: High-level summary of the mission being executed.
                Forwarded verbatim in the Intent section so the agent sees the
                user's original words unmodified.
            task_type: Task type key (e.g. "bug-fix", "new-feature") used to
                select the Success Criteria text.  Defaults to "" (no criteria shown).

        Returns:
            A formatted markdown delegation prompt ready to pass to the Agent tool.
        """
        role = step.agent_name
        project_line = project_description or task_summary or "this project"

        # Context files section
        if step.context_files:
            context_files_text = "\n".join(f"- `{f}`" for f in step.context_files)
        else:
            context_files_text = "_No specific files specified — use your judgment._"

        # Deliverables section
        if step.deliverables:
            deliverables_text = "\n".join(f"- {d}" for d in step.deliverables)
        else:
            deliverables_text = "_See task description for expected outputs._"

        # Boundaries
        allowed = ", ".join(step.allowed_paths) if step.allowed_paths else "any"
        blocked = ", ".join(step.blocked_paths) if step.blocked_paths else "none"

        # Previous step handoff
        previous_output = handoff_from.strip() if handoff_from.strip() else "This is the first step."

        # Shared context block
        shared_context_block = shared_context.strip() if shared_context.strip() else "_No shared context provided._"

        # Knowledge section (empty string when no attachments)
        knowledge_section = self._build_knowledge_section(step.knowledge)

        article = "an" if role[0:1] in "aeiouAEIOU" else "a"
        parts = [
            f"You are {article} {role} working on {project_line}.",
            "",
            "## Shared Context",
            shared_context_block,
            "",
            "Read `CLAUDE.md` for project conventions.",
            "",
        ]

        # Intent section — user's original words, unmodified, before the task
        if task_summary.strip():
            parts += [
                "## Intent",
                task_summary.strip(),
                "",
            ]

        # Insert knowledge section between Shared Context and Your Task
        if knowledge_section:
            parts.append(knowledge_section)
            parts.append("")

        parts += [
            f"## Your Task (Step {step.step_id})",
            step.task_description.strip(),
            "",
        ]

        # Success Criteria section — derived from task type
        success_criteria = _SUCCESS_CRITERIA.get(task_type, "")
        if success_criteria:
            parts += [
                "## Success Criteria",
                success_criteria,
                "",
            ]

        parts += [
            "## Files to Read",
            context_files_text,
            "",
            "## Deliverables",
            deliverables_text,
            "",
            "## Boundaries",
            f"- Write to: {allowed}",
            f"- Do NOT write to: {blocked}",
            "",
            _KNOWLEDGE_GAPS_BLOCK,
            "",
            "## Previous Step Output",
            previous_output,
            "",
            "## Decision Logging",
            "When you make a non-obvious decision, document it in your output",
            "under a 'Decisions' heading explaining why you chose this approach.",
            "",
            "## Deviations",
            "If the plan's approach doesn't fit the actual situation, document what",
            "you changed and why under a 'Deviations' heading. This feeds the",
            "learning loop — deviations improve future plans.",
        ]

        return "\n".join(parts)

    def build_team_delegation_prompt(
        self,
        step: PlanStep,
        member: TeamMember,
        *,
        shared_context: str = "",
        task_summary: str = "",
        team_overview: str = "",
    ) -> str:
        """Build a delegation prompt for a single team member.

        Includes the member's specific task, their role, the team
        composition, and any dependencies on other members' output.

        Knowledge is resolved at the step level and shared across all team
        members — every member in a team step receives the same knowledge
        attachments because they are working toward the same phase goal.
        """
        role = member.agent_name
        project_line = task_summary or "this project"
        article = "an" if role[0:1] in "aeiouAEIOU" else "a"

        deps_text = ""
        if member.depends_on:
            deps_text = (
                "\n## Dependencies\n"
                "Wait for and build on the output from: "
                + ", ".join(member.depends_on)
            )

        deliverables_text = (
            "\n".join(f"- {d}" for d in member.deliverables)
            if member.deliverables
            else "_See task description._"
        )

        shared_block = shared_context.strip() if shared_context.strip() else "_No shared context._"

        # Knowledge section — resolved at step level, shared by all team members
        knowledge_section = self._build_knowledge_section(step.knowledge)

        parts = [
            f"You are {article} {role} working on {project_line}.",
            f"You are part of a team: {team_overview}.",
            f"Your role: **{member.role}**.",
            "",
            "## Shared Context",
            shared_block,
            "",
            "Read `CLAUDE.md` for project conventions.",
            "",
        ]

        # Insert knowledge section between Shared Context and Your Task
        if knowledge_section:
            parts.append(knowledge_section)
            parts.append("")

        parts.extend([
            f"## Your Task (Step {step.step_id}, Member {member.member_id})",
            (member.task_description or step.task_description).strip(),
            "",
            "## Deliverables",
            deliverables_text,
        ])
        if deps_text:
            parts.append(deps_text)
        parts.extend([
            "",
            "## Boundaries",
            "- Coordinate with your team members on shared resources.",
            "",
            _KNOWLEDGE_GAPS_BLOCK,
            "",
            "## Decision Logging",
            "When you make a non-obvious decision, document it in your output",
            "under a 'Decisions' heading explaining why you chose this approach.",
        ])
        return "\n".join(parts)

    def build_gate_prompt(
        self,
        gate: PlanGate,
        *,
        phase_name: str = "",
        files_changed: list[str] | None = None,
    ) -> str:
        """Build instructions for running a QA gate.

        For automated gates (have a command): returns the command string to run.
        For review gates (no command): returns a prompt for the code-reviewer
        agent describing what to inspect.

        Args:
            gate: The PlanGate to build instructions for.
            phase_name: Human-readable name of the phase that just completed.
            files_changed: Optional list of files that changed in this phase.

        Returns:
            Command string for automated gates, or a reviewer prompt for review gates.
        """
        phase_label = f" for phase '{phase_name}'" if phase_name else ""

        if gate.command:
            # Automated gate — return the command to run
            command = gate.command
            if files_changed:
                # Substitute {files} placeholder if present
                files_str = " ".join(files_changed)
                command = command.replace("{files}", files_str)
            return command

        # Review gate — build a reviewer prompt
        files_section = ""
        if files_changed:
            file_list = "\n".join(f"- `{f}`" for f in files_changed)
            files_section = f"\n\n## Files Changed\n{file_list}"

        description = gate.description or f"Review the code changes{phase_label}."

        parts = [
            f"## Code Review Gate{phase_label}",
            "",
            f"**Gate type**: {gate.gate_type}",
            "",
            "## Review Task",
            description,
        ]

        if gate.fail_on:
            fail_criteria = "\n".join(f"- {criterion}" for criterion in gate.fail_on)
            parts.extend(["", "## Fail Criteria", fail_criteria])

        parts.extend([
            "",
            "## Instructions",
            "Review the changed files listed above. Return PASS or FAIL at the end",
            "of your review, with a brief explanation of your finding.",
        ])

        if files_section:
            # Insert files section after the gate header
            parts.insert(2, files_section)

        return "\n".join(parts)

    @staticmethod
    def build_path_enforcement(step: PlanStep) -> str | None:
        """Generate a bash guard command that blocks writes outside allowed paths.

        Returns a bash command suitable for a PreToolUse hook, or None if the
        step has no path restrictions.  The command exits 2 (BLOCK) when the
        target file is outside the allowed set or inside the blocked set.
        """
        if not step.allowed_paths and not step.blocked_paths:
            return None

        parts = []
        if step.allowed_paths:
            # Build a regex that matches any of the allowed path prefixes
            allowed_pattern = "|".join(
                p.replace(".", "\\.").replace("*", ".*")
                for p in step.allowed_paths
            )
            parts.append(
                f'if ! echo "$FILE" | grep -qE "^({allowed_pattern})"; then '
                f'echo "BLOCKED: Step {step.step_id} \u2014 write outside allowed paths: $FILE" >&2; exit 2; fi'
            )
        if step.blocked_paths:
            blocked_pattern = "|".join(
                p.replace(".", "\\.").replace("*", ".*")
                for p in step.blocked_paths
            )
            parts.append(
                f'if echo "$FILE" | grep -qE "^({blocked_pattern})"; then '
                f'echo "BLOCKED: Step {step.step_id} \u2014 write to blocked path: $FILE" >&2; exit 2; fi'
            )

        inner = "; ".join(parts)
        return f'bash -c \'FILE="$CLAUDE_TOOL_INPUT_FILE_PATH"; {inner}; exit 0\''

    def build_action(
        self,
        step: PlanStep,
        *,
        shared_context: str = "",
        handoff_from: str = "",
        project_description: str = "",
        task_summary: str = "",
        task_type: str = "",
    ) -> ExecutionAction:
        """Build a complete ExecutionAction with DISPATCH type.

        Combines build_delegation_prompt with the step metadata into an
        ExecutionAction ready to return to the caller (the driving session).

        Args:
            step: The PlanStep to dispatch.
            shared_context: Pre-built context document text.
            handoff_from: Summary from the previous step's output.
            project_description: One-line project description.
            task_summary: High-level mission summary.
            task_type: Task type key for Success Criteria selection.

        Returns:
            An ExecutionAction with action_type=DISPATCH and a fully-built
            delegation_prompt.  path_enforcement is populated whenever the
            step declares allowed_paths or blocked_paths.
        """
        prompt = self.build_delegation_prompt(
            step,
            shared_context=shared_context,
            handoff_from=handoff_from,
            project_description=project_description,
            task_summary=task_summary,
            task_type=task_type,
        )
        enforcement = self.build_path_enforcement(step)

        return ExecutionAction(
            action_type=ActionType.DISPATCH,
            message=f"Dispatch {step.agent_name} for step {step.step_id}",
            agent_name=step.agent_name,
            agent_model=step.model,
            delegation_prompt=prompt,
            step_id=step.step_id,
            path_enforcement=enforcement or "",
        )
