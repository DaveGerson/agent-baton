"""User identity middleware for the Agent Baton PMO API.

Resolves a caller's identity from each incoming request and stores it in
``request.state.user_id`` so route handlers can record it in the
``approval_log`` table without re-parsing headers.

Resolution order
----------------
1. ``X-Baton-User`` header — explicit user ID from trusted upstream proxy
   or direct API caller.
2. ``Authorization: Bearer <token>`` header — the token value is used as
   the user ID when present (simple single-token deployments).
3. Fallback — ``"local-user"`` in ``local`` approval mode (the default),
   which grants admin access without authentication.  In ``team`` mode
   there is no synthetic fallback: an unidentified caller resolves to an
   empty ``user_id`` so route-level segregation-of-duties checks (e.g.
   "approver must differ from submitter") cannot be bypassed by simply
   omitting the header.

Approval modes (``BATON_APPROVAL_MODE`` env var)
-------------------------------------------------
``local`` (default)
    The creator is also the approver.  Self-approval is permitted.
    Missing identity falls back to ``"local-user"`` / ``"admin"`` role.

``team``
    A different ``user_id`` is required to approve a decision than the
    one who created the task.  The middleware still resolves identity the
    same way, except it never mints a synthetic identity for an
    unauthenticated caller; enforcement of the team rule (including
    rejecting missing identity) is done in the route handler.
"""
from __future__ import annotations

import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class UserIdentityMiddleware(BaseHTTPMiddleware):
    """Inject ``request.state.user_id`` and ``request.state.user_role``
    into every request.

    The middleware is intentionally lightweight: it never touches the
    database and never blocks.  Role lookup happens in the route layer
    when the full ``users`` table record is needed.

    Attributes:
        approval_mode: Either ``"local"`` or ``"team"``.  Stored as an
            instance attribute so tests can override it without touching
            the environment.
    """

    def __init__(self, app, approval_mode: str | None = None) -> None:
        super().__init__(app)
        # Read the environment at construction time (not import time), so a
        # process that sets BATON_APPROVAL_MODE before create_app() actually
        # gets the mode it asked for. The constructor arg still wins when
        # given explicitly (tests rely on this to override the environment).
        default_mode = os.environ.get("BATON_APPROVAL_MODE", "local").lower()
        self.approval_mode = (approval_mode or default_mode).lower()

    async def dispatch(self, request: Request, call_next) -> Response:
        """Resolve user identity and store it in ``request.state``.

        Args:
            request: The incoming Starlette/FastAPI request.
            call_next: The next middleware or route handler in the stack.

        Returns:
            The response from the downstream handler.
        """
        user_id = self._resolve_user_id(request)
        request.state.user_id = user_id
        # In local mode the resolved identity always has admin rights.
        # In team mode route handlers must verify the role via the DB.
        request.state.user_role = "admin" if self.approval_mode == "local" else ""
        request.state.approval_mode = self.approval_mode
        return await call_next(request)

    def _resolve_user_id(self, request: Request) -> str:
        """Extract a user identifier from the request.

        Resolution order:
        1. ``X-Baton-User`` header
        2. ``Authorization: Bearer <token>`` (token used as user ID)
        3. ``"local-user"`` fallback — ``local`` approval mode only

        Args:
            request: The incoming HTTP request.

        Returns:
            A non-empty string identifying the caller in ``local`` mode.
            In ``team`` mode, an empty string when no identity was
            presented — callers must never receive a usable synthetic
            identity that could satisfy an approver-differs-from-submitter
            check.
        """
        # 1. Explicit header set by a trusted upstream.
        user_header = request.headers.get("X-Baton-User", "").strip()
        if user_header:
            return user_header

        # 2. Bearer token — use the token value as the user ID.
        auth_header = request.headers.get("Authorization", "").strip()
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
            if token:
                return token

        # 3. Local-mode fallback. Team (and any other non-local) mode must
        # not mint a synthetic identity for an unauthenticated caller.
        if self.approval_mode == "local":
            return "local-user"
        return ""
