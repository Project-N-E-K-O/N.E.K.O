from plugin.plugins.neko_roast.adapters.neko_dispatcher import _coalesce_key_for_request
from plugin.plugins.neko_roast.core.contracts import (
    InteractionRequest,
    ViewerEvent,
    ViewerIdentity,
    ViewerProfile,
)
from plugin.plugins.neko_roast.core.live_output_quality import (
    looks_like_unfulfilled_content_request,
)
from plugin.plugins.neko_roast.core.live_output_shape import shape_reply_text
from plugin.plugins.neko_roast.modules.danmaku_response import DanmakuResponseModule


def _hosting_request(*, source: str, beat_key: str) -> InteractionRequest:
    event = ViewerEvent(
        uid="host",
        source=source,
        target_lanlan="YUI",
        raw={"host_beat": {"key": beat_key}},
    )
    return InteractionRequest(
        event=event,
        identity=ViewerIdentity(uid="host", nickname="YUI"),
        profile=ViewerProfile(uid="host", nickname="YUI"),
        prompt_text="host",
        live_mode="solo_stream",
        strength="normal",
    )


def test_viewer_prefix_never_exceeds_reply_limit():
    metadata = {
        "live_reply_contract": "short_tts_line",
        "max_reply_chars": 10,
        "response_module_hint": "danmaku_response",
        "danmaku_profile": "question",
        "danmaku_viewer_nickname": "Viewer",
    }

    shaped, _ = shape_reply_text("abcdefghij", metadata)

    assert len(shaped) <= 10


def test_short_content_with_substantive_answer_is_not_replaced():
    metadata = {"danmaku_profile": "content_request"}

    assert not looks_like_unfulfilled_content_request(
        "\u53ef\u4ee5\uff0c\u7b11\u8bdd\u662f\u4e00\u53ea\u732b\u8d70\u8fdb\u9152\u5427",
        metadata,
    )
    assert looks_like_unfulfilled_content_request(
        "\u53ef\u4ee5\uff0c\u6211\u7ed9\u4f60\u8bb2\u4e2a\u7b11\u8bdd",
        metadata,
    )


def test_hosting_coalesce_key_separates_distinct_beats():
    first = _hosting_request(source="idle_hosting", beat_key="beat-a")
    second = _hosting_request(source="idle_hosting", beat_key="beat-b")
    warmup = _hosting_request(source="warmup_hosting", beat_key="beat-a")

    assert _coalesce_key_for_request(first) != _coalesce_key_for_request(second)
    assert _coalesce_key_for_request(first) != _coalesce_key_for_request(warmup)
    assert _coalesce_key_for_request(first) == _coalesce_key_for_request(first)


def test_generic_english_roast_targets_are_rejected():
    placeholders = (
        "guy",
        "person",
        "viewer",
        "user",
        "someone",
        "somebody",
        "everyone",
        "everybody",
        "anyone",
        "anybody",
    )

    for placeholder in placeholders:
        assert DanmakuResponseModule._target_roast_nickname(
            f"rate that {placeholder}"
        ) == ""
        assert DanmakuResponseModule._target_roast_nickname(
            f"roast @{placeholder}"
        ) == ""
    assert DanmakuResponseModule._target_roast_nickname("\u9510\u8bc4 @\u4ed6") == ""
    assert DanmakuResponseModule._target_roast_nickname("roast that Alice") == "Alice"


def test_generic_chinese_roast_targets_are_rejected():
    placeholders = (
        "\u67d0\u4eba",
        "\u67d0\u4f4d",
        "\u8fd9\u4f4d",
        "\u90a3\u4f4d",
        "\u90a3\u8c01",
        "\u5927\u5bb6",
        "\u6240\u6709\u4eba",
    )

    for placeholder in placeholders:
        assert DanmakuResponseModule._target_roast_nickname(
            f"\u9510\u8bc4 {placeholder}"
        ) == ""
        assert DanmakuResponseModule._target_roast_nickname(
            f"roast @{placeholder}"
        ) == ""

    for request in (
        "评价一下这个直播",
        "吐槽一下这个内容",
        "锐评本场表现",
        "损损那个视频",
        "评价一下那篇文章",
        "锐评一篇作文",
        "锐评这些内容",
        "roast @这些内容",
        "评价那些作品",
        "锐评哪些视频",
        "吐槽这点内容",
        "评价那点表现",
        "锐评 这期节目",
        "锐评 这集电视剧",
        "锐评 @这档综艺",
        "锐评 这期",
        "锐评 那集",
        "锐评 @这篇",
        "锐评 @那期",
        "锐评 文章视频",
        "锐评 @文章视频",
        "评价 代码设计",
        "roast @软件应用",
        "锐评 小明的表现",
        "锐评 @小明的表现",
        "锐评 这期节目中的内容",
    ):
        assert DanmakuResponseModule._target_roast_nickname(request) == ""

    for request in (
        "评价一下那篇文章",
        "锐评一篇作文",
        "锐评这些内容",
        "roast @这些内容",
        "评价那些作品",
        "锐评哪些视频",
        "吐槽这点内容",
        "评价那点表现",
        "锐评 这期节目",
        "锐评 这集电视剧",
        "锐评 @这档综艺",
        "锐评 这期",
        "锐评 那集",
        "锐评 @这篇",
        "锐评 @那期",
        "锐评 文章视频",
        "锐评 @文章视频",
        "评价 代码设计",
        "roast @软件应用",
        "锐评 小明的表现",
        "锐评 @小明的表现",
        "锐评 这期节目中的内容",
    ):
        profile = DanmakuResponseModule._danmaku_profile(request)
        assert profile["kind"] != "target_roast_request"

    assert DanmakuResponseModule._target_roast_nickname("锐评 小明") == "小明"
    assert DanmakuResponseModule._target_roast_nickname("锐评 @小明") == "小明"
