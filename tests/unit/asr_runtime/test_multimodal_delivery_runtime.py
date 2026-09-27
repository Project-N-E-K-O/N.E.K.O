import asyncio
import time
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch
import pytest
from main_logic.asr_client.lifecycle import VoiceTurnToken
from main_logic.voice_turn.contracts import VoiceTranscriptEvent

from tests.support.core_asr_harness import (
    _install_ready_lifecycle,
    _start_and_seal_turn,
)

from tests.support.asr_fakes import (
    _Runtime,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


@pytest.mark.unit
async def test_independent_multimodal_turn_samples_the_utterance_span() -> None:
    """One utterance carries first/middle/last; identity fields name the last."""
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=77)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    captured_at = time.monotonic()

    assert runtime._stage_independent_visual_frame(
        "first-frame",
        source="screen",
        request_id="frame-1",
        captured_at=captured_at,
    )
    assert runtime._stage_independent_visual_frame(
        "latest-frame",
        source="camera",
        request_id="frame-2",
        captured_at=captured_at + 0.1,
    )
    assert not runtime._stage_independent_visual_frame(
        "stale-frame",
        source="screen",
        request_id="frame-stale",
        captured_at=captured_at - 0.1,
    )

    turn = runtime._snapshot_core_multimodal_turn(turn_id, "what is that")

    assert turn is not None
    # 两帧都在本回合窗口内：开头那张不能因为"不是最新"被丢掉——用户开口时指的
    # 东西就在那张上。source / request_id 仍然描述最新那张（回合的收尾身份）。
    assert turn.images == ("first-frame", "latest-frame")
    assert turn.source == "camera"
    assert turn.request_id == "frame-2"
    assert turn.image_generation > turn.start_image_generation


@pytest.mark.unit
async def test_independent_multimodal_turn_never_reuses_prior_turn_frame() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    captured_at = time.monotonic()
    assert runtime._stage_independent_visual_frame(
        "prior-turn-frame",
        source="screen",
        request_id="screen-prior",
        captured_at=captured_at,
    )
    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=78)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)

    turn = runtime._snapshot_core_multimodal_turn(turn_id, "new question")

    assert turn is None


@pytest.mark.unit
async def test_independent_multimodal_turn_rejects_delayed_prior_capture() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    captured_before_turn = time.monotonic() - 1.0
    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=79)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]

    # Validation completes after prepare, so generation alone looks current;
    # the ingress capture time must keep this prior image out of the new turn.
    assert runtime._stage_independent_visual_frame(
        "delayed-prior-frame",
        source="screen",
        request_id="screen-delayed",
        captured_at=captured_before_turn,
    )
    assert record.last_frame is None
    assert runtime._snapshot_core_multimodal_turn(turn_id, "new question") is None

    assert runtime._stage_independent_visual_frame(
        "current-turn-frame",
        source="camera",
        request_id="camera-current",
        captured_at=record.started_at,
    )
    turn = runtime._snapshot_core_multimodal_turn(turn_id, "new question")

    assert turn is not None
    assert turn.images == ("current-turn-frame",)
    assert turn.captured_at == record.started_at


@pytest.mark.unit
async def test_independent_multimodal_turn_rejects_owned_frame_expired_at_final() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=80)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]
    assert runtime._stage_independent_visual_frame(
        "expired-owned-frame",
        source="screen",
        request_id="screen-expired",
        captured_at=record.started_at,
    )

    with patch(
        "main_logic.core.asr_runtime.time.monotonic",
        return_value=(
            record.started_at + runtime._independent_visual_frame_ttl_s + 1.0
        ),
    ):
        turn = runtime._snapshot_core_multimodal_turn(turn_id, "delayed final")

    assert turn is None


