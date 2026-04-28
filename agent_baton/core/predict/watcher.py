"""Wave 6.2 Part C — FileWatcher with privacy gate and debounce (bd-03b0).

Cross-platform FS watcher using the ``watchdog`` library.

Design decisions from wave-6-2-design.md Part C:
- Watches ``<project_root>`` recursively.
- Filters by ``.gitignore`` (via pathspec when available, else skip_dirs
  heuristic) plus configurable ``predict.watch_globs``.
- Emits ``FileEvent(path, op, ts, snapshot_hash)`` to an in-process queue.
- Debouncer collapses bursts to one event per file per 500 ms.
- Privacy gate: NEVER reads / hashes files whose RESOLVED path matches
  ``predict.never_read_globs`` (default: ``**/.env``, ``**/secrets/**``,
  ``**/*.key``, ``**/*.pem``).  Symlinks are followed before matching so a
  symlink pointing at ``.env`` is still blocked.

``watchdog>=3.0`` must be installed (``pip install -e ".[predict]"``).
When not installed the module imports cleanly but ``FileWatcher.start()``
raises ``ImportError`` with an actionable message.
"""
from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    pass

_log = logging.getLogger(__name__)

__all__ = ["FileEvent", "FileWatcher"]

# ---------------------------------------------------------------------------
# Default configuration constants
# ---------------------------------------------------------------------------

#: Patterns whose RESOLVED paths are unconditionally blocked from reads.
#: These are hardcoded and cannot be overridden by config — they are the
#: privacy guarantee.  The list is applied AFTER resolving symlinks so a
#: ``ln -s .env.prod .env.link`` is caught by the ``**/.env`` pattern.
HARDCODED_NEVER_READ_GLOBS: tuple[str, ...] = (
    "**/.env",
    "**/.env.*",
    "**/secrets/**",
    "**/*.key",
    "**/*.pem",
    "**/*.p12",
    "**/*.pfx",
    "**/*.crt",
    "**/*.cer",
)

# Burst debounce window: one event per file per N milliseconds.
_DEBOUNCE_MS: int = 500


# ---------------------------------------------------------------------------
# FileEvent dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileEvent:
    """A debounced filesystem event.

    Attributes:
        path: Absolute path of the changed file.
        op: One of ``'modified'``, ``'created'``, ``'deleted'``.
        ts: Unix timestamp of the event.
        snapshot_hash: SHA-256 hex digest of the file content at event time.
            Empty string for ``'deleted'`` events or when the privacy gate
            blocks reading.
    """

    path: Path
    op: str
    ts: float
    snapshot_hash: str


# ---------------------------------------------------------------------------
# Privacy-gate helpers
# ---------------------------------------------------------------------------


def _matches_never_read(path: Path, never_read_globs: tuple[str, ...]) -> bool:
    """Return True when *path* (resolved) matches any deny-list glob.

    Both the caller-supplied *never_read_globs* and the hardcoded
    ``HARDCODED_NEVER_READ_GLOBS`` are checked.  The path is resolved via
    ``Path.resolve()`` before matching so symlinks are followed.

    Args:
        path: Absolute or relative path to check.
        never_read_globs: Additional globs from configuration.

    Returns:
        True when the file must not be read.
    """
    # Resolve symlinks to defeat symlink-bypass attacks.
    try:
        resolved = path.resolve()
    except (OSError, ValueError):
        # Cannot resolve → treat as blocked (fail-closed).
        _log.debug(
            "FileWatcher._matches_never_read: cannot resolve %s — blocking",
            path,
        )
        return True

    resolved_str = str(resolved)
    # Also check the raw path (pre-resolution) so relative paths in globs work.
    raw_str = str(path)

    all_globs = HARDCODED_NEVER_READ_GLOBS + never_read_globs
    for pattern in all_globs:
        # fnmatch.fnmatch operates on the final path string.  We also check
        # against just the filename component for patterns like "*.key".
        if fnmatch.fnmatch(resolved_str, pattern):
            return True
        if fnmatch.fnmatch(raw_str, pattern):
            return True
        # Match against path components using PurePath-style segment matching.
        try:
            # Use glob matching relative to the drive root.
            from pathlib import PurePosixPath
            for check_path_str in (resolved_str, raw_str):
                if _glob_match(check_path_str, pattern):
                    return True
        except Exception:
            pass
    return False


def _glob_match(path_str: str, pattern: str) -> bool:
    """Return True when *path_str* matches *pattern* using ``**`` semantics.

    Uses ``fnmatch`` component-by-component for ``**`` wildcards.
    """
    # Normalize separators.
    path_str = path_str.replace("\\", "/")
    pattern = pattern.replace("\\", "/")

    # Split into components.
    path_parts = [p for p in path_str.split("/") if p]
    pat_parts = [p for p in pattern.split("/") if p]

    return _match_parts(path_parts, pat_parts)


