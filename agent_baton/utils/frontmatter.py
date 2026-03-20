"""Parse YAML frontmatter from markdown files."""
from __future__ import annotations

import yaml


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Split markdown with YAML frontmatter into (metadata, body).

    Expects content starting with '---', followed by YAML, followed by '---',
    followed by the markdown body.

    Returns:
        Tuple of (metadata dict, body string). If no valid frontmatter is found,
        returns ({}, original content).
    """
    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    try:
        metadata = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, content

    body = parts[2].strip()
    return metadata, body
