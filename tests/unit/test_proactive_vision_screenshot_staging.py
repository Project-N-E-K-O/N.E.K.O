"""主动搭话「以屏幕为素材」截图暂存 + 用户回复前注入 测试。

原本：主动搭话用 vision 模型看屏幕生成台词后，``finish_proactive_delivery``
只把纯文本 AIMessage 写进历史，那张截图当场丢弃——用户回复时对话模型完全
不知道刚才评论的屏幕长什么样。

本特性：把那张截图暂存到一个**独立单槽** ``_proactive_image_to_inject``，
下一条用户 text 回复经 ``stream_text`` 时作为*前导* image_url 折叠进该用户
HumanMessage（"用户说话前加入对话上下文"）。

三层契约：
1. ``OmniOfflineClient.set_proactive_screenshot``：写/清独立单槽，绝不碰用户
   自己的 ``_pending_images``（共用会偷走用户下一帧，Codex P2）。
2. ``OmniOfflineClient.stream_text``：暂存截图排在用户自己的帧*之前*（时间序），
   消费后一次性清空；无暂存时行为与原来完全一致（纯文本消息）。
3. ``LLMSessionManager.finish_proactive_delivery(vision_screenshot_b64=...)``：
   仅在 commit 成功（sid 未被用户抢占）时才暂存；传 ``None`` 清掉旧截图；
   sid 不匹配（用户接管）时整轮短路，绝不为未投递的轮次暂存截图。
"""
import asyncio
import os
import sys
from queue import Queue
from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock, MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from utils.llm_client import HumanMessage, SystemMessage
from main_logic.core import LLMSessionManager
from main_logic.omni_offline_client import OmniOfflineClient
from main_logic.session_state import SessionStateMachine


# ─────────────────────────────────────────────────────────────────────────────
# 1. set_proactive_screenshot —— 独立单槽语义 + 与 _pending_images 隔离
# ─────────────────────────────────────────────────────────────────────────────

def _bare_offline() -> OmniOfflineClient:
    """__new__ 绕过 __init__，仅装上槽相关字段。"""
    c = OmniOfflineClient.__new__(OmniOfflineClient)
    c._pending_images = []
    c._proactive_image_to_inject = None
    return c


def test_set_proactive_screenshot_stores_in_isolated_slot():
    c = _bare_offline()
    c.set_proactive_screenshot("SHOT_B64")
    assert c._proactive_image_to_inject == "SHOT_B64"
    # 关键隔离：绝不污染用户自己的待发帧队列（守住 Codex P2 约束）。
    assert c._pending_images == []


def test_set_proactive_screenshot_none_and_empty_clear_slot():
    c = _bare_offline()
    c._proactive_image_to_inject = "OLD"
    c.set_proactive_screenshot(None)
    assert c._proactive_image_to_inject is None
    # 空串也按"清空"处理（image_b64 or None），不会把空字符串当成一张图。
    c._proactive_image_to_inject = "OLD"
    c.set_proactive_screenshot("")
    assert c._proactive_image_to_inject is None


def test_close_clears_proactive_slot():
    """close() 必须把槽与 _pending_images 一并清掉，不跨实例泄漏。"""
    c = _bare_offline()
    c._conversation_history = []
    c._is_responding = False
    c._proactive_image_to_inject = "SHOT"
    c.llm = None
    c._genai_client = None

    asyncio.run(OmniOfflineClient.close(c))
    assert c._proactive_image_to_inject is None
    assert c._pending_images == []


# ─────────────────────────────────────────────────────────────────────────────
# 2. stream_text —— 暂存截图作为前导 image 注入用户回复
# ─────────────────────────────────────────────────────────────────────────────

