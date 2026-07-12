import asyncio
import json
from types import SimpleNamespace

import pytest

from plugin.plugins.neko_roast.core import active_topic_rules
from plugin.plugins.neko_roast.core.contracts import (
    InteractionRequest,
    InteractionResult,
    PipelineStep,
    RoastConfig,
    ViewerEvent,
    ViewerIdentity,
    ViewerProfile,
    normalize_live_platform,
    parse_room_id,
    utc_now_iso,
)
from plugin.plugins.neko_roast.core.contracts_public import public_dict, public_text
from plugin.plugins.neko_roast.core.live_output_quality import needs_quality_fallback, safe_fallback_reply
from plugin.plugins.neko_roast.core.permission_gate import PermissionGate
from plugin.plugins.neko_roast.core.pipeline import RoastPipeline
from plugin.plugins.neko_roast.core.runtime_live_input import record_result
from plugin.plugins.neko_roast.modules.active_engagement import ActiveEngagementModule
from plugin.plugins.neko_roast.modules.avatar_roast import AvatarRoastModule
from plugin.plugins.neko_roast.modules.danmaku_response import DanmakuResponseModule
from plugin.plugins.neko_roast.modules.warmup_hosting import WarmupHostingModule


class _SecretLike:
    def __str__(self) -> str:
        return "token=must-not-leak"

    def __bool__(self) -> bool:
        return True


def test_roast_config_defaults_to_real_output_mode():
    assert RoastConfig().dry_run is False
    assert RoastConfig.from_mapping({}).dry_run is False
    assert RoastConfig.from_mapping(None).dry_run is False


def test_roast_config_preserves_explicit_dry_run_false_for_real_output_window():
    assert RoastConfig.from_mapping({"dry_run": False}).dry_run is False


def test_roast_config_preserves_explicit_avatar_timeout_zero():
    assert RoastConfig.from_mapping({"avatar_fetch_timeout_seconds": 0}).avatar_fetch_timeout_seconds == 0


def test_roast_config_accepts_whole_number_float_int_fields():
    config = RoastConfig.from_mapping(
        {
            "rate_limit_seconds": 30.0,
            "queue_limit": 7.0,
            "recent_limit": 12.5,
        }
    )

    assert config.rate_limit_seconds == 30
    assert config.queue_limit == 7
    assert config.recent_limit == 30


def test_public_text_truncation_respects_max_len():
    assert public_text("abcdef", max_len=5) == "ab..."
    assert public_text("abcdef", max_len=3) == "..."
    assert public_text("abcdef", max_len=2) == ".."
    assert public_text("abcdef", max_len=0) == ""


def test_viewer_event_public_projection_sanitizes_trace_and_drops_raw():
    projected = ViewerEvent(
        uid="1",
        nickname="tester",
        trace_id="token=secret",
        raw={"token": "secret", "event_type": "danmaku"},
    ).to_dict()

    assert "raw" not in projected
    assert projected["trace_id"] == "[redacted]"
    assert projected["event_type"] == "danmaku"
    assert "token" not in projected


def test_public_dict_recursively_redacts_sensitive_keys_and_cookie_headers():
    projected = public_dict(
        {
            "nested": [
                {
                    "client_secret": "secret",
                    "refresh_token": "refresh",
                    "custom_token": "custom",
                    "safe": "Cookie: sid=hidden; theme=dark\nstatus=ok",
                }
            ]
        }
    )

    assert projected["nested"][0]["client_secret"] == "[redacted]"
    assert projected["nested"][0]["refresh_token"] == "[redacted]"
    assert projected["nested"][0]["custom_token"] == "[redacted]"
    assert projected["nested"][0]["safe"] == "[redacted] status=ok"


def test_viewer_derived_topic_content_stays_out_of_public_event_projection():
    event = ViewerEvent(
        uid="__neko_active__",
        source="active_engagement",
        raw={
            "topic_material": {
                "source": "recent_danmaku",
                "privacy_classification": "viewer_derived",
                "title": "private viewer words",
                "key": "danmaku:private viewer words",
                "hook": "repeat private viewer words",
                "evidence": ["private evidence"],
                "shape": "tiny_tease",
            }
        },
    )

    projected = event.to_dict()

    assert projected["topic_source"] == "recent_danmaku"
    assert projected["topic_shape"] == "tiny_tease"
    assert projected["topic_privacy_classification"] == "viewer_derived"
    assert "topic_title" not in projected
    assert "topic_key" not in projected
    assert "topic_hook" not in projected
    assert "private viewer words" not in json.dumps(projected)


def test_unknown_topic_privacy_fails_private_in_recursive_projection():
    projected = public_dict(
        {
            "topic_material": {
                "source": "future_source",
                "privacy_classification": "public",
                "title": "unknown private words",
                "key": "future:unknown-private-words",
                "hook": "unknown private hook",
                "evidence": ["unknown private evidence"],
                "shape": "light_stance",
            }
        }
    )

    topic = projected["topic_material"]
    assert topic == {
        "source": "future_source",
        "shape": "light_stance",
        "privacy_classification": "private",
    }


def test_viewer_derived_topic_key_stays_out_of_public_request_metadata():
    event = ViewerEvent(
        uid="__neko_active__",
        source="active_engagement",
        raw={
            "topic_material": {
                "source": "live_thread",
                "privacy_classification": "viewer_derived",
                "key": "thread:private-viewer-words",
                "title": "private viewer words",
            }
        },
    )
    request = InteractionRequest(
        event=event,
        identity=ViewerIdentity(uid="__neko_active__", nickname="NEKO"),
        profile=ViewerProfile(uid="__neko_active__", nickname="NEKO"),
        prompt_text="internal prompt keeps private viewer words",
        live_mode="solo_stream",
        strength="normal",
        metadata={
            "topic_source": "live_thread",
            "topic_key": "thread:private-viewer-words",
        },
    )

    projected = request.to_public_dict()

    assert request.prompt_text.endswith("private viewer words")
    assert projected["metadata"] == {"topic_source": "live_thread"}
    assert "private viewer words" not in json.dumps(projected)


def test_roast_config_accepts_string_scalars_without_truthy_string_bool_traps():
    config = RoastConfig.from_mapping(
        {
            "live_enabled": "true",
            "developer_tools_enabled": "false",
            "dry_run": "false",
            "roast_once_per_uid": 1,
            "avatar_fetch_timeout_seconds": "0",
            "recent_limit": "250",
            "rate_limit_seconds": "0",
            "queue_limit": "0",
            "safety_auto_stop_enabled": "off",
            "safety_window_seconds": "2",
            "safety_pipeline_failure_limit": "200",
            "safety_output_failure_limit": "4",
            "safety_queue_overflow_limit": "5",
        }
    )

    assert config.live_enabled is True
    assert config.developer_tools_enabled is False
    assert config.dry_run is False
    assert config.roast_once_per_uid is True
    assert config.avatar_fetch_timeout_seconds == 0
    assert config.recent_limit == 200
    assert config.rate_limit_seconds == 0
    assert config.queue_limit == 1
    assert config.safety_auto_stop_enabled is False

    numeric_false = RoastConfig.from_mapping({"live_enabled": 0, "dry_run": 0, "roast_once_per_uid": 2})
    assert numeric_false.live_enabled is False
    assert numeric_false.dry_run is False
    assert numeric_false.roast_once_per_uid is True
    assert config.safety_window_seconds == 5
    assert config.safety_pipeline_failure_limit == 100
    assert config.safety_output_failure_limit == 4
    assert config.safety_queue_overflow_limit == 5


def test_roast_config_does_not_coerce_object_scalars():
    class _LooksLikeScalar:
        def __bool__(self) -> bool:
            return True

        def __int__(self) -> int:
            return 99

        def __float__(self) -> float:
            return 99.0

        def __str__(self) -> str:
            return "sharp"

    value = _LooksLikeScalar()
    config = RoastConfig.from_mapping(
        {
            "live_mode": value,
            "roast_strength": value,
            "activity_level": value,
            "live_enabled": value,
            "developer_tools_enabled": value,
            "dry_run": value,
            "roast_once_per_uid": value,
            "co_stream_output_policy": value,
            "solo_output_policy": value,
            "avatar_fetch_timeout_seconds": value,
            "recent_limit": value,
            "rate_limit_seconds": value,
            "queue_limit": value,
            "safety_auto_stop_enabled": value,
            "safety_window_seconds": value,
            "safety_pipeline_failure_limit": value,
            "safety_output_failure_limit": value,
            "safety_queue_overflow_limit": value,
        }
    )

    assert config.live_mode == "co_stream"
    assert config.roast_strength == "normal"
    assert config.activity_level == "standard"
    assert config.live_enabled is False
    assert config.developer_tools_enabled is False
    assert config.dry_run is False
    assert config.roast_once_per_uid is True
    assert config.co_stream_output_policy == "auto_low_interrupt"
    assert config.solo_output_policy == "auto_rate_limited"
    assert config.avatar_fetch_timeout_seconds == 8.0
    assert config.recent_limit == 30
    assert config.rate_limit_seconds == 20
    assert config.queue_limit == 5
    assert config.safety_auto_stop_enabled is True
    assert config.safety_window_seconds == 60
    assert config.safety_pipeline_failure_limit == 3
    assert config.safety_output_failure_limit == 2
    assert config.safety_queue_overflow_limit == 3


def test_roast_config_parses_activity_level_with_standard_default():
    assert RoastConfig.from_mapping({}).activity_level == "standard"
    assert RoastConfig.from_mapping({"activity_level": "quiet"}).activity_level == "quiet"
    assert RoastConfig.from_mapping({"activity_level": "active"}).activity_level == "active"
    assert RoastConfig.from_mapping({"activity_level": "noisy"}).activity_level == "standard"


def test_roast_config_keeps_bilibili_room_id_and_room_ref_compatible():
    config = RoastConfig.from_mapping(
        {"live_platform": "bili", "live_room_ref": "https://live.bilibili.com/12345"}
    )

    assert config.live_platform == "bilibili"
    assert config.live_room_id == 12345
    assert config.live_room_ref == "https://live.bilibili.com/12345"


