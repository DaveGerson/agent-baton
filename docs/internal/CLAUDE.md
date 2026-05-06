# docs/internal/ — maintainer-only working directory

**Not published.** Excluded from MkDocs and the public site. Cross-cutting rules: [../../CLAUDE.md](../../CLAUDE.md). Public-doc rules: [../CLAUDE.md](../CLAUDE.md).

## What goes here

- Doc audits, doc IA, writer's-contract guidelines (`doc-audit.md`, `doc-ia.md`, `doc-guiding-principles.md`)
- Architectural design documents in progress (`005b-phase{1,2,3}-design.md`)
- Codebase reviews and retrospectives (`CODEBASE_REVIEW.md`, `REVIEW-*.md`)
- Diagnostic triage (`pyright-diagnostics-triage.md`)
- TODO and finding lists (`TODO-*.md`)
- `audit/`, `competitive-audit/`, `reviews/` — collected analyses

## What does NOT go here

- User-facing docs — those go in `docs/` (the parent) and follow Diátaxis.
- Decisions worth advertising — once a design is final, summarize it in `docs/design-decisions.md` and link back here for the working trail.
- Source code, plans, or beads — those live in their respective directories.

## Conventions

- Every document opens with a one-line **status**: `Draft`, `In review`, `Accepted`, or `Superseded`. Superseded docs link forward to their replacement.
- Reviews and audits are dated in the filename or the first heading.
- Never delete a historical review — it's the audit trail. Mark it `Superseded` instead.
- Don't link to files in here from public docs (`docs/*.md`). Internal-only links stay internal.

## Adding internal docs

1. Pick the right neighbor: design docs at the top, audits/reviews under their subdirectory.
2. Set the status line.
3. If the doc supersedes a previous decision, update the older doc's status to `Superseded by <link>`.
4. Note new design decisions in `docs/design-decisions.md` (public) once they're `Accepted`.