@pytest.mark.unit
async def test_dispatch_hands_the_ownership_predicate_to_the_handoff() -> None:
    """Checking before the handoff is not enough; it must check inside too.

    Connecting and promoting the Offline candidate, starting TTS and syncing
    tools are the longest awaits on the path, and the handoff's own
    ``operation_is_current`` covers route identity only. A guard that merely
    exercises the predicate in isolation still passes when the dispatch stops
    handing it over, so assert the call site itself.
    """
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    runtime._asr_route_mode = "independent"
    runtime.session.get_multimodal_turn_delivery = MagicMock(
        return_value="handoff_required"
    )
    runtime.session.submit_external_voice_turn = AsyncMock()
    seen: dict = {}

    async def observe_predicate_inside_the_handoff(_turn, **kwargs):
        still_owned = kwargs["visual_still_owned"]
        seen["before"] = still_owned()
        # 后继发声在交接进行中 prepare —— 谓词必须立刻反映出来，而不是停在
        # 进入交接那一刻的快照。
        seen["record"].invalidated.set()
        seen["after"] = still_owned()
        return True

    runtime._handoff_to_offline_vlm_and_submit = AsyncMock(
        side_effect=observe_predicate_inside_the_handoff
    )
    handoff_token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    handoff_turn_id = (
        f"asr-{handoff_token.ingress.session_epoch}-{handoff_token.turn_id}"
    )
    runtime._begin_core_multimodal_turn(handoff_turn_id, handoff_token)
    handoff_record = runtime._core_multimodal_turns[handoff_turn_id]
    seen["record"] = handoff_record
    assert runtime._stage_independent_visual_frame(
        "frame-of-this-turn",
        source="screen",
        request_id="screen-1",
        captured_at=handoff_record.started_at,
    )

    await runtime._dispatch_core_asr_transcript(
        VoiceTranscriptEvent(
            turn_token=handoff_token,
            provider="openai",
            text="look here",
        )
    )

    runtime._handoff_to_offline_vlm_and_submit.assert_awaited_once()
    assert seen["before"] is True
    assert seen["after"] is False


@pytest.mark.unit
async def test_route_close_drops_the_staged_visual_caches() -> None:
    """Staged originals belong to the route, not to the process.

    Their only other clearing point is the NEXT turn starting, so an episode
    that ends while screen sharing is on -- with no further utterance -- leaves
    full-size base64 originals pinned on a long-lived character manager, and the
    next episode starts with a buffer already full of the previous one's frames.
    """
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    assert runtime._stage_independent_visual_frame(
        "frame-with-no-utterance",
        source="screen",
        request_id="screen-1",
        captured_at=time.monotonic(),
    )
    assert runtime._prerecord_visual_frames
    assert runtime._latest_independent_visual_frame is not None

    await runtime._close_independent_asr(next_route_mode="blocked")

    assert runtime._prerecord_visual_frames == []
    assert runtime._latest_independent_visual_frame is None


@pytest.mark.unit
async def test_new_turn_wakes_visual_validation_wait_without_cancelling_task() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    first_token = VoiceTurnToken(
        ingress=runtime._capture_ingress_token(),
        turn_id=82,
    )
    first_turn_id = (
        f"asr-{first_token.ingress.session_epoch}-{first_token.turn_id}"
    )
    runtime._begin_core_multimodal_turn(first_turn_id, first_token)
    first_record = runtime._core_multimodal_turns[first_turn_id]
    release = asyncio.Event()
    validation_task = asyncio.create_task(release.wait())
    assert runtime._track_independent_visual_validation_task(
        validation_task,
        captured_at=first_record.started_at,
    )
    waiting = asyncio.create_task(
        runtime._await_independent_visual_validation_tasks(first_turn_id)
    )
    await asyncio.sleep(0)

    second_token = VoiceTurnToken(
        ingress=runtime._capture_ingress_token(),
        turn_id=83,
    )
    runtime._begin_core_multimodal_turn(
        f"asr-{second_token.ingress.session_epoch}-{second_token.turn_id}",
        second_token,
    )

    await asyncio.wait_for(waiting, timeout=0.1)
    assert not validation_task.done()
    release.set()
    await validation_task


