from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from plugin.plugins.neko_live.modules.twitch_identity import TwitchIdentityModule
from plugin.plugins.neko_live.modules.twitch_live_ingest import TwitchLiveIngestModule
from plugin.plugins.neko_live.modules.twitch_live_ingest.helix import lookup_channel_status
from plugin.plugins.neko_live.modules.twitch_live_ingest import projection as twitch_projection
from plugin.plugins.neko_live.modules.twitch_live_ingest.projection import project_chat_message
from plugin.plugins.neko_live.modules.live_events.provider_event import event_support_fields


class _AsyncItems:
    def __init__(self, items: list[Any]) -> None:
        self.items = items

    def __aiter__(self):
        async def iterate():
            for item in self.items:
                yield item

        return iterate()


class _HelixClient:
    def __init__(self, *, users: list[Any], streams: list[Any]) -> None:
        self.users = users
        self.streams = streams
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def fetch_users(self, **kwargs: Any) -> list[Any]:
        self.calls.append(("users", kwargs))
        return self.users

    def fetch_streams(self, **kwargs: Any) -> _AsyncItems:
        self.calls.append(("streams", kwargs))
        return _AsyncItems(self.streams)


@pytest.mark.asyncio
async def test_helix_lookup_returns_live_channel_metadata_with_user_token_owner() -> None:
    client = _HelixClient(
        users=[SimpleNamespace(id="100", name="target_channel", display_name="Target Channel")],
        streams=[SimpleNamespace(title="Building a tiny robot", user_name="Target Channel", type="live")],
    )

    status = await lookup_channel_status(client, "TARGET_CHANNEL", token_for="42")

    assert status.ok is True
    assert status.room_id == 100
    assert status.title == "Building a tiny robot"
    assert status.anchor_name == "Target Channel"
    assert status.live_status == "live"
    assert client.calls == [
        ("users", {"logins": ["target_channel"], "token_for": "42"}),
        ("streams", {"user_ids": ["100"], "type": "live", "token_for": "42", "max_results": 1}),
    ]


@pytest.mark.asyncio
async def test_helix_lookup_distinguishes_offline_and_missing_channels() -> None:
    offline = _HelixClient(
        users=[SimpleNamespace(id="100", name="target_channel", display_name="Target Channel")],
        streams=[],
    )
    missing = _HelixClient(users=[], streams=[])

    offline_status = await lookup_channel_status(offline, "target_channel", token_for="42")
    missing_status = await lookup_channel_status(missing, "missing_channel", token_for="42")

    assert offline_status.ok is True
    assert offline_status.live_status == "offline"
    assert offline_status.anchor_name == "Target Channel"
    assert missing_status.ok is False
    assert missing_status.live_status == "unknown"
    assert missing_status.message == "twitch channel was not found"


def test_chat_message_projection_contains_only_pipeline_fields() -> None:
    message = SimpleNamespace(
        id="message-1",
        text="hello NEKO",
        chatter=SimpleNamespace(id="200", name="viewer_login", display_name="Viewer Name"),
        broadcaster=SimpleNamespace(id="100", name="target_channel", display_name="Target Channel"),
        access_token="must-not-leak",
    )

    event = project_chat_message(message, room_ref="target_channel", ts=123.5)

    assert event.type == "danmaku"
    assert event.uid == "twitch:200"
    assert event.source == "live"
    assert event.ts == 123.5
    assert event.raw is None
    assert event.payload == {
        "event_type": "danmaku",
        "uid": "twitch:200",
        "nickname": "Viewer Name",
        "chatter_login": "viewer_login",
        "danmaku_text": "hello NEKO",
        "text": "hello NEKO",
        "message_id": "message-1",
        "room_ref": "target_channel",
    }
    assert "must-not-leak" not in str(event.to_dict())


