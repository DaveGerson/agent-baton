# Incident Response Runbook

A lightweight runbook for triaging unexpected production incidents
without panicking the on-call rotation.

## Phases

1. **Detect** — confirm the alert; check the dashboard for a second
   independent signal.
2. **Contain** — disable the failing path, fail traffic to a safe
   default, or roll back the most recent deploy.
3. **Diagnose** — capture logs, traces, and a snapshot of the failing
   subsystem before mutating anything.
4. **Repair** — apply the smallest reversible fix; deploy through the
   normal pipeline (no hotfixes).
5. **Retro** — within 48 hours: timeline, root cause, action items,
   and a "what went well" section.

## Suggested agents

- `devops-engineer` to drive containment and the deploy rollback
- `subject-matter-expert` for the affected domain
- `auditor` to file the retrospective bead trail
