"""CLI command group: govern.

Commands for policy enforcement, risk classification, compliance
reporting, and validation.  The govern group ensures that orchestrated
tasks stay within safety and quality boundaries.

Commands:
    * ``baton classify`` -- Classify task sensitivity and select guardrails.
    * ``baton compliance`` -- Show compliance reports.
    * ``baton policy`` -- List, show, or evaluate guardrail policy presets.
    * ``baton escalations`` -- Show, resolve, or clear agent escalations.
    * ``baton validate`` -- Validate agent definition ``.md`` files.
    * ``baton spec-check`` -- Validate agent output against a spec.
    * ``baton detect`` -- Detect the project technology stack.
"""
from __future__ import annotations
