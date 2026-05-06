"""``baton quickstart`` -- one-command onboarding for a new project.

Detects the surrounding repo, initialises ``.claude/team-context/`` with a
fresh SQLite store, drops a CLAUDE.md skeleton if none exists, checks for
bundled agents, and generates a tiny throwaway plan so the developer has
something to look at within ~30 seconds of installing agent-baton.

The command is fully idempotent: running it twice on an already-set-up
project reports "already initialised" for each step and exits 0.

Delegates to:
    agent_baton.core.storage.connection.ConnectionManager
    agent_baton.core.storage.schema.{PROJECT_SCHEMA_DDL, SCHEMA_VERSION}
    agent_baton.core.engine.planner.IntelligentPlanner
    agent_baton.core.orchestration.registry.AgentRegistry
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Stack detection
# ---------------------------------------------------------------------------

_STACK_MARKERS: list[tuple[str, tuple[str, ...]]] = [
    # Most-specific first; first hit wins.
    ("python", ("pyproject.toml", "setup.py", "requirements.txt")),
    ("node", ("package.json",)),
    ("rust", ("Cargo.toml",)),
]


def _find_repo_root(start: Path) -> Path | None:
    """Walk up from *start* to the first directory containing ``.git/``.

    Returns ``None`` when no git repo is found before reaching the
    filesystem root. The starting directory itself is considered.
    """
    cur = start.resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _detect_stack(repo_root: Path) -> str | None:
    """Return the most-specific stack tag matched by markers in *repo_root*.

    Returns ``None`` when no markers are found. Picks the first matching
    entry in :data:`_STACK_MARKERS` so the order encodes specificity.
    """
    for stack, markers in _STACK_MARKERS:
        for marker in markers:
            if (repo_root / marker).exists():
                return stack
    return None


# ---------------------------------------------------------------------------
# CLAUDE.md skeleton
# ---------------------------------------------------------------------------

_FALLBACK_CLAUDE_MD = """\
# Project Orchestration Rules

This project uses **agent-baton** for multi-agent orchestration.

## Quick Reference

- Agent definitions live in ``.claude/agents/``.
- Reference procedures live in ``.claude/references/``.
- Execution state and SQLite store live in ``.claude/team-context/``.

## Common Commands

```bash
baton plan "describe a task" --save --explain   # generate a plan
baton execute start                              # drive the plan loop
baton agents                                     # list installed agents
baton beads list                                 # browse agent memory
```

Run any command with ``--help`` for details.

## When to Orchestrate

For complex tasks crossing 3+ files or multiple domains, use ``baton plan``
to route work to specialist agents with risk gates and tracing.

