import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from main_logic.asr_client.lifecycle import VoiceIngressToken, VoiceTurnToken
from main_logic.asr_client.transcript import (
    TranscriptDispatcher,
    TranscriptEnvelope,
)
from main_logic.core import LLMSessionManager
from main_logic.omni_offline_client import OmniOfflineClient
from main_logic.omni_realtime_client import OmniRealtimeClient


pytestmark = pytest.mark.asyncio


def _transcript_envelope(turn_id: int) -> TranscriptEnvelope:
    token = VoiceTurnToken(
        ingress=VoiceIngressToken(1, "socket", 2, 3, 4),
        turn_id=turn_id,
    )
    return TranscriptEnvelope(token, "qwen", f"turn-{turn_id}")


async def test_offline_multimodal_submit_suppresses_duplicate_transcript() -> None:
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._pending_images = []
    client._multimodal_submit_lock = asyncio.Lock()
    client.stream_text = AsyncMock()

    assert await client.submit_multimodal_turn(
        "look",
        "raw-image",
        turn_id="turn-1",
    ) is True

    kwargs = client.stream_text.await_args.kwargs
    # 本轮的帧是 invocation-local 的，绝不进 session 级的一次性附件队列。
    assert kwargs["turn_images"] == ("raw-image",)
    assert client._pending_images == []
    callback = kwargs["input_transcript_callback"]
    assert await callback("look") is None

    await client.submit_external_voice_turn(
        "what about the one on the left",
        turn_id="turn-2",
    )

    assert client.stream_text.await_count == 2
    followup = client.stream_text.await_args
    assert followup.args == ("what about the one on the left",)
    assert await followup.kwargs["input_transcript_callback"]("followup") is None


async def test_offline_multimodal_failure_leaves_the_attachment_queue_alone() -> None:
    """A failed ASR turn cannot disturb an unrelated user attachment."""
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    prior_equal_image = "".join(["raw", "-image"])
    turn_image = "".join(["raw-", "image"])
    assert prior_equal_image == turn_image
    client._pending_images = [prior_equal_image]
    client._multimodal_submit_lock = asyncio.Lock()
    client.stream_text = AsyncMock(side_effect=RuntimeError("request failed"))

    with pytest.raises(RuntimeError, match="request failed"):
        await client.submit_multimodal_turn(
            "look",
            turn_image,
            turn_id="turn-1",
        )

    # 本轮帧从没进过共享队列；取走的那段用户附件在失败后原样放回，不会因为
    # 字节相同被误删。
    assert client._pending_images == [prior_equal_image]


async def test_offline_interruption_cancels_submit_during_model_switch() -> None:
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._pending_images = []
    client._multimodal_submit_lock = asyncio.Lock()
    client._conversation_history = []
    client._user_language_provider = None
    client._is_responding = False
    client._begin_reasoning_stream = MagicMock()
    client.on_response_discarded = None
    client.model = "text-model"
    client.vision_model = "vision-model"
    switch_started = asyncio.Event()

    async def block_model_switch(*_args, **_kwargs) -> None:
        switch_started.set()
        await asyncio.Event().wait()

    client.switch_model = AsyncMock(side_effect=block_model_switch)
    submit_task = asyncio.create_task(
        client.submit_multimodal_turn(
            "look",
            "raw-image",
            turn_id="turn-1",
        )
    )
    await asyncio.wait_for(switch_started.wait(), 1.0)

    assert client._is_responding is False
    await client.handle_interruption()

    assert await submit_task is False
    assert client._external_voice_submit_task is None
    assert client._pending_images == []


async def test_offline_close_cancels_submit_before_clearing_session_state() -> None:
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._pending_images = []
    client._multimodal_submit_lock = asyncio.Lock()
    client._conversation_history = []
    client._user_language_provider = None
    client._is_responding = False
    client._begin_reasoning_stream = MagicMock()
    client.on_response_discarded = None
    client.model = "text-model"
    client.vision_model = "vision-model"
    client._proactive_image_to_inject = None
    client._proactive_image_staged_at = 0.0
    client._proactive_image_history_len = 0
    client.llm = None
    client._genai_client = None
    client._genai_tools_unsupported = False
    switch_started = asyncio.Event()

    async def block_model_switch(*_args, **_kwargs) -> None:
        switch_started.set()
        await asyncio.Event().wait()

    client.switch_model = AsyncMock(side_effect=block_model_switch)
    submit_task = asyncio.create_task(
        client.submit_multimodal_turn(
            "look",
            "raw-image",
            turn_id="turn-1",
        )
    )
    await asyncio.wait_for(switch_started.wait(), 1.0)

    await client.close()

    assert await submit_task is False
    assert client._external_voice_submit_task is None
    assert client._pending_images == []
    assert client._conversation_history == []
    assert client.llm is None


