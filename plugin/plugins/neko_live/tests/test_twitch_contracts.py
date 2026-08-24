from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from plugin.plugins.neko_live.core.contracts import LiveRoomStatus, ViewerIdentity
from plugin.plugins.neko_live.core.contracts_config import LiveConfig, normalize_live_platform
from plugin.plugins.neko_live.core.live_provider_router import LiveProviderRouter
from plugin.plugins.neko_live.core.runtime_modules import assemble_runtime_modules, registered_modules
from plugin.plugins.neko_live.modules.twitch_live_ingest.room_ref import parse_twitch_room_ref


class _Ingest:
    def __init__(self) -> None:
        self.started: list[Any] = []
        self.lookups: list[Any] = []

    async def start_listening(self, room_ref: str) -> bool:
        self.started.append(room_ref)
        return True

    async def lookup_room_status(self, room_ref: str) -> LiveRoomStatus:
        self.lookups.append(room_ref)
        return LiveRoomStatus(room_id=0, ok=True, title=room_ref)

    def is_listening(self) -> bool:
        return False

    def status(self) -> dict[str, Any]:
        return {}


class _Identity:
    async def resolve(self, event: Any) -> ViewerIdentity:
        return ViewerIdentity(uid=f"twitch:{event.uid}", nickname=event.nickname)


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(
            live_platform="twitch",
            live_room_ref="Streamer_Name",
            live_room_id=123,
        ),
        bili_live_ingest=_Ingest(),
        douyin_live_ingest=_Ingest(),
        twitch_live_ingest=_Ingest(),
        bili_identity=_Identity(),
        douyin_identity=_Identity(),
        twitch_identity=_Identity(),
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Streamer_Name", "streamer_name"),
        ("https://www.twitch.tv/Streamer_Name", "streamer_name"),
        ("twitch.tv/streamer_name", "streamer_name"),
    ],
)
def test_parse_twitch_room_ref_accepts_login_and_channel_url(value: str, expected: str) -> None:
    parsed = parse_twitch_room_ref(value)

    assert parsed.ok is True
    assert parsed.room_ref == expected


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/streamer_name",
        "https://www.twitch.tv/streamer_name/videos",
        "https://www.twitch.tv/streamer_name?token=must-not-leak",
        True,
        object(),
    ],
)
def test_parse_twitch_room_ref_rejects_unsafe_or_invalid_values(value: object) -> None:
    parsed = parse_twitch_room_ref(value)

    assert parsed.ok is False
    assert parsed.room_ref == ""
    assert "must-not-leak" not in parsed.message


def test_twitch_platform_aliases_and_config_round_trip() -> None:
    assert normalize_live_platform("twitch") == "twitch"
    assert normalize_live_platform("TV") == "twitch"

    config = LiveConfig.from_mapping(
        {
            "live_platform": "tv",
            "live_room_ref": "Streamer_Name",
            "live_room_id": 999,
            "twitch_client_id": "client-id-123",
        }
    )

    assert config.live_platform == "twitch"
    assert config.live_room_ref == "Streamer_Name"
    assert config.live_room_id == 0
    assert config.twitch_client_id == "client-id-123"
    assert config.to_dict()["twitch_client_id"] == "client-id-123"
    assert config.to_public_dict()["twitch_client_id"] == "client-id-123"


@pytest.mark.asyncio
async def test_twitch_router_normalizes_channel_and_selects_provider_and_identity() -> None:
    runtime = _runtime()
    router = LiveProviderRouter(runtime)

    assert router.platform == "twitch"
    assert router.configured_room_ref() == "streamer_name"
    assert router.configured_room_id() == 0

    assert await router.start_listening("https://www.twitch.tv/Other_Channel") is True
    status = await router.lookup_room_status("OTHER_CHANNEL")
    identity = await router.resolve_identity(SimpleNamespace(uid="42", nickname="viewer"))

    assert runtime.twitch_live_ingest.started == ["other_channel"]
    assert runtime.twitch_live_ingest.lookups == ["other_channel"]
    assert status.ok is True
    assert status.title == "other_channel"
    assert identity.uid == "twitch:42"
    assert router.identity_step_id() == "twitch_identity"


def test_runtime_registers_twitch_modules() -> None:
    runtime = SimpleNamespace()

    assemble_runtime_modules(runtime)

    module_ids = [module.id for module in registered_modules(runtime)]
    assert "twitch_live_ingest" in module_ids
    assert "twitch_identity" in module_ids
    assert runtime.twitch_live_ingest.id == "twitch_live_ingest"
    assert runtime.twitch_identity.id == "twitch_identity"
