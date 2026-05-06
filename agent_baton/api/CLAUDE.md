# agent_baton/api/ — FastAPI surface

Backs the REST API and the PMO UI (`/pmo/`). Cross-cutting rules: [../../CLAUDE.md](../../CLAUDE.md).

## Layout

| Path | Role |
|------|------|
| `server.py` | FastAPI app factory; mounts routers, middleware, static UI |
| `deps.py` | Dependency-injection helpers (DB session, auth principal, settings) |
| `routes/` | One file per resource — `agents`, `decisions`, `events`, `executions`, `health`, `learn`, `metrics`, `noc`, `observe`, `plans`, `pmo`, `pmo_h3`, `specs`, `viz`, `webhooks` |
| `middleware/` | `auth`, `cors`, `user_identity` |
| `webhooks/` | Outbound webhook `dispatcher`, `payloads`, `registry` |
| `models/` | Request/response Pydantic models specific to the HTTP layer |

## Conventions

- Each router lives in its own `routes/<resource>.py` and is registered in `server.py`. Don't combine resources into one file.
- HTTP-shape models go in `api/models/`. Do not return raw `agent_baton.models` types — wrap them so the engine can change without breaking the wire.
- Auth is enforced via `middleware/auth.py` plus the `current_user` dependency in `deps.py`. Do not check headers manually inside routes.
- All routes are async. Block work goes through the engine's executor, not directly in the request handler.
- Errors raise `HTTPException` with a stable error code from `agent_baton.cli.errors` — UI relies on these codes.

## When you change a route

- Update [docs/api-reference.md](../../docs/api-reference.md).
- Add an integration test under `tests/api/` (or one of the `tests/test_api_*.py` files at the top level).
- If the PMO UI consumes the route, update the matching client in `pmo-ui/src/api/`.

## Webhooks

Outbound webhooks are dispatched by `webhooks/dispatcher.py` from engine events. The `registry` lists known event topics — adding a new event means: define the payload in `webhooks/payloads.py`, register the topic, and document it in `docs/api-reference.md`.