async def test_child_cancel_echo_does_not_stop_serial_transcript_worker() -> None:
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._pending_images = []
    client._multimodal_submit_lock = asyncio.Lock()
    client._conversation_history = []
    client._user_language_provider = None
    client._is_responding = False
    client._begin_reasoning_stream = MagicMock()
    client.on_response_discarded = None
    client.model = "text-model"
    client.vision_model = "vision-model"
    switch_started = asyncio.Event()
    delivered: list[int] = []
    injection_failures: list[int] = []

    async def block_model_switch(*_args, **_kwargs) -> None:
        switch_started.set()
        await asyncio.Event().wait()

    client.switch_model = AsyncMock(side_effect=block_model_switch)

    async def dispatch(envelope: TranscriptEnvelope) -> None:
        turn_id = envelope.turn_token.turn_id
        if turn_id == 1:
            try:
                was_delivered = await client.submit_multimodal_turn(
                    envelope.text,
                    "raw-image",
                    turn_id="turn-1",
                )
            except Exception:
                injection_failures.append(turn_id)
                raise
            if not was_delivered:
                return
        delivered.append(turn_id)

    dispatcher = TranscriptDispatcher(dispatch, capacity=2)
    first = _transcript_envelope(1)
    second = _transcript_envelope(2)
    assert dispatcher.try_reserve(first.final_key)
    assert dispatcher.try_reserve(second.final_key)
    dispatcher.submit(first)
    dispatcher.submit(second)
    await asyncio.wait_for(switch_started.wait(), 1.0)

    await client.handle_interruption()
    await asyncio.wait_for(dispatcher.wait_idle(), 1.0)

    assert delivered == [2]
    assert injection_failures == []
    assert dispatcher._worker is not None
    assert not dispatcher._worker.done()
    worker = dispatcher._worker
    dispatcher.invalidate_all()
    await worker


async def test_parent_submit_cancellation_remains_control_cancellation() -> None:
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._pending_images = []
    client._multimodal_submit_lock = asyncio.Lock()
    client._conversation_history = []
    client._user_language_provider = None
    client._is_responding = False
    client._begin_reasoning_stream = MagicMock()
    client.on_response_discarded = None
    client.model = "text-model"
    client.vision_model = "vision-model"
    switch_started = asyncio.Event()

    async def block_model_switch(*_args, **_kwargs) -> None:
        switch_started.set()
        await asyncio.Event().wait()

    client.switch_model = AsyncMock(side_effect=block_model_switch)
    submit_task = asyncio.create_task(
        client.submit_multimodal_turn(
            "look",
            "raw-image",
            turn_id="turn-1",
        )
    )
    await asyncio.wait_for(switch_started.wait(), 1.0)

    submit_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await submit_task
    assert client._external_voice_submit_task is None
    assert client._pending_images == []


async def test_handoff_candidate_drops_a_free_voice_after_region_flip() -> None:
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lanlan_name = "Test"
    manager.memory_server_port = 48912
    manager._config_manager = SimpleNamespace(
        aensure_region_resolved=AsyncMock(),
        aget_core_config=AsyncMock(
            return_value={
                "CORE_URL": "wss://www.lanlan.app/core",
                "DISABLE_TTS": False,
            }
        ),
        aget_model_api_config=AsyncMock(
            side_effect=[
                {"model": "text"},
                {"model": "vision"},
            ]
        ),
    )
    manager._drop_free_voice_on_route_flip = MagicMock()
    manager._register_builtin_tools = MagicMock()
    manager.tool_registry = SimpleNamespace(all=MagicMock(return_value=[]))
    candidate = SimpleNamespace(connect=AsyncMock(), close=AsyncMock())
    manager._create_offline_vlm_client = MagicMock(return_value=candidate)
    manager._snapshot_next_session_context_messages = MagicMock(return_value=[])
    manager._build_initial_prompt = AsyncMock(return_value="prompt")
    manager._start_session_fetch_new_dialog = AsyncMock(return_value="memory")
    manager._convert_cache_to_str = MagicMock(return_value="")
    manager._bind_session_lifecycle_callbacks = MagicMock()

    built, context_count = await manager._create_offline_vlm_handoff_candidate(
        cached_turns=[],
        previous_core_url="wss://www.lanlan.tech/core",
    )

    assert built is candidate
    assert context_count == 0
    manager._drop_free_voice_on_route_flip.assert_called_once_with(
        "wss://www.lanlan.tech/core",
        "wss://www.lanlan.app/core",
    )
    candidate.connect.assert_awaited_once_with("promptmemory", native_audio=False)


