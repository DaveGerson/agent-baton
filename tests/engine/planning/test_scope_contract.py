"""Tests for :mod:`agent_baton.core.engine.planning.scope_contract`.

Phase 3 "Make scope contracts authoritative" -- deterministic path
normalization, directory-prefix matching, generated-file policy, and
write-scope derivation/diagnostics.
"""
from __future__ import annotations

import pytest

from agent_baton.core.engine.planning.scope_contract import (
    ROLE_PATH_HINTS,
    ScopeContractError,
    ScopeDiagnostic,
    derive_allowed_paths,
    diagnose_step_scope,
    is_generated_path,
    is_intentionally_read_only,
    is_write_capable,
    normalize_path_list,
    normalize_scope_path,
    path_candidates_from_text,
    paths_overlap,
)


# ---------------------------------------------------------------------------
# normalize_scope_path
# ---------------------------------------------------------------------------


class TestNormalizeScopePath:
    def test_forward_slash_path_unchanged(self) -> None:
        assert normalize_scope_path("app/reporting/service.py") == "app/reporting/service.py"

    def test_backslashes_normalize_to_forward_slashes(self) -> None:
        assert normalize_scope_path("app\\reporting\\service.py") == "app/reporting/service.py"

    def test_duplicate_slashes_collapse(self) -> None:
        assert normalize_scope_path("app//reporting///service.py") == "app/reporting/service.py"

    def test_leading_dot_slash_stripped(self) -> None:
        assert normalize_scope_path("./app/reporting.py") == "app/reporting.py"

    def test_trailing_slash_stripped(self) -> None:
        assert normalize_scope_path("app/reporting/") == "app/reporting"

    def test_glob_marker_preserved(self) -> None:
        assert normalize_scope_path("app/reporting/**") == "app/reporting/**"

    @pytest.mark.parametrize(
        "raw",
        ["/etc/passwd", "//host/share", "C:/Windows/System32", "c:/temp"],
    )
    def test_absolute_paths_rejected(self, raw: str) -> None:
        with pytest.raises(ScopeContractError):
            normalize_scope_path(raw)

    @pytest.mark.parametrize(
        "raw",
        ["../secrets.env", "app/../../etc/passwd", "app/../secrets"],
    )
    def test_traversal_rejected(self, raw: str) -> None:
        with pytest.raises(ScopeContractError):
            normalize_scope_path(raw)

    @pytest.mark.parametrize("raw", ["", "   ", ".", "./", None])
    def test_empty_or_none_rejected(self, raw) -> None:
        with pytest.raises(ScopeContractError):
            normalize_scope_path(raw)


class TestNormalizePathList:
    def test_dedupes_order_preserving(self) -> None:
        result = normalize_path_list(["app/a.py", "app/b.py", "app/a.py"])
        assert result == ["app/a.py", "app/b.py"]

    def test_drops_malformed_entries_without_raising(self) -> None:
        result = normalize_path_list(["app/a.py", "../escape", "", None])
        assert result == ["app/a.py"]

    def test_empty_input(self) -> None:
        assert normalize_path_list(None) == []
        assert normalize_path_list([]) == []


# ---------------------------------------------------------------------------
# paths_overlap
# ---------------------------------------------------------------------------


class TestPathsOverlap:
    def test_exact_match(self) -> None:
        assert paths_overlap("app/a.py", "app/a.py") is True

    def test_candidate_inside_allowed_directory(self) -> None:
        assert paths_overlap("app/reporting/service.py", "app/reporting") is True

    def test_allowed_inside_candidate_directory(self) -> None:
        assert paths_overlap("app", "app/reporting/service.py") is True

    def test_sibling_paths_do_not_overlap(self) -> None:
        assert paths_overlap("app/billing/x.py", "app/reporting") is False

    def test_prefix_string_without_separator_does_not_overlap(self) -> None:
        # "app/reporting2" must not be treated as inside "app/reporting".
        assert paths_overlap("app/reporting2/x.py", "app/reporting") is False

    def test_glob_star_star_covers_subtree(self) -> None:
        assert paths_overlap("app/reporting/deep/file.py", "app/reporting/**") is True
        assert paths_overlap("app/other/file.py", "app/reporting/**") is False

    def test_malformed_path_never_overlaps(self) -> None:
        assert paths_overlap("../escape", "app") is False


# ---------------------------------------------------------------------------
# is_generated_path
# ---------------------------------------------------------------------------


class TestIsGeneratedPath:
    @pytest.mark.parametrize(
        "path",
        [
            "dist/bundle.js",
            "app/__pycache__/mod.pyc",
            "node_modules/react/index.js",
            "build/output.bin",
            ".venv/lib/site-packages",
        ],
    )
    def test_generated_paths_detected(self, path: str) -> None:
        assert is_generated_path(path) is True

    def test_real_source_path_not_generated(self) -> None:
        assert is_generated_path("app/reporting/service.py") is False


# ---------------------------------------------------------------------------
# step-type classification
# ---------------------------------------------------------------------------


class TestStepTypeClassification:
    @pytest.mark.parametrize(
        "step_type", ["developing", "testing", "automation", "synthesis"]
    )
    def test_write_capable_types(self, step_type: str) -> None:
        assert is_write_capable(step_type) is True
        assert is_intentionally_read_only(step_type) is False

    @pytest.mark.parametrize("step_type", ["reviewing", "consulting"])
    def test_read_only_types(self, step_type: str) -> None:
        assert is_intentionally_read_only(step_type) is True
        assert is_write_capable(step_type) is False

    def test_unknown_step_type_is_neither(self) -> None:
        assert is_write_capable("planning") is False
        assert is_intentionally_read_only("planning") is False


