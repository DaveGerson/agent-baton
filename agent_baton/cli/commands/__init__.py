"""CLI command plugin package for Agent Baton.

This package serves as the root namespace for all baton subcommands.
Commands are auto-discovered by main.discover_commands() at startup.

Sub-packages group commands by functional domain:

* execution/ -- Plan creation, execution loop, daemon, async dispatch.
* observe/ -- Tracing, telemetry, dashboards, usage, context queries.
* govern/ -- Classification, compliance, policy, validation.
* improve/ -- Scoring, patterns, budget, anomalies, conflicts.
* distribute/ -- Packaging, publishing, registry, install, transfer.
* agents/ -- Agent listing, routing, events, incidents.

Standalone modules (pmo_cmd, sync_cmd, query_cmd, source_cmd, serve)
implement commands that span multiple domains.
"""
from __future__ import annotations