def test_cheer_chat_message_projects_to_verified_gift_event() -> None:
    message = SimpleNamespace(
        id="message-cheer-1",
        text="cheer100 NEKO kawaii",
        chatter=SimpleNamespace(id="200", name="viewer_login", display_name="Viewer Name"),
        cheer=SimpleNamespace(bits=100),
    )

    event = project_chat_message(message, room_ref="target_channel", ts=123.5)

    assert event is not None
    assert event.type == "gift"
    assert event.uid == "twitch:200"
    assert event.raw is None
    assert event.payload == {
        "event_type": "gift",
        "uid": "twitch:200",
        "nickname": "Viewer Name",
        "chatter_login": "viewer_login",
        "danmaku_text": "cheer100 NEKO kawaii",
        "text": "cheer100 NEKO kawaii",
        "message_id": "message-cheer-1",
        "room_ref": "target_channel",
        "gift_name": "Twitch Bits",
        "gift_count": 1,
        "gift_value": 100,
        "coin_type": "gold",
        "support_verified": True,
        "support_evidence": "twitch_eventsub_typed_event",
        "provider_event_id": "message-cheer-1",
        "provider_event_type": "TWITCH_CHEER",
    }
    assert event_support_fields(event) == {
        "support_verified": True,
        "support_evidence": "twitch_eventsub_typed_event",
        "provider_event_id": "message-cheer-1",
        "provider_event_type": "TWITCH_CHEER",
        "coin_type": "gold",
    }


def test_cheer_without_provider_message_id_is_rejected() -> None:
    message = SimpleNamespace(
        id="",
        text="cheer100 NEKO kawaii",
        chatter=SimpleNamespace(id="200", name="viewer_login", display_name="Viewer Name"),
        cheer=SimpleNamespace(bits=100),
    )

    assert project_chat_message(message, room_ref="target_channel") is None


def test_chat_subscription_notification_projects_to_verified_gift_event() -> None:
    projector = getattr(twitch_projection, "project_chat_notification", None)
    assert callable(projector)
    notice = SimpleNamespace(
        id="notice-sub-1",
        notice_type="sub",
        anonymous=False,
        chatter=SimpleNamespace(id="201", name="subscriber", display_name="Subscriber"),
        text="",
        system_message="Subscriber subscribed at Tier 2!",
        sub=SimpleNamespace(tier="2000", months=1, prime=False),
    )

    event = projector(notice, room_ref="target_channel", ts=124.0)

    assert event is not None
    assert event.type == "gift"
    assert event.uid == "twitch:201"
    assert event.payload["gift_name"] == "Twitch Tier 2 subscription"
    assert event.payload["gift_count"] == 1
    assert event.payload["provider_event_type"] == "TWITCH_SUB"
    assert event.payload["support_evidence"] == "twitch_eventsub_typed_event"


def test_chat_community_gift_notification_preserves_count_and_anonymity() -> None:
    projector = getattr(twitch_projection, "project_chat_notification", None)
    assert callable(projector)
    notice = SimpleNamespace(
        id="notice-community-gift-1",
        notice_type="community_sub_gift",
        anonymous=True,
        chatter=SimpleNamespace(id="999", name="anonymous", display_name="Anonymous"),
        text="",
        system_message="An anonymous user gifted 5 Tier 1 subscriptions!",
        community_sub_gift=SimpleNamespace(tier="1000", total=5, id="community-1"),
    )

    event = projector(notice, room_ref="target_channel", ts=125.0)

    assert event is not None
    assert event.uid == "twitch:anonymous"
    assert event.payload["nickname"] == "Anonymous"
    assert event.payload["gift_name"] == "Twitch Tier 1 gift subscriptions"
    assert event.payload["gift_count"] == 5
    assert event.payload["provider_event_id"] == "notice-community-gift-1"
    assert event.payload["provider_event_type"] == "TWITCH_COMMUNITY_SUB_GIFT"


