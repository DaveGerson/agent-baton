# agent_baton/core/govern/ — risk, guardrails, compliance

Decides whether a step is allowed to run, with what guardrails, and who has to approve. Inherits: [../../../CLAUDE.md](../../../CLAUDE.md), [../CLAUDE.md](../CLAUDE.md).

## Files

| File | Role |
|------|------|
| `classifier.py` | `DataClassifier` — risk classification (uses Haiku via `ANTHROPIC_API_KEY` when set) |
| `policy.py` | Policy lookup: which guardrail preset applies to a given task |
| `validator.py`, `spec_validator.py` | Plan and spec validation |
| `compliance.py` | Compliance evaluators (regulated-data, audit chain) |
| `budget.py` | Token / cost budget enforcement |
| `escalation.py` | Escalation chain — who to ask when self-approval isn't allowed |
| `aibom.py` | AI bill-of-materials emission |
| `override_log.py` | Auditable record of every guardrail override |
| `_redaction.py` | PII / secrets redaction helpers — internal only |

## Mandatory rules

- **Regulated-data work** (compliance systems, audit-controlled records, industry-specific business rules) MUST involve the `subject-matter-expert` and `auditor` agents and follow the Regulated Data guardrail preset. See [../../../references/guardrail-presets.md](../../../references/guardrail-presets.md) and [../../../references/compliance-audit-chain.md](../../../references/compliance-audit-chain.md).
- **Every override is logged** through `override_log.py`. Don't add a code path that bypasses guardrails without a logged override.
- **`BATON_APPROVAL_MODE=team`** disables self-approval — the approver must differ from the actor. Don't add code that special-cases this away.

## Conventions

- The classifier is the **only** module that calls Anthropic for risk decisions. Other code paths consume its output.
- Guardrail decisions are **inputs to the engine**, not actions taken inside `govern/`. Return a decision; let `engine/dispatcher.py` enforce it.
- Compliance evaluators are pure functions of (plan, context). No side effects, no I/O outside the audit log.

## When you add a guardrail or preset

1. Define it in `policy.py` and (if needed) add a compliance evaluator in `compliance.py`.
2. Document the preset in `references/guardrail-presets.md`.
3. Add tests under `tests/govern/`.
4. If it touches regulated data, add a regression test that confirms the auditor agent gate fires.

## Don'ts

- Don't enforce guardrails outside `govern/`. Other modules consume decisions.
- Don't log raw user data — route everything through `_redaction.py` first.
- Don't make classification non-deterministic without gating it behind a feature flag.
