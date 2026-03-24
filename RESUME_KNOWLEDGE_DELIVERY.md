# Knowledge Delivery Implementation — Ready to Begin

## Design Doc
Read `docs/superpowers/specs/2026-03-24-knowledge-delivery-design.md` for the full architecture.

## What's Been Built (context for this work)

Since the design was written, the following infrastructure was added that the knowledge delivery system should integrate with:

### SQLite Storage (baton.db)
- Per-project `baton.db` at `.claude/team-context/baton.db` — 29 tables
- `SqliteStorage` in `agent_baton/core/storage/sqlite_backend.py` — full CRUD
- The `ExecutionEngine` now accepts an optional `storage` parameter for all I/O
- Knowledge registry/resolver data should be stored in baton.db (add tables via schema migration)

### Query API
- `agent_baton/core/storage/queries.py` — `QueryEngine` with typed functions
- `baton query` CLI — predefined + ad-hoc SQL queries
- `baton context briefing <agent>` — generates performance briefing for agents
- **Knowledge delivery should integrate here**: `baton context` could include relevant knowledge packs for the current task

### StorageBackend Protocol
- `agent_baton/core/storage/protocol.py` — formal interface both backends implement
- Knowledge resolution results could be persisted through this protocol

### Dispatcher (delegation prompts)
- `agent_baton/core/engine/dispatcher.py` — `PromptDispatcher.build_delegation_prompt()`
- This is where knowledge should be injected into agent prompts
- Currently inlines shared_context but NO knowledge pack content
- The knowledge resolver should hook into this method

### Retrospective Feedback Loop
- Retrospectives now capture `KnowledgeGap` objects with affected_agent + suggested_fix
- These feed back into the planner via `load_recent_feedback()`
- Knowledge delivery should consult these gaps when selecting packs

### Key Files to Read
- `agent_baton/core/engine/dispatcher.py` — where knowledge gets injected
- `agent_baton/core/engine/executor.py` — engine lifecycle (storage parameter)
- `agent_baton/core/storage/schema.py` — add knowledge tables here
- `agent_baton/core/storage/queries.py` — add knowledge queries here
- `agent_baton/core/orchestration/registry.py` — AgentRegistry pattern to follow
- `.claude/knowledge/` — existing knowledge packs on disk

### Test Suite
2828 tests passing. Run `python3 -m pytest --tb=short -q` to verify before starting.

## Build Order (from design doc)
1. KnowledgeRegistry — discover and index knowledge packs
2. KnowledgeResolver — match packs to agents/tasks with budget awareness
3. Dispatcher integration — inject resolved knowledge into delegation prompts
4. Runtime gap detection — agents signal missing knowledge
5. Tests + documentation
