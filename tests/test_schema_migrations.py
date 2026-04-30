from __future__ import annotations

import ast
from pathlib import Path


def test_migrations_keys_are_unique() -> None:
    """Catch duplicate migration version numbers (e.g., the key-16 bug).

    Python dicts silently drop duplicate keys at runtime, so the later
    definition wins without any error.  We parse the source with ast to
    detect duplicates before they can cause silent data-loss.

    Handles both plain ``Assign`` and annotated ``AnnAssign`` forms so the
    check works regardless of whether MIGRATIONS carries a type annotation.
    """
    schema_path = (
        Path(__file__).resolve().parent.parent
        / "agent_baton"
        / "core"
        / "storage"
        / "schema.py"
    )
    source = schema_path.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        # MIGRATIONS is declared as an AnnAssign: MIGRATIONS: dict[int, str] = {...}
        if isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value
        elif isinstance(node, ast.Assign):
            if len(node.targets) != 1:
                continue
            target = node.targets[0]
            value = node.value
        else:
            continue

        if not (isinstance(target, ast.Name) and target.id == "MIGRATIONS"):
            continue
        if not isinstance(value, ast.Dict):
            continue

        keys = [k.value for k in value.keys if isinstance(k, ast.Constant)]
        duplicates = sorted({k for k in keys if keys.count(k) > 1})
        assert not duplicates, f"Duplicate MIGRATIONS keys: {duplicates}"
