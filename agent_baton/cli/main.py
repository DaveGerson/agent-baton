"""CLI entry point for the baton command."""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from agent_baton.core.registry import AgentRegistry
from agent_baton.core.router import AgentRouter
from agent_baton.core.context import ContextManager
from agent_baton.core.escalation import EscalationManager
from agent_baton.core.validator import AgentValidator, ValidationResult
from agent_baton.core.vcs import AgentVersionControl
from agent_baton.core.usage import UsageLogger
from agent_baton.core.retrospective import RetrospectiveEngine
from agent_baton.core.scoring import PerformanceScorer
from agent_baton.core.dashboard import DashboardGenerator
from agent_baton.core.spec_validator import SpecValidator
from agent_baton.core.evolution import PromptEvolutionEngine, EvolutionProposal
from agent_baton.core.classifier import DataClassifier
from agent_baton.core.compliance import ComplianceReportGenerator
from agent_baton.core.transfer import ProjectTransfer, TransferManifest
from agent_baton.core.sharing import PackageBuilder
from agent_baton.core.telemetry import AgentTelemetry
from agent_baton.core.policy import PolicyEngine
from agent_baton.core.incident import IncidentManager
from agent_baton.core.async_dispatch import AsyncDispatcher, AsyncTask


def cmd_agents(args: argparse.Namespace) -> None:
    """List available agents."""
    registry = AgentRegistry()
    count = registry.load_default_paths()

    if count == 0:
        print("No agents found. Run scripts/install.sh to install.")
        return

    # Group by category
    by_category: dict[str, list[str]] = {}
    for agent in registry.agents.values():
        cat = agent.category.value
        by_category.setdefault(cat, []).append(agent.name)

    for category, names in sorted(by_category.items()):
        print(f"\n{category}:")
        for name in sorted(names):
            agent = registry.get(name)
            assert agent is not None
            model_tag = f"[{agent.model}]"
            flavor_tag = f" (flavor: {agent.flavor})" if agent.is_flavored else ""
            print(f"  {name:<35} {model_tag:<10}{flavor_tag}")

    print(f"\n{count} agents loaded.")


def cmd_detect(args: argparse.Namespace) -> None:
    """Detect the project stack."""
    registry = AgentRegistry()
    registry.load_default_paths()
    router = AgentRouter(registry)

    root = Path(args.path) if args.path else Path.cwd()
    stack = router.detect_stack(root)

    print(f"Language:  {stack.language or 'unknown'}")
    print(f"Framework: {stack.framework or 'unknown'}")
    if stack.detected_files:
        print(f"Signals:   {', '.join(stack.detected_files)}")


def cmd_route(args: argparse.Namespace) -> None:
    """Route base agent names to flavored variants."""
    registry = AgentRegistry()
    registry.load_default_paths()
    router = AgentRouter(registry)

    root = Path(args.path) if args.path else Path.cwd()
    stack = router.detect_stack(root)

    roles = args.roles or ["backend-engineer", "frontend-engineer"]
    routing = router.route_team(roles, stack)

    print(f"Stack: {stack.language or '?'}/{stack.framework or 'generic'}")
    print()
    for base, resolved in routing.items():
        marker = " *" if resolved != base else ""
        print(f"  {base:<30} → {resolved}{marker}")


def cmd_status(args: argparse.Namespace) -> None:
    """Show team-context status (recovery files)."""
    ctx = ContextManager()
    files = ctx.recovery_files_exist()

    print("Team context status:")
    for name, exists in files.items():
        marker = "✓" if exists else "✗"
        print(f"  {marker} {name}")


