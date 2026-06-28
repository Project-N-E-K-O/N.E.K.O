import asyncio
from types import SimpleNamespace

import pytest

from plugin.plugins.neko_roast.adapters.bili_auth_service import BiliAuthService
from plugin.plugins.neko_roast.adapters.neko_dispatcher import NekoDispatcher
from plugin.plugins.neko_roast.core.contracts import (
    InteractionRequest,
    InteractionResult,
    RoastConfig,
    SafetyDecision,
    ViewerEvent,
    ViewerIdentity,
    ViewerProfile,
    utc_now_iso,
)
from plugin.plugins.neko_roast.core.module_registry import ModuleRegistry
from plugin.plugins.neko_roast.core.permission_gate import PermissionGate
from plugin.plugins.neko_roast.core.pipeline import RoastPipeline
from plugin.plugins.neko_roast.modules.active_engagement import ActiveEngagementModule
from plugin.plugins.neko_roast.modules.avatar_roast import AvatarRoastModule
from plugin.plugins.neko_roast.modules.bili_identity import BiliIdentityModule
from plugin.plugins.neko_roast.modules.danmaku_response import DanmakuResponseModule
from plugin.plugins.neko_roast.modules.warmup_hosting import WarmupHostingModule


def test_roast_config_defaults_to_dry_run_for_real_room_safety():
    assert RoastConfig().dry_run is True
    assert RoastConfig.from_mapping({}).dry_run is True
    assert RoastConfig.from_mapping(None).dry_run is True


def test_roast_config_preserves_explicit_dry_run_false_for_real_output_window():
    assert RoastConfig.from_mapping({"dry_run": False}).dry_run is False


def test_roast_config_preserves_explicit_avatar_timeout_zero():
    assert RoastConfig.from_mapping({"avatar_fetch_timeout_seconds": 0}).avatar_fetch_timeout_seconds == 0


def test_roast_config_parses_activity_level_with_standard_default():
    assert RoastConfig.from_mapping({}).activity_level == "standard"
    assert RoastConfig.from_mapping({"activity_level": "quiet"}).activity_level == "quiet"
    assert RoastConfig.from_mapping({"activity_level": "active"}).activity_level == "active"
    assert RoastConfig.from_mapping({"activity_level": "noisy"}).activity_level == "standard"


def test_danmaku_response_prompt_is_not_avatar_roast_template():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="sharp", dry_run=True))
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="猫猫今天怎么这么安静",
        source="live_danmaku",
        live_mode="solo_stream",
    )
    identity = ViewerIdentity(uid="42", nickname="viewer")
    profile = ViewerProfile(uid="42", nickname="viewer", roast_count=1)

    request = module.build_request(event, identity, profile)

    assert request.dry_run is True
    assert "[NEKO Live danmaku response]" in request.prompt_text
    assert "猫猫今天怎么这么安静" in request.prompt_text
    assert "Do not repeat first-appearance" in request.prompt_text
    assert "avatar" in request.prompt_text
    assert "only host on stage" in request.prompt_text


def test_danmaku_response_prompt_includes_recent_interaction_context():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(
        config=RoastConfig(roast_strength="normal", dry_run=True),
        recent_interaction_context=lambda limit=3: [
            "avatar_roast / live_danmaku from viewer: 第一次来",
            "idle_hosting / idle_hosting: solo quiet-room host beat",
        ],
    )
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="那你继续说",
        source="live_danmaku",
        live_mode="solo_stream",
    )
    identity = ViewerIdentity(uid="42", nickname="viewer")
    profile = ViewerProfile(uid="42", nickname="viewer", roast_count=1)

    request = module.build_request(event, identity, profile)

    assert "Recent live context:" in request.prompt_text
    assert "avatar_roast / live_danmaku from viewer: 第一次来" in request.prompt_text
    assert "idle_hosting / idle_hosting: solo quiet-room host beat" in request.prompt_text
    assert "Use recent context only to avoid repetition" in request.prompt_text
    assert "Do not continue the previous reply" in request.prompt_text
    assert "The current danmaku is always the primary target" in request.prompt_text
    assert "Short danmaku should receive a short reply" in request.prompt_text


def test_danmaku_response_prompt_separates_solo_and_co_stream_roles():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    identity = ViewerIdentity(uid="42", nickname="viewer")
    profile = ViewerProfile(uid="42", nickname="viewer", roast_count=1)

    solo = module.build_request(
        ViewerEvent(uid="42", nickname="viewer", danmaku_text="hello", source="live_danmaku", live_mode="solo_stream"),
        identity,
        profile,
    )
    co_stream = module.build_request(
        ViewerEvent(uid="42", nickname="viewer", danmaku_text="hello", source="live_danmaku", live_mode="co_stream"),
        identity,
        profile,
    )

    assert "only on-stage host" in solo.prompt_text
    assert "low-interrupt partner" in co_stream.prompt_text
    assert "solo_stream response contract" in solo.prompt_text
    assert "carry the room alone" in solo.prompt_text
    assert "co_stream response contract" in co_stream.prompt_text
    assert "do not take over the host role" in co_stream.prompt_text
    assert "Do not invent or hard-code streamer relationship labels" in solo.prompt_text
    assert "Do not invent or hard-code streamer relationship labels" in co_stream.prompt_text


def test_danmaku_response_prompt_blocks_previous_reply_pollution():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(
        config=RoastConfig(roast_strength="normal", dry_run=True),
        recent_interaction_context=lambda limit=3: [
            "danmaku_response / live_danmaku from viewer: 上一条很长很长的接话",
        ],
    )
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="哦",
        source="live_danmaku",
        live_mode="solo_stream",
    )
    identity = ViewerIdentity(uid="42", nickname="viewer")
    profile = ViewerProfile(uid="42", nickname="viewer", roast_count=1)

    request = module.build_request(event, identity, profile)

    assert "Do not inherit the previous answer's topic, rhythm, or sentence length." in request.prompt_text
    assert "Do not continue prizes, plans, games, or audience-suggestion beats from the previous reply." in request.prompt_text
    assert "Current danmaku wins over recent context." in request.prompt_text
    assert "For one-word or very short danmaku, answer with a tiny reaction." in request.prompt_text
    assert "Do not launch a new show segment, special plan, topic poll, reward bit, or audience-suggestion prompt." in request.prompt_text
    assert "Carrying the room means crisp timing, not monologue, plans, or host-script expansion." in request.prompt_text


