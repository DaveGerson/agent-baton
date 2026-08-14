"""Team-mode identity enforcement for the Spec Queue review endpoints.

``BATON_APPROVAL_MODE=team`` is a segregation-of-duties control: the user
who approves (or bounces) a spec draft must be a *known* user, and must not
be the submitter.  ``docs/api-reference.md`` states that in team mode the
``X-Baton-User`` header is **required** for approve/bounce operations.

These tests pin the observable HTTP behaviour of that control:

* an authenticated non-submitter can approve (200)
* the submitter cannot self-approve (403)
* an *anonymous* caller — no ``X-Baton-User``, no ``Authorization`` — must
  not be granted a synthetic identity that sails past the self-approval
  check; the request is rejected (401) and the draft is left untouched
* the mode is read from the environment at app-construction time, so a
  process that sets ``BATON_APPROVAL_MODE`` before ``create_app()`` really
  gets team behaviour
* ``local`` mode (the default) keeps its permissive anonymous behaviour

Bootstrap strategy mirrors ``tests/api/test_specs_api.py``: a tmp sqlite
file exported through ``BATON_SPEC_DRAFT_DB`` so routes resolve the test DB.
"""
from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_baton.api.server import create_app  # noqa: E402
from agent_baton.core.federate.spec_draft_store import SpecDraftStore  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _mock_enrichment():
    """Return a minimal EnrichmentData so a draft can reach 'enriched'."""
    from agent_baton.models.spec_draft import EnrichmentData

    return EnrichmentData(
        risk_level="LOW",
        guardrail_preset="Standard Development",
        required_reviewers=[],
        signals_found=[],
        confidence="high",
        est_usd_low=0.01,
        est_usd_mid=0.02,
        est_usd_high=0.025,
        cost_confidence="default",
        breakdown=[],
    )


@pytest.fixture()
def spec_db(tmp_path: Path) -> Path:
    """Create a fresh spec_drafts database for each test."""
    db = tmp_path / "spec_drafts.db"
    SpecDraftStore(db_path=db)
    return db


@pytest.fixture()
def store(spec_db: Path) -> SpecDraftStore:
    """Direct store access for seeding test data."""
    return SpecDraftStore(db_path=spec_db)


@pytest.fixture()
def make_client(tmp_path: Path, spec_db: Path, monkeypatch):
    """Return a factory building a TestClient in the given approval mode.

    The environment variable is set *before* ``create_app()`` so the app
    under test genuinely reflects the operator's configuration.
    """
    monkeypatch.setenv("BATON_SPEC_DRAFT_DB", str(spec_db))

    def _factory(approval_mode: str) -> TestClient:
        monkeypatch.setenv("BATON_APPROVAL_MODE", approval_mode)
        return TestClient(create_app(team_context_root=tmp_path))

    return _factory


def _seed_enriched(store: SpecDraftStore, submitted_by: str = "alice"):
    """Seed a draft owned by *submitted_by* in 'enriched' status."""
    draft = store.create(title="Team spec", body="body", submitted_by=submitted_by)
    store.update_enrichment(draft.id, _mock_enrichment())
    return draft


# ---------------------------------------------------------------------------
# Team mode — approve
# ---------------------------------------------------------------------------


class TestTeamModeApproveIdentity:
    def test_non_submitter_can_approve(self, make_client, store):
        """A different, identified user may approve — the control is not a wall."""
        client = make_client("team")
        draft = _seed_enriched(store, submitted_by="alice")

        r = client.post(
            f"/api/v1/pmo/specs/{draft.id}/approve",
            headers={"X-Baton-User": "bob"},
        )

        assert r.status_code == 200, r.text
        assert r.json()["status"] == "approved"
        assert store.get(draft.id).status == "approved"

    def test_submitter_cannot_self_approve(self, make_client, store):
        """alice approving alice's own spec is refused with 403."""
        client = make_client("team")
        draft = _seed_enriched(store, submitted_by="alice")

        r = client.post(
            f"/api/v1/pmo/specs/{draft.id}/approve",
            headers={"X-Baton-User": "alice"},
        )

        assert r.status_code == 403, r.text
        assert store.get(draft.id).status == "enriched"

    def test_anonymous_caller_cannot_approve(self, make_client, store):
        """No identity headers at all must not bypass segregation of duties.

        Regression: the middleware minted the synthetic identity
        ``local-user`` regardless of approval mode, so ``"local-user" !=
        "alice"`` and the self-approval check never fired — the submitter
        could approve their own spec simply by omitting the header.
        """
        client = make_client("team")
        draft = _seed_enriched(store, submitted_by="alice")

        r = client.post(f"/api/v1/pmo/specs/{draft.id}/approve")

        assert r.status_code == 401, r.text
        assert store.get(draft.id).status == "enriched"

    def test_anonymous_caller_cannot_bounce(self, make_client, store):
        """docs/api-reference.md: the header is required for approve *and* bounce."""
        client = make_client("team")
        draft = _seed_enriched(store, submitted_by="alice")

        r = client.post(
            f"/api/v1/pmo/specs/{draft.id}/bounce",
            json={"feedback": "needs detail"},
        )

        assert r.status_code == 401, r.text
        assert store.get(draft.id).status == "enriched"

    def test_identified_user_can_still_bounce(self, make_client, store):
        """Bounce with an identity is unaffected by the team-mode check."""
        client = make_client("team")
        draft = _seed_enriched(store, submitted_by="alice")

        r = client.post(
            f"/api/v1/pmo/specs/{draft.id}/bounce",
            json={"feedback": "needs detail"},
            headers={"X-Baton-User": "bob"},
        )

        assert r.status_code == 200, r.text
        assert store.get(draft.id).status == "bounced"


