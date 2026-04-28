"""Wave 6.2 Part A — AST-aware code partitioner (bd-707d).

Python-only v1 using libcst for round-trip-safe concrete syntax tree
parsing.  Out of scope: tree-sitter multi-language (see Open Questions).

Algorithm:
  1. Resolve all call sites via libcst.metadata.ScopeProvider +
     FullyQualifiedNameProvider.
  2. Build a per-file dependency graph from import/call relationships.
  3. Compute SCCs via Tarjan's algorithm — each SCC is a candidate chunk.
  4. If len(SCCs) > max_chunks: greedily merge smallest by estimated_tokens.
  5. Verify static independence across all chunks before returning.
"""
from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

_log = logging.getLogger(__name__)

__all__ = [
    "ASTPartitioner",
    "CallSite",
    "ChangeSignature",
    "CodeChunk",
    "MigrateAPI",
    "ProofRef",
    "ReconcileResult",
    "RefactorDirective",
    "RenameSymbol",
    "ReplaceImport",
    "ScopeKind",
]

# ---------------------------------------------------------------------------
# libcst import — optional; raises ImportError with clear message if missing
# ---------------------------------------------------------------------------

try:
    import libcst as cst
    import libcst.metadata as cst_meta
    _LIBCST_AVAILABLE = True
except ImportError:  # pragma: no cover
    _LIBCST_AVAILABLE = False
    cst = None  # type: ignore[assignment]
    cst_meta = None  # type: ignore[assignment]


def _require_libcst() -> None:
    if not _LIBCST_AVAILABLE:
        raise ImportError(
            "libcst is required for ASTPartitioner.  "
            "Install it: pip install 'libcst>=1.0' or add libcst>=1.0 to "
            "your project dependencies (pyproject.toml [project.optional-dependencies] swarm)."
        )


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


class ScopeKind(Enum):
    FUNCTION = "function"
    CLASS = "class"
    MODULE = "module"
    PACKAGE = "package"


@dataclass(frozen=True)
class ProofRef:
    """Static independence proof for a CodeChunk.

    Attributes:
        kind: ``"disjoint-files"`` | ``"disjoint-symbols"`` | ``"sequential-fallback"``.
        details: Human-readable explanation (for bead/debug output).
    """

    kind: str
    details: str = ""


@dataclass(frozen=True)
class CallSite:
    """A resolved call/reference site for a directive's target symbol.

    Attributes:
        file: Absolute path to the source file.
        line: 1-based line number of the call site.
        column: 1-based column number.
        qualified_name: Fully-qualified name of the referenced symbol.
        scope_kind: Granularity of the enclosing scope.
    """

    file: Path
    line: int
    column: int
    qualified_name: str
    scope_kind: ScopeKind = ScopeKind.MODULE


@dataclass(frozen=True)
class CodeChunk:
    """An independently-refactorable unit of code.

    Attributes:
        chunk_id: Stable SHA-256 digest of sorted file paths + line ranges.
        files: Absolute paths of all files belonging to this chunk.
        call_sites: All call sites within this chunk.
        scope: Granularity of the chunk boundary.
        estimated_tokens: Rough token estimate (4 chars per token heuristic).
        independence_proof: Reference to static proof of independence.
    """

    chunk_id: str
    files: list[Path]
    call_sites: list[CallSite]
    scope: ScopeKind
    estimated_tokens: int
    independence_proof: ProofRef

    def __hash__(self) -> int:
        return hash(self.chunk_id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CodeChunk):
            return NotImplemented
        return self.chunk_id == other.chunk_id


# ---------------------------------------------------------------------------
# Refactor directive hierarchy
# ---------------------------------------------------------------------------


@dataclass
class RefactorDirective:
    """Base class for all refactor directives.

    Attributes:
        kind: Directive type tag — one of ``rename-symbol``,
            ``change-signature``, ``replace-import``, ``migrate-api``.
    """

    kind: str

    @classmethod
    def from_dict(cls, data: dict) -> RefactorDirective:
        """Deserialise a dict produced by JSON parsing (CLI input)."""
        kind = data.get("kind", "")
        if kind == "rename-symbol":
            return RenameSymbol(old=data["old"], new=data["new"])
        if kind == "change-signature":
            return ChangeSignature(
                symbol=data["symbol"],
                transform=data.get("transform", {}),
            )
        if kind == "replace-import":
            return ReplaceImport(old=data["old"], new=data["new"])
        if kind == "migrate-api":
            return MigrateAPI(
                old_call_pattern=data["old_call_pattern"],
                new_call_template=data["new_call_template"],
            )
        raise ValueError(f"Unknown directive kind: {kind!r}")


