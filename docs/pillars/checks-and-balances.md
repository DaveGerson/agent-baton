---
quadrant: explanation
audience: users, maintainers
see-also:
  - [../pillars.md](../pillars.md)
  - [../governance-knowledge-and-events.md](../governance-knowledge-and-events.md)
---

# Pillar 4 — Checks & Balances

!!! abstract "Pillar context"
    One of [the four pillars](../pillars.md) — the trust mechanism that keeps the project management honest.

> **In one line:** verify the work is *functionally* right, not just that it lints — and make the outcome auditable.

---

## The vision

Fast and syntactically valid is not the same as correct. A task that passes
linting and unit tests can still violate a business rule, misinterpret a
regulatory requirement, or silently alter a schema in a way that breaks a
downstream compliance report.

Pillar 4 addresses this by layering four kinds of checks:

1. **Domain-expert verification.** The `auditor` agent is independent of the
   orchestrator — it can overrule the plan. For regulated domains the
   `subject-matter-expert` provides the domain context (regulatory requirements,
   data models, validation rules) that the auditor and implementers need to be
   right, not just done. Neither agent writes code; both can block code from
   shipping.

2. **Guardrails on every action.** Policy rules enforce constraints at the
   tool-call level, not just at the plan level. Every `Write`, `Edit`, `Bash`,
   and `MultiEdit` call is checked before it executes and recorded after.

3. **Tamper-evident audit evidence.** The compliance audit log is hash-chained.
   Evidence bundles package per-task artifacts under a SHA-256 manifest. Neither
   can be silently altered after the fact.

4. **Front-loading risk.** Spec federation imports externally-sourced work for
   human review before any agent fires. Classification runs at plan time, not as
   an afterthought. Catching a HIGH/CRITICAL risk before execution costs one API
   call; catching it after costs a full rollback.

What this is NOT: the verification does not improve itself autonomously or learn
to check better over time. That capability was cut as nascent and unproven.
Verification is done by human-authored agents operating on explicit domain
knowledge, policy rules that humans write, and deterministic hash functions.

---

## How it works today

### Risk classification

**Module:** `agent_baton/core/govern/classifier.py` — `DataClassifier`

Every task is classified before a single specialist fires. The classifier
scans the task description and affected file paths across five signal
categories, producing a `ClassificationResult` with a risk tier and the
matching guardrail preset:

| Tier | Trigger | What fires |
|------|---------|-----------|
| LOW | No signals detected | Preset applied inline; no subagent overhead |
| MEDIUM | Database signals (migration, schema, alter table, …) | `auditor` reviews the plan before execution |
| HIGH | Regulated, PII, security, or infrastructure signals; or sensitive file paths (`.env`, `secrets/`, `auth/`, `terraform/`, …) | Independent `auditor` subagent with VETO authority; regulated domains also require `subject-matter-expert` |
| CRITICAL | Three or more regulated/PII signals in a single task (auto-escalation) | Same as HIGH |

Risk can only be escalated, never lowered by a secondary signal. The
cascade is deterministic: regulated and PII signals dominate security
and infrastructure, which dominate database signals.

When `ANTHROPIC_API_KEY` is set and the `agent-baton[classify]` extra is
installed, the planner uses an AI model for classification. Without it, the
classifier falls back to the keyword heuristic implemented in `classifier.py`.
Both paths produce the same `ClassificationResult` shape; the AI path produces
higher-quality signal on ambiguous task descriptions.

```bash
baton classify "Add HIPAA audit trail to patient records"
baton classify "Refactor utility functions" --files src/auth/login.py
baton classify "Add HIPAA audit trail" --activate  # also writes .claude/active-policy.json
```

### Independent auditor with veto authority

**Agent:** `agents/auditor.md`

The `auditor` agent runs in a separate context from the orchestrator by
design — it must be able to overrule the plan without being biased by the
planner's reasoning. It operates in three modes:

1. **Pre-execution plan review.** Checks scope boundaries, write overlaps,
   data safety, regulatory requirements, and rollback paths. Returns a
   guardrails report with a per-agent permission manifest the orchestrator
   enforces.
2. **Mid-execution checkpoints.** Returns CONTINUE / PAUSE / HALT at defined
   step boundaries. A HALT prevents the next dependent step from dispatching.
3. **Post-execution audit.** Diff review, compliance scan, security scan,
   domain validation. Returns a machine-readable verdict:

   | Verdict | Effect |
   |---------|--------|
   | `APPROVE` | Execution advances |
   | `APPROVE_WITH_CONCERNS` | Advances; concerns are tracked |
   | `REQUEST_CHANGES` | Revisions required before advancing |
   | `VETO` | Halts HIGH/CRITICAL phase advancement |

A `VETO` verdict blocks the executor from advancing HIGH/CRITICAL risk phases.
Overriding requires `--force --justification`, and every override is written to
the compliance audit chain — it cannot be silently discarded.

The `AuditorVerdict` enum and its `blocks_execution` property are implemented
in `agent_baton/core/govern/compliance.py`.

### Subject-matter expert for regulated domains

**Agent:** `agents/subject-matter-expert.md`