async def test_offline_image_free_voice_turn_retries_tts_after_failure() -> None:
    runtime = _Runtime()
    runtime.response_backend = "offline_vlm"
    runtime.ensure_tts_pipeline_alive = AsyncMock(
        side_effect=[RuntimeError("tts unavailable"), None]
    )
    runtime.session.submit_external_voice_turn = AsyncMock()

    with pytest.raises(RuntimeError, match="tts unavailable"):
        await runtime._submit_core_voice_turn(
            "first",
            turn_id="turn-1",
            session_ref=runtime.session,
        )
    runtime.session.submit_external_voice_turn.assert_not_awaited()

    await runtime._submit_core_voice_turn(
        "second",
        turn_id="turn-2",
        session_ref=runtime.session,
    )

    assert runtime.ensure_tts_pipeline_alive.await_count == 2
    runtime.session.submit_external_voice_turn.assert_awaited_once_with(
        "second",
        turn_id="turn-2",
    )


@pytest.mark.unit
async def test_handoff_failure_never_falls_back_to_transcript_only() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    runtime._asr_route_mode = "independent"
    runtime.session.get_multimodal_turn_delivery = MagicMock(
        return_value="handoff_required"
    )
    runtime.session.submit_external_voice_turn = AsyncMock()
    runtime._handoff_to_offline_vlm_and_submit = AsyncMock(return_value=False)
    runtime.is_preparing_new_session = True
    runtime.message_cache_for_new_session = [
        {"role": "Test", "text": "earlier reply"}
    ]

    async def cache_current_final(*_args, **_kwargs) -> bool:
        runtime.message_cache_for_new_session.append(
            {"role": "master", "text": "what is this"}
        )
        return True

    runtime.handle_input_transcript.side_effect = cache_current_final
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    assert runtime._stage_independent_visual_frame(
        "raw-frame",
        source="camera",
        request_id="camera-1",
        captured_at=time.monotonic(),
    )

    await runtime._dispatch_core_asr_transcript(
        VoiceTranscriptEvent(
            turn_token=token,
            provider="qwen",
            text="what is this",
        )
    )

    runtime._handoff_to_offline_vlm_and_submit.assert_awaited_once()
    handoff_kwargs = (
        runtime._handoff_to_offline_vlm_and_submit.await_args.kwargs
    )
    assert handoff_kwargs["prepared_session"] is runtime.session
    assert handoff_kwargs["cached_turns_before_final"] == [
        {"role": "Test", "text": "earlier reply"}
    ]
    runtime.session.submit_external_voice_turn.assert_not_awaited()
    assert "ASR_MULTIMODAL_TURN_FAILED" in str(
        runtime.send_status.await_args_list
    )


@pytest.mark.unit
async def test_new_prepare_does_not_erase_a_preceding_turn_record() -> None:
    """An in-flight accepted final must still find its own record.

    The preceding final can still be running in TranscriptDispatcher (for
    example awaiting the bounded visual-validation join) when the successor is
    prepared. Clearing every record there makes that dispatch fail its identity
    self-check and return without recording OR submitting the transcript — the
    overlapping utterance erases a complete user turn.
    """
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"

    first = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=201)
    first_id = f"asr-{first.ingress.session_epoch}-{first.turn_id}"
    runtime._begin_core_multimodal_turn(first_id, first)
    first_record = runtime._core_multimodal_turns[first_id]

    second = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=202)
    second_id = f"asr-{second.ingress.session_epoch}-{second.turn_id}"
    runtime._begin_core_multimodal_turn(second_id, second)

    # 前一条的记录仍在，且仍是同一个对象 —— 身份自检因此不会误判。
    assert runtime._core_multimodal_turns.get(first_id) is first_record
    # 但它已被标记作废：图归新回合，旧 final 只是别被整句丢掉。
    assert first_record.invalidated.is_set()
    assert runtime._core_multimodal_turns.get(second_id) is not None