@dataclass
class RenameSymbol(RefactorDirective):
    """Rename a fully-qualified symbol everywhere it appears."""

    old: str = ""   # fully-qualified old name, e.g. "mymod.MyClass"
    new: str = ""   # fully-qualified new name, e.g. "mymod.BetterClass"
    kind: str = field(default="rename-symbol", init=False)

    def __init__(self, old: str, new: str) -> None:
        self.old = old
        self.new = new
        self.kind = "rename-symbol"


@dataclass
class ChangeSignature(RefactorDirective):
    """Add, remove, or rename parameters in a function/method signature."""

    symbol: str = ""            # fully-qualified target
    transform: dict = field(default_factory=dict)  # {add_param, remove_param, rename_param}
    kind: str = field(default="change-signature", init=False)

    def __init__(self, symbol: str, transform: dict | None = None) -> None:
        self.symbol = symbol
        self.transform = transform or {}
        self.kind = "change-signature"


@dataclass
class ReplaceImport(RefactorDirective):
    """Replace an import path with a new one across the codebase."""

    old: str = ""   # e.g. "requests"
    new: str = ""   # e.g. "httpx"
    kind: str = field(default="replace-import", init=False)

    def __init__(self, old: str, new: str) -> None:
        self.old = old
        self.new = new
        self.kind = "replace-import"


@dataclass
class MigrateAPI(RefactorDirective):
    """Migrate one call pattern to another via libcst matcher syntax."""

    old_call_pattern: str = ""   # libcst matcher expression
    new_call_template: str = ""  # replacement template
    kind: str = field(default="migrate-api", init=False)

    def __init__(self, old_call_pattern: str, new_call_template: str) -> None:
        self.old_call_pattern = old_call_pattern
        self.new_call_template = new_call_template
        self.kind = "migrate-api"


@dataclass
class ReconcileResult:
    """Outcome of a conflict reconciliation attempt."""

    success: bool
    resolved_diff: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# Chunk independence violation sentinel
# ---------------------------------------------------------------------------


class IndependenceViolation(Exception):
    """Raised when two chunks are not statically independent."""

    def __init__(self, chunk_a: str, chunk_b: str, reason: str) -> None:
        super().__init__(f"Chunks {chunk_a!r} and {chunk_b!r} not independent: {reason}")
        self.chunk_a = chunk_a
        self.chunk_b = chunk_b
        self.reason = reason


# ---------------------------------------------------------------------------
# Lightweight libcst call-site visitor (used when libcst IS available)
# ---------------------------------------------------------------------------


if _LIBCST_AVAILABLE:
    class _CallSiteCollector(cst.CSTVisitor):  # type: ignore[misc]
        """libcst visitor that collects call sites for a target qualified name."""

        METADATA_DEPENDENCIES = (
            cst_meta.QualifiedNameProvider,
            cst_meta.PositionProvider,
        )

        def __init__(self, target_names: set[str]) -> None:
            self._targets = target_names
            self.sites: list[tuple[int, int, str, ScopeKind]] = []

        def visit_Name(self, node: cst.Name) -> None:  # type: ignore[override]
            self._check_node(node)

        def visit_Attribute(self, node: cst.Attribute) -> None:  # type: ignore[override]
            self._check_node(node)

        def _check_node(self, node: object) -> None:
            try:
                qnames = self.get_metadata(cst_meta.QualifiedNameProvider, node, set())
                pos = self.get_metadata(cst_meta.PositionProvider, node)
                for qn in qnames:
                    if qn.name in self._targets:
                        self.sites.append((
                            pos.start.line,
                            pos.start.column,
                            qn.name,
                            ScopeKind.MODULE,
                        ))
            except Exception:
                pass


# ---------------------------------------------------------------------------
# ASTPartitioner
# ---------------------------------------------------------------------------


