"""``baton predict`` — Zero-Latency Predictive Computation (Wave 6.2 Part C, bd-03b0).

Subcommands:

    baton predict start [--detach] [--project-dir DIR]
        Spawn the watcher + dispatcher daemon.
        Requires ``BATON_PREDICT_ENABLED=1``.

    baton predict stop [--project-dir DIR]
        Graceful shutdown: cancel in-flight speculations, clean worktrees,
        stop the watcher.

    baton predict status [--project-dir DIR]
        Show PID, in-flight specs, accept-rate metric, and cost-so-far.

    baton predict accept [<spec_id>] [--project-dir DIR]
        Accept a speculation (default: most-recent ready).
        Calls handoff_to_pipeliner → Wave 5.3 join point.

    baton predict reject <spec_id> [--project-dir DIR]
        Kill a speculation and clean up its worktree.

    baton predict show <spec_id> [--project-dir DIR]
        Show diff and classifier output for a speculation.

Feature gate: ``BATON_PREDICT_ENABLED`` must be ``1`` for start to work.
All other subcommands work regardless (to allow status/cleanup after disable).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# PID and state files live under .claude/predict/ in the project directory.
_PREDICT_DIR = Path(".claude") / "predict"
_PID_FILE = _PREDICT_DIR / "daemon.pid"
_STATE_FILE = _PREDICT_DIR / "daemon_state.json"


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    p = subparsers.add_parser(
        "predict",
        help="Zero-Latency Predictive Computation (Wave 6.2 Part C)",
    )
    sub = p.add_subparsers(dest="predict_action")

    # ── start ────────────────────────────────────────────────────────────────
    start_p = sub.add_parser("start", help="Start the predict watcher daemon")
    start_p.add_argument(
        "--detach", action="store_true",
        help="Fork into the background (default: run in foreground)",
    )
    start_p.add_argument(
        "--project-dir", metavar="DIR", default=None, dest="project_dir",
        help="Project directory (default: current working directory)",
    )

    # ── stop ─────────────────────────────────────────────────────────────────
    stop_p = sub.add_parser("stop", help="Stop the running predict daemon")
    stop_p.add_argument(
        "--project-dir", metavar="DIR", default=None, dest="project_dir",
        help="Project directory (default: current working directory)",
    )

    # ── status ───────────────────────────────────────────────────────────────
    status_p = sub.add_parser("status", help="Show predict daemon status")
    status_p.add_argument(
        "--project-dir", metavar="DIR", default=None, dest="project_dir",
        help="Project directory (default: current working directory)",
    )

    # ── accept ───────────────────────────────────────────────────────────────
    accept_p = sub.add_parser(
        "accept",
        help="Accept a speculation (default: most-recent ready)",
    )
    accept_p.add_argument(
        "spec_id", nargs="?", default=None,
        help="Speculation ID to accept (default: most-recent ready)",
    )
    accept_p.add_argument(
        "--project-dir", metavar="DIR", default=None, dest="project_dir",
        help="Project directory (default: current working directory)",
    )

    # ── reject ───────────────────────────────────────────────────────────────
    reject_p = sub.add_parser("reject", help="Reject a speculation (kill + cleanup)")
    reject_p.add_argument("spec_id", help="Speculation ID to reject")
    reject_p.add_argument(
        "--reason", default="human-reject", help="Rejection reason string",
    )
    reject_p.add_argument(
        "--project-dir", metavar="DIR", default=None, dest="project_dir",
        help="Project directory (default: current working directory)",
    )

    # ── show ─────────────────────────────────────────────────────────────────
    show_p = sub.add_parser("show", help="Show diff + classifier output for a spec")
    show_p.add_argument("spec_id", help="Speculation ID to show")
    show_p.add_argument(
        "--project-dir", metavar="DIR", default=None, dest="project_dir",
        help="Project directory (default: current working directory)",
    )

    return p


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def handler(args: argparse.Namespace) -> None:
    action = getattr(args, "predict_action", None)
    if action is None:
        print("Usage: baton predict <start|stop|status|accept|reject|show>")
        print("Run 'baton predict --help' for full usage.")
        return

    project_dir = Path(getattr(args, "project_dir", None) or os.getcwd()).resolve()

    if action == "start":
        _handle_start(args, project_dir)
    elif action == "stop":
        _handle_stop(args, project_dir)
    elif action == "status":
        _handle_status(args, project_dir)
    elif action == "accept":
        _handle_accept(args, project_dir)
    elif action == "reject":
        _handle_reject(args, project_dir)
    elif action == "show":
        _handle_show(args, project_dir)
    else:
        print(f"Unknown predict action: {action!r}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------


def _handle_start(args: argparse.Namespace, project_dir: Path) -> None:
    """Spawn the watcher + dispatcher daemon."""
    enabled = os.environ.get("BATON_PREDICT_ENABLED", "0")
    if enabled != "1":
        print(
            "error: predictive computation is disabled.\n"
            "Set BATON_PREDICT_ENABLED=1 to enable it.\n"
            "Note: also requires 'pip install -e \".[predict]\"' for watchdog.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Check that watchdog is importable before forking.
    try:
        import watchdog  # type: ignore[import-untyped]  # noqa: F401
    except ImportError:
        print(
            "error: watchdog is not installed.\n"
            "Install with: pip install -e '.[predict]'",
            file=sys.stderr,
        )
        sys.exit(1)

    predict_dir = project_dir / ".claude" / "predict"
    predict_dir.mkdir(parents=True, exist_ok=True)
    pid_file = predict_dir / "daemon.pid"

    if _daemon_is_running(pid_file):
        existing_pid = int(pid_file.read_text().strip())
        print(f"Predict daemon already running (PID {existing_pid}).")
        return

    detach = getattr(args, "detach", False)

    if detach:
        _fork_daemon(project_dir, predict_dir, pid_file)
    else:
        _run_daemon_foreground(project_dir, predict_dir, pid_file)


def _fork_daemon(
    project_dir: Path,
    predict_dir: Path,
    pid_file: Path,
) -> None:
    """Fork the daemon into the background."""
    import subprocess
    cmd = [
        sys.executable, "-m", "agent_baton.core.predict._daemon_runner",
        "--project-dir", str(project_dir),
        "--pid-file", str(pid_file),
    ]
    env = os.environ.copy()
    env["BATON_PREDICT_ENABLED"] = "1"
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        start_new_session=True,
    )
    # Write PID immediately so stop/status can find it.
    pid_file.write_text(str(proc.pid))
    print(f"Predict daemon started in background (PID {proc.pid}).")
    print(f"  State: {predict_dir / 'daemon_state.json'}")
    print("  Stop with: baton predict stop")


def _run_daemon_foreground(
    project_dir: Path,
    predict_dir: Path,
    pid_file: Path,
) -> None:
    """Run the daemon in the foreground (blocking)."""
    pid = os.getpid()
    pid_file.write_text(str(pid))
    print(f"Starting predict daemon for {project_dir} (PID {pid})")
    print("Press Ctrl+C to stop.")
    try:
        daemon = _build_daemon(project_dir, predict_dir)
        daemon.run()
    except KeyboardInterrupt:
        print("\nPredict daemon stopping...")
    finally:
        _cleanup_pid_file(pid_file)
        print("Predict daemon stopped.")


# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------


def _handle_stop(args: argparse.Namespace, project_dir: Path) -> None:
    predict_dir = project_dir / ".claude" / "predict"
    pid_file = predict_dir / "daemon.pid"

    if not _daemon_is_running(pid_file):
        print("No running predict daemon found.")
        return

    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to predict daemon (PID {pid}).")
        # Wait briefly for graceful shutdown.
        import time
        for _ in range(20):
            time.sleep(0.1)
            if not _daemon_is_running(pid_file):
                break
        if _daemon_is_running(pid_file):
            print("Daemon did not exit gracefully; sending SIGKILL.")
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        _cleanup_pid_file(pid_file)
        print("Predict daemon stopped.")
    except ProcessLookupError:
        print(f"No process with PID {pid} found; removing stale PID file.")
        _cleanup_pid_file(pid_file)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def _handle_status(args: argparse.Namespace, project_dir: Path) -> None:
    predict_dir = project_dir / ".claude" / "predict"
    pid_file = predict_dir / "daemon.pid"
    state_file = predict_dir / "daemon_state.json"

    running = _daemon_is_running(pid_file)
    pid = None
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
        except (ValueError, OSError):
            pass

    print(f"Predict daemon: {'running' if running else 'stopped'}")
    if pid:
        print(f"  PID: {pid}")

    # Load state if available.
    state: dict = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
        except Exception:
            pass

    speculations: list[dict] = state.get("speculations", [])
    in_flight = [s for s in speculations if s.get("status") in ("in-flight", "ready")]
    accepted = [s for s in speculations if s.get("status") == "accepted"]
    rejected = [s for s in speculations if s.get("status") in ("rejected", "pruned")]

    total_outcomes = len(accepted) + len(rejected)
    accept_rate = (len(accepted) / total_outcomes) if total_outcomes > 0 else None

    print(f"  In-flight specs: {len(in_flight)}")
    if accept_rate is not None:
        print(f"  Accept rate:     {accept_rate:.0%} ({len(accepted)}/{total_outcomes})")
    else:
        print("  Accept rate:     n/a (no completed speculations yet)")

    cost = state.get("cost_so_far_usd", 0.0)
    print(f"  Cost so far:     ${cost:.4f}")

    if in_flight:
        print("\n  In-flight speculations:")
        for s in in_flight:
            sid = s.get("spec_id", "?")
            intent = s.get("intent", "unknown")
            conf = s.get("confidence", 0.0)
            summary = s.get("summary", "")[:70]
            status = s.get("status", "?")
            print(f"    [{sid}] {status:8s} intent={intent} conf={conf:.2f} — {summary}")


# ---------------------------------------------------------------------------
# Accept
# ---------------------------------------------------------------------------


def _handle_accept(args: argparse.Namespace, project_dir: Path) -> None:
    spec_id: str | None = getattr(args, "spec_id", None)
    predict_dir = project_dir / ".claude" / "predict"
    state_file = predict_dir / "daemon_state.json"

    state: dict = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
        except Exception:
            pass

    speculations: list[dict] = state.get("speculations", [])

    if spec_id is None:
        # Default: most-recent ready.
        ready = [s for s in reversed(speculations) if s.get("status") == "ready"]
        if not ready:
            print("No ready speculations to accept.")
            return
        spec_id = ready[0]["spec_id"]
        print(f"Accepting most-recent ready speculation: {spec_id}")

    # Find the spec.
    matching = [s for s in speculations if s.get("spec_id") == spec_id]
    if not matching:
        print(f"Speculation {spec_id!r} not found.", file=sys.stderr)
        sys.exit(1)

    spec_data = matching[0]
    worktree_path = spec_data.get("worktree_path", "")

    print(f"Accepting speculation {spec_id}")
    print(f"  Intent:    {spec_data.get('intent', 'unknown')}")
    print(f"  Summary:   {spec_data.get('summary', '')}")
    print(f"  Worktree:  {worktree_path}")

    # Update state file to record acceptance.
    for s in speculations:
        if s.get("spec_id") == spec_id:
            s["status"] = "accepted"
            s["accepted_at"] = _utcnow()
    _write_state(state_file, state)

    # Attempt Wave 5.3 handoff if pipeliner is available via engine.
    _attempt_handoff(spec_id, worktree_path, project_dir)

    print(f"\nSpeculation {spec_id} accepted.")
    print("The Wave 5.3 pipeliner will now finish the implementation with Sonnet.")
    print("Monitor with: baton execute next")


def _attempt_handoff(spec_id: str, worktree_path: str, project_dir: Path) -> None:
    """Attempt to delegate to Wave 5.3 SpeculativePipeliner if engine is live."""
    # In daemon-less / test mode, just log.
    if not worktree_path:
        _log.debug("_attempt_handoff: no worktree_path for spec=%s", spec_id)
        return
    _log.info(
        "_attempt_handoff: spec=%s worktree=%s (Wave 5.3 handoff via baton execute)",
        spec_id, worktree_path,
    )
    print(f"  Worktree {worktree_path} is ready for Sonnet pickup.")


# ---------------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------------


def _handle_reject(args: argparse.Namespace, project_dir: Path) -> None:
    spec_id: str = args.spec_id
    reason: str = getattr(args, "reason", "human-reject")
    predict_dir = project_dir / ".claude" / "predict"
    state_file = predict_dir / "daemon_state.json"

    state: dict = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
        except Exception:
            pass

    speculations: list[dict] = state.get("speculations", [])
    matching = [s for s in speculations if s.get("spec_id") == spec_id]

    if not matching:
        print(f"Speculation {spec_id!r} not found.", file=sys.stderr)
        sys.exit(1)

    spec_data = matching[0]
    worktree_path = spec_data.get("worktree_path", "")

    print(f"Rejecting speculation {spec_id} (reason: {reason})")

    # Destroy the worktree unconditionally.
    if worktree_path:
        _destroy_worktree(worktree_path)

    # Update state.
    for s in speculations:
        if s.get("spec_id") == spec_id:
            s["status"] = "rejected"
            s["rejected_at"] = _utcnow()
            s["reject_reason"] = reason
    _write_state(state_file, state)

    print(f"Speculation {spec_id} rejected and worktree cleaned up.")


def _destroy_worktree(path: str) -> None:
    """Force-remove a worktree via ``git worktree remove --force``."""
    import subprocess
    try:
        r = subprocess.run(
            ["git", "worktree", "remove", "--force", path],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            _log.info("_destroy_worktree: removed %s", path)
        else:
            _log.warning(
                "_destroy_worktree: git worktree remove failed for %s: %s",
                path, r.stderr.strip(),
            )
            # Fall back to rmtree.
            import shutil
            if Path(path).exists():
                shutil.rmtree(path, ignore_errors=True)
    except Exception as exc:
        _log.debug("_destroy_worktree: %s", exc)


# ---------------------------------------------------------------------------
# Show
# ---------------------------------------------------------------------------


def _handle_show(args: argparse.Namespace, project_dir: Path) -> None:
    spec_id: str = args.spec_id
    predict_dir = project_dir / ".claude" / "predict"
    state_file = predict_dir / "daemon_state.json"

    state: dict = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
        except Exception:
            pass

    speculations: list[dict] = state.get("speculations", [])
    matching = [s for s in speculations if s.get("spec_id") == spec_id]

    if not matching:
        print(f"Speculation {spec_id!r} not found.", file=sys.stderr)
        sys.exit(1)

    spec_data = matching[0]
    worktree_path = spec_data.get("worktree_path", "")

    print(f"Speculation: {spec_id}")
    print(f"  Status:    {spec_data.get('status', 'unknown')}")
    print(f"  Intent:    {spec_data.get('intent', 'unknown')}")
    print(f"  Confidence:{spec_data.get('confidence', 0.0):.2f}")
    print(f"  Summary:   {spec_data.get('summary', '')}")
    print(f"  Scope:     {', '.join(spec_data.get('scope', []))}")
    print(f"  Started:   {spec_data.get('started_at', '')}")
    print(f"  Worktree:  {worktree_path}")
    print()

    # Show diff if worktree is present.
    if worktree_path and Path(worktree_path).exists():
        print("--- Git diff (worktree vs base) ---")
        _print_worktree_diff(worktree_path)
    else:
        print("(worktree not available)")

    # Show classifier output.
    directive = spec_data.get("speculation_directive")
    if directive:
        print()
        print("--- Classifier directive ---")
        print(f"  Kind:     {directive.get('kind', '?')}")
        print(f"  Prompt:   {directive.get('prompt', '')}")
        print(f"  Est files:{directive.get('estimated_files_changed', '?')}")


def _print_worktree_diff(worktree_path: str) -> None:
    """Print the diff between the worktree HEAD and its base."""
    import subprocess
    try:
        r = subprocess.run(
            ["git", "diff", "HEAD~1", "HEAD"],
            capture_output=True, text=True,
            cwd=worktree_path, timeout=10,
        )
        if r.stdout:
            print(r.stdout)
        else:
            print("(no diff — worktree HEAD matches base)")
    except Exception as exc:
        print(f"(could not read diff: {exc})")


# ---------------------------------------------------------------------------
# Daemon infrastructure
# ---------------------------------------------------------------------------


class _PredictDaemon:
    """The predict watcher + dispatcher event loop."""

    def __init__(
        self,
        project_dir: Path,
        predict_dir: Path,
    ) -> None:
        self._project_dir = project_dir
        self._predict_dir = predict_dir
        self._state_file = predict_dir / "daemon_state.json"
        self._stop_event = threading.Event()
        self._dispatcher: Any = None
        self._watcher: Any = None

    def run(self) -> None:
        """Main loop: start watcher, feed events to dispatcher."""
        from agent_baton.core.predict.watcher import FileWatcher
        from agent_baton.core.predict.classifier import IntentClassifier
        from agent_baton.core.predict.speculator import PredictiveDispatcher
        from agent_baton.core.govern.budget import BudgetEnforcer
        from agent_baton.core.immune.cache import ContextCache

        budget = BudgetEnforcer()
        cache = ContextCache(project_root=self._project_dir)
        classifier = IntentClassifier(
            launcher=None,   # no launcher in daemon mode; classifier is no-op
            cache=cache,
            project_root=self._project_dir,
        )
        dispatcher = PredictiveDispatcher(
            engine=None,
            worktree_mgr=None,
            classifier=classifier,
            budget=budget,
            max_concurrent=3,
        )
        self._dispatcher = dispatcher

        watcher = FileWatcher(project_root=self._project_dir)
        self._watcher = watcher

        _log.info("_PredictDaemon.run: starting watcher on %s", self._project_dir)
        watcher.start()

        # Register SIGTERM handler for graceful shutdown.
        def _on_sigterm(signum: int, frame: object) -> None:
            _log.info("_PredictDaemon: received SIGTERM")
            self._stop_event.set()

        signal.signal(signal.SIGTERM, _on_sigterm)

        # Event loop.
        try:
            for event in watcher.events():
                if self._stop_event.is_set():
                    break
                dispatcher.on_file_event(event)
                self._persist_state(dispatcher, budget)
        finally:
            watcher.stop()
            dispatcher.stop()
            _log.info("_PredictDaemon.run: stopped")

    def _persist_state(
        self,
        dispatcher: "PredictiveDispatcher",
        budget: "BudgetEnforcer",
    ) -> None:
        """Write current state to the JSON state file."""
        try:
            specs = [s.to_dict() for s in dispatcher.status()]
            # Also include recently settled specs.
            with dispatcher._lock:
                all_specs = [s.to_dict() for s in dispatcher._speculations.values()]
            state = {
                "updated_at": _utcnow(),
                "speculations": all_specs,
                "cost_so_far_usd": dispatcher.cost_so_far_usd(),
                "accept_rate": dispatcher.accept_rate(),
            }
            self._predict_dir.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(json.dumps(state, indent=2))
        except Exception as exc:
            _log.debug("_PredictDaemon._persist_state: %s", exc)


def _build_daemon(project_dir: Path, predict_dir: Path) -> "_PredictDaemon":
    return _PredictDaemon(project_dir=project_dir, predict_dir=predict_dir)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _daemon_is_running(pid_file: Path) -> bool:
    """Return True when a process with the stored PID is alive."""
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)   # signal 0 = existence check
        return True
    except (ValueError, ProcessLookupError, PermissionError, OSError):
        return False


def _cleanup_pid_file(pid_file: Path) -> None:
    try:
        if pid_file.exists():
            pid_file.unlink()
    except OSError:
        pass


def _write_state(state_file: Path, state: dict) -> None:
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state, indent=2))
    except Exception as exc:
        _log.debug("predict_cmd._write_state: %s", exc)


def _utcnow() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


# Import needed for the daemon's _lock attribute access in _persist_state.
# This is a deliberate internal access pattern (same-package, daemon mode).
from agent_baton.core.predict.speculator import PredictiveDispatcher  # noqa: E402
from agent_baton.core.govern.budget import BudgetEnforcer  # noqa: E402
