---
name: conventions
description: Coding conventions for the medium-project fixture repo
tags: [conventions, style, python]
priority: normal
---

# Coding Conventions

- Use `dataclasses` for simple value objects (see `app/reporting/service.py`).
- Type-hint every function signature.
- Keep service classes free of I/O; I/O belongs at the edges.
