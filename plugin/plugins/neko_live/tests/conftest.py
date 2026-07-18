from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from plugin.plugins.neko_live.core.runtime import RoastRuntime


class ConfigApi:
    def __init__(self) -> None:
        self.updates: list[dict] = []
        self.ensure_payloads: list[dict] = []
        self.update_entered: asyncio.Event | None = None
        self.resume_update: asyncio.Event | None = None

    async def dump(self, timeout: float = 0) -> dict:
        return {"neko_live": {}}

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
        self.pushed_messages: list[dict] = []
        self.output_channel_ready = True

    def data_path(self) -> Path:
        return self._data_path

    def push_message(self, **kwargs):
        self.pushed_messages.append(kwargs)
        return None


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
    rt.avatar_roast.ctx = rt
    rt.live_support_events.ctx = rt
    rt.active_engagement.ctx = rt
    rt.warmup_hosting.ctx = rt
    rt.bili_identity.ctx = rt
    rt.viewer_profile.ctx = rt
    return rt