def test_roast_config_accepts_douyin_room_ref_without_numeric_room_id():
    config = RoastConfig.from_mapping(
        {"live_platform": "dy", "live_room_ref": "https://live.douyin.com/abc"}
    )

    assert config.live_platform == "douyin"
    assert config.live_room_ref == "https://live.douyin.com/abc"
    assert config.live_room_id == 0


def test_roast_config_clears_legacy_bilibili_room_id_for_douyin():
    config = RoastConfig.from_mapping(
        {
            "live_platform": "douyin",
            "live_room_ref": "https://live.douyin.com/room-42",
            "live_room_id": 12345,
        }
    )

    assert config.live_platform == "douyin"
    assert config.live_room_ref == "https://live.douyin.com/room-42"
    assert config.live_room_id == 0


def test_parse_room_id_does_not_stringify_objects():
    class _LooksLikeRoom:
        def __str__(self) -> str:
            return "https://live.bilibili.com/12345"

    assert parse_room_id("https://live.bilibili.com/12345") == 12345
    assert parse_room_id(12345) == 12345
    assert parse_room_id(_LooksLikeRoom()) == 0


def test_roast_config_does_not_stringify_room_ref_or_platform_objects():
    class _LooksLikePlatform:
        def __str__(self) -> str:
            return "douyin"

    class _LooksLikeRoom:
        def __str__(self) -> str:
            return "https://live.douyin.com/room-42"

    config = RoastConfig.from_mapping(
        {
            "live_platform": _LooksLikePlatform(),
            "live_room_ref": _LooksLikeRoom(),
            "viewer_store_dir": _LooksLikeRoom(),
            "stream_theme": _LooksLikeRoom(),
            "stream_goal": _LooksLikeRoom(),
            "stream_columns": _LooksLikeRoom(),
            "stream_avoid_topics": _LooksLikeRoom(),
        }
    )

    assert normalize_live_platform(_LooksLikePlatform()) == "bilibili"
    assert config.live_platform == "bilibili"
    assert config.live_room_ref == ""
    assert config.live_room_id == 0
    assert config.viewer_store_dir == ""
    assert config.stream_theme == ""
    assert config.stream_goal == ""
    assert config.stream_columns == ""
    assert config.stream_avoid_topics == ""


def test_roast_config_accepts_stream_theme_fields_as_public_text():
    config = RoastConfig.from_mapping(
        {
            "stream_theme": "  战雷陆战练车 + 轻松陪聊  ",
            "stream_goal": "让观众能知道猫猫正在围绕同一场直播营业",
            "stream_columns": "短评、接梗、低压二选一",
            "stream_avoid_topics": "不要催礼物，不要公开审判观众",
        }
    )

    assert config.stream_theme == "战雷陆战练车 + 轻松陪聊"
    assert config.stream_goal == "让观众能知道猫猫正在围绕同一场直播营业"
    assert config.stream_columns == "短评、接梗、低压二选一"
    assert config.stream_avoid_topics == "不要催礼物，不要公开审判观众"

    public = config.to_public_dict()
    assert public["stream_theme"] == "战雷陆战练车 + 轻松陪聊"
    assert public["stream_goal"] == "让观众能知道猫猫正在围绕同一场直播营业"
    assert public["stream_columns"] == "短评、接梗、低压二选一"
    assert public["stream_avoid_topics"] == "不要催礼物，不要公开审判观众"


def test_roast_config_to_public_dict_is_public_projection_not_raw_asdict():
    secret = _SecretLike()
    config = RoastConfig(
        live_platform=secret,  # type: ignore[arg-type]
        live_room_ref="https://live.douyin.com/123?signature=must-not-leak",
        live_room_id=secret,  # type: ignore[arg-type]
        live_mode=secret,  # type: ignore[arg-type]
        live_enabled=secret,  # type: ignore[arg-type]
        developer_tools_enabled=secret,  # type: ignore[arg-type]
        dry_run=secret,  # type: ignore[arg-type]
        roast_once_per_uid=secret,  # type: ignore[arg-type]
        roast_strength=secret,  # type: ignore[arg-type]
        activity_level=secret,  # type: ignore[arg-type]
        co_stream_output_policy="token=policy-secret",
        solo_output_policy=secret,  # type: ignore[arg-type]
        avatar_fetch_timeout_seconds=secret,  # type: ignore[arg-type]
        recent_limit=999999,
        rate_limit_seconds=999999,
        queue_limit=999999,
        safety_auto_stop_enabled=secret,  # type: ignore[arg-type]
        safety_window_seconds=999999,
        safety_pipeline_failure_limit=999999,
        safety_output_failure_limit=999999,
        safety_queue_overflow_limit=999999,
        viewer_store_dir=secret,  # type: ignore[arg-type]
        stream_theme=secret,  # type: ignore[arg-type]
        stream_goal=secret,  # type: ignore[arg-type]
        stream_columns=secret,  # type: ignore[arg-type]
        stream_avoid_topics=secret,  # type: ignore[arg-type]
    )

    public = config.to_public_dict()
    rendered = json.dumps(public, ensure_ascii=False, sort_keys=True)

    assert public["live_platform"] == "bilibili"
    assert public["live_room_id"] == 0
    assert public["live_mode"] == "co_stream"
    assert public["live_enabled"] is False
    assert public["developer_tools_enabled"] is False
    assert public["dry_run"] is False
    assert public["roast_once_per_uid"] is True
    assert public["roast_strength"] == "normal"
    assert public["activity_level"] == "standard"
    assert public["avatar_fetch_timeout_seconds"] == 8.0
    assert public["recent_limit"] == 200
    assert public["rate_limit_seconds"] == 3600
    assert public["queue_limit"] == 100
    assert public["safety_auto_stop_enabled"] is True
    assert public["safety_window_seconds"] == 3600
    assert public["safety_pipeline_failure_limit"] == 100
    assert public["safety_output_failure_limit"] == 100
    assert public["safety_queue_overflow_limit"] == 100
    assert public["viewer_store_dir"] == ""
    assert public["stream_theme"] == ""
    assert public["stream_goal"] == ""
    assert public["stream_columns"] == ""
    assert public["stream_avoid_topics"] == ""
    assert "[redacted]" in rendered
    assert "must-not-leak" not in rendered
    assert "policy-secret" not in rendered


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


def test_danmaku_response_prompt_uses_configured_stream_theme():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(
        config=RoastConfig(
            roast_strength="normal",
            dry_run=True,
            stream_theme="战雷陆战练车 + 轻松陪聊",
            stream_goal="让观众知道猫猫围绕同一场直播营业",
            stream_columns="短评、接梗、低压二选一",
            stream_avoid_topics="不要催礼物，不要公开审判观众",
        )
    )
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="今天玩什么车",
        source="live_danmaku",
        live_mode="solo_stream",
    )
    identity = ViewerIdentity(uid="42", nickname="viewer")
    profile = ViewerProfile(uid="42", nickname="viewer", roast_count=1)

    request = module.build_request(event, identity, profile)

    assert "Current stream theme (private style anchor):" in request.prompt_text
    assert "human_theme: 战雷陆战练车 + 轻松陪聊" in request.prompt_text
    assert "stream_goal: 让观众知道猫猫围绕同一场直播营业" in request.prompt_text
    assert "preferred_columns_or_style: 短评、接梗、低压二选一" in request.prompt_text
    assert "avoid_topics_or_bits: 不要催礼物，不要公开审判观众" in request.prompt_text
    assert "theme_name: NEKO tiny radio patrol" not in request.prompt_text
    assert "configured stream anchor" in request.prompt_text
    assert "human host set this" not in request.prompt_text


def test_danmaku_response_prompt_uses_live_room_title_when_theme_is_blank():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(
        config=RoastConfig(roast_strength="normal", dry_run=True),
        live_room_context={
            "title": "战雷陆战练车：今晚只打轻松局",
            "anchor_name": "水水",
            "live_status": "live",
        },
    )
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="今天玩什么车",
        source="live_danmaku",
        live_mode="solo_stream",
    )
    identity = ViewerIdentity(uid="42", nickname="viewer")
    profile = ViewerProfile(uid="42", nickname="viewer", roast_count=1)

    request = module.build_request(event, identity, profile)

    assert "live_room_title_theme: 战雷陆战练车：今晚只打轻松局" in request.prompt_text
    assert "live_room_anchor_name: 水水" in request.prompt_text
    assert "live_room_status: live" in request.prompt_text
    assert "theme_name: NEKO tiny radio patrol" not in request.prompt_text


def test_danmaku_response_prompt_omits_stale_offline_room_status():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(
        config=RoastConfig(roast_strength="normal", dry_run=True),
        live_room_context={
            "title": "solo queue test room",
            "anchor_name": "NEKO",
            "live_status": "offline",
        },
    )
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="what are we doing",
        source="live_danmaku",
        live_mode="solo_stream",
    )

    request = module.build_request(
        event,
        ViewerIdentity(uid="42", nickname="viewer"),
        ViewerProfile(uid="42", nickname="viewer", roast_count=1),
    )

    assert "live_room_title_theme: solo queue test room" in request.prompt_text
    assert "live_room_status: offline" not in request.prompt_text


