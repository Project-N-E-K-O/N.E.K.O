"""针对主动搭话（proactive）TTS 管线 sid race guard 的单元测试。

覆盖场景：
1. feed_tts_chunk 的 expected_speech_id 语义（向后兼容 + race-safe drop）
2. handle_text_data / handle_output_transcript 的 contextvar-based guard
3. finish_proactive_delivery 的 expected_speech_id 语义
4. contextvar 的 per-task 隔离（核心保证：proactive guard 不会误伤用户轮次）
5. 端到端 race：proactive 流式 + 用户打断，验证 proactive 残留 chunk 被丢、
   用户回复不受影响
"""
import asyncio
import os
import sys
from queue import Queue
from unittest.mock import AsyncMock, MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from main_logic.core import LLMSessionManager, _proactive_expected_sid


def _make_mgr() -> LLMSessionManager:
    """跳过 __init__，直接装配 guard 测试必需的最小属性集。

    __init__ 会加载 config / character data / voice 等外部依赖，对 sid guard
    行为测试没有价值，只会带来环境耦合。
    """
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
    mgr.send_lanlan_response = AsyncMock()
    return mgr


# ─────────────────────────────────────────────────────────────────────────────
# feed_tts_chunk
# ─────────────────────────────────────────────────────────────────────────────

async def test_feed_tts_chunk_no_expected_sid_enqueues():
    """向后兼容：老 caller 不传 expected_speech_id，行为不变。"""
    mgr = _make_mgr()
    mgr.current_speech_id = "s_any"
    await LLMSessionManager.feed_tts_chunk(mgr, "hello")
    mgr._enqueue_tts_text_chunk.assert_called_once_with("s_any", "hello")


async def test_feed_tts_chunk_enqueues_when_sid_matches():
    mgr = _make_mgr()
    mgr.current_speech_id = "s_proactive"
    await LLMSessionManager.feed_tts_chunk(mgr, "hi", expected_speech_id="s_proactive")
    mgr._enqueue_tts_text_chunk.assert_called_once()


async def test_feed_tts_chunk_drops_when_sid_mismatches():
    """关键：proactive 生成期间用户换了 sid，本 chunk 必须丢，不能以新 sid 流入。"""
    mgr = _make_mgr()
    mgr.current_speech_id = "s_user"  # 用户已接管
    await LLMSessionManager.feed_tts_chunk(mgr, "proactive tail", expected_speech_id="s_proactive")
    mgr._enqueue_tts_text_chunk.assert_not_called()
    assert mgr.tts_pending_chunks == []


async def test_feed_tts_chunk_drop_is_atomic_with_enqueue():
    """lock 内判定：保证 check 和 enqueue 不会被交错。"""
    mgr = _make_mgr()
    mgr.current_speech_id = "s_proactive"

    enqueue_calls: list[str] = []

    def _spy_enqueue(sid, text):
        enqueue_calls.append((sid, text))

    mgr._enqueue_tts_text_chunk.side_effect = _spy_enqueue

    async def flipper():
        """在 feed_tts_chunk 执行中翻 sid，看能不能漏掉一次 drop。"""
        # 让 feed_tts_chunk 先进到 lock 里；这里等 lock 再改 sid
        async with mgr.tts_cache_lock:
            mgr.current_speech_id = "s_user"

    # 先触发一次 feed_tts_chunk 并让 flipper 并发；tts_cache_lock 保护 check+enqueue
    await asyncio.gather(
        LLMSessionManager.feed_tts_chunk(mgr, "x", expected_speech_id="s_proactive"),
        flipper(),
    )
    # 不管谁先拿锁：要么 enqueue 成功（s_proactive），要么被 flipper 拦到 drop；
    # 绝不会出现 "check 过了但 enqueue 时 sid 已变" 的脏数据。
    if enqueue_calls:
        assert enqueue_calls[0][0] == "s_proactive"


# ─────────────────────────────────────────────────────────────────────────────
# handle_text_data (text mode) 的 contextvar guard
# ─────────────────────────────────────────────────────────────────────────────

async def test_handle_text_data_no_contextvar_always_passes():
    """用户自己 stream_text：contextvar 为 None，不应 drop。"""
    mgr = _make_mgr()
    mgr.current_speech_id = "s_user"
    await LLMSessionManager.handle_text_data(mgr, "user reply", is_first_chunk=False)
    mgr.send_lanlan_response.assert_called_once()
    mgr._enqueue_tts_text_chunk.assert_called_once()