# ---------------------------------------------------------------------------
# Local mode — unchanged permissive behaviour
# ---------------------------------------------------------------------------


class TestLocalModeUnchanged:
    def test_anonymous_caller_may_approve_in_local_mode(self, make_client, store):
        """Local mode is single-operator: anonymous approval stays allowed."""
        client = make_client("local")
        draft = _seed_enriched(store, submitted_by="alice")

        r = client.post(f"/api/v1/pmo/specs/{draft.id}/approve")

        assert r.status_code == 200, r.text
        assert store.get(draft.id).status == "approved"

    def test_self_approval_allowed_in_local_mode(self, make_client, store):
        """The submitter approving their own spec is fine in local mode."""
        client = make_client("local")
        draft = _seed_enriched(store, submitted_by="alice")

        r = client.post(
            f"/api/v1/pmo/specs/{draft.id}/approve",
            headers={"X-Baton-User": "alice"},
        )

        assert r.status_code == 200, r.text
        assert store.get(draft.id).status == "approved"


# ---------------------------------------------------------------------------
# Middleware-level: no synthetic identity in team mode
# ---------------------------------------------------------------------------


class TestMiddlewareTeamModeIdentity:
    def _probe_app(self, recorded: list, **kwargs):
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        from agent_baton.api.middleware.user_identity import UserIdentityMiddleware

        async def _handler(request: Request):
            recorded.append(getattr(request.state, "user_id", "UNSET"))
            return JSONResponse({"user_id": recorded[-1]})

        app = Starlette(routes=[Route("/probe", _handler)])
        app.add_middleware(UserIdentityMiddleware, **kwargs)
        return app

    def test_team_mode_does_not_mint_local_user(self):
        """With no identity headers, team mode must not invent an identity.

        The implementation may either reject the request outright (401) or
        pass an empty identity down to the route layer — what it must not
        do is hand the route a usable ``local-user`` that satisfies an
        approver-differs-from-submitter check.
        """
        from starlette.testclient import TestClient as _SC

        recorded: list = []
        client = _SC(self._probe_app(recorded, approval_mode="team"))

        resp = client.get("/probe")

        if resp.status_code == 200:
            assert recorded, "handler never ran but response was 200"
            assert not recorded[-1], (
                "team mode minted a synthetic identity "
                f"{recorded[-1]!r} for an unauthenticated caller"
            )
        else:
            assert resp.status_code == 401, resp.text

    def test_team_mode_honours_explicit_identity(self):
        """An explicit X-Baton-User is still resolved in team mode."""
        from starlette.testclient import TestClient as _SC

        recorded: list = []
        client = _SC(self._probe_app(recorded, approval_mode="team"))

        resp = client.get("/probe", headers={"X-Baton-User": "alice"})

        assert resp.status_code == 200, resp.text
        assert recorded[-1] == "alice"

    def test_approval_mode_read_at_construction_time(self, monkeypatch):
        """The mode comes from the environment when the app is built.

        Regression: ``_APPROVAL_MODE`` was captured at *import* time, so a
        process that set ``BATON_APPROVAL_MODE=team`` after the module was
        first imported silently ran in local mode.
        """
        from starlette.testclient import TestClient as _SC

        monkeypatch.setenv("BATON_APPROVAL_MODE", "team")

        recorded: list = []
        client = _SC(self._probe_app(recorded))

        resp = client.get("/probe")

        if resp.status_code == 200:
            assert recorded, "handler never ran but response was 200"
            assert not recorded[-1], (
                "BATON_APPROVAL_MODE=team set before app construction did not "
                f"take effect; got identity {recorded[-1]!r}"
            )
        else:
            assert resp.status_code == 401, resp.text