@pytest.mark.parametrize("submit_fails", [False, True])
async def test_two_phase_handoff_keeps_audio_input_and_asr_state_alive(
    submit_fails: bool,
) -> None:
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lanlan_name = "Test"
    manager.input_mode = "audio"
    manager.response_backend = "realtime"
    manager._asr_route_mode = "independent"
    manager._independent_asr_provider = "qwen"
    manager._multimodal_handoff_lock = asyncio.Lock()
    manager._core_voice_session_swap_lock = asyncio.Lock()
    manager._core_voice_session_swap_barrier_timeout_s = 1.0
    manager.lock = asyncio.Lock()
    manager.message_cache_for_new_session = []
    manager.is_preparing_new_session = True
    manager.summary_triggered_time = object()
    manager._reset_preparation_state = AsyncMock()
    manager._cleanup_pending_session_resources = AsyncMock()
    manager.ensure_tts_pipeline_alive = AsyncMock()
    manager._sync_tools_to_active_session = AsyncMock()
    manager._consume_next_session_context_messages = MagicMock()

    old_session = SimpleNamespace(
        base_url="wss://www.lanlan.tech/core",
        close=AsyncMock(),
    )
    manager.session = old_session
    listener_cancelled = asyncio.Event()

    async def old_listener() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            listener_cancelled.set()
            raise

    manager.message_handler_task = asyncio.create_task(old_listener())
    await asyncio.sleep(0)

    candidate = SimpleNamespace(
        handle_messages=AsyncMock(),
        submit_multimodal_turn=AsyncMock(
            side_effect=(
                RuntimeError("vlm request failed") if submit_fails else None
            )
        ),
        close=AsyncMock(),
    )
    manager._create_offline_vlm_handoff_candidate = AsyncMock(
        return_value=(candidate, 0)
    )
    prior_cache = [{"role": "Test", "text": "earlier reply"}]
    turn = SimpleNamespace(
        transcript="what is this",
        images=("raw-image",),
        turn_id="turn-1",
    )

    handoff = manager._handoff_to_offline_vlm_and_submit(
        turn,
        expected_session=old_session,
        prepared_session=old_session,
        operation_is_current=lambda: True,
        cached_turns_before_final=prior_cache,
    )
    if submit_fails:
        with pytest.raises(RuntimeError, match="vlm request failed"):
            await handoff
        delivered = None
    else:
        delivered = await handoff

    assert delivered is (None if submit_fails else True)
    assert listener_cancelled.is_set()
    old_session.close.assert_awaited_once_with()
    assert manager.session is candidate
    assert manager.input_mode == "audio"
    assert manager._asr_route_mode == "independent"
    assert manager._independent_asr_provider == "qwen"
    assert manager.response_backend == "offline_vlm"
    assert manager.use_tts is True
    manager._reset_preparation_state.assert_awaited_once_with(
        clear_main_cache=False
    )
    manager._cleanup_pending_session_resources.assert_awaited_once_with()
    manager._create_offline_vlm_handoff_candidate.assert_awaited_once_with(
        cached_turns=prior_cache,
        previous_core_url="wss://www.lanlan.tech/core",
    )
    manager.ensure_tts_pipeline_alive.assert_awaited_once_with()
    candidate.submit_multimodal_turn.assert_awaited_once_with(
        "what is this",
        ("raw-image",),
        turn_id="turn-1",
    )
    candidate.close.assert_not_awaited()


async def test_handoff_candidate_failure_preserves_realtime_session() -> None:
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lanlan_name = "Test"
    manager._multimodal_handoff_lock = asyncio.Lock()
    manager._core_voice_session_swap_lock = asyncio.Lock()
    manager._core_voice_session_swap_barrier_timeout_s = 1.0
    manager._reset_preparation_state = AsyncMock()
    manager._cleanup_pending_session_resources = AsyncMock()
    old_session = SimpleNamespace(close=AsyncMock())
    manager.session = old_session
    manager._close_independent_asr = AsyncMock()
    manager._create_offline_vlm_handoff_candidate = AsyncMock(
        side_effect=RuntimeError("vision unavailable")
    )
    turn = SimpleNamespace(
        transcript="what is this",
        images=("raw-image",),
        turn_id="turn-1",
    )

    delivered = await manager._handoff_to_offline_vlm_and_submit(
        turn,
        expected_session=old_session,
        prepared_session=old_session,
        operation_is_current=lambda: True,
        cached_turns_before_final=[],
    )

    assert delivered is False
    assert manager.session is old_session
    old_session.close.assert_not_awaited()
    manager._close_independent_asr.assert_not_awaited()


async def test_handoff_listener_cancel_timeout_fail_closes_active_session() -> None:
    """A listener that swallows CancelledError must not wedge the swap lock."""
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lanlan_name = "Test"
    manager._multimodal_handoff_lock = asyncio.Lock()
    manager._core_voice_session_swap_lock = asyncio.Lock()
    manager._core_voice_session_swap_barrier_timeout_s = 1.0
    manager.lock = asyncio.Lock()
    manager.is_active = True
    manager.session_ready = True
    manager._reset_preparation_state = AsyncMock()
    manager._cleanup_pending_session_resources = AsyncMock()
    teardown_order = []
    manager._close_independent_asr = AsyncMock(
        side_effect=lambda **_kwargs: teardown_order.append("close_asr")
    )
    manager.send_session_ended_by_server = AsyncMock()
    manager.send_session_ended_by_server.side_effect = lambda: teardown_order.append(
        "session_ended"
    )

    listener_cancelled = asyncio.Event()
    listener_release = asyncio.Event()
    old_session_closed = asyncio.Event()

    async def close_old_session() -> None:
        old_session_closed.set()

    old_session = SimpleNamespace(
        close=AsyncMock(side_effect=close_old_session),
    )
    manager.session = old_session

    async def stuck_old_listener() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            listener_cancelled.set()
            await listener_release.wait()

    old_listener = asyncio.create_task(stuck_old_listener())
    manager.message_handler_task = old_listener
    await asyncio.sleep(0)

    candidate = SimpleNamespace(
        close=AsyncMock(),
        submit_multimodal_turn=AsyncMock(),
    )
    manager._create_offline_vlm_handoff_candidate = AsyncMock(
        return_value=(candidate, 0)
    )
    turn = SimpleNamespace(
        transcript="what is this",
        images=("raw-image",),
        turn_id="turn-1",
    )
    real_wait_for = asyncio.wait_for
    # 不再 monkeypatch wait_for：这个 listener 真的会吞掉 CancelledError 并挂住，
    # 所以超时必须由被测代码自己达成。伪造 TimeoutError 恰好会掩盖「wait_for 会
    # 等取消完成、因而永远不抛」这个真实行为（Codex P1）。
    manager._core_voice_listener_cancel_timeout_s = 0.05

    delivered = await real_wait_for(
        manager._handoff_to_offline_vlm_and_submit(
            turn,
            expected_session=old_session,
            prepared_session=old_session,
            operation_is_current=lambda: True,
            cached_turns_before_final=[],
        ),
        5.0,
    )

    assert delivered is False
    await real_wait_for(listener_cancelled.wait(), 1.0)
    assert manager.session is None
    assert manager.message_handler_task is None
    assert manager.is_active is False
    assert manager.session_ready is False
    manager._close_independent_asr.assert_awaited_once_with(
        next_route_mode="blocked"
    )
    manager.send_session_ended_by_server.assert_awaited_once_with()
    assert teardown_order == ["close_asr", "session_ended"]
    candidate.submit_multimodal_turn.assert_not_awaited()
    candidate.close.assert_awaited_once_with()

    # 孤儿会话必须**立刻**关，不能等那个已经证明停不下来的 listener：
    # 否则 WebSocket 永远开着，脱缰的 listener 还会在 Core 宣告会话结束之后继续
    # 回调（Codex P1）。
    await real_wait_for(old_session_closed.wait(), 1.0)
    old_session.close.assert_awaited_once_with()

    listener_release.set()


