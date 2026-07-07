"""``baton knowledge`` validation, search, and resolve simulation commands."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from agent_baton.cli.commands.knowledge import (
    ensure_parent_parser,
    register_handler,
)
from agent_baton.core.engine.knowledge_resolver import (
    _DOC_TOKEN_CAP_DEFAULT,
    _INLINE_BYTE_THRESHOLD_DEFAULT,
    KnowledgeResolver,
)
from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry
from agent_baton.core.orchestration.registry import AgentRegistry
from agent_baton.models.knowledge import KnowledgeDocument
from agent_baton.utils.frontmatter import parse_frontmatter


_CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class DoctorIssue:
    """Actionable validation issue emitted by ``knowledge doctor``."""

    severity: str
    code: str
    message: str
    path: str
    pack: str
    doc: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "pack": self.pack,
            "doc": self.doc,
        }


def register(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Hook doctor/search/resolve into the shared ``baton knowledge`` parser."""
    sub = ensure_parent_parser(subparsers)

    doctor_p = sub.add_parser(
        "doctor",
        help="Validate knowledge packs and print actionable warnings",
    )
    doctor_p.add_argument(
        "--knowledge-root",
        action="append",
        default=None,
        help="Knowledge root to validate; repeatable (default: global + project)",
    )
    doctor_p.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format: text (default) or json",
    )
    doctor_p.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when any warning is found",
    )
    doctor_p.add_argument(
        "--json",
        action="store_const",
        const="json",
        dest="format",
        help="Alias for --format json",
    )

    search_p = sub.add_parser(
        "search",
        help="Search knowledge metadata with the registry TF-IDF index",
    )
    search_p.add_argument("query", nargs="+", help="Search query text")
    search_p.add_argument(
        "--knowledge-root",
        action="append",
        default=None,
        help="Knowledge root to search; repeatable (default: global + project)",
    )
    search_p.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum results to return (default: 10)",
    )
    search_p.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Output format: table (default) or json",
    )
    search_p.add_argument(
        "--json",
        action="store_const",
        const="json",
        dest="format",
        help="Alias for --format json",
    )

    resolve_p = sub.add_parser(
        "resolve",
        help="Simulate knowledge attachments for an agent and task",
    )
    resolve_p.add_argument("--agent", required=True, help="Agent name")
    resolve_p.add_argument("--task", required=True, help="Task description")
    resolve_p.add_argument(
        "--knowledge-root",
        action="append",
        default=None,
        help="Knowledge root to load; repeatable (default: global + project)",
    )
    resolve_p.add_argument(
        "--task-type",
        default=None,
        help="Optional task type used by resolver keyword extraction",
    )
    resolve_p.add_argument(
        "--risk",
        default="LOW",
        help="Risk level passed through to resolver simulation",
    )
    resolve_p.add_argument(
        "--knowledge-pack",
        dest="knowledge_pack",
        action="append",
        default=[],
        help="Explicit pack to include; repeatable",
    )
    resolve_p.add_argument(
        "--knowledge",
        action="append",
        default=[],
        help="Explicit document path to include; repeatable",
    )
    resolve_p.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Output format: table (default) or json",
    )
    resolve_p.add_argument(
        "--json",
        action="store_const",
        const="json",
        dest="format",
        help="Alias for --format json",
    )

    register_handler("doctor", _run_doctor)
    register_handler("search", _run_search)
    register_handler("resolve", _run_resolve)
    return subparsers.choices["knowledge"]


def handler(args: argparse.Namespace) -> None:
    """Auto-discovery entry point; delegate to the parent dispatcher."""
    dispatch = getattr(args, "_dispatch", None)
    if dispatch is None:
        raise SystemExit("baton knowledge: dispatcher missing")
    dispatch(args)