@pytest.mark.parametrize(
    ("notice_type", "detail_name", "detail", "gift_name", "provider_event_type"),
    [
        (
            "resub",
            "resub",
            SimpleNamespace(tier="3000", months=1, cumulative_months=15),
            "Twitch Tier 3 resubscription",
            "TWITCH_RESUB",
        ),
        (
            "sub_gift",
            "sub_gift",
            SimpleNamespace(tier="1000", months=1, community_gift_id=None),
            "Twitch Tier 1 gift subscription",
            "TWITCH_SUB_GIFT",
        ),
    ],
)
def test_chat_resub_and_standalone_gift_notifications_project_as_support(
    notice_type: str,
    detail_name: str,
    detail: SimpleNamespace,
    gift_name: str,
    provider_event_type: str,
) -> None:
    projector = getattr(twitch_projection, "project_chat_notification", None)
    assert callable(projector)
    notice = SimpleNamespace(
        id=f"notice-{notice_type}-1",
        notice_type=notice_type,
        anonymous=False,
        chatter=SimpleNamespace(id="203", name="supporter", display_name="Supporter"),
        text="Still loving the stream",
        system_message="Supporter renewed their support.",
        **{detail_name: detail},
    )

    event = projector(notice, room_ref="target_channel", ts=126.0)

    assert event is not None
    assert event.payload["gift_name"] == gift_name
    assert event.payload["provider_event_type"] == provider_event_type
    assert event.payload["gift_count"] == 1
    assert event.payload["text"] == "Still loving the stream"


def test_chat_notification_ignores_community_gift_child_and_non_support_notice() -> None:
    projector = getattr(twitch_projection, "project_chat_notification", None)
    assert callable(projector)
    chatter = SimpleNamespace(id="202", name="gifter", display_name="Gifter")
    community_child = SimpleNamespace(
        id="notice-child-1",
        notice_type="sub_gift",
        anonymous=False,
        chatter=chatter,
        text="",
        system_message="Gifter gifted a subscription.",
        sub_gift=SimpleNamespace(tier="1000", months=1, community_gift_id="community-1"),
    )
    raid = SimpleNamespace(
        id="notice-raid-1",
        notice_type="raid",
        anonymous=False,
        chatter=chatter,
        text="",
        system_message="Incoming raid.",
    )

    assert projector(community_child, room_ref="target_channel") is None
    assert projector(raid, room_ref="target_channel") is None


@pytest.mark.asyncio
async def test_module_normalize_and_identity_keep_twitch_uid_namespace() -> None:
    module = TwitchLiveIngestModule()
    identity_module = TwitchIdentityModule()

    viewer = module.normalize(
        {
            "uid": "twitch:200",
            "nickname": "Viewer Name",
            "chatter_login": "viewer_login",
            "danmaku_text": "hello NEKO",
            "event_type": "danmaku",
        }
    )
    identity = await identity_module.resolve(viewer)

    assert viewer.uid == "twitch:200"
    assert viewer.source == "live_danmaku"
    assert viewer.danmaku_text == "hello NEKO"
    assert identity.uid == "twitch:200"
    assert identity.source_url == "https://www.twitch.tv/viewer_login"


def test_module_normalize_preserves_only_public_verified_support_fields() -> None:
    module = TwitchLiveIngestModule()

    viewer = module.normalize(
        {
            "event_type": "gift",
            "uid": "twitch:200",
            "nickname": "Viewer Name",
            "chatter_login": "viewer_login",
            "danmaku_text": "cheer100",
            "room_ref": "target_channel",
            "gift_name": "Twitch Bits",
            "gift_count": 1,
            "gift_value": 100,
            "coin_type": "gold",
            "support_verified": True,
            "support_evidence": "twitch_eventsub_typed_event",
            "provider_event_id": "message-cheer-1",
            "provider_event_type": "TWITCH_CHEER",
            "access_token": "must-not-leak",
        }
    )

    assert viewer.raw == {
        "event_type": "gift",
        "chatter_login": "viewer_login",
        "room_ref": "target_channel",
        "gift_name": "Twitch Bits",
        "gift_count": 1,
        "gift_value": 100,
        "coin_type": "gold",
        "support_verified": True,
        "support_evidence": "twitch_eventsub_typed_event",
        "provider_event_id": "message-cheer-1",
        "provider_event_type": "TWITCH_CHEER",
    }
    assert "must-not-leak" not in str(viewer.raw)
