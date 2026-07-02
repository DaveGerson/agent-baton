"""Session/auth stub -- intentionally minimal.

Fixture module: the manager-mode planning E2E's "Add a reporting
endpoint" task must not accidentally pull auth into its scope map. This
stub exists so the fixture repo has more than one top-level app/
subpackage to disambiguate against.
"""
from __future__ import annotations


def current_session_id() -> str | None:
    """Stub -- no real session backing store in this fixture."""
    return None