def _make_offline_for_stream(*, vision_model: str = "vm") -> tuple[OmniOfflineClient, list]:
    """装一个能跑到 stream_text 消息构建 + 首次 astream 调用的最小 client。

    ``_astream_visible_with_tools`` 被替换成一个捕获 messages 后立刻 raise 的
    桩——构建好的用户 HumanMessage 在 raise 前已 append 进历史并作为第一个参数
    传给它，所以能拿到它做断言；raise 走通用 except 直接 break（不重试、不 sleep）。
    """
    c = OmniOfflineClient.__new__(OmniOfflineClient)
    c._conversation_history = [SystemMessage(content="sys")]
    c._pending_images = []
    c._proactive_image_to_inject = None
    c.model = "m"
    c.vision_model = vision_model
    c.max_response_rerolls = 0
    c.max_response_length = 2000
    c._prefix_buffer_size = 0
    c.on_input_transcript = None
    c.on_text_delta = AsyncMock()
    c.on_status_message = None
    c.on_response_discarded = None
    c.on_response_done = None
    c._begin_reasoning_stream = MagicMock()
    c.switch_model = AsyncMock()

    captured: list = []

    async def _fake_astream_visible(messages, **overrides):
        # messages 此刻 = self._conversation_history，末尾就是刚构建的用户消息。
        captured.append(list(messages))
        raise RuntimeError("stop-after-construction")
        yield  # pragma: no cover —— 标记为 async generator

    c._astream_visible_with_tools = _fake_astream_visible
    return c, captured


def _last_user_message(captured: list) -> HumanMessage:
    assert captured, "astream 从未被调用——消息没构建到位"
    msg = captured[0][-1]
    assert isinstance(msg, HumanMessage)
    return msg


def _image_urls(content: list) -> list[str]:
    return [
        item["image_url"]["url"]
        for item in content
        if isinstance(item, dict) and item.get("type") == "image_url"
    ]


def test_stream_text_injects_proactive_screenshot_before_user_text():
    c, captured = _make_offline_for_stream()
    c.set_proactive_screenshot("PROACTIVE_B64")

    asyncio.run(c.stream_text("这是什么呀"))

    msg = _last_user_message(captured)
    assert isinstance(msg.content, list)
    urls = _image_urls(msg.content)
    assert urls == ["data:image/jpeg;base64,PROACTIVE_B64"]
    # 图在前、文本在后（"用户说话前加入"）。
    assert msg.content[0].get("type") == "image_url"
    assert msg.content[-1] == {"type": "text", "text": "这是什么呀"}
    # 一次性消费：消费后槽清空，绝不再注入后续轮次。
    assert c._proactive_image_to_inject is None
    # 有图 → 切 vision 模型（让对话模型真能看见这张屏）。
    c.switch_model.assert_awaited_once()


def test_stream_text_orders_proactive_before_user_frame():
    """暂存截图（她评论过的屏幕）排在用户自己的帧之前——时间序，避免模型把
    旧屏当成用户刚拍的。两者消费后都清空。"""
    c, captured = _make_offline_for_stream()
    c._pending_images = ["USER_FRAME_B64"]
    c.set_proactive_screenshot("PROACTIVE_B64")

    asyncio.run(c.stream_text("看看这个"))

    msg = _last_user_message(captured)
    urls = _image_urls(msg.content)
    assert urls == [
        "data:image/jpeg;base64,PROACTIVE_B64",
        "data:image/jpeg;base64,USER_FRAME_B64",
    ]
    assert c._proactive_image_to_inject is None
    assert c._pending_images == []


def test_stream_text_without_staging_is_text_only():
    """无暂存截图、无用户帧 → 纯文本消息，行为与改动前完全一致（无回归）。"""
    c, captured = _make_offline_for_stream()

    asyncio.run(c.stream_text("普通一句话"))

    msg = _last_user_message(captured)
    assert msg.content == "普通一句话"  # 纯字符串，非多模态列表
    c.switch_model.assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────────────
# 3. finish_proactive_delivery(vision_screenshot_b64=...) —— commit 成功才暂存
# ─────────────────────────────────────────────────────────────────────────────

