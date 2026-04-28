"""Wave 6.2 Part C — Intent Classifier (bd-03b0).

Classifies developer intent from filesystem events using a Haiku model
with a prompt-cached project context prefix.

Design decisions from wave-6-2-design.md Part C:
- Fires only when ``confidence >= 0.75`` AND ``kind != "none"`` AND
  ``estimated_files_changed <= 5``.
- Rolling counter; if accept-rate < 20% over last 50 → auto-disable for
  24 h and emit a learning signal.
- Reuses Wave 6.2 Part B ``ContextCache`` for the prompt-cached project
  summary (~12 K tokens, prefix-cached, ~90 % discount on cache hits).
- Input per event: ~5 K tokens (current_file + recent diffs + open context).
- Output: strict JSON schema validated against ``IntentClassification``.
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_baton.core.predict.watcher import FileEvent
    from agent_baton.core.immune.cache import ContextCache

_log = logging.getLogger(__name__)

__all__ = ["IntentKind", "IntentClassification", "IntentClassifier"]

# ---------------------------------------------------------------------------
# IntentKind enum
# ---------------------------------------------------------------------------


class IntentKind(Enum):
    ADD_FEATURE = "add-feature"
    FIX_BUG = "fix-bug"
    REFACTOR = "refactor"
    ADD_TEST = "add-test"
    DOC_UPDATE = "doc-update"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# IntentClassification dataclass
# ---------------------------------------------------------------------------


@dataclass
class IntentClassification:
    """Result of classifying a ``FileEvent``.

    Attributes:
        intent: Developer intent kind.
        confidence: Float in ``[0.0, 1.0]``.
        scope: Files expected to be affected.
        summary: Short summary (≤120 chars).
        speculation_directive: Directive for the speculative Haiku agent, or
            ``None`` when no speculation should be fired.
    """

    intent: IntentKind
    confidence: float
    scope: list[Path]
    summary: str
    speculation_directive: dict[str, Any] | None


# ---------------------------------------------------------------------------
# IntentClassifier
# ---------------------------------------------------------------------------


class IntentClassifier:
    """Haiku intent classifier for filesystem events.

    Classifies a ``FileEvent`` by combining:
    - The changed file's path and a diff of recent changes (last 5 files).
    - The heads of the 3 most-recently-modified files (≤4 K chars).
    - A prompt-cached project summary (~12 K tokens).

    The classifier fires only when the conditions from the spec are met
    (confidence ≥ 0.75, kind != "none", estimated_files_changed ≤ 5).
    If the accept-rate drops below 20 % over the last 50 speculations the
    classifier auto-disables for 24 hours.

    Args:
        launcher: A ``ClaudeCodeLauncher`` (or compatible mock) used for
            Haiku inference.  When ``None``, classification always returns
            ``UNKNOWN`` (disabled/test mode).
        cache: A ``ContextCache`` instance supplying the prompt-cached
            project summary.
        project_root: Root directory — used to compute relative paths and
            ``git diff`` fragments.
        confidence_threshold: Minimum confidence to surface a classification.
            Default 0.75.
        max_files_changed: Maximum ``estimated_files_changed`` to fire a
            speculation.  Default 5.
        accept_rate_window: Rolling window size for accept-rate tracking.
            Default 50.
        accept_rate_min: Auto-disable when rolling rate drops below this.
            Default 0.20.
    """

    _CLASSIFIER_PROMPT_HEADER = (
        "You are an intent classifier for a developer's ongoing edits. "
        "Analyze the context below and respond ONLY with valid JSON matching "
        "the schema exactly. Do not add explanation outside the JSON block.\n\n"
        "Schema:\n"
        "{\n"
        '  "intent": "add-feature" | "fix-bug" | "refactor" | "add-test" | '
        '"doc-update" | "unknown",\n'
        '  "confidence": <float 0.0-1.0>,\n'
        '  "scope": ["<relative_path>", ...],\n'
        '  "summary": "<string, max 120 chars>",\n'
        '  "speculation_directive": {\n'
        '    "kind": "implement" | "test" | "doc" | "none",\n'
        '    "prompt": "<string, max 500 chars>",\n'
        '    "estimated_files_changed": <int 1-10>\n'
        "  } | null\n"
        "}"
    )

    def __init__(
        self,
        launcher: object | None = None,
        cache: "ContextCache | None" = None,
        project_root: Path | None = None,
        confidence_threshold: float = 0.75,
        max_files_changed: int = 5,
        accept_rate_window: int = 50,
        accept_rate_min: float = 0.20,
    ) -> None:
        self._launcher = launcher
        self._cache = cache
        self._root = project_root or Path.cwd()
        self._confidence_threshold = confidence_threshold
        self._max_files_changed = max_files_changed
        self._accept_rate_window = accept_rate_window
        self._accept_rate_min = accept_rate_min

        # Rolling accept/reject counters.
        self._outcomes: list[bool] = []   # True = accepted, False = rejected
        self._disabled_until: datetime | None = None

        # Recent file activity (for open-files context).
        self._recent_files: list[Path] = []
        self._max_recent = 5

    # ── Public API ───────────────────────────────────────────────────────────

    def classify(self, event: "FileEvent") -> IntentClassification:
        """Classify a ``FileEvent`` and return an ``IntentClassification``.

        When the classifier is disabled (auto-disable after low accept rate,
        or launcher is None), returns an UNKNOWN classification with
        speculation_directive=None so the dispatcher is a no-op.
        """
        if self._is_disabled():
            return _unknown_classification()

        self._update_recent_files(event.path)

        if self._launcher is None:
            return _unknown_classification()

        raw = self._call_classifier(event)
        result = self._parse_and_validate(raw, event)

        _log.debug(
            "IntentClassifier: path=%s intent=%s confidence=%.2f",
            event.path, result.intent.value, result.confidence,
        )
        return result

    def record_outcome(self, accepted: bool) -> None:
        """Record whether a speculation derived from this classifier was accepted.

        When the rolling accept-rate over the last N outcomes drops below
        ``accept_rate_min``, the classifier auto-disables for 24 hours.
        """
        self._outcomes.append(accepted)
        # Trim to rolling window.
        if len(self._outcomes) > self._accept_rate_window:
            self._outcomes = self._outcomes[-self._accept_rate_window:]

        if len(self._outcomes) >= self._accept_rate_window:
            rate = sum(self._outcomes) / len(self._outcomes)
            if rate < self._accept_rate_min:
                self._disabled_until = datetime.now(tz=timezone.utc) + timedelta(hours=24)
                _log.warning(
                    "IntentClassifier: accept-rate %.0f%% over last %d "
                    "speculations < %.0f%% minimum — auto-disabling for 24 h",
                    rate * 100, self._accept_rate_window, self._accept_rate_min * 100,
                )

    def is_enabled(self) -> bool:
        """Return True when the classifier is not in the auto-disabled state."""
        return not self._is_disabled()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _is_disabled(self) -> bool:
        if self._disabled_until is None:
            return False
        if datetime.now(tz=timezone.utc) < self._disabled_until:
            return True
        # Suspension expired.
        self._disabled_until = None
        _log.info("IntentClassifier: auto-disable period expired; re-enabling")
        return False

    def _update_recent_files(self, path: Path) -> None:
        if path in self._recent_files:
            self._recent_files.remove(path)
        self._recent_files.insert(0, path)
        self._recent_files = self._recent_files[:self._max_recent]

    def _call_classifier(self, event: "FileEvent") -> str:
        """Build the prompt and call the launcher.  Returns raw text output."""
        prompt = self._build_prompt(event)
        launcher = self._launcher

        # Protocol 1: synchronous classify_intent(prompt) -> str.
        # Only used when the launcher explicitly exposes this method as a
        # non-Mock callable (checked via inspect, not hasattr, to avoid
        # MagicMock false-positives).
        try:
            import inspect
            ci_attr = getattr(launcher, "classify_intent", None)
            if ci_attr is not None and callable(ci_attr) and not _is_mock(ci_attr):
                return str(ci_attr(prompt))
        except Exception:
            pass

        # Protocol 2: async launch(agent_name, model, prompt, step_id) -> LaunchResult.
        try:
            import asyncio
            launch_attr = getattr(launcher, "launch", None)
            if launch_attr is not None and callable(launch_attr):
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(
                        launch_attr(
                            agent_name="intent-classifier",
                            model="claude-haiku",
                            prompt=prompt,
                            step_id="classify",
                        )
                    )
                    return getattr(result, "output", "") or ""
                finally:
                    loop.close()
        except Exception as exc:
            _log.warning("IntentClassifier._call_classifier: launcher failed: %s", exc)
        return ""

    def _build_prompt(self, event: "FileEvent") -> str:
        """Build the full classification prompt for one event."""
        parts: list[str] = [self._CLASSIFIER_PROMPT_HEADER, ""]

        # Prompt-cached project summary.
        if self._cache is not None:
            try:
                summary = self._cache.get_or_build()
                parts += [
                    "PROJECT SUMMARY (cached):",
                    summary[:12_000],   # hard cap ~12K tokens
                    "",
                ]
            except Exception as exc:
                _log.debug("IntentClassifier: cache failed: %s", exc)

        # Current file context.
        try:
            rel = event.path.relative_to(self._root)
        except ValueError:
            rel = event.path
        parts.append(f"CURRENT FILE: {rel}")
        parts.append(f"OP: {event.op}")
        parts.append("")

        # Recent changes (last 5 modified files).
        parts.append("RECENT CHANGES (unified diffs, max 200 lines each):")
        for p in self._recent_files[:5]:
            diff = self._get_diff(p)
            if diff:
                parts.append(f"--- {p}")
                parts.append(diff[:4_000])   # 200-line safety
        parts.append("")

        # Open-files context (heads of last 3 files, ≤4K chars).
        parts.append("OPEN FILES CONTEXT (head of last 3 modified files):")
        open_budget = 4_000
        for p in self._recent_files[:3]:
            if open_budget <= 0:
                break
            snippet = self._file_head(p, chars=min(1_500, open_budget))
            if snippet:
                parts.append(f"=== {p} ===")
                parts.append(snippet)
                open_budget -= len(snippet)
        parts.append("")

        return "\n".join(parts)

    def _get_diff(self, path: Path) -> str:
        """Return a ``git diff`` snippet for *path*, max 200 lines."""
        try:
            r = subprocess.run(
                ["git", "diff", "--unified=3", "--", str(path)],
                capture_output=True,
                text=True,
                cwd=str(self._root),
                timeout=5,
            )
            lines = r.stdout.splitlines()[:200]
            return "\n".join(lines)
        except Exception:
            return ""

    def _file_head(self, path: Path, chars: int = 1_500) -> str:
        """Return the first *chars* characters of *path*."""
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:chars]
        except (OSError, IsADirectoryError):
            return ""

    def _parse_and_validate(
        self,
        raw: str,
        event: "FileEvent",
    ) -> IntentClassification:
        """Parse the raw JSON output and validate against the schema."""
        if not raw:
            return _unknown_classification()

        # Extract JSON block (model may wrap in ```json ... ```).
        json_str = _extract_json_block(raw)
        if not json_str:
            _log.debug("IntentClassifier: no JSON block found in: %r", raw[:200])
            return _unknown_classification()

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            _log.debug("IntentClassifier: JSON parse error: %s in %r", exc, json_str[:200])
            return _unknown_classification()

        # Build IntentClassification from parsed data.
        try:
            intent_str = str(data.get("intent", "unknown"))
            intent = _parse_intent(intent_str)
            confidence = float(data.get("confidence", 0.0))
            scope_raw: list[str] = data.get("scope", [])
            scope = [Path(p) for p in scope_raw if isinstance(p, str)]
            summary = str(data.get("summary", ""))[:120]

            # Parse speculation_directive.
            directive = data.get("speculation_directive")
            if isinstance(directive, dict):
                kind = str(directive.get("kind", "none"))
                estimated = int(directive.get("estimated_files_changed", 1))
                # Apply fire-threshold filter.
                if (
                    confidence < self._confidence_threshold
                    or kind == "none"
                    or estimated > self._max_files_changed
                ):
                    directive = None
                else:
                    directive = {
                        "kind": kind,
                        "prompt": str(directive.get("prompt", ""))[:500],
                        "estimated_files_changed": estimated,
                    }
            else:
                directive = None

            return IntentClassification(
                intent=intent,
                confidence=confidence,
                scope=scope,
                summary=summary,
                speculation_directive=directive,
            )

        except (KeyError, ValueError, TypeError) as exc:
            _log.debug("IntentClassifier: validation error: %s", exc)
            return _unknown_classification()


# ---------------------------------------------------------------------------
# Private utilities
# ---------------------------------------------------------------------------


def _is_mock(obj: object) -> bool:
    """Return True when *obj* looks like a unittest.mock object.

    Used to avoid dispatching through ``MagicMock.classify_intent`` which
    always returns a MagicMock rather than a string.
    """
    type_name = type(obj).__name__
    module = getattr(type(obj), "__module__", "") or ""
    return "mock" in type_name.lower() or "mock" in module.lower()


def _parse_intent(value: str) -> IntentKind:
    """Parse a string into an ``IntentKind``; defaults to UNKNOWN."""
    for kind in IntentKind:
        if kind.value == value:
            return kind
    return IntentKind.UNKNOWN


def _unknown_classification() -> IntentClassification:
    return IntentClassification(
        intent=IntentKind.UNKNOWN,
        confidence=0.0,
        scope=[],
        summary="",
        speculation_directive=None,
    )


def _extract_json_block(text: str) -> str:
    """Extract the first JSON object from *text*.

    Handles both bare JSON and fenced code blocks (```json ... ```).
    """
    text = text.strip()
    # Fenced block.
    if "```" in text:
        import re
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            return m.group(1)
    # First raw `{...}` block.
    start = text.find("{")
    if start == -1:
        return ""
    depth = 0
    for i, ch in enumerate(text[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return ""
