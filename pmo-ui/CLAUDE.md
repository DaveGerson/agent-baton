# pmo-ui/ — React/Vite frontend

Served at `/pmo/` by the FastAPI app. Cross-cutting rules: [../CLAUDE.md](../CLAUDE.md).

## Stack

- **Vite** + **React** + **TypeScript**.
- **Vitest** for unit tests; **Playwright** for E2E (`e2e/`).
- API client lives in `src/api/` and talks to `agent_baton/api/routes/`.

## Layout

| Path | Role |
|------|------|
| `src/views/` | Top-level pages routed in `App.tsx` |
| `src/components/` | Reusable UI components (Kanban board, panels, dialogs) |
| `src/api/` | Typed clients for the backend (`client.ts`, `beads.ts`, `workforce.ts`, `types.ts`) |
| `src/hooks/` | React hooks |
| `src/contexts/` | React contexts |
| `src/utils/` | UI-only helpers |
| `src/styles/` | Styling |
| `src/test/` | Test setup |
| `e2e/` | Playwright specs |

## Conventions

- Generated API types live in `src/api/types.ts`. Don't hand-edit shapes that mirror backend models — regenerate or edit the matching `agent_baton/api/models/*.py` and re-derive.
- Components are functional + hooks. No class components.
- One component per file. File name matches the exported component (PascalCase).
- Tests for `Component.tsx` go in `__tests__/Component.test.tsx` next to the component, not in a separate tree.

## Running locally

```bash
npm install
npm run dev          # Vite dev server
npm test             # Vitest
npm run e2e          # Playwright (requires the FastAPI app running)
```

## Backend coupling

When you change a backend route in `agent_baton/api/routes/`:

1. Regenerate or update `src/api/types.ts`.
2. Update the matching client method in `src/api/`.
3. Update the consuming view/component.
4. Document the wire change in [../docs/api-reference.md](../docs/api-reference.md).

## Docs touchpoints

UI behavior visible to users belongs in [../docs/storage-sync-and-pmo.md](../docs/storage-sync-and-pmo.md) and the relevant `docs/architecture/` page.