def test_solo_host_prompts_do_not_address_unseen_human_operator():
    ctx = SimpleNamespace(
        config=RoastConfig(
            roast_strength="normal",
            dry_run=True,
            live_mode="solo_stream",
            stream_theme="solo queue test room",
        ),
        live_room_context={"live_status": "offline"},
        recent_results=[],
    )
    event = ViewerEvent(
        uid="__neko_warmup__",
        nickname="NEKO",
        danmaku_text="",
        source="warmup_hosting",
        live_mode="solo_stream",
    )
    identity = ViewerIdentity(uid=event.uid, nickname=event.nickname)
    profile = ViewerProfile(uid=event.uid, nickname=event.nickname)

    warmup = WarmupHostingModule()
    warmup.ctx = ctx
    active = ActiveEngagementModule()
    active.ctx = ctx

    warmup_prompt = warmup.build_request(event, identity, profile).prompt_text
    active_prompt = active.build_request(event, identity, profile).prompt_text

    assert "solo_stream_rule: NEKO is the on-stage host" in warmup_prompt
    assert "solo_stream_rule: NEKO is the on-stage host" in active_prompt
    assert "Do not address an unseen human host" in warmup_prompt
    assert "Do not address an unseen human host" in active_prompt
    assert "must perform all hosting actions herself" in warmup_prompt
    assert "must perform all hosting actions herself" in active_prompt
    assert "Never tell or ask an unseen streamer, operator, or current viewer" in warmup_prompt
    assert "Never tell or ask an unseen streamer, operator, or current viewer" in active_prompt
    assert "greet viewers, warm up the room, carry chat, or provide content" in warmup_prompt
    assert "greet viewers, warm up the room, carry chat, or provide content" in active_prompt
    assert "live_room_status: offline" not in warmup_prompt
    assert "live_room_status: offline" not in active_prompt


def test_configured_stream_theme_overrides_live_room_title():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(
        config=RoastConfig(
            roast_strength="normal",
            dry_run=True,
            stream_theme="人工指定：观众点歌陪聊",
        ),
        live_room_context={"title": "战雷陆战练车：今晚只打轻松局"},
    )
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="今天聊什么",
        source="live_danmaku",
        live_mode="solo_stream",
    )

    request = module.build_request(
        event,
        ViewerIdentity(uid="42", nickname="viewer"),
        ViewerProfile(uid="42", nickname="viewer", roast_count=1),
    )

    assert "human_theme: 人工指定：观众点歌陪聊" in request.prompt_text
    assert "live_room_title_theme:" not in request.prompt_text


def test_danmaku_response_prompt_requires_visible_target_anchor():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    event = ViewerEvent(
        uid="42",
        nickname="方块km",
        danmaku_text="别怀疑啦，就是你想的那样",
        source="live_danmaku",
        live_mode="solo_stream",
    )

    request = module.build_request(
        event,
        ViewerIdentity(uid="42", nickname="方块km"),
        ViewerProfile(uid="42", nickname="方块km", roast_count=1),
    )

    assert "Make the target legible like a live streamer without sounding like roll call" in request.prompt_text
    assert "ordinary replies may use the danmaku anchor instead of a name" in request.prompt_text
    assert "anchor_hint: 别怀疑啦" in request.prompt_text
    assert "Do not parrot the current danmaku" in request.prompt_text
    assert "The anchor_hint is for target clarity only" in request.prompt_text
    assert request.metadata["danmaku_anchor_hint"] == "别怀疑啦"
    assert request.metadata["danmaku_viewer_nickname"] == "方块km"
    assert "ordinary replies may use a natural room-facing phrase instead of a full nickname" in request.prompt_text
    assert "Only mention avatar if the current danmaku itself makes that relevant." in request.prompt_text


    assert "Mention at most one viewer nickname" in request.prompt_text
    assert "Never list, greet, or reassure multiple viewers in one line." in request.prompt_text


def test_danmaku_response_prompt_uses_short_address_for_regular_viewer():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    event = ViewerEvent(
        uid="42",
        nickname="\u4e0a\u4e5d\u5929\u63fd\u661f\u8fb0",
        danmaku_text="\u5c0f\u5fc3\u6211\u6320\u4f60\u54e6",
        source="live_danmaku",
        live_mode="solo_stream",
    )

    request = module.build_request(
        event,
        ViewerIdentity(uid="42", nickname="\u4e0a\u4e5d\u5929\u63fd\u661f\u8fb0"),
        ViewerProfile(uid="42", nickname="\u4e0a\u4e5d\u5929\u63fd\u661f\u8fb0", danmaku_count=8),
    )

    assert request.metadata["danmaku_viewer_nickname"] == "\u661f\u8fb0"
    assert request.metadata["danmaku_viewer_raw_nickname"] == "\u4e0a\u4e5d\u5929\u63fd\u661f\u8fb0"
    assert "viewer: \u661f\u8fb0 (UID 42)" in request.prompt_text
    assert "preferred_viewer_address: \u661f\u8fb0" in request.prompt_text
    assert "viewer_full_nickname: \u4e0a\u4e5d\u5929\u63fd\u661f\u8fb0" in request.prompt_text
    assert "ordinary replies may use a natural room-facing phrase instead of a full nickname" in request.prompt_text
    assert "prefer the natural short address" in request.prompt_text


def test_danmaku_response_prompt_allows_natural_target_for_question_reply():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    event = ViewerEvent(
        uid="42",
        nickname="\u5c0f\u738b",
        danmaku_text="\u54ea\u91cc\u9519\u4e86\uff1f",
        source="live_danmaku",
        live_mode="solo_stream",
    )

    request = module.build_request(
        event,
        ViewerIdentity(uid="42", nickname="\u5c0f\u738b"),
        ViewerProfile(uid="42", nickname="\u5c0f\u738b", roast_count=1),
    )

    assert "visible_reply_target: \u5c0f\u738b" in request.prompt_text
    assert "do not force the viewer's full nickname into the first clause" in request.prompt_text
    assert "If the reply would otherwise be ambiguous" in request.prompt_text
    assert request.metadata["danmaku_viewer_nickname"] == "\u5c0f\u738b"


def test_danmaku_response_prompt_profiles_tiny_reactions():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="哈哈哈",
        source="live_danmaku",
        live_mode="solo_stream",
    )

    request = module.build_request(
        event,
        ViewerIdentity(uid="42", nickname="viewer"),
        ViewerProfile(uid="42", nickname="viewer", roast_count=1),
    )

    assert "danmaku_profile: emoji_or_reaction" in request.prompt_text
    assert "reply_shape: mirror_mood_in_a_few_chars" in request.prompt_text
    assert request.metadata["danmaku_profile"] == "emoji_or_reaction"
    assert request.metadata["danmaku_reply_shape"] == "mirror_mood_in_a_few_chars"
    assert "mirror the mood in a few characters" in request.prompt_text
    assert "Do not explain the joke, expand the reaction, or turn it into a topic." in request.prompt_text


def test_danmaku_response_prompt_acknowledges_active_hook_answers():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="C",
        source="live_danmaku",
        live_mode="solo_stream",
        raw={"danmaku_context_hint": "active_hook_answer"},
    )

    request = module.build_request(
        event,
        ViewerIdentity(uid="42", nickname="viewer"),
        ViewerProfile(uid="42", nickname="viewer", roast_count=1),
    )

    assert "danmaku_profile: active_hook_answer" in request.prompt_text
    assert "reply_target: recent_active_hook_answer" in request.prompt_text
    assert "reply_shape: acknowledge_viewer_answer" in request.prompt_text
    assert request.metadata["danmaku_profile"] == "active_hook_answer"
    assert "viewer answer to NEKO's recent active-engagement hook" in request.prompt_text
    assert "Acknowledge the viewer's answer first" in request.prompt_text
    assert "do not ask for another vote" in request.prompt_text


def test_danmaku_response_prompt_answers_questions_directly():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="猫猫你觉得呢？",
        source="live_danmaku",
        live_mode="solo_stream",
    )

    request = module.build_request(
        event,
        ViewerIdentity(uid="42", nickname="viewer"),
        ViewerProfile(uid="42", nickname="viewer", roast_count=1),
    )

    assert "danmaku_profile: question" in request.prompt_text
    assert "reply_target: current_question" in request.prompt_text
    assert "answer it directly first" in request.prompt_text
    assert "Do not dodge into a topic change or ask a new question." in request.prompt_text


def test_danmaku_response_prompt_greets_before_viewer_memory():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="\u665a\u4e0a\u597d",
        source="live_danmaku",
        live_mode="solo_stream",
    )
    profile = ViewerProfile(
        uid="42",
        nickname="viewer",
        danmaku_count=6,
        preference_tags={"chat": 3},
        impression_summary="avatar has neatly lined-up cats",
    )

    request = module.build_request(event, ViewerIdentity(uid="42", nickname="viewer"), profile)

    assert "danmaku_profile: greeting" in request.prompt_text
    assert "reply_target: current_greeting" in request.prompt_text
    assert request.metadata["danmaku_profile"] == "greeting"
    assert "greet the viewer back first" in request.prompt_text
    assert "Do not turn a greeting into an avatar, ID, first-appearance, or profile-memory comment." in request.prompt_text
    assert "never let viewer impression, avatar, nickname, or old memory become the main reply topic" in request.prompt_text
    assert "do not mention avatar or visual impressions unless the current danmaku explicitly asks about them" in request.prompt_text


def test_danmaku_response_prompt_delivers_content_requests_now():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    event = ViewerEvent(
        uid="42",
        nickname="悠怡",
        danmaku_text="\u732b\u732b\u8bb2\u4e2a\u7b11\u8bdd",
        source="live_danmaku",
        live_mode="solo_stream",
    )

    request = module.build_request(
        event,
        ViewerIdentity(uid="42", nickname="悠怡"),
        ViewerProfile(uid="42", nickname="悠怡", roast_count=2),
    )

    assert "danmaku_profile: content_request" in request.prompt_text
    assert "reply_target: requested_content" in request.prompt_text
    assert "reply_shape: deliver_tiny_content_now" in request.prompt_text
    assert request.metadata["danmaku_profile"] == "content_request"
    assert request.metadata["danmaku_reply_shape"] == "deliver_tiny_content_now"
    assert "deliver the content in this reply" in request.prompt_text
    assert "Do not merely acknowledge, promise, or announce that NEKO will do it next." in request.prompt_text
    assert "a promise-only line is a failed reply" in request.prompt_text
    assert "If asked for a joke, include the tiny joke and punchline now." in request.prompt_text
    assert "Expanded request length: one or two short TTS-friendly sentences are allowed." in request.prompt_text
    assert "unless the same reply also contains the requested content" in request.prompt_text
    assert "Avoid opening with 好呀, 可以, 安排, or 来了" in request.prompt_text


