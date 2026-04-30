"""Stateless utility functions extracted from the legacy planner.

Each module groups related pure functions that stages import directly.
Functions accept explicit arguments (draft fields, services) instead of
``self`` — they can be reused by the runtime engine for dynamic
replanning without instantiating the full planner.
"""
from __future__ import annotations
