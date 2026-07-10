from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.plugins.neko_roast.core.runtime import RoastRuntime


class ConfigApi:
    def __init__(self) -> None:
        self.updates: list[dict] = []
        self.ensure_payloads: list[dict] = []
        self.update_entered: asyncio.Event | None = None
        self.resume_update: asyncio.Event | None = None

    async def dump(self, timeout: float = 0) -> dict:
        return {"neko_roast": {}}

    async def update(self, payload: dict) -> None:
        if self.update_entered is not None:
            self.update_entered.set()
        if self.resume_update is not None:
            await self.resume_update.wait()
        self.updates.append(payload)

    async def profile_ensure_active(self, _profile: str, payload: dict, timeout: float = 0) -> None:
        self.ensure_payloads.append(payload)


class Plugin:
    def __init__(self, tmp_path: Path) -> None:
        self.config = ConfigApi()
        self.ctx = None
        self.logger = None
        self._data_path = tmp_path

    def data_path(self) -> Path:
        return self._data_path


class FakeIngest:
    def __init__(self) -> None:
        self.started: list[int] = []
        self.stopped = 0
        self.room_id = 0
        self.start_result = True

    def is_listening(self) -> bool:
        return self.room_id > 0

    def listener_state(self) -> dict:
        if not self.is_listening():
            return {"state": "disconnected", "room_id": self.room_id, "viewer_count": 0}
        return {"state": "connected", "room_id": self.room_id, "viewer_count": 0}

    async def start_listening(self, room_id: int) -> bool:
        await self.stop_listening()
        self.started.append(room_id)
        if not self.start_result:
            self.room_id = 0
            return False
        self.room_id = room_id
        return True

    async def stop_listening(self) -> None:
        if self.room_id > 0:
            self.stopped += 1
        self.room_id = 0


@pytest.fixture
def runtime(tmp_path: Path) -> RoastRuntime:
    rt = RoastRuntime(Plugin(tmp_path))
    rt.bili_live_ingest = FakeIngest()
    return rt


@pytest.mark.asyncio
async def test_update_config_restarts_listener_when_room_changes(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 100
    runtime.config.live_enabled = True
    await runtime.bili_live_ingest.start_listening(100)

    await runtime.update_config({"live_room_id": 200, "live_enabled": True})

    assert runtime.bili_live_ingest.started == [100, 200]
    assert runtime.bili_live_ingest.stopped == 1
    assert runtime.bili_live_ingest.room_id == 200
    assert runtime.config.live_enabled is True
    assert runtime.live_connection_snapshot()["connected"] is True


@pytest.mark.asyncio
async def test_set_live_room_stops_listener_when_room_switch_fails(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 100
    runtime.config.live_enabled = True
    await runtime.bili_live_ingest.start_listening(100)
    runtime.bili_live_ingest.start_result = False

    await runtime.set_live_room(200)

    assert runtime.bili_live_ingest.stopped == 1
    assert runtime.bili_live_ingest.room_id == 0
    assert runtime.config.live_room_id == 200
    assert runtime.config.live_enabled is False
    assert runtime.live_connection_snapshot()["connected"] is False


@pytest.mark.asyncio
async def test_connect_live_room_rolls_back_live_enabled_when_start_fails(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 123
    runtime.bili_live_ingest.start_result = False

    snapshot = await runtime.connect_live_room()

    assert snapshot["connected"] is False
    assert runtime.config.live_enabled is False
    assert runtime.safety_guard.connected is False


@pytest.mark.asyncio
async def test_connect_live_room_switches_active_room_without_double_start(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 100
    runtime.config.live_enabled = True
    await runtime.bili_live_ingest.start_listening(100)

    snapshot = await runtime.connect_live_room(200)

    assert snapshot["connected"] is True
    assert runtime.config.live_room_id == 200
    assert runtime.bili_live_ingest.started == [100, 200]
    assert runtime.bili_live_ingest.stopped == 1
    assert runtime.bili_live_ingest.room_id == 200


@pytest.mark.asyncio
async def test_disconnect_during_room_update_is_not_undone_by_stale_listener_snapshot(runtime: RoastRuntime) -> None:
    runtime.config.live_room_id = 100
    runtime.config.live_enabled = True
    await runtime.bili_live_ingest.start_listening(100)
    runtime.plugin.config.update_entered = asyncio.Event()
    runtime.plugin.config.resume_update = asyncio.Event()

    update_task = asyncio.create_task(runtime.update_config({"live_room_id": 200}))
    await runtime.plugin.config.update_entered.wait()
    await runtime.disconnect_live_room()
    runtime.plugin.config.resume_update.set()
    _ = await update_task

    assert runtime.config.live_room_id == 200
    assert runtime.config.live_enabled is False
    assert runtime.bili_live_ingest.room_id == 0
    assert runtime.bili_live_ingest.started == [100]
    assert runtime.live_connection_snapshot()["connected"] is False


@pytest.mark.asyncio
async def test_config_fallback_does_not_persist_ephemeral_live_enabled(runtime: RoastRuntime) -> None:
    runtime.plugin.ctx = SimpleNamespace(update_own_config=None)
    runtime.config.live_enabled = True

    await runtime.update_config({"dry_run": False})

    assert runtime.plugin.config.ensure_payloads == [{"neko_roast": {"dry_run": False}}]
    assert runtime.plugin.config.updates == [{"neko_roast": {"dry_run": False}}]


@pytest.mark.asyncio
async def test_douyin_config_update_keeps_live_room_ref(runtime: RoastRuntime) -> None:
    runtime.config.live_platform = "douyin"
    runtime.config.live_room_ref = "room-42"
    runtime.config.live_enabled = True

    await runtime.update_config({"live_room_ref": "room-43", "live_enabled": True})

    assert runtime.live_provider.platform == "douyin"
    assert runtime.live_provider.configured_room_ref() == "room-43"
    assert runtime.config.live_room_ref == "room-43"