def test_danmaku_response_prompt_keeps_idiom_chain_state_from_room_context():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(
        config=RoastConfig(roast_strength="normal", dry_run=True),
        recent_room_danmaku_context=lambda event, limit=6: [
            "room_theme=idiom chain",
            "examples=viewer: \u6211\u4eec\u73a9\u6210\u8bed\u63a5\u9f99",
        ],
    )
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="\u4e00\u5fc3\u4e00\u610f",
        source="live_danmaku",
        live_mode="solo_stream",
    )

    request = module.build_request(
        event,
        ViewerIdentity(uid="42", nickname="viewer"),
        ViewerProfile(uid="42", nickname="viewer"),
    )

    assert "danmaku_profile: idiom_chain_turn" in request.prompt_text
    assert "reply_shape: continue_idiom_chain_now" in request.prompt_text
    assert "do not ask why the viewer said it" in request.prompt_text
    assert request.metadata["danmaku_profile"] == "idiom_chain_turn"


def test_danmaku_response_prompt_marks_unverified_support_claims():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="\u6211\u6295\u5582\u4e86\u8d85\u7ea7\u5927\u706b\u7bad",
        source="live_danmaku",
        live_mode="solo_stream",
        raw={"event_type": "danmaku"},
    )

    request = module.build_request(
        event,
        ViewerIdentity(uid="42", nickname="viewer"),
        ViewerProfile(uid="42", nickname="viewer"),
    )

    assert request.metadata["viewer_claimed_support"] == "unverified_danmaku_claim"
    assert "support_claim_contract: unverified_danmaku_claim_no_thanks" in request.prompt_text
    assert "treat it as a joke/claim, not a real support event" in request.prompt_text
    assert "a brief startled or mildly indignant reaction is allowed" in request.prompt_text


@pytest.mark.parametrize(
    "text",
    [
        "\u6211\u6253\u8d4f\u4e86\u5c0f\u82b1\u82b1",
        "\u9001\u4f60\u4e00\u4e2a\u5c0f\u5fc3\u5fc3",
        "\u8c22\u8c22\u661f\u8fb0\u7684\u8d85\u7ea7\u5927\u706b\u7bad\u548c\u5c0f\u82b1\u82b1",
    ],
)
def test_danmaku_response_marks_contextual_unverified_support_claims(text: str):
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text=text,
        source="live_danmaku",
        live_mode="solo_stream",
        raw={"event_type": "danmaku"},
    )

    request = module.build_request(
        event,
        ViewerIdentity(uid="42", nickname="viewer"),
        ViewerProfile(uid="42", nickname="viewer"),
    )

    assert request.metadata["viewer_claimed_support"] == "unverified_danmaku_claim"


@pytest.mark.parametrize(
    "text",
    [
        "\u793c\u7269\u529f\u80fd\u600e\u4e48\u7528",
        "\u53ef\u4ee5\u68c0\u6d4b\u6253\u8d4f\u3001\u6295\u5582\u4e4b\u7c7b\u7684\u8bcd\u8bed\u5417",
        "\u5982\u679c\u6709\u4eba\u9001\u706b\u7bad\u4f1a\u89e6\u53d1\u5417",
        "\u6211\u9001\u4e86\u4f5c\u4e1a\uff0c\u732b\u732b\u770b\u5417",
    ],
)
def test_danmaku_response_does_not_mark_support_discussion_as_claim(text: str):
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text=text,
        source="live_danmaku",
        live_mode="solo_stream",
        raw={"event_type": "danmaku"},
    )

    request = module.build_request(
        event,
        ViewerIdentity(uid="42", nickname="viewer"),
        ViewerProfile(uid="42", nickname="viewer"),
    )

    assert "viewer_claimed_support" not in request.metadata


def test_danmaku_response_prompt_marks_external_action_requests():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    event = ViewerEvent(
        uid="42",
        nickname="\u590f\u6674\u60e0\u7f8e",
        danmaku_text="\u53bb\u641cbeat on dream on\u8fd9\u9996\u6b4c",
        source="live_danmaku",
        live_mode="solo_stream",
    )

    request = module.build_request(
        event,
        ViewerIdentity(uid="42", nickname="\u590f\u6674\u60e0\u7f8e"),
        ViewerProfile(uid="42", nickname="\u590f\u6674\u60e0\u7f8e", roast_count=1),
    )

    assert "danmaku_profile: external_action_request" in request.prompt_text
    assert "reply_target: viewer_requested_external_action" in request.prompt_text
    assert request.metadata["danmaku_profile"] == "external_action_request"
    assert "Do not pretend NEKO is performing that action" in request.prompt_text
    assert "If no actual tool result is present" in request.prompt_text


def test_danmaku_response_prompt_discourages_stale_comparison_templates():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="\u5f53\u4ee3\u5b66\u751f\u7cbe\u795e\u72b6\u6001",
        source="live_danmaku",
        live_mode="solo_stream",
    )

    request = module.build_request(
        event,
        ViewerIdentity(uid="42", nickname="viewer"),
        ViewerProfile(uid="42", nickname="viewer", roast_count=1),
    )

    assert "Do not use stale comparison templates" in request.prompt_text
    assert "Do not compare the current viewer, students, or the room to master/viewer" in request.prompt_text
    assert "Avoid opening with 'NEKO thinks' or 'cat thinks'" in request.prompt_text


def test_danmaku_response_prompt_allows_room_bridge_length_for_shared_theme():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(
        config=RoastConfig(roast_strength="normal", dry_run=True),
        recent_room_danmaku_context=lambda event, limit=6: [
            "room_theme=choice / preference prompt (3 signals)",
            "room_rule=answer the current viewer first; if it matches the room theme, bridge the theme instead of replying one-by-one",
            "examples=alice: 夜里选小甜食还是热饮？ | viewer: 我选热饮",
        ],
    )
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="我还是想喝热饮，今天有点冷",
        source="live_danmaku",
        live_mode="solo_stream",
    )

    request = module.build_request(
        event,
        ViewerIdentity(uid="42", nickname="viewer"),
        ViewerProfile(uid="42", nickname="viewer", roast_count=2),
    )

    assert "reply_length_mode: room_bridge" in request.prompt_text
    assert "Room bridge length: one compact sentence is preferred" in request.prompt_text
    assert "answer the current viewer; the bridge may only add a tiny room-facing echo" in request.prompt_text
    assert request.metadata["reply_length_mode"] == "room_bridge"
    assert request.metadata["max_reply_chars"] == 48
    assert request.metadata["room_theme"] == "choice / preference prompt"


def test_danmaku_response_prompt_keeps_greeting_short_even_with_room_theme():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(
        config=RoastConfig(roast_strength="normal", dry_run=True),
        recent_room_danmaku_context=lambda event, limit=6: ["room_theme=greetings (3 signals)"],
    )
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="\u665a\u4e0a\u597d",
        source="live_danmaku",
        live_mode="solo_stream",
    )

    request = module.build_request(
        event,
        ViewerIdentity(uid="42", nickname="viewer"),
        ViewerProfile(uid="42", nickname="viewer", roast_count=2),
    )

    assert "reply_length_mode: default" in request.prompt_text
    assert "Room bridge length: one compact sentence is preferred" not in request.prompt_text
    assert "reply_length_mode" not in request.metadata


def test_danmaku_response_prompt_includes_private_viewer_preference_memory():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(
        config=RoastConfig(roast_strength="normal", dry_run=True),
        recent_interaction_context=lambda limit=12: [],
        live_session_memory=None,
        live_events=None,
        live_output_memory=None,
    )
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="这个怎么配置？",
        source="live_danmaku",
        live_mode="solo_stream",
    )
    profile = ViewerProfile(
        uid="42",
        nickname="viewer",
        roast_count=1,
        danmaku_count=3,
        preference_tags={"tech_ai": 2, "questions": 2},
        favorite_topics={"tech_ai": 2},
        running_jokes={"short_helper_mode": 2},
        interaction_style="question",
        response_preference="answer first, then add one light follow-up",
        last_interaction_summary="likes tech/AI, often asks questions",
        impression_summary="likes tech/AI, often asks questions; answer first",
        avoid_guidance="answer before teasing; do not dodge the question",
    )

    request = module.build_request(event, ViewerIdentity(uid="42", nickname="viewer"), profile)

    assert "Viewer impression memory (private guidance)" in request.prompt_text
    assert "viewer_stage: returning_viewer" in request.prompt_text
    assert "profile_confidence: medium" in request.prompt_text
    assert "preference_tags: questions, tech_ai" in request.prompt_text
    assert "top_preferences: questions(2), tech_ai(2)" in request.prompt_text
    assert "favorite_topics: tech_ai" in request.prompt_text
    assert "running_jokes_or_reply_cues: short_helper_mode(2)" in request.prompt_text
    assert "viewer_impression: likes tech/AI, often asks questions; answer first" in request.prompt_text
    assert "avoid_guidance: answer before teasing; do not dodge the question" in request.prompt_text
    assert "response_preference: answer first, then add one light follow-up" in request.prompt_text
    assert "use these hints silently; do not announce stored viewer data" in request.prompt_text


def test_danmaku_response_prompt_allows_requested_target_roast():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    event = ViewerEvent(
        uid="42",
        nickname="\u70b9\u83dc\u4eba",
        danmaku_text="\u732b\u732b\u5410\u69fd\u4e00\u4e0b@\u5c0f\u660e",
        source="live_danmaku",
        live_mode="solo_stream",
    )

    request = module.build_request(
        event,
        ViewerIdentity(uid="42", nickname="\u70b9\u83dc\u4eba"),
        ViewerProfile(uid="42", nickname="\u70b9\u83dc\u4eba", roast_count=1),
    )

    assert "danmaku_profile: target_roast_request" in request.prompt_text
    assert "reply_target: target_viewer_public_roast" in request.prompt_text
    assert "target_roast_viewer: \u5c0f\u660e" in request.prompt_text
    assert request.metadata["danmaku_profile"] == "target_roast_request"
    assert request.metadata["danmaku_target_viewer_nickname"] == "\u5c0f\u660e"
    assert "Do not say NEKO does not know" in request.prompt_text
    assert "Name the target viewer in the first clause" in request.prompt_text
    assert "danmaku_profile: viewer_to_viewer_mention" not in request.prompt_text


