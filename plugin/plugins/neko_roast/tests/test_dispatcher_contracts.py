from types import SimpleNamespace

import pytest

from plugin.plugins.neko_roast.adapters.neko_dispatcher import NekoDispatcher
from plugin.plugins.neko_roast.adapters.output_contract_bridge import (
    metadata_for_request,
    response_module_hint,
)
from plugin.plugins.neko_roast.core.contracts import (
    InteractionRequest,
    RoastConfig,
    ViewerEvent,
    ViewerIdentity,
    ViewerProfile,
)
from plugin.plugins.neko_roast.modules.avatar_roast import AvatarRoastModule
from plugin.plugins.neko_roast.modules.danmaku_response import DanmakuResponseModule

def test_output_contract_bridge_maps_manual_live_simulation_like_live_danmaku():
    avatar_request = InteractionRequest(
        event=ViewerEvent(uid="42", nickname="viewer", source="manual_live_simulation", live_mode="solo_stream"),
        identity=ViewerIdentity(uid="42", nickname="viewer"),
        profile=ViewerProfile(uid="42", nickname="viewer"),
        prompt_text="reply",
        live_mode="solo_stream",
        strength="normal",
        allow_avatar_image=True,
    )
    text_request = InteractionRequest(
        event=ViewerEvent(uid="42", nickname="viewer", source="manual_live_simulation", live_mode="solo_stream"),
        identity=ViewerIdentity(uid="42", nickname="viewer"),
        profile=ViewerProfile(uid="42", nickname="viewer"),
        prompt_text="reply",
        live_mode="solo_stream",
        strength="normal",
        allow_avatar_image=False,
    )

    assert response_module_hint(avatar_request) == "avatar_roast"
    assert metadata_for_request(avatar_request)["max_reply_chars"] == 32
    assert response_module_hint(text_request) == "danmaku_response"
    assert metadata_for_request(text_request)["max_reply_chars"] == 28


def test_output_contract_bridge_carries_trace_id_to_host_metadata():
    request = InteractionRequest(
        event=ViewerEvent(
            uid="42",
            nickname="viewer",
            source="live_danmaku",
            live_mode="solo_stream",
            trace_id="tr_output123",
        ),
        identity=ViewerIdentity(uid="42", nickname="viewer"),
        profile=ViewerProfile(uid="42", nickname="viewer"),
        prompt_text="reply",
        live_mode="solo_stream",
        strength="normal",
        allow_avatar_image=False,
    )

    metadata = metadata_for_request(request)

    assert metadata["trace_id"] == "tr_output123"
    assert metadata["live_event_source"] == "live_danmaku"


def test_output_contract_bridge_carries_unverified_support_claim_to_host_metadata():
    request = InteractionRequest(
        event=ViewerEvent(
            uid="42",
            nickname="viewer",
            danmaku_text="\u6253\u8d4f\u4e86\u8d85\u7ea7\u5927\u706b\u7bad",
            source="live_danmaku",
            live_mode="solo_stream",
        ),
        identity=ViewerIdentity(uid="42", nickname="viewer"),
        profile=ViewerProfile(uid="42", nickname="viewer"),
        prompt_text="reply",
        live_mode="solo_stream",
        strength="normal",
        allow_avatar_image=False,
        metadata={"viewer_claimed_support": "unverified_danmaku_claim"},
    )

    metadata = metadata_for_request(request)

    assert metadata["response_module_hint"] == "danmaku_response"
    assert metadata["viewer_claimed_support"] == "unverified_danmaku_claim"


def test_output_contract_bridge_rejects_object_support_event_type():
    class SpoofSupport:
        def __str__(self):
            return "gift"

    request = InteractionRequest(
        event=ViewerEvent(uid="42", nickname="viewer", source="live_danmaku", live_mode="solo_stream"),
        identity=ViewerIdentity(uid="42", nickname="viewer"),
        profile=ViewerProfile(uid="42", nickname="viewer"),
        prompt_text="reply",
        live_mode="solo_stream",
        strength="normal",
        allow_avatar_image=False,
        metadata={"support_event_type": SpoofSupport()},
    )

    assert response_module_hint(request) == "danmaku_response"
    assert metadata_for_request(request)["response_module_hint"] == "danmaku_response"


