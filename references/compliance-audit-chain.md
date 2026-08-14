# Compliance Audit Chain (F0.3)

`compliance-audit.jsonl` is the tamper-evident, hash-chained log of every
governance-relevant event the engine emits: dispatches, gate results,
auditor verdicts, override decisions. Each entry carries `prev_hash` and
`entry_hash` SHA-256 fields so any insertion, deletion, or in-place
mutation is detectable by `baton compliance verify`.

## CLI

```
baton compliance verify [--log PATH]
baton compliance rechain [--log PATH] [--out PATH]
```

`verify` walks the log line-by-line and reports the first divergence
(`Chain intact — N entries verified.` on success). `rechain` migrates a
pre-F0.3 plain-text log to the hashed format.

Default log path: `.claude/team-context/compliance-audit.jsonl`.

## Upgrade procedure (bd-c0e0)

Pre-F0.3 logs (or any log produced before bd-f606 was merged) contain
plain JSON rows with no `prev_hash`/`entry_hash`. After upgrading
agent-baton across the F0.3 boundary, `verify` will report:

```
Line N: missing prev_hash/entry_hash — this row pre-dates the F0.3 hash
chain. Run `baton compliance rechain --log <path>` once to migrate the
existing log to the hashed format.
```

Resolve once per project:

1. Stop any in-flight executions writing to the log.
2. `baton compliance rechain --log .claude/team-context/compliance-audit.jsonl`
3. `baton compliance verify --log .claude/team-context/compliance-audit.jsonl`
   — must report `Chain intact`.
4. Resume executions; new appends extend the chain via
   `ComplianceChainWriter.append()`.

`rechain` is idempotent — running it on an already-hashed log re-emits
the same hashes and reports the same count. Use `--out PATH` for
dry-runs or air-gapped review without an in-place swap.

## Upgrade boundary: F015 digest-formula change

F015 made `entry_hash` commit to `prev_hash` (previously `entry_hash` was a
pure function of the row's own payload, and `prev_hash` was an
unauthenticated pointer field — an attacker could delete, reorder, or
splice rows and repair the log by rewriting a single hex string per row).
This closes that hole, but it is an *unconditional* formula change: any
row written before F015 (every F0.3-era row, since the old `entry_hash`
never covered `prev_hash`) no longer matches the new formula.

Crossing the F015 boundary, `verify` will report:

```
Line N: entry_hash mismatch — this row predates the F015 digest change (it
was written under the legacy payload-only formula, before entry_hash
committed to prev_hash). This is an upgrade boundary, not tampering — run
`baton compliance rechain --log <path>` once to migrate this log to the
current chained format.
```

That message is *detect-and-advise*, not accept — `verify` still returns
failure for a legacy row, so a pre-F015 row is never treated as valid.
There is deliberately no version flag that lets an old-formula row pass;
resolve it the same way as the pre-F0.3 boundary:

1. Stop any in-flight executions writing to the log.
2. `baton compliance rechain --log .claude/team-context/compliance-audit.jsonl`
3. `baton compliance verify --log .claude/team-context/compliance-audit.jsonl`
   — must report `Chain intact`.
4. Resume executions; new appends extend the chain under the current
   (`prev_hash`-committing) formula.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `missing prev_hash/entry_hash` on a row | Pre-F0.3 plain-text entry | `baton compliance rechain` |
| `prev_hash mismatch` | Row inserted/deleted from middle of log | Investigate; chain is broken — restore from backup, then rechain |
| `entry_hash mismatch`, message names the **F015** upgrade boundary | Genuine pre-F015 row (payload-only digest); not tampering | `baton compliance rechain` — see "Upgrade boundary: F015 digest-formula change" above |
| `entry_hash mismatch`, message does **not** name the F015 boundary | Row payload mutated in place | Investigate; chain is broken — restore from backup, then rechain |
| `JSON parse error` | Torn write from a killed writer | Operator strips the torn line; future appends continue cleanly (see `tests/govern/test_chain_writer_concurrency.py`) |

## Concurrency

`LockedJSONLChainWriter` uses `fcntl.flock(LOCK_EX)` to serialise
appends across processes. Two concurrent writers cannot fork the hash
sequence; the loser blocks on the lock and reads the just-committed
prev_hash before computing its own entry_hash.

The legacy `ComplianceChainWriter` (in-process, single-writer) holds
`_last_hash` in memory. Multi-process callers must use
`LockedJSONLChainWriter` instead. See `bd-fce7` for the planned
consolidation.

## Override audit (bd-f606)

When the engine advances past a VETO under `--force` +
`--justification`, it appends an `Override` row via
`ComplianceChainWriter.append_override()` so the override is durably
auditable. `verify` still passes — overrides extend the chain rather
than break it.

## Fail-closed mode

When a compliance write fails the engine can either continue (best-effort,
the default) or halt with a `ComplianceWriteError` (fail-closed).

Resolution order (first non-`null` value wins):

1. `plan.compliance_fail_closed` in `plan.json` — set by the planner for
   regulated-data or HIGH/CRITICAL-risk tasks.
2. `BATON_COMPLIANCE_FAIL_CLOSED=1` env var — operator-level default.
3. `false` — historical best-effort default.

In fail-closed mode `state.status` is flipped to `"failed"` and
`ComplianceWriteError` is raised before the step can continue.

## See also

- `agent_baton/core/govern/compliance.py` — writer + verify + rechain
- `agent_baton/models/execution.py` — `MachinePlan.compliance_fail_closed` field
- `tests/govern/test_audit_chain.py` — integration tests including
  `test_rechain_then_verify_round_trip_on_pre_f03_log`
- `tests/test_governance_runtime.py` — `TestComplianceFailClosed` and
  `TestPlanComplianceFailClosed` for the full behavior matrix
- `references/guardrail-presets.md` — risk-tier policy that drives
  which agents and gates emit compliance entries
