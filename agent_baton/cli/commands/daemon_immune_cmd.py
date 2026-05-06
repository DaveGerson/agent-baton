"""``baton daemon immune`` — Immune System daemon management (Wave 6.2 Part B).

Subcommands:

    baton daemon immune start [--detach] [--cap-usd N] [--tick-sec N]
        Start the immune daemon.  Runs in the foreground by default; pass
        ``--detach`` to fork into the background.

    baton daemon immune stop
        Gracefully stop a running immune daemon.

    baton daemon immune status
        Show PID, last-tick timestamp, budget remaining, and recent findings
        count.

    baton daemon immune budget [--cap-usd N]
        Display or update the daily cap.

The daemon is disabled by default (``BATON_IMMUNE_ENABLED`` must be ``1`` or
the invocation must pass ``--cap-usd`` / explicit ``start``).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import threading
from datetime import datetime, timezone
from pathlib import Path

_log = logging.getLogger(__name__)

# PID and state files live under .claude/immune/ in the project directory.
_IMMUNE_DIR = Path(".claude") / "immune"
_PID_FILE = _IMMUNE_DIR / "daemon.pid"
_STATE_FILE = _IMMUNE_DIR / "daemon_state.json"


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    p = subparsers.add_parser(
        "daemon-immune",
        help="Immune System daemon management (Wave 6.2 Part B)",
    )
    sub = p.add_subparsers(dest="immune_action")

    # ── start ────────────────────────────────────────────────────────────────
    start_p = sub.add_parser("start", help="Start the immune daemon")
    start_p.add_argument(
        "--detach", action="store_true",
        help="Fork into the background (default: run in foreground)",
    )
    start_p.add_argument(
        "--cap-usd", metavar="N", type=float, default=None, dest="cap_usd",
        help="Override daily budget cap in USD (default: 5.00)",
    )
    start_p.add_argument(
        "--tick-sec", metavar="N", type=int, default=None, dest="tick_sec",
        help="Override tick interval in seconds (default: 300)",
    )
    start_p.add_argument(
        "--project-dir", metavar="DIR", default=None, dest="project_dir",
        help="Project directory (default: current working directory)",
    )

    # ── stop ─────────────────────────────────────────────────────────────────
    stop_p = sub.add_parser("stop", help="Stop the running immune daemon")
    stop_p.add_argument(
        "--project-dir", metavar="DIR", default=None, dest="project_dir",
        help="Project directory (default: current working directory)",
    )

    # ── status ───────────────────────────────────────────────────────────────
    status_p = sub.add_parser("status", help="Show immune daemon status")
    status_p.add_argument(
        "--project-dir", metavar="DIR", default=None, dest="project_dir",
        help="Project directory (default: current working directory)",
    )

    # ── budget ───────────────────────────────────────────────────────────────
    budget_p = sub.add_parser("budget", help="Show or update daily budget cap")
    budget_p.add_argument(
        "--cap-usd", metavar="N", type=float, default=None, dest="cap_usd",
        help="Set new daily cap (USD).  Omit to display current cap.",
    )
    budget_p.add_argument(
        "--project-dir", metavar="DIR", default=None, dest="project_dir",
        help="Project directory (default: current working directory)",
    )

    return p


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def handler(args: argparse.Namespace) -> None:  # noqa: C901 (complexity OK for CLI)
    action: str = getattr(args, "immune_action", None) or ""
    project_dir = _resolve_project_dir(getattr(args, "project_dir", None))
    immune_dir = project_dir / ".claude" / "immune"

    if action == "start":
        _handle_start(args, project_dir, immune_dir)
    elif action == "stop":
        _handle_stop(immune_dir)
    elif action == "status":
        _handle_status(immune_dir)
    elif action == "budget":
        _handle_budget(args, immune_dir)
    else:
        print("Usage: baton daemon-immune {start|stop|status|budget}")
        print()
        print("  start   — launch the immune sweep daemon")
        print("  stop    — stop a running daemon")
        print("  status  — show daemon state and budget")
        print("  budget  — display or update the daily cap")
        print()
        print("Set BATON_IMMUNE_ENABLED=1 or pass 'start' to activate.")


# ---------------------------------------------------------------------------
# Sub-handlers
# ---------------------------------------------------------------------------


def _handle_start(
    args: argparse.Namespace,
    project_dir: Path,
    immune_dir: Path,
) -> None:
    """Start the immune daemon (foreground or detached)."""
    from agent_baton.core.immune.daemon import ImmuneConfig, ImmuneDaemon
    from agent_baton.core.immune.cache import ContextCache
    from agent_baton.core.immune.scheduler import SweepScheduler
    from agent_baton.core.immune.sweeper import Sweeper
    from agent_baton.core.immune.triage import FindingTriage
    from agent_baton.core.govern.budget import BudgetEnforcer

    # Build config — env vars + CLI overrides.
    config = ImmuneConfig.from_env()
    config.enabled = True  # explicit start always enables
    if getattr(args, "cap_usd", None) is not None:
        config.daily_cap_usd = args.cap_usd
    if getattr(args, "tick_sec", None) is not None:
        config.tick_interval_sec = args.tick_sec

    immune_dir.mkdir(parents=True, exist_ok=True)
    pid_file = immune_dir / "daemon.pid"
    state_file = immune_dir / "daemon_state.json"

    # Check for already-running daemon.
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            print(
                f"Immune daemon already running (PID {pid}). "
                "Use 'baton daemon-immune stop' first."
            )
            return
        except (ValueError, OSError):
            pass  # stale PID file

    detach = getattr(args, "detach", False)
    if detach:
        from agent_baton.core.runtime.daemon import daemonize
        print(f"Starting immune daemon (detached) for project: {project_dir}")
        daemonize()

    # Wire up dependencies.
    db_path = _find_baton_db(project_dir)
    conn = _open_db(db_path)
    budget = BudgetEnforcer(immune_daily_cap_usd=config.daily_cap_usd)
    scheduler = SweepScheduler(project_root=project_dir, conn=conn)
    cache = ContextCache(project_root=project_dir)

    launcher = _build_launcher()
    sweeper = Sweeper(cache=cache, launcher=launcher)

    bead_store = _build_bead_store(db_path)
    triage = FindingTriage(
        bead_store=bead_store,
        budget=budget,
        config=config,
        launcher=launcher,
    )

    daemon = ImmuneDaemon(
        config=config,
        budget=budget,
        scheduler=scheduler,
        sweeper=sweeper,
        triage=triage,
    )

    # Write PID file.
    pid_file.write_text(str(os.getpid()))

    # Persist initial state.
    _write_state(state_file, {
        "status": "running",
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "daily_cap_usd": config.daily_cap_usd,
        "tick_interval_sec": config.tick_interval_sec,
        "findings_count": 0,
        "last_tick_at": None,
    })

    # Install SIGTERM handler for graceful shutdown.
    def _on_sigterm(sig: int, frame: object) -> None:  # noqa: ARG001
        _log.info("ImmuneDaemon: received SIGTERM — shutting down")
        daemon.shutdown()

    signal.signal(signal.SIGTERM, _on_sigterm)

    # State-updater thread writes progress every 30 s.
    def _state_updater() -> None:
        import time
        while not daemon._shutdown.is_set():
            time.sleep(30)
            _write_state(state_file, {
                "status": "running",
                "daily_cap_usd": config.daily_cap_usd,
                "tick_interval_sec": config.tick_interval_sec,
                "findings_count": daemon.findings_count,
                "last_tick_at": (
                    daemon.last_tick_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                    if daemon.last_tick_at else None
                ),
                "immune_spend_usd": budget.immune_daily_spend(),
                "budget_remaining_usd": max(
                    0.0, config.daily_cap_usd - budget.immune_daily_spend()
                ),
            })

    updater = threading.Thread(target=_state_updater, daemon=True)
    updater.start()

    if not detach:
        print(
            f"Immune daemon running (PID {os.getpid()}) "
            f"cap=${config.daily_cap_usd:.2f}/day  "
            f"tick={config.tick_interval_sec}s — Ctrl-C to stop"
        )

    try:
        daemon.run()
    except KeyboardInterrupt:
        daemon.shutdown()
    finally:
        _write_state(state_file, {
            "status": "stopped",
            "findings_count": daemon.findings_count,
            "last_tick_at": (
                daemon.last_tick_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                if daemon.last_tick_at else None
            ),
        })
        if pid_file.exists():
            pid_file.unlink()
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _handle_stop(immune_dir: Path) -> None:
    """Send SIGTERM to a running immune daemon."""
    pid_file = immune_dir / "daemon.pid"
    if not pid_file.exists():
        print("No running immune daemon found.")
        return
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        print(f"Stop signal sent to immune daemon (PID {pid}).")
    except (ValueError, ProcessLookupError):
        print("Stale PID file — daemon not running.")
        pid_file.unlink(missing_ok=True)
    except PermissionError:
        print(f"Permission denied sending SIGTERM to PID {pid_file.read_text().strip()}.")


def _handle_status(immune_dir: Path) -> None:
    """Print daemon status from the persisted state file."""
    pid_file = immune_dir / "daemon.pid"
    state_file = immune_dir / "daemon_state.json"

    running = False
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            running = True
        except (ValueError, OSError):
            running = False

    print(f"Daemon:          {'running' if running else 'not running'}")
    if pid_file.exists():
        try:
            print(f"PID:             {pid_file.read_text().strip()}")
        except OSError:
            pass

    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
            print(f"Status:          {state.get('status', 'unknown')}")
            if state.get("last_tick_at"):
                print(f"Last tick:       {state['last_tick_at']}")
            if state.get("findings_count") is not None:
                print(f"Findings:        {state['findings_count']}")
            cap = state.get("daily_cap_usd")
            remaining = state.get("budget_remaining_usd")
            spent = state.get("immune_spend_usd")
            if cap is not None:
                print(f"Daily cap:       ${cap:.2f}")
            if spent is not None:
                print(f"Spent today:     ${spent:.4f}")
            if remaining is not None:
                print(f"Budget left:     ${remaining:.4f}")
        except (json.JSONDecodeError, OSError):
            print("(state file unreadable)")
    else:
        print("(no state file — daemon has never run)")


def _handle_budget(args: argparse.Namespace, immune_dir: Path) -> None:
    """Show or update the daily budget cap."""
    state_file = immune_dir / "daemon_state.json"
    cap_usd = getattr(args, "cap_usd", None)

    if cap_usd is None:
        # Read mode.
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text())
                current = state.get("daily_cap_usd", 5.00)
                print(f"Daily cap: ${current:.2f}/project/day")
                spent = state.get("immune_spend_usd", 0.0)
                print(f"Spent today: ${spent:.4f}")
                print(f"Remaining: ${max(0.0, current - spent):.4f}")
                return
            except (json.JSONDecodeError, OSError):
                pass
        print("Daily cap: $5.00/project/day (default — daemon not yet started)")
    else:
        # Write mode.
        if not state_file.exists():
            immune_dir.mkdir(parents=True, exist_ok=True)
            state: dict = {}
        else:
            try:
                state = json.loads(state_file.read_text())
            except (json.JSONDecodeError, OSError):
                state = {}
        state["daily_cap_usd"] = cap_usd
        _write_state(state_file, state)
        print(f"Daily cap updated to ${cap_usd:.2f}/project/day.")
        print("Restart the daemon for the new cap to take effect.")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_project_dir(project_dir: str | None) -> Path:
    if project_dir is not None:
        return Path(project_dir).resolve()
    return Path.cwd()


def _write_state(state_file: Path, data: dict) -> None:
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        _log.debug("daemon-immune: could not write state file: %s", exc)


def _find_baton_db(project_dir: Path) -> Path:
    """Walk up from *project_dir* looking for baton.db."""
    current = project_dir
    for _ in range(5):
        candidate = current / "baton.db"
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    # Fall back to project_dir/baton.db (will be created on first use).
    return project_dir / "baton.db"


def _open_db(db_path: Path) -> object:
    """Open (or create) the SQLite database at *db_path*."""
    import sqlite3
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _build_launcher() -> object:
    """Build a ClaudeCodeLauncher or a no-op stub if Claude Code is absent."""
    try:
        from agent_baton.core.orchestration.registry import AgentRegistry
        from agent_baton.core.runtime.claude_launcher import ClaudeCodeLauncher
        registry = AgentRegistry()
        registry.load_default_paths()
        return ClaudeCodeLauncher(registry=registry)
    except Exception as exc:
        _log.warning("daemon-immune: ClaudeCodeLauncher unavailable (%s) — using stub", exc)

        class _StubLauncher:
            def launch(self, **kwargs: object) -> None:  # noqa: ARG002
                _log.warning("StubLauncher: launch called but Claude Code is not available")
                return None

        return _StubLauncher()


def _build_bead_store(db_path: Path) -> object:
    """Build a BeadStore for *db_path*."""
    from agent_baton.core.engine.bead_store import BeadStore
    return BeadStore(db_path=db_path)
