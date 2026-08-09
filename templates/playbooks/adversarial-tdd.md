# Adversarial-TDD Delivery Workflow

A staged, model-tiered, adversarially verified TDD pipeline for building
a new capability. Available as a built-in workflow preset:
`baton plan "<task>" --workflow adversarial-tdd`.

## When to use

New capabilities where correctness matters more than speed: the spec is
written first, tests are authored before code and independently verified
against the spec, implementation runs at an economical tier, and the
result is verified twice before a top-tier final review.

## Phases

1. **Brainstorm & Spec** (`architect`, fable) — frame the capability,
   write behaviors, non-goals, and acceptance criteria to
   `.claude/team-context/executions/<task>/spec.md`.
2. **Architecture** (`architect`, fable) — solution design and a
   file-boundary map for the implementers.
3. **Test Authoring** (`test-engineer`, opus) — failing tests that encode
   the spec's behaviors. No implementation code.
4. **Test Verification** (`test-adequacy-reviewer`, opus) — differently
   scoped: reads spec + tests only, and verifies the tests pin the
   intended behaviors.
5. **Implementation** (planned specialists, sonnet) — implement until the
   tests pass; gated on the stack's test command.
6. **Implementation Verification** (`code-reviewer`, opus) — verify the
   implementation against the spec beyond "tests pass". Optionally add an
   external-vendor verifier (gemini/codex CLI) via the
   `workflow.external_command` setting in `baton.yaml`.
7. **Final Review** (`code-reviewer`, fable) — whole-slice review; fans
   out to multiple reviewers for large slices.

## Suggested agents

- `architect` for spec and architecture (pinned to fable by the preset)
- `test-engineer` for TDD authoring, `test-adequacy-reviewer` to verify it
- sonnet `general-purpose` agents for research support at any stage
- `code-reviewer` for verification and final review
- `auditor` phases from the base plan are carried over automatically on
  regulated-domain tasks
