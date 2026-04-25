"""Knowledge effectiveness scoring + ROI helpers (K2.x roadmap).

This subpackage hosts the read-side analytics for knowledge documents:
attachment counts, success rates, ROI per kilo-token, and stale-doc
detection.  It is intentionally pure-stdlib and *read-only* with respect
to telemetry sources — the consuming pipeline (K2.3) handles deletion.

See ``effectiveness.py`` for the public API:
    - ``compute_effectiveness``
    - ``find_stale_docs``
    - ``DocEffectiveness`` / ``StaleDoc`` dataclasses
    - ``KnowledgeTelemetryStore`` protocol (default impl reads baton.db)
"""
from __future__ import annotations
