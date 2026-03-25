# TODO: Code Review & Audit Findings — Proposal 001

**Date**: 2026-03-22 (original), 2026-03-25 (DX audit additions)
**Source**: Code Reviewer (Phase C2) + Security Auditor (Phase C3) + DX Audit (6 parallel agents)
**Status of build**: 3727 tests passing. DX Phase 1 fixes shipped.

---

## Fixed in This Session

- [x] **ISSUE 1** (HIGH): Rate-limit check missed 429 in `is_error=True` JSON path. Fixed: `_parse_output()` now includes both stderr and result_text in the error field.
- [x] **ISSUE 2** (MEDIUM): `asyncio.get_event_loop()` deprecated in Python 3.10+. Fixed: replaced with `asyncio.get_running_loop()` in `signals.py`.
- [x] **ISSUE 3** (MEDIUM): Status file always wrote "completed" even on signal/crash. Fixed: `supervisor.py` now passes the actual `summary` variable to `_write_status()`.

---

## Outstanding TODOs

### MEDIUM Priority

**TODO-1: Encapsulation violation in worker gate polling**
- File: `agent_baton/core/runtime/worker.py` (in `_handle_gate()`)
- Issue: Worker reads `self._decision_manager._resolution_path()` — accessing a private method from outside DecisionManager.
- Fix: Add `get_resolution(request_id) -> dict | None` public method to `DecisionManager` and call that instead.

**TODO-2: Engine `_save_state()` not atomic**
- File: `agent_baton/core/engine/executor.py:_save_state()`
- Issue: Uses `path.write_text()` — not atomic. A crash mid-write corrupts `execution-state.json`, making `resume()` and `recover_dispatched_steps()` unable to recover.
- Fix: Apply the same tmp+rename pattern used in `supervisor._write_status()`.
- Note: Pre-existing technical debt, not introduced by this PR.

**TODO-3: Stderr redaction for API key patterns**
- File: `agent_baton/core/runtime/claude_launcher.py`
- Issue: `LaunchResult.error` may contain API key prefixes (`sk-ant-*`) if the `claude` CLI echoes them in stderr during auth failures. The error string is persisted to `execution-state.json` and trace events without sanitization.
- Fix: Add `_redact_stderr(text: str) -> str` helper that strips `sk-ant-[A-Za-z0-9_-]+` patterns. Apply at lines 336 and 366.
- Source: Auditor condition A1-4 (NOT VERIFIED in post-execution audit).

### LOW Priority

**TODO-4: TOCTOU race + unhandled RuntimeError in daemon CLI**
- File: `agent_baton/cli/commands/daemon.py`
- Issue: The single-instance check (PID probe) has a TOCTOU race with the flock in `_write_pid()`. If another daemon starts between the check and the flock, `supervisor.start()` raises `RuntimeError` which is not caught by the CLI handler.
- Fix: Wrap `supervisor.start()` in `try/except RuntimeError` and print a clean user error.

**TODO-5: `start_new_session=True` for launched agents**
- File: `agent_baton/core/runtime/claude_launcher.py`
- Issue: `create_subprocess_exec` does not pass `start_new_session=True`. Launched agents inherit the daemon's session. Not a security issue (daemon already called `setsid`), but adding it would enable cleaner `killpg`-based cleanup.
- Fix: Add `start_new_session=True` to the `create_subprocess_exec` kwargs.

**TODO-6: `--plan` required even with `--resume`**
- File: `agent_baton/cli/commands/daemon.py`
- Issue: `--plan` is `required=True` on the `start` subparser, but when `--resume` is set the plan file is ignored (state is loaded from disk). Users must provide a dummy `--plan` arg on resume.
- Fix: Make `--plan` optional when `--resume` is set, or split into separate subcommands.

**TODO-7: `_handle_gate` polling interval hardcoded**
- File: `agent_baton/core/runtime/worker.py`
- Issue: The 2-second polling interval in `_handle_gate()` is hardcoded. Should be configurable via `DecisionManagerConfig` or worker constructor parameter.

**TODO-8: Redundant monkeypatch in Windows test**
- File: `tests/test_daemon.py:test_windows_raises_runtime_error`
- Issue: Patches both `sys.platform` and `daemon.sys.platform` — the first is redundant since the daemon module uses its own reference.
- Fix: Remove the `monkeypatch.setattr(sys, "platform", "win32")` line.

---

## DX Audit — Phase 1 Fixes (SHIPPED 2026-03-25)

14 findings implemented across 7 files (187 insertions, 36 deletions):

- [x] **F1** (HIGH): Prerequisite checks in install scripts — Python 3.10+, git
- [x] **F2** (HIGH): JSON/schema parsing error handling in execute.py
- [x] **F3** (HIGH): Silent sync `except: pass` → logged warning
- [x] **F4** (CRITICAL): BATON_TASK_ID printed before first action
- [x] **F5** (HIGH): next_action() RuntimeError caught with recovery hints
- [x] **F7** (HIGH): Progress prints during `baton plan`
- [x] **F8** (HIGH): First-run detection in `baton` (no args)
- [x] **F9** (HIGH): "Next: baton execute start" hint after plan save
- [x] **F10** (HIGH): Recovery hints on "No active execution"
- [x] **F19** (MEDIUM): Context-specific SQLite error messages in source_cmd.py
- [x] **F20** (MEDIUM): SQLite fallback promoted from debug to info log
- [x] **F22** (MEDIUM): --add-phase/--add-step input validation
- [x] **F23** (MEDIUM): step_id format validation (N.N pattern)
- [x] **F33** (LOW): assert → ValueError in _print_action()

## DX Audit — Phase 2 TODOs ("Guide the Developer")

**TODO-DX-9: CLI help restructure (F6)**
- File: `agent_baton/cli/main.py`
- Issue: 53 commands listed flat in `baton --help`. Core workflow buried.
- Fix: Group commands into sections (Core Workflow / Observability / Governance / Admin).

**TODO-DX-10: Consolidate execution loop docs (F11)**
- Files: `CLAUDE.md:104-128`, `references/baton-engine.md:524-625`
- Issue: Two different execution loop descriptions with subtle divergences.
- Fix: Pick canonical version in baton-engine.md, cross-reference from CLAUDE.md.

**TODO-DX-11: End-to-end worked example (F12)**
- File: New `docs/examples/first-run.md`
- Issue: No copy-paste-and-learn path for new users.
- Fix: Write complete worked example with real task, plan output, full execute loop.

**TODO-DX-12: Troubleshooting index (F13)**
- File: New `docs/troubleshooting.md`
- Issue: Troubleshooting scattered across 3 files.
- Fix: Create single-page decision tree linking to existing docs.

**TODO-DX-13: Settings.json schema docs (F14)**
- Issue: Users learn hook structure by trial/error.
- Fix: Document schema, supported hooks, mcpServers, env, permissions.

**TODO-DX-14: --step vs --step-id consistency (F16)**
- File: `agent_baton/cli/commands/execution/execute.py`
- Issue: Inconsistent aliases across execute subcommands.
- Fix: Standardize on --step-id everywhere.

## DX Audit — Phase 3 TODOs ("Polish the Experience")

See full findings matrix in the DX Audit Report (conversation artifact, 2026-03-25).
Items F18, F24-F32, F34-F38 cover centralized formatting, agent definition
consistency, documentation polish, version flag, color support, and exit codes.
