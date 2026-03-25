"""Observe sub-package -- the data-collection layer of the closed-loop learning pipeline.

This package captures raw execution signals that downstream consumers
(:mod:`~agent_baton.core.learn` and :mod:`~agent_baton.core.improve`) use
to derive patterns, tune budgets, and evolve agent prompts.

Key responsibilities:

* **Trace recording** -- structured DAG of timestamped events per task,
  persisted as JSON files (``TraceRecorder``, ``TraceRenderer``).
* **Usage logging** -- append-only JSONL log of per-task resource
  consumption: agents used, tokens, retries, gate results (``UsageLogger``).
* **Telemetry** -- fine-grained tool-call events emitted in real time
  during agent execution (``AgentTelemetry``).
* **Retrospective generation** -- qualitative post-task analysis that
  merges explicit and implicit knowledge gaps, agent outcomes, and roster
  recommendations (``RetrospectiveEngine``).
* **Dashboard generation** -- aggregates usage and telemetry data into a
  Markdown dashboard for human review (``DashboardGenerator``).
* **Context profiling** -- analyses per-agent file I/O from traces to
  compute context-efficiency metrics and flag wasteful reading
  (``ContextProfiler``).

Data flow::

    Execution runtime
        |
        +--> TraceRecorder      (structured event DAG)
        +--> UsageLogger         (per-task resource summary)
        +--> AgentTelemetry      (real-time tool-call stream)
        |
        v
    RetrospectiveEngine          (post-task qualitative analysis)
        |
        v
    learn.PatternLearner         (pattern extraction from usage log)
    learn.BudgetTuner            (budget-tier recommendations)
    improve.PerformanceScorer    (agent scorecards from usage + retros)
"""
from __future__ import annotations

from agent_baton.core.observe.usage import UsageLogger
from agent_baton.core.observe.telemetry import AgentTelemetry, TelemetryEvent
from agent_baton.core.observe.retrospective import RetrospectiveEngine
from agent_baton.core.observe.dashboard import DashboardGenerator
from agent_baton.core.observe.trace import TraceRecorder, TraceRenderer
from agent_baton.core.observe.context_profiler import ContextProfiler

__all__ = [
    "UsageLogger",
    "AgentTelemetry",
    "TelemetryEvent",
    "RetrospectiveEngine",
    "DashboardGenerator",
    "TraceRecorder",
    "TraceRenderer",
    "ContextProfiler",
]
