<!-- ILLUSTRATIVE TEMPLATE ONLY — not maintained compliance content. -->
# HIPAA PHI Domain Overview

References: HIPAA Privacy Rule (45 CFR Part 164), Security Rule (45 CFR Part 164.300–318).

## What this pack governs

Protected Health Information (PHI) is individually identifiable health information
held or transmitted by a covered entity or business associate.  Any task that
reads, writes, transmits, or transforms PHI data falls under this pack.

## Key concepts

| Term | Definition |
|------|-----------|
| PHI | Protected Health Information — 18 HIPAA-identified data elements |
| Covered Entity | Health plans, healthcare clearinghouses, healthcare providers |
| Business Associate | Third-party vendors that handle PHI on behalf of a covered entity |
| Minimum Necessary | Only access the minimum PHI needed to accomplish the task |
| BAA | Business Associate Agreement — required before sharing PHI with vendors |
| ePHI | Electronic PHI — PHI stored or transmitted electronically |

## The 18 PHI identifiers

Names, geographic data, dates (except year), phone numbers, fax numbers,
email addresses, SSNs, medical record numbers, health plan beneficiary numbers,
account numbers, certificate/license numbers, VINs, device identifiers,
URLs, IP addresses, biometric identifiers, full-face photographs,
any other unique identifying number or code.

## Required safeguards

- **Encryption at rest:** AES-256 or equivalent
- **Encryption in transit:** TLS 1.2+
- **Access control:** Role-based; minimum necessary
- **Audit logging:** All access and modifications logged
- **Retention:** Per state law (minimum 6 years for HIPAA records)

## Resources

- [HHS HIPAA for Professionals](https://www.hhs.gov/hipaa/for-professionals/index.html)
- [NIST SP 800-66](https://csrc.nist.gov/publications/detail/sp/800-66/rev-2/final)
