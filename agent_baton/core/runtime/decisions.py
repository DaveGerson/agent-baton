"""DecisionManager -- persists human decision requests to disk and publishes events.

During async/daemon execution, certain actions (gates, approvals) require
human input that cannot be provided interactively.  The ``DecisionManager``
bridges this gap by:

1. Writing decision requests as JSON files + human-readable ``.md`` summaries.
2. Publishing ``human_decision_needed`` events via the EventBus.
3. Polling for resolution files written by ``baton decide --resolve``.
4. Publishing ``human_decision_resolved`` events to unblock waiting workers.

This file-based protocol enables out-of-band human interaction: the daemon
writes a decision request, the operator reviews it, and resolves it from
a separate CLI session.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from agent_baton.models.decision import ContributionRequest, DecisionRequest, DecisionResolution
from agent_baton.core.events.bus import EventBus
from agent_baton.core.events import events as evt


_ID_SEP = "::"


def deterministic_decision_id(task_id: str, kind: str, *parts: object) -> str:
    """Build a stable ``DecisionRequest.request_id`` from ``(task_id, kind, parts)``.

    ``DecisionRequest.create()`` mints a random UUID fragment for every call,
    which means two callers asking about the "same" human decision (e.g. the
    daemon's ``TaskWorker`` and a headless ``baton execute run`` subprocess
    re-invoked after a crash) would otherwise create two independent pending
    requests for one logical decision.  A deterministic ID keyed on the
    task/kind/identifying-parts tuple lets every surface converge on the
    same request file, and lets :func:`parse_decision_id` recover which
    task/phase/step a resolved decision was about without needing a
    separate structured field on :class:`~agent_baton.models.decision.DecisionRequest`.

    Args:
        task_id: The execution this decision belongs to.
        kind: A short category tag -- e.g. ``"gate"``, ``"approval"``,
            ``"feedback"``, ``"interact"``.
        *parts: Additional identifying values (phase_id, step_id,
            question_id, ...) stringified and joined into the ID.

    Returns:
        A request_id of the form ``"<task_id>::<kind>::<part1>::<part2>..."``.
    """
    tail = _ID_SEP.join(str(p) for p in parts)
    if tail:
        return f"{task_id}{_ID_SEP}{kind}{_ID_SEP}{tail}"
    return f"{task_id}{_ID_SEP}{kind}"


def parse_decision_id(request_id: str) -> tuple[str, str, list[str]] | None:
    """Inverse of :func:`deterministic_decision_id`.

    Returns ``(task_id, kind, parts)`` when *request_id* follows the
    deterministic scheme, or ``None`` when it does not (e.g. a legacy
    random-UUID ID minted by ``DecisionRequest.create()``) so callers can
    fall back gracefully instead of raising.
    """
    segments = request_id.split(_ID_SEP)
    if len(segments) < 2:
        return None
    task_id, kind, *rest = segments
    if not task_id or not kind:
        return None
    return task_id, kind, rest


def apply_decision_resolution(
    *,
    team_context_root: Path,
    task_id: str,
    kind: str,
    parts: list[str],
    chosen_option: str,
    rationale: str | None = None,
    bus: EventBus | None = None,
) -> bool:
    """Apply a resolved decision's outcome directly to the execution engine.

    This is what makes a decision resolved through an out-of-band surface
    (the REST API, ``baton decide --resolve``) actually take effect when
    there is no live ``TaskWorker`` polling the same :class:`DecisionManager`
    to notice the resolution itself -- the common case for a headless
    ``baton execute run`` subprocess that already exited after recording the
    pending decision (see ``cli/commands/execution/execute.py::_run_loop``).

    Idempotent by construction: if the engine is no longer in the state the
    decision expects (for example because a live worker already applied the
    same resolution concurrently), the engine call raises and this function
    swallows it, returning ``False`` rather than surfacing an error to a
    caller who already successfully persisted the human's answer.

    Args:
        team_context_root: The project's ``.claude/team-context`` directory.
        task_id: The execution this decision belongs to.
        kind: One of ``"gate"``, ``"approval"``, ``"feedback"``,
            ``"interact"`` -- as produced by :func:`deterministic_decision_id`.
        parts: The identifying parts recovered by :func:`parse_decision_id`
            (phase_id, question_id, step_id, ...).
        chosen_option: The option the human selected.
        rationale: Optional free-text rationale/feedback/input text.
        bus: Optional shared EventBus so engine-emitted events are visible
            to any connected SSE stream.

    Returns:
        ``True`` if the resolution was applied to the engine, ``False`` if
        it was a no-op (unknown *kind*, malformed *parts*, or the engine
        rejected the call because it was no longer in the expected state).
    """
    from agent_baton.core.engine.executor import ExecutionEngine
    from agent_baton.core.storage import detect_backend, get_project_storage

    try:
        backend = detect_backend(team_context_root)
        storage = get_project_storage(team_context_root, backend=backend)
        engine = ExecutionEngine(
            team_context_root=team_context_root,
            bus=bus,
            task_id=task_id,
            storage=storage,
        )
        if kind == "gate":
            engine.record_gate_result(
                phase_id=int(parts[0]),
                passed=chosen_option in ("approve", "pass"),
                output=f"Resolved via decisions API: {chosen_option}",
            )
        elif kind == "approval":
            engine.record_approval_result(
                phase_id=int(parts[0]),
                result=chosen_option,
                feedback=rationale or "",
            )
        elif kind == "feedback":
            question_id = parts[1] if len(parts) > 1 else ""
            try:
                chosen_index = int(chosen_option)
            except (TypeError, ValueError):
                chosen_index = 0
            engine.record_feedback_result(
                phase_id=int(parts[0]),
                question_id=question_id,
                chosen_index=chosen_index,
            )
        elif kind == "interact":
            step_id = parts[0] if parts else ""
            if chosen_option == "done":
                engine.complete_interaction(step_id=step_id)
            else:
                engine.provide_interact_input(
                    step_id=step_id,
                    input_text=rationale or chosen_option,
                )
        else:
            return False
        return True
    except Exception:
        # See docstring: any engine rejection here means the decision no
        # longer needs applying (or never mapped to a real engine call),
        # not that the API call itself failed.
        return False


def resume_task_headless(*, team_context_root: Path, task_id: str) -> bool:
    """Ensure *task_id* is being actively driven forward, exactly once.

    Idempotency guard: if a worker (daemon-managed or a previously spawned
    headless runner) is already alive for *task_id* -- detected via its
    ``executions/<task_id>/worker.pid`` file -- this is a no-op; we do not
    spawn a second process that would race the first on ``next_action()``/
    ``record_step_result()``.  Otherwise it launches a fresh headless
    ``baton execute run --task-id <task_id>`` subprocess (mirroring
    ``api/routes/pmo.py::execute_card``'s launch mechanism) which resolves
    the same active/resumable execution via the standard task-id resolution
    chain and continues from exactly where it paused.

    Returns:
        ``True`` if the task is (now, or already) being driven by a worker
        process; ``False`` if a new process could not be spawned.
    """
    import os
    import subprocess
    import sys as _sys

    pid_path = team_context_root / "executions" / task_id / "worker.pid"
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
            os.kill(pid, 0)  # probe -- raises if the process is gone
            return True
        except (ValueError, OSError):
            pass  # stale PID file -- fall through and relaunch headless.

    project_root = team_context_root.parent.parent
    cmd = [
        _sys.executable, "-m", "agent_baton", "execute", "run",
        "--task-id", task_id,
    ]
    try:
        subprocess.Popen(
            cmd,
            cwd=str(project_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return False
    return True


class DecisionManager:
    """Manage human decision requests during async execution.

    Decision requests are persisted as JSON files under the decisions directory.
    Resolution publishes events to the EventBus to unblock waiting workers.

    Each pending request also gets a companion human-readable ``.md`` file
    at the same path so operators can inspect it without JSON knowledge.

    Attributes:
        _dir: Absolute path to the decisions directory where request and
            resolution JSON files are stored.
        _bus: Optional EventBus for publishing decision-related domain events.
    """

    _DEFAULT_DIR = Path(".claude/team-context/decisions")

    def __init__(
        self,
        decisions_dir: Path | None = None,
        bus: EventBus | None = None,
        safe_read_root: Path | None = None,
    ) -> None:
        self._dir = (decisions_dir or self._DEFAULT_DIR).resolve()
        self._bus = bus
        self._safe_read_root = safe_read_root.resolve() if safe_read_root else None

    @property
    def decisions_dir(self) -> Path:
        """Absolute (or relative) path to the decisions directory."""
        return self._dir

    @property
    def safe_read_root(self) -> Path | None:
        """Absolute root directory inside which context_files reads are permitted.

        ``None`` means the manager is in defensive mode and *no* file reads
        through the API are allowed (secure-by-default for callers that did
        not opt into a constrained root).
        """
        return self._safe_read_root

    # ── Public API ───────────────────────────────────────────────────────────

    def request(self, req: DecisionRequest) -> Path:
        """Persist a decision request and notify via event bus.

        Returns the path of the written JSON file.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._write_request(req)
        self._write_summary(req)
        if self._bus is not None:
            self._bus.publish(
                evt.human_decision_needed(
                    task_id=req.task_id,
                    request_id=req.request_id,
                    decision_type=req.decision_type,
                    summary=req.summary,
                    options=req.options,
                    context_files=req.context_files,
                )
            )
        return path

    def resolve(
        self,
        request_id: str,
        chosen_option: str,
        rationale: str | None = None,
        resolved_by: str = "human",
    ) -> bool:
        """Resolve a pending decision.

        Returns ``True`` if the request was found and successfully resolved,
        ``False`` if the request does not exist or is not in pending state.
        """
        req = self.get(request_id)
        if req is None or req.status != "pending":
            return False

        resolution = DecisionResolution(
            request_id=request_id,
            chosen_option=chosen_option,
            rationale=rationale,
            resolved_by=resolved_by,
        )

        # Update and persist the request with new status.
        req.status = "resolved"
        self._write_request(req)

        # Write the resolution file.
        res_path = self._resolution_path(request_id)
        res_path.write_text(
            json.dumps(resolution.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        if self._bus is not None:
            self._bus.publish(
                evt.human_decision_resolved(
                    task_id=req.task_id,
                    request_id=request_id,
                    chosen_option=chosen_option,
                    rationale=rationale or "",
                    resolved_by=resolved_by,
                )
            )

        return True

    def get(self, request_id: str) -> DecisionRequest | None:
        """Return a decision request by ID, or ``None`` if not found."""
        path = self._request_path(request_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return DecisionRequest.from_dict(data)
        except (json.JSONDecodeError, OSError):
            return None

    def pending(self) -> list[DecisionRequest]:
        """Return all decision requests with status ``"pending"``."""
        return self._list_by_status("pending")

    def list_all(self) -> list[DecisionRequest]:
        """Return all decision requests regardless of status, sorted by filename."""
        if not self._dir.is_dir():
            return []
        requests: list[DecisionRequest] = []
        for path in sorted(self._dir.glob("*.json")):
            if path.stem.endswith("-resolution"):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                requests.append(DecisionRequest.from_dict(data))
            except (json.JSONDecodeError, OSError):
                continue
        return requests

    # ── Contribution API ─────────────────────────────────────────────────────

    def request_contribution(self, req: ContributionRequest) -> Path:
        """Persist a contribution request and publish an event.

        Returns the path to the written JSON file.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{self._safe_id(req.request_id)}.json"
        path.write_text(
            json.dumps(req.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # Write human-readable summary.
        summary_path = self._dir / f"{self._safe_id(req.request_id)}.md"
        lines = [
            f"# Contribution Request: {req.topic}",
            "",
            f"**Request ID:** {req.request_id}",
            f"**Task:** {req.task_id}",
            f"**Status:** {req.status}",
            "",
            "## Description",
            req.description or "(no description)",
            "",
            "## Expected Contributors",
        ]
        for c in req.contributors:
            status = "responded" if c in req.responses else "pending"
            lines.append(f"- {c}: {status}")
        lines.extend([
            "",
            f"**Facilitator:** {req.facilitator_agent}",
            "",
            "## Respond",
            "```bash",
            f"baton decide --contribute {req.request_id} --contributor NAME --response 'Your input'",
            "```",
        ])
        summary_path.write_text("\n".join(lines), encoding="utf-8")

        if self._bus is not None:
            from agent_baton.models.events import Event

            self._bus.publish(Event.create(
                topic="contribution.requested",
                task_id=req.task_id,
                payload={
                    "request_id": req.request_id,
                    "topic": req.topic,
                    "contributors": req.contributors,
                },
            ))

        return path

    def contribute(self, request_id: str, contributor: str, response: str) -> bool:
        """Record a contributor's response. Returns True if all responses collected."""
        path = self._dir / f"{self._safe_id(request_id)}.json"
        if not path.exists():
            raise FileNotFoundError(f"Contribution request {request_id} not found")

        data = json.loads(path.read_text(encoding="utf-8"))
        req = ContributionRequest.from_dict(data)
        req.respond(contributor, response)

        path.write_text(
            json.dumps(req.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        if self._bus is not None and req.is_complete:
            from agent_baton.models.events import Event

            self._bus.publish(Event.create(
                topic="contribution.ready",
                task_id=req.task_id,
                payload={
                    "request_id": req.request_id,
                    "topic": req.topic,
                    "responses": req.responses,
                },
            ))

        return req.is_complete

    def get_contribution(self, request_id: str) -> ContributionRequest | None:
        """Load a contribution request by ID."""
        path = self._dir / f"{self._safe_id(request_id)}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ContributionRequest.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def pending_contributions(self) -> list[ContributionRequest]:
        """Return all contribution requests with status 'collecting'."""
        results: list[ContributionRequest] = []
        if not self._dir.is_dir():
            return results
        for path in sorted(self._dir.glob("*.json")):
            if path.stem.endswith("-resolution"):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("status") == "collecting" and "contributors" in data:
                    results.append(ContributionRequest.from_dict(data))
            except (json.JSONDecodeError, KeyError, OSError):
                continue
        return results

    # ── Private helpers ──────────────────────────────────────────────────────

    def _list_by_status(self, status: str) -> list[DecisionRequest]:
        return [r for r in self.list_all() if r.status == status]

    def _write_request(self, req: DecisionRequest) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._request_path(req.request_id)
        path.write_text(
            json.dumps(req.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return path

    def _write_summary(self, req: DecisionRequest) -> Path:
        """Write a human-readable Markdown summary of the decision request."""
        md_path = self._dir / f"{self._safe_id(req.request_id)}.md"
        lines = [
            f"# Decision Required: {req.decision_type}",
            "",
            f"**Request ID**: `{req.request_id}`",
            f"**Task**: `{req.task_id}`",
            f"**Created**: {req.created_at}",
            "",
            "## Summary",
            "",
            req.summary,
            "",
            "## Options",
            "",
        ]
        for opt in req.options:
            lines.append(f"- `{opt}`")
        if req.context_files:
            lines += ["", "## Context Files", ""]
            for cf in req.context_files:
                lines.append(f"- {cf}")
        lines += [
            "",
            "## How to Resolve",
            "",
            "```bash",
            f"baton decide --resolve {req.request_id} --option <OPTION>",
            "```",
        ]
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return md_path

    def _request_path(self, request_id: str) -> Path:
        return self._dir / f"{self._safe_id(request_id)}.json"

    def _resolution_path(self, request_id: str) -> Path:
        return self._dir / f"{self._safe_id(request_id)}-resolution.json"

    def get_resolution(self, request_id: str) -> dict | None:
        """Return the resolution data for a resolved request, or ``None``."""
        path = self._resolution_path(request_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _safe_id(request_id: str) -> str:
        """Sanitise a request_id so it is safe to use as a filename stem."""
        return re.sub(r"[^a-zA-Z0-9_.-]", "-", request_id)