def _copy_file(src: Path, dst: Path, *, force: bool) -> bool:
    """Copy src to dst.  Returns True if the file was copied, False if skipped."""
    if dst.exists() and not force:
        print(f"  skip: '{dst}' exists (use --force to overwrite)")
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _merge_settings(src_path: Path, dst_path: Path) -> bool:
    """Merge agent-baton hooks into existing settings.json, preserving user keys.

    Strategy: the source provides 'hooks'. The destination may have 'hooks'
    plus user-specific keys (permissions, mcpServers, env, etc.).
    We merge hook events additively — baton hooks are added/updated,
    user hooks for other events are preserved.
    """
    import json

    try:
        src_data = json.loads(src_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    if dst_path.exists():
        try:
            dst_data = json.loads(dst_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            dst_data = {}
    else:
        dst_data = {}

    # Merge hooks: for each hook event in source, replace in destination
    src_hooks = src_data.get("hooks", {})
    if src_hooks:
        dst_hooks = dst_data.setdefault("hooks", {})
        for event, entries in src_hooks.items():
            dst_hooks[event] = entries  # replace per-event (baton owns these)
        print(f"  merge: settings.json hooks ({len(src_hooks)} events)")

    # All other top-level keys in destination are preserved untouched
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(
        json.dumps(dst_data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return True


def cmd_install(args: argparse.Namespace) -> None:
    """Non-interactive installer: copy agents, references, and templates.

    --upgrade mode: overwrites agents + references (they improve between
    versions), merges hooks into settings.json (preserving user keys),
    and preserves CLAUDE.md, knowledge/, and team-context/.
    """
    scope: str = args.scope
    source = Path(args.source).resolve()
    force: bool = args.force
    upgrade: bool = args.upgrade

    agents_src = source / "agents"
    refs_src = source / "references"
    claude_md_src = source / "templates" / "CLAUDE.md"
    settings_src = source / "templates" / "settings.json"

    if not agents_src.is_dir():
        print(
            f"error: agents/ directory not found under '{source}'. "
            "Pass the correct --source path."
        )
        sys.exit(1)

    if scope == "user":
        base = Path.home() / ".claude"
        claude_md_dst = base / "CLAUDE.md"
        settings_dst = base / "settings.json"
    else:
        base = Path.cwd() / ".claude"
        claude_md_dst = Path.cwd() / "CLAUDE.md"
        settings_dst = base / "settings.json"

    agent_target = base / "agents"
    ref_target = base / "references"
    team_ctx = base / "team-context"
    knowledge_dir = base / "knowledge"
    skills_dir = base / "skills"

    for d in (agent_target, ref_target, team_ctx, knowledge_dir, skills_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Agents + references: always overwrite on upgrade (these improve between versions)
    agent_force = force or upgrade
    ref_force = force or upgrade

    agent_count = 0
    for src_file in sorted(agents_src.glob("*.md")):
        dst_file = agent_target / src_file.name
        if _copy_file(src_file, dst_file, force=agent_force):
            agent_count += 1

    ref_count = 0
    if refs_src.is_dir():
        for src_file in sorted(refs_src.glob("*.md")):
            dst_file = ref_target / src_file.name
            if _copy_file(src_file, dst_file, force=ref_force):
                ref_count += 1

    # Settings.json: merge on upgrade (preserve user keys, update hooks),
    # copy on fresh install
    if settings_src.is_file():
        if upgrade:
            _merge_settings(settings_src, settings_dst)
        else:
            _copy_file(settings_src, settings_dst, force=force)

    # CLAUDE.md: only on fresh install or --force (user may have customized it)
    if not upgrade:
        if claude_md_src.is_file():
            _copy_file(claude_md_src, claude_md_dst, force=force)

    action = "Upgraded" if upgrade else "Installed"
    print(f"{action}: {agent_count} agents + {ref_count} references to {scope}")


def cmd_changelog(args: argparse.Namespace) -> None:
    """Show agent changelog entries or list backup files."""
    vcs = AgentVersionControl()

    if args.backups is not None:
        # --backups [NAME] — list backup files
        agent_filter: str | None = args.backups if args.backups else None
        backups = vcs.list_backups(agent_filter)
        if not backups:
            label = f" for agent '{agent_filter}'" if agent_filter else ""
            print(f"No backups found{label}.")
            return
        label = f" for agent '{agent_filter}'" if agent_filter else ""
        print(f"Backups{label}:")
        for path in backups:
            print(f"  {path}")
        return

    # Default: show changelog entries
    agent_filter_name: str | None = args.agent if args.agent else None
    if agent_filter_name:
        entries = vcs.get_agent_history(agent_filter_name)
    else:
        entries = vcs.read_changelog()

    if not entries:
        if agent_filter_name:
            print(f"No changelog entries for agent '{agent_filter_name}'.")
        else:
            print("Changelog is empty.")
        return

    label = f" for agent '{agent_filter_name}'" if agent_filter_name else ""
    print(f"Agent changelog{label}:\n")
    for entry in entries:
        print(f"  {entry.timestamp}  [{entry.action}]  {entry.agent_name}")
        print(f"    {entry.summary}")
        if entry.backup_path:
            print(f"    Backup: {entry.backup_path}")
        print()


def cmd_usage(args: argparse.Namespace) -> None:
    """Show usage statistics from the usage log."""
    logger = UsageLogger()

    if args.agent:
        # Per-agent stats
        stats = logger.agent_stats(args.agent)
        if stats["times_used"] == 0:
            print(f"No records found for agent '{args.agent}'.")
            return

        gate_rate = stats["gate_pass_rate"]
        gate_str = f"{gate_rate:.0%}" if gate_rate is not None else "n/a"

        print(f"Agent stats: {args.agent}")
        print(f"  Times used:      {stats['times_used']}")
        print(f"  Total retries:   {stats['total_retries']}")
        print(f"  Avg retries:     {stats['avg_retries']}")
        print(f"  Gate pass rate:  {gate_str}")
        if stats["models_used"]:
            print("  Models used:")
            for model, count in sorted(stats["models_used"].items(), key=lambda x: -x[1]):
                print(f"    {model:<20} {count}")
        return

    if args.recent is not None:
        # Recent records
        records = logger.read_recent(args.recent)
        if not records:
            print("No usage records found.")
            return
        print(f"Recent {len(records)} record(s):")
        for rec in records:
            agents_str = ", ".join(a.name for a in rec.agents_used) or "(none)"
            print(f"  {rec.timestamp}  [{rec.outcome or 'no outcome'}]  {rec.task_id}")
            print(f"    agents: {agents_str}")
            print(f"    risk: {rec.risk_level}  gates: {rec.gates_passed}P/{rec.gates_failed}F")
            if rec.notes:
                print(f"    notes: {rec.notes}")
        return

    # Default: summary
    stats = logger.summary()
    total = stats["total_tasks"]

    if total == 0:
        print("No usage records found.")
        return

    print(f"Usage Summary ({total} task{'s' if total != 1 else ''}):")
    print(f"  Total agents used:     {stats['total_agents_used']}")
    print(f"  Estimated tokens:      {stats['total_estimated_tokens']:,}")
    print(f"  Avg agents/task:       {stats['avg_agents_per_task']}")
    print(f"  Avg retries/task:      {stats['avg_retries_per_task']}")

    if stats["outcome_counts"]:
        print()
        print("Outcomes:")
        for outcome, count in sorted(stats["outcome_counts"].items(), key=lambda x: -x[1]):
            print(f"  {outcome:<18} {count}")

    if stats["agent_frequency"]:
        print()
        print("Top Agents:")
        sorted_agents = sorted(
            stats["agent_frequency"].items(), key=lambda x: -x[1]
        )
        for name, count in sorted_agents[:10]:
            uses = "use" if count == 1 else "uses"
            print(f"  {name:<35} {count} {uses}")


def cmd_scores(args: argparse.Namespace) -> None:
    """Show agent performance scorecards."""
    scorer = PerformanceScorer()

    if args.agent:
        sc = scorer.score_agent(args.agent)
        if sc.times_used == 0:
            print(f"No usage data for agent '{args.agent}'.")
            return
        print(sc.to_markdown())
        return

    if args.write:
        path = scorer.write_report()
        print(f"Scorecard report written to {path}")
        return

    report = scorer.generate_report()
    print(report)


def cmd_evolve(args: argparse.Namespace) -> None:
    """Analyze agent performance and propose prompt improvements."""
    engine = PromptEvolutionEngine()

    if args.agent:
        proposal = engine.propose_for_agent(args.agent)
        if proposal is None:
            print(f"No issues found for agent '{args.agent}' (no usage data or performing well).")
            return
        print(proposal.to_markdown())
        return

    if args.save:
        proposals = engine.analyze()
        if not proposals:
            print("All agents performing well. No proposals to save.")
            return
        paths = engine.save_proposals(proposals)
        print(f"Saved {len(paths)} proposal(s):")
        for p in paths:
            print(f"  {p}")
        return

    if args.write:
        path = engine.write_report()
        print(f"Evolution report written to {path}")
        return

    # Default: print report to stdout
    print(engine.generate_report())


def cmd_classify(args: argparse.Namespace) -> None:
    """Classify task sensitivity and select guardrail preset."""
    classifier = DataClassifier()
    file_paths: list[str] | None = args.files if args.files else None
    result = classifier.classify(args.description, file_paths)

    print(f"Risk Level: {result.risk_level.value}")
    print(f"Preset: {result.guardrail_preset}")
    print(f"Confidence: {result.confidence}")
    if result.signals_found:
        print(f"Signals: {', '.join(result.signals_found)}")
    if result.explanation:
        print(f"Explanation: {result.explanation}")


def cmd_dashboard(args: argparse.Namespace) -> None:
    """Generate or display the usage dashboard."""
    gen = DashboardGenerator()

    if args.write:
        path = gen.write()
        print(f"Dashboard written to {path}")
        return

    print(gen.generate())


def cmd_retro(args: argparse.Namespace) -> None:
    """Show retrospectives."""
    engine = RetrospectiveEngine()

    if args.search:
        results = engine.search(args.search)
        if not results:
            print(f"No retrospectives matching '{args.search}'.")
            return
        print(f"Retrospectives matching '{args.search}':")
        for path in results:
            print(f"  {path.stem}")
        return

    if args.recommendations:
        recs = engine.extract_recommendations()
        if not recs:
            print("No roster recommendations found.")
            return
        print("Roster Recommendations (across all retrospectives):")
        for rec in recs:
            print(f"  [{rec.action}] {rec.target}")
            if rec.reason:
                print(f"    {rec.reason}")
        return

    if args.task_id:
        content = engine.load(args.task_id)
        if content is None:
            print(f"No retrospective found for task '{args.task_id}'.")
            return
        print(content)
        return

    # Default: list recent retrospectives
    recent = engine.list_recent(args.count or 10)
    if not recent:
        print("No retrospectives found.")
        return
    print(f"Recent retrospectives ({len(recent)}):")
    for path in recent:
        print(f"  {path.stem}")


def cmd_escalations(args: argparse.Namespace) -> None:
    """Show, resolve, or clear escalations."""
    manager = EscalationManager()

    if args.clear:
        manager.clear_resolved()
        print("Resolved escalations cleared.")
        return

    if args.resolve:
        agent_name, answer = args.resolve
        if manager.resolve(agent_name, answer):
            print(f"Resolved escalation for agent '{agent_name}'.")
        else:
            print(f"No pending escalation found for agent '{agent_name}'.")
        return

    escalations = manager.get_all() if args.all else manager.get_pending()

    if not escalations:
        label = "escalations" if args.all else "pending escalations"
        print(f"No {label}.")
        return

    label = "All escalations" if args.all else "Pending escalations"
    print(f"{label} ({len(escalations)}):\n")
    for esc in escalations:
        print(esc.to_markdown())
        print()
        print("---")
        print()


def cmd_validate(args: argparse.Namespace) -> None:
    """Validate agent definition .md files."""
    strict: bool = args.strict
    validator = AgentValidator()

    all_results: list[ValidationResult] = []
    for raw_path in args.paths:
        target = Path(raw_path)
        if target.is_dir():
            all_results.extend(validator.validate_directory(target))
        elif target.is_file():
            all_results.append(validator.validate_file(target))
        else:
            all_results.append(
                ValidationResult(
                    path=target,
                    valid=False,
                    errors=[f"'{target}' does not exist"],
                )
            )

    valid_count = 0
    warn_count = 0
    error_count = 0

    for result in all_results:
        has_errors = bool(result.errors)
        # In strict mode, warnings are treated as errors for exit-code purposes
        has_warnings = bool(result.warnings)
        effective_fail = has_errors or (strict and has_warnings)

        if effective_fail:
            print(f"  {result.path}")
            for msg in result.errors:
                print(f"    error: {msg}")
            if strict:
                for msg in result.warnings:
                    print(f"    warning (strict): {msg}")
            elif has_warnings:
                for msg in result.warnings:
                    print(f"    warning: {msg}")
            error_count += 1
        elif has_warnings:
            print(f"  {result.path}")
            for msg in result.warnings:
                print(f"    warning: {msg}")
            warn_count += 1
        else:
            print(f"  {result.path}")
            valid_count += 1

    total = len(all_results)
    print(
        f"\nValidated {total} file{'s' if total != 1 else ''}: "
        f"{valid_count} valid, {warn_count} warnings, {error_count} errors"
    )

    if error_count > 0:
        sys.exit(1)


def cmd_spec_check(args: argparse.Namespace) -> None:
    """Run spec validation checks against agent outputs."""
    validator = SpecValidator()

    if args.json and args.schema:
        result = validator.validate_json_against_schema(
            Path(args.json), Path(args.schema)
        )
    elif args.files and args.expect:
        expected = [f.strip() for f in args.expect.split(",") if f.strip()]
        result = validator.validate_file_structure(Path(args.files), expected)
    elif args.exports and args.expect:
        expected = [n.strip() for n in args.expect.split(",") if n.strip()]
        result = validator.validate_exports(Path(args.exports), expected)
    else:
        print(
            "error: supply one of:\n"
            "  --json DATA --schema SCHEMA\n"
            "  --files ROOT --expect file1,file2,...\n"
            "  --exports MODULE --expect name1,name2,...",
        )
        sys.exit(1)

    for check in result.checks:
        status = "PASS" if check.passed else "FAIL"
        line = f"  [{status}] {check.name}"
        if check.message and not check.passed:
            line += f": {check.message}"
        print(line)

    print(f"\n{result.summary}")

    if not result.passed:
        sys.exit(1)


def cmd_transfer(args: argparse.Namespace) -> None:
    """Transfer agents, knowledge, and references between projects."""
    project_root = Path(args.project) if args.project else None
    transfer = ProjectTransfer(source_root=project_root)

    if args.discover:
        min_score: float = args.min_score or 0.0
        manifest = transfer.discover_transferable(min_score=min_score)
        print(manifest.to_markdown())
        return

    if args.export:
        target = Path(args.export)
        agent_names: list[str] = []
        if args.agents:
            raw = args.agents
            agent_names = [
                (a if a.endswith(".md") else f"{a}.md")
                for a in raw.split(",")
                if a.strip()
            ]
        knowledge_packs: list[str] = []
        if args.knowledge:
            knowledge_packs = [k.strip() for k in args.knowledge.split(",") if k.strip()]
        ref_names: list[str] = []
        if args.references:
            ref_names = [
                (r if r.endswith(".md") else f"{r}.md")
                for r in args.references.split(",")
                if r.strip()
            ]

        if args.all:
            manifest = transfer.discover_transferable()
        else:
            manifest = TransferManifest(
                agents=agent_names,
                knowledge_packs=knowledge_packs,
                references=ref_names,
            )

        counts = transfer.export_to(target, manifest, force=args.force)
        print(
            f"Exported to '{target}': "
            f"{counts['agents']} agents, "
            f"{counts['knowledge']} knowledge files, "
            f"{counts['references']} references"
        )
        return

    if args.import_from:
        source = Path(args.import_from)
        agent_names = []
        if args.agents:
            raw = args.agents
            agent_names = [
                (a if a.endswith(".md") else f"{a}.md")
                for a in raw.split(",")
                if a.strip()
            ]
        knowledge_packs = []
        if args.knowledge:
            knowledge_packs = [k.strip() for k in args.knowledge.split(",") if k.strip()]
        ref_names = []
        if args.references:
            ref_names = [
                (r if r.endswith(".md") else f"{r}.md")
                for r in args.references.split(",")
                if r.strip()
            ]

        other = ProjectTransfer(source_root=source)
        if args.all:
            manifest = other.discover_transferable()
        else:
            manifest = TransferManifest(
                agents=agent_names,
                knowledge_packs=knowledge_packs,
                references=ref_names,
            )

        counts = transfer.import_from(source, manifest, force=args.force)
        print(
            f"Imported from '{source}': "
            f"{counts['agents']} agents, "
            f"{counts['knowledge']} knowledge files, "
            f"{counts['references']} references"
        )
        return

    print("error: supply --discover, --export PATH, or --import PATH")
    sys.exit(1)


def cmd_package(args: argparse.Namespace) -> None:
    """Create, inspect, or install agent-baton package archives."""
    project_root = Path(args.project) if args.project else None
    builder = PackageBuilder(source_root=project_root)

    if args.name:
        output_dir = Path(args.output_dir) if args.output_dir else None
        archive = builder.build(
            name=args.name,
            version=args.version or "1.0.0",
            description=args.description or "",
            include_agents=not args.no_agents,
            include_references=not args.no_references,
            include_knowledge=args.include_knowledge,
            output_dir=output_dir,
        )
        print(f"Package created: {archive}")
        return

    if args.info:
        archive_path = Path(args.info)
        manifest = builder.read_manifest(archive_path)
        if manifest is None:
            print(f"error: could not read manifest from '{archive_path}'")
            sys.exit(1)
        print(f"Name:        {manifest.name}")
        print(f"Version:     {manifest.version}")
        print(f"Description: {manifest.description}")
        print(f"Created:     {manifest.created_at}")
        print(f"Baton ver:   {manifest.baton_version}")
        print(f"Agents ({len(manifest.agents)}):     {', '.join(manifest.agents) or '(none)'}")
        print(f"References ({len(manifest.references)}): {', '.join(manifest.references) or '(none)'}")
        print(f"Knowledge packs ({len(manifest.knowledge_packs)}): {', '.join(manifest.knowledge_packs) or '(none)'}")
        return

    if args.install:
        archive_path = Path(args.install)
        scope = args.scope or "project"
        counts = builder.install_package(archive_path, scope=scope, force=args.force)
        print(
            f"Installed to '{scope}': "
            f"{counts['agents']} agents, "
            f"{counts['references']} references, "
            f"{counts['knowledge']} knowledge files"
        )
        return

    print("error: supply --name NAME, --info ARCHIVE, or --install ARCHIVE")
    sys.exit(1)


def cmd_compliance(args: argparse.Namespace) -> None:
    """Show compliance reports."""
    generator = ComplianceReportGenerator()

    if args.task_id:
        content = generator.load(args.task_id)
        if content is None:
            print(f"No compliance report found for task '{args.task_id}'.")
            return
        print(content)
        return

    # Default: list recent reports
    count = args.count or 5
    recent = generator.list_recent(count)
    if not recent:
        print("No compliance reports found.")
        return
    print(f"Recent compliance reports ({len(recent)}):")
    for path in recent:
        print(f"  {path.stem}")


def cmd_telemetry(args: argparse.Namespace) -> None:
    """Show or clear agent telemetry events."""
    tel = AgentTelemetry()

    if args.clear:
        tel.clear()
        print("Telemetry log cleared.")
        return

    if args.recent is not None:
        events = tel.read_recent(args.recent)
        if not events:
            print("No telemetry events found.")
            return
        print(f"Recent {len(events)} event(s):")
        for ev in events:
            print(f"  {ev.timestamp}  [{ev.event_type}]  {ev.agent_name}  {ev.tool_name or ev.file_path or ev.details}")
        return

    if args.agent:
        events = tel.read_events(args.agent)
        if not events:
            print(f"No telemetry events for agent '{args.agent}'.")
            return
        print(f"Events for agent '{args.agent}' ({len(events)}):")
        for ev in events:
            print(f"  {ev.timestamp}  [{ev.event_type}]  {ev.tool_name or ev.file_path or ev.details}")
        return

    # Default: summary
    s = tel.summary()
    total = s["total_events"]
    if total == 0:
        print("No telemetry events found.")
        return

    print(f"Telemetry Summary ({total} event{'s' if total != 1 else ''}):")
    if s["events_by_agent"]:
        print("\nBy Agent:")
        for agent, count in sorted(s["events_by_agent"].items(), key=lambda x: -x[1]):
            print(f"  {agent:<35} {count}")
    if s["events_by_type"]:
        print("\nBy Type:")
        for etype, count in sorted(s["events_by_type"].items(), key=lambda x: -x[1]):
            print(f"  {etype:<20} {count}")
    if s["files_read"]:
        print(f"\nFiles read:    {len(s['files_read'])}")
    if s["files_written"]:
        print(f"Files written: {len(s['files_written'])}")


def cmd_policy(args: argparse.Namespace) -> None:
    """List, show, or check guardrail policy presets."""
    engine = PolicyEngine()

    if args.show:
        preset = engine.load_preset(args.show)
        if preset is None:
            print(f"Policy preset '{args.show}' not found.")
            return
        print(f"Policy: {preset.name}")
        print(f"Description: {preset.description}")
        print(f"Rules ({len(preset.rules)}):")
        for rule in preset.rules:
            print(f"  [{rule.rule_type}/{rule.severity}] {rule.name}: {rule.description}")
            if rule.pattern:
                print(f"    pattern: {rule.pattern}  scope: {rule.scope}")
        return

    if args.check and args.preset:
        preset = engine.load_preset(args.preset)
        if preset is None:
            print(f"Policy preset '{args.preset}' not found.")
            return
        allowed_paths = args.paths.split(",") if args.paths else []
        tools = args.tools.split(",") if args.tools else []
        violations = engine.evaluate(preset, args.check, allowed_paths, tools)
        if not violations:
            print(f"Agent '{args.check}' is compliant with preset '{args.preset}'.")
            return
        print(f"Violations for agent '{args.check}' against preset '{args.preset}':")
        for v in violations:
            severity_tag = f"[{v.rule.severity.upper()}]"
            print(f"  {severity_tag} {v.rule.name}: {v.details}")
        return

    # Default: list presets
    names = engine.list_presets()
    if not names:
        print("No policy presets found.")
        return
    print(f"Available policy presets ({len(names)}):")
    for name in names:
        preset = engine.load_preset(name)
        desc = preset.description if preset else ""
        print(f"  {name:<25} {desc}")


def cmd_incident(args: argparse.Namespace) -> None:
    """Manage incident response workflows."""
    manager = IncidentManager()

    if args.templates:
        for sev in ("P1", "P2", "P3", "P4"):
            tmpl = manager.get_template(sev)
            print(f"{sev}: {tmpl.name} ({len(tmpl.phases)} phases)")
            for i, phase in enumerate(tmpl.phases, start=1):
                print(f"  Phase {i}: {phase.name}")
        return

    if args.create and args.severity and args.desc:
        path = manager.create_incident(args.create, args.severity, args.desc)
        print(f"Incident created: {path}")
        return

    if args.show:
        content = manager.load_incident(args.show)
        if content is None:
            print(f"No incident found with ID '{args.show}'.")
            return
        print(content)
        return

    # Default: list incidents
    incidents = manager.list_incidents()
    if not incidents:
        print("No incidents found.")
        return
    print(f"Incidents ({len(incidents)}):")
    for path in incidents:
        print(f"  {path.stem}")


def cmd_async(args: argparse.Namespace) -> None:
    """Dispatch and track asynchronous tasks."""
    dispatcher = AsyncDispatcher()

    if args.dispatch:
        task = AsyncTask(
            task_id=args.task_id or f"task-{args.dispatch[:20].replace(' ', '-')}",
            command=args.dispatch,
            dispatch_type=args.type or "shell",
        )
        path = dispatcher.dispatch(task)
        print(f"Task dispatched: {task.task_id} -> {path}")
        return

    if args.show:
        task = dispatcher.check_status(args.show)
        if task is None:
            print(f"No task found with ID '{args.show}'.")
            return
        print(f"Task ID:      {task.task_id}")
        print(f"Command:      {task.command}")
        print(f"Type:         {task.dispatch_type}")
        print(f"Status:       {task.status}")
        if task.dispatched_at:
            print(f"Dispatched:   {task.dispatched_at}")
        if task.completed_at:
            print(f"Completed:    {task.completed_at}")
        if task.exit_code is not None:
            print(f"Exit code:    {task.exit_code}")
        if task.result:
            print(f"Result:       {task.result}")
        return

    if args.pending:
        tasks = dispatcher.list_pending()
        if not tasks:
            print("No pending tasks.")
            return
        print(f"Pending tasks ({len(tasks)}):")
        for t in tasks:
            print(f"  {t.task_id:<30} {t.command}")
        return

    # Default: list all tasks
    tasks = dispatcher.list_tasks()
    if not tasks:
        print("No async tasks found.")
        return
    print(f"Async tasks ({len(tasks)}):")
    for t in tasks:
        print(f"  [{t.status:<12}] {t.task_id:<30} {t.command}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="baton",
        description="Agent Baton — multi-agent orchestration tools",
    )
    sub = parser.add_subparsers(dest="command")

    # baton agents
    sub.add_parser("agents", help="List available agents")

    # baton detect
    p_detect = sub.add_parser("detect", help="Detect project stack")
    p_detect.add_argument("--path", default=None, help="Project root path")

    # baton route
    p_route = sub.add_parser("route", help="Route roles to agent flavors")
    p_route.add_argument("roles", nargs="*", help="Base agent names to route")
    p_route.add_argument("--path", default=None, help="Project root path")

    # baton status
    sub.add_parser("status", help="Show team-context file status")

    # baton install
    p_install = sub.add_parser("install", help="Install agents and references")
    p_install.add_argument(
        "--scope",
        required=True,
        choices=["user", "project"],
        help="Install to user (~/.claude/) or project (.claude/) scope",
    )
    p_install.add_argument(
        "--source",
        default=".",
        help="Path to the agent-baton repo root (default: current directory)",
    )
    p_install.add_argument(
        "--force",
        action="store_true",
        help="Overwrite ALL existing files without prompting",
    )
    p_install.add_argument(
        "--upgrade",
        action="store_true",
        help="Upgrade: overwrite agents + references but preserve settings, "
        "CLAUDE.md, knowledge packs, and team-context",
    )

    # baton changelog
    p_changelog = sub.add_parser("changelog", help="Show agent changelog or list backups")
    p_changelog.add_argument(
        "--agent",
        default=None,
        metavar="NAME",
        help="Show history for a specific agent",
    )
    p_changelog.add_argument(
        "--backups",
        nargs="?",
        const="",
        default=None,
        metavar="NAME",
        help=(
            "List backup files. Optionally filter by agent name: "
            "--backups lists all, --backups NAME lists for that agent."
        ),
    )

    # baton usage
    p_usage = sub.add_parser("usage", help="Show usage statistics")
    p_usage_group = p_usage.add_mutually_exclusive_group()
    p_usage_group.add_argument(
        "--recent",
        type=int,
        metavar="N",
        help="Show the N most recent records",
    )
    p_usage_group.add_argument(
        "--agent",
        metavar="NAME",
        help="Show stats for a specific agent",
    )

    # baton scores
    p_scores = sub.add_parser("scores", help="Show agent performance scorecards")
    p_scores_group = p_scores.add_mutually_exclusive_group()
    p_scores_group.add_argument(
        "--agent", metavar="NAME", help="Show scorecard for a specific agent",
    )
    p_scores_group.add_argument(
        "--write", action="store_true", help="Write scorecard report to disk",
    )

    # baton evolve
    p_evolve = sub.add_parser("evolve", help="Propose prompt improvements for underperforming agents")
    p_evolve_group = p_evolve.add_mutually_exclusive_group()
    p_evolve_group.add_argument(
        "--agent", metavar="NAME", help="Show proposal for a specific agent",
    )
    p_evolve_group.add_argument(
        "--save",
        action="store_true",
        help="Write proposals to .claude/team-context/evolution-proposals/",
    )
    p_evolve_group.add_argument(
        "--write",
        action="store_true",
        help="Write summary report to disk",
    )

    # baton dashboard
    p_dash = sub.add_parser("dashboard", help="Generate usage dashboard")
    p_dash.add_argument(
        "--write", action="store_true", help="Write dashboard to disk",
    )

    # baton retro
    p_retro = sub.add_parser("retro", help="Show retrospectives")
    p_retro_group = p_retro.add_mutually_exclusive_group()
    p_retro_group.add_argument(
        "--task-id", metavar="ID", help="Show a specific retrospective",
    )
    p_retro_group.add_argument(
        "--search", metavar="KEYWORD", help="Search retrospectives by keyword",
    )
    p_retro_group.add_argument(
        "--recommendations", action="store_true",
        help="Extract roster recommendations from all retrospectives",
    )
    p_retro.add_argument(
        "--count", type=int, default=None, metavar="N",
        help="Number of recent retrospectives to list (default 10)",
    )

    # baton escalations
    p_esc = sub.add_parser("escalations", help="Show or resolve agent escalations")
    p_esc_group = p_esc.add_mutually_exclusive_group()
    p_esc_group.add_argument(
        "--all",
        action="store_true",
        help="Show all escalations, including resolved ones",
    )
    p_esc_group.add_argument(
        "--resolve",
        nargs=2,
        metavar=("AGENT", "ANSWER"),
        help="Resolve the oldest pending escalation for AGENT with ANSWER",
    )
    p_esc_group.add_argument(
        "--clear",
        action="store_true",
        help="Remove all resolved escalations from the file",
    )

    # baton validate
    p_validate = sub.add_parser("validate", help="Validate agent .md files")
    p_validate.add_argument(
        "paths",
        nargs="+",
        help="File or directory paths to validate",
    )
    p_validate.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors (exit code 1 if any warnings)",
    )

    # baton classify
    p_classify = sub.add_parser(
        "classify", help="Classify task sensitivity and select guardrail preset"
    )
    p_classify.add_argument(
        "description",
        help="Task description to classify",
    )
    p_classify.add_argument(
        "--files",
        nargs="*",
        metavar="FILE",
        help="File paths affected by the task (used to elevate risk from path patterns)",
    )

    # baton spec-check
    p_spec = sub.add_parser(
        "spec-check", help="Validate agent output against a spec"
    )
    p_spec_mode = p_spec.add_mutually_exclusive_group()
    p_spec_mode.add_argument(
        "--json",
        metavar="DATA_FILE",
        help="JSON data file to validate",
    )
    p_spec_mode.add_argument(
        "--files",
        metavar="ROOT",
        help="Directory root to check for expected files",
    )
    p_spec_mode.add_argument(
        "--exports",
        metavar="MODULE",
        help="Python module file to check for expected exports",
    )
    p_spec.add_argument(
        "--schema",
        metavar="SCHEMA_FILE",
        help="JSON Schema file (used with --json)",
    )
    p_spec.add_argument(
        "--expect",
        metavar="NAMES",
        help="Comma-separated list of expected files or names (used with --files / --exports)",
    )

    # baton transfer
    p_transfer = sub.add_parser(
        "transfer", help="Transfer agents/knowledge/references between projects"
    )
    p_transfer_mode = p_transfer.add_mutually_exclusive_group()
    p_transfer_mode.add_argument(
        "--discover",
        action="store_true",
        help="Show what is available to transfer from this project",
    )
    p_transfer_mode.add_argument(
        "--export",
        metavar="TARGET",
        help="Export items to a target project root",
    )
    p_transfer_mode.add_argument(
        "--import",
        dest="import_from",
        metavar="SOURCE",
        help="Import items from another project root into this one",
    )
    p_transfer.add_argument(
        "--project",
        default=None,
        metavar="ROOT",
        help="Source project root (default: current directory)",
    )
    p_transfer.add_argument(
        "--agents",
        metavar="NAMES",
        help="Comma-separated agent names (without .md) or filenames",
    )
    p_transfer.add_argument(
        "--knowledge",
        metavar="PACKS",
        help="Comma-separated knowledge pack directory names",
    )
    p_transfer.add_argument(
        "--references",
        metavar="NAMES",
        help="Comma-separated reference filenames",
    )
    p_transfer.add_argument(
        "--all",
        action="store_true",
        help="Transfer all discoverable items",
    )
    p_transfer.add_argument(
        "--min-score",
        dest="min_score",
        type=float,
        default=0.0,
        metavar="RATE",
        help="Minimum first-pass rate for --discover (0.0–1.0)",
    )
    p_transfer.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files at the destination",
    )

    # baton package
    p_package = sub.add_parser("package", help="Create or install agent-baton packages")
    p_package_mode = p_package.add_mutually_exclusive_group()
    p_package_mode.add_argument(
        "--name",
        metavar="NAME",
        help="Create a package archive with this name",
    )
    p_package_mode.add_argument(
        "--info",
        metavar="ARCHIVE",
        help="Show manifest of an existing .tar.gz package",
    )
    p_package_mode.add_argument(
        "--install",
        metavar="ARCHIVE",
        help="Install an agent-baton package",
    )
    p_package.add_argument(
        "--version",
        default="1.0.0",
        help="Package version (default: 1.0.0)",
    )
    p_package.add_argument(
        "--description",
        default="",
        help="Package description",
    )
    p_package.add_argument(
        "--include-knowledge",
        dest="include_knowledge",
        action="store_true",
        help="Include knowledge packs in the package",
    )
    p_package.add_argument(
        "--no-agents",
        action="store_true",
        help="Exclude agents from the package",
    )
    p_package.add_argument(
        "--no-references",
        action="store_true",
        help="Exclude references from the package",
    )
    p_package.add_argument(
        "--output-dir",
        dest="output_dir",
        default=None,
        metavar="DIR",
        help="Directory to write the archive to (default: current directory)",
    )
    p_package.add_argument(
        "--scope",
        choices=["user", "project"],
        default="project",
        help="Install scope: user (~/.claude/) or project (.claude/) — used with --install",
    )
    p_package.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files when installing",
    )
    p_package.add_argument(
        "--project",
        default=None,
        metavar="ROOT",
        help="Source project root (default: current directory)",
    )

    # baton compliance
    p_compliance = sub.add_parser("compliance", help="Show compliance reports")
    p_compliance_group = p_compliance.add_mutually_exclusive_group()
    p_compliance_group.add_argument(
        "--task-id", metavar="ID", help="Show a specific compliance report",
    )
    p_compliance.add_argument(
        "--count", type=int, default=None, metavar="N",
        help="Number of recent reports to list (default 5)",
    )

    # baton telemetry
    p_tel = sub.add_parser("telemetry", help="Show or clear agent telemetry events")
    p_tel_group = p_tel.add_mutually_exclusive_group()
    p_tel_group.add_argument(
        "--agent", metavar="NAME", help="Show events for a specific agent",
    )
    p_tel_group.add_argument(
        "--recent", type=int, metavar="N", help="Show the N most recent events",
    )
    p_tel_group.add_argument(
        "--clear", action="store_true", help="Clear the telemetry log",
    )

    # baton policy
    p_pol = sub.add_parser("policy", help="List or evaluate guardrail policy presets")
    p_pol.add_argument(
        "--show", metavar="NAME", default=None,
        help="Show rules of a named policy preset",
    )
    p_pol.add_argument(
        "--check", metavar="AGENT", default=None,
        help="Agent name to evaluate (use with --preset)",
    )
    p_pol.add_argument(
        "--preset", metavar="NAME", default=None,
        help="Policy preset name to evaluate against (use with --check)",
    )
    p_pol.add_argument(
        "--paths", metavar="PATHS", default=None,
        help="Comma-separated allowed file paths for the agent (used with --check)",
    )
    p_pol.add_argument(
        "--tools", metavar="TOOLS", default=None,
        help="Comma-separated tools available to the agent (used with --check)",
    )

    # baton incident
    p_inc = sub.add_parser("incident", help="Manage incident response workflows")
    p_inc_group = p_inc.add_mutually_exclusive_group()
    p_inc_group.add_argument(
        "--templates", action="store_true", help="Show all built-in incident templates",
    )
    p_inc_group.add_argument(
        "--show", metavar="ID", help="Show a specific incident document",
    )
    p_inc.add_argument(
        "--create", metavar="ID", default=None,
        help="Create an incident document with the given ID",
    )
    p_inc.add_argument(
        "--severity", metavar="LEVEL", default=None,
        help="Severity level for --create (P1, P2, P3, P4)",
    )
    p_inc.add_argument(
        "--desc", metavar="TEXT", default=None,
        help="Description for --create",
    )

    # baton async
    p_async = sub.add_parser("async", help="Dispatch and track asynchronous tasks")
    p_async_group = p_async.add_mutually_exclusive_group()
    p_async_group.add_argument(
        "--pending", action="store_true", help="List only pending tasks",
    )
    p_async_group.add_argument(
        "--show", metavar="ID", help="Show a specific task's status",
    )
    p_async_group.add_argument(
        "--dispatch", metavar="COMMAND", help="Dispatch a new task",
    )
    p_async.add_argument(
        "--task-id", dest="task_id", metavar="ID", default=None,
        help="Task ID for --dispatch (auto-generated if omitted)",
    )
    p_async.add_argument(
        "--type", metavar="TYPE", default="shell",
        help="Dispatch type for --dispatch: shell, script, or manual (default: shell)",
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return

    commands = {
        "agents": cmd_agents,
        "detect": cmd_detect,
        "route": cmd_route,
        "status": cmd_status,
        "install": cmd_install,
        "changelog": cmd_changelog,
        "usage": cmd_usage,
        "scores": cmd_scores,
        "evolve": cmd_evolve,
        "dashboard": cmd_dashboard,
        "retro": cmd_retro,
        "escalations": cmd_escalations,
        "validate": cmd_validate,
        "spec-check": cmd_spec_check,
        "classify": cmd_classify,
        "compliance": cmd_compliance,
        "transfer": cmd_transfer,
        "package": cmd_package,
        "telemetry": cmd_telemetry,
        "policy": cmd_policy,
        "incident": cmd_incident,
        "async": cmd_async,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
