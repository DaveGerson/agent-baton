"""WebhookRegistry — CRUD for outbound webhook subscriptions.

Subscriptions are persisted as a JSON file (``webhooks.json``) inside the
team-context directory.  The file is re-read on every mutating operation so
that multiple processes sharing the same directory stay consistent.

Each entry shape::

    {
        "webhook_id": "abc123",
        "url": "https://example.com/hook",
        "events": ["step.*", "gate.required"],
        "secret": "optional-hmac-secret",
        "created": "2026-03-23T10:00:00+00:00",
        "enabled": true,
        "consecutive_failures": 0
    }

Topic matching uses :func:`fnmatch.fnmatch` so glob-style patterns work:
``step.*`` matches ``step.completed``, ``step.failed``, etc.
``*`` matches everything.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path


class WebhookRegistry:
    """CRUD for webhook subscriptions, persisted to ``webhooks.json``."""

    def __init__(self, webhooks_file: Path) -> None:
        self._path = webhooks_file

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _load(self) -> list[dict]:
        """Read all entries from disk.  Returns empty list if file is absent."""
        if not self._path.exists():
            return []
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, list):
                return data
            return []
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self, entries: list[dict]) -> None:
        """Write entries to disk, creating parent directories as needed."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def register(
        self,
        url: str,
        events: list[str],
        secret: str | None = None,
    ) -> dict:
        """Register a new webhook subscription.

        Args:
            url: The HTTPS endpoint to deliver events to.
            events: List of topic patterns (glob-style) to subscribe to.
            secret: Optional shared secret for HMAC-SHA256 signing.

        Returns:
            The new webhook entry dict with ``webhook_id``, ``url``,
            ``events``, ``created``, ``enabled``, and
            ``consecutive_failures``.
        """
        entries = self._load()

        entry: dict = {
            "webhook_id": uuid.uuid4().hex[:16],
            "url": url,
            "events": list(events),
            "secret": secret,
            "created": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            "enabled": True,
            "consecutive_failures": 0,
        }

        entries.append(entry)
        self._save(entries)
        return entry

    def delete(self, webhook_id: str) -> bool:
        """Remove a webhook by ID.

        Returns:
            ``True`` if the webhook was found and removed, ``False`` if it
            did not exist.
        """
        entries = self._load()
        new_entries = [e for e in entries if e.get("webhook_id") != webhook_id]

        if len(new_entries) == len(entries):
            return False

        self._save(new_entries)
        return True

    def list_all(self) -> list[dict]:
        """Return all registered webhooks (enabled and disabled)."""
        return self._load()

    def match(self, event_topic: str) -> list[dict]:
        """Return enabled webhooks whose event patterns match *event_topic*.

        Matching uses :func:`fnmatch.fnmatch` so glob-style patterns such as
        ``step.*`` and ``*`` work as expected.

        Args:
            event_topic: The fully-qualified topic string (e.g.
                ``"step.completed"``).

        Returns:
            Webhook entry dicts whose ``events`` list contains at least one
            pattern matching *event_topic* and whose ``enabled`` flag is
            ``True``.
        """
        results: list[dict] = []
        for entry in self._load():
            if not entry.get("enabled", True):
                continue
            for pattern in entry.get("events", []):
                if fnmatch(event_topic, pattern):
                    results.append(entry)
                    break
        return results

    def update(self, webhook_id: str, **fields: object) -> bool:
        """Apply arbitrary field updates to a webhook entry.

        Used internally by the dispatcher to update ``consecutive_failures``
        and ``enabled``.

        Args:
            webhook_id: The webhook to update.
            **fields: Key-value pairs to merge into the entry.

        Returns:
            ``True`` if the entry was found and updated.
        """
        entries = self._load()
        for entry in entries:
            if entry.get("webhook_id") == webhook_id:
                entry.update(fields)
                self._save(entries)
                return True
        return False
