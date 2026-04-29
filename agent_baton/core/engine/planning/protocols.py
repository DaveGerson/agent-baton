"""Protocols for the planning pipeline.

Each stage implements ``Stage``: takes a ``PlanDraft`` + ``PlannerServices``,
returns a ``PlanDraft``.  Stages are pure with respect to their inputs —
they read from the draft + services and produce a new (or mutated) draft.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .draft import PlanDraft
from .services import PlannerServices


@runtime_checkable
class Stage(Protocol):
    """A pipeline stage.

    Stages run in order; each receives the draft produced by the previous
    stage.  A stage may mutate the draft in place or return a new one —
    callers always use the returned value.
    """

    name: str

    def run(self, draft: PlanDraft, services: PlannerServices) -> PlanDraft: ...
