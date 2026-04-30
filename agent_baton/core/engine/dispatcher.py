"""Prompt dispatcher -- generates delegation prompts for agent subagents.

This module translates ``PlanStep`` objects into complete delegation prompts
following the comms-protocols template.  It handles three prompt types:

- **Single-agent delegation** (``build_delegation_prompt``): standard
  step-by-step dispatch with shared context, knowledge, handoff, and
  boundary enforcement.
- **Team member delegation** (``build_team_delegation_prompt``): per-member
  prompts within a team step, including role context and inter-member
  dependencies.
- **Gate prompts** (``build_gate_prompt``): instructions for running QA
  gate checks, either automated commands or reviewer prompts.

The class is stateless; every method operates purely on its arguments.
Knowledge sections are built lazily -- inline documents are loaded from
disk only when the attachment delivery is ``inline``.

Session-level knowledge deduplication is supported via the
``delivered_knowledge`` parameter on ``build_delegation_prompt``.  When
provided, docs that were already inlined in a prior step are downgraded to
reference delivery.  After building the prompt the caller is responsible
for persisting the updated ``delivered_knowledge`` dict back to
``ExecutionState``.
"""
from __future__ import annotations

import logging
import warnings
from pathlib import Path

from agent_baton.utils.frontmatter import parse_frontmatter

logger = logging.getLogger(__name__)
from agent_baton.models.execution import (
    ActionType,
    ExecutionAction,
    PlanGate,
    PlanStep,
    TeamMember,
)
from agent_baton.models.knowledge import KnowledgeAttachment

# Agents report knowledge gaps, discoveries, decisions, design choices, and
# conflicts using structured signals appended to their outcome text. Consolidated
# into a single compact block to minimize per-dispatch token overhead.
# Order: KNOWLEDGE_GAP first (a gap blocks progress), then BEAD signals (outputs),
# then DESIGN_CHOICE / CONFLICT (escalations).
# Full signal reference: references/signals.md
_SIGNALS_BLOCK = (
    "## Signals (append to outcome as needed)\n"
    "- `KNOWLEDGE_GAP: <desc>` + `CONFIDENCE: none|low|partial` — STOP if blocked on HIGH/CRITICAL risk.\n"
    "- `BEAD_DISCOVERY: <what>` / `BEAD_DECISION: <what> CHOSE: <x> BECAUSE: <y>` / `BEAD_WARNING: <risk>`\n"
    "- `BEAD_FEEDBACK: <bead-id> useful|misleading|outdated` (rate prior discoveries above)\n"
    "- `DESIGN_CHOICE: <desc>` + `OPTION_A/B` + `RECOMMENDATION` — only for decisions that materially change outcome.\n"
    "- `CONFLICT: <what> PARTIES: <steps> RECOMMENDATION: <resolution>` — only for genuine disagreement with prior work."
)

# Retained for backward compatibility with any external callers; prefer _SIGNALS_BLOCK.
# All three constants now point to the same consolidated block — the prompt
# builder only emits _SIGNALS_BLOCK once, so legacy reads still see the content.
_KNOWLEDGE_GAPS_LINE = _SIGNALS_BLOCK
_BEAD_SIGNALS_LINE = _SIGNALS_BLOCK
_FLAG_SIGNALS_LINE = _SIGNALS_BLOCK


# Worktree Discipline contract (Fix A from proposals/worktree-isolation-fix.md).
# Prepended to the delegation prompt whenever the engine signals
# ``isolation="worktree"``.  Text is verbatim from the proposal and must NOT
# diverge — the orchestrator template references this exact block.
_WORKTREE_DISCIPLINE_BLOCK = (
    "## Worktree Discipline (MANDATORY)\n"
    "You are running in an isolated git worktree. Your cwd at spawn is your\n"
    "worktree root. ALL file operations must be relative to your cwd.\n"
    "- Before EVERY Bash call: prepend `cd \"$PWD\" &&` or use absolute paths\n"
    "  rooted at your worktree, NOT the project root.\n"
    "- For Read/Write/Edit: convert any absolute path you see in this prompt\n"
    "  that begins with the project root to a path relative to your worktree.\n"
    "- Run `git rev-parse --show-toplevel` once. That is your root. All git\n"
    "  commands MUST report this path. If they don't, STOP and report.\n"
    "- Never `cd` out of your worktree. Never reference `/home/.../<project>/`\n"
    "  paths outside your worktree even if they appear in this prompt."
)


