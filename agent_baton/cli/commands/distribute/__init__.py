"""CLI command group: distribute.

Commands for packaging, publishing, and sharing agent definitions,
references, and knowledge packs across projects and registries.

Commands:
    * ``baton package`` -- Create, inspect, or install package archives.
    * ``baton publish`` -- Publish a package to a local registry.
    * ``baton pull`` -- Install a package from a local registry.
    * ``baton verify-package`` -- Validate a ``.tar.gz`` package.
    * ``baton install`` -- Install agents and references to user/project scope.
    * ``baton transfer`` -- Transfer items between projects.
"""
from __future__ import annotations