async def test_new_user_turn_during_candidate_connect_cancels_old_handoff() -> None:
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lanlan_name = "Test"
    manager._multimodal_handoff_lock = asyncio.Lock()
    manager._core_voice_session_swap_lock = asyncio.Lock()
    manager._core_voice_session_swap_barrier_timeout_s = 1.0
    manager.lock = asyncio.Lock()
    manager._reset_preparation_state = AsyncMock()
    manager._cleanup_pending_session_resources = AsyncMock()
    old_session = SimpleNamespace(close=AsyncMock())
    manager.session = old_session
    manager.message_handler_task = None
    candidate = SimpleNamespace(
        close=AsyncMock(),
        submit_multimodal_turn=AsyncMock(),
    )
    connect_started = asyncio.Event()
    release_connect = asyncio.Event()

    async def connect_candidate(*, cached_turns, previous_core_url):
        del cached_turns, previous_core_url
        connect_started.set()
        await release_connect.wait()
        return candidate, 0

    manager._create_offline_vlm_handoff_candidate = AsyncMock(
        side_effect=connect_candidate
    )
    turn_owned = True
    turn = SimpleNamespace(
        transcript="old turn",
        images=("old-frame",),
        turn_id="turn-old",
    )
    task = asyncio.create_task(
        manager._handoff_to_offline_vlm_and_submit(
            turn,
            expected_session=old_session,
            prepared_session=old_session,
            operation_is_current=lambda: turn_owned,
            cached_turns_before_final=[],
        )
    )
    await asyncio.wait_for(connect_started.wait(), 1.0)

    # A newer prepare synchronously drops the old Core turn record, which is
    # represented by this ownership fence becoming false.
    turn_owned = False
    release_connect.set()

    assert await asyncio.wait_for(task, 1.0) is False
    assert manager.session is old_session
    old_session.close.assert_not_awaited()
    candidate.submit_multimodal_turn.assert_not_awaited()
    candidate.close.assert_awaited_once_with()


async def test_new_user_turn_during_listener_cancel_restores_realtime_listener() -> None:
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lanlan_name = "Test"
    manager._multimodal_handoff_lock = asyncio.Lock()
    manager._core_voice_session_swap_lock = asyncio.Lock()
    manager._core_voice_session_swap_barrier_timeout_s = 1.0
    manager.lock = asyncio.Lock()
    manager.is_active = True
    manager.session_ready = True
    manager._reset_preparation_state = AsyncMock()
    manager._cleanup_pending_session_resources = AsyncMock()
    manager._close_independent_asr = AsyncMock()
    manager.send_session_ended_by_server = AsyncMock()

    turn_owned = True
    replacement_listener_release = asyncio.Event()

    async def listen_after_cancel() -> None:
        await replacement_listener_release.wait()

    old_session = SimpleNamespace(
        close=AsyncMock(),
        handle_messages=AsyncMock(side_effect=listen_after_cancel),
    )
    manager.session = old_session

    async def old_listener() -> None:
        nonlocal turn_owned
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            turn_owned = False
            raise

    retired_listener = asyncio.create_task(old_listener())
    manager.message_handler_task = retired_listener
    await asyncio.sleep(0)

    candidate = SimpleNamespace(
        close=AsyncMock(),
        submit_multimodal_turn=AsyncMock(),
    )
    manager._create_offline_vlm_handoff_candidate = AsyncMock(
        return_value=(candidate, 0)
    )
    turn = SimpleNamespace(
        transcript="old turn",
        images=("old-frame",),
        turn_id="turn-old",
    )

    try:
        delivered = await manager._handoff_to_offline_vlm_and_submit(
            turn,
            expected_session=old_session,
            prepared_session=old_session,
            operation_is_current=lambda: turn_owned,
            cached_turns_before_final=[],
        )

        assert delivered is False
        assert retired_listener.cancelled()
        assert manager.session is old_session
        assert manager.message_handler_task is not retired_listener
        assert not manager.message_handler_task.done()
        old_session.handle_messages.assert_called_once_with()
        old_session.close.assert_not_awaited()
        candidate.submit_multimodal_turn.assert_not_awaited()
        candidate.close.assert_awaited_once_with()
        manager._close_independent_asr.assert_not_awaited()
        manager.send_session_ended_by_server.assert_not_awaited()
    finally:
        replacement_listener_release.set()
        if manager.message_handler_task is not None:
            await manager.message_handler_task


