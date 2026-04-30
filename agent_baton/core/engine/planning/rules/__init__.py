"""Pure-data lookup tables for the planner.

These modules contain ONLY data — no behavior, no I/O, no LLM calls.
Stages import from here; legacy modules (``planner.py``,
``_planner_helpers.py``, ``strategies.py``) re-export from here while
they exist, then those modules are deleted.

Submodules
----------

* :mod:`.risk_signals`   — keyword → ``RiskLevel`` mapping + ordinal
* :mod:`.default_agents` — task-type → default agent roster
* :mod:`.phase_templates`— task-type → phase name templates + verbs
* :mod:`.phase_roles`    — phase → ideal/blocked agent roles + fallbacks
* :mod:`.concerns`       — concern marker regex + signal table
* :mod:`.step_types`     — agent → step_type mapping
* :mod:`.templates`      — agent×phase → step description templates
"""
from __future__ import annotations