@pytest.mark.asyncio
async def test_dispatcher_respects_non_deliverable_request():
    class Plugin:
        def push_message(self, **_kwargs):
            raise AssertionError("non-deliverable requests must not be pushed")

    event = ViewerEvent(uid="1", nickname="tester")
    identity = ViewerIdentity(uid="1", nickname="tester")
    profile = ViewerProfile(uid="1", nickname="tester")
    request = InteractionRequest(
        event=event,
        identity=identity,
        profile=profile,
        prompt_text="nope",
        live_mode="co_stream",
        strength="normal",
        should_push=False,
        reason="upstream skip",
    )

    result = await NekoDispatcher(Plugin()).push_roast(request)

    assert result == "skipped_to_neko(reason=upstream skip)"

def test_avatar_roast_is_the_default_visual_input_owner():
    module = AvatarRoastModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    request = module.build_request(
        ViewerEvent(uid="42", nickname="viewer", danmaku_text="hi", source="live_danmaku", live_mode="solo_stream"),
        ViewerIdentity(uid="42", nickname="viewer", avatar_bytes=b"avatar", avatar_mime="image/png"),
        ViewerProfile(uid="42", nickname="viewer"),
    )

    assert request.allow_avatar_image is True

def test_danmaku_response_is_text_only_even_when_identity_has_avatar():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    request = module.build_request(
        ViewerEvent(uid="42", nickname="viewer", danmaku_text="hi again", source="live_danmaku", live_mode="solo_stream"),
        ViewerIdentity(uid="42", nickname="viewer", avatar_bytes=b"avatar", avatar_mime="image/png"),
        ViewerProfile(uid="42", nickname="viewer", roast_count=1),
    )

    assert request.allow_avatar_image is False

def test_idle_hosting_is_text_only_even_when_identity_has_avatar():
    module = AvatarRoastModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    request = module.build_request(
        ViewerEvent(uid="__neko_idle__", nickname="NEKO", source="idle_hosting", live_mode="solo_stream"),
        ViewerIdentity(uid="__neko_idle__", nickname="NEKO", avatar_bytes=b"avatar", avatar_mime="image/png"),
        ViewerProfile(uid="__neko_idle__", nickname="NEKO"),
    )

    assert request.allow_avatar_image is False

@pytest.mark.asyncio
async def test_dispatcher_does_not_attach_avatar_image_without_visual_opt_in():
    class Plugin:
        def __init__(self):
            self.parts = None

        def push_message(self, **kwargs):
            self.parts = kwargs["parts"]

    plugin = Plugin()
    request = InteractionRequest(
        event=ViewerEvent(uid="42", nickname="viewer", source="live_danmaku", live_mode="solo_stream"),
        identity=ViewerIdentity(uid="42", nickname="viewer", avatar_bytes=b"avatar", avatar_mime="image/png"),
        profile=ViewerProfile(uid="42", nickname="viewer"),
        prompt_text="reply",
        live_mode="solo_stream",
        strength="normal",
        metadata={"danmaku_profile": "short_line"},
    )

    result = await NekoDispatcher(plugin).push_roast(request)

    assert "queued_to_neko(" in result
    assert "image_part_bytes=0" in result
    assert len(plugin.parts) == 1
    assert plugin.parts[0]["type"] == "text"
    assert plugin.parts[0]["text"].startswith("reply\n\nNEKO Live short output contract:")
    assert "target<=14 zh" in plugin.parts[0]["text"]
    assert "hard<=28 zh" in plugin.parts[0]["text"]
    assert "answer current danmaku" in plugin.parts[0]["text"]
    assert "For danmaku_response: answer only the current danmaku" not in plugin.parts[0]["text"]


