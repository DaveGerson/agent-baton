# Agent Baton — Codebase Review Assessment

**Date**: 2026-03-21
**Reviewer**: Claude Code (automated audit)
**Test Suite**: 1712 tests, all passing

---

## Pass 1: Security Audit (InfoSec Red Flags)

### HIGH

#### 1. Unsafe `tar.extractall()` — Path Traversal via Malicious Archives

- **Files**: `core/distribute/sharing.py:195,234`, `core/distribute/registry_client.py:140`
- **Description**: All three calls use `tar.extractall()` without member filtering. The `# noqa: S202` comments dismiss the Bandit warning, but `sharing.py:extract()` accepts `archive_path` from CLI input. A crafted `.tar.gz` with entries like `../../.bashrc` or absolute paths will write outside the intended temp directory.
- **Impact**: Supply-chain attack vector when packages are shared via registry.
- **Remediation**: Use `tar.extractall(path, filter="data")` (Python 3.12+) or manually reject members with `..` or absolute paths.

#### 2. Gate Command Injection via `{files}` Placeholder

- **Files**: `core/engine/gates.py:94-96`, `core/engine/dispatcher.py:117-119`
- **Description**: `files_changed` list is joined and string-interpolated into shell commands without escaping. A file path like `foo; rm -rf /` becomes a shell injection payload.
- **Impact**: A compromised agent definition can escalate to arbitrary shell execution.
- **Remediation**: Use `shlex.quote()` on each element before joining.

### MEDIUM

#### 3. Incomplete Path Traversal Sanitization in Task/Incident ID Handling

- **Files**: `core/distribute/async_dispatch.py:66`, `core/distribute/incident.py:231,255`
- **Description**: Sanitization only replaces `/` and spaces but does not handle `..`, null bytes, or other path metacharacters.
- **Remediation**: Validate IDs against `^[a-zA-Z0-9_-]+$` with max length.

#### 4. Plaintext Telemetry of Sensitive Data

- **Files**: `core/observe/telemetry.py`, `core/observe/usage.py`, `core/observe/trace.py`
- **Description**: Logs file paths, task descriptions, and plan snapshots (including `shared_context`) as plaintext JSONL with no rotation, redaction, or access controls.
- **Remediation**: Document what is logged; add redaction option; include `.claude/team-context/` in data governance policies.

#### 5. Broad `auto-edit` + `Bash` Permissions on Agent Definitions

- **Files**: Agent `.md` files, `templates/settings.json`
- **Description**: Implementation agents granted `auto-edit` + full Bash. Hook-based guardrails are bypassable by design (symlinks, indirect writes).
- **Remediation**: Document threat model explicitly; consider "restricted" agent profile without Bash for regulated environments.

### LOW

- **subprocess.run in classifier**: Safe pattern (fixed args, no shell=True, timeout)
- **No outbound network activity**: Positive finding — zero HTTP/socket calls in the codebase
- **Temp directory leak**: `sharing.py:extract()` creates temp dir without cleanup context manager

---

## Pass 2: Fit for Purpose & Functionality

### CRITICAL

#### 1. Execution Engine Phase-Skipping Bug

- **File**: `core/engine/executor.py:268`
- **Description**: `record_gate_result` sets `state.current_phase = phase_id + 1`. But `phase_id` comes from `PlanPhase.phase_id` (1-indexed, from `enumerate(..., start=1)`), while `current_phase` is a 0-indexed array index. When the first phase (index 0, phase_id=1) gate passes, `current_phase` becomes 2, skipping the phase at index 1.
- **Impact**: **Any multi-phase plan with 3+ phases will skip the second phase after the first gate passes.** This is the core execution state machine.
- **Fix**: Change line 268 to `state.current_phase += 1`.
- **Related**: `gates.py:143,153,164,184,194` — `evaluate_output()` hardcodes `phase_id=0` in all returned GateResults (same semantic mismatch).

### HIGH

#### 2. Zero CLI Test Coverage

