"""Type-specific resolution strategies for LearningIssues.

Each function receives a LearningIssue and a LearnedOverrides instance,
applies the appropriate correction, and returns a human-readable description
of what was done.

All resolvers are pure from the caller's perspective: they do not update
the issue status themselves — that is the responsibility of LearningEngine.
"""
from __future__ import annotations

import logging
from pathlib import Path

from agent_baton.core.learn.overrides import LearnedOverrides
from agent_baton.models.learning import LearningIssue

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Resolution functions
# ---------------------------------------------------------------------------


def resolve_routing_mismatch(
    issue: LearningIssue, overrides: LearnedOverrides
) -> str:
    """Write a FLAVOR_MAP override to correct agent routing for a stack.

    The ``target`` field is expected to be a composite key in the form
    ``"<language>/<framework>:<agent_base>"``, e.g.
    ``"python/react:backend-engineer"``.  If the format cannot be parsed,
    the override is written using the raw target as the stack key.

    Returns:
        Human-readable description of the override written.
    """
    target = issue.target
    # Parse target: "language/framework:agent_base" or "language:agent_base"
    stack_key = target
    agent_base = ""
    flavor = ""

    if ":" in target:
        stack_key, agent_part = target.split(":", 1)
        # The agent_part may have an "=reason" suffix (e.g. "=detected_language_mismatch")
        # that encodes the detection reason, not the desired flavor.  Strip it.
        agent_base = agent_part.split("=", 1)[0].strip()

    # Always prefer the evidence-based suggested_flavor — it is more reliable
    # than any encoded suffix in the target string.
    for ev in issue.evidence:
        ev_data = ev.data
        if "suggested_flavor" in ev_data:
            flavor = ev_data["suggested_flavor"]
            break
        if "detected_stack" in ev_data:
            flavor = ev_data["detected_stack"].split("/")[0]
            break

    if not agent_base or not flavor:
        _log.warning(
            "resolve_routing_mismatch: cannot parse target '%s' — skipping override",
            target,
        )
        return (
            f"Could not parse routing target '{target}'; "
            "manual override required."
        )

    overrides.add_flavor_override(stack_key, agent_base, flavor)
    return (
        f"Added flavor override: stack={stack_key!r}, "
        f"agent={agent_base!r} -> flavor={flavor!r}"
    )


def resolve_agent_degradation(
    issue: LearningIssue, overrides: LearnedOverrides
) -> str:
    """Add the degraded agent to the persistent drop list.

    The ``target`` is the agent name (base or fully-qualified).

    Returns:
        Human-readable description of the drop added.
    """
    agent_name = issue.target
    overrides.add_agent_drop(agent_name)
    return (
        f"Added '{agent_name}' to agent drop list. "
        "Future plans will exclude this agent."
    )


def resolve_knowledge_gap(
    issue: LearningIssue,
    overrides: LearnedOverrides,
    knowledge_root: Path | None = None,
) -> str:
    """Create a knowledge pack stub for the identified gap.

    The stub is created in ``.claude/knowledge/`` with a filename derived
    from the issue target.  The file contains a template prompt for the
    domain engineer to fill in.

    Returns:
        Human-readable description of the stub created, or error message.
    """
    target = issue.target
    # Sanitize target to a safe filename fragment
    safe_name = target.replace(" ", "-").replace("/", "-").replace("\\", "-")
    safe_name = "".join(c for c in safe_name if c.isalnum() or c in "-_")[:60]
    if not safe_name:
        safe_name = "gap-stub"

    pack_root = (knowledge_root or Path(".claude/knowledge")).resolve()
    pack_root.mkdir(parents=True, exist_ok=True)

    stub_path = pack_root / f"{safe_name}.md"

    # Don't overwrite an existing pack
    if stub_path.exists():
        return f"Knowledge pack stub already exists at {stub_path}."

    detail_lines = [ev.detail for ev in issue.evidence[:3] if ev.detail]
    evidence_summary = "\n".join(f"- {d}" for d in detail_lines) or "- (no detail captured)"

    stub_content = (
        f"# Knowledge Pack: {target}\n\n"
        f"**Auto-generated stub** from learning issue `{issue.issue_id}`\n\n"
        f"## Problem\n\n"
        f"{issue.title}\n\n"
        f"## Evidence\n\n"
        f"{evidence_summary}\n\n"
        f"## Domain Knowledge\n\n"
        f"> Fill in relevant context, patterns, or constraints here.\n\n"
        f"## Agent Guidance\n\n"
        f"> Specific instructions for agents handling this domain.\n"
    )
    try:
        stub_path.write_text(stub_content, encoding="utf-8")
        return f"Created knowledge pack stub at {stub_path}."
    except OSError as exc:
        return f"Failed to create knowledge pack stub: {exc}"


def resolve_gate_mismatch(
    issue: LearningIssue, overrides: LearnedOverrides
) -> str:
    """Write a gate command override correcting the language/gate mismatch.

    The ``target`` is expected as ``"<language>:<gate_type>"``, e.g.
    ``"typescript:test"``.  The correct command is read from the first
    evidence entry's ``data`` dict under the key ``"suggested_command"``.

    Returns:
        Human-readable description of the override written.
    """
    target = issue.target
    language = ""
    gate_type = ""
    command = ""

    if ":" in target:
        language, gate_type = target.split(":", 1)

    for ev in issue.evidence:
        if "suggested_command" in ev.data:
            command = ev.data["suggested_command"]
            break
        if "detected_command" in ev.data:
            command = ev.data["detected_command"]
            break

    if not language or not gate_type or not command:
        return (
            f"Could not parse gate mismatch target '{target}' or find a "
            "suggested command in evidence; manual override required."
        )

    overrides.add_gate_override(language, gate_type, command)
    return (
        f"Added gate override: language={language!r}, "
        f"gate_type={gate_type!r}, command={command!r}"
    )


def resolve_roster_bloat(
    issue: LearningIssue, overrides: LearnedOverrides
) -> str:
    """Adjust classifier scoring thresholds to reduce false positives.

    Reads the suggested ``min_keyword_overlap`` from evidence data, then
    writes it into the ``classifier_adjustments`` section of the overrides
    file.  Falls back to incrementing the current threshold by 1 if no
    suggestion is found in evidence.

    Returns:
        Human-readable description of the adjustment applied.
    """
    suggested_overlap: int | None = None
    for ev in issue.evidence:
        if "suggested_min_keyword_overlap" in ev.data:
            suggested_overlap = int(ev.data["suggested_min_keyword_overlap"])
            break

    data = overrides.load()
    adjustments: dict = data.setdefault("classifier_adjustments", {})
    current = int(adjustments.get("min_keyword_overlap", 2))

    new_value = suggested_overlap if suggested_overlap is not None else current + 1
    adjustments["min_keyword_overlap"] = new_value
    data["version"] = data.get("version", 1) + 1
    overrides.save(data)

    return (
        f"Updated classifier min_keyword_overlap: {current} -> {new_value}. "
        "Future routing will require more keyword matches."
    )