For one-off bug fixes or single-file changes, work directly.
"""


def _resolve_template_claude_md() -> str:
    """Return the contents of the bundled ``templates/CLAUDE.md`` skeleton.

    Resolution order:
    1. ``templates/CLAUDE.md`` relative to cwd (development checkout).
    2. ``agent_baton/templates/CLAUDE.md`` shipped with the installed package.
    3. The hard-coded :data:`_FALLBACK_CLAUDE_MD` string.
    """
    candidate = Path("templates/CLAUDE.md").resolve()
    if candidate.exists():
        try:
            return candidate.read_text(encoding="utf-8")
        except OSError:
            pass

    try:
        import importlib.resources as pkg_resources

        pkg_files = pkg_resources.files("agent_baton")
        bundled = pkg_files / "templates" / "CLAUDE.md"
        bundled_path = Path(str(bundled))
        if bundled_path.exists():
            return bundled_path.read_text(encoding="utf-8")
    except Exception:
        pass

    return _FALLBACK_CLAUDE_MD


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------


def _init_team_context(repo_root: Path) -> tuple[Path, bool, int]:
    """Initialise ``.claude/team-context/baton.db``.

    Returns a tuple ``(ctx_dir, created, schema_version)`` where ``created``
    is ``True`` when the database file did not exist before and was created
    by this call. The schema version is read back from
    :data:`agent_baton.core.storage.schema.SCHEMA_VERSION` so the printed
    value tracks the codebase rather than a hard-coded constant.
    """
    from agent_baton.core.storage.connection import ConnectionManager
    from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION

    ctx_dir = repo_root / ".claude" / "team-context"
    db_path = ctx_dir / "baton.db"
    pre_existed = db_path.exists()

    ctx_dir.mkdir(parents=True, exist_ok=True)

    mgr = ConnectionManager(db_path)
    mgr.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
    # First connection triggers schema bootstrap / migrations.
    conn = mgr.get_connection()
    conn.close()

    return ctx_dir, not pre_existed, SCHEMA_VERSION


def _install_claude_md(repo_root: Path) -> tuple[Path, bool]:
    """Install the bundled CLAUDE.md skeleton if no project file exists.

    Returns ``(claude_md_path, wrote)``. When a CLAUDE.md is already
    present we never overwrite it -- ``wrote`` is ``False``.
    """
    claude_md = repo_root / "CLAUDE.md"
    if claude_md.exists():
        return claude_md, False

    claude_md.write_text(_resolve_template_claude_md(), encoding="utf-8")
    return claude_md, True


def _count_installed_agents(repo_root: Path) -> int:
    """Return the number of agent definitions visible to the project.

    Uses :class:`AgentRegistry` so the count includes bundled, global, and
    project-level agents -- matching what ``baton agents`` would show.
    Falls back to a directory listing of ``.claude/agents/`` if the
    registry cannot be loaded for any reason.
    """
    try:
        from agent_baton.core.orchestration.registry import AgentRegistry

        registry = AgentRegistry()
        return registry.load_default_paths()
    except Exception:
        agents_dir = repo_root / ".claude" / "agents"
        if not agents_dir.exists():
            return 0
        return sum(1 for p in agents_dir.glob("*.md") if p.is_file())


def _generate_starter_plan(
    repo_root: Path,
    ctx_dir: Path,
    *,
    project_name: str | None,
    dry_run: bool,
) -> tuple[int, int, bool]:
    """Generate the starter plan via :class:`IntelligentPlanner`.

    Returns ``(n_phases, n_steps, saved)``. When *dry_run* is ``True`` the
    plan is rendered but neither files nor the SQLite ``plans`` table are
    touched, and ``saved`` is ``False``.
    """
    from agent_baton.core.engine.planner import IntelligentPlanner
    from agent_baton.core.govern.classifier import DataClassifier
    from agent_baton.core.govern.policy import PolicyEngine
    from agent_baton.core.observe.retrospective import RetrospectiveEngine
    from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry

    summary = "Add a hello-world function"
    if project_name:
        summary = f"Add a hello-world function to {project_name}"

    knowledge_registry = KnowledgeRegistry()
    try:
        knowledge_registry.load_default_paths()
    except Exception:
        # KnowledgeRegistry is best-effort -- a fresh project may not have
        # any documents to load yet.
        pass

    bead_store = None
    try:
        from agent_baton.core.engine.bead_store import BeadStore

        db_path = ctx_dir / "baton.db"
        if db_path.exists():
            bead_store = BeadStore(db_path)
    except Exception:
        bead_store = None

    planner = IntelligentPlanner(
        retro_engine=RetrospectiveEngine(),
        classifier=DataClassifier(),
        policy_engine=PolicyEngine(),
        knowledge_registry=knowledge_registry,
        bead_store=bead_store,
    )

    plan = planner.create_plan(
        summary,
        task_type="new-feature",
        complexity="light",
        project_root=repo_root,
    )

    n_phases = len(plan.phases)
    n_steps = plan.total_steps

    if dry_run:
        _print_dry_run_forecast(plan)
        return n_phases, n_steps, False

    # Save plan.json + plan.md alongside any existing artefacts.
    json_path = ctx_dir / "plan.json"
    md_path = ctx_dir / "plan.md"
    json_path.write_text(
        json.dumps(plan.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(plan.to_markdown(), encoding="utf-8")

    # Best-effort persistence to SQLite ``plans`` table.
    try:
        from agent_baton.core.storage import get_project_storage

        storage = get_project_storage(ctx_dir, backend="sqlite")
        storage.save_plan(plan)
    except Exception:
        # Same policy as plan_cmd._persist_plan_to_db: never let this
        # interrupt the file-based save.
        pass

    return n_phases, n_steps, True


def _print_dry_run_forecast(plan) -> None:
    """Render a compact, human-readable preview of *plan*.

    Mirrors the spirit of ``--dry-run`` previews elsewhere in the CLI:
    show task metadata followed by a one-line agent summary per phase.
    """
    print("Dry-run forecast (plan NOT saved):")
    print(f"  Task ID:  {plan.task_id}")
    print(f"  Risk:     {plan.risk_level}")
    print(f"  Budget:   {plan.budget_tier}")
    print(f"  Phases:   {len(plan.phases)}")
    print(f"  Steps:    {plan.total_steps}")
    for phase in plan.phases:
        agents = ", ".join(s.agent_name for s in phase.steps) or "(no steps)"
        print(f"    Phase {phase.phase_id}: {phase.name} -> {agents}")


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "quickstart",
        help="One-command onboarding: detect repo, init storage, generate a starter plan",
    )
    p.add_argument(
        "--name",
        dest="name",
        default=None,
        help="Project name (used to personalise the starter plan summary)",
    )
    p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Preview the starter plan without writing plan.json/plan.md",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    cwd = Path.cwd()

    # ------------------------------------------------------------------
    # Step 1: detect repo state
    # ------------------------------------------------------------------
    repo_root = _find_repo_root(cwd)
    if repo_root is None:
        print(
            "error: not inside a git repository.\n"
            "  Run 'git init' first, then re-run 'baton quickstart'.",
            file=sys.stderr,
        )
        sys.exit(1)

    stack = _detect_stack(repo_root)
    project_label = args.name or repo_root.name
    print(f"baton quickstart for {project_label}")
    print(f"  Repo root:  {repo_root}")
    if stack is None:
        print("  Stack:      unknown (no pyproject.toml/package.json/Cargo.toml)")
    else:
        print(f"  Stack:      {stack}")

    # ------------------------------------------------------------------
    # Step 2: initialise .claude/team-context/baton.db
    # ------------------------------------------------------------------
    ctx_dir, db_created, schema_version = _init_team_context(repo_root)
    if db_created:
        print(
            f"✓ Initialised .claude/team-context/baton.db "
            f"(schema v{schema_version})"
        )
    else:
        print(
            f"✓ baton.db already initialised (schema v{schema_version}) -- skipping"
        )

    # ------------------------------------------------------------------
    # Step 3: install CLAUDE.md skeleton (only if absent)
    # ------------------------------------------------------------------
    _, wrote_md = _install_claude_md(repo_root)
    if wrote_md:
        print("✓ Wrote CLAUDE.md (skeleton)")
    else:
        print("✓ CLAUDE.md already exists -- skipping")

    # ------------------------------------------------------------------
    # Step 4: bundled agents check
    # ------------------------------------------------------------------
    agent_count = _count_installed_agents(repo_root)
    if agent_count == 0:
        print(
            "→ No agent definitions found.\n"
            "  Run 'scripts/install.sh' to install distributable agents/references "
            "into this project."
        )
    else:
        print(f"✓ Found {agent_count} agent definitions")

    # ------------------------------------------------------------------
    # Step 5: generate starter plan
    # ------------------------------------------------------------------
    try:
        n_phases, n_steps, saved = _generate_starter_plan(
            repo_root,
            ctx_dir,
            project_name=args.name,
            dry_run=bool(args.dry_run),
        )
    except Exception as exc:  # noqa: BLE001
        print(
            f"✗ Could not generate starter plan: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        print(
            "  Skipping plan generation. The rest of quickstart succeeded; you can "
            "run 'baton plan \"<task>\" --save' manually.",
            file=sys.stderr,
        )
    else:
        if saved:
            print(
                f"✓ Generated starter plan: {n_phases} phases, {n_steps} steps"
            )
            print("  View it: cat .claude/team-context/plan.md")
            print("  Run it:  baton execute start")
        else:
            # dry-run: forecast already printed inside _generate_starter_plan.
            print(f"  (dry-run) Would generate {n_phases} phases, {n_steps} steps")

    # ------------------------------------------------------------------
    # Step 6: final hint
    # ------------------------------------------------------------------
    print()
    print("Next steps:")
    print("  baton plan --dry-run \"<your task>\"   # preview before committing")
    print("  baton execute start                  # drive the plan")
    print("  baton beads list                     # browse agent memory")
    print("Tip: every command has --help.")
