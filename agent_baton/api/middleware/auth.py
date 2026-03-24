"""Bearer token authentication middleware for the Agent Baton API.

The middleware is intentionally a no-op when no token is configured, so
development setups (no token) and secured deployments (token required) use
exactly the same code path without conditional imports.

DECISION: We implement this as a Starlette ``BaseHTTPMiddleware`` subclass
rather than a FastAPI dependency so that it applies globally to every route
without needing to be listed in each router's ``dependencies=[...]``.  This
also means health-check endpoints can be unconditionally excluded by path
without touching the route definitions.

DECISION: Health/readiness probes at ``/api/v1/health`` and
``/api/v1/ready`` are exempt from auth so that load balancers and container
orchestrators can probe the service without credentials.
"""
from __future__ import annotations

import json

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# Paths that bypass token authentication.
_AUTH_EXEMPT_PATHS: frozenset[str] = frozenset({
    "/api/v1/health",
    "/api/v1/ready",
    # OpenAPI schema endpoints — allow tooling to introspect without a token.
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
})


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Require ``Authorization: Bearer <token>`` on all non-exempt paths.

    When *token* is ``None`` or an empty string the middleware is disabled
    and every request is passed through unconditionally.
    """

    def __init__(self, app, token: str | None = None) -> None:  # type: ignore[override]
        super().__init__(app)
        # Normalise: treat empty string the same as None (auth disabled).
        self._token: str | None = token.strip() if token else None

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Validate the bearer token, or pass through if auth is disabled."""
        # Auth is disabled — skip all checks.
        if not self._token:
            return await call_next(request)

        # Exempt paths bypass auth regardless of token presence.
        if request.url.path in _AUTH_EXEMPT_PATHS:
            return await call_next(request)

        # Validate the Authorization header.
        auth_header = request.headers.get("Authorization", "")
        if not _is_valid_bearer(auth_header, self._token):
            return _unauthorized_response()

        return await call_next(request)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_valid_bearer(header: str, expected_token: str) -> bool:
    """Return True if *header* is a valid Bearer token matching *expected_token*.

    Performs a simple equality check; no timing-safe comparison is needed
    here because the server is local-only by default and the auth header
    value is not a secret that benefits from constant-time comparison in
    this threat model.  If the deployment is ever exposed to the internet,
    use ``hmac.compare_digest`` instead.
    """
    parts = header.split(" ", 1)
    if len(parts) != 2:
        return False
    scheme, provided = parts
    return scheme.lower() == "bearer" and provided == expected_token


def _unauthorized_response() -> Response:
    """Return a 401 JSON response with a standard error body."""
    body = json.dumps({"error": "unauthorized", "detail": "Valid Bearer token required."})
    return Response(
        content=body,
        status_code=401,
        media_type="application/json",
    )