def test_danmaku_response_prompt_marks_viewer_to_viewer_mentions():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="@路过的舰长 你看这个",
        source="live_danmaku",
        live_mode="solo_stream",
    )

    request = module.build_request(
        event,
        ViewerIdentity(uid="42", nickname="viewer"),
        ViewerProfile(uid="42", nickname="viewer", roast_count=1),
    )

    assert "danmaku_profile: viewer_to_viewer_mention" in request.prompt_text
    assert "reply_target: public_side_reaction" in request.prompt_text
    assert "do not answer as if it was addressed to NEKO" in request.prompt_text
    assert "do not mediate between viewers" in request.prompt_text


def test_danmaku_response_prompt_keeps_neko_mentions_as_current_target():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="@猫猫 今天像小电台",
        source="live_danmaku",
        live_mode="solo_stream",
    )

    request = module.build_request(
        event,
        ViewerIdentity(uid="42", nickname="viewer"),
        ViewerProfile(uid="42", nickname="viewer", roast_count=1),
    )

    assert "danmaku_profile: viewer_to_viewer_mention" not in request.prompt_text
    assert "reply_target: current_short_line" in request.prompt_text
    assert "Do not answer @other-viewer messages as a call to NEKO unless NEKO is the mentioned target." in request.prompt_text


def test_danmaku_response_prompt_includes_recent_interaction_context():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(
        config=RoastConfig(roast_strength="normal", dry_run=True),
        recent_interaction_context=lambda limit=3: [
            "avatar_roast / live_danmaku from viewer: 第一次来",
            "idle_hosting / idle_hosting: solo quiet-room host beat",
        ],
        viewer_session_context=lambda uid, limit=2: [
            "avatar_roast: 第一次来",
            "danmaku_response: 那你继续说",
        ]
        if uid == "42"
        else [],
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

    assert "Used live material, for anti-repeat only:" in request.prompt_text
    assert "avatar_roast / live_danmaku from viewer: 第一次来" in request.prompt_text
    assert "idle_hosting / idle_hosting: solo quiet-room host beat" in request.prompt_text
    assert "Anti-repeat rule: Treat every line above as already spent material." in request.prompt_text
    assert "Do not continue, summarize, paraphrase, or remix those old lines." in request.prompt_text
    assert "Lines starting with 'NEKO already said' are previous broadcast outputs" in request.prompt_text
    assert "topic_family, host_beat_family, spent_output_family, fun_axis, shape, intent, or reply path" in request.prompt_text
    assert "avoid using the same family or reply path again" in request.prompt_text
    assert "This block is a forbidden-material list" in request.prompt_text
    assert "not context to continue and not a script prefix" in request.prompt_text
    assert "The current danmaku is always the primary target" in request.prompt_text
    assert "Short danmaku should receive a short reply" in request.prompt_text
    assert "current_turn_contract: answer the current danmaku from viewer first; ordinary replies may use a natural room-facing phrase instead of a full nickname" in request.prompt_text
    assert "Target lock: this reply is for the current danmaku from viewer" in request.prompt_text
    assert "If several recent danmaku share a theme, use that theme only as a quiet bridge after answering the current viewer." in request.prompt_text
    assert "satisfy that pending thread now when the current danmaku continues it" in request.prompt_text
    assert "Do not list multiple viewer names; one current target or a natural room-facing phrase is enough." in request.prompt_text
    assert "Same viewer used material, for anti-repeat only:" in request.prompt_text
    assert "danmaku_response: 那你继续说" in request.prompt_text
    assert "Lines starting with 'NEKO already said' are previous outputs to this viewer" in request.prompt_text
    assert "Treat same-viewer history as spent material" in request.prompt_text
    assert "If a line lists spent_output_family, treat that family as already used for this viewer." in request.prompt_text
    assert "Do not repeat this viewer's previous danmaku" in request.prompt_text
    assert "Only continue an old thread if the current danmaku explicitly asks to continue that exact thread." in request.prompt_text
    assert "Do not repeat avatar, ID, or first-appearance comments for this viewer." in request.prompt_text


def test_danmaku_response_prompt_includes_recent_room_danmaku_context():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(
        config=RoastConfig(roast_strength="normal", dry_run=True),
        recent_interaction_context=lambda limit=12: [],
        viewer_session_context=lambda uid, limit=2: [],
        recent_room_danmaku_context=lambda event, limit=6: [
            "room_theme=choice / preference prompt (3 signals)",
            "room_rule=answer the current viewer first; if it matches the room theme, bridge the theme instead of replying one-by-one",
            "room_rule=filter low-value repeats and do not re-ask the same choice or topic prompt",
            "filtered_low_value_danmaku=1",
            "examples=alice: 夜里选小甜食还是热饮？ | carol: 我选热饮",
            "current_danmaku_theme=choice / preference prompt",
        ],
    )

    request = module.build_request(
        ViewerEvent(uid="42", nickname="viewer", danmaku_text="热饮吧", source="live_danmaku", live_mode="solo_stream"),
        ViewerIdentity(uid="42", nickname="viewer"),
        ViewerProfile(uid="42", nickname="viewer", roast_count=1),
    )

    assert "Recent room danmaku context, for topic grouping:" in request.prompt_text
    assert "room_theme=choice / preference prompt" in request.prompt_text
    assert "filtered_low_value_danmaku=1" in request.prompt_text
    assert "examples=alice: 夜里选小甜食还是热饮？ | carol: 我选热饮" in request.prompt_text
    assert "Use this only to understand the current room mood and avoid one-by-one tunnel vision." in request.prompt_text
    assert "If recent danmaku share a theme, bridge that theme in one compact reply instead of asking the same prompt again." in request.prompt_text
    assert "When a room theme exists, synthesize the theme briefly; do not reply to each message separately." in request.prompt_text


def test_danmaku_response_prompt_uses_wider_recent_context_window_by_default():
    requested_limits: list[int] = []

    def recent_context(limit: int = 3) -> list[str]:
        requested_limits.append(limit)
        return [f"danmaku_response / live_danmaku from viewer: old line {index}" for index in range(limit)]

    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(
        config=RoastConfig(roast_strength="normal", dry_run=True),
        recent_interaction_context=recent_context,
        viewer_session_context=lambda uid, limit=2: [],
    )
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="new line",
        source="live_danmaku",
        live_mode="solo_stream",
    )

    request = module.build_request(
        event,
        ViewerIdentity(uid="42", nickname="viewer"),
        ViewerProfile(uid="42", nickname="viewer", roast_count=1),
    )

    assert requested_limits == [12]
    assert "old line 0" in request.prompt_text
    assert "old line 11" in request.prompt_text


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
    assert "Do not direct the streamer/operator/current viewer to greet viewers" in co_stream.prompt_text
    assert "warm up the room, carry chat, provide topics, or help NEKO host" in co_stream.prompt_text
    assert "Do not invent or hard-code streamer relationship labels" in solo.prompt_text
    assert "Do not invent or hard-code streamer relationship labels" in co_stream.prompt_text


def test_danmaku_response_prompt_blocks_previous_reply_pollution():
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(
        config=RoastConfig(roast_strength="normal", dry_run=True),
        recent_interaction_context=lambda limit=3: [
            "danmaku_response / live_danmaku from viewer: previous line / NEKO already said: old reward bit",
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

    assert "Do not inherit their topic, rhythm, sentence length, reward bit, plan, or audience prompt." in request.prompt_text
    assert "Do not reuse the same opening, punchline shape, reward/present bit, plan, audience-suggestion beat, or host beat." in request.prompt_text
    assert "Before writing, compare against NEKO's recent live-output memory." in request.prompt_text
    assert "Do not reuse the same wording, opening, rhythm, punchline, or topic framing as the previous NEKO reply." in request.prompt_text
    assert "Do not paraphrase the previous NEKO reply with different words." in request.prompt_text
    assert "Do not revive an old reward bit, plan, game, audience prompt, or host beat unless the current event explicitly asks for it." in request.prompt_text
    assert "If a recent line and the current draft share the same subject" in request.prompt_text
    assert "Current danmaku wins over recent context." in request.prompt_text
    assert "For one-word or very short danmaku, answer with a tiny reaction." in request.prompt_text
    assert "Do not launch a new show segment, special plan, topic poll, reward bit, or audience-suggestion prompt." in request.prompt_text
    assert "Carrying the room means crisp timing, not monologue, plans, or host-script expansion." in request.prompt_text
    assert "NEKO already said: old reward bit" in request.prompt_text


def test_danmaku_response_prompt_compacts_long_recent_context():
    long_recent_line = (
        "danmaku_response / live_danmaku from viewer: "
        + "old reply material should not be injected back into the prompt " * 5
    )
    long_viewer_line = "danmaku_response: " + "same viewer old joke should not be resumed " * 4
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(
        config=RoastConfig(roast_strength="normal", dry_run=True),
        recent_interaction_context=lambda limit=3: [long_recent_line],
        viewer_session_context=lambda uid, limit=2: [long_viewer_line] if uid == "42" else [],
    )
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="new short line",
        source="live_danmaku",
        live_mode="solo_stream",
    )
    identity = ViewerIdentity(uid="42", nickname="viewer")
    profile = ViewerProfile(uid="42", nickname="viewer", roast_count=1)

    request = module.build_request(event, identity, profile)

    assert "old reply material should not be injected back into the prompt " * 2 not in request.prompt_text
    assert "same viewer old joke should not be resumed " * 2 not in request.prompt_text
    assert "..." in request.prompt_text
    assert "Current danmaku wins over recent context." in request.prompt_text
    assert "Treat same-viewer history as spent material" in request.prompt_text


def test_danmaku_response_prompt_preserves_spent_neko_output_when_context_is_long():
    long_recent_line = (
        "active_engagement / active_engagement: fallback small_challenge tiny_answer "
        + "long route context " * 8
        + " / NEKO already said: keep this old punchline"
    )
    long_viewer_line = (
        "danmaku_response: same viewer long context "
        + "same viewer old context " * 8
        + " / NEKO already said: viewer old joke"
    )
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(
        config=RoastConfig(roast_strength="normal", dry_run=True),
        recent_interaction_context=lambda limit=3: [long_recent_line],
        viewer_session_context=lambda uid, limit=2: [long_viewer_line] if uid == "42" else [],
    )
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="new short line",
        source="live_danmaku",
        live_mode="solo_stream",
    )
    identity = ViewerIdentity(uid="42", nickname="viewer")
    profile = ViewerProfile(uid="42", nickname="viewer", roast_count=1)

    request = module.build_request(event, identity, profile)

    assert "NEKO already said: keep this old punchline" in request.prompt_text
    assert "NEKO already said: viewer old joke" in request.prompt_text
    assert "long route context " * 2 not in request.prompt_text
    assert "same viewer old context " * 2 not in request.prompt_text


