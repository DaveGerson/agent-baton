"""CLI command group: finops.

Read-side cost reporting that joins the F0.2 tenancy hierarchy with the
existing ``usage_records`` / ``agent_usage`` tables.  Velocity-zero --
no schema mutations, no writes to usage tables.
"""
from __future__ import annotations
