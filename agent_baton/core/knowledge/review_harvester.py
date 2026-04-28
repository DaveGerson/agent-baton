"""PR-review-comment-to-knowledge harvester.

Pulls review comments from a GitHub pull request via the ``gh`` CLI and
distils the salient ones into a single ``lessons-from-pr-<N>.md``
knowledge document.

Salience filter:
    A comment is considered salient when ANY is true:
        - The body contains a severity emoji (``🚫`` / ``⚠️`` / ``💡``).
        - The body contains a severity keyword
          (``MUST``, ``SHOULD``, ``CONSIDER``, ``BUG``, ``NIT``, …).
        - The body length exceeds ``MIN_BODY_LENGTH`` (default: 50 chars).

Severity tagging (keyed on first match):
    ``🚫`` / ``MUST`` / ``BUG``           -> ``blocker``
    ``⚠️`` / ``SHOULD`` / ``WARNING``     -> ``warning``
    ``💡`` / ``CONSIDER`` / ``IDEA``      -> ``suggestion``
    (otherwise)                            -> ``note``

Idempotency:
    The output document records each source-comment URL in a
    ``source_urls`` frontmatter list. On re-harvest of the same PR, an
    existing doc with the same set of URLs is left untouched.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

MIN_BODY_LENGTH = 50

# Severity markers — ordered by precedence (first match wins).
_SEVERITY_TABLE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("blocker", ("🚫", "MUST", "BUG", "BLOCKER")),
    ("warning", ("⚠️", "⚠", "SHOULD", "WARNING", "ISSUE")),
    ("suggestion", ("💡", "CONSIDER", "IDEA", "SUGGEST", "NIT")),
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ReviewComment:
    """A normalised PR review comment."""

    id: int
    body: str
    user: str
    path: str | None
    url: str
    severity: str = "note"

    @property
    def is_salient(self) -> bool:
        if self.severity != "note":
            return True
        return len(self.body.strip()) > MIN_BODY_LENGTH


@dataclass
class ReviewHarvestResult:
    pr_number: int
    target_path: Path
    comments_in: int = 0
    comments_kept: int = 0
    written: bool = False
    skipped_reason: str | None = None
    by_severity: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Severity classification
# ---------------------------------------------------------------------------


def classify_severity(body: str) -> str:
    """Return the severity bucket for *body* (``"note"`` if no marker)."""
    upper = body.upper()
    for severity, markers in _SEVERITY_TABLE:
        for marker in markers:
            if marker in body or marker in upper:
                return severity
    return "note"


# ---------------------------------------------------------------------------
# gh CLI integration
# ---------------------------------------------------------------------------


def _run_gh(args: list[str]) -> str:
    """Run ``gh`` and return stdout. Raise :class:`RuntimeError` on failure."""
    if shutil.which("gh") is None:
        raise RuntimeError(
            "gh CLI not found on PATH — install GitHub CLI to use the "
            "review harvester (https://cli.github.com/)."
        )
    proc = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"gh exited {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout


def fetch_pr_review_comments(
    pr_number: int,
    repo: str,
    *,
    runner=_run_gh,
) -> list[dict]:
    """Fetch line-level PR review comments from GitHub.

    Args:
        pr_number: The PR number.
        repo: ``owner/name`` slug.
        runner: Override for the gh-CLI invoker (used by tests).
    """
    raw = runner(
        ["api", f"repos/{repo}/pulls/{pr_number}/comments", "--paginate"]
    )
    if not raw.strip():
        return []
    data = json.loads(raw)
    if not isinstance(data, list):
        raise RuntimeError(f"unexpected gh response shape for PR {pr_number}")
    return data


def fetch_pr_reviews(
    pr_number: int,
    repo: str,
    *,
    runner=_run_gh,
) -> list[dict]:
    """Fetch top-level PR reviews (review-summary bodies)."""
    raw = runner(
        ["api", f"repos/{repo}/pulls/{pr_number}/reviews", "--paginate"]
    )
    if not raw.strip():
        return []
    data = json.loads(raw)
    if not isinstance(data, list):
        raise RuntimeError(f"unexpected gh response shape for PR {pr_number} reviews")
    return data


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_comments(payload: list[dict]) -> list[ReviewComment]:
    """Normalise the raw gh JSON into :class:`ReviewComment` objects.

    Empty / deleted comments are filtered out at this stage.
    """
    out: list[ReviewComment] = []
    for entry in payload:
        body = (entry.get("body") or "").strip()
        if not body:
            continue
        user = (entry.get("user") or {}).get("login") or "unknown"
        url = entry.get("html_url") or ""
        path = entry.get("path")
        cid = int(entry.get("id") or 0)
        out.append(
            ReviewComment(
                id=cid,
                body=body,
                user=user,
                path=path,
                url=url,
                severity=classify_severity(body),
            )
        )
    return out


def filter_salient(comments: list[ReviewComment]) -> list[ReviewComment]:
    """Return only comments with a severity marker OR length > 50."""
    return [c for c in comments if c.is_salient]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


_SEVERITY_RANK = {"blocker": 0, "warning": 1, "suggestion": 2, "note": 3}


def _bucket_by_path(comments: list[ReviewComment]) -> dict[str, list[ReviewComment]]:
    buckets: dict[str, list[ReviewComment]] = {}
    for c in comments:
        key = c.path or "(general)"
        buckets.setdefault(key, []).append(c)
    return buckets


def _summarise_body(body: str, max_len: int = 240) -> str:
    """One-line-ish summary suitable for a bullet."""
    cleaned = re.sub(r"\s+", " ", body).strip()
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"


def render_lessons_doc(
    pr_number: int,
    repo: str,
    comments: list[ReviewComment],
) -> str:
    """Render the lessons markdown for *comments* (already filtered)."""
    sorted_buckets = _bucket_by_path(comments)
    by_severity: dict[str, int] = {}
    for c in comments:
        by_severity[c.severity] = by_severity.get(c.severity, 0) + 1

    front = {
        "name": f"lessons-from-pr-{pr_number}",
        "description": (
            f"Lessons harvested from review comments on {repo}#{pr_number}"
        ),
        "tags": ["lessons", "code-review", "pr"],
        "priority": "normal",
        "pr_number": pr_number,
        "repo": repo,
        "source_urls": sorted({c.url for c in comments if c.url}),
    }
    front_yaml = yaml.safe_dump(front, sort_keys=False).strip()

    lines = [
        "---",
        front_yaml,
        "---",
        "",
        f"# Lessons from {repo}#{pr_number}",
        "",
        f"Harvested {len(comments)} salient review comment(s).",
        "",
        "Severity breakdown: "
        + ", ".join(
            f"{sev}={by_severity.get(sev, 0)}"
            for sev in ("blocker", "warning", "suggestion", "note")
            if by_severity.get(sev)
        ),
        "",
    ]

    for path in sorted(sorted_buckets):
        lines.append(f"## {path}")
        lines.append("")
        bucket = sorted(
            sorted_buckets[path],
            key=lambda c: (_SEVERITY_RANK.get(c.severity, 99), c.id),
        )
        for c in bucket:
            badge = f"[{c.severity}]"
            summary = _summarise_body(c.body)
            link = f" ([source]({c.url}))" if c.url else ""
            lines.append(f"- {badge} (@{c.user}) {summary}{link}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def _existing_source_urls(doc_path: Path) -> list[str] | None:
    if not doc_path.is_file():
        return None
    try:
        text = doc_path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return None
    val = meta.get("source_urls")
    if not isinstance(val, list):
        return None
    return [str(v) for v in val]


def harvest_reviews(
    pr_number: int,
    repo: str,
    *,
    knowledge_root: Path | None = None,
    runner=_run_gh,
    raw_comments: list[dict] | None = None,
    raw_reviews: list[dict] | None = None,
) -> ReviewHarvestResult:
    """Harvest review comments for a PR into a lessons knowledge doc.

    Args:
        pr_number: PR number.
        repo: ``owner/name`` slug.
        knowledge_root: Root knowledge directory (default ``.claude/knowledge``).
        runner: Override for the gh-CLI invoker (tests inject this).
        raw_comments: Pre-fetched line-level comments (skips gh CLI).
        raw_reviews: Pre-fetched review-summary objects.
    """
    if knowledge_root is None:
        knowledge_root = (Path(".claude") / "knowledge").resolve()
    knowledge_root.mkdir(parents=True, exist_ok=True)

    target = knowledge_root / f"lessons-from-pr-{pr_number}.md"

    if raw_comments is None:
        raw_comments = fetch_pr_review_comments(pr_number, repo, runner=runner)
    if raw_reviews is None:
        try:
            raw_reviews = fetch_pr_reviews(pr_number, repo, runner=runner)
        except Exception as exc:  # tolerate review-list failures
            logger.warning("Could not fetch top-level reviews: %s", exc)
            raw_reviews = []

    # Treat top-level reviews with non-empty body the same as line comments,
    # mapping them to the "(general)" bucket via ``path = None``.
    raw_all: list[dict] = list(raw_comments)
    for review in raw_reviews:
        body = (review.get("body") or "").strip()
        if not body:
            continue
        raw_all.append(
            {
                "id": review.get("id"),
                "body": body,
                "user": review.get("user") or {},
                "path": None,
                "html_url": review.get("html_url") or "",
            }
        )

    parsed = parse_comments(raw_all)
    salient = filter_salient(parsed)

    result = ReviewHarvestResult(
        pr_number=pr_number,
        target_path=target,
        comments_in=len(parsed),
        comments_kept=len(salient),
    )
    for c in salient:
        result.by_severity[c.severity] = result.by_severity.get(c.severity, 0) + 1

    if not salient:
        result.skipped_reason = "no salient comments"
        return result

    new_urls = sorted({c.url for c in salient if c.url})
    existing_urls = _existing_source_urls(target)
    if existing_urls is not None and sorted(existing_urls) == new_urls:
        result.skipped_reason = "no new comments since last harvest"
        return result

    rendered = render_lessons_doc(pr_number, repo, salient)
    target.write_text(rendered, encoding="utf-8")
    result.written = True
    return result