def test_live_interaction_prompts_share_short_reply_contract():
    identity = ViewerIdentity(uid="42", nickname="viewer")
    profile = ViewerProfile(uid="42", nickname="viewer", roast_count=1)
    short_contract = "Hard length limit: one sentence, no paragraph, at most 14 Chinese characters or 8 English words."

    danmaku = DanmakuResponseModule()
    danmaku.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    danmaku_request = danmaku.build_request(
        ViewerEvent(uid="42", nickname="viewer", danmaku_text="短句", source="live_danmaku", live_mode="solo_stream"),
        identity,
        profile,
    )

    avatar = AvatarRoastModule()
    avatar.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    avatar_request = avatar.build_request(
        ViewerEvent(uid="42", nickname="viewer", danmaku_text="短句", source="live_danmaku", live_mode="solo_stream"),
        identity,
        ViewerProfile(uid="42", nickname="viewer", roast_count=0),
    )
    idle_request = avatar.build_request(
        ViewerEvent(uid="__neko_idle__", nickname="NEKO", source="idle_hosting", live_mode="solo_stream"),
        ViewerIdentity(uid="__neko_idle__", nickname="NEKO"),
        ViewerProfile(uid="__neko_idle__", nickname="NEKO"),
    )

    warmup = WarmupHostingModule()
    warmup.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    warmup_request = warmup.build_request(
        ViewerEvent(uid="__neko_warmup__", nickname="NEKO", source="warmup_hosting", live_mode="solo_stream"),
        ViewerIdentity(uid="__neko_warmup__", nickname="NEKO"),
        ViewerProfile(uid="__neko_warmup__", nickname="NEKO"),
    )

    active = ActiveEngagementModule()
    active.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    active_request = active.build_request(
        ViewerEvent(uid="__neko_active__", nickname="NEKO", source="active_engagement", live_mode="solo_stream"),
        ViewerIdentity(uid="__neko_active__", nickname="NEKO"),
        ViewerProfile(uid="__neko_active__", nickname="NEKO"),
    )

    common_rules = [
        short_contract,
        "One breath only: no more than 20 Chinese chars or 10 English words when the idea still works.",
        "Prefer a compact live punchline over explanation, setup, or follow-up commentary.",
        "Do not turn a reply into a host script, segment intro, plan, or audience survey.",
        "Do not chain multiple clauses with commas",
    ]
    reply_rules = [
        "If the viewer's danmaku is short, answer even shorter.",
        "For one-word or very short danmaku, answer with a tiny reaction.",
        "If recent context was longer than the current danmaku, shrink the reply instead of matching it.",
        "No explanation, no setup, no second sentence, no follow-up question unless the current danmaku asks one.",
    ]
    host_rules = [
        "If the room is quiet, keep the line even smaller.",
        "One small host beat only; if asking, ask one concrete low-pressure question.",
        "If recent context was longer than this host beat, shrink the line instead of matching it.",
        "No explanation, no setup, no second sentence, no extra follow-up after the concrete hook.",
    ]

    for request in [danmaku_request, avatar_request, idle_request, warmup_request, active_request]:
        for rule in common_rules:
            assert rule in request.prompt_text

    for request in [danmaku_request, avatar_request]:
        for rule in reply_rules:
            assert rule in request.prompt_text
        assert "One small host beat only; if asking, ask one concrete low-pressure question." not in request.prompt_text

    for request in [idle_request, warmup_request, active_request]:
        for rule in host_rules:
            assert rule in request.prompt_text
        assert "If the viewer's danmaku is short, answer even shorter." not in request.prompt_text
        assert (
            "No explanation, no setup, no second sentence, no follow-up question unless the current danmaku asks one."
            not in request.prompt_text
        )


def test_avatar_roast_prompt_separates_solo_and_co_stream_roles():
    module = AvatarRoastModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    identity = ViewerIdentity(uid="42", nickname="viewer")
    profile = ViewerProfile(uid="42", nickname="viewer")

    solo = module.build_request(
        ViewerEvent(uid="42", nickname="viewer", danmaku_text="hello", source="live_danmaku", live_mode="solo_stream"),
        identity,
        profile,
    )
    co_stream = module.build_request(
        ViewerEvent(uid="42", nickname="viewer", danmaku_text="hello", source="live_danmaku", live_mode="co_stream"),
        identity,
        profile,
    )

    assert "solo_stream first-appearance contract" in solo.prompt_text
    assert "NEKO is carrying the room alone" in solo.prompt_text
    assert "co_stream first-appearance contract" in co_stream.prompt_text
    assert "do not steal the human streamer's host role" in co_stream.prompt_text
    assert "Do not invent or hard-code streamer relationship labels" in solo.prompt_text
    assert "Do not invent or hard-code streamer relationship labels" in co_stream.prompt_text


def test_solo_avatar_roast_uses_current_danmaku_before_avatar_details():
    module = AvatarRoastModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="sharp", dry_run=True))
    identity = ViewerIdentity(uid="42", nickname="viewer", avatar_bytes=b"avatar", avatar_mime="image/png")
    profile = ViewerProfile(uid="42", nickname="viewer")

    solo = module.build_request(
        ViewerEvent(
            uid="42",
            nickname="viewer",
            danmaku_text="猫猫今天怎么这么安静",
            source="live_danmaku",
            live_mode="solo_stream",
        ),
        identity,
        profile,
    )
    co_stream = module.build_request(
        ViewerEvent(
            uid="42",
            nickname="viewer",
            danmaku_text="猫猫今天怎么这么安静",
            source="live_danmaku",
            live_mode="co_stream",
        ),
        identity,
        profile,
    )

    assert "solo_stream first-appearance priority: current danmaku first" in solo.prompt_text
    assert "Use avatar and nickname only as accents after answering the current danmaku." in solo.prompt_text
    assert "Do not turn a first appearance into a pure avatar or ID roast when the viewer sent a danmaku." in solo.prompt_text
    assert "solo_stream first-appearance priority: current danmaku first" not in co_stream.prompt_text


def test_idle_hosting_prompt_includes_recent_interaction_context_without_metrics():
    module = AvatarRoastModule()
    module.ctx = SimpleNamespace(
        config=RoastConfig(roast_strength="normal", dry_run=True),
        recent_interaction_context=lambda limit=3: [
            "danmaku_response / live_danmaku from viewer: 猫猫在吗",
            "idle_hosting / idle_hosting: solo quiet-room host beat",
        ],
    )
    event = ViewerEvent(uid="__neko_idle__", nickname="NEKO", source="idle_hosting", live_mode="solo_stream")
    identity = ViewerIdentity(uid="__neko_idle__", nickname="NEKO")
    profile = ViewerProfile(uid="__neko_idle__", nickname="NEKO")

    request = module.build_request(event, identity, profile)

    assert "Recent live context:" in request.prompt_text
    assert "danmaku_response / live_danmaku from viewer: 猫猫在吗" in request.prompt_text
    assert "idle_hosting / idle_hosting: solo quiet-room host beat" in request.prompt_text
    assert "Do not reuse the same opening, punchline shape, or host beat" in request.prompt_text
    assert "last_activity_age_sec" not in request.prompt_text
    assert "cooldown" not in request.prompt_text.lower()


def test_idle_hosting_prompt_uses_activity_level_strategy():
    event = ViewerEvent(uid="__neko_idle__", nickname="NEKO", source="idle_hosting", live_mode="solo_stream")
    identity = ViewerIdentity(uid="__neko_idle__", nickname="NEKO")
    profile = ViewerProfile(uid="__neko_idle__", nickname="NEKO")

    quiet_module = AvatarRoastModule()
    quiet_module.ctx = SimpleNamespace(config=RoastConfig(activity_level="quiet", dry_run=True))
    quiet_request = quiet_module.build_request(event, identity, profile)
    assert "pacing: quiet" in quiet_request.prompt_text
    assert "Prefer a soft observation over a direct question." in quiet_request.prompt_text

    active_module = AvatarRoastModule()
    active_module.ctx = SimpleNamespace(config=RoastConfig(activity_level="active", dry_run=True))
    active_request = active_module.build_request(event, identity, profile)
    assert "pacing: active" in active_request.prompt_text
    assert "You may ask one specific, low-pressure question." in active_request.prompt_text


