---
name: api-conventions-example
description: Example knowledge pack — REST API conventions for a Python/FastAPI project
tags: [api, rest, python, fastapi, conventions]
applies_to: [backend-engineer, backend-engineer--python, architect]
---

# API Conventions

This is an **example** knowledge pack. Replace with your project's actual conventions.

## URL Patterns

- Resources are plural: `/api/v1/users`, `/api/v1/orders`
- Nested resources: `/api/v1/users/{user_id}/orders`
- Actions that aren't CRUD: `/api/v1/orders/{id}/cancel` (POST)

## Response Format

All responses follow this envelope:
```json
{
  "data": { ... },
  "meta": { "request_id": "...", "timestamp": "..." },
  "errors": []
}
```

## Error Codes

| HTTP Status | When to use |
|-------------|-------------|
| 400 | Validation failure (missing field, wrong type) |
| 401 | Not authenticated |
| 403 | Authenticated but not authorized |
| 404 | Resource not found |
| 409 | Conflict (duplicate, version mismatch) |
| 422 | Business rule violation |
| 500 | Unexpected server error (always log full trace) |

## Pagination

Use cursor-based pagination for all list endpoints:
```
GET /api/v1/users?cursor=abc123&limit=50
```