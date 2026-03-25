"""CLI command group: agents.

Commands for discovering available agents, routing roles to
stack-specific agent flavors, querying the domain event log, and
managing incident response workflows.

Commands:
    * ``baton agents`` -- List available agents grouped by category.
    * ``baton route`` -- Route roles to agent flavors based on detected stack.
    * ``baton events`` -- Query the event log for a task.
    * ``baton incident`` -- Manage incident response workflows.
"""
from __future__ import annotations