async def test_new_user_turn_during_old_close_fail_closes_handoff() -> None:
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lanlan_name = "Test"
    manager._multimodal_handoff_lock = asyncio.Lock()
    manager._core_voice_session_swap_lock = asyncio.Lock()
    manager._core_voice_session_swap_barrier_timeout_s = 1.0
    manager.lock = asyncio.Lock()
    manager.is_active = True
    manager.session_ready = True
    manager._reset_preparation_state = AsyncMock()
    manager._cleanup_pending_session_resources = AsyncMock()
    teardown_order = []
    manager._close_independent_asr = AsyncMock(
        side_effect=lambda **_kwargs: teardown_order.append("close_asr")
    )
    manager.send_session_ended_by_server = AsyncMock()
    manager.send_session_ended_by_server.side_effect = lambda: teardown_order.append(
        "session_ended"
    )
    manager.message_handler_task = None

    turn_owned = True

    async def close_old_session() -> None:
        nonlocal turn_owned
        turn_owned = False

    old_session = SimpleNamespace(
        close=AsyncMock(side_effect=close_old_session),
    )
    manager.session = old_session
    candidate = SimpleNamespace(
        close=AsyncMock(),
        submit_multimodal_turn=AsyncMock(),
    )
    manager._create_offline_vlm_handoff_candidate = AsyncMock(
        return_value=(candidate, 0)
    )
    turn = SimpleNamespace(
        transcript="old turn",
        images=("old-frame",),
        turn_id="turn-old",
    )

    delivered = await manager._handoff_to_offline_vlm_and_submit(
        turn,
        expected_session=old_session,
        prepared_session=old_session,
        operation_is_current=lambda: turn_owned,
        cached_turns_before_final=[],
    )

    assert delivered is False
    old_session.close.assert_awaited_once_with()
    assert manager.session is None
    assert manager.message_handler_task is None
    assert manager.is_active is False
    assert manager.session_ready is False
    manager._close_independent_asr.assert_awaited_once_with(
        next_route_mode="blocked"
    )
    manager.send_session_ended_by_server.assert_awaited_once_with()
    assert teardown_order == ["close_asr", "session_ended"]
    candidate.submit_multimodal_turn.assert_not_awaited()
    candidate.close.assert_awaited_once_with()


async def test_new_user_turn_after_promotion_skips_multimodal_submit() -> None:
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lanlan_name = "Test"
    manager._multimodal_handoff_lock = asyncio.Lock()
    manager._core_voice_session_swap_lock = asyncio.Lock()
    manager._core_voice_session_swap_barrier_timeout_s = 1.0
    manager.lock = asyncio.Lock()
    manager.message_cache_for_new_session = []
    manager.is_preparing_new_session = True
    manager.summary_triggered_time = object()
    manager._reset_preparation_state = AsyncMock()
    manager._cleanup_pending_session_resources = AsyncMock()
    manager.ensure_tts_pipeline_alive = AsyncMock()
    manager._consume_next_session_context_messages = MagicMock()
    manager.message_handler_task = None

    old_session = SimpleNamespace(close=AsyncMock())
    manager.session = old_session
    listener_release = asyncio.Event()

    async def listen() -> None:
        await listener_release.wait()

    turn_owned = True

    async def invalidate_during_tool_sync() -> None:
        nonlocal turn_owned
        turn_owned = False

    manager._sync_tools_to_active_session = AsyncMock(
        side_effect=invalidate_during_tool_sync
    )
    candidate = SimpleNamespace(
        handle_messages=AsyncMock(side_effect=listen),
        submit_multimodal_turn=AsyncMock(),
        close=AsyncMock(),
    )
    manager._create_offline_vlm_handoff_candidate = AsyncMock(
        return_value=(candidate, 0)
    )
    turn = SimpleNamespace(
        transcript="old turn",
        images=("old-frame",),
        turn_id="turn-old",
    )

    try:
        delivered = await manager._handoff_to_offline_vlm_and_submit(
            turn,
            expected_session=old_session,
            prepared_session=old_session,
            operation_is_current=lambda: turn_owned,
            cached_turns_before_final=[],
        )

        assert delivered is False
        assert manager.session is candidate
        assert manager.response_backend == "offline_vlm"
        assert manager.message_handler_task is not None
        assert not manager.message_handler_task.done()
        candidate.submit_multimodal_turn.assert_not_awaited()
        candidate.close.assert_not_awaited()
    finally:
        listener_release.set()
        if manager.message_handler_task is not None:
            await manager.message_handler_task