@pytest.mark.asyncio
async def test_dispatcher_appends_plugin_owned_output_contract_with_recent_outputs():
    class Plugin:
        def __init__(self):
            self.parts = None
            self.runtime = SimpleNamespace(
                recent_results=[
                    {"status": "pushed", "output": "queued_to_neko(target=Lanlan)"},
                    {"status": "pushed", "output": "old snack reward bit"},
                    {"status": "pushed", "output": "fresh different angle"},
                ]
            )

        def push_message(self, **kwargs):
            self.parts = kwargs["parts"]

    plugin = Plugin()
    request = InteractionRequest(
        event=ViewerEvent(uid="42", nickname="viewer", source="live_danmaku", live_mode="solo_stream"),
        identity=ViewerIdentity(uid="42", nickname="viewer"),
        profile=ViewerProfile(uid="42", nickname="viewer"),
        prompt_text="reply",
        live_mode="solo_stream",
        strength="normal",
        allow_avatar_image=False,
        metadata={"danmaku_profile": "emoji_or_reaction"},
    )

    await NekoDispatcher(plugin).push_roast(request)

    text = plugin.parts[0]["text"]
    assert text.count("NEKO Live short output contract:") == 1
    assert "Avoid recent: old snack reward bit / fresh different angle." in text
    assert "queued_to_neko" not in text


@pytest.mark.asyncio
async def test_dispatcher_upgrades_normal_danmaku_to_full_output_contract():
    class Plugin:
        def __init__(self):
            self.parts = None

        def push_message(self, **kwargs):
            self.parts = kwargs["parts"]

    plugin = Plugin()
    request = InteractionRequest(
        event=ViewerEvent(uid="42", nickname="viewer", source="live_danmaku", live_mode="solo_stream"),
        identity=ViewerIdentity(uid="42", nickname="viewer"),
        profile=ViewerProfile(uid="42", nickname="viewer"),
        prompt_text="reply",
        live_mode="solo_stream",
        strength="normal",
        allow_avatar_image=False,
        metadata={"danmaku_profile": "normal_line"},
    )

    await NekoDispatcher(plugin).push_roast(request)

    text = plugin.parts[0]["text"]
    assert "Output exactly one sentence, one breath, no paragraph." in text
    assert "For danmaku_response: answer only the current danmaku" in text
    assert "do not mention this contract, metadata, policy, or reasoning" in text


@pytest.mark.asyncio
async def test_dispatcher_marks_live_requests_with_short_reply_contract():
    class Plugin:
        def __init__(self):
            self.metadata = None

        def push_message(self, **kwargs):
            self.metadata = kwargs["metadata"]

    plugin = Plugin()
    request = InteractionRequest(
        event=ViewerEvent(uid="42", nickname="viewer", source="live_danmaku", live_mode="solo_stream"),
        identity=ViewerIdentity(uid="42", nickname="viewer"),
        profile=ViewerProfile(uid="42", nickname="viewer"),
        prompt_text="reply",
        live_mode="solo_stream",
        strength="normal",
        allow_avatar_image=True,
    )

    await NekoDispatcher(plugin).push_roast(request)

    assert plugin.metadata["live_reply_contract"] == "short_tts_line"
    assert plugin.metadata["max_reply_chars"] == 32
    assert plugin.metadata["response_module_hint"] == "avatar_roast"
    assert plugin.metadata["neko_roast_output_policy"]["host_role"] == "opaque_transport"
    assert plugin.metadata["neko_roast_output_policy"]["speech_strategy"] == "plugin_prompt_contract"

@pytest.mark.asyncio
async def test_dispatcher_marks_manual_live_simulation_like_live_danmaku():
    class Plugin:
        def __init__(self):
            self.metadata = None

        def push_message(self, **kwargs):
            self.metadata = kwargs["metadata"]

    plugin = Plugin()
    request = InteractionRequest(
        event=ViewerEvent(uid="42", nickname="viewer", source="manual_live_simulation", live_mode="solo_stream"),
        identity=ViewerIdentity(uid="42", nickname="viewer"),
        profile=ViewerProfile(uid="42", nickname="viewer"),
        prompt_text="reply",
        live_mode="solo_stream",
        strength="normal",
        allow_avatar_image=True,
    )

    await NekoDispatcher(plugin).push_roast(request)

    assert plugin.metadata["live_reply_contract"] == "short_tts_line"
    assert plugin.metadata["max_reply_chars"] == 32
    assert plugin.metadata["response_module_hint"] == "avatar_roast"