def test_idle_hosting_prompt_uses_host_beat_material():
    module = AvatarRoastModule()
    module.ctx = SimpleNamespace(config=RoastConfig(activity_level="standard", dry_run=True))
    event = ViewerEvent(
        uid="__neko_idle__",
        nickname="NEKO",
        source="idle_hosting",
        live_mode="solo_stream",
        raw={
            "host_beat": {
                "key": "idle:soft-observation",
                "shape": "soft_observation",
                "fun_axis": "mood",
                "title": "quiet room temperature",
                "hint": "Say one soft observation, not a direct question.",
                "reply_affordance": "viewer can answer with one small mood word",
            }
        },
    )
    identity = ViewerIdentity(uid="__neko_idle__", nickname="NEKO")
    profile = ViewerProfile(uid="__neko_idle__", nickname="NEKO")

    request = module.build_request(event, identity, profile)

    assert "Host beat material:" in request.prompt_text
    assert "soft_observation" in request.prompt_text
    assert "fun_axis: mood" in request.prompt_text
    assert "quiet room temperature" in request.prompt_text
    assert "Say one soft observation, not a direct question." in request.prompt_text
    assert "viewer can answer with one small mood word" in request.prompt_text
    assert "Use the host beat reply_affordance as the only reply hook; do not add a second question." in request.prompt_text
    assert "Use the host beat fun_axis as the line's purpose; do not drift into generic hosting." in request.prompt_text


def test_active_engagement_prompt_is_one_light_solo_topic():
    module = ActiveEngagementModule()
    module.ctx = SimpleNamespace(
        config=RoastConfig(activity_level="active", roast_strength="sharp", dry_run=True),
        recent_interaction_context=lambda limit=3: ["danmaku_response / live_danmaku from viewer: 猫猫聊点什么"],
    )
    event = ViewerEvent(uid="__neko_active__", nickname="NEKO", source="active_engagement", live_mode="solo_stream")
    identity = ViewerIdentity(uid=event.uid, nickname=event.nickname)
    profile = ViewerProfile(uid=event.uid, nickname=event.nickname)

    request = module.build_request(event, identity, profile)

    assert request.dry_run is True
    assert "[NEKO Live active engagement]" in request.prompt_text
    assert "one concrete, low-pressure question" in request.prompt_text
    assert "Do not pretend a viewer sent a message" in request.prompt_text
    assert "Do not use generic host slogans" in request.prompt_text
    assert "Never address the whole room with broad audience-bait openings like everyone, anyone, chat, 大家, or 你们." in request.prompt_text
    assert "Prefer one tiny observation over a plan, segment, or open-ended topic survey." in request.prompt_text
    assert "Every active engagement line must give viewers one concrete reply handle" in request.prompt_text
    assert "Use the provided viewer reply path as the only reply handle; do not add a second question." in request.prompt_text
    assert "Use the provided fun axis as the line's purpose; do not drift into generic hosting." in request.prompt_text
    assert "A/B choice, one-word answer, tiny stance, or playful yes/no-with-a-side" in request.prompt_text
    assert "Do not use generic Chinese host lines equivalent to" in request.prompt_text
    assert "澶у" not in request.prompt_text
    assert "Do not say special plan, everyone look, next let's, what should we talk about, or tell me what you want." in request.prompt_text
    assert "Do not invent or hard-code streamer relationship labels" in request.prompt_text
    assert "Continuity rule" in request.prompt_text


def test_active_engagement_prompt_turns_shape_into_concrete_task():
    module = ActiveEngagementModule()
    module.ctx = SimpleNamespace(config=RoastConfig(activity_level="standard", roast_strength="normal", dry_run=True))
    event = ViewerEvent(
        uid="__neko_active__",
        nickname="NEKO",
        source="active_engagement",
        live_mode="solo_stream",
        raw={
            "topic_material": {
                "source": "bili_trending",
                "shape": "either_or",
                "title": "猫猫今天怎么这么安静",
                "intent": "quick_vote",
                "reply_affordance": "viewer can answer with one side",
                "hint": "Use this topic as raw material.",
            }
        },
    )
    identity = ViewerIdentity(uid=event.uid, nickname=event.nickname)
    profile = ViewerProfile(uid=event.uid, nickname=event.nickname)

    request = module.build_request(event, identity, profile)

    assert "shape task:" in request.prompt_text
    assert "turn the title into one A/B choice" in request.prompt_text
    assert "example pattern:" in request.prompt_text
    assert "two concrete sides" in request.prompt_text
    assert "intent: quick_vote" in request.prompt_text
    assert "viewer reply path: viewer can answer with one side" in request.prompt_text
    assert "avoid yes/no questions" in request.prompt_text


def test_active_engagement_prompt_blocks_broad_engagement_bait():
    module = ActiveEngagementModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))

    request = module.build_request(
        ViewerEvent(uid="__neko_active__", nickname="NEKO", source="active_engagement", live_mode="solo_stream"),
        ViewerIdentity(uid="__neko_active__", nickname="NEKO"),
        ViewerProfile(uid="__neko_active__", nickname="NEKO"),
    )

    assert "Do not ask viewers what they want to hear" in request.prompt_text
    assert "Do not ask viewers to choose the stream topic for NEKO" in request.prompt_text
    assert "Do not say get the chat moving" in request.prompt_text
    assert "Do not say 大家快来互动, 弹幕刷起来, 接下来我们, or 特别企划." in request.prompt_text


def test_warmup_hosting_prompt_is_opening_not_idle_filler():
    module = WarmupHostingModule()
    module.ctx = SimpleNamespace(config=RoastConfig(activity_level="standard", roast_strength="normal", dry_run=True))
    event = ViewerEvent(uid="__neko_warmup__", nickname="NEKO", source="warmup_hosting", live_mode="solo_stream")
    identity = ViewerIdentity(uid=event.uid, nickname=event.nickname)
    profile = ViewerProfile(uid=event.uid, nickname=event.nickname)

    request = module.build_request(event, identity, profile)

    assert request.dry_run is True
    assert "[NEKO Live solo warmup hosting]" in request.prompt_text
    assert "opening a solo_stream" in request.prompt_text
    assert "not a cold-room filler" in request.prompt_text
    assert "Do not pretend a viewer sent a message" in request.prompt_text
    assert "Do not invent or hard-code streamer relationship labels" in request.prompt_text
    assert "Output only NEKO's line" in request.prompt_text


def test_utc_now_iso_returns_timezone_aware_utc_timestamp():
    assert utc_now_iso().endswith("+00:00")


def test_viewer_identity_public_dict_does_not_expose_email():
    public = ViewerIdentity(uid="1", nickname="tester", email="private@example.test").to_public_dict()

    assert "email" not in public


def test_interaction_result_public_dict_does_not_expose_prompt_text():
    event = ViewerEvent(uid="1", nickname="tester", danmaku_text="private danmaku")
    identity = ViewerIdentity(uid="1", nickname="tester")
    profile = ViewerProfile(uid="1", nickname="tester")
    request = InteractionRequest(
        event=event,
        identity=identity,
        profile=profile,
        prompt_text="internal prompt with private danmaku and persona rules",
        live_mode="solo_stream",
        strength="sharp",
    )
    result = InteractionResult(
        accepted=True,
        status="dry_run",
        event=event,
        identity=identity,
        profile=profile,
        request=request,
    )

    public_request = result.to_public_dict()["request"]

    assert public_request is not None
    assert "prompt_text" not in public_request


