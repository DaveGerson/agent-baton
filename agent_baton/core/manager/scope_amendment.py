"""Atomic scope-amendment application (Phase 3 "Make scope contracts
authoritative", step 3.2).

``agent_baton.core.engine.executor.ExecutionEngine.resolve_scope_expansion``
is the only caller. Flow:

1. A step's actual git diff is independently verified against its scope
   contract (see ``agent_baton.core.engine.manager_scope_signal.
   derive_scope_expansion_from_diff``) and found to violate it. The step is
   forced to ``"failed"`` (never folded back) and a durable
   :class:`~agent_baton.models.manager.ManagerDecision` is filed, backed by
   evidence persisted via :func:`write_scope_evidence`.
2. A human resolves that decision. ``reject`` calls
   :func:`deny_scope_amendment` -- pure decision-log bookkeeping, no other
   state changes (the failed step and its retained worktree are left
   exactly as the violation left them: fully recoverable). ``approve``
   calls :func:`apply_scope_amendment`, which durably widens the step's
   scope-contract sidecars (JSON + Markdown, when present) and marks the
   decision resolved -- all written via :func:`_atomic_write_text` (a
   temp-file-then-``os.replace``, atomic-on-same-filesystem rename) BEFORE
   the caller is allowed to touch the authoritative in-memory
   ``PlanStep.allowed_paths`` / persist ``ExecutionState``. That ordering
   is what "atomically ... before retry" means here: if any sidecar write
   fails, the caller never mutates the plan, so the plan can never claim an
   expanded scope the sidecars/decision log don't also agree on. This is
   filesystem-level atomicity (each individual write is atomic; the
   *sequence* of writes is not a single database transaction) -- the same
   guarantee every other manager-mode sidecar writer in this codebase
   relies on (``agent_baton.core.manager.artifacts.write_all``); a true
   multi-file transaction would need a WAL/journal this codebase doesn't
   have.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_baton.core.engine.planning.scope_contract import normalize_path_list
from agent_baton.core.manager.artifacts import append_decision_log
from agent_baton.core.manager.paths import ManagerArtifactPaths

if TYPE_CHECKING:
    from agent_baton.models.manager import ManagerDecision

__all__ = [
    "ScopeAmendmentResult",
    "apply_scope_amendment",
    "deny_scope_amendment",
    "write_scope_evidence",
    "load_scope_evidence",
    "load_decision",
]


# ---------------------------------------------------------------------------
# Atomic file helpers
# ---------------------------------------------------------------------------


def _atomic_write_text(path: Path, text: str) -> None:
    """Write *text* to *path* via a same-directory temp file + ``os.replace``.

    ``os.replace`` is atomic on POSIX when source and destination are on
    the same filesystem (guaranteed here: the temp file is created as a
    sibling of *path*), so a reader never observes a partially-written
    file, and a crash mid-write leaves the original *path* untouched.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _atomic_write_json(path: Path, data: dict) -> None:
    _atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Evidence persistence
# ---------------------------------------------------------------------------


def write_scope_evidence(
    *,
    paths: ManagerArtifactPaths,
    decision_id: str,
    step_id: str,
    agent_name: str,
    violations: "list[Any]",
    real_changed_files: "list[str]",
    created_at: str = "",
) -> Path:
    """Persist the independently-computed diff evidence backing *decision_id*.

    *violations* is a list of objects duck-typed as ``ScopeExpansionSignal``
    (``.path`` / ``.reason`` attributes) -- kept loosely typed here so this
    module has no import-time dependency on
    ``agent_baton.core.engine.manager_scope_signal``.

    This is what lets :func:`load_scope_evidence` locate exactly which
    step/paths a later ``resolve_scope_expansion`` call concerns without
    re-parsing free text out of ``ManagerDecision.context`` or ``.summary``.
    """
    if not created_at:
        from agent_baton.utils.time import utcnow_zulu

        created_at = utcnow_zulu()
    data = {
        "decision_id": decision_id,
        "step_id": step_id,
        "agent_name": agent_name,
        "created_at": created_at,
        "violations": [
            {"path": getattr(v, "path", ""), "reason": getattr(v, "reason", "")}
            for v in violations
        ],
        "real_changed_files": list(real_changed_files),
    }
    path = paths.scope_evidence(decision_id)
    _atomic_write_json(path, data)
    return path