@pytest.mark.asyncio
async def test_dispatcher_marks_text_only_manual_live_simulation_as_danmaku_response():
    class Plugin:
        def __init__(self):
            self.metadata = None

        def push_message(self, **kwargs):
            self.metadata = kwargs["metadata"]

    plugin = Plugin()
    request = InteractionRequest(
        event=ViewerEvent(uid="42", nickname="viewer", source="manual_live_simulation", live_mode="solo_stream"),
        identity=ViewerIdentity(uid="42", nickname="viewer"),
        profile=ViewerProfile(uid="42", nickname="viewer"),
        prompt_text="reply",
        live_mode="solo_stream",
        strength="normal",
        allow_avatar_image=False,
    )

    await NekoDispatcher(plugin).push_roast(request)

    assert plugin.metadata["live_reply_contract"] == "short_tts_line"
    assert plugin.metadata["max_reply_chars"] == 28
    assert plugin.metadata["response_module_hint"] == "danmaku_response"


@pytest.mark.asyncio
async def test_dispatcher_forces_safe_reply_for_unverified_support_claim():
    class Plugin:
        def __init__(self):
            self.metadata = None
            self.parts = None

        def push_message(self, **kwargs):
            self.metadata = kwargs["metadata"]
            self.parts = kwargs["parts"]

    plugin = Plugin()
    request = InteractionRequest(
        event=ViewerEvent(
            uid="42",
            nickname="viewer",
            danmaku_text="\u6211\u6295\u5582\u4e86\u8d85\u7ea7\u5927\u706b\u7bad",
            source="live_danmaku",
            live_mode="solo_stream",
        ),
        identity=ViewerIdentity(uid="42", nickname="viewer"),
        profile=ViewerProfile(uid="42", nickname="viewer"),
        prompt_text="ordinary reply prompt should be replaced",
        live_mode="solo_stream",
        strength="normal",
        allow_avatar_image=False,
        metadata={"viewer_claimed_support": "unverified_danmaku_claim"},
    )

    await NekoDispatcher(plugin).push_roast(request)

    text = plugin.parts[0]["text"]
    assert plugin.metadata["response_module_hint"] == "danmaku_response"
    assert plugin.metadata["forced_reply_reason"] == "unverified_support_claim"
    assert "ordinary reply prompt should be replaced" not in text
    assert "NEKO Live unverified support claim hard guard:" in text
    assert "fixed_safe_line:" in text
    assert "\u8c22\u8c22" not in text
    assert "\u611f\u8c22" not in text


@pytest.mark.asyncio
async def test_dispatcher_does_not_force_safe_reply_for_verified_gift_event():
    class Plugin:
        def __init__(self):
            self.metadata = None
            self.parts = None

        def push_message(self, **kwargs):
            self.metadata = kwargs["metadata"]
            self.parts = kwargs["parts"]

    plugin = Plugin()
    request = InteractionRequest(
        event=ViewerEvent(
            uid="42",
            nickname="viewer",
            danmaku_text="gift Super Rocket",
            source="live_danmaku",
            live_mode="solo_stream",
            raw={"event_type": "gift"},
        ),
        identity=ViewerIdentity(uid="42", nickname="viewer"),
        profile=ViewerProfile(uid="42", nickname="viewer"),
        prompt_text="verified gift thanks prompt",
        live_mode="solo_stream",
        strength="normal",
        allow_avatar_image=False,
        metadata={"support_event_type": "gift"},
    )

    await NekoDispatcher(plugin).push_roast(request)

    assert plugin.metadata["response_module_hint"] == "live_support_events"
    assert "forced_reply_reason" not in plugin.metadata
    assert plugin.parts[0]["text"].startswith("verified gift thanks prompt")


