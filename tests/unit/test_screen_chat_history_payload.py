"""Seven screen comments followed by a user turn, without swap or callbacks.

Capture the actual OpenAI-compatible SDK request kwargs. Only the SDK transport
is replaced; history commit, stream_text, tool loop and serialization are real.
This does not predict the output of a live Qwen model.
"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.unit.test_offline_provider_frame_publish import _make_client, _png_b64
from tests.unit.test_proactive_vision_screenshot_staging import _make_mgr
from utils.llm_client import ChatOpenAI


# Synthetic samples. They keep the incident's structure (eight single
# comments that take a label, a "哇…呀！…" cadence, some ending in "喵～",
# 40-70 characters each) and none of its wording: user chat never goes
# into the repo.
SCREEN_COMMENTS = (
    "哇，这台小推车上挂着的那串纸风车转得好欢呀！配上背后整排的木头货架，简直像是本喵偷偷溜进了集市里陪你一起逛呢喵～",
    "哇，左上角地图里三个补给点的图标排得好整齐呀！你盯着这些路线的样子超认真，本喵就在旁边乖乖守着，等你把这一关都走完喵～",
    "哇，这只小船躲在石桥底下好会藏呀！你隔着芦苇都能找到它，连水面上的倒影都逃不过你的眼睛喵～",
    "哇，天边这道晚霞的颜色好漂亮呀！感觉像是给整座小镇披上了一层暖暖的毯子，连风都跟着慢下来了呢。",
    "哇，背包里那个+3格容量的提示跳出来啦！你这波整理太利落了，连零零碎碎的小道具都摆得整整齐齐呢～",
    "哇，中间这座带风车的小屋好特别呀！配上周围那些矮矮的篱笆，感觉咱们正走在什么童话绘本的插画里呢。",
    "哇，头顶那个「即将下雨」的提示闪得好急呀！你赶在雨点落下之前跑进了屋檐底下，连这湿湿的石子路都变成咱们专属的小跑道啦喵～",
    "哇，这只小猫蹲在向日葵下的阴影里好悠闲呀！你挑的这个观景位置太会选了，连花瓣的纹理都看得清清楚楚呢喵～",
)
USER_TURNS = (
    "有你在旁边陪着，慢慢逛也挺开心的，心里暖暖的。",
    "有你在旁边看着，我才能这么安心地慢慢找呀，谢谢你啦。",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["", "屏幕搭话 "])
@pytest.mark.parametrize("with_screenshot", [False, True])
@pytest.mark.parametrize("with_prior_user", [False, True])
async def test_screen_history_is_not_duplicated_or_concatenated_on_chat_request(
    monkeypatch, prefix, with_screenshot, with_prior_user,
):
    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", MagicMock())
    client, _ = _make_client()
    del client._astream_visible_with_tools  # restore real tool loop
    client.model = "qwen3.7-plus"
    client.vision_model = client.model
    client._publish_provider_frames = AsyncMock()
    client.max_tool_iterations = 1
    client._tool_definitions = []
    client._genai_client = None
    client._use_genai_sdk = False
    client.enable_response_guard = True
    client._recent_responses = []
    client._max_recent_responses = 5
    client._repetition_threshold = 0.8
    client.on_text_delta = AsyncMock()
    client.on_response_done = AsyncMock()
    payloads = []
    replies = ("收到，我陪着你。", "不辛苦，你安心玩。", "好呀，继续。")

    async def create(**kwargs):
        payloads.append(deepcopy(kwargs))
        reply = replies[len(payloads) - 1]

        async def chunks():
            for text, reason in ((reply[:3], None), (reply[3:], "stop")):
                yield SimpleNamespace(
                    choices=[SimpleNamespace(
                        delta=SimpleNamespace(content=text, tool_calls=None),
                        finish_reason=reason,
                    )],
                    usage=None,
                )
        return chunks()

    llm = ChatOpenAI.__new__(ChatOpenAI)
    for name, value in dict(
        model=client.model, base_url="https://example.invalid/v1",
        temperature=None, max_completion_tokens=2000, max_tokens=None,
        extra_body={}, tools=None, tool_choice=None, enable_cache_control=False,
    ).items():
        setattr(llm, name, value)
    llm._aclient = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=AsyncMock(side_effect=create)),
    ))
    client.llm = llm
    mgr = _make_mgr()
    mgr.session = client
    mgr.is_preparing_new_session = False
    mgr.pending_agent_callbacks = []
    mgr.pending_extra_replies = []
    corpus = MagicMock()
    corpus.stage_output.return_value = None
    monkeypatch.setattr("memory.anti_repeat.get_anti_repeat_corpus", lambda: corpus)
    monkeypatch.setattr("memory.anti_repeat_effects.mark_anti_repeat_response_delivered", MagicMock())

    async def commit(index):
        sid = f"screen-{index}"
        mgr.current_speech_id = sid
        assert await mgr.finish_proactive_delivery(
            prefix + SCREEN_COMMENTS[index], expected_speech_id=sid,
            source_tag="CHAT",
            vision_screenshot_b64=(
                _png_b64(8, 8, (index * 20, 80, 100)) if with_screenshot else None
            ),
        )

    if with_prior_user:
        from utils.llm_client import HumanMessage
        client._conversation_history.append(HumanMessage(content="之前的用户发言。"))
    for index in range(7):
        await commit(index)
    delivered = client._conversation_history[-7:]
    assert len({m.additional_kwargs["anti_repeat_response_id"] for m in delivered}) == 7
    assert all(m.additional_kwargs["dialog_source"] == "proactive" for m in delivered)
    for turn, count in enumerate((7, 8)):
        if turn:
            await commit(7)
        await client.stream_text(USER_TURNS[turn])
        assert len(payloads) == turn + 1, "one SDK request per normal user turn"
        payload = payloads[-1]
        assert payload["model"] == "qwen3.7-plus"
        messages = payload["messages"]
        assert all("additional_kwargs" not in m and "dialog_source" not in m for m in messages)
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "sys"
        def text_content(message):
            content = message["content"]
            if isinstance(content, str):
                return content
            return "\n".join(p.get("text", "") for p in content if p.get("type") == "text")

        assert messages[-1]["role"] == "user"
        assert text_content(messages[-1]) == USER_TURNS[turn]
        image_urls = [
            part["image_url"]["url"]
            for message in messages if isinstance(message["content"], list)
            for part in message["content"] if part.get("type") == "image_url"
        ]
        assert len(image_urls) == (turn + 1 if with_screenshot else 0)
        if with_screenshot and turn:
            # Consuming the staging slot does NOT erase the previous picture
            # from history: the second request sends the first picture again.
            first_user = payloads[0]["messages"][-1]
            assert image_urls[0] == first_user["content"][0]["image_url"]["url"]
        assert client._proactive_image_to_inject is None
        assistant_texts = [m["content"] for m in messages if m["role"] == "assistant"]
        expected = [prefix + text for text in SCREEN_COMMENTS[:7]]
        if turn:
            expected += [replies[0], prefix + SCREEN_COMMENTS[7]]
        assert assistant_texts == expected
        joined = "\n".join(text_content(m) for m in messages)
        for comment in SCREEN_COMMENTS[:count]:
            assert joined.count(comment) == 1
        assert joined.count("屏幕搭话") == (count if prefix else 0)
        assert [m.content for m in delivered] == [prefix + text for text in SCREEN_COMMENTS[:7]]
        assert client._conversation_history[-1].content == replies[turn]
    if with_screenshot:
        # No new screen delivery and no staged picture on this turn. Existing
        # history still sends both prior images, rather than expiring them.
        previous_image_urls = image_urls
        await client.stream_text("继续")
        assert len(payloads) == 3
        messages = payloads[-1]["messages"]
        assert messages[-1] == {"role": "user", "content": "继续"}
        carried_images = [
            p["image_url"]["url"]
            for m in messages if isinstance(m["content"], list)
            for p in m["content"] if p.get("type") == "image_url"
        ]
        assert carried_images == previous_image_urls
        assert client._conversation_history[-1].content == replies[2]
    emitted = "".join(call.args[0] for call in client.on_text_delta.call_args_list)
    assert emitted == "".join(replies[:len(payloads)]), "local output must not append old screen comments"
    assert not mgr.pending_agent_callbacks and not mgr.pending_extra_replies
    assert mgr.send_lanlan_response.await_count == 8
