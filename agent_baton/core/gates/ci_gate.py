"""CI provider gate runner — polls GitHub Actions / GitLab CI for a SHA.

Wave 4.1 of the strategic remediation roadmap.  Resolves bd-b050.

The :class:`CIGateRunner` waits for the CI run that corresponds to the
current branch's HEAD commit and returns a :class:`CIGateResult` describing
the conclusion.  Unlike :func:`agent_baton.core.engine.gates.run_github_actions_gate`
(which *dispatches* a workflow run), this runner observes existing runs —
the model that matches how human PRs flow through CI.

Design constraints (Wave 4.1):

- Velocity-first: CI gates are **opt-in** per plan.  Default plans do not
  add a CI gate.  Authors who want production-grade verification add a
  ``gate_type="ci"`` gate explicitly.
- Polling-only: no webhook listener, no event subscriptions.  ``gh run list``
  every 15 s with a hard timeout (default 600 s = 10 min).
- Best-effort: missing ``gh`` CLI returns a friendly ``passed=False``
  result rather than raising, so the CLI can record a clean gate failure
  instead of crashing the run.
- GitHub-only for v1.  GitLab raises :class:`NotImplementedError` with a
  pointer to ``--provider github``.

The runner shells out to ``gh`` rather than calling the GitHub REST API
directly so that auth (GH_TOKEN, gh login) is delegated to a CLI the user
already manages.  All ``subprocess`` calls use ``capture_output=True,
check=False, timeout=...`` so a hung ``gh`` invocation cannot wedge the
gate.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Defaults ────────────────────────────────────────────────────────────────
# Polling cadence and overall timeout.  Tuned for typical PR CI budgets
# (5-10 minutes) while staying short enough that an agent does not block
# the orchestrator for an unbounded period.
DEFAULT_POLL_INTERVAL_S: int = 15
DEFAULT_TIMEOUT_S: int = 600  # 10 minutes
DEFAULT_GH_INVOKE_TIMEOUT_S: int = 30
DEFAULT_PROVIDER: str = "github"

# Cap log excerpts to keep gate output payloads small in the execution
# state JSON / DB.  500 chars is enough for the failing pytest line +
# traceback head without ballooning the trace.
LOG_EXCERPT_MAX_CHARS: int = 500

# Conclusions the GitHub Actions API reports.  ``success`` is the only
# pass; everything else (failure, cancelled, timed_out, action_required,
# neutral, skipped, stale) is a fail for the purposes of the gate.
GITHUB_PASS_CONCLUSION: str = "success"


@dataclass
class CIGateResult:
    """Outcome of a CI provider gate poll.

    Returned by :meth:`CIGateRunner.wait_for_workflow`.  Mirrors the
    minimum information a human reviewer would want to see: did it pass,
    where can I read the run, and (on failure) what was the last bit of
    log output.

    Attributes:
        passed: ``True`` when the CI run completed with a pass conclusion
            (currently ``"success"`` for GitHub Actions).  ``False`` for
            any failure, timeout, or precondition error.
        run_id: Provider-assigned numeric run identifier.  Empty string
            when no run was found (timeout / gh missing).
        conclusion: Provider conclusion string.  Special sentinels used by
            this runner for non-CI failures: ``"gh_unavailable"`` (gh CLI
            missing), ``"timeout"`` (no run reached completion within
            ``timeout_s``), ``"not_implemented"`` (provider stub).
        url: Human-readable URL to the CI run page.  Empty when no run
            was found.
        duration_s: Wall-clock seconds spent waiting for the run.
            Includes initial polling for the run to appear.
        log_excerpt: Last :data:`LOG_EXCERPT_MAX_CHARS` characters of the
            failing logs (from ``gh run view --log-failed``).  Empty on
            success.  Capped server-side here so callers cannot
            accidentally explode trace payloads.
    """

    passed: bool
    run_id: str = ""
    conclusion: str = ""
    url: str = ""
    duration_s: float = 0.0
    log_excerpt: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict (for state/trace persistence)."""
        return {
            "passed": self.passed,
            "run_id": self.run_id,
            "conclusion": self.conclusion,
            "url": self.url,
            "duration_s": round(self.duration_s, 2),
            "log_excerpt": self.log_excerpt,
        }


@dataclass
class _CIGateConfig:
    """Parsed CI gate configuration (internal)."""

    provider: str = DEFAULT_PROVIDER
    workflow: str = ""
    timeout_s: int = DEFAULT_TIMEOUT_S
    branch: str = "auto"
    poll_interval_s: int = DEFAULT_POLL_INTERVAL_S


