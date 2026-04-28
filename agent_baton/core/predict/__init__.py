"""Wave 6.2 Part C — Zero-Latency Predictive Computation (bd-03b0).

Exports the public surface of the predict package.

Feature is disabled by default.  Set ``BATON_PREDICT_ENABLED=1`` (and install
the ``[predict]`` optional extra) to activate.

Submodules:
    watcher    — cross-platform FS watcher with privacy gate and debounce.
    classifier — Haiku intent classifier with prompt-cached project context.
    speculator — speculative dispatcher with eviction, pruning, budget cap.
    accept     — Wave 5.3 join-point: handoff_to_pipeliner().
"""
from __future__ import annotations

from agent_baton.core.predict.watcher import FileEvent, FileWatcher
from agent_baton.core.predict.classifier import (
    IntentClassification,
    IntentClassifier,
    IntentKind,
)
from agent_baton.core.predict.speculator import PredictiveDispatcher, Speculation
from agent_baton.core.predict.accept import handoff_to_pipeliner

__all__ = [
    # watcher
    "FileEvent",
    "FileWatcher",
    # classifier
    "IntentKind",
    "IntentClassification",
    "IntentClassifier",
    # speculator
    "Speculation",
    "PredictiveDispatcher",
    # accept
    "handoff_to_pipeliner",
]
