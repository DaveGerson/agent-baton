# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Agent Baton, please report it
responsibly. **Do not open a public issue.**

Email your report to the maintainers via the repository's GitHub Security
Advisories tab, or open a private vulnerability report at:

> **https://github.com/DaveGerson/agent-baton/security/advisories/new**

Include:

1. A description of the vulnerability
2. Steps to reproduce
3. The potential impact
4. Any suggested fix (optional)

We aim to acknowledge reports within 48 hours and provide a fix timeline
within 7 days.

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |

## Security Model

Agent Baton runs locally on your machine. It does not phone home, collect
telemetry, or require external API keys beyond your Claude Code session.

Key security boundaries:

- **Gate commands** are executed as shell subprocesses. Only run plans you
  trust, and review gate commands before approving execution.
- **Package archives** (created via `baton package`) should be verified
  with `baton verify-package` before installation.
- **The REST API** (`baton serve`) binds to localhost by default. If you
  expose it to a network, enable bearer token auth (`--token`).
- **Hook configurations** in `.claude/settings.json` block writes to
  `.env`, secrets, keys, and credential files by default.