def parse_ci_gate_config(raw: str) -> _CIGateConfig:
    """Parse a CI gate command field into a :class:`_CIGateConfig`.

    The plan-level ``gate.command`` for a CI gate may be either:

    1. **JSON object** (full form)::

           {"provider": "github", "workflow": "ci.yml", "timeout_s": 600,
            "branch": "auto"}

    2. **Shorthand string** — just the workflow file name::

           "ci.yml"

       Defaults are applied for everything else.  This is the common case
       and keeps plan files readable.

    Args:
        raw: The raw command string from ``PlanGate.command``.

    Returns:
        A populated :class:`_CIGateConfig`.  When *raw* is empty or
        unparseable, a default config with ``workflow="ci.yml"`` is
        returned so that callers still get a sensible attempt rather
        than a crash.
    """
    raw = (raw or "").strip()
    if not raw:
        return _CIGateConfig(workflow="ci.yml")

    # Try JSON first (must start with '{').  If it doesn't look like JSON,
    # treat the whole string as a workflow name.
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("CI gate: malformed JSON config, treating as workflow name: %s", raw)
            return _CIGateConfig(workflow=raw)
        return _CIGateConfig(
            provider=str(data.get("provider", DEFAULT_PROVIDER)).lower(),
            workflow=str(data.get("workflow", "ci.yml")),
            timeout_s=int(data.get("timeout_s", DEFAULT_TIMEOUT_S)),
            branch=str(data.get("branch", "auto")),
            poll_interval_s=int(data.get("poll_interval_s", DEFAULT_POLL_INTERVAL_S)),
        )

    # Shorthand: just a workflow file name.
    return _CIGateConfig(workflow=raw)


