"""Proactive-vision screenshot staging + inject-before-user-reply tests.

Before: a proactive round used the vision model to look at the screen, but
``finish_proactive_delivery`` committed only the text AIMessage and dropped the
screenshot — so when the user replied, the conversation model had no idea what
was on screen.

Now: that screenshot is stashed in a dedicated single slot
``_proactive_image_to_inject`` and the next user text reply folds it in via
``stream_text`` as the LEADING image of that user HumanMessage.

Caching policy (latest spec): cache the last screenshot whenever the backend
obtained one AND a vision model is available; a new proactive round overwrites /
clears the prior cache; the cache has a short TTL
(``_PROACTIVE_SCREENSHOT_TTL_SECONDS``, currently 60s) and expired frames are not
injected.

Contracts under test:
1. ``OmniOfflineClient.set_proactive_screenshot``: write/clear the isolated slot
   + timestamp; never touch the user's own ``_pending_images`` (sharing it would
   steal the user's next frame — Codex P2).
2. ``OmniOfflineClient.stream_text``: the staged screenshot leads the user's own
   frame(s) (temporal order) and is consumed one-shot; with nothing staged the
   behavior is byte-for-byte the old text-only path.
3. TTL + supersede: a screenshot older than ``_PROACTIVE_SCREENSHOT_TTL_SECONDS``
   (60s), OR one whose AI turn was superseded by a later AI message appended
   through another path (greeting / agent callback via ``prompt_ephemeral``), is
   dropped lazily at injection (Codex P2).
4. ``LLMSessionManager.finish_proactive_delivery(vision_screenshot_b64=...)``:
   stage only on a genuine commit (sid not preempted); ``None`` clears the prior
   cache; a sid mismatch (user took over) short-circuits and never stages a
   screenshot for an undelivered turn.
"""
import asyncio
import os
import sys
import time
from queue import Queue
from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock, MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from utils.llm_client import AIMessage, HumanMessage, SystemMessage
from main_logic.core import LLMSessionManager
from main_logic.omni_offline_client import OmniOfflineClient
from main_logic.session_state import SessionStateMachine


# ─────────────────────────────────────────────────────────────────────────────
# 1. set_proactive_screenshot —— isolated single slot + 与 _pending_images 隔离
# ─────────────────────────────────────────────────────────────────────────────

def _bare_offline() -> OmniOfflineClient:
    """__new__ past __init__; wire up only the slot-related fields."""
    c = OmniOfflineClient.__new__(OmniOfflineClient)
    c._conversation_history = []
    c._pending_images = []
    c._proactive_image_to_inject = None
    c._proactive_image_staged_at = 0.0
    c._proactive_image_history_len = 0
    return c


def test_set_proactive_screenshot_stores_in_isolated_slot():
    c = _bare_offline()
    c.set_proactive_screenshot("SHOT_B64")
    assert c._proactive_image_to_inject == "SHOT_B64"
    # 暂存即打上时间戳，给 TTL 用。
    assert c._proactive_image_staged_at > 0.0
    # 关键隔离：绝不污染用户自己的待发帧队列（守住 Codex P2 约束）。
    assert c._pending_images == []


def test_set_proactive_screenshot_none_and_empty_clear_slot():
    c = _bare_offline()
    c._proactive_image_to_inject = "OLD"
    c._proactive_image_staged_at = 123.0
    c.set_proactive_screenshot(None)
    assert c._proactive_image_to_inject is None
    assert c._proactive_image_staged_at == 0.0  # 时间戳一并清
    # 空串也按"清空"处理（image_b64 or None），不会把空字符串当成一张图。
    c._proactive_image_to_inject = "OLD"
    c.set_proactive_screenshot("")
    assert c._proactive_image_to_inject is None


def test_close_clears_proactive_slot():
    """close() must clear slot + timestamp + marker + _pending_images (no leak)."""
    c = _bare_offline()
    c._is_responding = False
    c._proactive_image_to_inject = "SHOT"
    c._proactive_image_staged_at = 123.0
    c._proactive_image_history_len = 5
    c.llm = None
    c._genai_client = None

    asyncio.run(OmniOfflineClient.close(c))
    assert c._proactive_image_to_inject is None
    assert c._proactive_image_staged_at == 0.0
    assert c._proactive_image_history_len == 0
    assert c._pending_images == []


# ─────────────────────────────────────────────────────────────────────────────
# 2. stream_text —— stage the screenshot as the leading image of the user reply
# ─────────────────────────────────────────────────────────────────────────────