@pytest.mark.unit
async def test_retained_turn_records_are_bounded() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"

    for turn_id in range(210, 230):
        token = VoiceTurnToken(
            ingress=runtime._capture_ingress_token(), turn_id=turn_id
        )
        runtime._begin_core_multimodal_turn(
            f"asr-{token.ingress.session_epoch}-{token.turn_id}", token
        )

    # 记录本该由各自 dispatch 的 finally 移除；这个上限只是内存兜底。
    assert len(runtime._core_multimodal_turns) <= 8
    # 留下的是最近的那些 —— 淘汰绝不能挑到最新那条（它才是当前在跑的）。
    kept = sorted(runtime._core_multimodal_turns)
    assert kept[-1].endswith("-229")


@pytest.mark.unit
async def test_microphone_route_syncs_provider_neutral_visual_delivery_mode() -> None:
    """Independent ASR must fail closed for raw vision during every route state."""
    runtime = _Runtime()
    runtime.session._supports_native_image = True
    runtime.session.set_visual_delivery_mode = MagicMock()
    runtime.session.block_raw_visual_delivery = MagicMock()
    runtime.session.allow_raw_visual_delivery = MagicMock()

    runtime._set_microphone_route("independent")
    runtime._set_microphone_route("blocked")
    runtime._set_microphone_route("native")

    delivered_modes = [
        getattr(item.args[0], "value", item.args[0])
        for item in runtime.session.set_visual_delivery_mode.call_args_list
    ]
    assert delivered_modes == ["native"]
    assert runtime.session.block_raw_visual_delivery.call_count >= 2
    runtime.session.allow_raw_visual_delivery.assert_called_once_with()


@pytest.mark.unit
async def test_direct_multimodal_final_submits_raw_image_once() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    runtime._asr_route_mode = "independent"
    runtime.session.get_multimodal_turn_delivery = MagicMock(
        return_value="direct_atomic"
    )
    # 在**调用发生的那一刻**取一次所有权判据的值。它是个活闭包，事后再调时
    # 这一轮早已结束、所有权已释放，所以只能在这里记。
    owned_at_call: list = []

    async def _record_ownership(*_args, **kwargs):
        cb = kwargs.get("visual_still_owned")
        owned_at_call.append(cb() if callable(cb) else None)

    runtime.session.submit_multimodal_turn = AsyncMock(
        side_effect=_record_ownership
    )
    runtime.session.submit_external_voice_turn = AsyncMock()
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]

    async def validate_frame() -> None:
        await asyncio.sleep(0)
        assert runtime._stage_independent_visual_frame(
            "raw-frame",
            source="screen",
            request_id="screen-1",
            captured_at=record.started_at,
        )

    validation_task = asyncio.create_task(validate_frame())
    assert runtime._track_independent_visual_validation_task(
        validation_task,
        captured_at=record.started_at,
    )

    await runtime._dispatch_core_asr_transcript(
        VoiceTranscriptEvent(
            turn_token=token,
            provider="openai",
            text="look here",
        )
    )
    await validation_task

    runtime.session.submit_multimodal_turn.assert_awaited_once_with(
        "look here",
        ("raw-frame",),
        turn_id=turn_id,
        # 帧总线的频道标签，与这批帧一起冻结。会话侧读活状态会在裁剪 / arbiter
        # 排队 / SDK send 那几段 await 里漂到后继发声的通道上。
        source="screen",
        # Gemini 那条路在真正送出之前还有一段压缩 await，所有权判据必须跟着进去。
        visual_still_owned=ANY,
    )
    # 传的是这一轮 record 自己的 source，不是某个字面量碰巧相等。
    assert runtime.session.submit_multimodal_turn.await_args.kwargs["source"] == (
        record.source if hasattr(record, "source") else "screen"
    )
    # 穿进去的必须是活的判据，且在真正调用 provider 的那一刻仍持有所有权。
    assert owned_at_call == [True]
    runtime.session.submit_external_voice_turn.assert_not_awaited()
    assert turn_id not in runtime._core_multimodal_turns


