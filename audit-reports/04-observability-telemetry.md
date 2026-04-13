# Audit Report: Observability & Telemetry Coverage

**Scope:** `core/observe/`, `cli/commands/observe/`, logging across all subsystems
**Date:** 2026-04-13

---

## Findings

### BLIND-SPOT: Subsystems with zero logging

51 of ~80 core modules have **no `import logging` or logger instance at all**. Critical gaps:

| Subsystem | Files | Impact |
|-----------|-------|--------|
| **engine/dispatcher.py** | Prompt construction for all agent delegation | Cannot diagnose "wrong agent got wrong instructions" |
| **engine/gates.py** | All gate pass/fail decisions | Cannot diagnose "gate passed when it should have failed" |
| **engine/persistence.py** | State load/save | Silent `None` return on corrupt state (line 101-102) |
| **orchestration/router.py** | Stack detection + agent flavor mapping | Cannot diagnose "wrong agent selected for my stack" |
| **orchestration/registry.py** | Agent definition loading | Silent `None` on read failure (line 170-171) |
| **observe/** (all 7 files) | The observability layer itself | The system that logs has no logging of its own failures |
| **govern/** (all 6 files) | Policy, compliance, classification, escalation | Cannot audit the auditors |
| **events/** (all 4 files) | Event bus, persistence, projections | Event infrastructure failures are invisible |
| **runtime/daemon.py, worker.py, scheduler.py, launcher.py** | Background execution | Headless execution has no diagnostic trail |

### SILENT-FAILURE: Errors caught but not logged

| Location | What happens | Consequence |
|----------|-------------|-------------|
| `dashboard.py:65` | `except Exception: storage_records = []` | Storage read failure silently shows "no data" |
| `dashboard.py:230` | `except Exception: tel_summary = None` | Telemetry section silently vanishes from dashboard |
| `router.py:194` | `except Exception: pass` | Stack detection failure swallowed — wrong agent flavor selected silently |
| `router.py:251` | `except Exception: pass` | Learning-based routing hints silently discarded |
| `persistence.py:101` | `except (JSONDecodeError, KeyError, TypeError): return None` | Corrupt state returns None — callers interpret as "no execution exists" |
| `registry.py:170` | `except (OSError, UnicodeDecodeError): return None` | Agent definition fails to load silently |
| `executor.py:498` | `except Exception: pass` | Active-task marker write fails silently |
| `policy.py:427` | `except (JSONDecodeError, KeyError, OSError): return None` | Corrupt policy falls through to built-in presets without notice |

### MISSING-INSTRUMENTATION: No feature-usage analytics

**CLI command invocation is never tracked.** No CLI command records that it was called. You cannot answer "which commands do users actually run" or "which agents get dispatched most" without parsing shell history.

The only instrumentation is `TaskUsageRecord` (token/cost per completed orchestrated task). This misses:
- Direct (non-orchestrated) CLI usage entirely
- Abandoned/failed plans that never reach `complete()`
- Feature discovery — which subcommands users find vs. never touch
- Agent dispatch frequency outside the engine loop

### INCONSISTENT: Logging quality varies drastically

| Component | Level | What's logged | What's missing |
|-----------|-------|---------------|----------------|
| **executor.py** | Rich | State transitions, telemetry events, budget warnings, bead operations | Good coverage |
| **planner.py** | Minimal | 4 debug statements, all for bead/knowledge edge cases | **Zero logging of phase selection logic, agent assignment rationale, risk classification, or step template choices** |
| **knowledge_resolver.py** | Moderate | Pack not found, agent not found | Successful resolution path not logged |
| **bead_store.py** | Rich | 15+ log statements | Good |
| **gates.py** | None | — | Gate pass/fail reasoning, lint errors, spec check details — all invisible |
| **dispatcher.py** | None | — | Prompt template used, knowledge sections included, boundary rules — all invisible |

### WRITE-ONLY: Telemetry data collected but analysis is limited

| Data Source | Written To | Consumed By | Gap |
|-------------|-----------|-------------|-----|
| `telemetry.jsonl` | JSONL file | `baton telemetry` (last N events), dashboard summary | No filtering by time range, event type, or correlation. No error-rate computation. |
| Trace files (`traces/*.json`) | JSON per task | `baton trace` shows timeline | No cross-trace analysis ("show me all tasks where gate failed"). No trend detection. |
| `usage-log.jsonl` | JSONL file | Dashboard, pattern learner, budget tuner | No time-series analysis. No anomaly detection. |
| Domain events (EventBus) | JSONL via EventPersistence | TaskView materialized view | **Events are write-only outside TaskView** — no CLI to query raw events, no replay, no search. |

### Telemetry/logging boundary is blurred

No architectural separation between debug logging (`logging.getLogger`) and operational telemetry (`AgentTelemetry`). The executor uses both for the same events. Debug logs go to stderr; telemetry goes to JSONL. But the planner's decision-making only uses debug logging (barely), while the executor uses both. A user debugging "why did the planner choose this agent" gets nothing from telemetry and almost nothing from logs.

### Dashboard data completeness

The dashboard reads from usage records and telemetry. It reports token costs, retry rates, agent utilization, and gate pass rates. It **does not include**: error rates, failure modes, plan abandonment rates, average execution duration, knowledge gap frequency, or bead hit/miss rates. The dashboard answers "how much did we spend" but not "how well is the system performing."

### Retrospective data access

The retrospective engine depends on the executor calling `_generate_retrospective()` with qualitative data (`what_worked`, `what_didnt`). The qualitative fields come from accumulated step results. If steps only record `status=complete` without rich outcomes, the retrospective is quantitative-only — an empty shell.

---

## Key Takeaway

The system has a well-designed observability *architecture* (telemetry, traces, usage, retrospectives, dashboard) but suffers from **uneven instrumentation** — the executor is well-covered while the planner, dispatcher, gates, router, and the entire govern layer are nearly or completely dark. **The most common user complaint ("it chose the wrong agent" / "it made a bad plan") maps to the subsystems with the least logging.**