async def test_tts_failure_after_promotion_keeps_offline_listener_coherent() -> None:
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lanlan_name = "Test"
    manager._multimodal_handoff_lock = asyncio.Lock()
    manager._core_voice_session_swap_lock = asyncio.Lock()
    manager._core_voice_session_swap_barrier_timeout_s = 1.0
    manager.lock = asyncio.Lock()
    manager.message_cache_for_new_session = [{"role": "Test", "text": "old"}]
    manager.is_preparing_new_session = True
    manager.summary_triggered_time = object()
    manager._reset_preparation_state = AsyncMock()
    manager._cleanup_pending_session_resources = AsyncMock()
    manager.ensure_tts_pipeline_alive = AsyncMock(
        side_effect=RuntimeError("tts unavailable")
    )
    manager._sync_tools_to_active_session = AsyncMock()
    manager._consume_next_session_context_messages = MagicMock()
    manager.message_handler_task = None

    old_session = SimpleNamespace(close=AsyncMock())
    manager.session = old_session
    listener_release = asyncio.Event()

    async def listen() -> None:
        await listener_release.wait()

    candidate = SimpleNamespace(
        handle_messages=AsyncMock(side_effect=listen),
        submit_multimodal_turn=AsyncMock(),
        close=AsyncMock(),
    )
    manager._create_offline_vlm_handoff_candidate = AsyncMock(
        return_value=(candidate, 2)
    )
    turn = SimpleNamespace(
        transcript="what is this",
        images=("raw-frame",),
        turn_id="turn-1",
    )

    try:
        delivered = await manager._handoff_to_offline_vlm_and_submit(
            turn,
            expected_session=old_session,
            prepared_session=old_session,
            operation_is_current=lambda: True,
            cached_turns_before_final=[],
        )

        assert delivered is False
        assert manager.session is candidate
        assert manager.response_backend == "offline_vlm"
        assert manager.use_tts is True
        assert manager.message_handler_task is not None
        assert not manager.message_handler_task.done()
        manager._consume_next_session_context_messages.assert_called_once_with(2)
        assert manager.message_cache_for_new_session == []
        assert manager.is_preparing_new_session is False
        candidate.submit_multimodal_turn.assert_not_awaited()
        candidate.close.assert_not_awaited()
    finally:
        listener_release.set()
        if manager.message_handler_task is not None:
            await manager.message_handler_task


async def test_existing_offline_fast_path_does_not_repeat_turn_preparation() -> None:
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lanlan_name = "Test"
    manager._multimodal_handoff_lock = asyncio.Lock()
    manager._core_voice_session_swap_lock = asyncio.Lock()
    manager._core_voice_session_swap_barrier_timeout_s = 1.0
    manager.response_backend = "offline_vlm"
    manager.handle_new_message = AsyncMock()
    manager.ensure_tts_pipeline_alive = AsyncMock()

    session = OmniOfflineClient.__new__(OmniOfflineClient)

    async def interrupt() -> None:
        assert manager._core_voice_session_swap_lock.locked()

    session.handle_interruption = AsyncMock(side_effect=interrupt)
    session.submit_multimodal_turn = AsyncMock()
    manager.session = session
    turn = SimpleNamespace(
        transcript="what is this",
        images=("raw-frame",),
        turn_id="turn-1",
    )

    delivered = await manager._handoff_to_offline_vlm_and_submit(
        turn,
        expected_session=session,
        prepared_session=session,
        operation_is_current=lambda: True,
        cached_turns_before_final=[],
    )

    assert delivered is True
    session.handle_interruption.assert_not_awaited()
    manager.handle_new_message.assert_not_awaited()
    manager.ensure_tts_pipeline_alive.assert_awaited_once_with()
    session.submit_multimodal_turn.assert_awaited_once_with(
        "what is this",
        ("raw-frame",),
        turn_id="turn-1",
    )


async def test_offline_replacement_wins_and_receives_multimodal_turn() -> None:
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lanlan_name = "Test"
    manager._multimodal_handoff_lock = asyncio.Lock()
    manager._core_voice_session_swap_lock = asyncio.Lock()
    manager._core_voice_session_swap_barrier_timeout_s = 1.0
    manager.handle_new_message = AsyncMock()
    manager.ensure_tts_pipeline_alive = AsyncMock()

    expected = OmniOfflineClient.__new__(OmniOfflineClient)
    expected.handle_interruption = AsyncMock()
    expected.submit_multimodal_turn = AsyncMock()
    replacement = OmniOfflineClient.__new__(OmniOfflineClient)
    replacement.handle_interruption = AsyncMock()
    replacement.submit_multimodal_turn = AsyncMock()
    manager.session = expected
    turn = SimpleNamespace(
        transcript="what is this",
        images=("raw-frame",),
        turn_id="turn-1",
    )

    await manager._core_voice_session_swap_lock.acquire()
    task = asyncio.create_task(
        manager._handoff_to_offline_vlm_and_submit(
            turn,
            expected_session=expected,
            prepared_session=expected,
            operation_is_current=lambda: True,
            cached_turns_before_final=[],
        )
    )
    await asyncio.sleep(0)
    manager.session = replacement
    manager._core_voice_session_swap_lock.release()

    assert await asyncio.wait_for(task, 1.0) is True
    expected.handle_interruption.assert_not_awaited()
    expected.submit_multimodal_turn.assert_not_awaited()
    replacement.handle_interruption.assert_awaited_once_with()
    replacement.submit_multimodal_turn.assert_awaited_once_with(
        "what is this",
        ("raw-frame",),
        turn_id="turn-1",
    )
    manager.handle_new_message.assert_awaited_once_with()
    manager.ensure_tts_pipeline_alive.assert_awaited_once_with()


