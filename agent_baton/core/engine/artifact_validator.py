"""Derive validation commands from agent-created runnable artifacts.

The phase-gate command set in the plan only validates the checks the
plan author thought of up front.  When an agent introduces a new
runnable artifact during a phase — a CI workflow, an npm script, an
end-to-end test config — the gate has no visibility into it and the
artifact ships unverified, "trusted-but-untested".

:class:`ArtifactValidator` scans the files an agent changed and
produces extra shell commands that exercise those new artifacts.
Callers append the derived commands to the gate command (chained with
``&&``) so a phase only passes when both the planned gate **and** the
new-artifact checks succeed.

Recognised artifact types (best-effort; safe defaults):

- ``.github/workflows/*.yml`` / ``.yaml`` — extract every ``run:`` step
  in the workflow.  Each becomes a derived command.  Workflow-only
  shell features (``${{ ... }}`` expressions, multi-line ``run`` blocks
  whose body depends on the runner image) are skipped: only single-line
  commands that are safe to execute on a developer machine survive.
- ``package.json`` — when scripts named ``test``, ``test:*``, ``lint``,
  ``typecheck``, or ``audit`` are added or modified, surface
  ``npm run <name>`` for each.
- Playwright config (``playwright.config.{js,ts,mjs,cjs}``) — surface
  ``npm run test:e2e`` when ``package.json`` exposes that script.
- ``Makefile`` (or ``**/Makefile``) — when targets named ``test``,
  ``lint``, ``typecheck``, ``check``, ``audit``, or ``ci`` are declared,
  surface ``make <target>`` for each.  Uses a tolerant line-based
  parser; caps at :data:`_MAX_COMMANDS_PER_FILE`.
- ``.pre-commit-config.yaml`` — surfaces ``pre-commit run --all-files``
  when the file parses as valid YAML.

Unrecognised files are ignored.  The class is stateless aside from the
project root used to resolve relative paths and reads files only from
that root.  When ``project_root`` is ``None``, file inspection is
skipped and only path-based derivations (Playwright → ``npm run
test:e2e``) fire.

Disabling: set ``BATON_ARTIFACT_VALIDATION=0`` to suppress derivation
without touching the plan.  Useful for debugging or for plans that
already exhaustively enumerate their gates.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from agent_baton.core.engine._command_safety import (
    MAX_GATE_COMMAND_LENGTH,
    is_destructive,
    is_safe_gate_command,
)

logger = logging.getLogger(__name__)

# Process-level flag: emit the BATON_ARTIFACT_VALIDATION=0 warning at most
# once per process so high-frequency callers do not flood the log.
_DISABLED_WARNING_EMITTED: bool = False


# ---------------------------------------------------------------------------
# Path matchers — workflows and configs use forward slashes regardless of OS.
# ---------------------------------------------------------------------------

_CI_WORKFLOW_RE = re.compile(r"(?:^|/)\.github/workflows/[^/]+\.ya?ml$")
_PLAYWRIGHT_RE = re.compile(r"(?:^|/)playwright\.config\.(?:js|ts|mjs|cjs)$")
_PACKAGE_JSON_RE = re.compile(r"(?:^|/)package\.json$")
_MAKEFILE_RE = re.compile(r"(?:^|/)Makefile$")
_PRE_COMMIT_RE = re.compile(r"(?:^|/)\.pre-commit-config\.ya?ml$")

# Script keys in package.json that are gate-worthy: cheap to run, fail
# fast when broken, and exactly the kind of thing agents land on first.
_GATE_SCRIPT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^test$"),
    re.compile(r"^test:.+"),
    re.compile(r"^lint$"),
    re.compile(r"^typecheck$"),
    re.compile(r"^audit$"),
)

# Makefile target names that are gate-worthy.  Matching is exact and
# case-sensitive — ``Test`` is not the same as ``test`` in make.
_GATE_MAKE_TARGETS: frozenset[str] = frozenset(
    {"test", "lint", "typecheck", "check", "audit", "ci"}
)

# A run-line is unsafe to lift out of a CI runner when it references
# GitHub Actions expression syntax or runner-only env (``${{ ... }}``,
# ``$GITHUB_*``).  These commands need the workflow harness to
# evaluate; running them locally would either fail with an unbound
# variable or — worse — silently no-op.  Skip them.
_UNSAFE_RUN_MARKERS: tuple[str, ...] = (
    "${{",
    "$GITHUB_",
    "$RUNNER_",
)

# Maximum number of derived commands to emit per file.  A workflow
# with 50 ``run:`` steps would otherwise blow the gate command up to
# something the shell cannot reasonably execute.  The cap is per file
# so a single noisy artifact cannot drown out other artifacts.
_MAX_COMMANDS_PER_FILE = 8


@dataclass(frozen=True)
class DerivedCommand:
    """A single shell command derived from an agent-created artifact.

    Attributes:
        command: The shell command to run.
        source_file: Repository-relative path of the artifact this
            command was derived from.
        rationale: One-line explanation surfaced in logs and the gate
            action message so reviewers can see *why* the command was
            added.
    """

    command: str
    source_file: str
    rationale: str


class ArtifactValidator:
    """Derives gate-extension commands from agent-created artifacts.

    Stateless aside from the optional project root used to read file
    contents.  Safe to instantiate per gate invocation; cheap to call.
    """

    def __init__(self, project_root: Path | str | None = None) -> None:
        self._root: Path | None = (
            Path(project_root).resolve() if project_root else None
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def derive_commands(
        self,
        files_changed: list[str] | tuple[str, ...] | None,
    ) -> list[DerivedCommand]:
        """Return validation commands for every recognised artifact.

        Args:
            files_changed: Repository-relative paths the agent created
                or modified.  Empty / ``None`` returns an empty list.

        Returns:
            Ordered list of :class:`DerivedCommand` — duplicates across
            files are de-duplicated by command string while preserving
            the first occurrence's source attribution.
        """
        if not files_changed:
            return []
        if os.environ.get("BATON_ARTIFACT_VALIDATION", "1") == "0":
            global _DISABLED_WARNING_EMITTED
            if not _DISABLED_WARNING_EMITTED:
                _DISABLED_WARNING_EMITTED = True
                logger.warning(
                    "ArtifactValidator disabled via BATON_ARTIFACT_VALIDATION=0;"
                    " phase gate will not validate agent-created artifacts"
                )
            return []

        derived: list[DerivedCommand] = []
        seen: set[str] = set()

        normalized = [self._normalize(p) for p in files_changed if p]

        for path in normalized:
            for cmd in self._derive_for_path(path, normalized):
                if cmd.command in seen:
                    continue
                seen.add(cmd.command)
                derived.append(cmd)

        return derived

    # ------------------------------------------------------------------
    # Per-path dispatch
    # ------------------------------------------------------------------

    def _derive_for_path(
        self,
        path: str,
        all_paths: list[str],
    ) -> list[DerivedCommand]:
        if _CI_WORKFLOW_RE.search(path):
            return self._derive_from_workflow(path)
        if _PACKAGE_JSON_RE.search(path):
            return self._derive_from_package_json(path)
        if _PLAYWRIGHT_RE.search(path):
            return self._derive_from_playwright(path, all_paths)
        if _MAKEFILE_RE.search(path):
            return self._derive_from_makefile(path)
        if _PRE_COMMIT_RE.search(path):
            return self._derive_from_pre_commit(path)
        return []

    # ------------------------------------------------------------------
    # GitHub Actions workflows
    # ------------------------------------------------------------------

    def _derive_from_workflow(self, path: str) -> list[DerivedCommand]:
        text = self._read_text(path)
        if text is None:
            return []
        run_lines = _extract_workflow_run_lines(text)
        out: list[DerivedCommand] = []
        for cmd in run_lines:
            # Skip runner-only expressions that cannot execute locally.
            if any(m in cmd for m in _UNSAFE_RUN_MARKERS):
                continue
            # Defence in depth: reject commands that are too long, contain
            # shell metacharacters, or match known destructive patterns.
            if len(cmd) > MAX_GATE_COMMAND_LENGTH:
                logger.warning(
                    "ArtifactValidator: rejected workflow run (too long, %d chars): %.80s",
                    len(cmd),
                    cmd,
                )
                continue
            if not is_safe_gate_command(cmd):
                logger.warning(
                    "ArtifactValidator: rejected workflow run (shell metacharacter): %.80s",
                    cmd,
                )
                continue
            if is_destructive(cmd):
                logger.warning(
                    "ArtifactValidator: rejected workflow run (destructive pattern): %.80s",
                    cmd,
                )
                continue
            out.append(
                DerivedCommand(
                    command=cmd,
                    source_file=path,
                    rationale=f"run-step from workflow {path}",
                )
            )
            # Cap AFTER filtering so unsafe lines don't consume the budget.
            if len(out) >= _MAX_COMMANDS_PER_FILE:
                break
        return out

    # ------------------------------------------------------------------
    # package.json
    # ------------------------------------------------------------------

    def _derive_from_package_json(self, path: str) -> list[DerivedCommand]:
        text = self._read_text(path)
        if text is None:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.debug("ArtifactValidator: %s parse error: %s", path, exc)
            return []
        scripts = data.get("scripts") if isinstance(data, dict) else None
        if not isinstance(scripts, dict):
            return []

        out: list[DerivedCommand] = []
        for name in scripts.keys():
            if not isinstance(name, str):
                continue
            if not _is_gate_script(name):
                continue
            out.append(
                DerivedCommand(
                    command=f"npm run {name}",
                    source_file=path,
                    rationale=f"script '{name}' declared in {path}",
                )
            )
            if len(out) >= _MAX_COMMANDS_PER_FILE:
                break
        return out

    # ------------------------------------------------------------------
    # Playwright
    # ------------------------------------------------------------------

    def _derive_from_playwright(
        self,
        path: str,
        all_paths: list[str],
    ) -> list[DerivedCommand]:
        # Only emit the e2e command when package.json declares it —
        # otherwise we'd emit an "npm run test:e2e" that fails not
        # because the config is broken but because the script name
        # doesn't exist.
        pkg = self._find_package_json(path, all_paths)
        if pkg is None:
            return []
        text = self._read_text(pkg)
        if text is None:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        scripts = data.get("scripts") if isinstance(data, dict) else None
        if not isinstance(scripts, dict) or "test:e2e" not in scripts:
            return []
        return [
            DerivedCommand(
                command="npm run test:e2e",
                source_file=path,
                rationale=f"playwright config edited in {path}",
            )
        ]

    # ------------------------------------------------------------------
    # Makefile
    # ------------------------------------------------------------------

    def _derive_from_makefile(self, path: str) -> list[DerivedCommand]:
        text = self._read_text(path)
        if text is None:
            return []
        out: list[DerivedCommand] = []
        for line in text.splitlines():
            # A target line looks like ``<name>:`` optionally followed by
            # prerequisites.  Skip recipe lines (start with tab), blank
            # lines, comments, and .PHONY declarations.
            if not line or line[0] in ("\t", "#"):
                continue
            m = re.match(r"^([A-Za-z0-9_][A-Za-z0-9_./-]*):", line)
            if m is None:
                continue
            target = m.group(1)
            if target not in _GATE_MAKE_TARGETS:
                continue
            out.append(
                DerivedCommand(
                    command=f"make {target}",
                    source_file=path,
                    rationale=f"Makefile target '{target}' in {path}",
                )
            )
            if len(out) >= _MAX_COMMANDS_PER_FILE:
                break
        return out

    # ------------------------------------------------------------------
    # .pre-commit-config.yaml
    # ------------------------------------------------------------------

    def _derive_from_pre_commit(self, path: str) -> list[DerivedCommand]:
        text = self._read_text(path)
        if text is None:
            return []
        # Validate that the file is parseable YAML before emitting a
        # command — a corrupt config would cause pre-commit to error out
        # in a way unrelated to the agent's changes.
        try:
            import yaml  # noqa: PLC0415
            yaml.safe_load(text)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "ArtifactValidator: %s parse error: %s", path, exc
            )
            return []
        return [
            DerivedCommand(
                command="pre-commit run --all-files",
                source_file=path,
                rationale=f"pre-commit config modified in {path}",
            )
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(path: str) -> str:
        # Always reason in forward-slash form so Windows paths match
        # the regex literals.  Strip a leading ``./`` (if present) so a
        # path like ``./package.json`` matches ``package.json`` patterns.
        norm = path.replace("\\", "/")
        while norm.startswith("./"):
            norm = norm[2:]
        if norm.startswith("/"):
            norm = norm.lstrip("/")
        return norm

    def _read_text(self, rel_path: str) -> str | None:
        if self._root is None:
            return None
        target = (self._root / rel_path).resolve()
        # Defence in depth — a crafted path must not escape the root.
        try:
            target.relative_to(self._root)
        except ValueError:
            logger.debug(
                "ArtifactValidator: refused out-of-root path %s", rel_path
            )
            return None
        if not target.is_file():
            return None
        try:
            return target.read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("ArtifactValidator: read failed for %s: %s", target, exc)
            return None

    @staticmethod
    def _find_package_json(
        config_path: str,
        all_paths: list[str],
    ) -> str | None:
        # Prefer a package.json that lives in the same project the
        # config does — i.e. the deepest one whose directory is a
        # prefix of the config path.
        config_dir = config_path.rsplit("/", 1)[0] if "/" in config_path else ""
        candidates = [p for p in all_paths if _PACKAGE_JSON_RE.search(p)]
        if not candidates:
            # Fall back to the conventional sibling: same dir as config,
            # or repo root.
            return f"{config_dir}/package.json" if config_dir else "package.json"
        # Pick the candidate sharing the longest dir prefix with the config.
        best = max(
            candidates,
            key=lambda p: len(_common_dir_prefix(p, config_path)),
        )
        return best


# ---------------------------------------------------------------------------
# Free helpers
# ---------------------------------------------------------------------------


def _is_gate_script(name: str) -> bool:
    return any(p.match(name) for p in _GATE_SCRIPT_PATTERNS)


def _common_dir_prefix(a: str, b: str) -> str:
    a_dir = a.rsplit("/", 1)[0] if "/" in a else ""
    b_dir = b.rsplit("/", 1)[0] if "/" in b else ""
    out: list[str] = []
    for x, y in zip(a_dir.split("/"), b_dir.split("/")):
        if x != y:
            break
        out.append(x)
    return "/".join(out)


def _extract_workflow_run_lines(text: str) -> list[str]:
    """Pull every ``run:`` payload out of a GitHub Actions workflow.

    Tries PyYAML first (ships as a runtime dependency) and falls back
    to a regex sweep when parsing fails.  Multi-line ``run`` blocks are
    flattened to their first non-empty line — agents typically put the
    actual check there and use subsequent lines for shell prologue.
    """
    try:
        import yaml  # noqa: PLC0415
    except ImportError:  # pragma: no cover — pyyaml is a hard dep.
        return _regex_run_lines(text)

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return _regex_run_lines(text)

    if not isinstance(data, dict):
        return []

    out: list[str] = []
    jobs = data.get("jobs", {})
    if not isinstance(jobs, dict):
        return []
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps", [])
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            cmd = step.get("run")
            if not isinstance(cmd, str):
                continue
            first = _first_command_line(cmd)
            if first:
                out.append(first)
    return out


def _regex_run_lines(text: str) -> list[str]:
    out: list[str] = []
    # ``run: foo`` (single line) or ``run: |`` followed by an indented block.
    # The optional ``- `` prefix accommodates list-item style inside
    # ``steps:`` blocks (``      - run: foo``).
    inline = re.compile(
        r"^[ \t]*-?[ \t]*run:[ \t]*(?![|>])(.+?)[ \t]*$",
        re.MULTILINE,
    )
    for m in inline.finditer(text):
        first = _first_command_line(m.group(1).strip().strip("'\""))
        if first:
            out.append(first)
    block = re.compile(
        r"^([ \t]*)-?[ \t]*run:[ \t]*[|>][^\n]*\n((?:\1[ \t]+[^\n]*\n?)+)",
        re.MULTILINE,
    )
    for m in block.finditer(text):
        body = m.group(2)
        first = _first_command_line(body)
        if first:
            out.append(first)
    return out


def _first_command_line(block: str) -> str:
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped
    return ""
