"""Wave 6.1 Part C — Executable Beads: sandbox runner (bd-81b9).

Executes scripts in an isolated environment with resource limits, a
temporary working directory, and best-effort network blocking.

Strategy (in priority order):
1. **firejail** (Linux, when installed): full process isolation with private
   filesystem, no network, CPU/mem limits via rlimit flags.
2. **unshare --net** (Linux, no firejail): network namespace isolation +
   subprocess with ``resource.setrlimit`` and ``cwd=tmpdir``.
3. **Plain subprocess** (fallback / non-Linux): ``PATH=/usr/bin:/bin``,
   ``cwd=tmpdir``, ``HOME=tmpdir``, stripped env, ``resource.setrlimit``
   where available, hard timeout via ``subprocess.run(..., timeout=...)``.
   Network blocking is best-effort only (proxy vars cleared); a
   ``BEAD_WARNING`` is emitted in the result stderr when ``net=False`` is
   requested but true namespace isolation is unavailable.

Output caps:
- stdout: 8192 chars in-memory; overflow spilled to ``full_output_path``.
- stderr: 4096 chars in-memory; overflow appended to the same spill file.

The spill directory is ``{project_root}/.claude/team-context/exec-results/``.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

_log = logging.getLogger(__name__)

_STDOUT_CAP = 8192
_STDERR_CAP = 4096
_SAFE_PATH = "/usr/bin:/bin"

# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass
class SandboxConfig:
    """Runtime constraints for a single script execution.

    Attributes:
        timeout_s: Hard kill timeout in seconds.
        mem_mb: Memory limit in megabytes (enforced via ``RLIMIT_AS`` when
            possible).
        net: Whether network access is permitted.  When ``False``, the sandbox
            attempts to block outbound connections via namespace isolation or
            proxy-var clearing.
        cwd_tmpdir: Always ``True`` in v1 — the script always runs in a fresh
            temporary directory that is deleted after the run.
    """

    timeout_s: int = 30
    mem_mb: int = 256
    net: bool = False
    cwd_tmpdir: bool = True  # always True for v1


@dataclass
class ExecutionResult:
    """Outcome of a sandboxed script execution.

    Attributes:
        exit_code: Process exit code; ``124`` indicates a timeout kill.
        stdout: First ``8192`` chars of stdout.
        stderr: First ``4096`` chars of stderr, may include ``BEAD_WARNING``
            annotations injected by the sandbox.
        duration_ms: Wall-clock runtime in milliseconds.
        full_output_path: Path to the spill log when output exceeded the
            in-memory caps, or ``None``.
    """

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    full_output_path: str | None = None


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------


class Sandbox:
    """Execute a script body in an isolated environment.

    Args:
        config: Resource and isolation constraints.
        spill_dir: Directory for large-output spill logs.  Defaults to
            ``.claude/team-context/exec-results`` relative to the process
            working directory at construction time.
    """

    def __init__(
        self,
        config: SandboxConfig | None = None,
        spill_dir: Path | None = None,
    ) -> None:
        self._config = config or SandboxConfig()
        self._spill_dir: Path = (
            spill_dir
            if spill_dir is not None
            else Path(".claude/team-context/exec-results")
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, script: str, interpreter: str) -> ExecutionResult:
        """Execute *script* under *interpreter* and return the result.

        Args:
            script: The script body text.
            interpreter: One of ``'bash'``, ``'python'``, ``'ast-grep'``,
                ``'pytest'``.

        Returns:
            :class:`ExecutionResult` with exit code, captured output, and
            optional spill path.
        """
        if self._firejail_available():
            _log.debug("Sandbox: using firejail for %s script", interpreter)
            return self._run_firejail(script, interpreter)
        if self._unshare_available():
            _log.debug("Sandbox: using unshare --net for %s script", interpreter)
            return self._run_unshare(script, interpreter)
        _log.debug("Sandbox: using plain subprocess for %s script", interpreter)
        return self._run_subprocess(script, interpreter)

    # ------------------------------------------------------------------
    # Availability probes
    # ------------------------------------------------------------------

    def _firejail_available(self) -> bool:
        """Return ``True`` when ``firejail`` is on PATH and we are on Linux."""
        if sys.platform != "linux":
            return False
        return shutil.which("firejail") is not None

    def _unshare_available(self) -> bool:
        """Return ``True`` when ``unshare --net`` is usable (Linux only)."""
        if sys.platform != "linux":
            return False
        unshare = shutil.which("unshare")
        if not unshare:
            return False
        # Quick probe: does unshare support --net without needing root?
        try:
            probe = subprocess.run(
                ["unshare", "--net", "true"],
                capture_output=True,
                timeout=3,
            )
            return probe.returncode == 0
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Execution backends
    # ------------------------------------------------------------------

    def _run_firejail(self, script: str, interpreter: str) -> ExecutionResult:
        """Run via firejail with private filesystem and no network."""
        with tempfile.TemporaryDirectory(prefix="baton-exec-") as tmpdir:
            script_path = Path(tmpdir) / _script_filename(interpreter)
            script_path.write_text(script, encoding="utf-8")
            script_path.chmod(0o700)

            cmd = [
                "firejail",
                "--quiet",
                "--private",
                "--net=none",
                f"--rlimit-as={self._config.mem_mb}m",
                f"--rlimit-cpu={self._config.timeout_s}",
                f"--whitelist={tmpdir}",
            ]
            if self._config.net:
                # Remove --net=none when network is explicitly allowed.
                cmd = [c for c in cmd if c != "--net=none"]

            cmd.extend(_interpreter_argv(interpreter, str(script_path)))
            return self._exec_cmd(cmd, tmpdir, use_rlimit=False)

    def _run_unshare(self, script: str, interpreter: str) -> ExecutionResult:
        """Run in a new network namespace via ``unshare --net``."""
        with tempfile.TemporaryDirectory(prefix="baton-exec-") as tmpdir:
            script_path = Path(tmpdir) / _script_filename(interpreter)
            script_path.write_text(script, encoding="utf-8")
            script_path.chmod(0o700)

            inner_cmd = _interpreter_argv(interpreter, str(script_path))
            if not self._config.net:
                cmd = ["unshare", "--net"] + inner_cmd
            else:
                cmd = inner_cmd
            return self._exec_cmd(cmd, tmpdir, use_rlimit=True)

    def _run_subprocess(self, script: str, interpreter: str) -> ExecutionResult:
        """Plain subprocess fallback with best-effort isolation."""
        with tempfile.TemporaryDirectory(prefix="baton-exec-") as tmpdir:
            script_path = Path(tmpdir) / _script_filename(interpreter)
            script_path.write_text(script, encoding="utf-8")
            script_path.chmod(0o700)

            cmd = _interpreter_argv(interpreter, str(script_path))
            result = self._exec_cmd(cmd, tmpdir, use_rlimit=True)

            # Annotate stderr with a warning when net=False could not be
            # enforced at the namespace level.
            if not self._config.net:
                warning = (
                    "\nBEAD_WARNING: net=False requested but firejail/unshare "
                    "unavailable — outbound network NOT actually blocked.\n"
                )
                result = ExecutionResult(
                    exit_code=result.exit_code,
                    stdout=result.stdout,
                    stderr=(result.stderr + warning)[: _STDERR_CAP],
                    duration_ms=result.duration_ms,
                    full_output_path=result.full_output_path,
                )
            return result

    # ------------------------------------------------------------------
    # Core execution helper
    # ------------------------------------------------------------------

    def _exec_cmd(
        self,
        cmd: list[str],
        cwd: str,
        *,
        use_rlimit: bool,
    ) -> ExecutionResult:
        """Run *cmd* with timeout, resource limits, and output capture."""
        env = _build_env(cwd, net=self._config.net)

        def _preexec() -> None:
            if use_rlimit:
                _apply_rlimits(self._config.mem_mb, self._config.timeout_s)

        start = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                cwd=cwd,
                env=env,
                timeout=self._config.timeout_s + 2,  # hard kill margin
                preexec_fn=_preexec if use_rlimit else None,
            )
            exit_code = proc.returncode
            raw_stdout = proc.stdout.decode("utf-8", errors="replace")
            raw_stderr = proc.stderr.decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = 124
            raw_stdout = (exc.stdout or b"").decode("utf-8", errors="replace")
            raw_stderr = (exc.stderr or b"").decode("utf-8", errors="replace")
            raw_stderr += "\nBEAD_WARNING: execution timed out\n"

        elapsed_ms = int((time.monotonic() - start) * 1000)

        # Cap and optionally spill.
        stdout_capped = raw_stdout[:_STDOUT_CAP]
        stderr_capped = raw_stderr[:_STDERR_CAP]
        spill_path: str | None = None

        if len(raw_stdout) > _STDOUT_CAP or len(raw_stderr) > _STDERR_CAP:
            spill_path = self._spill_output(raw_stdout, raw_stderr)

        return ExecutionResult(
            exit_code=exit_code,
            stdout=stdout_capped,
            stderr=stderr_capped,
            duration_ms=elapsed_ms,
            full_output_path=spill_path,
        )

    # ------------------------------------------------------------------
    # Spill helper
    # ------------------------------------------------------------------

    def _spill_output(self, stdout: str, stderr: str) -> str | None:
        """Write full stdout+stderr to a spill log file.

        Returns the absolute path string, or ``None`` on failure.
        """
        try:
            spill_dir = self._spill_dir.resolve()
            spill_dir.mkdir(parents=True, exist_ok=True)
            fd, path = tempfile.mkstemp(
                prefix="exec-", suffix=".log", dir=str(spill_dir)
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("=== STDOUT ===\n")
                f.write(stdout)
                f.write("\n=== STDERR ===\n")
                f.write(stderr)
            return path
        except Exception as exc:
            _log.warning("Sandbox: failed to spill output: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _script_filename(interpreter: str) -> str:
    """Return an appropriate filename for the script body."""
    ext = {
        "bash": "script.sh",
        "python": "script.py",
        "ast-grep": "rule.yaml",
        "pytest": "test_exec.py",
    }
    return ext.get(interpreter, "script.txt")


def _interpreter_argv(interpreter: str, script_path: str) -> list[str]:
    """Build the argv list to invoke *script_path* under *interpreter*."""
    if interpreter == "bash":
        return ["bash", script_path]
    if interpreter == "python":
        return ["python3", script_path]
    if interpreter == "ast-grep":
        return ["ast-grep", "scan", "--rule", script_path]
    if interpreter == "pytest":
        return ["python3", "-m", "pytest", script_path, "-v", "--tb=short"]
    # Unknown interpreter — run as bash for safety (lint should catch this).
    _log.warning("Sandbox: unknown interpreter %r, falling back to bash", interpreter)
    return ["bash", script_path]


def _build_env(tmpdir: str, *, net: bool) -> dict[str, str]:
    """Build a minimal, stripped environment for the child process."""
    env: dict[str, str] = {
        "PATH": _SAFE_PATH,
        "HOME": tmpdir,
        "TMPDIR": tmpdir,
        "LANG": "en_US.UTF-8",
    }
    if not net:
        # Best-effort: clear proxy vars so naive HTTP clients fail.
        env["http_proxy"] = "http://127.0.0.1:0"
        env["https_proxy"] = "http://127.0.0.1:0"
        env["HTTP_PROXY"] = "http://127.0.0.1:0"
        env["HTTPS_PROXY"] = "http://127.0.0.1:0"
        env["no_proxy"] = "*"
        env["NO_PROXY"] = "*"
    return env


def _apply_rlimits(mem_mb: int, timeout_s: int) -> None:
    """Set RLIMIT_AS and RLIMIT_CPU in the child process.

    Called from ``preexec_fn`` — runs in the child after fork but before exec.
    Silently skips on platforms where ``resource`` is unavailable (Windows).
    """
    try:
        import resource  # not available on Windows

        mem_bytes = mem_mb * 1024 * 1024
        # RLIMIT_AS: virtual address space
        try:
            resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
        except (ValueError, resource.error):
            pass  # may fail if current limit is already lower

        # RLIMIT_CPU: CPU time in seconds
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (timeout_s, timeout_s + 5))
        except (ValueError, resource.error):
            pass
    except ImportError:
        pass
