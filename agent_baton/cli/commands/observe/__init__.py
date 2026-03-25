"""CLI command group: observe.

Commands for inspecting execution artifacts, monitoring agent behaviour,
and querying historical data.  The observe group surfaces everything that
happens during and after orchestrated tasks.

Commands:
    * ``baton dashboard`` -- Generate or display the usage dashboard.
    * ``baton trace`` -- List and inspect structured task execution traces.
    * ``baton usage`` -- Show usage statistics from the usage log.
    * ``baton telemetry`` -- Show or clear agent telemetry events.
    * ``baton retro`` -- Show retrospectives and extract recommendations.
    * ``baton context-profile`` -- Agent context efficiency profiling.
    * ``baton cleanup`` -- Archive or remove old execution artifacts.
    * ``baton migrate-storage`` -- Migrate JSON/JSONL files to SQLite.
    * ``baton query`` -- Typed and ad-hoc queries against baton.db.
    * ``baton context`` -- Situational awareness for dispatched agents.
"""
from __future__ import annotations
