"""Tests for the PR-review-comment harvester.

The gh CLI is never invoked — every test injects a fake ``runner``
callable or pre-fetched JSON into :func:`harvest_reviews`.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from agent_baton.core.knowledge.review_harvester import (
    classify_severity,
    fetch_pr_review_comments,
    filter_salient,
    harvest_reviews,
    parse_comments,
    render_lessons_doc,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _comment(
    *,
    cid: int,
    body: str,
    user: str = "alice",
    path: str | None = "src/foo.py",
    url: str | None = None,
) -> dict:
    return {
        "id": cid,
        "body": body,
        "user": {"login": user},
        "path": path,
        "html_url": url or f"https://github.com/o/r/pull/9#discussion_r{cid}",
    }


def _make_payload() -> list[dict]:
    return [
        _comment(
            cid=1,
            body="🚫 MUST add input validation here — this is a SQL injection vector.",
            path="src/api.py",
        ),
        _comment(
            cid=2,
            body="⚠️ SHOULD use a context manager so the file is closed deterministically.",
            path="src/io.py",
        ),
        _comment(
            cid=3,
            body="💡 CONSIDER extracting this into a helper for reuse across modules.",
            path="src/io.py",
        ),
        _comment(
            cid=4,
            body="typo: tabs vs spaces here.",  # short, no marker -> dropped
            path="src/io.py",
        ),
        _comment(
            cid=5,
            body=(
                "This block is hard to follow. The control flow inverts halfway "
                "through and we end up dispatching twice on the same condition."
            ),
            path="src/dispatch.py",
        ),
        _comment(cid=6, body="", path="src/io.py"),  # empty body -> dropped
    ]


# ---------------------------------------------------------------------------
# Severity / parsing
# ---------------------------------------------------------------------------


def test_classify_severity_table() -> None:
    assert classify_severity("🚫 do not merge") == "blocker"
    assert classify_severity("MUST validate input") == "blocker"
    assert classify_severity("⚠️ heads up") == "warning"
    assert classify_severity("SHOULD raise on overflow") == "warning"
    assert classify_severity("💡 a thought") == "suggestion"
    assert classify_severity("CONSIDER renaming") == "suggestion"
    assert classify_severity("plain prose with no marker") == "note"


def test_parse_comments_drops_empty_bodies() -> None:
    parsed = parse_comments(_make_payload())
    # 6 raw -> 5 after dropping the empty body
    assert [c.id for c in parsed] == [1, 2, 3, 4, 5]
    assert parsed[0].severity == "blocker"
    assert parsed[1].severity == "warning"
    assert parsed[2].severity == "suggestion"
    # Comment 4 ("nit: missing trailing newline.") is short and uses no
    # severity emoji — its severity stays "note".
    assert parsed[3].severity == "note"
    assert parsed[4].severity == "note"


def test_filter_excludes_short_noise() -> None:
    parsed = parse_comments(_make_payload())
    salient = filter_salient(parsed)
    kept_ids = {c.id for c in salient}
    # Severity-marked comments survive, length>50 prose survives,
    # the short un-marked nit (id=4) is dropped.
    assert kept_ids == {1, 2, 3, 5}


# ---------------------------------------------------------------------------
# gh runner fixture
# ---------------------------------------------------------------------------


def test_fetch_pr_review_comments_uses_runner_with_correct_args() -> None:
    """gh CLI must NEVER be invoked from tests — verify via a stub runner."""
    captured: list[list[str]] = []
    payload = _make_payload()

    def fake_runner(args: list[str]) -> str:
        captured.append(args)
        return json.dumps(payload)

    out = fetch_pr_review_comments(9, "octo/repo", runner=fake_runner)
    assert out == payload
    assert captured == [
        ["api", "repos/octo/repo/pulls/9/comments", "--paginate"],
    ]


# ---------------------------------------------------------------------------
# End-to-end render + harvest
# ---------------------------------------------------------------------------


def test_back_links_use_html_url(tmp_path: Path) -> None:
    payload = _make_payload()

    def runner(args: list[str]) -> str:
        if "/comments" in args[1]:
            return json.dumps(payload)
        # /reviews
        return json.dumps([])

    knowledge_root = tmp_path / ".claude" / "knowledge"
    result = harvest_reviews(
        9,
        "octo/repo",
        knowledge_root=knowledge_root,
        runner=runner,
    )

    assert result.written is True
    assert result.target_path == knowledge_root / "lessons-from-pr-9.md"
    text = result.target_path.read_text(encoding="utf-8")

    # Bucketed by file
    assert "## src/api.py" in text
    assert "## src/io.py" in text
    assert "## src/dispatch.py" in text
    # Each kept comment produces a back-link to its source URL
    for cid in (1, 2, 3, 5):
        assert f"https://github.com/o/r/pull/9#discussion_r{cid}" in text
    # Dropped comments are not referenced
    assert "discussion_r4" not in text
    assert "discussion_r6" not in text

    # Frontmatter includes source_urls list
    front_text = text.split("---", 2)[1]
    front = yaml.safe_load(front_text)
    assert front["pr_number"] == 9
    assert front["repo"] == "octo/repo"
    assert sorted(front["source_urls"]) == [
        "https://github.com/o/r/pull/9#discussion_r1",
        "https://github.com/o/r/pull/9#discussion_r2",
        "https://github.com/o/r/pull/9#discussion_r3",
        "https://github.com/o/r/pull/9#discussion_r5",
    ]


def test_harvest_reviews_idempotent(tmp_path: Path) -> None:
    payload = _make_payload()

    def runner(args: list[str]) -> str:
        if "/comments" in args[1]:
            return json.dumps(payload)
        return json.dumps([])

    knowledge_root = tmp_path / ".claude" / "knowledge"

    first = harvest_reviews(9, "octo/repo", knowledge_root=knowledge_root, runner=runner)
    assert first.written is True

    second = harvest_reviews(9, "octo/repo", knowledge_root=knowledge_root, runner=runner)
    assert second.written is False
    assert second.skipped_reason == "no new comments since last harvest"


def test_harvest_uses_pre_fetched_payloads(tmp_path: Path) -> None:
    """raw_comments / raw_reviews bypass the runner entirely."""
    knowledge_root = tmp_path / ".claude" / "knowledge"

    def boom(_args: list[str]) -> str:  # would fail if invoked
        raise AssertionError("runner should not be called")

    result = harvest_reviews(
        42,
        "owner/name",
        knowledge_root=knowledge_root,
        runner=boom,
        raw_comments=_make_payload(),
        raw_reviews=[],
    )
    assert result.written is True
    assert result.comments_kept == 4


def test_render_lessons_doc_severity_breakdown() -> None:
    parsed = parse_comments(_make_payload())
    salient = filter_salient(parsed)
    rendered = render_lessons_doc(9, "octo/repo", salient)
    assert "[blocker]" in rendered
    assert "[warning]" in rendered
    assert "[suggestion]" in rendered
    assert "blocker=1" in rendered
    assert "warning=1" in rendered
    assert "suggestion=1" in rendered