def _run_doctor(args: argparse.Namespace) -> None:
    roots = _knowledge_roots_from_args(args)
    explicit = bool(getattr(args, "knowledge_root", None))
    issues, summary = validate_knowledge_roots(roots, require_roots=explicit)
    payload = {
        "ok": not issues,
        "summary": summary,
        "issues": [issue.to_dict() for issue in issues],
    }

    if getattr(args, "format", "text") == "json":
        print(json.dumps(payload, indent=2))
    else:
        print(_render_doctor(payload))

    if getattr(args, "strict", False) and issues:
        raise SystemExit(1)


def _run_search(args: argparse.Namespace) -> None:
    query = " ".join(getattr(args, "query", []))
    limit = max(1, int(getattr(args, "limit", 10) or 10))
    registry = _load_knowledge_registry(getattr(args, "knowledge_root", None))
    results = [
        _search_result_to_dict(registry, doc, score)
        for doc, score in registry.search(query, limit=limit)
    ]
    payload = {"query": query, "results": results}

    if getattr(args, "format", "table") == "json":
        print(json.dumps(payload, indent=2))
    else:
        print(_render_search(payload))


def _run_resolve(args: argparse.Namespace) -> None:
    registry = _load_knowledge_registry(getattr(args, "knowledge_root", None))
    agent_registry = AgentRegistry()
    agent_registry.load_default_paths()
    resolver = KnowledgeResolver(registry, agent_registry=agent_registry)
    attachments = resolver.resolve(
        agent_name=args.agent,
        task_description=args.task,
        task_type=getattr(args, "task_type", None),
        risk_level=getattr(args, "risk", "LOW"),
        explicit_packs=list(getattr(args, "knowledge_pack", []) or []),
        explicit_docs=list(getattr(args, "knowledge", []) or []),
    )
    payload = {
        "agent": args.agent,
        "task": args.task,
        "attachments": [attachment.to_dict() for attachment in attachments],
    }

    if getattr(args, "format", "table") == "json":
        print(json.dumps(payload, indent=2))
    else:
        print(_render_resolve(payload))