def test_live_interaction_prompts_share_short_reply_contract():
    identity = ViewerIdentity(uid="42", nickname="viewer")
    profile = ViewerProfile(uid="42", nickname="viewer", roast_count=1)
    short_contract = "Hard length limit: one sentence, no paragraph, at most 14 Chinese characters or 8 English words."
    host_contract = "Default host length: one compact sentence; occasional two short sentences are allowed for a fun host beat."

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
        "Current stream theme (private style anchor):",
        "theme_name: NEKO tiny radio patrol",
        "continuity_rule: use this only as light flavor; never announce the theme name or explain the format.",
        "variety_rule: rotate motifs; do not force every line to mention radio, desk, paw, weather, or snacks.",
        "Output only the final visible NEKO line; no labels, quotes, JSON, analysis, rule recap, or alternative replies.",
        "Prefer a compact live punchline over explanation, setup, or follow-up commentary.",
        "Do not turn a reply into a host script, segment intro, plan, or audience survey.",
        "Do not append numeric audience calls such as type 1, drop a 1, reply 1, or vote 1/2.",
        "Avoid comma chains; if the draft has too many clauses, cut the weakest side.",
        "Avoid repeated presence checks like anyone here, still here, 有人吗, 还在吗, or 在不在",
        "Avoid empty praise like interesting, has a vibe, has a joke, 有点意思, 有点东西, or 很有梗",
        "Do not use 喵 as the whole punchline or default ending",
        "If the draft needs hidden context, expert knowledge, or a guessed viewer intention",
        "Do not invent a dilemma, punishment, report, trial, labor-camp, public-shaming",
        "Forbidden words: 公开示众, 劳改, 审判, 处刑, 惩罚.",
        "Do not force a technical, game-specific, guide, tutorial, or news title into a fake expert question.",
        "Never output an unfinished choice; do not end with 还是, 或者, or or.",
        "Avoid unclear abstract choices; each option must be ordinary, complete, and immediately understandable.",
        "Keep NEKO's presence cumulative: each line should feel like the same live cat host, not a reset template.",
        "Use tiny recurring motifs sparingly, such as paw, tail, nest, desk, room weather, stamp, patrol, or password.",
        "Switch motif when recent material already used the same tiny scene, object, or callback shape.",
        "Prefer a fresh micro-scene over abstract hosting language.",
        "Never ask viewers to reply with numbers such as 1/2, type 1, drop a 1, or any numeric vote.",
        "If a reply cue is needed, ask viewers to put words in danmaku instead of using numeric prompts.",
    ]
    reply_rules = [
        short_contract,
        "One breath only: no more than 20 Chinese chars or 10 English words when the idea still works.",
        "Do not chain multiple clauses with commas; if the draft has a comma, cut one side.",
        "If the viewer's danmaku is short, answer even shorter.",
        "For one-word or very short danmaku, answer with a tiny reaction.",
        "If recent context was longer than the current danmaku, shrink the reply instead of matching it.",
        "No explanation, no setup, no second sentence, no follow-up question unless the current danmaku asks one.",
        "If the current danmaku clearly answers a recent tiny hook, acknowledge the answer first without repeating the old prompt.",
        "Carry only a tiny emotional echo from recent host material; do not continue old wording or topic by default.",
    ]
    host_rules = [
        host_contract,
        "Solo-stream agency: NEKO is the only on-stage host and must perform all hosting actions herself.",
        "Never tell or ask an unseen streamer, operator, or current viewer to greet the room, warm up the stream, carry the chat, provide topics, or help NEKO host.",
        "A reply cue must be a natural danmaku cue, not a numeric vote or attendance check.",
        "Usually keep the host beat within 36 Chinese chars; a rare flavorful beat may reach about 60.",
        "If the room is quiet, keep the line smaller unless the material itself is especially fun.",
        "One host beat only; if asking, ask one concrete non-numeric question and tell viewers to answer in danmaku.",
        "If recent context was longer than this host beat, do not match its length by default.",
        "No explanation, no setup, no extra follow-up after the concrete hook.",
        "For host beats, make it feel like one bead in a tiny live column: room image, verdict, patrol, weather, password, or challenge.",
        "Do not announce the column name; let the format show through the line.",
        "After a callback-style host beat, leave space for viewer answers instead of adding a second prompt.",
    ]

    for request in [danmaku_request, avatar_request, idle_request, warmup_request, active_request]:
        for rule in common_rules:
            assert rule in request.prompt_text

    for request in [danmaku_request, avatar_request]:
        for rule in reply_rules:
            assert rule in request.prompt_text
        assert "reply_rule: answer the current viewer first" in request.prompt_text
        assert "no_drift_rule: do not ignore the danmaku just to continue the theme." in request.prompt_text
        assert host_contract not in request.prompt_text
        assert "One host beat only; if asking, ask one concrete non-numeric question and tell viewers to answer in danmaku." not in request.prompt_text

    for request in [idle_request, warmup_request, active_request]:
        for rule in host_rules:
            assert rule in request.prompt_text
        assert "host_rule: make idle/warmup/active beats feel like beads from the same tiny show" in request.prompt_text
        assert "host_hook_rule: if asking, use one natural non-numeric danmaku cue" in request.prompt_text
        assert short_contract not in request.prompt_text
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
    assert "do not direct the streamer/operator/current viewer to greet viewers" in co_stream.prompt_text
    assert "warm up the room, carry chat, provide topics, or help NEKO host" in co_stream.prompt_text
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
    assert "Treat UID as a routing identity, not default roast material" in solo.prompt_text
    assert "do not quote raw UID digits unless the nickname itself is missing" in solo.prompt_text
    assert "solo_stream first-appearance priority: current danmaku first" not in co_stream.prompt_text


def test_avatar_roast_prompt_handles_delayed_retry_naturally():
    module = AvatarRoastModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="sharp", dry_run=True))
    identity = ViewerIdentity(uid="42", nickname="viewer", avatar_bytes=b"avatar", avatar_mime="image/png")
    profile = ViewerProfile(uid="42", nickname="viewer")

    request = module.build_request(
        ViewerEvent(
            uid="42",
            nickname="viewer",
            danmaku_text="roast my avatar",
            source="live_danmaku",
            live_mode="solo_stream",
        ),
        identity,
        profile,
    )

    assert "delayed chance to roast" in request.prompt_text
    assert "spontaneous live banter" in request.prompt_text
    assert "never say this is a makeup, retry, missed first roast, or system correction" in request.prompt_text
    assert "satisfy it directly with a natural witty line" in request.prompt_text


def test_avatar_roast_prompt_uses_natural_viewer_address_not_initials():
    module = AvatarRoastModule()
    module.ctx = SimpleNamespace(config=RoastConfig(roast_strength="normal", dry_run=True))
    identity = ViewerIdentity(uid="42", nickname="\u6d45\u971c\u6e05\u97f5-WF")
    profile = ViewerProfile(uid="42", nickname="\u6d45\u971c\u6e05\u97f5-WF", danmaku_count=8)

    request = module.build_request(
        ViewerEvent(
            uid="42",
            nickname="\u6d45\u971c\u6e05\u97f5-WF",
            danmaku_text="\u4e0d\u597d\u5403",
            source="live_danmaku",
            live_mode="solo_stream",
        ),
        identity,
        profile,
    )

    assert "viewer: \u6e05\u97f5 (UID 42)" in request.prompt_text
    assert "viewer_full_nickname: \u6d45\u971c\u6e05\u97f5-WF" in request.prompt_text
    assert "viewer: WF" not in request.prompt_text
    assert "Never invent pinyin initials, Latin initials, or all-letter abbreviations" in request.prompt_text


def test_avatar_roast_prompt_includes_recent_used_material_blocklist():
    module = AvatarRoastModule()
    module.ctx = SimpleNamespace(
        config=RoastConfig(roast_strength="normal", dry_run=True),
        recent_interaction_context=lambda limit=3: [
            "avatar_roast / live_danmaku from viewer: 猫猫先夸了小鱼干",
            "idle_hosting / idle_hosting: solo quiet-room host beat",
        ],
    )
    event = ViewerEvent(uid="42", nickname="viewer", danmaku_text="第一次来", source="live_danmaku", live_mode="solo_stream")
    identity = ViewerIdentity(uid="42", nickname="viewer")
    profile = ViewerProfile(uid="42", nickname="viewer")

    request = module.build_request(event, identity, profile)

    assert "Used live material, for anti-repeat only:" in request.prompt_text
    assert "猫猫先夸了小鱼干" in request.prompt_text
    assert "forbidden-material list" in request.prompt_text
    assert "Do not use the same opening, sentence shape, punchline, or host beat as recent live replies." in request.prompt_text
    assert "Do not revive an old reward bit, plan, game, audience prompt, or host beat" in request.prompt_text


