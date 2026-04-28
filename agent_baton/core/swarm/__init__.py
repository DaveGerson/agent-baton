"""Wave 6.2 Part A — Massive Swarm Refactoring (bd-707d).

Public surface of the swarm package. Feature-gated by BATON_SWARM_ENABLED=1
in baton.yaml; disabled by default.
"""
from __future__ import annotations

from agent_baton.core.swarm.coalescer import CoalesceResult, Coalescer
from agent_baton.core.swarm.dispatcher import (
    SwarmBudgetError,
    SwarmDispatcher,
    SwarmResult,
)
from agent_baton.core.swarm.partitioner import (
    ASTPartitioner,
    CallSite,
    ChangeSignature,
    CodeChunk,
    MigrateAPI,
    ProofRef,
    ReconcileResult,
    RefactorDirective,
    RenameSymbol,
    ReplaceImport,
    ScopeKind,
)
from agent_baton.core.swarm.reconciler import ConflictReconciler

__all__ = [
    # partitioner
    "ASTPartitioner",
    "CallSite",
    "ChangeSignature",
    "CodeChunk",
    "MigrateAPI",
    "ProofRef",
    "ReconcileResult",
    "RefactorDirective",
    "RenameSymbol",
    "ReplaceImport",
    "ScopeKind",
    # dispatcher
    "SwarmBudgetError",
    "SwarmDispatcher",
    "SwarmResult",
    # coalescer
    "CoalesceResult",
    "Coalescer",
    # reconciler
    "ConflictReconciler",
]
