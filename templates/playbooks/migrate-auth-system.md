# Migrate to a New Authentication System

A curated workflow for swapping out a project's auth provider with
minimal disruption to active sessions.

## When to use

- Replacing a homegrown session store with a managed identity provider
- Consolidating multiple auth flows into a single OIDC-compliant path
- Hardening an existing auth surface after a security review

## Phases

1. **Inventory** — enumerate every entry point that authenticates
   users (HTTP handlers, background workers, CLI tools, webhooks).
2. **Shadow** — run the new provider alongside the old one and mirror
   verifications without enforcing.
3. **Cutover** — switch the canonical check to the new provider; keep
   the old path as an emergency fallback for 24 hours.
4. **Decommission** — remove the legacy provider, rotate any shared
   secrets, and update the threat model.

## Suggested agents

- `architect` for the inventory + cutover sequencing
- `security-reviewer` to gate the shadow → cutover transition
- `auditor` for the post-cutover verification
