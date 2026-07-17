from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from plugin.plugins.neko_roast.core.contracts import (
    LiveRoomStatus,
    ViewerEvent,
    ViewerIdentity,
    ViewerProfile,
)
from plugin.plugins.neko_roast.core.live_provider_router import LiveProviderRouter
from plugin.plugins.neko_roast.core.pipeline_viewers import resolve_viewer_context


class _Ingest:
    def __init__(self) -> None:
        self.started: list[Any] = []
        self.lookups: list[Any] = []
        self.stopped = 0
        self.listening = False

    def is_listening(self) -> bool:
        return self.listening

    def listener_state(self) -> dict[str, Any]:
        return {"state": "connected", "viewer_count": 12}

    async def start_listening(self, room_ref: Any) -> bool:
        self.started.append(room_ref)
        self.listening = True
        return True

    async def stop_listening(self) -> None:
        self.stopped += 1
        self.listening = False

    def normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"normalized": payload}

    async def lookup_room_status(self, room_ref: Any) -> LiveRoomStatus:
        self.lookups.append(room_ref)
        return LiveRoomStatus(room_id=int(room_ref) if isinstance(room_ref, int) else 0, ok=True)

    def status(self) -> dict[str, Any]:
        return {"last_event_type": "danmaku"}


class _Identity:
    async def resolve(self, event: Any) -> ViewerIdentity:
        return ViewerIdentity(uid=f"resolved:{event.uid}", nickname=event.nickname)


def _runtime(platform: Any, *, live_room_ref: Any = "", live_room_id: Any = 0) -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(
            live_platform=platform,
            live_room_ref=live_room_ref,
            live_room_id=live_room_id,
        ),
        bili_live_ingest=_Ingest(),
        douyin_live_ingest=_Ingest(),
        bili_identity=_Identity(),
        douyin_identity=_Identity(),
    )


@pytest.mark.asyncio
async def test_bilibili_router_passes_numeric_room_id_to_provider() -> None:
    runtime = _runtime("bili", live_room_id=123)
    router = LiveProviderRouter(runtime)

    assert router.platform == "bilibili"
    assert router.configured_room_ref() == "123"
    assert router.configured_room_id() == 123

    started = await router.start_listening("https://live.bilibili.com/456?from=test")
    status = await router.lookup_room_status("https://live.bilibili.com/789")

    assert started is True
    assert runtime.bili_live_ingest.started == [456]
    assert runtime.douyin_live_ingest.started == []
    assert runtime.bili_live_ingest.lookups == [789]
    assert status.room_id == 789


@pytest.mark.asyncio
async def test_douyin_router_passes_room_ref_to_provider() -> None:
    runtime = _runtime("dy", live_room_ref="https://live.douyin.com/room-42", live_room_id=12345)
    router = LiveProviderRouter(runtime)

    assert router.platform == "douyin"
    assert router.configured_room_ref() == "room-42"
    assert router.configured_room_id() == 0

    started = await router.start_listening("https://live.douyin.com/room-42?foo=bar")
    status = await router.lookup_room_status("https://live.douyin.com/room-43")

    assert started is True
    assert runtime.douyin_live_ingest.started == ["room-42"]
    assert runtime.bili_live_ingest.started == []
    assert runtime.douyin_live_ingest.lookups == ["room-43"]
    assert status.ok is True


def test_douyin_router_configured_room_ref_is_public_projection() -> None:
    runtime = _runtime("douyin", live_room_ref="https://live.douyin.com/room-42?cookie=must-not-leak")
    router = LiveProviderRouter(runtime)

    assert router.configured_room_ref() == "room-42"
    assert router.status()["room_ref"] == "room-42"


def test_douyin_router_configured_room_ref_does_not_stringify_objects() -> None:
    class _LooksLikeRoomRef:
        def __str__(self) -> str:
            return "room-42"

    runtime = _runtime("douyin", live_room_ref=_LooksLikeRoomRef())
    router = LiveProviderRouter(runtime)

    assert router.configured_room_ref() == ""
    assert router.status()["room_ref"] == ""


def test_douyin_router_does_not_derive_room_ref_from_legacy_bilibili_room_id() -> None:
    runtime = _runtime("douyin", live_room_ref="", live_room_id=12345)
    router = LiveProviderRouter(runtime)

    assert router.configured_room_ref() == ""
    assert router.configured_room_id() == 0
    assert router.status()["room_ref"] == ""
    assert router.status()["room_id"] == 0


def test_bilibili_router_configured_room_ref_and_id_do_not_stringify_objects() -> None:
    class _LooksLikeRoomRef:
        def __str__(self) -> str:
            return "https://live.bilibili.com/123"

    class _LooksLikeRoomId:
        def __int__(self) -> int:
            return 456

    runtime = _runtime("bilibili", live_room_ref=_LooksLikeRoomRef(), live_room_id=_LooksLikeRoomId())
    router = LiveProviderRouter(runtime)

    assert router.configured_room_ref() == ""
    assert router.configured_room_id() == 0
    assert router.status()["room_ref"] == ""
    assert router.status()["room_id"] == 0