async def test_handle_text_data_contextvar_match_passes():
    mgr = _make_mgr()
    mgr.current_speech_id = "s_proactive"
    token = _proactive_expected_sid.set("s_proactive")
    try:
        await LLMSessionManager.handle_text_data(mgr, "p", is_first_chunk=False)
    finally:
        _proactive_expected_sid.reset(token)
    mgr.send_lanlan_response.assert_called_once()
    mgr._enqueue_tts_text_chunk.assert_called_once()


async def test_handle_text_data_contextvar_mismatch_drops_all_writes():
    """sid guard 要同时拦前端显示和 TTS —— proactive 文本不能进用户气泡。"""
    mgr = _make_mgr()
    mgr.current_speech_id = "s_user"
    token = _proactive_expected_sid.set("s_proactive")
    try:
        await LLMSessionManager.handle_text_data(mgr, "stale proactive", is_first_chunk=True)
    finally:
        _proactive_expected_sid.reset(token)
    mgr.send_lanlan_response.assert_not_called()
    mgr._enqueue_tts_text_chunk.assert_not_called()


async def test_handle_text_data_contextvar_mismatch_skips_queue_clear():
    """回归保护：is_first_chunk 分支原本会清 TTS 响应队列；drop 路径不该触发。"""
    mgr = _make_mgr()
    mgr.current_speech_id = "s_user"
    mgr.tts_pending_chunks = [("s_user", "user_cached")]
    mgr.tts_response_queue.put(b"user audio bytes")

    token = _proactive_expected_sid.set("s_proactive")
    try:
        await LLMSessionManager.handle_text_data(mgr, "stale", is_first_chunk=True)
    finally:
        _proactive_expected_sid.reset(token)

    # 用户的 pending chunk 和 queue 都不能被动到
    assert mgr.tts_pending_chunks == [("s_user", "user_cached")]
    assert not mgr.tts_response_queue.empty()


# ─────────────────────────────────────────────────────────────────────────────
# handle_output_transcript (voice mode) 的 contextvar guard
# ─────────────────────────────────────────────────────────────────────────────

async def test_handle_output_transcript_no_contextvar_passes():
    mgr = _make_mgr()
    mgr.current_speech_id = "s_user"
    await LLMSessionManager.handle_output_transcript(mgr, "text", is_first_chunk=False)
    mgr.send_lanlan_response.assert_called_once()


async def test_handle_output_transcript_contextvar_mismatch_drops():
    mgr = _make_mgr()
    mgr.current_speech_id = "s_user"
    token = _proactive_expected_sid.set("s_proactive")
    try:
        await LLMSessionManager.handle_output_transcript(mgr, "stale", is_first_chunk=False)
    finally:
        _proactive_expected_sid.reset(token)
    mgr.send_lanlan_response.assert_not_called()
    mgr._enqueue_tts_text_chunk.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# finish_proactive_delivery 的 expected_speech_id
# ─────────────────────────────────────────────────────────────────────────────

async def test_finish_proactive_delivery_no_expected_sid_runs_all():
    """向后兼容：不传 expected_speech_id 照常投递，并返回 True。"""
    mgr = _make_mgr()
    mgr.current_speech_id = "anything"
    mgr.session = MagicMock()
    mgr.session._conversation_history = []
    result = await LLMSessionManager.finish_proactive_delivery(mgr, "done")
    assert result is True
    mgr.send_lanlan_response.assert_called_once()
    assert len(mgr.session._conversation_history) == 1
    assert mgr._tts_done_queued_for_turn is True


async def test_finish_proactive_delivery_sid_match_runs_all():
    mgr = _make_mgr()
    mgr.current_speech_id = "s_proactive"
    mgr.session = MagicMock()
    mgr.session._conversation_history = []
    result = await LLMSessionManager.finish_proactive_delivery(
        mgr, "done", expected_speech_id="s_proactive",
    )
    assert result is True
    mgr.send_lanlan_response.assert_called_once()
    assert len(mgr.session._conversation_history) == 1


