---
name: compliance-example
description: Example knowledge pack — SOX compliance context for financial systems
tags: [compliance, financial, audit, sox]
applies_to: [auditor, subject-matter-expert, backend-engineer]
---

# SOX Compliance Context

This is an **example** knowledge pack demonstrating the format. Replace this
content with your actual domain knowledge.

## Key Requirements

- All financial data mutations must be audit-logged with timestamp, user, and before/after values
- Access to financial records requires role-based authorization (not just authentication)
- Data retention: 7 years minimum for transaction records
- Change management: all production changes require documented approval

## Common Patterns

When building features that touch financial data:
1. Always log to the audit trail before committing the transaction
2. Use optimistic locking to prevent concurrent mutation
3. Never delete financial records — use soft-delete with retention policy
4. Encrypt PII fields at rest (AES-256) and in transit (TLS 1.2+)

## Glossary

| Term | Definition |
|------|-----------|
| SOD | Separation of Duties — no single person can both initiate and approve |
| MDA | Management Discussion and Analysis — quarterly disclosure requirement |
| ICFR | Internal Controls over Financial Reporting |