def test_interaction_result_public_dict_exposes_response_latency_ms():
    event = ViewerEvent(
        uid="1",
        nickname="tester",
        source="live_danmaku",
        seen_at="2026-06-20T10:00:00+00:00",
    )
    result = InteractionResult(
        accepted=True,
        status="pushed",
        event=event,
        created_at="2026-06-20T10:00:02.500000+00:00",
    )

    assert result.to_public_dict()["response_latency_ms"] == 2500
    assert result.to_sandbox_dict()["response_latency_ms"] == 2500


def test_permission_gate_requires_developer_tools_for_sandbox():
    gate = PermissionGate(RoastConfig(developer_tools_enabled=False))

    allowed, reason = gate.allows_source("developer_sandbox")

    assert allowed is False
    assert reason == "developer tools are disabled"

    gate.update(RoastConfig(developer_tools_enabled=True))
    assert gate.allows_source("developer_sandbox") == (True, "")


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
    )

    result = await NekoDispatcher(plugin).push_roast(request)

    assert "queued_to_neko(" in result
    assert "image_part_bytes=0" in result
    assert plugin.parts == [{"type": "text", "text": "reply"}]


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
    assert plugin.metadata["max_reply_chars"] == 40
    assert plugin.metadata["response_module_hint"] == "avatar_roast"


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
    assert "max_reply_chars=40" in result
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
    assert plugin.parts == [
        {"type": "text", "text": "reply"},
        {"type": "image", "data": b"avatar", "mime": "image/png"},
    ]


@pytest.mark.asyncio
async def test_module_toggle_failure_keeps_previous_state_and_success_clears_degraded():
    class Module:
        id = "demo"
        title = "Demo"
        version = "1"
        enabled = False
        domain = "interaction"
        fail = True

        async def setup(self, ctx):
            return None

        async def teardown(self):
            return None

        async def on_enable(self, ctx):
            if self.fail:
                raise RuntimeError("boom")

        async def on_disable(self):
            return None

        def status(self):
            return {}

        def config_schema(self):
            return []

    module = Module()
    registry = ModuleRegistry()
    registry.register(module)

    assert await registry.enable("demo", ctx=None) is False
    assert module.enabled is False
    assert registry.is_degraded("demo") is True

    module.fail = False
    assert await registry.enable("demo", ctx=None) is True
    assert module.enabled is True
    assert registry.is_degraded("demo") is False


@pytest.mark.asyncio
async def test_bili_login_check_none_state_stays_waiting():
    class Events:
        NONE = object()
        SCAN = object()
        CONF = object()
        TIMEOUT = object()
        DONE = object()

    class Session:
        async def check_state(self):
            return Events.NONE

    service = BiliAuthService(
        credential_provider=lambda: None,
        credential_saver=lambda _payload: True,
        credential_reloader=lambda: None,
    )
    service._login_session = Session()
    service._login_generated_at = 0.0
    service._require_login_sdk = lambda: (object, Events)

    result = await service.login_check()

    assert result["status"] == "waiting"


@pytest.mark.asyncio
async def test_bili_login_check_clears_session_when_credential_save_fails():
    class Events:
        NONE = object()
        SCAN = object()
        CONF = object()
        TIMEOUT = object()
        DONE = object()

    class Credential:
        sessdata = "sess"
        bili_jct = "jct"
        dedeuserid = "42"
        buvid3 = "buvid"

    class Session:
        async def check_state(self):
            return Events.DONE

        def get_credential(self):
            return Credential()

    cleanup_calls = 0

    async def save_fails(_payload):
        return False

    async def no_credential():
        return None

    async def reload_unused():
        raise AssertionError("credential reload should not run after save failure")

    def cleanup():
        nonlocal cleanup_calls
        cleanup_calls += 1

    service = BiliAuthService(
        credential_provider=no_credential,
        credential_saver=save_fails,
        credential_reloader=reload_unused,
        cleanup_callback=cleanup,
    )
    service._login_session = Session()
    service._require_login_sdk = lambda: (object, Events)

    with pytest.raises(RuntimeError):
        await service.login_check()

    assert service._login_session is None
    assert cleanup_calls == 1


@pytest.mark.asyncio
async def test_pipeline_records_dry_run_as_dispatcher_outcome_not_pushed():
    class Audit:
        def record(self, *_args, **_kwargs):
            return None

    class Safety:
        def before_event(self, _event):
            return SafetyDecision(True)

        def before_output(self, _event):
            return SafetyDecision(True)

        def after_event(self):
            return None

        def record_failure(self, _kind, _message):
            return None

    class ViewerProfileModule:
        async def upsert(self, identity):
            return ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)

        async def has_roasted(self, _uid):
            return False

        async def mark_roasted(self, _uid, _output):
            return None

    class Dispatcher:
        async def push_roast(self, _request):
            return "dry_run(target=none, ai_behavior=respond)"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text="test",
                live_mode=event.live_mode,
                strength="normal",
                dry_run=True,
            )

    ctx = SimpleNamespace(
        audit=Audit(),
        config=RoastConfig(live_enabled=True, roast_once_per_uid=True, dry_run=True),
        permission_gate=PermissionGate(RoastConfig(live_enabled=True, roast_once_per_uid=True)),
        safety_guard=Safety(),
        bili_identity=SimpleNamespace(resolve=lambda event: asyncio.sleep(0, result=ViewerIdentity(uid=event.uid, nickname=event.nickname))),
        viewer_profile=ViewerProfileModule(),
        avatar_roast=AvatarRoast(),
        dispatcher=Dispatcher(),
        results=[],
    )
    ctx.record_result = ctx.results.append

    result = await RoastPipeline(ctx).handle_event(
        ViewerEvent(uid="42", nickname="dry", danmaku_text="hi", source="live_danmaku")
    )

    assert result.status == "dry_run"
    assert result.accepted is False
    assert ctx.results[0].status == "dry_run"
    assert not any(step.id == "viewer_profile.mark_roasted" for step in result.steps)


@pytest.mark.asyncio
async def test_pipeline_public_result_profile_reflects_successful_first_roast():
    class Audit:
        def record(self, *_args, **_kwargs):
            return None

    class Safety:
        def before_event(self, _event):
            return SafetyDecision(True)

        def before_output(self, _event):
            return SafetyDecision(True)

        def after_event(self):
            return None

        def record_failure(self, _kind, _message):
            return None

    class ViewerProfileModule:
        async def upsert(self, identity):
            return ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)

        async def has_roasted(self, _uid):
            return False

        async def mark_roasted(self, _uid, _output):
            return None

    class Dispatcher:
        async def push_roast(self, _request):
            return "queued_to_neko(first roast)"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text="first roast",
                live_mode=event.live_mode,
                strength="normal",
            )

    ctx = SimpleNamespace(
        audit=Audit(),
        config=RoastConfig(live_enabled=True, roast_once_per_uid=True),
        permission_gate=PermissionGate(RoastConfig(live_enabled=True, roast_once_per_uid=True)),
        safety_guard=Safety(),
        bili_identity=SimpleNamespace(resolve=lambda event: asyncio.sleep(0, result=ViewerIdentity(uid=event.uid, nickname=event.nickname))),
        viewer_profile=ViewerProfileModule(),
        avatar_roast=AvatarRoast(),
        dispatcher=Dispatcher(),
        results=[],
    )
    ctx.record_result = ctx.results.append

    result = await RoastPipeline(ctx).handle_event(
        ViewerEvent(uid="42", nickname="first", danmaku_text="hi", source="live_danmaku")
    )

    assert result.status == "pushed"
    assert result.profile is not None
    assert result.profile.roast_count == 1
    assert result.profile.last_result == "queued_to_neko(first roast)"
    public_result = result.to_public_dict()
    assert public_result["profile"]["roast_count"] == 1
    assert public_result["profile"]["last_result"] == "queued_to_neko(first roast)"