- **Files**: `cli/main.py` and all 32 files in `cli/commands/`
- **Description**: No unit tests for the primary user-facing surface. The `discover_commands()` function, argparse wiring, and all 32 handler functions are untested.
- **Impact**: Broken arguments, wrong imports, or typos in handler logic would go undetected.

### MEDIUM

#### 3. `datetime.now()` Without Timezone in Audit-Relevant Modules

- **Files**: `context.py:114`, `compliance.py:48,132`, `evolution.py:31,137,178`, `plan.py:57,131`
- **Description**: These modules produce naive (timezone-unaware) timestamps while the engine modules use `datetime.now(tz=timezone.utc)`. Compliance report timestamps and plan `created_at` fields lack timezone context.
- **Impact**: Inconsistent timestamps across audit artifacts.

#### 4. Silent Exception Swallowing in Planner

- **File**: `core/engine/planner.py:169,192,532,539,553,577`
- **Description**: Six bare `except Exception: pass` blocks hide real errors. If pattern learner data is corrupted or a model changes its API, the planner silently produces degraded plans.
- **Impact**: Wrong agent selection or budget tier with no diagnostic output.

#### 5. Migrations Path Risk Classification Bug

- **File**: `core/govern/classifier.py:155-171`
- **Description**: The comment says "Migrations alone are MEDIUM, not HIGH" but line 155 sets `max_risk = RiskLevel.HIGH` before the migrations check at line 164, making the MEDIUM branch unreachable.
- **Impact**: Migration-only changes incorrectly classified as HIGH risk.

#### 6. Dual Plan Model System Without Converter

- **Files**: `models/plan.py` (`ExecutionPlan`) vs `models/execution.py` (`MachinePlan`)
- **Description**: Two independent plan hierarchies with similar but non-identical fields, no shared base class, and no converter. `PlanBuilder` produces `ExecutionPlan` while `IntelligentPlanner` produces `MachinePlan`.
- **Impact**: Plans created via `PlanBuilder` cannot be fed to `ExecutionEngine` without manual conversion.

#### 7. No Test for PlanBuilder -> ContextManager -> Engine Handoff

- **Description**: The markdown-based plan files from `PlanBuilder`/`ContextManager` and the JSON-based files from `ExecutionEngine` are parallel persistence paths with no integration test.

### LOW

- `_higher()` function in `classifier.py:45-47` is dead code
- Mutable `PolicySet` objects shared via module-level dict without copy (`policy.py:298-304`)
- Review gate always passes regardless of content — no blocking capability
- `agent-definition-engineer` in CLAUDE.md roster has no `.md` file
- `resolve_all` does O(N^2) file I/O (`escalation.py:139-151`)
- `models/reference.py` has no tests
- Agent model strings are not validated against known values

---

## Positive Findings

- **1712 tests pass** with high quality — no tautological tests found
- **`test_engine_integration.py`** (1176 lines) traces the full pipeline end-to-end
- **All agent definitions** reference valid tools and models
- **No hardcoded secrets**, API keys, or `.env` files in the codebase
- **No outbound network connections** — zero HTTP/socket calls
- **Data model serialization** round-trips are thoroughly tested

---

## Top Recommendations (Priority Order)

1. **Fix the phase-skipping bug** — single line change in `executor.py:268` (CRITICAL)
2. **Fix gate command injection** — add `shlex.quote()` in `gates.py` and `dispatcher.py` (HIGH/security)
3. **Fix tar.extractall path traversal** — add member filtering (HIGH/security)
4. **Add CLI unit tests** — primary user surface has zero coverage (HIGH)
5. **Normalize timezone usage** — use `datetime.now(tz=timezone.utc)` everywhere (MEDIUM)
6. **Fix migrations classifier bug** — reorder logic so MEDIUM classification applies (MEDIUM)
7. **Add logging to planner exception handlers** — replace `pass` with `logger.debug()` (MEDIUM)
8. **Unify or bridge plan models** — add converter or deprecate one (MEDIUM)