def _match_parts(path: list[str], pattern: list[str]) -> bool:
    """Recursive ``**``-aware path component matching."""
    if not pattern:
        return not path
    if not path:
        # Remaining pattern must be all ``**`` to match empty path.
        return all(p == "**" for p in pattern)

    if pattern[0] == "**":
        # ``**`` matches zero or more path components.
        # Try consuming zero components (skip the ``**``).
        if _match_parts(path, pattern[1:]):
            return True
        # Try consuming one component and keep ``**``.
        return _match_parts(path[1:], pattern)

    # Normal segment matching.
    if fnmatch.fnmatch(path[0], pattern[0]):
        return _match_parts(path[1:], pattern[1:])
    return False


def _snapshot_hash(path: Path) -> str:
    """Return SHA-256 hex digest of the file content at *path*.

    Returns an empty string when the file cannot be read (e.g., deleted
    between event and hash time).
    """
    try:
        data = path.read_bytes()
        return hashlib.sha256(data).hexdigest()
    except (OSError, IsADirectoryError):
        return ""


# ---------------------------------------------------------------------------
# Gitignore filter
# ---------------------------------------------------------------------------


class _GitignoreFilter:
    """Best-effort .gitignore filter.

    Uses ``pathspec`` when available; falls back to a minimal built-in
    pattern set (node_modules, __pycache__, .git, dist, build, .venv).
    """

    _BUILTIN_SKIPS: frozenset[str] = frozenset({
        ".git", "__pycache__", "node_modules", ".tox",
        ".venv", "venv", "dist", "build", ".mypy_cache",
        ".pytest_cache", "site-packages", ".ruff_cache",
    })

    def __init__(self, project_root: Path) -> None:
        self._root = project_root
        self._spec: object | None = None
        self._load_pathspec()

    def _load_pathspec(self) -> None:
        try:
            import pathspec  # type: ignore[import-untyped]
            gitignore = self._root / ".gitignore"
            if gitignore.exists():
                lines = gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
                self._spec = pathspec.PathSpec.from_lines("gitwildmatch", lines)
                _log.debug("_GitignoreFilter: loaded .gitignore via pathspec")
        except ImportError:
            _log.debug("_GitignoreFilter: pathspec not installed; using builtin skip set")
        except Exception as exc:
            _log.debug("_GitignoreFilter: could not load .gitignore: %s", exc)

    def should_skip(self, path: Path) -> bool:
        """Return True when *path* is ignored by .gitignore or builtin rules."""
        # Check builtin skips on any path component.
        for part in path.parts:
            if part in self._BUILTIN_SKIPS:
                return True
        # Check pathspec.
        if self._spec is not None:
            try:
                rel = path.relative_to(self._root)
                return self._spec.match_file(str(rel))  # type: ignore[union-attr]
            except (ValueError, Exception):
                pass
        return False


# ---------------------------------------------------------------------------
# Debouncer
# ---------------------------------------------------------------------------


class _Debouncer:
    """Collapses file-event bursts to one event per file per 500 ms.

    Maintains a per-file timer; the latest event overwrites earlier ones
    within the debounce window.  On expiry the event is forwarded to the
    output queue.
    """

    def __init__(self, output: "queue.Queue[FileEvent]", window_ms: int = _DEBOUNCE_MS) -> None:
        self._out = output
        self._window_s: float = window_ms / 1000.0
        self._lock = threading.Lock()
        # path → (last_event, timer)
        self._pending: dict[Path, tuple[FileEvent, threading.Timer]] = {}

    def push(self, event: FileEvent) -> None:
        """Push an event; cancels any pending timer for the same path."""
        with self._lock:
            if event.path in self._pending:
                _old_ev, old_timer = self._pending[event.path]
                old_timer.cancel()
            timer = threading.Timer(self._window_s, self._flush, args=(event.path,))
            self._pending[event.path] = (event, timer)
            timer.daemon = True
            timer.start()

    def _flush(self, path: Path) -> None:
        with self._lock:
            entry = self._pending.pop(path, None)
        if entry is not None:
            self._out.put(entry[0])

    def cancel_all(self) -> None:
        with self._lock:
            for _ev, timer in self._pending.values():
                timer.cancel()
            self._pending.clear()


# ---------------------------------------------------------------------------
# FileWatcher
# ---------------------------------------------------------------------------