def _relativize_path(raw: str, project_root: Path) -> tuple[str, bool]:
    """Return ``(rendered_path, is_outside_root)``.

    Absolute paths under *project_root* are rewritten to project-relative
    form (e.g. ``/abs/proj/agent_baton/foo.py`` -> ``agent_baton/foo.py``).
    Already-relative paths are returned unchanged.  Absolute paths that
    fall outside *project_root* are returned unchanged with the second
    element set to ``True`` so the caller can flag them in the prompt.
    """
    if not raw:
        return raw, False
    candidate = Path(raw)
    if not candidate.is_absolute():
        return raw, False
    try:
        rel = candidate.resolve().relative_to(project_root.resolve())
    except ValueError:
        return raw, True
    return rel.as_posix(), False


def _render_path_list(
    paths: list[str], project_root: Path | None,
) -> list[str]:
    """Render *paths* as bullet items, relativizing under *project_root*.

    Out-of-root absolutes are kept verbatim and tagged with a
    ``# WARNING: outside project root, do not modify`` comment so the
    agent treats them as read-only references rather than write targets.
    """
    out: list[str] = []
    for p in paths:
        if project_root is None:
            out.append(f"- `{p}`")
            continue
        rendered, outside = _relativize_path(p, project_root)
        if outside:
            out.append(
                f"- `{rendered}`  # WARNING: outside project root, do not modify"
            )
        else:
            out.append(f"- `{rendered}`")
    return out