@pytest.mark.asyncio
async def test_pipeline_records_dispatcher_skip_as_skipped_not_pushed():
    class Audit:
        def record(self, *_args, **_kwargs):
            return None

    class Safety:
        def before_event(self, _event):
            return SafetyDecision(True)

        def before_output(self, _event):
            return SafetyDecision(True)

        def after_event(self):
            return None

        def record_failure(self, _kind, _message):
            return None

    class ViewerProfileModule:
        async def upsert(self, identity):
            return ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)

        async def has_roasted(self, _uid):
            return False

        async def mark_roasted(self, _uid, _output):
            return None

    class Dispatcher:
        async def push_roast(self, _request):
            return "skipped_to_neko(reason=non-deliverable)"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text="test",
                live_mode=event.live_mode,
                strength="normal",
                should_push=False,
                reason="non-deliverable",
            )

    ctx = SimpleNamespace(
        audit=Audit(),
        config=RoastConfig(live_enabled=True, roast_once_per_uid=True, dry_run=False),
        permission_gate=PermissionGate(RoastConfig(live_enabled=True, roast_once_per_uid=True)),
        safety_guard=Safety(),
        bili_identity=SimpleNamespace(resolve=lambda event: asyncio.sleep(0, result=ViewerIdentity(uid=event.uid, nickname=event.nickname))),
        viewer_profile=ViewerProfileModule(),
        avatar_roast=AvatarRoast(),
        dispatcher=Dispatcher(),
        results=[],
    )
    ctx.record_result = ctx.results.append

    result = await RoastPipeline(ctx).handle_event(
        ViewerEvent(uid="42", nickname="skip", danmaku_text="hi", source="live_danmaku")
    )

    assert result.status == "skipped"
    assert result.accepted is False
    assert result.reason == "non-deliverable"
    assert ctx.results[0].status == "skipped"
    assert not any(step.id == "viewer_profile.mark_roasted" for step in result.steps)


@pytest.mark.asyncio
async def test_pipeline_routes_repeat_live_danmaku_to_danmaku_response():
    class Audit:
        def __init__(self):
            self.records = []

        def record(self, op, message="", level="info", detail=None):
            self.records.append({"op": op, "message": message, "level": level, "detail": detail or {}})

    class Safety:
        def before_event(self, _event):
            return SafetyDecision(True)

        def before_output(self, _event):
            return SafetyDecision(True)

        def after_event(self):
            return None

        def record_failure(self, _kind, _message):
            return None

    class ViewerProfileModule:
        async def upsert(self, identity):
            return ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url, roast_count=1)

        async def has_roasted(self, _uid):
            return True

        async def mark_roasted(self, _uid, _output):
            raise AssertionError("repeat danmaku responses must not mark avatar roast")

    class Dispatcher:
        def __init__(self):
            self.requests = []

        async def push_roast(self, request):
            self.requests.append(request)
            return "queued_to_neko(danmaku_response)"

    class AvatarRoast:
        def build_request(self, *_args):
            raise AssertionError("repeat danmaku must not use avatar_roast")

    class DanmakuResponse:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text=f"reply to: {event.danmaku_text}",
                live_mode=event.live_mode,
                strength="normal",
            )

    ctx = SimpleNamespace(
        audit=Audit(),
        config=RoastConfig(live_enabled=True, roast_once_per_uid=True),
        permission_gate=PermissionGate(RoastConfig(live_enabled=True, roast_once_per_uid=True)),
        safety_guard=Safety(),
        bili_identity=SimpleNamespace(resolve=lambda event: asyncio.sleep(0, result=ViewerIdentity(uid=event.uid, nickname=event.nickname))),
        viewer_profile=ViewerProfileModule(),
        avatar_roast=AvatarRoast(),
        danmaku_response=DanmakuResponse(),
        dispatcher=Dispatcher(),
        results=[],
    )
    ctx.record_result = ctx.results.append

    result = await RoastPipeline(ctx).handle_event(
        ViewerEvent(uid="42", nickname="same", danmaku_text="还在吗", source="live_danmaku", live_mode="solo_stream")
    )

    assert result.status == "pushed"
    assert result.request is not None
    assert result.request.prompt_text == "reply to: 还在吗"
    assert any(step.id == "viewer_gate" and step.status == "ok" and step.message == "repeat_danmaku" for step in result.steps)
    assert any(step.id == "danmaku_response" and step.status == "ok" for step in result.steps)
    assert not any(step.id == "viewer_profile.mark_roasted" for step in result.steps)
    assert ctx.dispatcher.requests == [result.request]
    assert ctx.results == [result]


@pytest.mark.asyncio
async def test_pipeline_paces_consecutive_solo_first_roasts_to_danmaku_response():
    class Audit:
        def __init__(self):
            self.records = []

        def record(self, op, message="", level="info", detail=None):
            self.records.append({"op": op, "message": message, "level": level, "detail": detail or {}})

    class Safety:
        def before_event(self, _event):
            return SafetyDecision(True)

        def before_output(self, _event):
            return SafetyDecision(True)

        def after_event(self):
            return None

        def record_failure(self, _kind, _message):
            return None

    class ViewerProfileModule:
        def __init__(self):
            self.roasted = set()
            self.mark_calls = []

        async def upsert(self, identity):
            return ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)

        async def has_roasted(self, uid):
            return uid in self.roasted

        async def mark_roasted(self, uid, _output):
            self.mark_calls.append(uid)
            self.roasted.add(uid)

    class Dispatcher:
        def __init__(self):
            self.requests = []

        async def push_roast(self, request):
            self.requests.append(request)
            return f"queued_to_neko({request.prompt_text})"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text=f"avatar_roast:{event.uid}",
                live_mode=event.live_mode,
                strength="normal",
            )

    class DanmakuResponse:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text=f"danmaku_response:{event.danmaku_text}",
                live_mode=event.live_mode,
                strength="normal",
            )

    viewer_profile = ViewerProfileModule()
    ctx = SimpleNamespace(
        audit=Audit(),
        config=RoastConfig(live_enabled=True, roast_once_per_uid=True),
        permission_gate=PermissionGate(RoastConfig(live_enabled=True, roast_once_per_uid=True)),
        safety_guard=Safety(),
        bili_identity=SimpleNamespace(resolve=lambda event: asyncio.sleep(0, result=ViewerIdentity(uid=event.uid, nickname=event.nickname))),
        viewer_profile=viewer_profile,
        avatar_roast=AvatarRoast(),
        danmaku_response=DanmakuResponse(),
        dispatcher=Dispatcher(),
        results=[],
    )
    ctx.record_result = ctx.results.append
    pipeline = RoastPipeline(ctx)
    now = [100.0]
    pipeline._now = lambda: now[0]

    first = await pipeline.handle_event(ViewerEvent(uid="42", nickname="first", danmaku_text="第一次来", source="live_danmaku", live_mode="solo_stream"))
    now[0] += 10.0
    second = await pipeline.handle_event(ViewerEvent(uid="77", nickname="second", danmaku_text="猫猫在吗", source="live_danmaku", live_mode="solo_stream"))

    assert first.status == "pushed"
    assert second.status == "pushed"
    assert first.request is not None
    assert second.request is not None
    assert first.request.prompt_text == "avatar_roast:42"
    assert second.request.prompt_text == "danmaku_response:猫猫在吗"
    assert any(step.id == "viewer_gate" and step.status == "ok" and step.message == "entrance_pacing" for step in second.steps)
    assert any(step.id == "danmaku_response" and step.status == "ok" for step in second.steps)
    assert viewer_profile.mark_calls == ["42", "77"]