def validate_knowledge_roots(
    roots: Iterable[Path],
    *,
    require_roots: bool = False,
) -> tuple[list[DoctorIssue], dict[str, int]]:
    """Validate all pack directories under *roots*.

    The registry intentionally degrades on bad packs so planning can continue.
    Doctor is stricter: it reports the same tolerance points as actionable
    edits without changing runtime loading semantics.

    With *require_roots* (explicit ``--knowledge-root`` arguments), a root
    that does not exist is itself an issue; default roots are allowed to be
    absent.
    """
    issues: list[DoctorIssue] = []
    summary = {
        "roots": 0,
        "packs": 0,
        "documents": 0,
        "warnings": 0,
    }

    if require_roots:
        seen_roots: set[Path] = set()
        for root in roots:
            resolved = root.expanduser()
            # De-dup consistent with _unique_existing_roots so a repeated
            # --knowledge-root argument doesn't inflate the warning count.
            dedup_key = resolved.resolve()
            if dedup_key in seen_roots:
                continue
            seen_roots.add(dedup_key)
            if not resolved.is_dir():
                issues.append(_issue(
                    code="missing-root",
                    path=resolved,
                    pack="",
                    message=(
                        f"Knowledge root '{root}' does not exist or is not a "
                        "directory. Fix the --knowledge-root argument or "
                        "create the directory."
                    ),
                ))

    for root in _unique_existing_roots(roots):
        summary["roots"] += 1
        try:
            pack_candidates = sorted(root.iterdir())
        except OSError as exc:
            issues.append(_issue(
                code="unreadable-pack",
                path=root,
                pack="",
                message=(
                    f"Knowledge root '{root}' could not be listed: {exc}. "
                    "Check directory permissions and re-run doctor."
                ),
            ))
            continue

        for pack_dir in pack_candidates:
            # Isolate per-pack filesystem failures (Windows ACLs, cloud-sync
            # placeholders) so one unreadable pack doesn't abort the whole
            # doctor run — mirror KnowledgeRegistry.load_directory's guard.
            try:
                if not pack_dir.is_dir():
                    continue
                summary["packs"] += 1
                manifest, manifest_ok = _read_manifest(pack_dir, issues)
                default_delivery = str(
                    manifest.get("default_delivery") or "reference"
                ).strip().lower()

                declared_paths = _declared_doc_paths(manifest)
                for rel_path in declared_paths:
                    doc_path = pack_dir / rel_path
                    if not _declared_doc_exists(pack_dir, rel_path):
                        issues.append(_issue(
                            code="missing-declared-file",
                            path=doc_path,
                            pack=pack_dir.name,
                            message=(
                                f"Pack '{pack_dir.name}' declares missing document "
                                f"'{rel_path}'. Edit {pack_dir / 'knowledge.yaml'} "
                                "or create that file."
                            ),
                        ))

                if manifest_ok and not str(manifest.get("description") or "").strip():
                    issues.append(_issue(
                        code="empty-pack-description",
                        path=pack_dir / "knowledge.yaml",
                        pack=pack_dir.name,
                        message=(
                            f"Pack '{pack_dir.name}' has an empty description. "
                            f"Edit {pack_dir / 'knowledge.yaml'} and add a "
                            "description for search and resolver matching."
                        ),
                    ))

                names: dict[str, list[Path]] = {}
                for doc_path in sorted(pack_dir.glob("*.md")):
                    summary["documents"] += 1
                    doc_metadata = _read_doc_metadata(
                        doc_path, pack_dir.name, issues
                    )
                    if doc_metadata is None:
                        continue
                    doc_name, metadata, raw = doc_metadata
                    names.setdefault(doc_name, []).append(doc_path)

                    description = str(metadata.get("description") or "").strip()
                    if not description:
                        issues.append(_issue(
                            code="empty-doc-description",
                            path=doc_path,
                            pack=pack_dir.name,
                            doc=doc_name,
                            message=(
                                f"Document '{doc_name}' has an empty description. "
                                f"Edit {doc_path} frontmatter and add description."
                            ),
                        ))

                    if _is_large_inline_candidate(
                        doc_path, raw, default_delivery=default_delivery
                    ):
                        issues.append(_issue(
                            code="large-inline-candidate",
                            path=doc_path,
                            pack=pack_dir.name,
                            doc=doc_name,
                            message=(
                                f"Document '{doc_name}' is too large for likely "
                                f"inline delivery. Edit {doc_path} to shorten it "
                                f"or edit {pack_dir / 'knowledge.yaml'} and set "
                                "default_delivery: reference."
                            ),
                        ))

                for doc_name, paths in sorted(names.items()):
                    if len(paths) <= 1:
                        continue
                    joined = ", ".join(str(p) for p in paths)
                    issues.append(_issue(
                        code="duplicate-doc-name",
                        path=paths[0],
                        pack=pack_dir.name,
                        doc=doc_name,
                        message=(
                            f"Document name '{doc_name}' is duplicated in "
                            f"{joined}. Edit one frontmatter name field so each "
                            "document name is unique within the pack."
                        ),
                    ))
            except OSError as exc:
                issues.append(_issue(
                    code="unreadable-pack",
                    path=pack_dir,
                    pack=pack_dir.name,
                    message=(
                        f"Pack '{pack_dir.name}' could not be validated: {exc}. "
                        "Check directory permissions and re-run doctor."
                    ),
                ))
                continue

    summary["warnings"] = len(issues)
    return issues, summary


def _knowledge_roots_from_args(args: argparse.Namespace) -> list[Path]:
    raw_roots = getattr(args, "knowledge_root", None)
    if raw_roots:
        return [Path(root) for root in raw_roots]
    return _default_knowledge_roots()


def _default_knowledge_roots() -> list[Path]:
    return [
        Path.home() / ".claude" / "knowledge",
        Path.cwd() / ".claude" / "knowledge",
    ]