# ---------------------------------------------------------------------------
# path_candidates_from_text / derive_allowed_paths
# ---------------------------------------------------------------------------


class TestPathCandidatesFromText:
    def test_extracts_path_like_tokens(self) -> None:
        candidates = path_candidates_from_text(
            "Implement app/reporting/service.py and wire routes.py"
        )
        assert "app/reporting/service.py" in candidates
        assert "routes.py" in candidates

    def test_prose_without_path_tokens_yields_nothing(self) -> None:
        assert path_candidates_from_text("Improve things.") == []

    def test_generated_path_tokens_excluded(self) -> None:
        candidates = path_candidates_from_text("Regenerate dist/bundle.js output")
        assert candidates == []


class TestDeriveAllowedPaths:
    def test_explicit_paths_win_and_are_normalized(self) -> None:
        paths, source = derive_allowed_paths(
            explicit_paths=["app\\reporting\\service.py"],
            deliverables=["ignored"],
        )
        assert paths == ["app/reporting/service.py"]
        assert source == "explicit"

    def test_explicit_generated_path_is_honored(self) -> None:
        paths, source = derive_allowed_paths(explicit_paths=["dist/bundle.js"])
        assert paths == ["dist/bundle.js"]
        assert source == "explicit"

    def test_falls_back_to_deliverables(self) -> None:
        paths, source = derive_allowed_paths(
            deliverables=["app/reporting/service.py"],
        )
        assert paths == ["app/reporting/service.py"]
        assert source == "deliverables"

    def test_falls_back_to_context_files_excluding_sentinel(self) -> None:
        paths, source = derive_allowed_paths(
            context_files=["CLAUDE.md", "app/reporting/service.py"],
        )
        assert paths == ["app/reporting/service.py"]
        assert source == "context_files"

    def test_falls_back_to_repo_topology(self) -> None:
        paths, source = derive_allowed_paths(likely_repo_areas=["app"])
        assert paths == ["app"]
        assert source == "repo_topology"

    def test_agent_role_only_fires_with_confirmed_existing_dirs(self) -> None:
        no_dirs_paths, no_dirs_source = derive_allowed_paths(agent_base="test-engineer")
        assert no_dirs_paths == []
        assert no_dirs_source == "none"

        with_dirs_paths, with_dirs_source = derive_allowed_paths(
            agent_base="test-engineer",
            existing_dirs=frozenset({"tests"}),
        )
        assert with_dirs_paths == ["tests"]
        assert with_dirs_source == "agent_role"

    def test_agent_role_hints_have_no_unknown_agents(self) -> None:
        # Sanity: every hint tuple is non-empty (a mapping with an empty
        # tuple would silently never fire).
        for hints in ROLE_PATH_HINTS.values():
            assert hints

    def test_no_evidence_anywhere_yields_none(self) -> None:
        paths, source = derive_allowed_paths()
        assert paths == []
        assert source == "none"


# ---------------------------------------------------------------------------
# diagnose_step_scope
# ---------------------------------------------------------------------------


class TestDiagnoseStepScope:
    def test_write_capable_with_paths_is_clean(self) -> None:
        assert diagnose_step_scope("1.1", "developing", ["app/a.py"]) is None

    def test_write_capable_without_paths_is_ambiguous_warning(self) -> None:
        diag = diagnose_step_scope("1.1", "developing", [])
        assert isinstance(diag, ScopeDiagnostic)
        assert diag.code == "write_scope_missing"
        assert diag.severity == "warning"
        assert "1.1" in str(diag)

    def test_read_only_without_paths_is_clean(self) -> None:
        assert diagnose_step_scope("review-1", "reviewing", []) is None
        assert diagnose_step_scope("2.1", "consulting", None) is None

    def test_unknown_step_type_without_paths_is_not_flagged(self) -> None:
        # Only the explicitly write-capable types are enforced -- an
        # unrecognized/neutral step_type (e.g. "planning") stays silent
        # rather than false-positiving on every non-standard step_type.
        assert diagnose_step_scope("1.1", "planning", []) is None

    def test_allowed_overlapping_blocked_is_contradictory_critical(self) -> None:
        diag = diagnose_step_scope(
            "1.1", "developing", ["app/reporting/service.py"], ["app/reporting"]
        )
        assert diag is not None
        assert diag.code == "write_scope_contradictory"
        assert diag.severity == "critical"

    def test_contradiction_takes_priority_over_missing(self) -> None:
        # allowed_paths is non-empty (so "missing" would not fire anyway)
        # but collides with blocked_paths -- contradiction always wins.
        diag = diagnose_step_scope("1.1", "reviewing", ["app/a.py"], ["app/a.py"])
        assert diag is not None
        assert diag.code == "write_scope_contradictory"

    def test_non_overlapping_allowed_and_blocked_is_clean(self) -> None:
        assert diagnose_step_scope("1.1", "developing", ["app/a.py"], ["app/b.py"]) is None

    def test_malformed_allowed_path_counts_as_missing_for_write_capable(self) -> None:
        diag = diagnose_step_scope("1.1", "developing", ["../escape"])
        assert diag is not None
        assert diag.code == "write_scope_missing"