async def test_realtime_replacement_wins_and_continues_handoff() -> None:
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lanlan_name = "Test"
    manager._multimodal_handoff_lock = asyncio.Lock()
    manager._core_voice_session_swap_lock = asyncio.Lock()
    manager._core_voice_session_swap_barrier_timeout_s = 1.0
    manager.lock = asyncio.Lock()
    manager.message_cache_for_new_session = []
    manager.is_preparing_new_session = True
    manager.summary_triggered_time = object()
    manager._reset_preparation_state = AsyncMock()
    manager._cleanup_pending_session_resources = AsyncMock()
    manager.ensure_tts_pipeline_alive = AsyncMock()
    manager._sync_tools_to_active_session = AsyncMock()
    manager._consume_next_session_context_messages = MagicMock()
    manager.message_handler_task = None

    prepared = SimpleNamespace(close=AsyncMock())
    replacement = OmniRealtimeClient.__new__(OmniRealtimeClient)

    async def prepare_replacement(*, turn_id: str) -> bool:
        assert turn_id == "turn-1"
        assert manager._core_voice_session_swap_lock.locked()
        return False

    replacement.prepare_external_voice_turn = AsyncMock(
        side_effect=prepare_replacement
    )
    replacement.close = AsyncMock()
    manager.session = replacement
    candidate = SimpleNamespace(
        handle_messages=AsyncMock(),
        submit_multimodal_turn=AsyncMock(),
        close=AsyncMock(),
    )
    manager._create_offline_vlm_handoff_candidate = AsyncMock(
        return_value=(candidate, 0)
    )
    turn = SimpleNamespace(
        transcript="what is this",
        images=("raw-frame",),
        turn_id="turn-1",
    )

    delivered = await manager._handoff_to_offline_vlm_and_submit(
        turn,
        expected_session=prepared,
        prepared_session=prepared,
        operation_is_current=lambda: True,
        cached_turns_before_final=[],
    )

    assert delivered is True
    prepared.close.assert_not_awaited()
    replacement.prepare_external_voice_turn.assert_awaited_once_with(
        turn_id="turn-1"
    )
    replacement.close.assert_awaited_once_with()
    assert manager.session is candidate
    candidate.submit_multimodal_turn.assert_awaited_once_with(
        "what is this",
        ("raw-frame",),
        turn_id="turn-1",
    )
    candidate.close.assert_not_awaited()


async def test_handoff_entry_session_close_fails_without_candidate() -> None:
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lanlan_name = "Test"
    manager._multimodal_handoff_lock = asyncio.Lock()
    manager._core_voice_session_swap_lock = asyncio.Lock()
    manager._core_voice_session_swap_barrier_timeout_s = 1.0
    manager.session = None
    manager._reset_preparation_state = AsyncMock()
    manager._cleanup_pending_session_resources = AsyncMock()
    manager._create_offline_vlm_handoff_candidate = AsyncMock()
    prepared = SimpleNamespace(close=AsyncMock())
    turn = SimpleNamespace(
        transcript="what is this",
        images=("raw-frame",),
        turn_id="turn-1",
    )

    delivered = await manager._handoff_to_offline_vlm_and_submit(
        turn,
        expected_session=prepared,
        prepared_session=prepared,
        operation_is_current=lambda: True,
        cached_turns_before_final=[],
    )

    assert delivered is False
    manager._reset_preparation_state.assert_not_awaited()
    manager._cleanup_pending_session_resources.assert_not_awaited()
    manager._create_offline_vlm_handoff_candidate.assert_not_awaited()


async def test_existing_offline_multimodal_submit_retries_tts_each_turn() -> None:
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lanlan_name = "Test"
    manager._multimodal_handoff_lock = asyncio.Lock()
    manager._core_voice_session_swap_lock = asyncio.Lock()
    manager._core_voice_session_swap_barrier_timeout_s = 1.0
    manager.handle_new_message = AsyncMock()
    manager.ensure_tts_pipeline_alive = AsyncMock(
        side_effect=[RuntimeError("tts unavailable"), None]
    )

    session = OmniOfflineClient.__new__(OmniOfflineClient)
    session.handle_interruption = AsyncMock()
    session.submit_multimodal_turn = AsyncMock()
    manager.session = session
    turn = SimpleNamespace(
        transcript="what is this",
        images=("raw-frame",),
        turn_id="turn-1",
    )

    first = await manager._handoff_to_offline_vlm_and_submit(
        turn,
        expected_session=session,
        prepared_session=session,
        operation_is_current=lambda: True,
        cached_turns_before_final=[],
    )
    second = await manager._handoff_to_offline_vlm_and_submit(
        turn,
        expected_session=session,
        prepared_session=session,
        operation_is_current=lambda: True,
        cached_turns_before_final=[],
    )

    assert first is False
    assert second is True
    assert manager.ensure_tts_pipeline_alive.await_count == 2
    session.submit_multimodal_turn.assert_awaited_once_with(
        "what is this",
        ("raw-frame",),
        turn_id="turn-1",
    )