class FileWatcher:
    """Cross-platform FS watcher using ``watchdog``.

    Filters by .gitignore + configurable ``predict.watch_globs``.
    Privacy gate: never reads files matching ``predict.never_read_globs``
    (default: ``**/.env``, ``**/secrets/**``, ``**/*.key``, ``**/*.pem``).
    Debounces bursts to one event per file per 500 ms.

    Args:
        project_root: Root directory to watch recursively.
        watch_globs: Only emit events for files matching these patterns.
            Defaults to ``["**/*.py", "**/*.md"]``.
        never_read_globs: Additional globs to block reading beyond the
            hardcoded ``HARDCODED_NEVER_READ_GLOBS``.  The hardcoded list
            is always enforced regardless of this parameter.
        debounce_ms: Burst debounce window in milliseconds.  Default 500.
    """

    def __init__(
        self,
        project_root: Path,
        watch_globs: list[str] | None = None,
        never_read_globs: tuple[str, ...] | None = None,
        debounce_ms: int = _DEBOUNCE_MS,
    ) -> None:
        self._root = project_root
        self._watch_globs: list[str] = watch_globs or ["**/*.py", "**/*.md"]
        self._never_read_globs: tuple[str, ...] = never_read_globs or ()
        self._gitignore = _GitignoreFilter(project_root)
        self._event_queue: queue.Queue[FileEvent] = queue.Queue()
        self._debouncer = _Debouncer(self._event_queue, window_ms=debounce_ms)
        self._observer: object | None = None
        self._started = False

    # ── Public API ───────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the watchdog observer in a background thread.

        Raises:
            ImportError: When ``watchdog`` is not installed.
        """
        try:
            from watchdog.observers import Observer  # type: ignore[import-untyped]
            from watchdog.events import (  # type: ignore[import-untyped]
                FileSystemEventHandler,
                FileCreatedEvent,
                FileModifiedEvent,
                FileDeletedEvent,
                FileMovedEvent,
            )
        except ImportError as exc:
            raise ImportError(
                "watchdog>=3.0 is required for predictive computation. "
                "Install with: pip install -e '.[predict]'"
            ) from exc

        handler = self._make_handler(
            FileSystemEventHandler,
            FileCreatedEvent,
            FileModifiedEvent,
            FileDeletedEvent,
            FileMovedEvent,
        )

        observer = Observer()
        observer.schedule(handler, str(self._root), recursive=True)
        observer.start()
        self._observer = observer
        self._started = True
        _log.info("FileWatcher: started watching %s", self._root)

    def stop(self) -> None:
        """Stop the watchdog observer and cancel pending debounce timers."""
        self._debouncer.cancel_all()
        if self._observer is not None:
            try:
                self._observer.stop()  # type: ignore[attr-defined]
                self._observer.join(timeout=5)  # type: ignore[attr-defined]
            except Exception as exc:
                _log.debug("FileWatcher.stop: %s", exc)
            self._observer = None
        self._started = False
        _log.info("FileWatcher: stopped")

    def events(self) -> Iterator[FileEvent]:
        """Generator yielding debounced events.

        Blocks until an event is available (non-blocking peek via 100ms
        timeout internally so the generator remains interruptible).

        Honors the privacy gate: events where snapshot_hash would require
        reading a blocked file are emitted with ``snapshot_hash=""`` and
        the file content is never accessed.
        """
        while True:
            try:
                yield self._event_queue.get(timeout=0.1)
            except queue.Empty:
                continue

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _should_watch(self, path: Path) -> bool:
        """Return True when *path* should generate a watch event."""
        # Skip gitignored paths.
        if self._gitignore.should_skip(path):
            return False
        # Check watch_globs — must match at least one.
        path_str = str(path).replace("\\", "/")
        for pattern in self._watch_globs:
            if _glob_match(path_str, pattern):
                return True
        return False

    def _make_event(self, path: Path, op: str) -> FileEvent | None:
        """Construct a FileEvent for *path*.

        Returns None when the path is filtered by gitignore or watch_globs.
        Never reads files blocked by the privacy gate.
        """
        if not self._should_watch(path):
            return None

        ts = time.time()

        # Privacy gate check — use resolved path.
        blocked = _matches_never_read(path, self._never_read_globs)
        if op == "deleted" or blocked:
            return FileEvent(path=path, op=op, ts=ts, snapshot_hash="")

        snap = _snapshot_hash(path)
        return FileEvent(path=path, op=op, ts=ts, snapshot_hash=snap)

    def _make_handler(
        self,
        base_cls: type,
        created_cls: type,
        modified_cls: type,
        deleted_cls: type,
        moved_cls: type,
    ) -> object:
        """Build a watchdog event handler bound to this FileWatcher."""

        watcher = self

        class _Handler(base_cls):  # type: ignore[valid-type,misc]
            def on_created(self, event: object) -> None:
                if getattr(event, "is_directory", False):
                    return
                p = Path(getattr(event, "src_path", ""))
                ev = watcher._make_event(p, "created")
                if ev is not None:
                    watcher._debouncer.push(ev)

            def on_modified(self, event: object) -> None:
                if getattr(event, "is_directory", False):
                    return
                p = Path(getattr(event, "src_path", ""))
                ev = watcher._make_event(p, "modified")
                if ev is not None:
                    watcher._debouncer.push(ev)

            def on_deleted(self, event: object) -> None:
                if getattr(event, "is_directory", False):
                    return
                p = Path(getattr(event, "src_path", ""))
                ev = watcher._make_event(p, "deleted")
                if ev is not None:
                    watcher._debouncer.push(ev)

            def on_moved(self, event: object) -> None:
                if getattr(event, "is_directory", False):
                    return
                # Treat a move as: old path deleted, new path created.
                src = Path(getattr(event, "src_path", ""))
                dst = Path(getattr(event, "dest_path", ""))
                del_ev = watcher._make_event(src, "deleted")
                if del_ev is not None:
                    watcher._debouncer.push(del_ev)
                cre_ev = watcher._make_event(dst, "created")
                if cre_ev is not None:
                    watcher._debouncer.push(cre_ev)

        return _Handler()