def test_avatar_roast_prompt_includes_same_viewer_used_material_blocklist():
    module = AvatarRoastModule()
    module.ctx = SimpleNamespace(
        config=RoastConfig(roast_strength="normal", dry_run=True),
        recent_interaction_context=lambda limit=3: [],
        viewer_session_context=lambda uid, limit=2: [
            "avatar_roast / live_danmaku from viewer: first entrance / NEKO already said: old avatar bit",
            "danmaku_response / live_danmaku from viewer: one more / spent_output_family=audience_prompt",
        ]
        if uid == "42"
        else [],
    )
    event = ViewerEvent(uid="42", nickname="viewer", danmaku_text="again", source="live_danmaku", live_mode="solo_stream")
    identity = ViewerIdentity(uid="42", nickname="viewer")
    profile = ViewerProfile(uid="42", nickname="viewer")

    request = module.build_request(event, identity, profile)

    assert "Same viewer used material, for anti-repeat only:" in request.prompt_text
    assert "old avatar bit" in request.prompt_text
    assert "spent_output_family=audience_prompt" in request.prompt_text
    assert "Treat same-viewer history as spent material" in request.prompt_text
    assert "Do not repeat avatar, ID, or first-appearance comments for this viewer." in request.prompt_text


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

    assert "Used live material, for anti-repeat only:" in request.prompt_text
    assert "danmaku_response / live_danmaku from viewer: 猫猫在吗" in request.prompt_text
    assert "idle_hosting / idle_hosting: solo quiet-room host beat" in request.prompt_text
    assert "Do not reuse the same opening, punchline shape, reward/present bit, plan, audience-suggestion beat, or host beat." in request.prompt_text
    assert "This block is a forbidden-material list" in request.prompt_text
    assert "Do not paraphrase the previous NEKO reply with different words." in request.prompt_text
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
    assert "You may ask one specific, low-pressure question, but never as a numeric vote." in active_request.prompt_text


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
                "live_column": "NEKO tiny radio",
                "idle_stage": "settle",
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
    assert "NEKO live column: NEKO tiny radio" in request.prompt_text
    assert "idle_stage: settle" in request.prompt_text
    assert "viewer can answer with one small mood word" in request.prompt_text
    assert "Use the host beat reply_affordance as the only reply cue; do not add a second question." in request.prompt_text
    assert "Use the host beat fun_axis as the line's purpose; do not drift into generic hosting." in request.prompt_text


def test_idle_hosting_prompt_can_reuse_meme_knowledge_as_optional_seasoning():
    module = AvatarRoastModule()
    module.ctx = SimpleNamespace(config=RoastConfig(activity_level="standard", dry_run=True))
    event = ViewerEvent(
        uid="__neko_idle__",
        nickname="NEKO",
        source="idle_hosting",
        live_mode="solo_stream",
        raw={
            "host_beat": {
                "key": "idle:soft-lamp-choice",
                "shape": "tiny_choice",
                "fun_axis": "choice",
                "title": "这盏灯更像陪猫猫值班还是陪猫猫偷懒",
                "meme_query": "班味 松弛感",
                "hint": "Offer one object-flavored A/B choice; keep it warm and not worklike.",
                "live_column": "NEKO lamp poll",
                "idle_stage": "column",
                "reply_affordance": "viewer can pick duty or lazy mode",
            }
        },
    )
    identity = ViewerIdentity(uid="__neko_idle__", nickname="NEKO")
    profile = ViewerProfile(uid="__neko_idle__", nickname="NEKO")

    request = module.build_request(event, identity, profile)

    assert "Meme knowledge hints" in request.prompt_text
    assert "松弛感" in request.prompt_text
    assert "If a meme hint is present, treat it as optional seasoning" in request.prompt_text
    assert request.metadata["meme_hint_ids"] == "songchi_gan"
    assert "comfort" in request.metadata["meme_hint_tags"]


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
    assert "one concrete, low-pressure question, but answers must be words in danmaku, not numbers" in request.prompt_text
    assert "Do not pretend a viewer sent a message" in request.prompt_text
    assert "Do not use generic host slogans" in request.prompt_text
    assert "Never address the whole room with broad audience-bait openings like everyone, anyone, chat, 大家, or 你们." in request.prompt_text
    assert "Prefer one tiny observation over a plan, segment, or open-ended topic survey." in request.prompt_text
    assert "Every active engagement line may give viewers one concrete non-numeric danmaku cue" in request.prompt_text
    assert "Use the provided viewer reply path as the only reply cue; do not add a second question." in request.prompt_text
    assert "Use the provided fun axis as the line's purpose; do not drift into generic hosting." in request.prompt_text
    assert "A/B choice by words, one-word answer, tiny stance, or playful yes/no-with-a-side" in request.prompt_text
    assert "Do not use generic Chinese host lines equivalent to" in request.prompt_text
    assert "澶у" not in request.prompt_text
    assert "Do not say special plan, everyone look, next let's, what should we talk about, or tell me what you want." in request.prompt_text
    assert "Do not invent or hard-code streamer relationship labels" in request.prompt_text
    assert "Anti-repeat rule: Treat every line above as already spent material." in request.prompt_text


def test_active_engagement_prompt_treats_recent_reply_path_as_spent_material():
    module = ActiveEngagementModule()
    module.ctx = SimpleNamespace(
        config=RoastConfig(activity_level="active", roast_strength="sharp", dry_run=True),
        recent_interaction_context=lambda limit=3: [
            "active_engagement / active_engagement: fallback small_challenge / reply: viewer can answer in a few words",
        ],
    )
    event = ViewerEvent(uid="__neko_active__", nickname="NEKO", source="active_engagement", live_mode="solo_stream")
    identity = ViewerIdentity(uid=event.uid, nickname=event.nickname)
    profile = ViewerProfile(uid=event.uid, nickname=event.nickname)

    request = module.build_request(event, identity, profile)

    assert "reply: viewer can answer in a few words" in request.prompt_text
    assert "avoid using the same family or reply path again" in request.prompt_text


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
                "key": "bili:room-choice",
                "family": "choice",
                "fun_axis": "choice",
                "shape": "either_or",
                "title": "猫猫今天怎么这么安静",
                "intent": "quick_vote",
                "live_column": "NEKO micro poll",
                "topic_pack": "micro_poll",
                "reply_affordance": "viewer can answer with one side",
                "recent_topic_skip_reason": "similar_topic_title",
                "shape_guard_reason": "recent_shape_streak",
                "hint": "Use this topic as raw material.",
            }
        },
    )
    identity = ViewerIdentity(uid=event.uid, nickname=event.nickname)
    profile = ViewerProfile(uid=event.uid, nickname=event.nickname)

    request = module.build_request(event, identity, profile)

    assert "shape task:" in request.prompt_text
    assert "turn the title into one A/B choice by words" in request.prompt_text
    assert "example pattern:" in request.prompt_text
    assert "two concrete word sides" in request.prompt_text
    assert "intent: word_choice_in_danmaku (internal quick_vote; never use numeric voting)" in request.prompt_text
    assert "NEKO live column: NEKO micro poll" in request.prompt_text
    assert "topic pack: micro_poll" in request.prompt_text
    assert "viewer reply path: viewer can answer with one side" in request.prompt_text
    assert "both options are obvious and ordinary" in request.prompt_text
    assert "Start from a clear anchor" in request.prompt_text
    assert "The final line must be a complete sentence" in request.prompt_text
    assert request.metadata["topic_source"] == "bili_trending"
    assert request.metadata["topic_key"] == "bili:room-choice"
    assert request.metadata["topic_shape"] == "either_or"
    assert request.metadata["topic_family"] == "choice"
    assert request.metadata["topic_fun_axis"] == "choice"
    assert request.metadata["topic_reply_affordance"] == "viewer can answer with one side"
    assert request.metadata["topic_recent_skip_reason"] == "similar_topic_title"
    assert request.metadata["topic_shape_guard_reason"] == "recent_shape_streak"


def test_live_output_quality_blocks_numeric_audience_cta_in_danmaku_reply():
    metadata = {"response_module_hint": "danmaku_response"}

    for text in (
        "\u521a\u8fdb\u6765\u7684\u89c2\u4f17\u6263\u4e2a1\u8ba9\u732b\u732b\u770b\u770b",
        "\u9009\u732b\u732b\u7684\u6263\u4e2a2",
        "\u8fd8\u5728\u7684\u53d1666",
        "\u61c2\u7684\u5237111111",
    ):
        assert needs_quality_fallback(text, metadata) is True
        assert "\u6263" not in safe_fallback_reply(text, metadata)


def test_live_output_quality_blocks_thanks_for_unverified_support_claim():
    metadata = {
        "response_module_hint": "danmaku_response",
        "viewer_claimed_support": "unverified_danmaku_claim",
    }
    text = "\u8c22\u8c22\u661f\u8fb0\u7684\u8d85\u7ea7\u5927\u706b\u7bad\u548c\u5c0f\u82b1\u82b1"

    assert needs_quality_fallback(text, metadata) is True
    fallback = safe_fallback_reply(text, metadata)
    assert "\u8c22\u8c22" not in fallback
    assert "\u611f\u8c22" not in fallback


def test_live_output_quality_blocks_numeric_audience_cta_in_hosting():
    metadata = {"response_module_hint": "active_engagement"}
    text = "\u559c\u6b22\u732b\u732b\u7684\u62531\uff0c\u559c\u6b22\u732b\u72d7\u7684\u62532"

    assert needs_quality_fallback(text, metadata) is True


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


