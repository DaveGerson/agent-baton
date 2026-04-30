"""``baton viz`` -- visualize a plan and its execution state.

Renders a structured, color-coded view of the current plan and execution
progress.  Defaults to terminal output via Rich; use ``--web`` for an
interactive HTML visualization, or ``--save`` to write HTML to a file.

Delegates to:
    agent_baton.visualize.snapshot.PlanSnapshot
    agent_baton.visualize.cli_renderer.render
    agent_baton.visualize.web_renderer.render_html
"""
from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "viz",
        help="Visualize a plan and its execution state",
    )
    p.add_argument(
        "--task-id",
        default=None,
        help="Target a specific execution",
    )
    p.add_argument(
        "--plan",
        default=None,
        help="Path to plan.json (plan-only, no execution state)",
    )
    p.add_argument(
        "--web",
        action="store_true",
        default=False,
        help="Open interactive HTML visualization in browser",
    )
    p.add_argument(
        "--port",
        type=int,
        default=0,
        help="Port for --web server (default: auto-pick)",
    )
    p.add_argument(
        "--save",
        default=None,
        metavar="PATH",
        help="Write HTML to file instead of serving",
    )
    p.add_argument(
        "--compact",
        action="store_true",
        default=False,
        help="Show compact 2-line status summary",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    import json
    import os
    import sys
    from pathlib import Path

    from agent_baton.cli._context import resolve_context_root
    from agent_baton.models.execution import MachinePlan
    from agent_baton.visualize.snapshot import PlanSnapshot

    snapshot: PlanSnapshot | None = None

    if args.plan:
        plan_path = Path(args.plan)
        if not plan_path.exists():
            print(f"error: plan not found: {plan_path}", file=sys.stderr)
            sys.exit(1)
        data = json.loads(plan_path.read_text(encoding="utf-8"))
        plan = MachinePlan.from_dict(data)
        snapshot = PlanSnapshot.from_plan(plan)
    else:
        # Load from execution state
        from agent_baton.core.engine.persistence import StatePersistence
        from agent_baton.core.storage import detect_backend, get_project_storage

        context_root = resolve_context_root()
        task_id = args.task_id or os.environ.get("BATON_TASK_ID")

        if task_id is None:
            _backend = detect_backend(context_root)
            if _backend == "sqlite":
                try:
                    _storage = get_project_storage(context_root, backend="sqlite")
                    task_id = _storage.get_active_task()
                except Exception:
                    pass
            if task_id is None:
                task_id = StatePersistence.get_active_task_id(context_root)

        if task_id is None:
            plan_path = context_root / "plan.json"
            if plan_path.exists():
                data = json.loads(plan_path.read_text(encoding="utf-8"))
                plan = MachinePlan.from_dict(data)
                snapshot = PlanSnapshot.from_plan(plan)
            else:
                print("No active execution or plan found.", file=sys.stderr)
                sys.exit(1)
        else:
            sp = StatePersistence(context_root, task_id=task_id)
            state = sp.load()
            if state is None:
                print(
                    f"error: no execution state for task {task_id}",
                    file=sys.stderr,
                )
                sys.exit(1)
            snapshot = PlanSnapshot.from_state(state)

    assert snapshot is not None

    if args.save:
        from agent_baton.visualize.web_renderer import render_html

        html_str = render_html(snapshot)
        Path(args.save).write_text(html_str, encoding="utf-8")
        print(f"Saved to {args.save}")
        return

    if args.web:
        _serve_web(snapshot, args.port)
        return

    if args.compact:
        from agent_baton.visualize.compact import render_compact
        render_compact(snapshot)
        _print_viz_path()
        return

    from agent_baton.visualize.cli_renderer import render

    render(snapshot)
    _print_viz_path()


def _serve_web(snapshot: PlanSnapshot, port: int) -> None:
    """Serve the HTML visualization in a local browser."""
    import http.server
    import socket
    import webbrowser

    from agent_baton.visualize.snapshot import PlanSnapshot
    from agent_baton.visualize.web_renderer import render_html

    html_content = render_html(snapshot)
    port = port or _find_free_port()

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html_content.encode("utf-8"))

        def log_message(self, *a: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"Serving at {url}")
    print("Ctrl+C to stop.")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


def _print_viz_path() -> None:
    """Print the path to viz.html if it exists."""
    from pathlib import Path as P

    from agent_baton.cli._context import resolve_context_root

    ctx = resolve_context_root()
    viz = ctx / "viz.html"
    if viz.exists():
        try:
            from rich.console import Console
            from rich.text import Text

            c = Console(stderr=True)
            line = Text()
            line.append("  📊 ", style="dim")
            line.append("Open in browser: ", style="dim")
            line.append(f"file://{viz.resolve()}", style="underline cyan")
            c.print(line)
        except ImportError:
            import sys

            print(f"  Viz: file://{viz.resolve()}", file=sys.stderr)


def _find_free_port() -> int:
    """Find a free TCP port on localhost."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
