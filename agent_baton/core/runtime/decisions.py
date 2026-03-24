"""DecisionManager — persists human decision requests to disk and publishes events."""
from __future__ import annotations

import json
import re
from pathlib import Path

from agent_baton.models.decision import DecisionRequest, DecisionResolution
from agent_baton.core.events.bus import EventBus
from agent_baton.core.events import events as evt


class DecisionManager:
    """Manage human decision requests during async execution.

    Decision requests are persisted as JSON files under the decisions directory.
    Resolution publishes events to the EventBus to unblock waiting workers.

    Each pending request also gets a companion human-readable ``.md`` file
    at the same path so operators can inspect it without JSON knowledge.
    """

    _DEFAULT_DIR = Path(".claude/team-context/decisions")

    def __init__(
        self,
        decisions_dir: Path | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self._dir = (decisions_dir or self._DEFAULT_DIR).resolve()
        self._bus = bus

    @property
    def decisions_dir(self) -> Path:
        """Absolute (or relative) path to the decisions directory."""
        return self._dir

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