def load_scope_evidence(paths: ManagerArtifactPaths, decision_id: str) -> "dict | None":
    """Load evidence written by :func:`write_scope_evidence`, or ``None``
    when absent/unreadable (a caller must treat that as "cannot resolve
    this decision automatically", never as "no violation")."""
    path = paths.scope_evidence(decision_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_decision(paths: ManagerArtifactPaths, decision_id: str) -> "ManagerDecision | None":
    """Load the current state of *decision_id* from ``decision-log.jsonl``.

    The log is append-only (see
    ``agent_baton.core.manager.artifacts.append_decision_log``); this scans
    to the end and keeps the *last* entry matching *decision_id*, so a
    resolution appended after the original filing wins.
    """
    from agent_baton.models.manager import ManagerDecision

    path = paths.decision_log
    if not path.is_file():
        return None
    found: "dict | None" = None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if data.get("decision_id") == decision_id:
            found = data
    if found is None:
        return None
    return ManagerDecision(**found)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


@dataclass
class ScopeAmendmentResult:
    """Outcome of :func:`apply_scope_amendment`."""

    applied: bool
    step_id: str
    new_allowed_paths: "list[str]" = field(default_factory=list)
    written_paths: "list[Path]" = field(default_factory=list)
    error: str = ""


def apply_scope_amendment(
    *,
    step_id: str,
    current_allowed_paths: "list[str]",
    additional_paths: "list[str]",
    paths: ManagerArtifactPaths,
    decision: "ManagerDecision",
) -> ScopeAmendmentResult:
    """Approve *decision*: durably widen *step_id*'s scope-contract
    sidecars to include *additional_paths*, and mark *decision* resolved.

    Does **not** touch any in-memory plan object -- callers (see module
    docstring) only mutate ``PlanStep.allowed_paths`` and persist
    ``ExecutionState`` after this returns ``applied=True``, so the plan
    (the authoritative source the engine re-reads on the next dispatch
    pass) is always the last thing to change and only ever changes after
    every sidecar durably agrees.

    Sidecars that don't exist on disk (e.g. a dry-run plan with no
    persisted manager-mode artifacts, or a step no ``ScopeContractBuilder``
    ever ran for) are silently skipped rather than treated as an error --
    the plan mutation the caller performs afterward is what's authoritative
    at execution time either way.
    """
    merged = normalize_path_list(list(current_allowed_paths) + list(additional_paths))
    if not merged:
        return ScopeAmendmentResult(
            applied=False,
            step_id=step_id,
            error=(
                "scope amendment produced no usable allowed_paths -- every "
                f"entry in current={current_allowed_paths!r} + "
                f"additional={additional_paths!r} failed normalization"
            ),
        )

    staged: "list[tuple[Path, str]]" = []

    contract_json_path = paths.scope_contract(step_id, ext="json")
    if contract_json_path.is_file():
        try:
            data = json.loads(contract_json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {"step_id": step_id}
        data["allowed_paths"] = merged
        staged.append(
            (contract_json_path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        )

    contract_md_path = paths.scope_contract(step_id, ext="md")
    if contract_md_path.is_file():
        try:
            md_text = contract_md_path.read_text(encoding="utf-8")
        except OSError:
            md_text = ""
        if md_text:
            staged.append((contract_md_path, _rewrite_allowed_paths_section(md_text, merged)))

    if not decision.resolution:
        decision.resolution = "approved"
    if not decision.resolved_at:
        from agent_baton.utils.time import utcnow_zulu

        decision.resolved_at = utcnow_zulu()

    written: "list[Path]" = []
    try:
        for path, text in staged:
            _atomic_write_text(path, text)
            written.append(path)

        if decision.decision_id:
            from agent_baton.core.manager.decisions import decision_to_markdown

            decision_md_path = paths.decision(decision.decision_id)
            _atomic_write_text(decision_md_path, decision_to_markdown(decision))
            written.append(decision_md_path)

        # Append-only audit trail -- written last so a prior write failure
        # never logs a resolution that didn't actually stick.
        append_decision_log(paths, decision)
    except OSError as exc:
        return ScopeAmendmentResult(
            applied=False,
            step_id=step_id,
            error=f"sidecar write failed: {exc}",
            written_paths=written,
        )

    return ScopeAmendmentResult(
        applied=True,
        step_id=step_id,
        new_allowed_paths=merged,
        written_paths=written,
    )


def deny_scope_amendment(
    *,
    paths: ManagerArtifactPaths,
    decision: "ManagerDecision",
) -> "Path | None":
    """Reject *decision*: mark it resolved, mutate nothing else.

    Per the step 3.2 contract, denied expansion must leave recoverable
    state -- the failed ``StepResult`` and retained worktree are entirely
    the caller's concern (it never calls into this module for the "leave
    everything alone" half of a rejection); this function only records
    that a human looked at the evidence and said no.
    """
    if not decision.resolution:
        decision.resolution = "rejected"
    if not decision.resolved_at:
        from agent_baton.utils.time import utcnow_zulu

        decision.resolved_at = utcnow_zulu()
    if not decision.decision_id:
        return None

    from agent_baton.core.manager.decisions import decision_to_markdown

    path = paths.decision(decision.decision_id)
    _atomic_write_text(path, decision_to_markdown(decision))
    append_decision_log(paths, decision)
    return path


def _rewrite_allowed_paths_section(markdown: str, allowed_paths: "list[str]") -> str:
    """Replace the ``## Allowed Paths`` bullet list in *markdown* with
    *allowed_paths*, preserving every other section verbatim.

    Falls back to appending a fresh ``## Allowed Paths`` section when the
    heading isn't present (defensive -- every contract rendered by
    ``agent_baton.core.manager.context_bundles.contract_to_markdown``
    includes it, but a hand-edited or future-format file should still get
    a usable amendment rather than a silently-dropped one).
    """
    lines = markdown.splitlines()
    out: "list[str]" = []
    i = 0
    replaced = False
    while i < len(lines):
        line = lines[i]
        out.append(line)
        if line.strip() == "## Allowed Paths":
            i += 1
            while i < len(lines) and (lines[i].startswith("- ") or not lines[i].strip()):
                i += 1
            bullets = [f"- {p}" for p in allowed_paths] or ["- (none)"]
            out.extend(bullets)
            out.append("")
            replaced = True
            continue
        i += 1
    if not replaced:
        out.append("")
        out.append("## Allowed Paths")
        out.extend(f"- {p}" for p in allowed_paths)
    return "\n".join(out).rstrip("\n") + "\n"