@pytest.mark.asyncio
async def test_pipeline_once_per_uid_gate_is_atomic_for_concurrent_events():
    class Audit:
        def __init__(self):
            self.records = []

        def record(self, op, message="", level="info", detail=None):
            self.records.append({"op": op, "message": message, "level": level, "detail": detail or {}})

    class Safety:
        def before_event(self, _event):
            return SafetyDecision(True)

        def before_output(self, _event):
            return SafetyDecision(True)

        def after_event(self):
            return None

        def record_failure(self, _kind, _message):
            return None

    class ViewerProfileModule:
        def __init__(self):
            self.roasted = set()
            self.mark_calls = 0

        async def upsert(self, identity):
            return ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)

        async def has_roasted(self, uid):
            return uid in self.roasted

        async def mark_roasted(self, uid, _output):
            self.mark_calls += 1
            self.roasted.add(uid)

    class Dispatcher:
        def __init__(self):
            self.calls = 0
            self.prompts = []

        async def push_roast(self, request):
            self.calls += 1
            self.prompts.append(request.prompt_text)
            await asyncio.sleep(0)
            return "queued_to_neko(test)"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text="test",
                live_mode=event.live_mode,
                strength="normal",
            )

    class DanmakuResponse:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text="danmaku response",
                live_mode=event.live_mode,
                strength="normal",
            )

    viewer_profile = ViewerProfileModule()
    ctx = SimpleNamespace(
        audit=Audit(),
        config=RoastConfig(live_enabled=True, roast_once_per_uid=True),
        permission_gate=PermissionGate(RoastConfig(live_enabled=True, roast_once_per_uid=True)),
        safety_guard=Safety(),
        bili_identity=SimpleNamespace(resolve=lambda event: asyncio.sleep(0, result=ViewerIdentity(uid=event.uid, nickname=event.nickname))),
        viewer_profile=viewer_profile,
        avatar_roast=AvatarRoast(),
        danmaku_response=DanmakuResponse(),
        dispatcher=Dispatcher(),
        results=[],
    )
    ctx.record_result = ctx.results.append
    pipeline = RoastPipeline(ctx)
    event = ViewerEvent(uid="42", nickname="same", danmaku_text="hi", source="live_danmaku")

    first, second = await asyncio.gather(pipeline.handle_event(event), pipeline.handle_event(event))

    statuses = sorted([first.status, second.status])
    assert statuses == ["pushed", "pushed"]
    assert ctx.dispatcher.calls == 2
    assert sorted(ctx.dispatcher.prompts) == ["danmaku response", "test"]
    assert viewer_profile.mark_calls == 1
    assert any(step.id == "danmaku_response" and step.status == "ok" for step in second.steps)


@pytest.mark.asyncio
async def test_pipeline_dry_run_repeat_live_danmaku_uses_session_first_roast_marker():
    class Audit:
        def record(self, *_args, **_kwargs):
            return None

    class Safety:
        def before_event(self, _event):
            return SafetyDecision(True)

        def before_output(self, _event):
            return SafetyDecision(True)

        def after_event(self):
            return None

        def record_failure(self, _kind, _message):
            return None

    class ViewerProfileModule:
        def __init__(self):
            self.mark_calls = 0

        async def upsert(self, identity):
            return ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)

        async def has_roasted(self, _uid):
            return False

        async def mark_roasted(self, _uid, _output):
            self.mark_calls += 1

    class Dispatcher:
        def __init__(self):
            self.requests = []

        async def push_roast(self, request):
            self.requests.append(request)
            return f"dry_run({request.prompt_text})"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text="avatar_roast",
                live_mode=event.live_mode,
                strength="normal",
                dry_run=True,
            )

    class DanmakuResponse:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text="danmaku_response",
                live_mode=event.live_mode,
                strength="normal",
                dry_run=True,
            )

    viewer_profile = ViewerProfileModule()
    ctx = SimpleNamespace(
        audit=Audit(),
        config=RoastConfig(live_enabled=True, roast_once_per_uid=True, dry_run=True),
        permission_gate=PermissionGate(RoastConfig(live_enabled=True, roast_once_per_uid=True, dry_run=True)),
        safety_guard=Safety(),
        bili_identity=SimpleNamespace(resolve=lambda event: asyncio.sleep(0, result=ViewerIdentity(uid=event.uid, nickname=event.nickname))),
        viewer_profile=viewer_profile,
        avatar_roast=AvatarRoast(),
        danmaku_response=DanmakuResponse(),
        dispatcher=Dispatcher(),
        results=[],
    )
    ctx.record_result = ctx.results.append
    pipeline = RoastPipeline(ctx)

    first = await pipeline.handle_event(ViewerEvent(uid="42", nickname="same", danmaku_text="first", source="live_danmaku"))
    second = await pipeline.handle_event(ViewerEvent(uid="42", nickname="same", danmaku_text="second", source="live_danmaku"))

    assert first.status == "dry_run"
    assert second.status == "dry_run"
    assert any(step.id == "avatar_roast" and step.status == "ok" for step in first.steps)
    assert any(step.id == "viewer_gate" and step.status == "ok" and step.message == "repeat_danmaku" for step in second.steps)
    assert any(step.id == "danmaku_response" and step.status == "ok" for step in second.steps)
    assert not any(step.id == "viewer_profile.mark_roasted" for step in first.steps + second.steps)
    assert viewer_profile.mark_calls == 0


@pytest.mark.asyncio
async def test_pipeline_dry_run_session_marker_can_be_cleared_for_fresh_validation():
    class Audit:
        def record(self, *_args, **_kwargs):
            return None

    class Safety:
        def before_event(self, _event):
            return SafetyDecision(True)

        def before_output(self, _event):
            return SafetyDecision(True)

        def after_event(self):
            return None

        def record_failure(self, _kind, _message):
            return None

    class ViewerProfileModule:
        async def upsert(self, identity):
            return ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)

        async def has_roasted(self, _uid):
            return False

        async def mark_roasted(self, _uid, _output):
            raise AssertionError("dry_run must not persist first-roast state")

    class Dispatcher:
        async def push_roast(self, request):
            return f"dry_run({request.prompt_text})"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text="avatar_roast",
                live_mode=event.live_mode,
                strength="normal",
                dry_run=True,
            )

    class DanmakuResponse:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text="danmaku_response",
                live_mode=event.live_mode,
                strength="normal",
                dry_run=True,
            )

    ctx = SimpleNamespace(
        audit=Audit(),
        config=RoastConfig(live_enabled=True, roast_once_per_uid=True, dry_run=True),
        permission_gate=PermissionGate(RoastConfig(live_enabled=True, roast_once_per_uid=True, dry_run=True)),
        safety_guard=Safety(),
        bili_identity=SimpleNamespace(resolve=lambda event: asyncio.sleep(0, result=ViewerIdentity(uid=event.uid, nickname=event.nickname))),
        viewer_profile=ViewerProfileModule(),
        avatar_roast=AvatarRoast(),
        danmaku_response=DanmakuResponse(),
        dispatcher=Dispatcher(),
        results=[],
    )
    ctx.record_result = ctx.results.append
    pipeline = RoastPipeline(ctx)

    first = await pipeline.handle_event(ViewerEvent(uid="42", nickname="same", danmaku_text="first", source="live_danmaku"))
    pipeline.clear_dry_run_session_state()
    second = await pipeline.handle_event(ViewerEvent(uid="42", nickname="same", danmaku_text="fresh first", source="live_danmaku"))

    assert first.status == "dry_run"
    assert second.status == "dry_run"
    assert any(step.id == "avatar_roast" and step.status == "ok" for step in second.steps)
    assert not any(step.id == "danmaku_response" for step in second.steps)