class CIGateRunner:
    """Polls a CI provider for the run that matches a given commit SHA.

    Stateless: each call to :meth:`wait_for_workflow` runs an independent
    polling loop.  Subprocess invocations of ``gh`` are isolated and
    bounded by ``DEFAULT_GH_INVOKE_TIMEOUT_S`` so the gate never hangs
    silently.

    Constructor takes ``poll_interval_s`` and ``sleep_func`` to keep
    tests fast (override the sleep) without changing the production
    cadence.

    Args:
        poll_interval_s: Seconds between ``gh run list`` polls.  Defaults
            to :data:`DEFAULT_POLL_INTERVAL_S` (15 s) which is well below
            GitHub's secondary-rate-limit threshold.
        sleep_func: Callable used for blocking sleeps.  Defaults to
            :func:`time.sleep`.  Tests inject a no-op so the polling
            loop runs at full speed.
        time_func: Callable returning monotonic seconds.  Defaults to
            :func:`time.monotonic`.  Tests inject a counter to drive
            deterministic timeout behaviour.
    """

    def __init__(
        self,
        *,
        poll_interval_s: int = DEFAULT_POLL_INTERVAL_S,
        sleep_func: Any = time.sleep,
        time_func: Any = time.monotonic,
    ) -> None:
        self._poll_interval_s = poll_interval_s
        self._sleep = sleep_func
        self._time = time_func

    # ── Public API ────────────────────────────────────────────────────────

    def wait_for_workflow(
        self,
        provider: str,
        workflow: str,
        branch: str,
        commit_sha: str,
        timeout_s: int = DEFAULT_TIMEOUT_S,
    ) -> CIGateResult:
        """Wait for *workflow* on *branch* @ *commit_sha* to complete.

        Args:
            provider: ``"github"`` (supported) or ``"gitlab"`` (raises
                :class:`NotImplementedError`).
            workflow: Workflow file name (e.g. ``"ci.yml"``) — the value
                passed to ``gh run list --workflow``.
            branch: Branch name to scope the search.  Pass the agent's
                current branch (e.g. ``"feat/ci-gate"``).
            commit_sha: Full commit SHA the run must match.  Used to
                disambiguate when multiple runs exist for the same branch.
            timeout_s: Hard ceiling on total wait time, including initial
                polling for the run to appear.

        Returns:
            A :class:`CIGateResult`.  Never raises for normal failures
            (missing gh, timeout, failed CI) — those are reported as
            ``passed=False`` with a descriptive ``conclusion`` sentinel.
            Only raises :class:`NotImplementedError` for unsupported
            providers, since that is a programmer error rather than a
            runtime condition.
        """
        provider_norm = (provider or "").lower().strip()

        if provider_norm == "gitlab":
            # Future work: GitLab CI integration via `glab` CLI or REST.
            # For v1 we surface a clear NotImplementedError so plan
            # authors discover the gap immediately rather than at runtime
            # of an opt-in gate.
            raise NotImplementedError(
                "GitLab CI provider is not yet supported. "
                "Pass --provider github for now (Wave 4.1 ships GitHub Actions only)."
            )

        if provider_norm not in ("github", ""):
            raise NotImplementedError(
                f"Unknown CI provider '{provider}'. Supported: github."
            )

        return self._wait_for_github(workflow, branch, commit_sha, timeout_s)

    # ── GitHub implementation ─────────────────────────────────────────────

    def _wait_for_github(
        self,
        workflow: str,
        branch: str,
        commit_sha: str,
        timeout_s: int,
    ) -> CIGateResult:
        """Poll ``gh run list`` until the matching run completes or times out."""
        # Pre-flight: gh must be on PATH.  Without it we cannot do anything,
        # so return a friendly failure that the operator can act on without
        # reading the traceback.
        if shutil.which("gh") is None:
            return CIGateResult(
                passed=False,
                conclusion="gh_unavailable",
                log_excerpt="install gh CLI: https://cli.github.com",
            )

        start = self._time()
        deadline = start + max(1, timeout_s)
        run_id = ""
        run_url = ""

        # Phase 1: poll until a run matching commit_sha appears.  GitHub
        # Actions can take up to a minute to register a push, so we poll
        # rather than expect the run to be there on first try.
        while self._time() < deadline:
            run_id, run_url = self._find_run_for_sha(workflow, branch, commit_sha)
            if run_id:
                break
            self._sleep(self._poll_interval_s)
        else:
            # while-else: loop exhausted without break.
            return CIGateResult(
                passed=False,
                conclusion="timeout",
                duration_s=self._time() - start,
                log_excerpt=(
                    f"Timed out after {timeout_s}s waiting for a run of "
                    f"workflow '{workflow}' on branch '{branch}' for sha "
                    f"{commit_sha[:8]} to appear."
                )[:LOG_EXCERPT_MAX_CHARS],
            )

        # Phase 2: poll the same run for completion.  We use `gh run list`
        # rather than `gh run watch` so the polling cadence is identical
        # and tests can drive both phases with one mock pattern.
        while self._time() < deadline:
            status, conclusion, url = self._poll_run_status(run_id)
            if url:
                run_url = url
            if status == "completed":
                passed = conclusion == GITHUB_PASS_CONCLUSION
                excerpt = ""
                if not passed:
                    excerpt = self._fetch_failed_log_excerpt(run_id)
                return CIGateResult(
                    passed=passed,
                    run_id=run_id,
                    conclusion=conclusion or "unknown",
                    url=run_url,
                    duration_s=self._time() - start,
                    log_excerpt=excerpt,
                )
            self._sleep(self._poll_interval_s)

        return CIGateResult(
            passed=False,
            run_id=run_id,
            conclusion="timeout",
            url=run_url,
            duration_s=self._time() - start,
            log_excerpt=(
                f"Timed out after {timeout_s}s waiting for run {run_id} "
                "to complete."
            )[:LOG_EXCERPT_MAX_CHARS],
        )

    # ── gh CLI shell-outs ────────────────────────────────────────────────

    def _find_run_for_sha(
        self, workflow: str, branch: str, commit_sha: str
    ) -> tuple[str, str]:
        """Return (run_id, url) for the most recent run matching *commit_sha*.

        Empty strings when no matching run is found yet (still pending
        registration with GitHub).
        """
        cmd = [
            "gh", "run", "list",
            "--workflow", workflow,
            "--branch", branch,
            "--limit", "1",
            "--json", "databaseId,headSha,status,conclusion,url",
        ]
        proc = self._run_gh(cmd)
        if proc is None or proc.returncode != 0:
            return "", ""

        try:
            runs = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError:
            return "", ""

        if not runs:
            return "", ""

        latest = runs[0]
        head_sha = str(latest.get("headSha", ""))
        # Match on full SHA OR short prefix (gh sometimes returns short shas
        # in older versions).  This mirrors how `gh pr checks` matches.
        if commit_sha and head_sha and not (
            head_sha == commit_sha
            or head_sha.startswith(commit_sha)
            or commit_sha.startswith(head_sha)
        ):
            return "", ""

        return str(latest.get("databaseId", "")), str(latest.get("url", ""))

    def _poll_run_status(self, run_id: str) -> tuple[str, str, str]:
        """Return (status, conclusion, url) for *run_id*."""
        cmd = [
            "gh", "run", "view", run_id,
            "--json", "status,conclusion,url",
        ]
        proc = self._run_gh(cmd)
        if proc is None or proc.returncode != 0:
            return "", "", ""

        try:
            data = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            return "", "", ""

        return (
            str(data.get("status", "")),
            str(data.get("conclusion", "")),
            str(data.get("url", "")),
        )

    def _fetch_failed_log_excerpt(self, run_id: str) -> str:
        """Best-effort: last 500 chars of `gh run view <id> --log-failed`."""
        cmd = ["gh", "run", "view", run_id, "--log-failed"]
        proc = self._run_gh(cmd, timeout=DEFAULT_GH_INVOKE_TIMEOUT_S)
        if proc is None:
            return ""
        # --log-failed exits non-zero when there are no failed logs (e.g.
        # cancelled run with no job output).  We still want whatever stdout
        # captured, capped.
        text = (proc.stdout or "") + (proc.stderr or "")
        return text[-LOG_EXCERPT_MAX_CHARS:] if text else ""

    @staticmethod
    def _run_gh(
        cmd: list[str], *, timeout: int = DEFAULT_GH_INVOKE_TIMEOUT_S
    ) -> Optional[subprocess.CompletedProcess]:
        """Run a ``gh`` subprocess with consistent safety flags.

        Returns ``None`` on FileNotFoundError or TimeoutExpired so callers
        can treat both as "no data" without try/except sprawl.
        """
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except FileNotFoundError:
            logger.warning("CI gate: gh CLI disappeared mid-poll")
            return None
        except subprocess.TimeoutExpired:
            logger.warning("CI gate: gh invocation timed out: %s", " ".join(cmd))
            return None