@pytest.mark.unit
async def test_final_superseded_after_freeze_submits_text_without_frames() -> None:
    """Freezing the frames is not the last word; the submit is.

    The record is retained past a successor prepare so this final keeps its
    transcript, which means the route self-check still finds the same record
    object and passes. But the successor now owns the visuals, so the frozen
    frames belong to the newer utterance. The sentence still has to be
    submitted -- as plain text, the ordinary no-image path.
    """
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    runtime._asr_route_mode = "independent"
    runtime.session.get_multimodal_turn_delivery = MagicMock(
        return_value="direct_atomic"
    )
    runtime.session.submit_multimodal_turn = AsyncMock()
    runtime.session.submit_external_voice_turn = AsyncMock()
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]
    assert runtime._stage_independent_visual_frame(
        "frame-of-the-old-turn",
        source="screen",
        request_id="screen-1",
        captured_at=record.started_at,
    )

    accepted = runtime.handle_input_transcript

    async def accept_then_let_a_successor_start(*args, **kwargs):
        result = await accepted(*args, **kwargs)
        # 冻结之后、提交之前：后继发声 prepare，视觉所有权交出去。
        successor = VoiceTurnToken(
            ingress=runtime._capture_ingress_token(),
            turn_id=token.turn_id + 1,
        )
        runtime._begin_core_multimodal_turn(
            f"asr-{successor.ingress.session_epoch}-{successor.turn_id}",
            successor,
        )
        return result

    runtime.handle_input_transcript = accept_then_let_a_successor_start

    await runtime._dispatch_core_asr_transcript(
        VoiceTranscriptEvent(
            turn_token=token,
            provider="openai",
            text="look here",
        )
    )

    assert record.invalidated.is_set()
    runtime.session.submit_multimodal_turn.assert_not_awaited()
    runtime.session.submit_external_voice_turn.assert_awaited_once()
    assert "look here" in runtime.session.submit_external_voice_turn.await_args.args


@pytest.mark.unit
async def test_provider_admission_rejection_submits_the_transcript_as_text() -> None:
    """Losing the provider's admission window must not lose the sentence.

    The arbiter rejects a multimodal ticket once a newer turn has armed its
    pause, and deletes the committed item on the way out -- nothing of this
    request survives provider-side. Propagating that error drops the user's
    whole utterance; the frames are gone but the transcript still has to be
    answered, exactly as when Core detects the supersession itself.
    """
    from main_logic.omni_realtime_client._response_arbiter import (
        ResponseAdmissionRejected,
    )

    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    runtime._asr_route_mode = "independent"
    runtime.session.get_multimodal_turn_delivery = MagicMock(
        return_value="direct_atomic"
    )
    runtime.session.submit_multimodal_turn = AsyncMock(
        side_effect=ResponseAdmissionRejected(
            "response dispatch admission rejected after commit"
        )
    )
    runtime.session.submit_external_voice_turn = AsyncMock()
    admission_token = runtime._asr_runtime._capture_turn_token(
        runtime._asr_lifecycle
    )
    admission_turn_id = (
        f"asr-{admission_token.ingress.session_epoch}-{admission_token.turn_id}"
    )
    runtime._begin_core_multimodal_turn(admission_turn_id, admission_token)
    admission_record = runtime._core_multimodal_turns[admission_turn_id]
    assert runtime._stage_independent_visual_frame(
        "frame-of-this-turn",
        source="screen",
        request_id="screen-1",
        captured_at=admission_record.started_at,
    )

    await runtime._dispatch_core_asr_transcript(
        VoiceTranscriptEvent(
            turn_token=admission_token,
            provider="openai",
            text="这句话不能消失",
        )
    )

    runtime.session.submit_multimodal_turn.assert_awaited_once()
    runtime.session.submit_external_voice_turn.assert_awaited_once()
    assert (
        "这句话不能消失"
        in runtime.session.submit_external_voice_turn.await_args.args
    )


