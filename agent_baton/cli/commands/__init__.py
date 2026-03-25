"""CLI command plugin package for Agent Baton.

This package serves as the root namespace for all ``baton`` subcommands.
Commands are auto-discovered by :func:`~agent_baton.cli.main.discover_commands`
at startup -- any module here or in a sub-package that exposes both
``register(subparsers)`` and ``handler(args)`` is registered as a
subcommand.

Sub-packages group commands by functional domain:

* ``execution/`` -- Plan creation, execution loop, daemon, async dispatch.
* ``observe/`` -- Tracing, telemetry, dashboards, usage, context queries.
* ``govern/`` -- Classification, compliance, policy, validation.
* ``improve/`` -- Scoring, evolution, patterns, budget, experiments.
* ``distribute/`` -- Packaging, publishing, registry, install, transfer.
* ``agents/`` -- Agent listing, routing, events, incidents.

Standalone modules in this package (``pmo_cmd``, ``sync_cmd``,
``query_cmd``, ``source_cmd``, ``serve``) implement commands that
span multiple domains or manage cross-project infrastructure.
"""
from __future__ import annotations
