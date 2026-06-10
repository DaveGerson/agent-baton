<!-- ILLUSTRATIVE TEMPLATE ONLY — not maintained compliance content. -->
# OWASP Secure Coding Review Rubric

References: OWASP Top 10 (2021), CWE/SANS Top 25.

## OWASP A01 — Broken Access Control

- [ ] Access controls verified: users cannot act outside their intended permissions.
- [ ] Directory traversal and IDOR checks completed.

## OWASP A02 — Cryptographic Failures

- [ ] No sensitive data transmitted in plaintext.
- [ ] Deprecated cryptographic algorithms replaced (MD5, SHA-1, DES).
- [ ] Encryption at rest confirmed for sensitive data.

## OWASP A03 — Injection

- [ ] All user-supplied inputs validated and sanitised.
- [ ] Parameterised queries / prepared statements used for SQL.
- [ ] No dynamic code execution from untrusted input.

## OWASP A04 — Insecure Design

- [ ] Threat model reviewed for new features.
- [ ] Security requirements captured before implementation.

## OWASP A05 — Security Misconfiguration

- [ ] Default credentials changed or removed.
- [ ] Unnecessary features/endpoints disabled.
- [ ] Error messages do not expose stack traces to end users.

## Secret scan gate

- [ ] secret_scan gate passed — no hardcoded credentials in committed code.
- [ ] No API keys, passwords, or tokens found in source files.
- [ ] All credentials sourced from environment variables or a secret manager.

## Reviewer sign-off

- [ ] Security reviewer completed post-execution review.
- [ ] Code reviewer completed implementation review.
