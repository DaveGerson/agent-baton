"""Smoke tests for ``baton slo`` CLI subcommands (O1.5)."""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from agent_baton.cli.commands.observe import slo_cmd
from agent_baton.core.storage.slo_store import SLOStore
from agent_baton.models.slo import DEFAULT_SLOS


def _ns(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    return tmp_path / "baton.db"


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------


class TestRegister:
    def test_register_returns_parser(self) -> None:
        root = argparse.ArgumentParser(prog="baton")
        sub = root.add_subparsers()
        p = slo_cmd.register(sub)
        assert isinstance(p, argparse.ArgumentParser)
        # Make sure all five subactions parse.
        for action in ("define", "list", "measure", "burns", "seed-defaults"):
            args = root.parse_args(
                [
                    "slo",
                    action,
                ]
                + (
                    [
                        "--name",
                        "x",
                        "--sli",
                        "dispatch_success_rate",
                        "--target",
                        "0.99",
                    ]
                    if action == "define"
                    else []
                )
            )
            assert args.slo_action == action


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


class TestHandlers:
    def test_seed_defaults(self, db: Path, capsys: pytest.CaptureFixture[str]) -> None:
        slo_cmd.handler(_ns(slo_action="seed-defaults", db=str(db)))
        out = capsys.readouterr().out
        assert "Seeded 3 canonical SLO" in out
        store = SLOStore(db)
        names = {d.name for d in store.list_definitions()}
        assert names == {s.name for s in DEFAULT_SLOS}

    def test_define_persists(self, db: Path, capsys: pytest.CaptureFixture[str]) -> None:
        slo_cmd.handler(
            _ns(
                slo_action="define",
                db=str(db),
                name="custom",
                sli="gate_pass_rate",
                target=0.97,
                window=14,
                description="custom slo",
            )
        )
        out = capsys.readouterr().out
        assert "SLO 'custom' defined" in out
        store = SLOStore(db)
        assert store.get_definition("custom") is not None

    def test_define_rejects_invalid_sli(
        self, db: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        slo_cmd.handler(
            _ns(
                slo_action="define",
                db=str(db),
                name="x",
                sli="bogus",
                target=0.99,
                window=28,
                description="",
            )
        )
        out = capsys.readouterr().out
        assert "Error: --sli" in out
        store = SLOStore(db)
        assert store.get_definition("x") is None

    def test_define_rejects_target_out_of_range(
        self, db: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        slo_cmd.handler(
            _ns(
                slo_action="define",
                db=str(db),
                name="x",
                sli="gate_pass_rate",
                target=1.5,
                window=28,
                description="",
            )
        )
        out = capsys.readouterr().out
        assert "Error: --target" in out

    def test_list_empty(self, db: Path, capsys: pytest.CaptureFixture[str]) -> None:
        slo_cmd.handler(_ns(slo_action="list", db=str(db)))
        out = capsys.readouterr().out
        assert "No SLOs defined" in out

    def test_list_with_seeded_defaults(
        self, db: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        slo_cmd.handler(_ns(slo_action="seed-defaults", db=str(db)))
        capsys.readouterr()  # drain
        slo_cmd.handler(_ns(slo_action="list", db=str(db)))
        out = capsys.readouterr().out
        assert "dispatch_success_rate" in out
        assert "gate_pass_rate" in out
        assert "engine_uptime" in out
        # No measurements yet -> "no data" status.
        assert "no data" in out

    def test_measure_all(self, db: Path, capsys: pytest.CaptureFixture[str]) -> None:
        slo_cmd.handler(_ns(slo_action="seed-defaults", db=str(db)))
        capsys.readouterr()
        slo_cmd.handler(_ns(slo_action="measure", db=str(db), name=None))
        out = capsys.readouterr().out
        # Each canonical SLO should appear once with an OK flag (no real
        # data -> SLI defaults to 1.0 which beats every target).
        assert out.count("OK") == 3
        store = SLOStore(db)
        for s in DEFAULT_SLOS:
            assert store.latest_measurement(s.name) is not None

    def test_measure_unknown_name(
        self, db: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        slo_cmd.handler(_ns(slo_action="measure", db=str(db), name="ghost"))
        out = capsys.readouterr().out
        assert "No SLO named 'ghost'" in out

    def test_burns_empty(self, db: Path, capsys: pytest.CaptureFixture[str]) -> None:
        slo_cmd.handler(
            _ns(slo_action="burns", db=str(db), name=None, since="")
        )
        out = capsys.readouterr().out
        assert "No error-budget burns recorded" in out


# ---------------------------------------------------------------------------
# _resolve_since
# ---------------------------------------------------------------------------


class TestResolveSince:
    def test_passthrough_for_iso_value(self) -> None:
        assert (
            slo_cmd._resolve_since("2026-04-29T10:00:00Z")
            == "2026-04-29T10:00:00Z"
        )

    def test_duration_d_suffix(self) -> None:
        out = slo_cmd._resolve_since("7d")
        # Must look like an ISO-Z timestamp.
        assert out.endswith("Z")
        assert len(out) == 20

    def test_duration_h_and_w_suffix(self) -> None:
        for v in ("3h", "2w"):
            assert slo_cmd._resolve_since(v).endswith("Z")