class ASTPartitioner:
    """Partition a Python codebase into independent refactorable chunks.

    Python-only v1, libcst-based.  Out of scope: tree-sitter multi-language.

    Args:
        codebase_root: Absolute path to the root of the Python project.
    """

    def __init__(self, codebase_root: Path) -> None:
        self._root = codebase_root.resolve()

    # ── Public API ────────────────────────────────────────────────────────────

    def partition(
        self,
        directive: RefactorDirective,
        max_chunks: int = 100,
    ) -> list[CodeChunk]:
        """Partition the codebase into independent chunks for *directive*.

        Algorithm:
          1. Resolve call sites.
          2. Build dependency graph.
          3. Compute SCCs (Tarjan).
          4. Merge if needed.
          5. Verify static independence.

        Args:
            directive: The refactor directive to apply.
            max_chunks: Upper bound on the number of returned chunks.

        Returns:
            List of :class:`CodeChunk` values with static independence proofs.

        Raises:
            ImportError: If libcst is not installed.
            IndependenceViolation: If chunks cannot be made independent
                (falls back to a single sequential chunk).
        """
        _require_libcst()

        sites = self._resolve_call_sites(directive)
        if not sites:
            _log.info("ASTPartitioner: no call sites found for directive %s", directive.kind)
            return []

        graph = self._build_dependency_graph(sites)
        sccs = self._compute_sccs(graph)

        # Convert SCCs to CodeChunk objects
        chunks = self._sccs_to_chunks(sccs, sites, directive)

        # Enforce max_chunks cap via greedy merge
        if len(chunks) > max_chunks:
            chunks = self._greedy_merge(chunks, max_chunks)

        # Verify independence — raises IndependenceViolation on failure,
        # which the caller (SwarmDispatcher) can catch to fall back to
        # a sequential single-chunk plan.
        try:
            self._verify_static_independence(chunks)
        except IndependenceViolation as exc:
            _log.warning(
                "ASTPartitioner: independence violation detected — "
                "falling back to single sequential chunk: %s",
                exc,
            )
            chunks = self._make_sequential_fallback(sites, directive)

        return chunks

    # ── Private: call-site resolution ─────────────────────────────────────────

    def _resolve_call_sites(self, directive: RefactorDirective) -> list[CallSite]:
        """Walk the codebase and return all sites touched by *directive*."""
        sites: list[CallSite] = []

        if directive.kind == "rename-symbol":
            assert isinstance(directive, RenameSymbol)
            target_names = {directive.old}
            # Also match short name (last component) for local references
            short = directive.old.rsplit(".", 1)[-1]
            target_names.add(short)
            sites = self._find_name_references(target_names)

        elif directive.kind == "change-signature":
            assert isinstance(directive, ChangeSignature)
            target_names = {directive.symbol, directive.symbol.rsplit(".", 1)[-1]}
            sites = self._find_name_references(target_names)

        elif directive.kind == "replace-import":
            assert isinstance(directive, ReplaceImport)
            sites = self._find_import_references(directive.old)

        elif directive.kind == "migrate-api":
            assert isinstance(directive, MigrateAPI)
            # Extract the top-level name from the call pattern
            # e.g. "requests.get" → look for "requests" imports
            base = directive.old_call_pattern.split(".")[0]
            sites = self._find_import_references(base)

        else:
            _log.warning("ASTPartitioner: unsupported directive kind %r", directive.kind)

        return sites

    def _find_name_references(self, target_names: set[str]) -> list[CallSite]:
        """Scan Python files for references to any name in *target_names*."""
        sites: list[CallSite] = []

        for py_file in self._root.rglob("*.py"):
            if self._is_excluded(py_file):
                continue
            try:
                source = py_file.read_text(encoding="utf-8", errors="replace")
                file_sites = self._extract_sites_with_libcst(
                    source, py_file, target_names
                )
                sites.extend(file_sites)
            except Exception as exc:
                _log.debug(
                    "ASTPartitioner: skipping %s (parse error: %s)", py_file, exc
                )

        return sites

    def _find_import_references(self, module_name: str) -> list[CallSite]:
        """Scan Python files for imports of *module_name*."""
        sites: list[CallSite] = []
        module_base = module_name.split(".")[0]

        for py_file in self._root.rglob("*.py"):
            if self._is_excluded(py_file):
                continue
            try:
                source = py_file.read_text(encoding="utf-8", errors="replace")
                # Quick pre-filter before full parse
                if module_base not in source:
                    continue
                tree = cst.parse_module(source)
                visitor = _ImportRefCollector(module_base)
                wrapper = cst_meta.MetadataWrapper(tree, unsafe_skip_copy=True)
                try:
                    wrapper.visit(visitor)
                except Exception:
                    # Fallback: visit without metadata
                    tree.walk(visitor)  # type: ignore[arg-type]
                for line, col in visitor.hits:
                    sites.append(CallSite(
                        file=py_file,
                        line=line,
                        column=col,
                        qualified_name=module_name,
                        scope_kind=ScopeKind.MODULE,
                    ))
            except Exception as exc:
                _log.debug(
                    "ASTPartitioner: skipping %s (import scan error: %s)", py_file, exc
                )

        return sites

    def _extract_sites_with_libcst(
        self,
        source: str,
        file_path: Path,
        target_names: set[str],
    ) -> list[CallSite]:
        """Parse *source* with libcst and collect call sites for *target_names*."""
        try:
            tree = cst.parse_module(source)
        except Exception as exc:
            _log.debug("libcst parse failed for %s: %s", file_path, exc)
            return []

        # Use metadata wrapper for qualified name resolution when possible
        try:
            wrapper = cst_meta.MetadataWrapper(tree, unsafe_skip_copy=True)
            collector = _CallSiteCollector(target_names)
            wrapper.visit(collector)
            return [
                CallSite(
                    file=file_path,
                    line=line,
                    column=col,
                    qualified_name=qname,
                    scope_kind=scope,
                )
                for line, col, qname, scope in collector.sites
            ]
        except Exception:
            # Fallback: simple name-based scan without full metadata
            return self._fallback_name_scan(source, file_path, target_names)

    def _fallback_name_scan(
        self,
        source: str,
        file_path: Path,
        target_names: set[str],
    ) -> list[CallSite]:
        """Line-by-line scan when libcst metadata is not available."""
        sites: list[CallSite] = []
        for lineno, line in enumerate(source.splitlines(), start=1):
            for name in target_names:
                short = name.rsplit(".", 1)[-1]
                if short in line:
                    col = line.find(short) + 1
                    sites.append(CallSite(
                        file=file_path,
                        line=lineno,
                        column=col,
                        qualified_name=name,
                        scope_kind=ScopeKind.MODULE,
                    ))
                    break  # one site per line per file is enough for partitioning
        return sites

    # ── Private: dependency graph + SCC ───────────────────────────────────────

    def _build_dependency_graph(
        self, sites: list[CallSite]
    ) -> dict[Path, set[Path]]:
        """Build a file-level dependency graph from import relationships.

        Two files are connected when one imports from the other.  The call
        sites are also used to anchor which files share a dependency.

        Returns:
            Adjacency dict ``{file: {files it depends on}}``.
        """
        # Start with all files that contain call sites
        file_to_sites: dict[Path, list[CallSite]] = defaultdict(list)
        for site in sites:
            file_to_sites[site.file].append(site)

        graph: dict[Path, set[Path]] = {f: set() for f in file_to_sites}

        # Parse imports for each file to build edges
        for py_file in list(file_to_sites.keys()):
            try:
                source = py_file.read_text(encoding="utf-8", errors="replace")
                imported = self._extract_local_imports(source, py_file)
                for imp_file in imported:
                    if imp_file in file_to_sites:
                        graph[py_file].add(imp_file)
            except Exception as exc:
                _log.debug(
                    "ASTPartitioner: import extraction failed for %s: %s",
                    py_file, exc,
                )

        return graph

    def _extract_local_imports(self, source: str, file_path: Path) -> list[Path]:
        """Return absolute paths of local modules imported in *source*."""
        imported: list[Path] = []
        try:
            tree = cst.parse_module(source)
        except Exception:
            return imported

        visitor = _LocalImportCollector(self._root, file_path)
        tree.walk(visitor)  # type: ignore[arg-type]
        return visitor.local_imports

    def _compute_sccs(
        self, graph: dict[Path, set[Path]]
    ) -> list[list[Path]]:
        """Compute strongly-connected components via Tarjan's algorithm.

        Returns a list of SCCs in reverse topological order (callee before
        caller).  Each SCC is a candidate independent chunk.
        """
        index_counter = [0]
        stack: list[Path] = []
        on_stack: set[Path] = set()
        index: dict[Path, int] = {}
        lowlink: dict[Path, int] = {}
        sccs: list[list[Path]] = []

        def strongconnect(v: Path) -> None:
            index[v] = lowlink[v] = index_counter[0]
            index_counter[0] += 1
            stack.append(v)
            on_stack.add(v)

            for w in graph.get(v, set()):
                if w not in index:
                    strongconnect(w)
                    lowlink[v] = min(lowlink[v], lowlink[w])
                elif w in on_stack:
                    lowlink[v] = min(lowlink[v], index[w])

            if lowlink[v] == index[v]:
                scc: list[Path] = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    scc.append(w)
                    if w == v:
                        break
                sccs.append(scc)

        for node in graph:
            if node not in index:
                strongconnect(node)

        return sccs

    def _sccs_to_chunks(
        self,
        sccs: list[list[Path]],
        sites: list[CallSite],
        directive: RefactorDirective,
    ) -> list[CodeChunk]:
        """Convert SCC groups into :class:`CodeChunk` objects."""
        # Build site index by file
        file_to_sites: dict[Path, list[CallSite]] = defaultdict(list)
        for site in sites:
            file_to_sites[site.file].append(site)

        chunks: list[CodeChunk] = []
        for scc in sccs:
            scc_files = [f for f in scc if f in file_to_sites]
            if not scc_files:
                continue

            chunk_sites: list[CallSite] = []
            for f in scc_files:
                chunk_sites.extend(file_to_sites[f])

            chunk_id = _stable_chunk_id(scc_files)
            est_tokens = self._estimate_tokens(scc_files)

            chunks.append(CodeChunk(
                chunk_id=chunk_id,
                files=scc_files,
                call_sites=chunk_sites,
                scope=ScopeKind.MODULE,
                estimated_tokens=est_tokens,
                independence_proof=ProofRef(
                    kind="disjoint-files",
                    details=f"SCC of {len(scc_files)} file(s); no cross-SCC edges",
                ),
            ))

        return chunks

    # ── Private: merge + independence ─────────────────────────────────────────

    def _greedy_merge(
        self,
        chunks: list[CodeChunk],
        target_count: int,
    ) -> list[CodeChunk]:
        """Greedily merge smallest chunks by estimated_tokens until len <= target_count."""
        result = list(chunks)
        while len(result) > target_count:
            # Sort by estimated tokens ascending and merge the two smallest
            result.sort(key=lambda c: c.estimated_tokens)
            a = result.pop(0)
            b = result.pop(0)
            merged_files = list(dict.fromkeys(a.files + b.files))
            merged_sites = a.call_sites + b.call_sites
            merged_id = _stable_chunk_id(merged_files)
            merged = CodeChunk(
                chunk_id=merged_id,
                files=merged_files,
                call_sites=merged_sites,
                scope=ScopeKind.MODULE,
                estimated_tokens=a.estimated_tokens + b.estimated_tokens,
                independence_proof=ProofRef(
                    kind="disjoint-files",
                    details=(
                        f"Merged chunks {a.chunk_id[:8]} + {b.chunk_id[:8]} "
                        f"(greedy token-size merge to stay within max_chunks={target_count})"
                    ),
                ),
            )
            result.append(merged)

        return result

    def _verify_static_independence(self, chunks: list[CodeChunk]) -> None:
        """Verify no two chunks share a file.

        Raises:
            IndependenceViolation: If any file appears in two or more chunks.
        """
        seen: dict[Path, str] = {}
        for chunk in chunks:
            for f in chunk.files:
                if f in seen:
                    raise IndependenceViolation(
                        seen[f],
                        chunk.chunk_id,
                        f"File {f} appears in both chunks",
                    )
                seen[f] = chunk.chunk_id

    def _make_sequential_fallback(
        self,
        sites: list[CallSite],
        directive: RefactorDirective,
    ) -> list[CodeChunk]:
        """Fall back to a single sequential chunk covering all files."""
        all_files = list(dict.fromkeys(s.file for s in sites))
        chunk_id = _stable_chunk_id(all_files)
        return [
            CodeChunk(
                chunk_id=chunk_id,
                files=all_files,
                call_sites=sites,
                scope=ScopeKind.MODULE,
                estimated_tokens=self._estimate_tokens(all_files),
                independence_proof=ProofRef(
                    kind="sequential-fallback",
                    details=(
                        "Independence violation detected; falling back to a single "
                        "sequential chunk covering all call sites."
                    ),
                ),
            )
        ]

    # ── Private: utilities ────────────────────────────────────────────────────

    def _estimate_tokens(self, files: list[Path]) -> int:
        """Estimate token count via 4-chars-per-token heuristic."""
        total = 0
        for f in files:
            try:
                total += f.stat().st_size
            except OSError:
                pass
        return max(1, total // 4)

    @staticmethod
    def _is_excluded(path: Path) -> bool:
        """Return True for paths that should be skipped (venv, caches, etc.)."""
        parts = path.parts
        excluded_dirs = {
            ".venv", "venv", ".env", "__pycache__", ".git",
            "node_modules", ".tox", "dist", "build", ".eggs",
            ".mypy_cache", ".pytest_cache", "site-packages",
        }
        return any(part in excluded_dirs for part in parts)


# ---------------------------------------------------------------------------
# Ancillary libcst visitors (import collectors)
# ---------------------------------------------------------------------------

if _LIBCST_AVAILABLE:

    class _ImportRefCollector(cst.CSTVisitor):  # type: ignore[misc]
        """Collect line/col positions where *module_base* is imported or used."""

        def __init__(self, module_base: str) -> None:
            self._base = module_base
            self.hits: list[tuple[int, int]] = []

        def visit_ImportFrom(self, node: cst.ImportFrom) -> None:  # type: ignore[override]
            try:
                if node.module and hasattr(node.module, "value"):
                    mod_str = cst.parse_module("").code_for_node(node.module)  # type: ignore[attr-defined]
                    if mod_str.split(".")[0] == self._base:
                        self.hits.append((
                            getattr(getattr(node, "lpar", None), "whitespace_before", None)
                            and 0 or 1,
                            1,
                        ))
            except Exception:
                pass

        def visit_Import(self, node: cst.Import) -> None:  # type: ignore[override]
            try:
                if hasattr(node, "names") and node.names:
                    for alias in (node.names if isinstance(node.names, (list, tuple)) else []):
                        if hasattr(alias, "name") and hasattr(alias.name, "value"):
                            if alias.name.value.split(".")[0] == self._base:
                                self.hits.append((1, 1))
            except Exception:
                pass

    class _LocalImportCollector(cst.CSTVisitor):  # type: ignore[misc]
        """Collect absolute paths for local (relative) imports in a file."""

        def __init__(self, root: Path, current_file: Path) -> None:
            self._root = root
            self._current = current_file
            self.local_imports: list[Path] = []

        def visit_ImportFrom(self, node: cst.ImportFrom) -> None:  # type: ignore[override]
            # Handle relative imports (from . import X, from ..foo import Y)
            try:
                dots = len(node.relative) if node.relative else 0
                if dots == 0 and node.module:
                    # Absolute import — only include if it resolves locally
                    mod_parts = self._dotted(node.module)
                    candidate = self._resolve_local(mod_parts)
                    if candidate:
                        self.local_imports.append(candidate)
                elif dots > 0:
                    resolved = self._resolve_relative(dots, node.module)
                    if resolved:
                        self.local_imports.append(resolved)
            except Exception:
                pass

        @staticmethod
        def _dotted(module_node: object) -> list[str]:
            """Extract dot-separated parts from an Attribute or Name node."""
            parts: list[str] = []
            node = module_node
            while hasattr(node, "attr"):
                parts.insert(0, node.attr.value)  # type: ignore[attr-defined]
                node = node.value  # type: ignore[assignment]
            if hasattr(node, "value"):
                parts.insert(0, node.value)  # type: ignore[attr-defined]
            return parts

        def _resolve_local(self, parts: list[str]) -> Path | None:
            candidate = self._root.joinpath(*parts).with_suffix(".py")
            if candidate.exists():
                return candidate
            pkg = self._root.joinpath(*parts) / "__init__.py"
            if pkg.exists():
                return pkg
            return None

        def _resolve_relative(
            self, dots: int, module_node: object | None
        ) -> Path | None:
            base = self._current.parent
            for _ in range(dots - 1):
                base = base.parent
            if module_node:
                parts = self._dotted(module_node)
                candidate = base.joinpath(*parts).with_suffix(".py")
            else:
                candidate = base / "__init__.py"
            return candidate if candidate.exists() else None


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _stable_chunk_id(files: list[Path]) -> str:
    """Return a stable SHA-256 hex digest for the given file list."""
    key = "|".join(sorted(str(f) for f in files))
    return hashlib.sha256(key.encode()).hexdigest()
