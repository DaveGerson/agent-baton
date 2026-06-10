---
name: evidence-bundle
description: |
  Agent-consumable reference for the Evidence Bundle system (007 Phase H).
  Read this when you need to produce, verify, or interpret a per-task
  evidence bundle — typically at task completion, before opening a PR, or
  as part of a compliance review.
---

# Evidence Bundle — Agent Reference

An evidence bundle is a **single verifiable artifact** that captures the
full assurance record for one task execution.  It can be checked into the
repo, attached to a PR, or handed to an external auditor.  Every file in
the bundle has a SHA-256 hash in `manifest.json`; the manifest may also
carry a cryptographic soul signature.

---

## When to produce a bundle

1. **After `baton execute complete`** — the engine has finalized the
   execution trace.
2. **Before opening a pull request** — attach the bundle path (or the
   `.tar.gz`) to the PR description.
3. **When an auditor requests evidence** — run `baton evidence bundle`
   with `--sign` so the output is cryptographically attributable.
4. **In a GATE step** — gate commands may include
   `baton evidence bundle <task-id> && baton evidence verify <path>` to
   confirm integrity as part of CI.

---

## Producing a bundle

```bash
baton evidence bundle <task-id> \
    [--output DIR]           # default: .claude/team-context/
    [--sign]                 # requires BATON_SOULS_ENABLED=1
    [--tar]                  # → <task-id>.tar.gz, removes directory
    [--db PATH]              # override baton.db
    [--compliance-log PATH]  # override compliance-audit.jsonl
    [--packs-dir PATH]       # override .claude/packs/
    [--soul-db PATH]         # override ~/.baton/central.db
```

The bundle lands at `<output>/evidence/<task-id>/` (or
`<output>/evidence/<task-id>.tar.gz` with `--tar`).

**Signing note:** `--sign` picks the first active `auditor` soul in the
registry, falling back to an auto-minted `evidence-signer` soul.
If `BATON_SOULS_ENABLED` is not `1`, a warning is printed and the bundle
is created unsigned.

---

## Bundle file inventory

| File | What it proves |
|------|---------------|
| `manifest.json` | SHA-256 of every other file; optional soul signature; schema version `agent-baton-evidence/1.0` |
| `aibom.json` | AI Bill of Materials — models, agents, MCP servers, knowledge, gates, chain anchor |
| `aibom.md` | Human-readable AIBOM (same data as `aibom.json`) |
| `compliance-segment.jsonl` | Task-scoped compliance-audit entries with original hash-chain fields intact; omitted when no compliance log exists |
| `gates.json` | Full `gate_results` table dump for this task (id, gate_type, passed, outcome, command, exit_code, actor) |
| `verdicts.json` | `step_results` rows from auditor/reviewer agents; each row includes `verdict` (an `AuditorVerdict` string or `null`) and `outcome_truncated` flag |
| `approvals.json` | `approval_results` rows + `pending_approval_request` (with `"_pending": true`) if one was set on the execution |
| `packs.json` | Assurance pack metadata + `active-policy.json` snapshot; **omitted** when neither packs nor active-policy are found |

---

## Verifying a bundle

```bash
# CI-runnable — no network, no database
baton evidence verify <path-to-bundle-dir-or-tar>
```

Exit codes:

| Code | Meaning |
|------|---------|
| 0 | All checks passed (warnings allowed) |
| 1 | One or more integrity failures |
| 2 | Bundle unusable — `manifest.json` missing or unparseable |

Checks performed (in order):
1. `manifest.json` present and valid JSON.
2. Per-file SHA-256 matches the value in `manifest.files`.
3. `compliance-segment.jsonl` internal chain consistency (each entry's
   `prev_hash` matches the previous entry's `entry_hash`).
4. AIBOM `chain_anchor` vs segment tail hash — a mismatch is a WARNING
   (the compliance log grew after bundling) not a failure.
5. Soul signature — when `manifest.soul_signature` is present, the
   signer soul is looked up in the local registry, checked for revocation,
   and the signature is cryptographically verified.

Add `--strict` to stop at the first failure.

---

## Reading verdicts.json

Each row in `verdicts.json` represents a step from an auditor or reviewer
agent:

```json
{
  "task_id": "task-abc123",
  "step_id": "2.1",
  "agent_name": "auditor",
  "step_type": "reviewing",
  "outcome": "...<agent output>...",
  "outcome_truncated": false,
  "verdict": "APPROVE",
  "status": "complete",
  "completed_at": "2026-06-10T10:00:00Z"
}
```

`verdict` is one of `APPROVE`, `APPROVE_WITH_CONCERNS`,
`REQUEST_CHANGES`, `VETO`, or `null` (verdict not extracted from the
outcome text).

`outcome_truncated` is `true` when the outcome text contains a
`TRUNCATED:` breadcrumb written by the runtime — the full outcome may be
in a spillover file not captured in the bundle.

---

## Reading approvals.json

```json
{
  "approvals": [
    {
      "id": 1, "task_id": "task-abc123", "phase_id": 1,
      "result": "APPROVED", "feedback": "LGTM",
      "decided_at": "2026-06-10T10:05:00Z",
      "decision_source": "human", "actor": "alice", "rationale": ""
    }
  ],
  "pending_approval_request": null
}
```

When `pending_approval_request` is not `null`, it contains the full
`pending_approval_request_json` from the execution row with an added
`"_pending": true` key.

---

## Integration with GATE steps

The engine can emit a GATE step that includes evidence verification:

```bash
baton evidence bundle "$BATON_TASK_ID" --output .claude/team-context \
  && baton evidence verify .claude/team-context/evidence/"$BATON_TASK_ID"
```

A non-zero exit from `baton evidence verify` will cause the gate to fail,
preventing the execution from advancing.

---

## See also

- `references/compliance-audit-chain.md` — hash chain format
- `references/guardrail-presets.md` — when bundles are mandatory
- `docs/cli-reference.md` — full flag reference for `baton evidence`