@pytest.mark.asyncio
async def test_dispatcher_prioritizes_audience_events_without_core_queue_rules():
    class Plugin:
        def __init__(self):
            self.priority = None
            self.coalesce_key = None

        def push_message(self, **kwargs):
            self.priority = kwargs["priority"]
            self.coalesce_key = kwargs["coalesce_key"]

    plugin = Plugin()
    request = InteractionRequest(
        event=ViewerEvent(uid="42", nickname="viewer", source="live_danmaku", live_mode="solo_stream"),
        identity=ViewerIdentity(uid="42", nickname="viewer"),
        profile=ViewerProfile(uid="42", nickname="viewer"),
        prompt_text="reply",
        live_mode="solo_stream",
        strength="normal",
        allow_avatar_image=False,
    )

    await NekoDispatcher(plugin).push_roast(request)

    assert plugin.priority == 8
    assert plugin.coalesce_key == ""


@pytest.mark.asyncio
async def test_dispatcher_coalesces_auto_hosting_prompts_in_plugin_scope():
    class Plugin:
        def __init__(self):
            self.priority = None
            self.coalesce_key = None

        def push_message(self, **kwargs):
            self.priority = kwargs["priority"]
            self.coalesce_key = kwargs["coalesce_key"]

    plugin = Plugin()
    request = InteractionRequest(
        event=ViewerEvent(
            uid="__neko_active__",
            nickname="NEKO",
            source="active_engagement",
            live_mode="solo_stream",
            target_lanlan="悠怡",
            raw={"topic_material": {"key": "topic-1"}},
        ),
        identity=ViewerIdentity(uid="__neko_active__", nickname="NEKO"),
        profile=ViewerProfile(uid="__neko_active__", nickname="NEKO"),
        prompt_text="reply",
        live_mode="solo_stream",
        strength="normal",
        allow_avatar_image=False,
    )

    await NekoDispatcher(plugin).push_roast(request)

    assert plugin.priority == 3
    assert plugin.coalesce_key == "neko_roast:auto_host:悠怡:active_engagement:topic-1"


@pytest.mark.asyncio
async def test_dispatcher_passes_danmaku_viewer_nickname_metadata():
    class Plugin:
        def __init__(self):
            self.metadata = None

        def push_message(self, **kwargs):
            self.metadata = kwargs["metadata"]

    plugin = Plugin()
    request = InteractionRequest(
        event=ViewerEvent(uid="42", nickname="方块km", source="live_danmaku", live_mode="solo_stream"),
        identity=ViewerIdentity(uid="42", nickname="方块km"),
        profile=ViewerProfile(uid="42", nickname="方块km"),
        prompt_text="reply",
        live_mode="solo_stream",
        strength="normal",
        allow_avatar_image=False,
        metadata={
            "danmaku_profile": "normal_line",
            "danmaku_viewer_nickname": "方块km",
            "danmaku_anchor_hint": "别怀疑啦",
        },
    )

    await NekoDispatcher(plugin).push_roast(request)

    assert plugin.metadata["response_module_hint"] == "danmaku_response"
    assert plugin.metadata["danmaku_viewer_nickname"] == "方块km"
    assert plugin.metadata["danmaku_anchor_hint"] == "别怀疑啦"


@pytest.mark.asyncio
async def test_dispatcher_allows_expanded_danmaku_reply_for_joke_request():
    class Plugin:
        def __init__(self):
            self.metadata = None
            self.parts = None

        def push_message(self, **kwargs):
            self.metadata = kwargs["metadata"]
            self.parts = kwargs["parts"]

    plugin = Plugin()
    request = InteractionRequest(
        event=ViewerEvent(
            uid="42",
            nickname="viewer",
            danmaku_text="\u732b\u732b\u8bb2\u4e2a\u7b11\u8bdd",
            source="live_danmaku",
            live_mode="solo_stream",
        ),
        identity=ViewerIdentity(uid="42", nickname="viewer"),
        profile=ViewerProfile(uid="42", nickname="viewer", roast_count=2),
        prompt_text="reply",
        live_mode="solo_stream",
        strength="normal",
        allow_avatar_image=False,
    )

    await NekoDispatcher(plugin).push_roast(request)

    assert plugin.metadata["response_module_hint"] == "danmaku_response"
    assert plugin.metadata["reply_length_mode"] == "expanded"
    assert plugin.metadata["max_reply_chars"] == 56
    assert plugin.metadata["neko_roast_output_policy"]["max_reply_chars"] == 56
    assert "Expanded viewer requests may use up to two short sentences" in plugin.parts[0]["text"]
    assert "the line itself must contain the requested joke" in plugin.parts[0]["text"]
    assert "For danmaku_response: answer only the current danmaku" in plugin.parts[0]["text"]