def test_pipeline_session_state_clear_resets_entrance_pacing_marker():
    pipeline = RoastPipeline(SimpleNamespace())
    now = [100.0]
    pipeline._now = lambda: now[0]

    pipeline._record_avatar_roast_sent()
    now[0] += 10.0
    assert pipeline._entrance_pacing_active() is True

    pipeline.clear_dry_run_session_state()

    assert pipeline._entrance_pacing_active() is False


def test_pipeline_entrance_pacing_interval_follows_activity_level():
    now = [100.0]

    quiet_pipeline = RoastPipeline(SimpleNamespace(config=RoastConfig(activity_level="quiet")))
    quiet_pipeline._now = lambda: now[0]
    quiet_pipeline._record_avatar_roast_sent()
    now[0] += 50.0
    assert quiet_pipeline._entrance_pacing_active() is True

    now[0] = 100.0
    active_pipeline = RoastPipeline(SimpleNamespace(config=RoastConfig(activity_level="active")))
    active_pipeline._now = lambda: now[0]
    active_pipeline._record_avatar_roast_sent()
    now[0] += 35.0
    assert active_pipeline._entrance_pacing_active() is False


@pytest.mark.asyncio
async def test_pipeline_session_marker_prevents_repeat_avatar_roast_when_persist_fails():
    class Audit:
        def record(self, *_args, **_kwargs):
            return None

    class Safety:
        def before_event(self, _event):
            return SafetyDecision(True)

        def before_output(self, _event):
            return SafetyDecision(True)

        def after_event(self):
            return None

        def record_failure(self, _kind, _message):
            return None

    class ViewerProfileModule:
        def __init__(self):
            self.mark_calls = 0

        async def upsert(self, identity):
            return ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)

        async def has_roasted(self, _uid):
            return False

        async def mark_roasted(self, _uid, _output):
            self.mark_calls += 1
            raise RuntimeError("store temporarily unavailable")

    class Dispatcher:
        async def push_roast(self, request):
            return f"queued_to_neko({request.prompt_text})"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text=f"avatar_roast:{event.danmaku_text}",
                live_mode=event.live_mode,
                strength="normal",
            )

    class DanmakuResponse:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text=f"danmaku_response:{event.danmaku_text}",
                live_mode=event.live_mode,
                strength="normal",
            )

    viewer_profile = ViewerProfileModule()
    ctx = SimpleNamespace(
        audit=Audit(),
        config=RoastConfig(live_enabled=True, roast_once_per_uid=True),
        permission_gate=PermissionGate(RoastConfig(live_enabled=True, roast_once_per_uid=True)),
        safety_guard=Safety(),
        bili_identity=SimpleNamespace(resolve=lambda event: asyncio.sleep(0, result=ViewerIdentity(uid=event.uid, nickname=event.nickname))),
        viewer_profile=viewer_profile,
        avatar_roast=AvatarRoast(),
        danmaku_response=DanmakuResponse(),
        dispatcher=Dispatcher(),
        results=[],
    )
    ctx.record_result = ctx.results.append
    pipeline = RoastPipeline(ctx)

    first = await pipeline.handle_event(ViewerEvent(uid="42", nickname="same", danmaku_text="first", source="live_danmaku"))
    second = await pipeline.handle_event(ViewerEvent(uid="42", nickname="same", danmaku_text="second", source="live_danmaku"))

    assert first.status == "pushed"
    assert second.status == "pushed"
    assert first.request is not None
    assert second.request is not None
    assert first.request.prompt_text == "avatar_roast:first"
    assert second.request.prompt_text == "danmaku_response:second"
    assert viewer_profile.mark_calls == 1
    assert any(step.id == "viewer_gate" and step.status == "ok" and step.message == "repeat_danmaku" for step in second.steps)


@pytest.mark.asyncio
async def test_pipeline_avatar_roast_attempt_prevents_repeat_avatar_roast_when_dispatcher_fails():
    class Audit:
        def record(self, *_args, **_kwargs):
            return None

    class Safety:
        def before_event(self, _event):
            return SafetyDecision(True)

        def before_output(self, _event):
            return SafetyDecision(True)

        def after_event(self):
            return None

        def record_failure(self, _kind, _message):
            return None

    class ViewerProfileModule:
        def __init__(self):
            self.mark_calls = 0

        async def upsert(self, identity):
            return ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)

        async def has_roasted(self, _uid):
            return False

        async def mark_roasted(self, _uid, _output):
            self.mark_calls += 1

    class Dispatcher:
        def __init__(self):
            self.calls = 0

        async def push_roast(self, request):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary output failure")
            return f"queued_to_neko({request.prompt_text})"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text=f"avatar_roast:{event.danmaku_text}",
                live_mode=event.live_mode,
                strength="normal",
            )

    class DanmakuResponse:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text=f"danmaku_response:{event.danmaku_text}",
                live_mode=event.live_mode,
                strength="normal",
            )

    viewer_profile = ViewerProfileModule()
    dispatcher = Dispatcher()
    ctx = SimpleNamespace(
        audit=Audit(),
        config=RoastConfig(live_enabled=True, roast_once_per_uid=True),
        permission_gate=PermissionGate(RoastConfig(live_enabled=True, roast_once_per_uid=True)),
        safety_guard=Safety(),
        bili_identity=SimpleNamespace(resolve=lambda event: asyncio.sleep(0, result=ViewerIdentity(uid=event.uid, nickname=event.nickname))),
        viewer_profile=viewer_profile,
        avatar_roast=AvatarRoast(),
        danmaku_response=DanmakuResponse(),
        dispatcher=dispatcher,
        results=[],
    )
    ctx.record_result = ctx.results.append
    pipeline = RoastPipeline(ctx)

    first = await pipeline.handle_event(ViewerEvent(uid="42", nickname="same", danmaku_text="first", source="live_danmaku"))
    second = await pipeline.handle_event(ViewerEvent(uid="42", nickname="same", danmaku_text="second", source="live_danmaku"))

    assert first.status == "failed"
    assert second.status == "pushed"
    assert first.request is not None
    assert second.request is not None
    assert first.request.prompt_text == "avatar_roast:first"
    assert second.request.prompt_text == "danmaku_response:second"
    assert viewer_profile.mark_calls == 0
    assert any(step.id == "viewer_gate" and step.status == "ok" and step.message == "repeat_danmaku" for step in second.steps)