For any task that touches compliance systems, regulated data, or
industry-specific business rules (HIPAA, GDPR, SOX, PCI-DSS, FERPA, …), the
`subject-matter-expert` is required by the Regulated Data guardrail preset.
The SME supplies the domain context — regulatory constraints, data retention
rules, validation requirements, audit trail obligations — that implementers and
the auditor need to be correct. It does not write code; it provides the
knowledge that makes code correct.

Regulated-domain rule from `agent_baton/core/govern/policy.py`:
the `regulated` preset requires both `subject-matter-expert` (`require_agent`,
severity `block`) and `auditor` (`require_agent`, severity `block`) in the
execution plan. Bash access on regulated data is also blocked by rule.

### Policy hooks: enforcement on every tool call

**Module:** `agent_baton/core/govern/policy.py` — `PolicyEngine`

**Hook configuration:** `templates/settings.json`

Two Claude Code hooks run on every tool call during execution:

- **`baton policy-check`** (PreToolUse on `Bash|Write|Edit|MultiEdit`) —
  evaluates the tool call against the active guardrail preset. A blocking
  rule (`path_block`, `tool_restrict`) causes exit code 2, denying the
  tool call before it executes. `BATON_POLICY_FAIL_CLOSED=1` makes hook
  errors also deny; the default is fail-open.

- **`baton comply-record`** (PostToolUse on `Bash|Write|Edit|MultiEdit`,
  and on `Stop`) — appends a hash-chained entry to `compliance-audit.jsonl`
  after each tool use. `BATON_COMPLIANCE_FAIL_CLOSED=1` makes write failures
  halt execution rather than log-and-continue; required for regulated work
  where losing an audit entry is itself a compliance defect.

A separate inline path-block hook (also PreToolUse on `Write|Edit`) blocks
writes to `.env`, `secrets/`, `node_modules/`, and `.pem`/`.key` files at
the shell level before the policy engine even runs.

The active preset is written to `.claude/active-policy.json` by
`baton classify --activate` or automatically by the execution engine when
it starts a task. The PreToolUse hook reads this file on every call; no
restart is required when the preset changes.

Five built-in presets ship: `standard_dev`, `data_analysis`, `infrastructure`,
`regulated`, `security`. Custom presets live as JSON under `.claude/policies/`.

### Assurance packs

**Module:** `agent_baton/core/govern/packs.py`

Organisations author domain-specific governance units under
`.claude/packs/<name>/`. Each pack bundles a policy set, classification
signals, a review rubric, gate definitions, and evidence requirements into
a single versioned directory:

```
.claude/packs/<name>/
├── pack.json       # Manifest: name, version, description (required)
├── policy.json     # PolicySet — preset name must be "pack:<dirname>"
├── signals.json    # Classification signals — keywords + path patterns
├── rubric.md       # Review checklist (must have headings and checkboxes)
├── gates.json      # Gate definitions (id, description, command)
└── evidence.json   # Required artifacts (id, description)
```

Loading a pack merges its keyword signals into the `DataClassifier` and
registers its policy set so `policy-check` resolves `"pack:<name>"` presets
automatically. When multiple packs match, the highest risk tier wins; ties
break alphabetically by preset name.

```bash
baton packs init <name>       # Scaffold a new pack directory
baton packs validate <name>   # Validate all 7 schema checks
baton packs list              # List loaded packs and their status
```

### Verifiable evidence bundles

**Module:** `agent_baton/core/govern/evidence_bundle.py` — `EvidenceBundleBuilder`, `verify_bundle`

After each task, `baton evidence bundle <task_id>` assembles a
self-contained directory under `evidence/<task_id>/`:

| File | Contents |
|------|----------|
| `manifest.json` | SHA-256 digest of every other file in the bundle |
| `aibom.json` / `aibom.md` | AI Bill of Materials for the task |
| `compliance-segment.jsonl` | Task-scoped entries from the compliance audit chain |
| `gates.json` | Full gate results dump |
| `verdicts.json` | Auditor and reviewer step verdicts |
| `approvals.json` | Approval decisions and any pending approval request |
| `packs.json` | Active assurance packs + active-policy snapshot |

`verify_bundle` checks every SHA-256 digest in `manifest.json`, verifies the
internal consistency of the compliance segment's hash chain, and (when
`--sign` was used) verifies the soul signature on the manifest. It accepts
either a directory or a `.tar.gz` archive and is network-free, suitable for
CI.

```bash
baton evidence bundle <task_id>            # Build bundle
baton evidence bundle <task_id> --tar      # Build and compress to .tar.gz
baton evidence bundle <task_id> --sign     # Sign manifest (requires BATON_SOULS_ENABLED=1)
baton evidence verify <path>               # Verify directory or .tar.gz; exits 0/1/2
```

### Segregation-of-duties approval

`BATON_APPROVAL_MODE=team` requires the approving actor to differ from whoever
requested the approval. Self-approval is blocked at the engine level. The
approval request records the requester identity; the approval result records
the actor. This satisfies the basic segregation-of-duties requirement for
regulated work.

### Spec federation: the cheapest control point