def _make_mgr() -> LLMSessionManager:
    """复用 test_proactive_action_note.py 的最小 manager 装配。"""
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.use_tts = True
    mgr.tts_cache_lock = asyncio.Lock()
    mgr.lock = asyncio.Lock()
    mgr._proactive_write_lock = asyncio.Lock()
    mgr.tts_pending_chunks = []
    mgr.tts_request_queue = Queue()
    mgr.tts_response_queue = Queue()
    mgr.tts_thread = MagicMock()
    mgr.tts_thread.is_alive.return_value = True
    mgr.tts_ready = True
    mgr.current_speech_id = None
    mgr._tts_done_queued_for_turn = False
    mgr.lanlan_name = "Test"
    mgr.session = None
    mgr.websocket = None
    mgr.sync_message_queue = Queue()
    mgr._enqueue_tts_text_chunk = MagicMock()
    mgr._respawn_tts_worker = MagicMock()
    mgr._tts_norm_speech_id = None
    mgr.send_lanlan_response = AsyncMock()
    mgr.state = SessionStateMachine(lanlan_name="Test")
    mgr._activity_tracker = MagicMock()
    mgr._current_ai_turn_text = ''
    return mgr


def _real_session() -> OmniOfflineClient:
    """真实 OmniOfflineClient（__new__）当 session：用真的 set_proactive_screenshot
    + 真的 _proactive_image_to_inject 槽，断言才有意义。"""
    sess = OmniOfflineClient.__new__(OmniOfflineClient)
    sess._conversation_history = []
    sess._pending_images = []
    sess._proactive_image_to_inject = None
    return sess


@pytest.mark.asyncio
async def test_finish_stages_vision_screenshot_on_commit():
    """本轮以屏幕为素材投递 + commit 成功 → 截图落到独立槽，等下一条用户回复注入。"""
    mgr = _make_mgr()
    mgr.current_speech_id = "s"
    mgr.session = _real_session()

    result = await LLMSessionManager.finish_proactive_delivery(
        mgr, "你这屏幕上的图好好看～", expected_speech_id="s",
        vision_screenshot_b64="SHOT_B64",
    )

    assert result is True
    assert mgr.session._proactive_image_to_inject == "SHOT_B64"
    # 截图只进独立槽，绝不混进用户的 _pending_images。
    assert mgr.session._pending_images == []
    # 历史里仍只有纯文本 AIMessage（截图不作为图片写历史）。
    assert mgr.session._conversation_history[0].content == "你这屏幕上的图好好看～"


@pytest.mark.asyncio
async def test_finish_clears_stale_screenshot_when_none():
    """非 vision 轮（传 None）→ 清掉上一轮可能遗留的截图，避免与屏幕无关的
    搭话拖着一张陈旧图。"""
    mgr = _make_mgr()
    mgr.current_speech_id = "s"
    mgr.session = _real_session()
    mgr.session._proactive_image_to_inject = "STALE_OLD_SHOT"

    await LLMSessionManager.finish_proactive_delivery(
        mgr, "我突然想起件事", expected_speech_id="s",
        vision_screenshot_b64=None,
    )

    assert mgr.session._proactive_image_to_inject is None


@pytest.mark.asyncio
async def test_finish_does_not_stage_on_sid_mismatch():
    """sid 不匹配（用户已接管本轮）→ finish 短路返回 False，截图绝不暂存，
    旧槽值也不被这条未投递的孤儿轮次篡改。"""
    mgr = _make_mgr()
    mgr.current_speech_id = "s_user"
    mgr.session = _real_session()
    mgr.session._proactive_image_to_inject = "PREEXISTING"

    result = await LLMSessionManager.finish_proactive_delivery(
        mgr, "孤儿 proactive", expected_speech_id="s_proactive",
        vision_screenshot_b64="SHOULD_NOT_STAGE",
    )

    assert result is False
    # 既不写新截图，也不清旧值——整轮在 sid 校验处早 return。
    assert mgr.session._proactive_image_to_inject == "PREEXISTING"
    assert mgr.session._conversation_history == []
