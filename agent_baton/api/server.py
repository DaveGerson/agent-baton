"""FastAPI application factory for the Agent Baton API.

The public entry point is :func:`create_app`.  It wires up:

- Dependency injection (via :mod:`agent_baton.api.deps`)
- CORS (via :mod:`agent_baton.api.middleware.cors`)
- Optional Bearer token auth (via :mod:`agent_baton.api.middleware.auth`)
- Route modules (plans, executions, agents, health, observe)

The caller is responsible for running the returned app with uvicorn::

    import uvicorn
    from agent_baton.api.server import create_app

    app = create_app(host="127.0.0.1", port=8741, token="secret")
    uvicorn.run(app, host="127.0.0.1", port=8741)

DECISION: ``create_app`` accepts ``host`` and ``port`` only for informational
purposes (they appear in the OpenAPI spec ``servers`` list).  The actual
network binding is done by the uvicorn caller, not by this factory.  This
keeps the factory pure and side-effect-free.

DECISION: Route modules are imported inside ``create_app`` rather than at
module level to prevent import-time errors when optional route dependencies
(e.g. ``sse-starlette``) are absent.  This makes the import of ``server.py``
itself safe even if only a subset of optional deps is installed.
"""
from __future__ import annotations

import importlib
from pathlib import Path

from fastapi import FastAPI

from agent_baton.api.deps import init_dependencies, get_bus, get_webhook_registry
from agent_baton.api.middleware.auth import TokenAuthMiddleware
from agent_baton.api.middleware.cors import configure_cors
from agent_baton.api.middleware.user_identity import UserIdentityMiddleware
from agent_baton.core.events.bus import EventBus

try:
    from agent_baton import __version__ as _VERSION
except ImportError:
    _VERSION = "0.1.0"

# ---------------------------------------------------------------------------
# Route module registry
# ---------------------------------------------------------------------------

# Each entry is (module_path, attribute_name, url_prefix, tags).
# Modules are imported lazily inside create_app so missing optional deps
# raise ImportError only if the route is actually registered.
_ROUTE_MODULES: list[tuple[str, str, str, list[str]]] = [
    ("agent_baton.api.routes.health", "router", "/api/v1", ["health"]),
    # Prometheus scrape endpoint mounts at root (/metrics), not /api/v1,
    # to match the standard Prometheus convention.
    ("agent_baton.api.routes.metrics", "router", "", ["metrics"]),
    ("agent_baton.api.routes.plans", "router", "/api/v1", ["plans"]),
    ("agent_baton.api.routes.executions", "router", "/api/v1", ["executions"]),
    ("agent_baton.api.routes.agents", "router", "/api/v1", ["agents"]),
    ("agent_baton.api.routes.observe", "router", "/api/v1", ["observe"]),
    ("agent_baton.api.routes.decisions", "router", "/api/v1", ["decisions"]),
    ("agent_baton.api.routes.events", "router", "/api/v1", ["events"]),
    ("agent_baton.api.routes.webhooks", "router", "/api/v1", ["webhooks"]),
    ("agent_baton.api.routes.pmo", "router", "/api/v1", ["pmo"]),
    ("agent_baton.api.routes.learn", "router", "/api/v1", ["learn"]),
    ("agent_baton.api.routes.specs", "router", "/api/v1", ["specs"]),
    ("agent_baton.api.routes.noc", "router", "/api/v1", ["noc"]),
]


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(
    host: str = "127.0.0.1",
    port: int = 8741,
    token: str | None = None,
    team_context_root: Path | None = None,
    allowed_origins: list[str] | None = None,
    bus: EventBus | None = None,
) -> FastAPI:
    """Create and configure the Agent Baton FastAPI application.

    Args:
        host: Hostname the server will listen on.  Used in the OpenAPI
            ``servers`` entry only; actual binding is done by uvicorn.
        port: Port the server will listen on.  Same note as *host*.
        token: If provided, every non-exempt request must include an
            ``Authorization: Bearer <token>`` header.  Pass ``None`` (the
            default) to disable authentication entirely — appropriate for
            local development.
        team_context_root: Absolute path to the team-context directory.
            Defaults to ``Path(".claude/team-context")`` relative to the
            working directory at startup.
        allowed_origins: CORS allowed origins.  ``None`` permits all
            localhost / 127.0.0.1 origins on any port.  Pass ``["*"]`` to
            allow all origins.
        bus: Optional pre-constructed :class:`~agent_baton.core.events.bus.EventBus`
            to share with the app's dependencies.  When ``None`` a new bus
            is created internally.

    Returns:
        A configured :class:`fastapi.FastAPI` instance ready to be served.
    """
    # Resolve team-context root.
    root = team_context_root or Path(".claude/team-context")

    # Initialise dependency singletons before any route is registered.
    init_dependencies(team_context_root=root, bus=bus)

    # Wire the WebhookDispatcher to the shared EventBus so outbound
    # deliveries start as soon as the app is ready.
    from agent_baton.api.webhooks.dispatcher import WebhookDispatcher

    _shared_bus = get_bus()
    _webhook_reg = get_webhook_registry()
    _webhook_dispatcher = WebhookDispatcher(  # noqa: F841 — held alive by the bus subscription
        registry=_webhook_reg,
        bus=_shared_bus,
        failures_path=root / "webhook-failures.jsonl",
    )

    # Build the FastAPI app.
    app = FastAPI(
        title="Agent Baton API",
        version=_VERSION,
        description=(
            "HTTP API for the Agent Baton multi-agent orchestration system. "
            "Wraps the core engine, planner, registry, and observability stack."
        ),
        servers=[{"url": f"http://{host}:{port}", "description": "Local daemon"}],
        # Disable the default /docs redirect to avoid CORS complications when
        # the UI is loaded from a different origin.
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # --- Middleware (order matters: last added = outermost wrapper) ----------

    # CORS must be added before auth so pre-flight OPTIONS requests are
    # answered without requiring a token.
    configure_cors(app, allowed_origins=allowed_origins)

    # Auth middleware — no-op when token is None.
    app.add_middleware(TokenAuthMiddleware, token=token)

    # User identity middleware — resolves X-Baton-User header or Bearer token
    # into request.state.user_id.  In local approval mode (BATON_APPROVAL_MODE
    # unset or "local") falls back to "local-user" with admin role.
    app.add_middleware(UserIdentityMiddleware)

    # --- Routes --------------------------------------------------------------

    _register_routes(app)

    # Serve PMO UI static files if built assets exist
    _pmo_dist = Path(__file__).resolve().parent.parent.parent / "pmo-ui" / "dist"
    if _pmo_dist.is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount("/pmo", StaticFiles(directory=str(_pmo_dist), html=True), name="pmo-ui")

    return app


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _register_routes(app: FastAPI) -> None:
    """Import and include each route module.

    Missing modules are skipped with a warning rather than crashing the
    server.  This allows the server to start even if a route module has an
    unresolved optional dependency (e.g. ``sse-starlette`` for SSE routes).
    """
    import logging
    _log = logging.getLogger(__name__)

    for module_path, attr, prefix, tags in _ROUTE_MODULES:
        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:
            _log.warning(
                "Skipping route module %s — import failed: %s",
                module_path,
                exc,
            )
            continue

        router = getattr(module, attr, None)
        if router is None:
            _log.warning(
                "Route module %s has no attribute '%s'; skipping.",
                module_path,
                attr,
            )
            continue

        app.include_router(router, prefix=prefix, tags=tags)