def _make_offline_for_stream(*, vision_model: str = "vm") -> tuple[OmniOfflineClient, list]:
    """Minimal client that reaches stream_text's message build + first astream call.

    ``_astream_visible_with_tools`` is replaced by a stub that captures the
    messages and immediately raises — the built user HumanMessage is appended to
    history and passed to it before the raise, so we can assert on it; the raise
    hits the generic except → break (no retry, no sleep).
    """
    c = OmniOfflineClient.__new__(OmniOfflineClient)
    c._conversation_history = [SystemMessage(content="sys")]
    c._pending_images = []
    c._proactive_image_to_inject = None
    c._proactive_image_staged_at = 0.0
    c._proactive_image_history_len = 0
    # stream_text 在到达 astream stub 前会读 self.llm.max_completion_tokens（focus
    # overrides / summary-budget bump），且 self.llm is None 会被当成「client 已 close」
    # 提前 break。astream 本身被 stub，llm 只当占位，给个可读写 max_completion_tokens
    # 的轻量对象即可。
    c.llm = SimpleNamespace(max_completion_tokens=2000)
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
    assert captured, "astream never called — message did not get built"
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
    """Staged screenshot (the screen she commented on) leads the user's own
    frame — temporal order, so the model doesn't read the earlier screen as the
    user's just-captured frame. Both cleared after consume."""
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
    """Nothing staged, no user frame → plain-text message, identical to the
    pre-change path (no regression)."""
    c, captured = _make_offline_for_stream()

    asyncio.run(c.stream_text("普通一句话"))

    msg = _last_user_message(captured)
    assert msg.content == "普通一句话"  # 纯字符串，非多模态列表
    c.switch_model.assert_not_awaited()


def test_stream_text_drops_expired_screenshot():
    """Staged > 2-min TTL → not injected, plain-text reply, expired slot cleared."""
    from main_logic.omni_offline_client import _PROACTIVE_SCREENSHOT_TTL_SECONDS

    c, captured = _make_offline_for_stream()
    c.set_proactive_screenshot("STALE_B64")
    # 把暂存时刻倒拨到 TTL 之外，模拟用户隔了很久才回。
    c._proactive_image_staged_at = time.monotonic() - (_PROACTIVE_SCREENSHOT_TTL_SECONDS + 5)

    asyncio.run(c.stream_text("现在才回你"))

    msg = _last_user_message(captured)
    assert msg.content == "现在才回你"  # 过期 → 纯文本，无图
    assert c._proactive_image_to_inject is None
    assert c._proactive_image_staged_at == 0.0
    c.switch_model.assert_not_awaited()


def test_stream_text_injects_within_ttl():
    """Within TTL (just staged) → injected. Dual of the expiry case, pinning both
    sides of the TTL boundary."""
    c, captured = _make_offline_for_stream()
    c.set_proactive_screenshot("FRESH_B64")  # staged_at = now

    asyncio.run(c.stream_text("马上回你"))

    msg = _last_user_message(captured)
    assert _image_urls(msg.content) == ["data:image/jpeg;base64,FRESH_B64"]


def test_stream_text_drops_screenshot_superseded_by_later_ai_turn():
    """A later AI turn after staging (e.g. greeting / agent callback via
    prompt_ephemeral, NOT finish_proactive_delivery) supersedes the screenshot —
    it's no longer tied to the last AI turn, so injection drops it. Pins Codex P2:
    another delivery path not clearing the slot still can't leak a stale screen
    into the user's reply."""
    c, captured = _make_offline_for_stream()
    c.set_proactive_screenshot("SCREEN_TALK_B64")  # marker = len([Sys]) = 1
    # 模拟随后一条 prompt_ephemeral 投递的 AI 轮把历史变长（不经 set_*）。
    c._conversation_history.append(AIMessage(content="顺便说，记得喝水哦"))

    asyncio.run(c.stream_text("好的"))

    msg = _last_user_message(captured)
    assert msg.content == "好的"  # 陈旧屏被丢弃 → 纯文本
    assert c._proactive_image_to_inject is None
    assert c._proactive_image_history_len == 0
    c.switch_model.assert_not_awaited()


def test_proactive_injected_image_evicts_through_normal_keep_turns():
    """Once injected, the proactive screenshot is a normal list-content image turn
    and occupies a history image slot governed by the SAME _evict_old_images
    (keep_turns=2) as user frames — no special-casing, no exemption. With two
    newer image turns, the proactive turn's image is stripped (text kept) just
    like any older user image would be."""
    c = _bare_offline()

    def _img_turn(url_tag: str, text: str) -> HumanMessage:
        return HumanMessage(content=[
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{url_tag}"}},
            {"type": "text", "text": text},
        ])

    # Oldest image turn = the proactive-injected reply (image leads, then text),
    # exactly the shape stream_text builds. Then two newer user image turns.
    proactive_turn = _img_turn("PROACTIVE", "这是什么")
    c._conversation_history = [
        SystemMessage(content="sys"),
        proactive_turn,
        AIMessage(content="ai-1"),
        _img_turn("USER1", "看这个"),
        AIMessage(content="ai-2"),
        _img_turn("USER2", "还有这个"),
    ]

    c._evict_old_images()  # keep_turns=2 → keep the two newest image turns

    # Proactive turn is the oldest image turn → image stripped, collapses to text.
    assert c._conversation_history[1].content == "这是什么"
    # The two newer image turns keep their images.
    assert _image_urls(c._conversation_history[3].content) == ["data:image/jpeg;base64,USER1"]
    assert _image_urls(c._conversation_history[5].content) == ["data:image/jpeg;base64,USER2"]