@pytest.mark.unit
async def test_independent_visual_sync_failure_blocks_raw_images_without_stopping_asr() -> None:
    runtime = _Runtime()
    call_order: list[str] = []

    def block_raw_visual_delivery() -> None:
        call_order.append("block")

    def fail_visual_mode_sync(_mode: str) -> None:
        call_order.append("sync")
        raise RuntimeError("stale realtime session")

    runtime.session.block_raw_visual_delivery = block_raw_visual_delivery
    runtime.session.set_visual_delivery_mode = fail_visual_mode_sync

    runtime._set_microphone_route("independent")

    assert runtime._asr_route_mode == "independent"
    assert call_order == ["block"]


@pytest.mark.unit
async def test_visual_validation_wait_timeout_does_not_cancel_image_task() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    runtime._independent_visual_frame_ttl_s = 0.01
    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=81)
    turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._begin_core_multimodal_turn(turn_id, token)
    record = runtime._core_multimodal_turns[turn_id]
    release = asyncio.Event()
    validation_task = asyncio.create_task(release.wait())
    assert runtime._track_independent_visual_validation_task(
        validation_task,
        captured_at=record.started_at,
    )

    await runtime._await_independent_visual_validation_tasks(turn_id)

    assert not validation_task.done()
    release.set()
    await validation_task


@pytest.mark.unit
async def test_direct_multimodal_failure_reports_status_without_text_fallback() -> None:
    runtime = _Runtime()
    runtime.core_api_type = "openai"
    runtime.session.get_multimodal_turn_delivery = MagicMock(
        return_value="direct_atomic"
    )
    runtime.session.submit_multimodal_turn = AsyncMock(
        side_effect=RuntimeError("provider rejected image")
    )
    runtime.session.submit_external_voice_turn = AsyncMock()
    epoch = runtime._asr_session_epoch
    await _start_and_seal_turn(runtime, "openai")
    record = runtime._active_multimodal_turn_record()
    assert record is not None
    # The frame was captured during speech and validated after the endpoint.
    # Stamping it with the current clock can exclude it from this sealed turn.
    assert runtime._stage_independent_visual_frame(
        "raw-frame",
        source="screen",
        request_id="screen-1",
        captured_at=record.started_at,
    )

    await runtime._handle_independent_asr_final(
        "look here",
        epoch,
        "openai",
    )
    await runtime._wait_asr_transcript_dispatch_idle()

    runtime.session.submit_multimodal_turn.assert_awaited_once()
    runtime.session.submit_external_voice_turn.assert_not_awaited()
    status_payloads = [call.args[0] for call in runtime.send_status.await_args_list]
    assert any("ASR_INDEPENDENT_INJECTION_FAILED" in item for item in status_payloads)
    assert "provider rejected image" not in str(status_payloads)


@pytest.mark.unit
async def test_native_visual_sync_failure_keeps_raw_images_blocked() -> None:
    runtime = _Runtime()
    call_order: list[str] = []

    def allow_raw_visual_delivery() -> None:
        call_order.append("allow")

    def block_raw_visual_delivery() -> None:
        call_order.append("block")

    def fail_visual_mode_sync(_mode: str) -> None:
        call_order.append("sync")
        raise RuntimeError("stale realtime session")

    runtime.session.allow_raw_visual_delivery = allow_raw_visual_delivery
    runtime.session.block_raw_visual_delivery = block_raw_visual_delivery
    runtime.session.set_visual_delivery_mode = fail_visual_mode_sync

    runtime._set_microphone_route("native")

    assert runtime._asr_route_mode == "native"
    assert call_order == ["sync", "block"]