def _render_path_csv(
    paths: list[str], project_root: Path | None,
) -> str:
    """Render *paths* as a comma-separated list, relativized under root."""
    if not paths:
        return ""
    if project_root is None:
        return ", ".join(paths)
    rendered: list[str] = []
    for p in paths:
        text, outside = _relativize_path(p, project_root)
        if outside:
            rendered.append(f"{text} (outside project root)")
        else:
            rendered.append(text)
    return ", ".join(rendered)

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
                raw = path.read_text(encoding="utf-8")
                _, body = parse_frontmatter(raw)
                return body
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

    @staticmethod
    def _attachment_dedup_key(attachment: "KnowledgeAttachment") -> str:
        """Return the session-dedup key for an attachment.

        Mirrors ``KnowledgeResolver._dedup_key``: prefer source path, fall back
        to ``{pack_name}::{document_name}``.
        """
        if attachment.path:
            return attachment.path
        return f"{attachment.pack_name or ''}::{attachment.document_name}"

    def _apply_session_dedup(
        self,
        attachments: "list[KnowledgeAttachment]",
        current_step_id: str,
        delivered_knowledge: "dict[str, str]",
    ) -> "list[KnowledgeAttachment]":
        """Apply session-level dedup to a list of attachments.

        For each attachment:
        - If it is from the ``"explicit"`` source layer, leave it unchanged
          (explicit user attachments always inline regardless of prior delivery).
        - If its dedup key is in *delivered_knowledge*, downgrade delivery to
          ``"reference"`` and append a note to the grounding indicating which
          prior step delivered it inline.
        - Otherwise, if the attachment would be inlined, record the key in
          *delivered_knowledge* (mutates the dict in-place) so future steps
          can detect it.

        Returns a new list with (potentially modified) attachments.
        """
        result: list[KnowledgeAttachment] = []
        for att in attachments:
            key = self._attachment_dedup_key(att)
            if att.source == "explicit":
                # Explicit layer: never downgrade; record if inline.
                if att.delivery == "inline":
                    delivered_knowledge.setdefault(key, current_step_id)
                result.append(att)
                continue

            prior_step = delivered_knowledge.get(key)
            if prior_step is not None and att.delivery == "inline":
                # Downgrade: build a reference attachment with a retrieval note.
                note = (
                    f"(previously delivered inline in step {prior_step}"
                    f" — re-read from: {att.path})"
                )
                new_grounding = f"{att.grounding} {note}".strip() if att.grounding else note
                att = KnowledgeAttachment(
                    source=att.source,
                    pack_name=att.pack_name,
                    document_name=att.document_name,
                    path=att.path,
                    delivery="reference",
                    retrieval=att.retrieval,
                    grounding=new_grounding,
                    token_estimate=att.token_estimate,
                )
            elif att.delivery == "inline":
                # First time inlined — record it.
                delivered_knowledge.setdefault(key, current_step_id)
            result.append(att)
        return result

    @staticmethod
    def _build_prior_beads_section(prior_beads: list) -> str:
        """Render the ``## Prior Discoveries`` section from a list of beads.

        Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).

        Returns an empty string when *prior_beads* is empty so callers can
        skip the section entirely without adding blank lines.
        """
        if not prior_beads:
            return ""

        lines = [
            "## Prior Discoveries",
            "The following were discovered by prior agents in this execution.",
            "Treat as established context unless you find contrary evidence.",
            "",
        ]

        total = len(prior_beads)
        shown = min(total, 5)  # hard cap matches BeadSelector.max_beads default

        for bead in prior_beads[:shown]:
            bead_type_label = bead.bead_type.capitalize()
            files_str = ""
            if bead.affected_files:
                files_str = f"\nFiles: {', '.join(bead.affected_files)}"
            tags_str = ""
            if bead.tags:
                tags_str = f"\nTags: {', '.join(bead.tags)}"
            lines.append(
                f"### {bead_type_label} (step {bead.step_id}, "
                f"{bead.agent_name}, confidence: {bead.confidence})"
            )
            lines.append(bead.content)
            if files_str:
                lines.append(files_str.strip())
            if tags_str:
                lines.append(tags_str.strip())
            lines.append("")

        if total > shown:
            lines.append(
                f"[{total - shown} additional discovery/ies omitted — "
                f"run `baton beads list` to review]"
            )
            lines.append("")

        return "\n".join(lines).rstrip()

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
        prior_beads: "list | None" = None,
        delivered_knowledge: "dict[str, str] | None" = None,
        isolation: str | None = None,
        project_root: Path | None = None,
        prior_context_block: str = "",
        prior_step_result: "object | None" = None,
        handoff_conn: "object | None" = None,
        handoff_task_id: str = "",
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
            delivered_knowledge: Session-level dedup map (doc-key → step_id).
                Docs present in this map are downgraded from inline to reference
                delivery in the knowledge section.  The dict is mutated in-place:
                any doc that ends up inlined in THIS dispatch is added to it so
                the caller can persist the updated state.

        Returns:
            A formatted markdown delegation prompt ready to pass to the Agent tool.
        """
        role = step.agent_name
        # Use a short fallback rather than the full task_summary — task_summary is
        # rendered canonically in the Intent section below, and duplicating it in
        # the opening sentence inflates the prompt on every DISPATCH (token burn).
        project_line = project_description or "this project"

        logger.debug(
            "Building delegation prompt for step %s: agent=%s task_type=%s "
            "knowledge_attachments=%d prior_beads=%d",
            step.step_id,
            role,
            task_type or "unset",
            len(step.knowledge),
            len(prior_beads) if prior_beads else 0,
        )

        # Path relativization: when running under worktree isolation we
        # MUST NOT emit absolute project-root paths into the prompt — the
        # subagent would dutifully follow them back out of its worktree
        # and contaminate the parent branch.  Relativization is a no-op
        # when project_root is None or when paths are already relative.
        rel_root = project_root if isolation == "worktree" else None

        # Context files section
        if step.context_files:
            context_files_text = "\n".join(
                _render_path_list(step.context_files, rel_root)
            )
        else:
            context_files_text = "_No specific files specified — use your judgment._"

        # Deliverables section
        if step.deliverables:
            deliverables_text = "\n".join(f"- {d}" for d in step.deliverables)
        else:
            deliverables_text = "_See task description for expected outputs._"

        # Boundaries
        allowed = _render_path_csv(step.allowed_paths, rel_root) if step.allowed_paths else "any"
        blocked = _render_path_csv(step.blocked_paths, rel_root) if step.blocked_paths else "none"

        # Previous step handoff
        previous_output = handoff_from.strip() if handoff_from.strip() else "This is the first step."

        # Shared context block
        shared_context_block = shared_context.strip() if shared_context.strip() else "_No shared context provided._"

        # Session-level knowledge deduplication.
        # For each attachment that would be inlined, check whether it was already
        # delivered inline in a prior step.  If so, downgrade it to reference.
        # Explicit (layer-1) attachments are never downgraded.
        # After building the section, mark newly-inlined docs in the dict so the
        # caller can persist the update back to ExecutionState.
        knowledge_attachments = list(step.knowledge)
        if delivered_knowledge is not None:
            knowledge_attachments = self._apply_session_dedup(
                knowledge_attachments, step.step_id, delivered_knowledge
            )

        # Knowledge section (empty string when no attachments)
        knowledge_section = self._build_knowledge_section(knowledge_attachments)

        article = "an" if role[0:1] in "aeiouAEIOU" else "a"
        parts: list[str] = []

        # Worktree Discipline goes FIRST — before any path the agent might
        # otherwise act on naively.  Engine signals this via isolation kw.
        if isolation == "worktree":
            parts.extend([_WORKTREE_DISCIPLINE_BLOCK, ""])

        parts += [
            f"You are {article} {role} working on {project_line}.",
            "",
        ]

        # Prior Context (Wave 2.2 — ContextHarvester).
        # When the executor passes a non-empty prior_context_block we prepend
        # it before Shared Context so the agent sees its own past work in
        # this domain.  Caller is responsible for capping the block size.
        if prior_context_block.strip():
            parts += [prior_context_block.strip(), ""]

        # Handoff from Prior Step (Wave 3.2 — HandoffSynthesizer).
        # Synthesizes a compact summary of the prior step's git diff,
        # discoveries (beads created), and blockers (open warnings whose
        # files/tags overlap this step).  Persists to handoff_beads for
        # audit.  Best-effort: any failure leaves the section out.
        if prior_step_result is not None:
            try:
                from agent_baton.core.intel.handoff_synthesizer import (
                    HANDOFF_MAX_CHARS,
                    HandoffSynthesizer,
                )
                _handoff_text = HandoffSynthesizer().synthesize_for_dispatch(
                    prior_step_result,
                    step,
                    handoff_conn,
                    task_id=handoff_task_id or None,
                )
                if _handoff_text:
                    if len(_handoff_text) > HANDOFF_MAX_CHARS:
                        _handoff_text = _handoff_text[: HANDOFF_MAX_CHARS - 3].rstrip() + "..."
                    parts += [
                        "## Handoff from Prior Step",
                        _handoff_text,
                        "",
                    ]
            except Exception as _hf_exc:  # noqa: BLE001
                logger.debug(
                    "HandoffSynthesizer prepend skipped (non-fatal): %s", _hf_exc
                )

        parts += [
            "## Shared Context",
            shared_context_block,
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

        # Insert Prior Discoveries section (F3 Forward Relay).
        # Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).
        if prior_beads:
            prior_section = self._build_prior_beads_section(prior_beads)
            if prior_section:
                parts.append(prior_section)
                parts.append("")

        parts += [
            f"## Your Task (Step {step.step_id})",
            step.task_description.strip(),
            "",
        ]

        # Wave 3.1 — Expected Outcome (Demo Statement).  Anchors review on
        # behavioral correctness rather than "no errors".  Only emitted when
        # the planner derived an outcome (preserves prompt shape for plans
        # built before Wave 3.1).
        if getattr(step, "expected_outcome", "").strip():
            parts += [
                "## Expected Outcome",
                step.expected_outcome.strip(),
                "",
            ]

        # Success Criteria — inline when present
        success_criteria = _SUCCESS_CRITERIA.get(task_type, "")
        if success_criteria:
            parts += [
                f"**Success criteria:** {success_criteria}",
                "",
            ]

        # Files to Read — only when files are specified
        if step.context_files:
            parts += [
                "## Files to Read",
                context_files_text,
                "",
            ]

        # Deliverables — only when explicitly listed
        if step.deliverables:
            parts += [
                "## Deliverables",
                deliverables_text,
                "",
            ]

        # Boundaries — only when restrictions are set
        has_boundaries = step.allowed_paths or step.blocked_paths
        if has_boundaries:
            parts += [
                "## Boundaries",
                f"- Write to: {allowed}",
                f"- Do NOT write to: {blocked}",
                "",
            ]

        parts.append(_SIGNALS_BLOCK)
        parts.append("")

        # Previous Step Output — only when there is actual handoff content
        if handoff_from.strip():
            parts += [
                "## Previous Step Output",
                previous_output,
                "",
            ]

        # Decision & deviation logging — compact
        parts += [
            "Log non-obvious decisions under a **Decisions** heading. "
            "If you deviate from the plan, explain under a **Deviations** heading.",
        ]

        return "\n".join(parts)

    def build_continuation_prompt(
        self,
        step: "PlanStep",
        interaction_history: "list",
        *,
        shared_context: str = "",
        task_summary: str = "",
    ) -> str:
        """Build a continuation delegation prompt for an interactive step re-dispatch.

        Called when a step in ``interact_dispatched`` status is re-dispatched so
        the agent can respond to human input.  Includes the full interaction
        history using a sliding window: the last 3 turns in full detail, earlier
        turns condensed to one-line summaries.

        The prompt ends with instructions for the ``INTERACT_COMPLETE`` signal so
        the agent can signal when it is done without waiting for a human ``--done``.

        Args:
            step: The PlanStep being re-dispatched.
            interaction_history: List of :class:`InteractionTurn` objects
                accumulated so far.
            shared_context: Pre-built context string (plan's ``shared_context``).
            task_summary: High-level task description (shown in Intent section).

        Returns:
            A formatted markdown delegation prompt ready to pass to the Agent tool.
        """
        from agent_baton.models.execution import InteractionTurn  # avoid circular at module level

        role = step.agent_name
        project_line = task_summary or "this project"
        article = "an" if role[0:1] in "aeiouAEIOU" else "a"
        shared_context_block = shared_context.strip() or "_No shared context provided._"

        # Build the sliding-window interaction history section.
        # Last 3 turns in full; earlier turns as one-line summaries.
        _WINDOW = 3
        history_lines: list[str] = ["## Interaction History", ""]

        if interaction_history:
            older = interaction_history[:-_WINDOW] if len(interaction_history) > _WINDOW else []
            recent = interaction_history[-_WINDOW:] if len(interaction_history) > _WINDOW else interaction_history

            if older:
                history_lines.append(
                    f"_Earlier turns ({len(older)} summarised):_"
                )
                for turn in older:
                    snippet = turn.content[:100].replace("\n", " ")
                    if len(turn.content) > 100:
                        snippet += "…"
                    history_lines.append(
                        f"- Turn {turn.turn_number} [{turn.role}]: {snippet}"
                    )
                history_lines.append("")

            for turn in recent:
                label = "Agent" if turn.role == "agent" else "Human"
                history_lines.append(f"### Turn {turn.turn_number} — {label}")
                history_lines.append(turn.content)
                history_lines.append("")

        history_section = "\n".join(history_lines)

        turn_count = len(interaction_history)

        parts = [
            f"You are {article} {role} working on {project_line}.",
            "",
            "## Shared Context",
            shared_context_block,
            "",
        ]

        if task_summary.strip():
            parts += [
                "## Intent",
                task_summary.strip(),
                "",
            ]

        parts += [
            f"## Your Task (Step {step.step_id} — Continuation, Turn {turn_count + 1})",
            step.task_description.strip(),
            "",
            history_section,
            "",
            "## Instructions",
            "Continue the interaction above, responding to the latest human input.",
            "When you are done and no further turns are needed, output the following",
            "signal on its own line at the end of your response:",
            "",
            "    INTERACT_COMPLETE",
            "",
            "If you still need another round of input, do NOT output INTERACT_COMPLETE",
            "and end your response normally.",
            "",
            _SIGNALS_BLOCK,
            "",
            "Log non-obvious decisions under a **Decisions** heading. "
            "If you deviate from the plan, explain under a **Deviations** heading.",
        ]

        return "\n".join(parts)

    def build_consultation_prompt(
        self,
        step: PlanStep,
        *,
        flag_context: str = "",
        original_outcome: str = "",
        task_summary: str = "",
        prior_beads: "list | None" = None,
    ) -> str:
        """Build a lightweight consultation prompt for specialist agent dispatch.

        Targets ~3-5K tokens input.  Includes the agent role preamble, the
        structured flag context describing the obstacle, relevant excerpts from
        the blocked agent's output, precedent beads (if any), and resolution
        instructions.

        Intentionally excludes the full shared_context document, knowledge pack
        attachments, handoff chain, context_files list, path enforcement, and
        success criteria — all of which are irrelevant for a focused specialist
        consultation.

        Args:
            step: The consulting PlanStep (``step.agent_name`` is the specialist).
            flag_context: Structured description of the obstacle (e.g. from
                ``flag.to_consultation_description()``).  Optional — Layer 1
                callers may omit it when no flag context exists yet.
            original_outcome: What the blocked agent produced (last 2000 chars
                are extracted here).  Optional.
            task_summary: One-line mission context.
            prior_beads: Precedent decision beads for similar past choices.

        Returns:
            A focused markdown delegation prompt ready to pass to the Agent tool.
        """
        role = step.agent_name
        project_line = task_summary or "this project"
        article = "an" if role[0:1] in "aeiouAEIOU" else "a"

        # Truncate original_outcome to last 2000 chars to keep the prompt lean.
        outcome_excerpt = ""
        if original_outcome.strip():
            outcome_excerpt = original_outcome.strip()[-2000:]

        parts = [
            f"You are {article} {role} consulting on {project_line}.",
            "",
        ]

        if task_summary.strip():
            parts += [
                "## Mission Context",
                task_summary.strip(),
                "",
            ]

        parts += [
            f"## Your Consultation Task (Step {step.step_id})",
            step.task_description.strip(),
            "",
        ]

        if flag_context.strip():
            parts += [
                "## Obstacle / Flag",
                flag_context.strip(),
                "",
            ]

        if outcome_excerpt:
            parts += [
                "## Relevant Agent Output (excerpt)",
                outcome_excerpt,
                "",
            ]

        # Insert Prior Discoveries section when precedent beads are available.
        if prior_beads:
            prior_section = self._build_prior_beads_section(prior_beads)
            if prior_section:
                parts.append(prior_section)
                parts.append("")

        parts += [
            "## Resolution Instructions",
            "Provide a focused, actionable recommendation. Use one of these signals:",
            "- `FLAG_RESOLVED: <your recommendation>` — you have a clear answer.",
            "- `ESCALATE_TO_INTERACT: <reason>` — human judgement is needed.",
            "- `KNOWLEDGE_GAP: <description>` with `CONFIDENCE: none|low|partial`"
            " — critical information is missing.",
            "",
            "Keep your response under 500 tokens.",
        ]

        return "\n".join(parts)

    def build_task_prompt(
        self,
        step: PlanStep,
        *,
        task_summary: str = "",
    ) -> str:
        """Build a minimal prompt for task-runner agents executing bespoke skill instructions.

        Targets ~1-3K tokens input.  Passes the step's ``task_description``
        verbatim — the caller is responsible for embedding the bespoke skill
        instructions there.

        Intentionally excludes shared context, knowledge packs, beads, handoff
        chain, context files, deliverables, path enforcement, and success
        criteria.

        Args:
            step: The task PlanStep (``step.task_description`` contains bespoke
                skill instructions).
            task_summary: One-line mission context.

        Returns:
            A minimal markdown delegation prompt ready to pass to the Agent tool.
        """
        parts = [
            "You are a task runner. Follow these instructions exactly.",
            "",
        ]

        if task_summary.strip():
            parts += [
                f"**Context:** {task_summary.strip()}",
                "",
            ]

        parts += [
            f"## Task Instructions (Step {step.step_id})",
            step.task_description.strip(),
            "",
            "## Output Format",
            "Report what you did, the result, and whether it succeeded.",
            "Keep your response under 500 tokens.",
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
        prior_beads: "list | None" = None,
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

        # Insert Prior Discoveries section (F3 Forward Relay).
        # Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).
        if prior_beads:
            prior_section = self._build_prior_beads_section(prior_beads)
            if prior_section:
                parts.append(prior_section)
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
            "Coordinate with your team members on shared resources.",
            "",
            _SIGNALS_BLOCK,
            "",
            "Log non-obvious decisions under a **Decisions** heading. "
            "If you deviate from the plan, explain under a **Deviations** heading.",
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
        isolation: str = "",
        project_root: Path | None = None,
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
        logger.info(
            "Dispatching step %s to agent '%s' (model=%s, task_type=%s)",
            step.step_id,
            step.agent_name,
            step.model or "default",
            task_type or "unset",
        )

        if step.agent_name == "orchestrator":
            warnings.warn(
                "Dispatching 'orchestrator' as a subagent will fail — "
                "it requires depth-2 nesting which Claude Code does not support. "
                "Run orchestration at the top level instead.",
                stacklevel=2,
            )

        prompt = self.build_delegation_prompt(
            step,
            shared_context=shared_context,
            handoff_from=handoff_from,
            project_description=project_description,
            task_summary=task_summary,
            task_type=task_type,
            isolation=isolation or None,
            project_root=project_root,
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
            mcp_servers=step.mcp_servers,
            isolation=isolation,
        )

    # ------------------------------------------------------------------
    # Wave 5 prompt builders (bd-1483, bd-9839)
    # These bypass the standard "Worktree Discipline" block because the
    # agent is already placed in the worktree via cwd_override.
    # ------------------------------------------------------------------

    @staticmethod
    def build_self_heal_prompt(
        tier: str,
        failure_ctx: dict,
        *,
        prior_failed_patch: str = "",
    ) -> str:
        """Build a tier-specific self-heal prompt for a micro-agent (bd-1483).

        Args:
            tier: EscalationTier.value string — 'haiku-1', 'haiku-2',
                'sonnet-1', 'sonnet-2', or 'opus'.
            failure_ctx: Context dict with keys:
                gate_command (str), stderr_tail (str), diff (str),
                file_windows (dict[str, str]), bead_summaries (list[str]),
                full_file_contents (dict[str, str]), project_summary (str).
            prior_failed_patch: Diff from the previous failed attempt.
                Included on Haiku-2 and Sonnet-2 as "DO NOT REPEAT" reference.

        Returns:
            A formatted prompt ready for the self-heal micro-agent.
        """
        gate_command = failure_ctx.get("gate_command", "(unknown)")
        stderr_tail = failure_ctx.get("stderr_tail", "")
        diff = failure_ctx.get("diff", "")
        file_windows: dict = failure_ctx.get("file_windows", {})
        bead_summaries: list = failure_ctx.get("bead_summaries", [])
        full_file_contents: dict = failure_ctx.get("full_file_contents", {})
        project_summary: str = failure_ctx.get("project_summary", "")

        # Base block shared by all tiers.
        lines: list[str] = [
            f"ROLE: self-heal-{tier.split('-')[0]}",
            "",
            f"The gate '{gate_command}' failed. "
            "Apply the smallest possible patch to make it pass.",
            "",
        ]

        if diff:
            lines += [
                "DIFF (HEAD~1..HEAD):",
                diff,
                "",
            ]

        if stderr_tail:
            lines += [
                "GATE OUTPUT (last 50 lines of stderr):",
                stderr_tail,
                "",
            ]

        # Variation on attempt-2 for Haiku and Sonnet.
        if tier in ("haiku-2", "sonnet-2") and prior_failed_patch:
            lines += [
                "NOTE: Your previous attempt did not fix the gate. "
                "Try a DIFFERENT approach.",
                "",
                "DO NOT REPEAT this patch:",
                prior_failed_patch[:2000],
                "",
            ]

        # Sonnet and Opus: add file context windows.
        if tier in ("sonnet-1", "sonnet-2", "opus") and file_windows:
            for fpath, snippet in list(file_windows.items())[:10]:
                lines += [
                    f"FILE CONTEXT ({fpath}, 10-line window):",
                    snippet,
                    "",
                ]

        # Sonnet and Opus: add linked beads.
        if tier in ("sonnet-1", "sonnet-2", "opus") and bead_summaries:
            for bead in bead_summaries[:5]:
                lines.append(f"BEAD: {bead}")
            lines.append("")

        # Opus only: full file contents + project summary + framing.
        if tier == "opus":
            if full_file_contents:
                for fpath, content in list(full_file_contents.items()):
                    lines += [
                        f"FULL FILE ({fpath}):",
                        content,
                        "",
                    ]
            if project_summary:
                lines += [
                    "PROJECT SUMMARY:",
                    project_summary,
                    "",
                ]
            lines += [
                "NOTE: Two cheaper models have already failed to fix this gate. "
                "The bug is likely structural. Diagnose the ROOT CAUSE before patching.",
                "",
            ]

        lines += [
            "INSTRUCTIONS:",
            "Apply your fix as a single git commit in this worktree.",
            "Do not modify unrelated files.",
            "After committing, the gate will be re-run automatically.",
        ]

        return "\n".join(lines)

    @staticmethod
    def build_handoff_prompt(
        spec: object,
        target_step: object,
    ) -> str:
        """Build the Wave 5.3 handoff prompt for the heavy-model pickup agent.

        Args:
            spec: ``SpeculationRecord`` instance (or any object with
                ``worktree_path``, ``spec_id`` attributes).
            target_step: ``PlanStep`` whose ``task_description`` describes the work.

        Returns:
            A formatted handoff prompt ready for the Sonnet/Opus pickup agent.
        """
        from agent_baton.core.engine.speculator import SpeculativePipeliner

        worktree_path = getattr(spec, "worktree_path", "(unknown)")
        spec_id = getattr(spec, "spec_id", "(unknown)")
        step_description = getattr(target_step, "task_description", "") or str(target_step)

        # Gather git log from the worktree.
        git_log = ""
        try:
            import subprocess as _sp
            r = _sp.run(
                ["git", "log", "--oneline", "-10"],
                capture_output=True,
                text=True,
                cwd=worktree_path,
                timeout=10,
            )
            if r.returncode == 0:
                git_log = r.stdout.strip()
        except Exception:
            pass

        # Base SHA for the "start fresh" escape hatch.
        base_sha = ""
        try:
            import subprocess as _sp
            r = _sp.run(
                ["git", "rev-parse", "HEAD~1"],
                capture_output=True,
                text=True,
                cwd=worktree_path,
                timeout=10,
            )
            if r.returncode == 0:
                base_sha = r.stdout.strip()
        except Exception:
            pass

        from agent_baton.core.engine.speculator import _build_handoff_prompt
        return _build_handoff_prompt(
            next_step_description=step_description,
            git_log=git_log,
            base_sha=base_sha,
        )
