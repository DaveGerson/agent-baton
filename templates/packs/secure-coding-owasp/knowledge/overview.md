<!-- ILLUSTRATIVE TEMPLATE ONLY — not maintained compliance content. -->
# OWASP Secure Coding Domain Overview

References: OWASP Top 10 (2021), CWE/SANS Top 25 Most Dangerous Software Weaknesses.

## What this pack governs

Any task involving authentication, authorisation, input handling, cryptography,
secrets management, or third-party dependencies falls under this pack.

## OWASP Top 10 (2021) quick reference

| ID | Category | Key concern |
|----|----------|-------------|
| A01 | Broken Access Control | 94% of apps tested; path traversal, IDOR, privilege escalation |
| A02 | Cryptographic Failures | Sensitive data in plaintext, weak ciphers, improper key storage |
| A03 | Injection | SQL, NoSQL, OS, LDAP injection; XSS; template injection |
| A04 | Insecure Design | Missing threat models, flawed business logic, no security requirements |
| A05 | Security Misconfiguration | Default creds, unnecessary features, verbose error messages |
| A06 | Vulnerable Components | Outdated libraries, unpatched CVEs, missing SCA scanning |
| A07 | Auth Failures | Weak passwords, missing MFA, improper session management |
| A08 | Software Integrity Failures | Unsigned updates, insecure CI/CD pipelines, unsafe deserialisation |
| A09 | Logging Failures | No audit logs, sensitive data in logs, insufficient monitoring |
| A10 | SSRF | Unvalidated URLs, internal service exposure via user-supplied URLs |

## Credential hygiene rules

- Never hardcode passwords, API keys, tokens, or private keys in source files.
- Source all credentials from environment variables or a dedicated secret manager.
- Rotate secrets immediately if accidentally committed — treat as compromised.
- Use short-lived tokens over long-lived API keys wherever possible.

## Resources

- [OWASP Top 10 (2021)](https://owasp.org/Top10/)
- [OWASP Secure Coding Practices](https://owasp.org/www-project-secure-coding-practices-quick-reference-guide/)
- [CWE/SANS Top 25](https://cwe.mitre.org/top25/)