def _unique_existing_roots(roots: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    existing: list[Path] = []
    for root in roots:
        resolved = root.expanduser().resolve()
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        existing.append(resolved)
    return existing


def _read_manifest(
    pack_dir: Path,
    issues: list[DoctorIssue],
) -> tuple[dict[str, Any], bool]:
    manifest_path = pack_dir / "knowledge.yaml"
    if not manifest_path.is_file():
        issues.append(_issue(
            code="missing-manifest",
            path=manifest_path,
            pack=pack_dir.name,
            message=(
                f"Pack '{pack_dir.name}' is missing knowledge.yaml. "
                f"Edit {manifest_path} and add name, description, tags, "
                "target_agents, and default_delivery."
            ),
        ))
        return {}, False

    try:
        parsed = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        issues.append(_issue(
            code="invalid-manifest",
            path=manifest_path,
            pack=pack_dir.name,
            message=(
                f"Pack '{pack_dir.name}' has invalid knowledge.yaml: {exc}. "
                f"Edit {manifest_path} and fix the YAML."
            ),
        ))
        return {}, False

    if parsed is None:
        return {}, True

    if not isinstance(parsed, dict):
        issues.append(_issue(
            code="invalid-manifest",
            path=manifest_path,
            pack=pack_dir.name,
            message=(
                f"Pack '{pack_dir.name}' knowledge.yaml must be a mapping. "
                f"Edit {manifest_path} and use YAML key/value fields."
            ),
        ))
        return {}, False

    return parsed, True


def _declared_doc_paths(manifest: dict[str, Any]) -> list[str]:
    raw_docs = manifest.get("documents") or manifest.get("docs") or []
    if not isinstance(raw_docs, list):
        return []

    paths: list[str] = []
    for item in raw_docs:
        candidate: object
        if isinstance(item, str):
            candidate = item
        elif isinstance(item, dict):
            candidate = (
                item.get("path")
                or item.get("file")
                or item.get("source")
                or item.get("name")
            )
        else:
            candidate = None
        if isinstance(candidate, str) and candidate.strip():
            paths.append(candidate.strip())
    return paths


# Suffixes that are plausible standalone file types in their own right.
# A declaration ending in one of these (e.g. "config.json") should not
# be satisfied by a shadow "config.json.md" — only genuinely extensionless
# stems (e.g. "notes.v2") get the +.md fallback.
_KNOWN_NON_MD_EXTENSIONS = {
    ".json", ".yaml", ".yml", ".txt", ".py", ".toml", ".csv",
}


def _declared_doc_exists(pack_dir: Path, rel_path: str) -> bool:
    doc_path = pack_dir / rel_path
    if doc_path.is_file():
        return True
    suffix = doc_path.suffix.lower()
    if suffix == ".md" or suffix in _KNOWN_NON_MD_EXTENSIONS:
        return False
    return doc_path.with_name(doc_path.name + ".md").is_file()


def _read_doc_metadata(
    doc_path: Path,
    pack_name: str,
    issues: list[DoctorIssue],
) -> tuple[str, dict[str, Any], str] | None:
    try:
        raw = doc_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        issues.append(_issue(
            code="unreadable-doc",
            path=doc_path,
            pack=pack_name,
            doc=doc_path.stem,
            message=(
                f"Document '{doc_path.name}' cannot be read: {exc}. "
                f"Edit file permissions for {doc_path}."
            ),
        ))
        return None

    metadata, _body = parse_frontmatter(raw)
    if not isinstance(metadata, dict):
        issues.append(_issue(
            code="invalid-doc-frontmatter",
            path=doc_path,
            pack=pack_name,
            doc=doc_path.stem,
            message=(
                f"Document '{doc_path.stem}' frontmatter must be a mapping. "
                f"Edit {doc_path} and use YAML key/value fields."
            ),
        ))
        return None

    if not metadata:
        issues.append(_issue(
            code="empty-doc-metadata",
            path=doc_path,
            pack=pack_name,
            doc=doc_path.stem,
            message=(
                f"Document '{doc_path.stem}' has no frontmatter metadata. "
                f"Edit {doc_path} and add YAML frontmatter with name, "
                "description, tags, grounding, and priority."
            ),
        ))

    doc_name = str(metadata.get("name") or "").strip() or doc_path.stem
    return doc_name, metadata, raw


def _is_large_inline_candidate(
    doc_path: Path,
    raw: str,
    *,
    default_delivery: str,
) -> bool:
    if default_delivery != "inline":
        return False
    token_estimate = max(1, len(raw) // _CHARS_PER_TOKEN) if raw else 0
    try:
        byte_size = doc_path.stat().st_size
    except OSError:
        byte_size = 0
    return (
        token_estimate > _DOC_TOKEN_CAP_DEFAULT
        or byte_size > _INLINE_BYTE_THRESHOLD_DEFAULT
    )


def _issue(
    *,
    code: str,
    path: Path,
    pack: str,
    message: str,
    doc: str = "",
) -> DoctorIssue:
    return DoctorIssue(
        severity="warning",
        code=code,
        message=message,
        path=str(path),
        pack=pack,
        doc=doc,
    )


def _load_knowledge_registry(raw_roots: list[str] | None = None) -> KnowledgeRegistry:
    registry = KnowledgeRegistry()
    if raw_roots:
        for root in raw_roots:
            registry.load_directory(Path(root).expanduser(), override=True)
    else:
        registry.load_default_paths()
    return registry


def _find_pack_name(registry: KnowledgeRegistry, doc: KnowledgeDocument) -> str:
    for pack in registry.all_packs.values():
        for pack_doc in pack.documents:
            if pack_doc is doc:
                return pack.name
    return ""


def _search_result_to_dict(
    registry: KnowledgeRegistry,
    doc: KnowledgeDocument,
    score: float,
) -> dict[str, Any]:
    return {
        "pack": _find_pack_name(registry, doc),
        "doc": doc.name,
        "score": round(score, 6),
        "path": str(doc.source_path) if doc.source_path is not None else "",
        "tags": list(doc.tags),
        "priority": doc.priority,
        "token_estimate": doc.token_estimate,
    }


def _render_doctor(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "Knowledge doctor",
        (
            f"roots={summary['roots']} packs={summary['packs']} "
            f"documents={summary['documents']} warnings={summary['warnings']}"
        ),
    ]
    issues = payload["issues"]
    if not issues:
        lines.append("OK: no knowledge pack warnings found.")
        return "\n".join(lines)
    for issue in issues:
        lines.append(
            f"WARNING [{issue['code']}] {issue['message']}"
        )
    return "\n".join(lines)


def _render_search(payload: dict[str, Any]) -> str:
    rows = payload["results"]
    if not rows:
        return f"No knowledge results for: {payload['query']}"
    lines = [
        "| Pack | Document | Score | Priority | Tokens | Path | Tags |",
        "|------|----------|-------|----------|--------|------|------|",
    ]
    for row in rows:
        tags = ", ".join(row["tags"])
        lines.append(
            f"| {row['pack']} | {row['doc']} | {row['score']:.6f} "
            f"| {row['priority']} | {row['token_estimate']} "
            f"| {row['path']} | {tags} |"
        )
    return "\n".join(lines)


def _render_resolve(payload: dict[str, Any]) -> str:
    rows = payload["attachments"]
    if not rows:
        return (
            f"No knowledge attachments resolved for agent '{payload['agent']}'."
        )
    lines = [
        "| Source | Pack | Document | Delivery | Retrieval | Tokens | Path |",
        "|--------|------|----------|----------|-----------|--------|------|",
    ]
    for row in rows:
        lines.append(
            f"| {row['source']} | {row.get('pack_name') or ''} "
            f"| {row['document_name']} | {row['delivery']} "
            f"| {row['retrieval']} | {row['token_estimate']} "
            f"| {row['path']} |"
        )
    return "\n".join(lines)