@pytest.mark.asyncio
async def test_dispatcher_allows_expanded_danmaku_reply_for_casual_content_request():
    class Plugin:
        def __init__(self):
            self.metadata = None

        def push_message(self, **kwargs):
            self.metadata = kwargs["metadata"]

    plugin = Plugin()
    request = InteractionRequest(
        event=ViewerEvent(
            uid="42",
            nickname="viewer",
            danmaku_text="\u6765\u4e00\u4e2a\u7b11\u8bdd",
            source="live_danmaku",
            live_mode="solo_stream",
        ),
        identity=ViewerIdentity(uid="42", nickname="viewer"),
        profile=ViewerProfile(uid="42", nickname="viewer", roast_count=2),
        prompt_text="reply",
        live_mode="solo_stream",
        strength="normal",
        allow_avatar_image=False,
    )

    await NekoDispatcher(plugin).push_roast(request)

    assert plugin.metadata["response_module_hint"] == "danmaku_response"
    assert plugin.metadata["reply_length_mode"] == "expanded"
    assert plugin.metadata["max_reply_chars"] == 56
    assert plugin.metadata["neko_roast_output_policy"]["max_reply_chars"] == 56


@pytest.mark.asyncio
async def test_dispatcher_carries_room_bridge_danmaku_reply_contract():
    class Plugin:
        def __init__(self):
            self.metadata = None

        def push_message(self, **kwargs):
            self.metadata = kwargs["metadata"]

    plugin = Plugin()
    request = InteractionRequest(
        event=ViewerEvent(
            uid="42",
            nickname="viewer",
            danmaku_text="我还是想喝热饮，今天有点冷",
            source="live_danmaku",
            live_mode="solo_stream",
        ),
        identity=ViewerIdentity(uid="42", nickname="viewer"),
        profile=ViewerProfile(uid="42", nickname="viewer", roast_count=2),
        prompt_text="reply",
        live_mode="solo_stream",
        strength="normal",
        allow_avatar_image=False,
        metadata={
            "danmaku_profile": "normal_line",
            "danmaku_reply_target": "current_danmaku_meaning",
            "danmaku_reply_shape": "one_compact_reply",
            "danmaku_anchor_hint": "我还是想",
            "reply_length_mode": "room_bridge",
            "max_reply_chars": 48,
            "room_theme": "choice / preference prompt",
        },
    )

    await NekoDispatcher(plugin).push_roast(request)

    assert plugin.metadata["response_module_hint"] == "danmaku_response"
    assert plugin.metadata["reply_length_mode"] == "room_bridge"
    assert plugin.metadata["max_reply_chars"] == 48
    assert plugin.metadata["neko_roast_output_policy"]["max_reply_chars"] == 48
    assert plugin.metadata["danmaku_profile"] == "normal_line"
    assert plugin.metadata["room_theme"] == "choice / preference prompt"
    assert plugin.metadata["danmaku_anchor_hint"] == "我还是想"


