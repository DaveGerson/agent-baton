"""Route package for the Agent Baton API.

Route modules are registered via ``agent_baton.api.server._ROUTE_MODULES``
and lazily imported inside ``create_app()`` to maintain optional-dependency
isolation.  Nothing is imported at the package level so that a missing
optional dependency (e.g. ``sse-starlette`` for the events SSE route)
does not prevent the rest of the API from starting.

Available route modules:

- ``health`` -- liveness and readiness probes (no auth required)
- ``plans`` -- plan creation and retrieval
- ``executions`` -- execution lifecycle (start, record, gate, complete, cancel)
- ``agents`` -- agent registry queries
- ``observe`` -- dashboard, traces, and usage records
- ``decisions`` -- human-in-the-loop decision management
- ``events`` -- SSE event streaming
- ``webhooks`` -- outbound webhook subscription CRUD
- ``pmo`` -- portfolio management office (board, projects, forge, signals)

All routes are mounted under the ``/api/v1`` prefix.
"""