# ─────────────────────────────────────────────────────────────────────────────
# 2b. prompt_ephemeral —— a visible ephemeral reply supersedes the staged screen
#     (covers persist_response=False replies the history-len marker can't see)
# ─────────────────────────────────────────────────────────────────────────────

def _make_offline_for_ephemeral():
    """Minimal client to drive prompt_ephemeral up to its finally block."""
    c = OmniOfflineClient.__new__(OmniOfflineClient)
    c._conversation_history = [SystemMessage(content="sys")]
    c._pending_images = []
    c._proactive_image_to_inject = None
    c._proactive_image_staged_at = 0.0
    c._proactive_image_history_len = 0
    c.model = "m"
    c.vision_model = ""  # no images here, keep model switch out of the path
    c._is_responding = False
    c._prefix_buffer_size = 0
    c.master_name = "M"
    c.lanlan_name = "L"
    c.on_text_delta = AsyncMock()
    c.on_status_message = None
    c.on_response_done = AsyncMock()
    c._begin_reasoning_stream = MagicMock(return_value=1)
    c._notify_reasoning_done = AsyncMock()

    def _set_chunks(chunks):
        async def _fake(messages):
            for ch in chunks:
                yield ch
        c._astream_visible_with_tools = _fake

    return c, _set_chunks


def test_prompt_ephemeral_visible_reply_supersedes_staged_screenshot():
    """The avatar-tap path: a visible persist_response=False reply leaves history
    length unchanged (the marker can't see it), so prompt_ephemeral itself must
    clear the staged screenshot when it commits visible text (Codex P2)."""
    c, set_chunks = _make_offline_for_ephemeral()
    c.set_proactive_screenshot("SCREEN_B64")
    set_chunks([SimpleNamespace(content="戳你一下喵~")])

    committed = asyncio.run(
        c.prompt_ephemeral("======戳头像======", completion_mode="response", persist_response=False)
    )

    assert committed is True
    assert c._proactive_image_to_inject is None
    assert c._proactive_image_staged_at == 0.0
    assert c._proactive_image_history_len == 0


def test_prompt_ephemeral_no_visible_text_keeps_staged_screenshot():
    """An aborted / no-text ephemeral attempt must NOT drop a still-valid staged
    screen (the user saw no new reply)."""
    c, set_chunks = _make_offline_for_ephemeral()
    c.set_proactive_screenshot("SCREEN_B64")
    set_chunks([])  # nothing emitted → content_committed is False

    committed = asyncio.run(
        c.prompt_ephemeral("======戳头像======", completion_mode="response", persist_response=False)
    )

    assert committed is False
    assert c._proactive_image_to_inject == "SCREEN_B64"


# ─────────────────────────────────────────────────────────────────────────────
# 3. finish_proactive_delivery(vision_screenshot_b64=...) —— stage only on commit
# ─────────────────────────────────────────────────────────────────────────────

def _make_mgr() -> LLMSessionManager:
    """Minimal manager assembly reused from test_proactive_action_note.py."""
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
    """Real OmniOfflineClient (__new__) as the session, so the assertions exercise
    the real set_proactive_screenshot + _proactive_image_to_inject slot."""
    sess = OmniOfflineClient.__new__(OmniOfflineClient)
    sess._conversation_history = []
    sess._pending_images = []
    sess._proactive_image_to_inject = None
    sess._proactive_image_staged_at = 0.0
    sess._proactive_image_history_len = 0
    return sess


@pytest.mark.asyncio
async def test_finish_stages_vision_screenshot_on_commit():
    """Screenshot obtained + commit succeeds → it lands in the isolated slot,
    ready for the next user reply to inject."""
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
    """A round with no screenshot (passes None) discards any prior cache, so a
    later screen-unrelated talk never trails a stale image."""
    mgr = _make_mgr()
    mgr.current_speech_id = "s"
    mgr.session = _real_session()
    mgr.session._proactive_image_to_inject = "STALE_OLD_SHOT"

    await LLMSessionManager.finish_proactive_delivery(
        mgr, "我突然想起件事", expected_speech_id="s",
        vision_screenshot_b64=None,
    )

    assert mgr.session._proactive_image_to_inject is None


def test_clear_text_pending_images_also_clears_proactive_slot():
    """Magic-command bypasses (OpenClaw/Qwenpaw) go through _clear_text_pending_images
    instead of stream_text, so that hook must also drop the staged screenshot —
    otherwise it survives and injects into a later unrelated message (Codex P2)."""
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.session = _real_session()
    mgr.session._pending_images = ["user-frame"]
    mgr.session.set_proactive_screenshot("SCREEN_B64")
    assert mgr.session._proactive_image_to_inject == "SCREEN_B64"

    LLMSessionManager._clear_text_pending_images(mgr)

    assert mgr.session._pending_images == []
    assert mgr.session._proactive_image_to_inject is None
    assert mgr.session._proactive_image_staged_at == 0.0


@pytest.mark.asyncio
async def test_finish_does_not_stage_on_sid_mismatch():
    """sid mismatch (user already took over this turn) → finish short-circuits
    and returns False; the screenshot is never staged and the prior slot value is
    not mutated by this orphan undelivered turn."""
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