@pytest.mark.asyncio
async def test_dispatcher_rejects_object_reply_length_mode():
    class SpoofMode:
        def __str__(self):
            return "room_bridge"

    class Plugin:
        def __init__(self):
            self.metadata = None

        def push_message(self, **kwargs):
            self.metadata = kwargs["metadata"]

    plugin = Plugin()
    request = InteractionRequest(
        event=ViewerEvent(
            uid="42",
            nickname="viewer",
            danmaku_text="我还是想喝热饮，今天有点冷",
            source="live_danmaku",
            live_mode="solo_stream",
        ),
        identity=ViewerIdentity(uid="42", nickname="viewer"),
        profile=ViewerProfile(uid="42", nickname="viewer", roast_count=2),
        prompt_text="reply",
        live_mode="solo_stream",
        strength="normal",
        allow_avatar_image=False,
        metadata={"reply_length_mode": SpoofMode(), "max_reply_chars": 48},
    )

    await NekoDispatcher(plugin).push_roast(request)

    assert plugin.metadata["response_module_hint"] == "danmaku_response"
    assert plugin.metadata["max_reply_chars"] == 28
    assert "reply_length_mode" not in plugin.metadata


@pytest.mark.asyncio
async def test_dispatcher_allows_longer_host_reply_contracts():
    class Plugin:
        def __init__(self):
            self.metadata = None

        def push_message(self, **kwargs):
            self.metadata = kwargs["metadata"]

    plugin = Plugin()
    request = InteractionRequest(
        event=ViewerEvent(uid="__neko_active__", nickname="NEKO", source="active_engagement", live_mode="solo_stream"),
        identity=ViewerIdentity(uid="__neko_active__", nickname="NEKO"),
        profile=ViewerProfile(uid="__neko_active__", nickname="NEKO"),
        prompt_text="reply",
        live_mode="solo_stream",
        strength="normal",
    )

    await NekoDispatcher(plugin).push_roast(request)

    assert plugin.metadata["live_reply_contract"] == "short_tts_line"
    assert plugin.metadata["max_reply_chars"] == 72
    assert plugin.metadata["response_module_hint"] == "active_engagement"
    assert plugin.metadata["neko_roast_output_policy"]["owner"] == "neko_roast"

@pytest.mark.asyncio
async def test_dispatcher_dry_run_summary_includes_short_reply_contract():
    class Plugin:
        def push_message(self, **_kwargs):
            raise AssertionError("dry_run requests must not be pushed")

    request = InteractionRequest(
        event=ViewerEvent(uid="42", nickname="viewer", source="live_danmaku", live_mode="solo_stream"),
        identity=ViewerIdentity(uid="42", nickname="viewer"),
        profile=ViewerProfile(uid="42", nickname="viewer"),
        prompt_text="reply",
        live_mode="solo_stream",
        strength="normal",
        dry_run=True,
    )

    result = await NekoDispatcher(Plugin()).push_roast(request)

    assert "reply_contract=short_tts_line" in result
    assert "max_reply_chars=28" in result
    assert "response_module_hint=danmaku_response" in result

@pytest.mark.asyncio
async def test_dispatcher_attaches_avatar_image_for_visual_opt_in():
    class Plugin:
        def __init__(self):
            self.parts = None

        def push_message(self, **kwargs):
            self.parts = kwargs["parts"]

    plugin = Plugin()
    request = InteractionRequest(
        event=ViewerEvent(uid="42", nickname="viewer", source="live_danmaku", live_mode="solo_stream"),
        identity=ViewerIdentity(uid="42", nickname="viewer", avatar_bytes=b"avatar", avatar_mime="image/png"),
        profile=ViewerProfile(uid="42", nickname="viewer"),
        prompt_text="reply",
        live_mode="solo_stream",
        strength="normal",
        allow_avatar_image=True,
    )

    result = await NekoDispatcher(plugin).push_roast(request)

    assert "queued_to_neko(" in result
    assert "image_part_bytes=6" in result
    assert len(plugin.parts) == 2
    assert plugin.parts[0]["type"] == "text"
    assert plugin.parts[0]["text"].startswith("reply\n\nNEKO Live short output contract:")
    assert "For avatar_roast: connect the viewer's first message" in plugin.parts[0]["text"]
    assert plugin.parts[1] == {"type": "image", "data": b"avatar", "mime": "image/png"}
