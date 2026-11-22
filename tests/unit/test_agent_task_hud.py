# -*- coding: utf-8 -*-
"""Whether the main page shows the task HUD, kept by the agent process.

The setting used to live only in the main page's browser storage, set from the
small panel of the hammer button; the hammer now opens the NekoPower page,
which reads and changes it through the agent process (``app.agent_task_hud``,
the ``set_task_hud_visible`` command, ``task_hud_visible`` in the status
snapshot and in ``/console/switches``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The setting's file in a tmp dir instead of the config dir."""
    from app import agent_task_hud

    class _FakeCM:
        def load_json_config(self, filename, default_value=None):
            path = tmp_path / filename
            if not path.exists():
                return dict(default_value or {})
            return json.loads(path.read_text(encoding="utf-8"))

        def save_json_config(self, filename, data, *, bypass_write_fence: bool = False):
            (tmp_path / filename).write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr("utils.config_manager.get_config_manager", lambda: _FakeCM())
    agent_task_hud.reset_cache_for_testing()
    yield tmp_path / agent_task_hud.FILENAME
    agent_task_hud.reset_cache_for_testing()


def test_never_set_reads_as_none_and_a_setting_is_kept(store: Path):
    from app import agent_task_hud

    assert agent_task_hud.get_visible() is None
    assert not store.exists()

    assert agent_task_hud.set_visible(False) is True
    assert json.loads(store.read_text(encoding="utf-8")) == {"visible": False}
    assert agent_task_hud.set_visible(False) is False  # the same again is no change

    agent_task_hud.reset_cache_for_testing()  # as at the next start
    assert agent_task_hud.get_visible() is False
    assert agent_task_hud.set_visible(True) is True
    assert agent_task_hud.get_visible() is True


@pytest.mark.parametrize("written", [{"visible": "false"}, {"visible": 0}, ["visible"], {}])
def test_a_file_that_says_nothing_clear_reads_as_never_set(store: Path, written):
    from app import agent_task_hud

    store.write_text(json.dumps(written), encoding="utf-8")
    assert agent_task_hud.get_visible() is None


def test_it_is_not_among_the_switches_replayed_at_start():
    from app import agent_runtime_intent, agent_task_hud

    assert "task_hud_visible" not in agent_runtime_intent.INTENT_KEYS
    assert agent_task_hud.FILENAME != agent_runtime_intent.INTENT_FILENAME


@pytest.fixture
def routes(store: Path, monkeypatch: pytest.MonkeyPatch):
    from app.agent_server import api_routes as srv

    told: list[str | None] = []
    bumped: list[int] = []

    async def _emit(lanlan_name=None):
        told.append(lanlan_name)

    monkeypatch.setattr(srv, "_emit_agent_status_update", _emit)
    monkeypatch.setattr(srv, "_bump_state_revision", lambda: bumped.append(1))
    return srv, told, bumped


@pytest.mark.asyncio
async def test_the_command_keeps_the_setting_and_tells_every_main_page(routes):
    from app import agent_task_hud

    srv, told, bumped = routes
    answer = await srv.agent_command({"command": "set_task_hud_visible", "value": False, "lanlan_name": "Lan"})

    assert answer["success"] is True and answer["task_hud_visible"] is False
    assert agent_task_hud.get_visible() is False
    # One setting for every character: the update is not addressed to one of them.
    assert told == [None]
    # A new revision, or the main page drops the snapshot as one it has seen.
    assert bumped == [1]

    await srv.agent_command({"command": "set_task_hud_visible", "value": False})
    assert told == [None] and bumped == [1]  # nothing changed, nothing told


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"value": "false"}, {"value": 0}, {"value": None}])
async def test_the_command_takes_only_true_or_false(routes, payload):
    from app import agent_task_hud

    srv, told, _ = routes
    with pytest.raises(HTTPException) as refused:
        await srv.agent_command({"command": "set_task_hud_visible", **payload})
    assert refused.value.status_code == 400
    assert agent_task_hud.get_visible() is None and told == []


def test_the_status_snapshot_carries_the_setting(store: Path, monkeypatch: pytest.MonkeyPatch):
    from app import agent_task_hud
    from app.agent_server import capabilities

    monkeypatch.setattr("app.agent_server._check_agent_api_gate", lambda: {"ready": True, "reasons": []})

    assert capabilities._collect_agent_status_snapshot()["task_hud_visible"] is None
    agent_task_hud.set_visible(False)
    assert capabilities._collect_agent_status_snapshot()["task_hud_visible"] is False