async def test_finish_proactive_delivery_sid_mismatch_skips_all_writes():
    """关键：Phase 2 结束→finish 之间用户打断，finish 内所有副作用必须跳过，且返回 False。

    路由层依赖 False 这个返回值来短路 _record_proactive_chat / topic usage /
    surfaced reflection 等下游副作用，避免"未送达内容"被记为已送达。
    """
    mgr = _make_mgr()
    mgr.current_speech_id = "s_user"  # 用户接管
    mgr.session = MagicMock()
    mgr.session._conversation_history = []
    result = await LLMSessionManager.finish_proactive_delivery(
        mgr, "orphan proactive", expected_speech_id="s_proactive",
    )
    assert result is False
    mgr.send_lanlan_response.assert_not_called()
    assert mgr.session._conversation_history == []
    assert mgr._tts_done_queued_for_turn is False
    assert mgr.sync_message_queue.empty()


# ─────────────────────────────────────────────────────────────────────────────
# contextvar per-task 隔离 —— 本次修复的核心正确性前提
# ─────────────────────────────────────────────────────────────────────────────

async def test_contextvar_isolated_across_tasks():
    """一个 task set 的 contextvar 不会被另一个 task 读到。

    这是本次 guard 方案的基石：proactive 设 guard → 只影响 proactive 自己的
    callback 链；用户 stream_text 的回调在另一个 task，永远读到 None。
    """
    observed: dict[str, str | None] = {}

    async def proactive():
        token = _proactive_expected_sid.set("s_proactive")
        try:
            await asyncio.sleep(0)  # 让出
            observed["proactive"] = _proactive_expected_sid.get()
        finally:
            _proactive_expected_sid.reset(token)

    async def user():
        await asyncio.sleep(0)  # 让 proactive 先 set
        observed["user"] = _proactive_expected_sid.get()

    await asyncio.gather(asyncio.create_task(proactive()), asyncio.create_task(user()))
    assert observed["proactive"] == "s_proactive"
    assert observed["user"] is None  # 用户 task 看不到 proactive 的 contextvar


# ─────────────────────────────────────────────────────────────────────────────
# 端到端 race：proactive 流式 + 用户打断
# ─────────────────────────────────────────────────────────────────────────────

async def test_end_to_end_proactive_interrupted_by_user():
    """模拟 bug 现场：proactive 正在 handle_text_data 喂 TTS，用户突然改 sid。

    期望：
    - 打断前的 proactive chunk 正常入队（带 proactive sid）
    - 打断后的 proactive 残留 chunk 被丢（guard 拦截）
    - 用户自己的回复 chunk 正常入队（带 user sid，不受 guard 影响）
    """
    mgr = _make_mgr()
    mgr.current_speech_id = "s_proactive"

    user_started = asyncio.Event()
    proactive_can_continue = asyncio.Event()

    async def proactive_flow():
        token = _proactive_expected_sid.set("s_proactive")
        try:
            # 第一个 chunk：sid 仍是 s_proactive，应入队
            await LLMSessionManager.handle_text_data(mgr, "p_early", is_first_chunk=False)
            # 把控制权让给 user，等它改完 sid
            user_started.set()
            await proactive_can_continue.wait()
            # 第二个 chunk：此时 sid 已被 user 换掉，guard 应拦截
            await LLMSessionManager.handle_text_data(mgr, "p_late", is_first_chunk=False)
        finally:
            _proactive_expected_sid.reset(token)

    async def user_flow():
        await user_started.wait()
        # 模拟 stream_text：改 sid（真实场景还会清 queue，此处测 guard 语义就够）
        mgr.current_speech_id = "s_user"
        # 用户自己的回复 chunk 不在 proactive contextvar 下
        await LLMSessionManager.handle_text_data(mgr, "u_reply", is_first_chunk=False)
        proactive_can_continue.set()

    await asyncio.gather(
        asyncio.create_task(proactive_flow()),
        asyncio.create_task(user_flow()),
    )

    # 核心断言：p_late 被丢，p_early 和 u_reply 各入队一次
    enqueued_texts = [c.args[1] for c in mgr._enqueue_tts_text_chunk.call_args_list]
    assert "p_early" in enqueued_texts
    assert "u_reply" in enqueued_texts
    assert "p_late" not in enqueued_texts
    assert len(enqueued_texts) == 2

    # 入队的 sid 必须和当时的 current_speech_id 对应
    enqueued_sids = {c.args[1]: c.args[0] for c in mgr._enqueue_tts_text_chunk.call_args_list}
    assert enqueued_sids["p_early"] == "s_proactive"
    assert enqueued_sids["u_reply"] == "s_user"
