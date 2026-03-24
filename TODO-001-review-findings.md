# TODO: Code Review & Audit Findings — Proposal 001

**Date**: 2026-03-22
**Source**: Code Reviewer (Phase C2) + Security Auditor (Phase C3)
**Status of build**: 1977 tests passing. Shipped with fixes for top 3 issues.

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