@pytest.mark.asyncio
async def test_pipeline_records_idle_hosting_as_own_route():
    class Audit:
        def record(self, *_args, **_kwargs):
            return None

    class Safety:
        def before_event(self, _event):
            return SafetyDecision(True)

        def before_output(self, _event):
            return SafetyDecision(True)

        def after_event(self):
            return None

        def record_failure(self, _kind, _message):
            return None

    class ViewerProfileModule:
        async def upsert(self, identity):
            return ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)

        async def has_roasted(self, _uid):
            return False

        async def mark_roasted(self, _uid, _output):
            return None

    class Dispatcher:
        async def push_roast(self, _request):
            return "queued_to_neko(idle_hosting)"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text="idle hosting prompt",
                live_mode=event.live_mode,
                strength="normal",
            )

    ctx = SimpleNamespace(
        audit=Audit(),
        config=RoastConfig(live_enabled=True, roast_once_per_uid=True),
        permission_gate=PermissionGate(RoastConfig(live_enabled=True, roast_once_per_uid=True)),
        safety_guard=Safety(),
        bili_identity=SimpleNamespace(resolve=lambda event: asyncio.sleep(0, result=ViewerIdentity(uid=event.uid, nickname=event.nickname))),
        viewer_profile=ViewerProfileModule(),
        avatar_roast=AvatarRoast(),
        dispatcher=Dispatcher(),
        results=[],
    )
    ctx.record_result = ctx.results.append

    result = await RoastPipeline(ctx).handle_event(
        ViewerEvent(uid="__neko_idle__", nickname="NEKO", source="idle_hosting", live_mode="solo_stream")
    )

    assert result.status == "pushed"
    assert any(step.id == "idle_hosting" and step.status == "ok" for step in result.steps)
    assert not any(step.id == "avatar_roast" for step in result.steps)


@pytest.mark.asyncio
async def test_pipeline_mark_roasted_failure_keeps_success_result():
    class Audit:
        def __init__(self):
            self.records = []

        def record(self, op, message="", level="info", detail=None):
            self.records.append({"op": op, "message": message, "level": level, "detail": detail or {}})

    class Safety:
        def before_event(self, _event):
            return SafetyDecision(True)

        def before_output(self, _event):
            return SafetyDecision(True)

        def after_event(self):
            return None

        def record_failure(self, _kind, _message):
            return None

    class ViewerProfileModule:
        async def upsert(self, identity):
            return ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)

        async def has_roasted(self, _uid):
            return False

        async def mark_roasted(self, _uid, _output):
            raise OSError("disk full")

    class Dispatcher:
        async def push_roast(self, _request):
            return "queued_to_neko(test)"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text="test",
                live_mode=event.live_mode,
                strength="normal",
            )

    ctx = SimpleNamespace(
        audit=Audit(),
        config=RoastConfig(live_enabled=True, roast_once_per_uid=True),
        permission_gate=PermissionGate(RoastConfig(live_enabled=True, roast_once_per_uid=True)),
        safety_guard=Safety(),
        bili_identity=SimpleNamespace(resolve=lambda event: asyncio.sleep(0, result=ViewerIdentity(uid=event.uid, nickname=event.nickname))),
        viewer_profile=ViewerProfileModule(),
        avatar_roast=AvatarRoast(),
        dispatcher=Dispatcher(),
        results=[],
    )
    ctx.record_result = ctx.results.append

    result = await RoastPipeline(ctx).handle_event(
        ViewerEvent(uid="42", nickname="same", danmaku_text="hi", source="live_danmaku")
    )

    assert result.status == "pushed"
    assert any(step.id == "viewer_profile.mark_roasted" and step.status == "failed" for step in result.steps)
    assert any(record["op"] == "viewer_profile_mark_failed" for record in ctx.audit.records)


@pytest.mark.asyncio
async def test_bili_identity_avatar_fetch_tolerates_ctx_release():
    module = BiliIdentityModule()

    class Cache:
        def get(self, _key):
            return None

        def put(self, _key, _data, _mime):
            raise AssertionError("cache should not be accessed after ctx release")

    module.ctx = SimpleNamespace(
        avatar_cache=Cache(),
        config=SimpleNamespace(avatar_fetch_timeout_seconds=1),
        audit=SimpleNamespace(record=lambda *args, **kwargs: None),
    )

    def _fetch_avatar(_url, _timeout):
        module.ctx = None
        return b"avatar", "image/png"

    module._fetch_avatar = _fetch_avatar
    module._inspect_avatar = lambda _data: (True, False)

    identity = await module.resolve(ViewerEvent(uid="7", nickname="七", avatar_url="https://example.test/a.png"))

    assert identity.avatar_bytes == b"avatar"
    assert identity.avatar_mime == "image/png"


@pytest.mark.asyncio
async def test_bili_identity_ignores_undecodable_avatar_bytes():
    module = BiliIdentityModule()
    module.ctx = SimpleNamespace(
        avatar_cache=SimpleNamespace(get=lambda _key: None, put=lambda *_args: None),
        config=SimpleNamespace(avatar_fetch_timeout_seconds=1),
        audit=SimpleNamespace(record=lambda *args, **kwargs: None),
    )
    module._fetch_avatar = lambda _url, _timeout: (b"<html>not image</html>", "text/html")

    identity = await module.resolve(ViewerEvent(uid="7", nickname="viewer", avatar_url="https://example.test/a.png"))

    assert identity.avatar_bytes is None
    assert identity.avatar_vision_ok is False
    assert "avatar_fetch_failed: ValueError" in identity.error


def test_bili_identity_rejects_private_avatar_url():
    with pytest.raises(ValueError):
        BiliIdentityModule._fetch_avatar("http://127.0.0.1/avatar.png", timeout=1)


def test_bili_identity_avatar_fetch_uses_validated_resolved_ip(monkeypatch):
    opened = {}

    def fake_getaddrinfo(host, port, type=0):
        assert host == "cdn.example.test"
        assert port == 8443
        return [(None, None, None, "", ("8.8.8.8", port))]

    class Response:
        status = 200

        def read(self, _limit):
            return b"png"

        def getheader(self, name):
            return "image/png" if name == "content-type" else ""

    class Connection:
        def request(self, method, path, headers):
            opened["method"] = method
            opened["path"] = path
            opened["host"] = headers["Host"]

        def getresponse(self):
            return Response()

        def close(self):
            opened["closed"] = True

    def fake_open(parsed, resolved_ip, port, timeout):
        opened["hostname"] = parsed.hostname
        opened["resolved_ip"] = resolved_ip
        opened["port"] = port
        opened["timeout"] = timeout
        return Connection()

    monkeypatch.setattr("plugin.plugins.neko_roast.modules.bili_identity.socket.getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(BiliIdentityModule, "_open_avatar_connection", staticmethod(fake_open))

    data, mime = BiliIdentityModule._fetch_avatar("https://cdn.example.test:8443/avatar.png?size=small", timeout=3)

    assert data == b"png"
    assert mime == "image/png"
    assert opened == {
        "hostname": "cdn.example.test",
        "resolved_ip": "8.8.8.8",
        "port": 8443,
        "timeout": 3,
        "method": "GET",
        "path": "/avatar.png?size=small",
        "host": "cdn.example.test:8443",
        "closed": True,
    }
