"""Events sub-package — event bus, domain events, persistence, projections."""
from __future__ import annotations

from agent_baton.models.events import Event
from agent_baton.core.events.bus import EventBus
from agent_baton.core.events.persistence import EventPersistence
from agent_baton.core.events.projections import (
    TaskView,
    PhaseView,
    StepView,
    project_task_view,
)

__all__ = [
    "Event",
    "EventBus",
    "EventPersistence",
    "TaskView",
    "PhaseView",
    "StepView",
    "project_task_view",
]
