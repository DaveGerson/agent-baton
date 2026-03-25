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

## Pipeline Bug Fixes

**[x] Pipeline Bug 7: Trace not saved to SQLite in complete()**
- File: `agent_baton/core/engine/executor.py`, `complete()` method
- Issue: `complete_trace()` wrote the trace to the filesystem only. No call to `self._storage.save_trace()` existed anywhere in the engine.
- Fix: After `complete_trace()` in `complete()`, added a try/except block that calls `self._storage.save_trace(finished_trace)` when `self._storage` is set. Uses the same log-and-fallback pattern as `_save_execution()` — logs a warning on failure, never raises.
- Tests: `tests/test_executor.py::TestCompleteSavesTraceToSQLite` (3 tests: save_trace called once with correct task_id, file-only mode still writes to disk, storage failure does not crash complete())

---

## Outstanding TODOs

### MEDIUM Priority

**[x] TODO-1: Encapsulation violation in worker gate polling**
- File: `agent_baton/core/runtime/worker.py` (in `_handle_gate()`)
- Issue: Worker reads `self._decision_manager._resolution_path()` — accessing a private method from outside DecisionManager.
- Fix: Added `get_resolution(request_id) -> dict | None` public method to `DecisionManager` (`decisions.py`) and updated `worker.py` to call it instead of the private method.
- Test: `tests/test_decisions.py::TestDecisionManagerGetResolution` (4 tests covering resolved, pending, and unknown request cases).

**[x] TODO-2: Engine `_save_state()` not atomic**
- File: `agent_baton/core/engine/executor.py:_save_state()`
- Issue: Uses `path.write_text()` — not atomic. A crash mid-write corrupts `execution-state.json`, making `resume()` and `recover_dispatched_steps()` unable to recover.
- Fix: Applied tmp+rename pattern in `StatePersistence.save()` (`persistence.py`) — writes to `.json.tmp` then `os.rename()` to target.
- Test: `tests/test_executor.py::TestStatePersistence::test_save_is_atomic_no_tmp_file_left` and `test_save_overwrites_previous_state`.

**[x] TODO-3: Stderr redaction for API key patterns**
- File: `agent_baton/core/runtime/claude_launcher.py`
- Issue: `LaunchResult.error` may contain API key prefixes (`sk-ant-*`) if the `claude` CLI echoes them in stderr during auth failures. The error string is persisted to `execution-state.json` and trace events without sanitization.
- Fix: Added `_redact_stderr(text: str) -> str` helper using `_API_KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]+")`. Applied throughout `_parse_output()` wherever `error` is built from stderr.
- Test: `tests/test_claude_launcher.py::TestRedactStderr` (parametrized for key patterns, multiple keys, clean text, empty string, and full launch() integration path).

### LOW Priority

**[x] TODO-4: TOCTOU race + unhandled RuntimeError in daemon CLI**
- File: `agent_baton/cli/commands/execution/daemon.py`
- Issue: The single-instance check (PID probe) has a TOCTOU race with the flock in `_write_pid()`. If another daemon starts between the check and the flock, `supervisor.start()` raises `RuntimeError` which is not caught by the CLI handler.
- Fix: Wrapped `supervisor.start()` in `try/except RuntimeError` in the worker-only path; prints "Error: ..." and returns cleanly.
- Test: `tests/test_daemon.py::TestDaemonHandlerRuntimeErrorIsCaught::test_runtime_error_from_supervisor_prints_clean_error`

**[x] TODO-5: `start_new_session=True` for launched agents**
- File: `agent_baton/core/runtime/claude_launcher.py`
- Issue: `create_subprocess_exec` did not pass `start_new_session=True`. Launched agents inherited the daemon's session.
- Fix: `start_new_session=True` added to the `create_subprocess_exec` call in `_run_once()`.
- Test: `tests/test_claude_launcher.py::TestStartNewSession::test_run_once_passes_start_new_session_true`

**[x] TODO-6: `--plan` required even with `--resume`**
- File: `agent_baton/cli/commands/execution/daemon.py`
- Issue: `--plan` was `required=True` on the `start` subparser, but when `--resume` is set the plan file is ignored. Users had to provide a dummy `--plan` arg on resume.
- Fix: `--plan` is `required=False` (default `None`). Handler checks `not args.resume and not args.plan` and prints an error when both are absent.
- Tests: `tests/test_daemon.py::TestDaemonPlanOptionalWithResume` (3 tests covering no-plan no-resume error, argparse accepts resume without plan, resume skips plan-required error)

**[x] TODO-7: `_handle_gate` polling interval hardcoded**
- File: `agent_baton/core/runtime/worker.py`
- Issue: The 2-second polling interval in `_handle_gate()` was hardcoded.
- Fix: `gate_poll_interval: float = 2.0` constructor parameter added to `TaskWorker`; stored as `self._gate_poll_interval` and used in both `_handle_gate()` and `_handle_approval()`.
- Tests: `tests/test_runtime.py::TestTaskWorkerGatePollInterval` (3 tests: default value, custom value stored, custom interval used during gate polling)

**[x] TODO-8: Redundant monkeypatch in Windows test**
- File: `tests/test_daemon.py::TestDaemonizeFunction::test_windows_raises_runtime_error`
- Issue: Patched both `sys.platform` and `daemon.sys.platform` — the first was redundant.
- Fix: Only `daemon.sys.platform` is patched; the redundant `monkeypatch.setattr(sys, "platform", "win32")` line was removed.
