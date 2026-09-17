import asyncio
import time
from dataclasses import replace
from unittest.mock import AsyncMock, call
import pytest
from main_logic.asr_client.lifecycle import VoiceLifecycleState
from main_logic.voice_turn.contracts import AsrLifecycleNotification, SpeechActivityEvent

from tests.support.core_asr_harness import (
    _install_ready_lifecycle,
)

from tests.support.asr_fakes import (
    _Runtime,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.unit_fast]


@pytest.mark.unit
async def test_replay_drops_the_pending_slot_when_the_transport_identity_moves_on() -> None:
    """A drifted runtime identity must not strand the pending confirmation.

    _send_asr_lifecycle_state() swallows delivery exceptions and returns
    _runtime_identity_matches(), so a false return means the runtime identity
    moved on -- and _restart_transport / _close_transport_only swap
    _asr_session and bump transport_generation without bumping the epoch or
    running _reset_asr_turn_state(). Holding the pending confirmation across
    that return strands it: the compensation already transitioned to ACTIVE,
    and both redemption sites gate on PREWARMING, so nothing ever collects it.
    The next unrelated utterance then adopts the stale onset as its visual
    ownership boundary, and the poisoned flag pins pending_before True so the
    overlap compensation silently stops firing.

    The real onset is already committed to _asr_turn_onset_at before the
    broadcast, so clearing the slot on confirmation loses nothing.
    """
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch
    component = runtime._asr_runtime

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    assert component._asr_overlap_onset_at is not None
    await runtime._handle_independent_asr_endpoint(epoch)

    # monotonic 在这台机器上一整个测试跑下来只走一格，靠时钟自然推进区分不了
    # 「陈旧 onset」和「新回合 onset」。按仓库既有做法直接注入一个明显靠前的
    # 时刻，后面那条继承断言才有分辨力。
    recorded_onset = time.monotonic() - 5.0
    component._asr_overlap_onset_at = recorded_onset

    component._asr_session.is_ready = False
    lifecycle_ref = runtime._asr_lifecycle

    # ACTIVE 广播飞在半空时来一次「仅关传输」：换掉 _asr_session、bump
    # transport_generation，epoch 与 lifecycle 对象都不动 —— 这正是
    # _close_transport_only 干的事，也是唯一能让 delivered 为假的那条腿。
    real_on_lifecycle = component._callbacks.on_lifecycle
    drifted = False

    async def _drift_transport_midflight(note: AsrLifecycleNotification) -> None:
        nonlocal drifted
        if note.state == VoiceLifecycleState.ACTIVE.value and not drifted:
            drifted = True
            component._asr_session = None
            lifecycle_ref.invalidate_transport()
        await real_on_lifecycle(note)

    component._callbacks = replace(
        component._callbacks,
        on_lifecycle=_drift_transport_midflight,
    )

    await runtime._handle_independent_asr_final("first", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert drifted is True
    # 走的确实是「传输身份漂移」这条腿，不是 detach / fail-closed 那条
    # （那两条会 bump epoch、换 lifecycle，并且自己会跑 _reset_asr_turn_state）。
    assert runtime._asr_session_epoch == epoch
    assert runtime._asr_lifecycle is lifecycle_ref

    # 挂起槽必须已经腾空 —— 没人会再来兑付它。
    assert component._asr_pending_speech_confirmed is False
    assert component._asr_pending_speech_onset_at is None
    # 而用户真实开口的时刻一点没丢：它在 await 之前就装进了 _asr_turn_onset_at。
    assert component._asr_turn_onset_at == recorded_onset

    # 行为层：走完这一轮，下一次**不相干**的开口不能继承那个陈旧时刻。
    component._asr_session = type("Asr", (), {"is_ready": True})()
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("second", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE

    fresh_floor = time.monotonic()
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    assert component._asr_turn_onset_at != recorded_onset
    assert component._asr_turn_onset_at >= fresh_floor


@pytest.mark.unit
async def test_credit_redemption_drops_the_pending_slot_when_the_transport_identity_moves_on() -> None:
    """Dual of the direct-replay case for the completed-overlap credit path.

    Both compensation blocks force-confirm the same way, so both strand the
    pending slot the same way when the runtime identity drifts across the
    ACTIVE broadcast. Covering only one leaves the other free to regress.
    """
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch
    component = runtime._asr_runtime

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    # 后继在上一轮还 ACTIVE 时开口又停顿：攒下一张 completed-overlap credit。
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.CANDIDATE_PAUSE,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("first", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()
    assert runtime._asr_overlap_completed_turns == 1
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE

    # 同上：注入一个明显靠前的时刻，后面那条继承断言才有分辨力。
    recorded_onset = time.monotonic() - 5.0
    component._asr_overlap_completed_onsets[0] = recorded_onset

    component._asr_session.is_ready = False
    lifecycle_ref = runtime._asr_lifecycle
    real_on_lifecycle = component._callbacks.on_lifecycle
    drifted = False

    async def _drift_transport_midflight(note: AsrLifecycleNotification) -> None:
        nonlocal drifted
        if note.state == VoiceLifecycleState.ACTIVE.value and not drifted:
            drifted = True
            component._asr_session = None
            lifecycle_ref.invalidate_transport()
        await real_on_lifecycle(note)

    component._callbacks = replace(
        component._callbacks,
        on_lifecycle=_drift_transport_midflight,
    )

    # 后继自己的 endpoint 兑付这张 credit，重放停在 PREWARMING 后就地补确认。
    await runtime._handle_independent_asr_endpoint(epoch)

    assert drifted is True
    assert runtime._asr_session_epoch == epoch
    assert runtime._asr_lifecycle is lifecycle_ref
    assert component._asr_pending_speech_confirmed is False
    assert component._asr_pending_speech_onset_at is None
    assert component._asr_turn_onset_at == recorded_onset
    # 确认已经落地（lifecycle 是 ACTIVE，这一轮会照常封口），所以那张 credit
    # 必须跟着确认一起记掉，不能被身份漂移那条 return 跳过。
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    assert runtime._asr_overlap_completed_turns == 0
    assert not component._asr_overlap_completed_onsets

    # 身份漂移让这一轮停在 ACTIVE（那次 return 越过了随后的封口）。恢复身份、
    # 把它正常走完，才谈得上「下一次不相干的开口」。
    component._asr_session = type("Asr", (), {"is_ready": True})()
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("second", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE

    fresh_floor = time.monotonic()
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    assert component._asr_turn_onset_at != recorded_onset
    assert component._asr_turn_onset_at >= fresh_floor

    # 行为层：后面一次**真实**的 overlap 兑付必须拿到它自己的 onset。credit 若
    # 被漏记，这张陈旧的会按 FIFO 排在前面先被兑走，这一轮就拿错了开口时刻。
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.CANDIDATE_PAUSE,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("third", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()
    assert runtime._asr_overlap_completed_turns == 1
    later_onset = component._asr_overlap_completed_onsets[0]
    assert later_onset != recorded_onset

    await runtime._handle_independent_asr_endpoint(epoch)
    assert component._asr_turn_onset_at == later_onset
    assert runtime._asr_overlap_completed_turns == 0


@pytest.mark.unit
async def test_direct_overlap_replay_seals_when_the_session_is_not_ready() -> None:
    """The direct replay must complete its confirmation in place too.

    Dual of the completed-overlap credit path. Parking in PREWARMING and just
    holding the onset is not enough: this successor's provider endpoint and
    final are already queued in the ordered FIFO and about to arrive, a
    PREWARMING lifecycle cannot seal, and _handle_independent_asr_final()
    requires DRAINING -- so the whole utterance is discarded with no watchdog
    armed.

    Waiting for the reconnect cannot recover it either: a reconnect swaps in a
    new session and is_adopted_candidate() drops every callback still queued on
    the old one (_restart_transport / _close_transport_only both null
    _asr_session before closing it). Reaching this point proves the old session
    is still adopted, i.e. the reconnect has not started.
    """
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch
    component = runtime._asr_runtime

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    # 后继在上一轮还 ACTIVE 时开口：它的 onset 被记下来等直接重放。
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    recorded_onset = component._asr_overlap_onset_at
    assert recorded_onset is not None
    await runtime._handle_independent_asr_endpoint(epoch)

    # 两条有序回调之间传输掉线：重放会停在 PREWARMING 并挂起确认。
    component._asr_session.is_ready = False

    await runtime._handle_independent_asr_final("first", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    # 重放就地补完了确认：回合醒着，后继排在 FIFO 里的 endpoint 才封得了口。
    # （HEAD 上这里是 PREWARMING，封不了口，那条 final 会被整条丢弃。）
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    # 这一刻还没 prepare 是对的：直接重放只负责唤醒，prepare 由后继自己的
    # endpoint 完成（_handle_independent_asr_endpoint 的 not _asr_turn_prepared
    # 分支）。断言它已 prepare 属于对契约的过度主张。
    # 用的是用户当初真实开口的时刻，不是这次重放的时刻。
    assert component._asr_turn_onset_at == recorded_onset
    assert component._asr_pending_speech_onset_at is None
    # 没走 fail-closed 出口（那条会 bump epoch、拆掉 session）。
    assert runtime._asr_session_epoch == epoch

    # 后继自己的 endpoint 紧随其后到达 —— 这一步才封口。
    await runtime._handle_independent_asr_endpoint(epoch)
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.DRAINING
    # 忙窗口有定时器兜底。
    assert component._asr_final_watchdog_task is not None

    await runtime._handle_independent_asr_final("second", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert [
        call.args[0] for call in runtime.handle_input_transcript.await_args_list
    ] == ["first", "second"]
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE


@pytest.mark.unit
async def test_direct_overlap_replay_reclaims_its_lent_onset_when_it_never_wakes() -> None:
    """A direct replay that never reaches ACTIVE must take its onset back.

    Both overlap replay paths lend the recorded onset to the confirmation
    branch. The credit-redemption path reclaims it when the wake-up fails; the
    direct path (driven by the delayed provider final) did not, so the stale
    timestamp stayed in the pending slot and the NEXT, unrelated utterance
    adopted it as its visual ownership boundary -- pulling in frames that
    belong to nobody and rejecting the ones it is actually about.

    The carve-out is identical to the credit path: an onset held for a PENDING
    confirmation is deliberately kept, because clearing it would send that
    confirmation back to a fresh detected_at and drop every frame since the
    user actually started speaking. The dual below pins that half.
    """
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    # A successor spoke while the first turn was still ACTIVE and prepared:
    # its onset is remembered for the direct replay after the delayed final.
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    assert runtime._asr_overlap_onset_at is not None
    await runtime._handle_independent_asr_endpoint(epoch)

    # The replay cannot wake the turn, and leaves no pending confirmation
    # behind (Smart Turn lease unavailable / lifecycle broadcast undelivered).
    async def refuse_to_wake(*_args, **_kwargs):
        return None

    runtime._asr_runtime._handle_independent_asr_activity = refuse_to_wake

    await runtime._handle_independent_asr_final("first", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert runtime._asr_pending_speech_onset_at is None, (
        "the lent onset stayed behind and a later unrelated turn will adopt it"
    )


@pytest.mark.unit
async def test_direct_overlap_replay_keeps_the_onset_for_a_pending_confirmation() -> None:
    """Dual: an onset held for a pending confirmation must NOT be reclaimed.

    When the session is momentarily unavailable the replay parks in PREWARMING
    with the confirmation pending and deliberately holds the onset for it.
    Reclaiming it there sends that confirmation back to a fresh detected_at and
    every frame since the user started speaking is excluded.
    """
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)

    async def park_with_pending_confirmation(*_args, **_kwargs):
        runtime._asr_runtime._asr_pending_speech_confirmed = True

    runtime._asr_runtime._handle_independent_asr_activity = (
        park_with_pending_confirmation
    )

    await runtime._handle_independent_asr_final("first", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert runtime._asr_pending_speech_onset_at is not None


@pytest.mark.unit
async def test_overlap_credit_survives_a_replay_that_never_activates() -> None:
    """Spend the credit on a successful wake-up, not on the attempt.

    The replay can leave the lifecycle short of ACTIVE when the session is
    momentarily unavailable. Deducting the credit first strands that turn: its
    endpoint can no longer seal, the final queued right behind it is discarded,
    and the popped onset goes on to be inherited by an unrelated later turn.
    """
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.CANDIDATE_PAUSE,
        epoch,
    )
    assert runtime._asr_overlap_completed_turns == 1
    onset_before = list(runtime._asr_overlap_completed_onsets)

    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("first", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE

    # 重放唤不醒这一轮（会话暂时不可用）。
    async def refuse_to_wake(*_args, **_kwargs):
        return None

    runtime._asr_runtime._handle_independent_asr_activity = refuse_to_wake
    await runtime._handle_independent_asr_endpoint(epoch)

    # credit 和 onset 都原样留着，等下一次兑付。
    assert runtime._asr_overlap_completed_turns == 1
    assert list(runtime._asr_overlap_completed_onsets) == onset_before
    # 借出去的 onset 也收回了，不会被后面不相干的回合继承。
    assert runtime._asr_pending_speech_onset_at is None


@pytest.mark.unit
async def test_an_unwoken_redemption_still_seals_and_delivers_the_queued_final() -> None:
    """An unwoken replay must still seal: its final is already on the way.

    This test REPLACES test_a_pending_confirmation_keeps_the_lent_onset and
    deliberately overturns its reasoning ("hold the onset for the confirmation
    that follows the reconnect"). The reconnect cannot recover this final:
    _restart_transport() nulls _asr_session before closing it, after which
    every provider callback is dropped by is_adopted_candidate(). Reaching this
    point proves the old session is still adopted -- the reconnect has not
    started and the final is right behind this endpoint in the ordered FIFO.
    Completing the confirmation in place, so that final finds a DRAINING turn,
    is the only way not to lose the utterance.

    Measured on HEAD: state=PREWARMING, credit still 1, sealed_token=None, both
    the warm-expiry and provider-final timers None, transcripts only ["first"]
    -- the whole sentence lost AND a busy flag left with no timer behind it.
    """
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.CANDIDATE_PAUSE,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("first", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    component = runtime._asr_runtime
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
    assert component._asr_overlap_completed_turns == 1
    recorded_onset = component._asr_overlap_completed_onsets[0]

    # 不打桩：跑真实控制流，只让传输在两条有序回调之间掉线。
    component._asr_session.is_ready = False

    await runtime._handle_independent_asr_endpoint(epoch)

    # 仍然封口，那条排在后面的 final 才有 DRAINING 可落。
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.DRAINING
    # 恰好兑付一次，不多不少。
    assert component._asr_overlap_completed_turns == 0
    assert list(component._asr_overlap_completed_onsets) == []
    assert component._asr_overlap_completed_token is None
    # onset 被本轮消费掉，不会被后面某个不相干的回合当成自己的起点。
    assert component._asr_pending_speech_onset_at is None
    # 用的是用户当初真实开口的时刻，不是这次重放的时刻。
    assert component._asr_turn_onset_at == recorded_onset
    # 忙窗口有定时器兜底（HEAD 上这里是 None）。
    assert component._asr_final_watchdog_task is not None
    # 没走 fail-closed 出口：那条会 bump epoch、拆掉 session、把语音判死。
    # 只有这组断言能区分两条出口——错误出口也会发同名 status。
    assert runtime._asr_session_epoch == epoch
    assert runtime._asr_lifecycle is not None

    await runtime._handle_independent_asr_final("second", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert [
        call.args[0] for call in runtime.handle_input_transcript.await_args_list
    ] == ["first", "second"]
    # 收尾不留忙标志。
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE


@pytest.mark.unit
async def test_an_unwoken_redemption_never_parks_in_an_untimed_busy_state() -> None:
    """Invariant: a busy state must always carry a timer, whatever the fix is.

    Asserts the absence of the combination "busy state AND both timers None"
    rather than any particular implementation, so it survives a different
    compensation strategy later. HEAD lands squarely in that forbidden
    combination.
    """
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch
    component = runtime._asr_runtime

    for event in (
        SpeechActivityEvent.SPEECH_STARTED,
        SpeechActivityEvent.SPEECH_RESUMED,
        SpeechActivityEvent.CANDIDATE_PAUSE,
    ):
        await runtime._handle_independent_asr_activity(event, epoch)
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("first", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    component._asr_session.is_ready = False
    await runtime._handle_independent_asr_endpoint(epoch)

    state = (
        runtime._asr_lifecycle.snapshot.state
        if runtime._asr_lifecycle is not None
        else None
    )
    busy = {
        VoiceLifecycleState.PREWARMING,
        VoiceLifecycleState.ACTIVE,
        VoiceLifecycleState.DRAINING,
    }
    assert not (
        state in busy
        and component._asr_warm_expiry_task is None
        and component._asr_final_watchdog_task is None
    ), "忙标志停在了没有任何定时器兜底的状态上"


async def test_lease_resync_does_not_hand_a_successor_the_replaced_episode() -> None:
    """A takeover inside the display send must not reach the new recorder.

    The display push is an await, so the voice-owner lookup that follows it
    can resolve the SUCCESSOR's socket. Withholding the ledger commit
    afterwards is not enough -- a delivered status cannot be retracted.
    """

    runtime = _Runtime()
    assert runtime._begin_voice_input_connection("chat-window") is True

    send_started = asyncio.Event()
    release_send = asyncio.Event()
    owner_payloads: list[dict] = []

    async def stalling_send_status(_message: str) -> bool:
        send_started.set()
        await release_send.wait()
        return True

    async def record_owner_send(payload: dict):
        owner_payloads.append(payload)
        return successor_socket

    successor_socket = object()
    runtime.send_status = AsyncMock(side_effect=stalling_send_status)
    runtime._voice_owner_socket = lambda: successor_socket
    runtime._send_to_voice_owner = record_owner_send

    signal = asyncio.create_task(runtime._maybe_signal_voice_lease_resync())
    await asyncio.wait_for(send_started.wait(), timeout=1)

    # A different window claims the microphone while the display push is stuck.
    assert runtime._begin_voice_input_connection("recorder-window") is True
    release_send.set()
    await asyncio.wait_for(signal, timeout=1)

    assert owner_payloads == []
    assert runtime._voice_lease_resync_signal_state is None
