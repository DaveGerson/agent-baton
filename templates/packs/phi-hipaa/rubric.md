<!-- ILLUSTRATIVE TEMPLATE ONLY — not maintained compliance content. -->
# HIPAA PHI Review Rubric

References: 45 CFR Part 164 (Security Rule), 45 CFR Part 164.524 (Access), HIPAA Privacy Rule.

## Pre-execution checks

- [ ] Subject-matter-expert has reviewed the plan and confirmed PHI scope.
- [ ] Auditor pre-execution review is complete and signed off.
- [ ] All PHI data paths are identified and access-controlled.
- [ ] Minimum-necessary principle applied — only required PHI fields accessed.
- [ ] Business Associate Agreement (BAA) confirmed for any third-party services.

## Implementation checks

- [ ] PHI is never written to log files, error messages, or debug output.
- [ ] All PHI at rest is encrypted (AES-256 or equivalent).
- [ ] All PHI in transit uses TLS 1.2+ with valid certificates.

## Post-execution checks

- [ ] Audit trail gate passed — every write logged with who/when/what/why.
- [ ] PHI scan gate passed — no PHI found in unencrypted output.
- [ ] Auditor post-execution review completed.
- [ ] Evidence artifacts collected (see evidence.json for required list).
- [ ] Append-only constraints verified for health records.
- [ ] Breach risk assessment completed if any unexpected PHI exposure.