@pytest.mark.asyncio
async def test_router_resolves_identity_via_selected_provider() -> None:
    runtime = _runtime("douyin")
    router = LiveProviderRouter(runtime)
    event = SimpleNamespace(uid="42", nickname="viewer")

    identity = await router.resolve_identity(event)

    assert identity.uid == "resolved:42"
    assert identity.nickname == "viewer"
    assert router.identity_step_id() == "douyin_identity"


@pytest.mark.asyncio
async def test_pipeline_viewer_context_uses_selected_live_provider_identity() -> None:
    runtime = _runtime("douyin")
    runtime.live_provider = LiveProviderRouter(runtime)
    event = ViewerEvent(uid="42", nickname="viewer", source="live_danmaku")
    steps = []

    viewer = await resolve_viewer_context(runtime, event, steps, is_transient_event=True)

    assert viewer.identity.uid == "resolved:42"
    assert viewer.identity.nickname == "viewer"
    assert viewer.profile.uid == "resolved:42"
    assert steps[0].id == "douyin_identity"
    assert steps[1].id == "viewer_profile"


@pytest.mark.asyncio
async def test_live_danmaku_records_profile_once_without_redundant_upsert() -> None:
    class _Profiles:
        def __init__(self) -> None:
            self.upsert_calls = 0
            self.record_calls = 0

        async def upsert(self, identity: ViewerIdentity) -> ViewerProfile:
            self.upsert_calls += 1
            return ViewerProfile(uid=identity.uid, nickname=identity.nickname)

        async def record_live_danmaku(
            self,
            identity: ViewerIdentity,
            _danmaku_text: str,
        ) -> ViewerProfile:
            self.record_calls += 1
            return ViewerProfile(
                uid=identity.uid,
                nickname=identity.nickname,
                danmaku_count=1,
            )

    runtime = _runtime("douyin")
    runtime.live_provider = LiveProviderRouter(runtime)
    runtime.viewer_profile = _Profiles()
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="hello",
        source="live_danmaku",
    )

    viewer = await resolve_viewer_context(
        runtime,
        event,
        [],
        is_transient_event=False,
    )

    assert runtime.viewer_profile.record_calls == 1
    assert runtime.viewer_profile.upsert_calls == 0
    assert viewer.profile.danmaku_count == 1


def test_router_status_defaults_and_listener_state_are_isolated() -> None:
    class BadIngest:
        def listener_state(self) -> dict[str, Any]:
            raise RuntimeError("boom")

        def status(self) -> dict[str, Any]:
            raise RuntimeError("boom")

    runtime = _runtime("douyin", live_room_ref="room-42")
    runtime.douyin_live_ingest = BadIngest()
    router = LiveProviderRouter(runtime)

    assert router.listener_state() == {}
    assert router.status() == {
        "platform": "douyin",
        "room_ref": "room-42",
        "room_id": 0,
        "listening": False,
    }


def test_router_status_public_projection_rejects_provider_objects() -> None:
    class _LooksLikeText:
        def __str__(self) -> str:
            return "room-evil?cookie=must-not-leak"

    class _LooksLikeRoomId:
        def __int__(self) -> int:
            return 999

    class LooseStatusIngest(_Ingest):
        def status(self) -> dict[str, Any]:
            return {
                "platform": _LooksLikeText(),
                "room_ref": _LooksLikeText(),
                "room_id": _LooksLikeRoomId(),
                "listening": True,
            }

    runtime = _runtime("douyin", live_room_ref="room-42")
    runtime.douyin_live_ingest = LooseStatusIngest()
    router = LiveProviderRouter(runtime)
    status = router.status()

    assert status["platform"] == "douyin"
    assert status["room_ref"] == "room-42"
    assert status["room_id"] == 0
    assert status["listening"] is True
    assert "must-not-leak" not in json.dumps(status, ensure_ascii=False)


@pytest.mark.asyncio
async def test_router_listening_and_start_results_require_exact_bool() -> None:
    class _TruthyResult:
        def __bool__(self) -> bool:
            return True

    class LooseIngest(_Ingest):
        def is_listening(self) -> object:
            return _TruthyResult()

        async def start_listening(self, room_ref: Any) -> object:
            self.started.append(room_ref)
            return _TruthyResult()

        def status(self) -> dict[str, Any]:
            return {"listening": _TruthyResult()}

    runtime = _runtime("douyin", live_room_ref="room-42")
    runtime.douyin_live_ingest = LooseIngest()
    router = LiveProviderRouter(runtime)

    assert router.is_listening() is False
    assert router.status()["listening"] is False
    assert await router.start_listening("room-42") is False
    assert runtime.douyin_live_ingest.started == ["room-42"]