async def test_handoff_losing_frame_ownership_midflight_still_delivers_the_text():
    """The handoff owns the longest await window on the whole path.

    Connecting the candidate, promoting it, starting TTS and syncing tools all
    happen after the caller's last ownership check, and ``operation_is_current``
    only covers route identity. A successor prepared inside that window takes
    the frames -- but not the sentence: the session must still be promoted and
    the transcript must still be answered, as text.
    """
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lanlan_name = "Test"
    manager.input_mode = "audio"
    manager.response_backend = "realtime"
    manager._asr_route_mode = "independent"
    manager._independent_asr_provider = "qwen"
    manager._multimodal_handoff_lock = asyncio.Lock()
    manager._core_voice_session_swap_lock = asyncio.Lock()
    manager._core_voice_session_swap_barrier_timeout_s = 1.0
    manager.lock = asyncio.Lock()
    manager.message_cache_for_new_session = []
    manager.is_preparing_new_session = True
    manager.summary_triggered_time = object()
    manager._reset_preparation_state = AsyncMock()
    manager._cleanup_pending_session_resources = AsyncMock()
    manager._sync_tools_to_active_session = AsyncMock()
    manager._consume_next_session_context_messages = MagicMock()

    owned = {"frames": True}

    async def lose_ownership_during_tts_startup() -> None:
        # 后继发声正好在这段 await 里 prepare，帧的所有权交了出去。
        owned["frames"] = False

    manager.ensure_tts_pipeline_alive = AsyncMock(
        side_effect=lose_ownership_during_tts_startup
    )

    old_session = SimpleNamespace(
        base_url="wss://www.lanlan.tech/core",
        close=AsyncMock(),
    )
    manager.session = old_session
    manager.message_handler_task = None

    candidate = SimpleNamespace(
        handle_messages=AsyncMock(),
        submit_multimodal_turn=AsyncMock(),
        submit_external_voice_turn=AsyncMock(return_value=True),
        close=AsyncMock(),
    )
    manager._create_offline_vlm_handoff_candidate = AsyncMock(
        return_value=(candidate, 0)
    )
    turn = SimpleNamespace(
        transcript="what is this",
        images=("raw-image",),
        turn_id="turn-1",
    )

    delivered = await manager._handoff_to_offline_vlm_and_submit(
        turn,
        expected_session=old_session,
        prepared_session=old_session,
        operation_is_current=lambda: True,
        cached_turns_before_final=[],
        visual_still_owned=lambda: owned["frames"],
    )

    assert delivered is True
    # 会话照常 promote —— 路由本来就需要 offline。
    assert manager.session is candidate
    assert manager.response_backend == "offline_vlm"
    # 帧没跟着走，话跟着走了。
    candidate.submit_multimodal_turn.assert_not_awaited()
    candidate.submit_external_voice_turn.assert_awaited_once_with(
        "what is this",
        turn_id="turn-1",
    )
    candidate.close.assert_not_awaited()


async def test_handoff_keeping_ownership_still_submits_the_frames():
    """The downgrade must be conditional, not the new default."""
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lanlan_name = "Test"
    manager.input_mode = "audio"
    manager.response_backend = "realtime"
    manager._asr_route_mode = "independent"
    manager._independent_asr_provider = "qwen"
    manager._multimodal_handoff_lock = asyncio.Lock()
    manager._core_voice_session_swap_lock = asyncio.Lock()
    manager._core_voice_session_swap_barrier_timeout_s = 1.0
    manager.lock = asyncio.Lock()
    manager.message_cache_for_new_session = []
    manager.is_preparing_new_session = True
    manager.summary_triggered_time = object()
    manager._reset_preparation_state = AsyncMock()
    manager._cleanup_pending_session_resources = AsyncMock()
    manager.ensure_tts_pipeline_alive = AsyncMock()
    manager._sync_tools_to_active_session = AsyncMock()
    manager._consume_next_session_context_messages = MagicMock()

    old_session = SimpleNamespace(
        base_url="wss://www.lanlan.tech/core",
        close=AsyncMock(),
    )
    manager.session = old_session
    manager.message_handler_task = None

    candidate = SimpleNamespace(
        handle_messages=AsyncMock(),
        submit_multimodal_turn=AsyncMock(),
        submit_external_voice_turn=AsyncMock(),
        close=AsyncMock(),
    )
    manager._create_offline_vlm_handoff_candidate = AsyncMock(
        return_value=(candidate, 0)
    )
    turn = SimpleNamespace(
        transcript="what is this",
        images=("raw-image",),
        turn_id="turn-1",
    )

    delivered = await manager._handoff_to_offline_vlm_and_submit(
        turn,
        expected_session=old_session,
        prepared_session=old_session,
        operation_is_current=lambda: True,
        cached_turns_before_final=[],
        visual_still_owned=lambda: True,
    )

    assert delivered is True
    candidate.submit_multimodal_turn.assert_awaited_once_with(
        "what is this",
        ("raw-image",),
        turn_id="turn-1",
    )
    candidate.submit_external_voice_turn.assert_not_awaited()


async def test_existing_offline_fast_path_also_downgrades_on_lost_ownership():
    """The fast path has its own awaits, so it needs the same check.

    A session that is already Offline skips candidate construction, but still
    goes through preparation/interruption/handle_new_message and the TTS
    pipeline before submitting. A successor prepared in any of those windows
    takes the frames; the sentence must still be answered, as text.
    """
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lanlan_name = "Test"
    manager._multimodal_handoff_lock = asyncio.Lock()
    manager._core_voice_session_swap_lock = asyncio.Lock()
    manager._core_voice_session_swap_barrier_timeout_s = 1.0
    manager.response_backend = "offline_vlm"
    manager.handle_new_message = AsyncMock()

    owned = {"frames": True}

    async def lose_ownership_during_tts_startup() -> None:
        owned["frames"] = False

    manager.ensure_tts_pipeline_alive = AsyncMock(
        side_effect=lose_ownership_during_tts_startup
    )

    session = OmniOfflineClient.__new__(OmniOfflineClient)
    session.handle_interruption = AsyncMock()
    session.submit_multimodal_turn = AsyncMock()
    session.submit_external_voice_turn = AsyncMock(return_value=True)
    manager.session = session
    turn = SimpleNamespace(
        transcript="what is this",
        images=("raw-frame",),
        turn_id="turn-1",
    )

    delivered = await manager._handoff_to_offline_vlm_and_submit(
        turn,
        expected_session=session,
        prepared_session=session,
        operation_is_current=lambda: True,
        cached_turns_before_final=[],
        visual_still_owned=lambda: owned["frames"],
    )

    assert delivered is True
    session.submit_multimodal_turn.assert_not_awaited()
    session.submit_external_voice_turn.assert_awaited_once_with(
        "what is this",
        turn_id="turn-1",
    )