def test_active_engagement_rejects_low_confidence_weird_topics():
    assert not active_topic_rules._is_meaningful_active_topic_text("\u770b\u8fd9\u4e2a\u6838\u7535\u7ad9\u653b\u7565")
    assert active_topic_rules._active_topic_filter_reason("\u770b\u8fd9\u4e2a\u6838\u7535\u7ad9\u653b\u7565") == "low_confidence_topic"
    assert not active_topic_rules._is_meaningful_active_topic_text("\u6cf0\u62c9\u745e\u4e9a\u91cc\u9020\u7535\u8111")
    assert not active_topic_rules._is_meaningful_active_topic_text("\u732b\u732b\u5047\u88c5\u4e13\u5bb6\u61c2\u5f88\u591a")
    assert active_topic_rules._active_topic_filter_reason("\u732b\u732b\u5047\u88c5\u4e13\u5bb6\u61c2\u5f88\u591a") == "low_confidence_topic"
    assert active_topic_rules._active_topic_material_profile("\u6cf0\u62c9\u745e\u4e9a\u91cc\u9020\u7535\u8111") == {}


def test_live_material_filter_rejects_mojibake_and_judgment_language():
    assert not active_topic_rules._is_clean_live_material(
        {
            "title": "鐚尗涓夌鍋囪鎳傚緢澶?",
            "hint": "Make one tiny line.",
            "reply_affordance": "viewer can reply",
        }
    )
    assert not active_topic_rules._is_clean_live_material(
        {
            "title": "\u628a\u8fd9\u79cd\u4eba\u516c\u5f00\u793a\u4f17\u8fd8\u662f\u9001\u53bb\u52b3\u6539",
            "hint": "Make one tiny line.",
            "reply_affordance": "viewer can reply",
        }
    )
    assert active_topic_rules._is_clean_live_material(
        {
            "title": "\u732b\u732b\u7ed9\u7a7a\u6c14\u76d6\u4e00\u679a\u5b89\u9759\u7ae0",
            "hint": "Make one tiny mood-stamp line.",
            "reply_affordance": "viewer can answer with one stamp word",
        }
    )


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
    assert "confident stage presence" in request.prompt_text
    assert "not a rescue attempt for an empty room" in request.prompt_text
    assert "Do not say this is a warmup, test, waiting period, or preparation step." in request.prompt_text
    assert "Use one concrete stream/theme anchor if available before leaving a tiny opening." in request.prompt_text
    assert "Output only NEKO's line" in request.prompt_text


def test_idle_hosting_prompt_treats_gap_as_stage_time_not_dead_air():
    avatar = AvatarRoastModule()
    avatar.ctx = SimpleNamespace(config=RoastConfig(activity_level="active", roast_strength="normal", dry_run=True))
    request = avatar.build_request(
        ViewerEvent(uid="__neko_idle__", nickname="NEKO", source="idle_hosting", live_mode="solo_stream"),
        ViewerIdentity(uid="__neko_idle__", nickname="NEKO"),
        ViewerProfile(uid="__neko_idle__", nickname="NEKO"),
    )

    assert "Treat the idle gap as normal stage time" in request.prompt_text
    assert "room is quiet, empty, cold, or waiting" in request.prompt_text
    assert "while we wait" in request.prompt_text
    assert "since nobody is talking" in request.prompt_text
    assert "warm things up" in request.prompt_text


def test_warmup_hosting_prompt_includes_recent_used_material_blocklist():
    module = WarmupHostingModule()
    module.ctx = SimpleNamespace(
        config=RoastConfig(activity_level="standard", roast_strength="normal", dry_run=True),
        recent_interaction_context=lambda limit=3: [
            "warmup_hosting / warmup_hosting: NEKO opened with a fish snack bit",
            "idle_hosting / idle_hosting: solo quiet-room host beat",
        ],
    )
    event = ViewerEvent(uid="__neko_warmup__", nickname="NEKO", source="warmup_hosting", live_mode="solo_stream")
    identity = ViewerIdentity(uid=event.uid, nickname=event.nickname)
    profile = ViewerProfile(uid=event.uid, nickname=event.nickname)

    request = module.build_request(event, identity, profile)

    assert "Used live material, for anti-repeat only:" in request.prompt_text
    assert "warmup_hosting / warmup_hosting" in request.prompt_text
    assert "NEKO opened" in request.prompt_text
    assert "Do not continue, summarize, paraphrase, or remix those old lines." in request.prompt_text
    assert "Do not repeat the same host beat shape twice in a row" in request.prompt_text
    assert "This block is a forbidden-material list" in request.prompt_text


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


def test_interaction_result_public_dict_sanitizes_public_projection_fields():
    secret = _SecretLike()
    event = ViewerEvent(
        uid=secret,  # type: ignore[arg-type]
        nickname="Cookie: ttwid=must-not-leak",
        avatar_url=secret,  # type: ignore[arg-type]
        danmaku_text="hello token=must-not-leak",
        source="live_danmaku",
        raw={
            "event_type": "gift",
            "gift_name": "signature=gift-secret",
            "gift_count": secret,
            "topic_material": {"title": secret, "hook": "Authorization: Bearer bearer-secret"},
            "host_beat": {"title": "cookie=host-secret"},
        },
    )
    identity = ViewerIdentity(
        uid=secret,  # type: ignore[arg-type]
        nickname="token=identity-secret",
        name=secret,  # type: ignore[arg-type]
        avatar_url=secret,  # type: ignore[arg-type]
        source_url="https://example.test/?token=source-secret",
        error=secret,  # type: ignore[arg-type]
    )
    profile = ViewerProfile(
        uid=secret,  # type: ignore[arg-type]
        nickname="cookie=profile-secret",
        avatar_url=secret,  # type: ignore[arg-type]
        last_result=secret,  # type: ignore[arg-type]
    )
    request = InteractionRequest(
        event=event,
        identity=identity,
        profile=profile,
        prompt_text="internal prompt with token=prompt-secret",
        live_mode=secret,  # type: ignore[arg-type]
        strength=secret,  # type: ignore[arg-type]
        reason=secret,  # type: ignore[arg-type]
        metadata={"danmaku_profile": "token=metadata-secret", "object": secret},
    )
    result = InteractionResult(
        accepted=secret,  # type: ignore[arg-type]
        status=secret,  # type: ignore[arg-type]
        event=event,
        identity=identity,
        profile=profile,
        request=request,
        output="Authorization: Bearer output-secret",
        reason=secret,  # type: ignore[arg-type]
        steps=[PipelineStep(secret, secret, secret)],  # type: ignore[arg-type]
        created_at=secret,  # type: ignore[arg-type]
    )

    public = result.to_public_dict()
    rendered = json.dumps(public, ensure_ascii=False, sort_keys=True)

    assert public["accepted"] is False
    assert public["status"] == "failed"
    assert public["event"]["uid"] == ""
    assert public["request"]["live_mode"] == "co_stream"
    assert public["request"]["strength"] == "normal"
    assert public["steps"] == [{"id": "", "status": "failed", "message": ""}]
    assert "[redacted]" in rendered
    assert "must-not-leak" not in rendered
    assert "bearer-secret" not in rendered
    assert "gift-secret" not in rendered
    assert "host-secret" not in rendered
    assert "identity-secret" not in rendered
    assert "profile-secret" not in rendered
    assert "prompt-secret" not in rendered
    assert "metadata-secret" not in rendered
    assert "output-secret" not in rendered


def test_record_result_stores_sanitized_recent_result_payload():
    runtime = SimpleNamespace(
        recent_results=[],
        recent_sandbox_results=[],
        event_bus=SimpleNamespace(emit=lambda *_args, **_kwargs: None),
        _route_from_result=lambda _payload: "danmaku_response",
        _event_signal_from_result=lambda _payload: "danmaku_signal",
        _spent_output_text=lambda _payload: "",
        _spent_output_families=lambda _output: set(),
    )
    event = ViewerEvent(
        uid="douyin:1",
        nickname="token=must-not-leak",
        danmaku_text="hello",
        source="live_danmaku",
        raw={"event_type": "chat", "topic_material": {"title": "cookie=topic-secret"}},
    )
    result = InteractionResult(
        accepted=True,
        status="dry_run",
        event=event,
        output="signature=output-secret",
        request=InteractionRequest(
            event=event,
            identity=ViewerIdentity(uid="douyin:1", nickname="tester"),
            profile=ViewerProfile(uid="douyin:1", nickname="tester"),
            prompt_text="raw prompt token=prompt-secret",
            live_mode="solo_stream",
            strength="sharp",
            metadata={"danmaku_profile": "Authorization: Bearer profile-secret"},
        ),
    )

    record_result(runtime, result)

    payload = runtime.recent_results[-1]
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert payload["danmaku_profile"] == "[redacted]"
    assert payload["response_module"] == "danmaku_response"
    assert "must-not-leak" not in rendered
    assert "topic-secret" not in rendered
    assert "output-secret" not in rendered
    assert "prompt-secret" not in rendered
    assert "profile-secret" not in rendered


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
        dispatcher_latency_ms=125,
    )

    assert result.to_public_dict()["response_latency_ms"] == 2500
    assert result.to_sandbox_dict()["response_latency_ms"] == 2500
    assert result.to_public_dict()["pipeline_latency_ms"] == 2500
    assert result.to_sandbox_dict()["pipeline_latency_ms"] == 2500
    assert result.to_public_dict()["dispatcher_latency_ms"] == 125
    assert result.to_sandbox_dict()["dispatcher_latency_ms"] == 125


def test_permission_gate_requires_developer_tools_for_sandbox():
    gate = PermissionGate(RoastConfig(developer_tools_enabled=False))

    allowed, reason = gate.allows_source("developer_sandbox")

    assert allowed is False
    assert reason == "developer tools are disabled"

    gate.update(RoastConfig(developer_tools_enabled=True))
    assert gate.allows_source("developer_sandbox") == (True, "")


def test_permission_gate_requires_developer_tools_for_manual_live_simulation():
    gate = PermissionGate(RoastConfig(live_enabled=True, developer_tools_enabled=False))

    assert gate.allows_source("manual_live_simulation") == (False, "developer tools are disabled")

    gate.update(RoastConfig(live_enabled=False, developer_tools_enabled=True))
    assert gate.allows_source("manual_live_simulation") == (False, "live roast is disabled")

    gate.update(RoastConfig(live_enabled=True, developer_tools_enabled=True))
    assert gate.allows_source("manual_live_simulation") == (True, "")