Before any agent fires on externally-sourced work, a spec can be imported from
GitHub Issues or Azure DevOps, auto-enriched with a risk classification and
cost forecast (pack-aware when packs are loaded), and routed for senior review.
A HIGH/CRITICAL spec can be bounced at this stage for one API call rather than
discovering the problem mid-execution.

```bash
baton spec import           # Import from GitHub Issues / Azure DevOps
baton spec list
baton spec approve          # Blocked on self-approval in team mode
baton spec bounce           # Return with feedback
```

The `SpecDraftStore` backing these routes is in
`agent_baton/api/routes/spec_queue.py`.

---

## The gap today

The checks-and-balances layer is functional, but several parts are experimental
or have documented limits.

**Persistent agent souls are experimental.** `BATON_SOULS_ENABLED` defaults to
`0`. When disabled, evidence bundles are built and SHA-256 verified
correctly, but `manifest.json` is unsigned and `verdicts.json` carries no
soul-signature fields. Cryptographic attribution of who produced and signed
each verdict is only available with souls enabled.

**The `soul.verify()` bypass (bd-1ca2).** `Soul.verify()` is a pure
cryptographic check — it does not consult the revocation registry. This means
any caller that calls `soul.verify()` directly instead of going through
`SoulRouter.verify_signature()` will accept a revoked soul's signature as
valid. The regression tests in
`tests/test_soul_verify_revocation_through_callers.py` document and cover this
bug. `SoulRouter.verify_signature()` is the correct call site; callers that
have not yet been migrated to it are not protected by the revocation guard.
Evidence bundle signing routes through the correct path via
`agent_baton/core/govern/evidence_bundle.py`, but the bd-1ca2 issue means
any future caller that reaches for `soul.verify()` directly will silently
bypass revocation enforcement.

**Evidence bundle signing depends on the experimental souls feature.** The
`--sign` flag on `baton evidence bundle` is gated behind
`BATON_SOULS_ENABLED=1`. When souls are disabled, `--sign` emits a warning
and produces an unsigned bundle. Tamper detection via SHA-256 manifest
verification still works without souls; cryptographic signing of the manifest
and verdict attribution do not.

**The executable-beads sandbox is process-level only.** When
`BATON_EXEC_BEADS_ENABLED=1`, scripts stored as executable beads run inside a
sandbox in `agent_baton/core/exec/` that enforces a wall-clock timeout,
memory limit, captured stdout/stderr, a static lint denylist, and an
operator-confirmation prompt plus auditor gate. It does NOT provide filesystem
namespacing, network namespacing, or a syscall filter. The trust model assumes
scripts are locally-authored, version-controlled, and team-reviewed. Scripts
from external origins — federation, downloaded packs, fork PRs, customer
uploads — are not covered by this sandbox. `baton beads exec` emits a
`[security]` warning when it detects a non-local `source` value, but that
warning is a tripwire, not a defence. (Source: `docs/architecture.md`,
"Trust Boundary" section.)

**Keyword-only classification when `ANTHROPIC_API_KEY` is absent.** The
`DataClassifier` in `agent_baton/core/govern/classifier.py` is a keyword
matching engine by default. AI-powered classification (higher accuracy on
ambiguous task descriptions) requires `BATON_API_KEY` set and the
`agent-baton[classify]` extra installed. Deployments without the API key fall
back silently to the keyword heuristic; tasks with unusual phrasing may be
under-classified.

**Policy hooks run via Claude Code hooks, not the executor.** `baton
policy-check` and `baton comply-record` are Claude Code settings hooks, not
in-process enforcement inside the Python engine. This means policy evaluation
depends on Claude Code loading `settings.json` and on the hooks being invoked
correctly. Hooks that fail (e.g., because the `baton` binary is not on PATH)
are fail-open by default (`BATON_POLICY_FAIL_CLOSED=0`). There is no
executor-level backstop that catches a policy rule violation the hook missed.

---

## Where this lives

| Area | Location |
|------|----------|
| Governance explanation | [../governance-knowledge-and-events.md](../governance-knowledge-and-events.md) |
| Risk classifier | `agent_baton/core/govern/classifier.py` |
| Policy engine | `agent_baton/core/govern/policy.py` |
| Compliance chain | `agent_baton/core/govern/compliance.py` |
| Assurance packs | `agent_baton/core/govern/packs.py` |
| Evidence bundles | `agent_baton/core/govern/evidence_bundle.py` |
| Auditor agent | `agents/auditor.md` |
| Subject-matter-expert agent | `agents/subject-matter-expert.md` |
| Hook configuration | `templates/settings.json` |
| Spec federation routes | `agent_baton/api/routes/spec_queue.py` |

**Commands:**

```bash
baton classify "<task>"              # Risk classification
baton classify "<task>" --activate   # Classification + activate preset
baton policy                         # List presets
baton policy --show regulated        # Show rules in a preset
baton evidence bundle <task_id>      # Build evidence bundle
baton evidence verify <path>         # Verify bundle integrity
baton packs list                     # List assurance packs
baton packs validate <name>          # Validate pack structure
baton compliance                     # List compliance reports
```
