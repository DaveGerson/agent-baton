#!/usr/bin/env python3
"""Record spec audit findings as beads in the project's bead store.

Creates:
- 12 CLOSED outcome beads for completed specs
- 5 OPEN planning beads for partially-complete specs with remaining items
- Links between related beads
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_baton.models.bead import Bead, BeadLink, _generate_bead_id
from agent_baton.core.engine.bead_store import BeadStore

DB_PATH = Path(".claude/team-context/baton.db").resolve()
TASK_ID = "spec-audit-2026-04-14"
AGENT = "orchestrator"
NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_bead(
    step_id: str,
    bead_type: str,
    content: str,
    tags: list[str],
    status: str = "open",
    scope: str = "project",
    confidence: str = "high",
    summary: str = "",
    affected_files: list[str] | None = None,
    bead_count: int = 0,
) -> Bead:
    bid = _generate_bead_id(TASK_ID, step_id, content, NOW, bead_count)
    closed_at = NOW if status == "closed" else ""
    return Bead(
        bead_id=bid,
        task_id=TASK_ID,
        step_id=step_id,
        agent_name=AGENT,
        bead_type=bead_type,
        content=content,
        confidence=confidence,
        scope=scope,
        tags=tags,
        affected_files=affected_files or [],
        status=status,
        created_at=NOW,
        closed_at=closed_at,
        summary=summary,
        links=[],
        source="manual",
        token_estimate=len(content) // 4,
    )


def main() -> None:
    store = BeadStore(DB_PATH)

    beads: list[Bead] = []
    n = 0

    # ── CLOSED SPECS (outcome beads) ──────────────────────────────────

    beads.append(make_bead(
        "audit.1", "outcome",
        "Knowledge Delivery spec COMPLETE. KnowledgeRegistry (518 LOC), KnowledgeResolver (452 LOC), "
        "KnowledgeGap (247 LOC), models/knowledge.py, dispatcher._build_knowledge_section(), "
        "planner integration. 4-layer resolution pipeline: explicit > agent-declared > tag-match > relevance fallback.",
        ["spec-closed", "knowledge-delivery", "knowledge-registry", "knowledge-resolver"],
        status="closed", summary="Knowledge delivery fully implemented with 4-layer resolution pipeline",
        affected_files=["agent_baton/core/orchestration/knowledge_registry.py",
                        "agent_baton/core/engine/knowledge_resolver.py",
                        "agent_baton/core/engine/knowledge_gap.py",
                        "agent_baton/models/knowledge.py"],
        bead_count=n,
    )); n += 1

    beads.append(make_bead(
        "audit.2", "outcome",
        "Intelligent Delegation spec COMPLETE. Outcome-oriented _STEP_TEMPLATES, _agent_expertise_level(), "
        "_extract_file_paths(), _SUCCESS_CRITERIA in dispatcher, StepResult.deviations field + extraction, "
        "retrospective integration for deviation feedback loop.",
        ["spec-closed", "intelligent-delegation", "planner", "dispatcher"],
        status="closed", summary="Intelligent delegation fully implemented with outcome-oriented templates and deviation protocol",
        affected_files=["agent_baton/core/engine/planner.py",
                        "agent_baton/core/engine/dispatcher.py",
                        "agent_baton/models/execution.py"],
        bead_count=n,
    )); n += 1

    beads.append(make_bead(
        "audit.3", "outcome",
        "Pipeline Gap Closure spec COMPLETE. All 8 bugs fixed: error handling (no bare except:pass), "
        "dual-write fallback, scanner SQLite awareness, execute list/switch SQLite, gate counting, "
        "trace save, E2E integration test (test_pipeline_e2e.py).",
        ["spec-closed", "pipeline-gap-closure", "executor", "sqlite"],
        status="closed", summary="All 8 pipeline bugs fixed with E2E test coverage",
        affected_files=["agent_baton/core/engine/executor.py",
                        "agent_baton/core/pmo/scanner.py",
                        "tests/test_pipeline_e2e.py"],
        bead_count=n,
    )); n += 1

    beads.append(make_bead(
        "audit.4", "outcome",
        "Concurrent Execution Isolation spec COMPLETE. BATON_TASK_ID env var resolution, "
        "export hint after start, Bound: field in status output, full test coverage.",
        ["spec-closed", "concurrent-isolation", "BATON_TASK_ID", "execute"],
        status="closed", summary="Session-scoped task binding via BATON_TASK_ID fully implemented",
        affected_files=["agent_baton/cli/commands/execution/execute.py"],
        bead_count=n,
    )); n += 1

    beads.append(make_bead(
        "audit.5", "outcome",
        "Federated Sync spec COMPLETE. SyncEngine with watermark-based incremental push, "
        "CentralStore as read replica, PMO migration to central.db, sync CLI (sync/status/--all/--rebuild).",
        ["spec-closed", "federated-sync", "sync-engine", "central-db"],
        status="closed", summary="Federated sync fully implemented with watermark-based incremental push",
        affected_files=["agent_baton/core/storage/sync.py",
                        "agent_baton/core/storage/central.py",
                        "agent_baton/core/storage/schema.py"],
        bead_count=n,
    )); n += 1

    beads.append(make_bead(
        "audit.6", "outcome",
        "Adaptive Plan Sizing spec COMPLETE. HaikuClassifier, KeywordClassifier, FallbackClassifier, "
        "planner integration, --complexity CLI flag, MachinePlan.complexity + classification_source fields.",
        ["spec-closed", "adaptive-plan-sizing", "classifier", "haiku"],
        status="closed", summary="Adaptive plan sizing fully implemented with Haiku classification and keyword fallback",
        affected_files=["agent_baton/core/engine/classifier.py",
                        "agent_baton/core/engine/planner.py",
                        "agent_baton/models/execution.py"],
        bead_count=n,
    )); n += 1

    beads.append(make_bead(
        "audit.7", "outcome",
        "Forge Smart Plan Generation spec COMPLETE. generate_interview(), regenerate_plan(), "
        "InterviewQuestion/InterviewAnswer models, ForgePanel/PlanEditor/InterviewPanel/AdoCombobox UI components, "
        "API routes (interview, regenerate, ado/search).",
        ["spec-closed", "forge", "plan-generation", "pmo-ui"],
        status="closed", summary="Forge smart plan generation fully implemented with interview loop and UI",
        affected_files=["agent_baton/core/pmo/forge.py",
                        "agent_baton/models/pmo.py",
                        "pmo-ui/src/components/ForgePanel.tsx"],
        bead_count=n,
    )); n += 1

    beads.append(make_bead(
        "audit.8", "outcome",
        "PMO UX Review spec COMPLETE. All 7 artifacts delivered: scenarios.md, workflow-audit.md, "
        "interaction-analysis.md, architecture-fitness.md, AUDIT.md, ISSUES.md, REMEDIATION-PLAN.md.",
        ["spec-closed", "pmo-ux-review", "audit"],
        status="closed", summary="PMO UX review complete with all 7 artifacts",
        affected_files=["docs/reviews/pmo-ux/AUDIT.md",
                        "docs/reviews/pmo-ux/ISSUES.md",
                        "docs/reviews/pmo-ux/REMEDIATION-PLAN.md"],
        bead_count=n,
    )); n += 1

    beads.append(make_bead(
        "audit.9", "outcome",
        "Bead Memory spec COMPLETE. Bead model, BeadStore (SQLite), BeadSignal parsing, BeadDecay, "
        "BeadSelector with tier-based ranking, full CLI (list/show/ready/close/link/cleanup/promote/graph).",
        ["spec-closed", "bead-memory", "beadstore", "agent-memory"],
        status="closed", summary="Bead memory system fully implemented with SQLite persistence and CLI",
        affected_files=["agent_baton/models/bead.py",
                        "agent_baton/core/engine/bead_store.py",
                        "agent_baton/core/engine/bead_signal.py",
                        "agent_baton/core/engine/bead_decay.py",
                        "agent_baton/core/engine/bead_selector.py",
                        "agent_baton/cli/commands/bead_cmd.py"],
        bead_count=n,
    )); n += 1

    beads.append(make_bead(
        "audit.10", "outcome",
        "Learning Automation spec COMPLETE. LearningLedger (SQLite CRUD), LearningEngine "
        "(detect/analyze/apply), LearningInterviewer, LearnedOverrides, schema migration v5, "
        "full CLI (learn status/issues/analyze/apply/interview/history/reset).",
        ["spec-closed", "learning-automation", "learning-ledger", "learning-engine"],
        status="closed", summary="Learning automation fully implemented with detect-analyze-apply cycle",
        affected_files=["agent_baton/models/learning.py",
                        "agent_baton/core/learn/ledger.py",
                        "agent_baton/core/learn/engine.py",
                        "agent_baton/core/learn/interviewer.py",
                        "agent_baton/core/learn/overrides.py"],
        bead_count=n,
    )); n += 1

    beads.append(make_bead(
        "audit.11", "outcome",
        "Functionality Audit spec COMPLETE. Audit executed, findings in docs/reviews/agent-feedback-audit-plan.md "
        "with 7 issues documented (planner limitations, engine gaps, platform gaps) and remediation priorities.",
        ["spec-closed", "functionality-audit", "audit"],
        status="closed", summary="Functionality audit complete with findings documented",
        affected_files=["docs/reviews/agent-feedback-audit-plan.md"],
        bead_count=n,
    )); n += 1

    beads.append(make_bead(
        "audit.12", "outcome",
        "TODO-001 Review Findings COMPLETE. All 8 issues fixed: rate-limit check, asyncio deprecation, "
        "status file handling, trace SQLite save, encapsulation violation, non-atomic save, stderr redaction.",
        ["spec-closed", "todo-001", "code-review-fixes"],
        status="closed", summary="All 8 TODO-001 review findings resolved",
        affected_files=["docs/internal/TODO-001-review-findings.md"],
        bead_count=n,
    )); n += 1

    # ── OPEN SPECS (planning beads) ───────────────────────────────────

    beads.append(make_bead(
        "audit.13", "planning",
        "Daemon Mode Roadmap ~45% complete. DONE: core daemon, supervisor, worker, launcher, --serve, --resume. "
        "OPEN: Phase 1 baton daemon run (plan+execute combo), Phase 2 inbound triggers (POST /api/v1/triggers), "
        "Phase 3 board-based approvals (DecisionManager exists, board rendering missing), "
        "Phase 4 runtime plan mutation triggers (amend exists, auto-triggers missing), "
        "Phase 5 cross-project coordination (MetaSupervisor not started), "
        "Phase 6 ActionType.INTERACT (iterative multi-turn steps not started), "
        "Phase 7 team cost prediction + MCP passthrough (BudgetTuner exists, cost pred + MCP missing).",
        ["spec-open", "daemon-mode", "roadmap", "daemon-run", "triggers", "interact"],
        status="open", scope="project", confidence="high",
        affected_files=["docs/specs/daemon-mode-roadmap.md",
                        "agent_baton/core/runtime/daemon.py",
                        "agent_baton/core/runtime/supervisor.py"],
        bead_count=n,
    )); n += 1

    beads.append(make_bead(
        "audit.14", "planning",
        "Agent Teams Enablement ~65% complete. DONE: TeamMember model, wave dispatch, "
        "record_team_member_result(), build_team_delegation_prompt(), StepScheduler, ContextManager. "
        "OPEN: team profiles (models defined, not in planner), team synthesis step (merge parallel outputs), "
        "conflict escalation protocol, decision propagation between parallel agents, "
        "reusable collaboration patterns (Challenge/Panel/Synthesis), runtime adaptive team composition.",
        ["spec-open", "agent-teams", "team-synthesis", "conflict-resolution", "collaboration-patterns"],
        status="open", scope="project", confidence="high",
        affected_files=["docs/specs/agent-teams-enablement.md",
                        "agent_baton/core/engine/planner.py",
                        "agent_baton/core/engine/executor.py",
                        "agent_baton/core/engine/dispatcher.py"],
        bead_count=n,
    )); n += 1

    beads.append(make_bead(
        "audit.15", "planning",
        "Interactive Team Journeys ~35% complete. Works via manual workarounds (amend, approve-with-feedback). "
        "BLOCKED BY: ActionType.INTERACT primitive (not started). "
        "OPEN: native iterative step execution, multi-turn conversational loop within a step, "
        "shared mutable artifact protocol between parallel agents, human-in-the-loop at sub-step granularity.",
        ["spec-open", "interactive-journeys", "actiontype-interact", "iterative-steps"],
        status="open", scope="project", confidence="high",
        affected_files=["docs/specs/interactive-team-journeys.md"],
        bead_count=n,
    )); n += 1

    beads.append(make_bead(
        "audit.16", "planning",
        "Sequenced Roadmap Epic 2 ~50% complete. DONE: Wave 3 (Trace Recorder, Context Profiler, "
        "Pattern Learner, Budget Auto-Tuner, baton package/publish). "
        "NOT STARTED: Wave 4 (Replay Engine, Run Differ, Meta-Optimizer), "
        "Wave 5 Safety (Prompt Regression, Canary Deployments).",
        ["spec-open", "roadmap-epic2", "wave4", "wave5", "replay-engine", "canary"],
        status="open", scope="project", confidence="high",
        affected_files=["reference_files/SEQUENCED-ROADMAP_epic2.md"],
        bead_count=n,
    )); n += 1

    beads.append(make_bead(
        "audit.17", "planning",
        "Production Readiness ~95% complete. OPEN: PMO UI advanced filtering (risk, agent, date range), "
        "distributed execution naming cleanup (experimental/ directory).",
        ["spec-open", "production-readiness", "pmo-ui-filtering", "naming-cleanup"],
        status="open", scope="project", confidence="high",
        affected_files=["docs/PRODUCTION_READINESS.md",
                        "pmo-ui/src/"],
        bead_count=n,
    )); n += 1

    # ── Insert synthetic execution record for FK constraint ─────────

    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT OR IGNORE INTO executions (task_id, status, current_phase, "
        "current_step_index, started_at, completed_at) "
        "VALUES (?, 'completed', 0, 0, ?, ?)",
        (TASK_ID, NOW, NOW),
    )
    conn.commit()
    conn.close()
    print(f"  ensured execution record for {TASK_ID}")

    # ── Write all beads ───────────────────────────────────────────────

    written = []
    for b in beads:
        bid = store.write(b)
        if bid:
            written.append(b)
            print(f"  wrote {bid}  [{b.status:6s}]  {b.summary or b.content[:60]}...")
        else:
            print(f"  FAILED to write bead for step {b.step_id}", file=sys.stderr)

    # ── Link related open beads ───────────────────────────────────────

    # Interactive journeys blocked by daemon Phase 6 (INTERACT)
    if len(written) >= 17:
        daemon_bead = written[12]   # audit.13 — daemon roadmap
        journeys_bead = written[14]  # audit.15 — interactive journeys
        teams_bead = written[13]     # audit.14 — agent teams

        store.link(journeys_bead.bead_id, daemon_bead.bead_id, "blocked_by")
        print(f"  linked {journeys_bead.bead_id} --blocked_by--> {daemon_bead.bead_id}")

        # Teams enablement relates to interactive journeys
        store.link(teams_bead.bead_id, journeys_bead.bead_id, "relates_to")
        print(f"  linked {teams_bead.bead_id} --relates_to--> {journeys_bead.bead_id}")

        # Teams enablement relates to daemon roadmap
        store.link(teams_bead.bead_id, daemon_bead.bead_id, "relates_to")
        print(f"  linked {teams_bead.bead_id} --relates_to--> {daemon_bead.bead_id}")

    print(f"\nDone: {len(written)} beads written, {len(written) - sum(1 for b in written if b.status == 'closed')} open, "
          f"{sum(1 for b in written if b.status == 'closed')} closed.")


if __name__ == "__main__":
    main()
