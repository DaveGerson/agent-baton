"""PMO subsystem — portfolio management overlay for orchestration plans.

Provides three main components:

``PmoStore`` / ``PmoSqliteStore``
    Persistence layer for registered projects, programs, cross-project
    signals, archived cards, forge sessions, and PMO metrics.

``PmoScanner``
    Scans registered projects to build Kanban board state (queued,
    in-progress, awaiting-human, deployed) and computes per-program
    health metrics.

``ForgeSession``
    Smart Forge — an interactive, interview-driven plan creation workflow
    that delegates to ``IntelligentPlanner`` for plan generation and adds
    structured refinement through targeted questions.
"""
