"""Proactive TTS sid race-guard unit tests.

Coverage:
1. feed_tts_chunk expected_speech_id semantics (backward-compatible + race-safe drop)
2. handle_text_data / handle_output_transcript contextvar-based guards
3. finish_proactive_delivery expected_speech_id semantics
4. Per-task contextvar isolation (proactive guards never affect user turns)
5. End-to-end proactive streaming + user interruption; discard residual proactive chunks,
   preserving user replies
"""
import asyncio
import os
import sys
from queue import Queue
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from main_logic.core import (
    LLMSessionManager,
    _proactive_expected_sid,
    _proactive_published_text_chunks,
)
from main_logic.core import proactive as proactive_core
from main_logic.session_state import ProactivePhase, SessionEvent, SessionStateMachine
from tests.fake_clock import patch_module_clock


def _make_mgr() -> LLMSessionManager:
    """Bypass __init__, supplying minimal properties required for guard tests.

    __init__ loads config / character data / voice dependencies, irrelevant to sid guard
    behavior and only coupling tests to environment.
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
    mgr.core_api_type = "qwen"
    mgr.voice_id = "voice-a"
    mgr._is_free_preset_voice = False
    mgr._tts_runtime_key = LLMSessionManager._build_tts_runtime_key(mgr)
    mgr.current_speech_id = None
    mgr.last_user_engagement_time = None
    mgr._tts_done_queued_for_turn = False
    mgr.lanlan_name = "Test"
    mgr.session = None
    mgr.websocket = None
    mgr.sync_message_queue = Queue()
    mgr._enqueue_tts_text_chunk = MagicMock()
    mgr._respawn_tts_worker = MagicMock()
    mgr._tts_markdown_stripper = MagicMock()
    mgr._tts_markdown_stripper.flush.return_value = ""
    mgr._tts_bracket_stripper = MagicMock()
    mgr._tts_bracket_stripper.feed.side_effect = lambda text: text
    mgr._tts_bracket_stripper.flush.return_value = ""
    mgr._tts_norm_speech_id = None
    mgr.emotion_pattern = MagicMock()
    mgr.emotion_pattern.sub.side_effect = lambda _replacement, text: text
    mgr.send_lanlan_response = AsyncMock()
    mgr._takeover_active = False
    mgr._takeover_input_dispatcher = None
    # State machine: finish_proactive_delivery / handle_new_message fire events,
    # so test manager needs real lightweight SM instance, without external dependencies.
    mgr.state = SessionStateMachine(lanlan_name="Test")
    # finish_proactive_delivery calls _flush_ai_turn_text_to_tracker → activity_tracker;
    # mock it without affecting sid guard semantics.
    mgr._activity_tracker = MagicMock()
    mgr._current_ai_turn_text = ''
    return mgr


# ─────────────────────────────────────────────────────────────────────────────
# feed_tts_chunk
# ─────────────────────────────────────────────────────────────────────────────

async def test_feed_tts_chunk_no_expected_sid_enqueues():
    """Backward compatibility: callers omitting expected_speech_id behave unchanged."""
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
    """User changes sid during proactive generation: discard chunk, never send under new sid."""
    mgr = _make_mgr()
    mgr.current_speech_id = "s_user"  # User already took over.
    await LLMSessionManager.feed_tts_chunk(mgr, "proactive tail", expected_speech_id="s_proactive")
    mgr._enqueue_tts_text_chunk.assert_not_called()
    assert mgr.tts_pending_chunks == []


async def test_feed_tts_chunk_drops_when_engagement_advances_while_waiting():
    mgr = _make_mgr()
    mgr.current_speech_id = "s_proactive"
    mgr.last_user_engagement_time = 100.0

    await mgr.tts_cache_lock.acquire()
    feed_task = asyncio.create_task(
        LLMSessionManager.feed_tts_chunk(
            mgr,
            "stale reminder",
            expected_speech_id="s_proactive",
            expected_user_engagement_time=100.0,
        )
    )
    await asyncio.sleep(0)
    mgr.last_user_engagement_time = 200.0
    mgr.tts_cache_lock.release()

    accepted = await feed_task

    assert accepted is False
    mgr._enqueue_tts_text_chunk.assert_not_called()
    assert mgr.tts_pending_chunks == []


async def test_feed_tts_chunk_drop_is_atomic_with_enqueue():
    """Check under lock so check and enqueue cannot interleave."""
    mgr = _make_mgr()
    mgr.current_speech_id = "s_proactive"

    enqueue_calls: list[str] = []

    def _spy_enqueue(sid, text):
        enqueue_calls.append((sid, text))

    mgr._enqueue_tts_text_chunk.side_effect = _spy_enqueue

    async def flipper():
        """Change sid during feed_tts_chunk to test whether a drop can leak through."""
        # Let feed_tts_chunk enter lock first; wait for lock here before changing sid.
        async with mgr.tts_cache_lock:
            mgr.current_speech_id = "s_user"

    # Trigger feed_tts_chunk with concurrent flipper; tts_cache_lock protects check+enqueue.
    await asyncio.gather(
        LLMSessionManager.feed_tts_chunk(mgr, "x", expected_speech_id="s_proactive"),
        flipper(),
    )
    # Whoever locks first, either s_proactive enqueues successfully or flipper causes drop;
    # never stale check-passed-but-sid-changed-at-enqueue data.
    if enqueue_calls:
        assert enqueue_calls[0][0] == "s_proactive"


def test_can_preserve_tts_ready_for_live_worker():
    mgr = _make_mgr()
    mgr.tts_ready = True
    mgr.tts_thread.is_alive.return_value = True

    assert LLMSessionManager._can_preserve_tts_ready_for_session_start(mgr) is True


def test_cannot_preserve_tts_ready_for_dead_or_unready_worker():
    mgr = _make_mgr()

    mgr.tts_ready = False
    mgr.tts_thread.is_alive.return_value = True
    assert LLMSessionManager._can_preserve_tts_ready_for_session_start(mgr) is False

    mgr.tts_ready = True
    mgr.tts_thread.is_alive.return_value = False
    assert LLMSessionManager._can_preserve_tts_ready_for_session_start(mgr) is False


def test_cannot_preserve_tts_ready_when_runtime_identity_changed():
    mgr = _make_mgr()
    mgr.tts_ready = True
    mgr.tts_thread.is_alive.return_value = True
    mgr.voice_id = "voice-b"

    assert LLMSessionManager._can_preserve_tts_ready_for_session_start(mgr) is False


async def test_prepare_proactive_delivery_counts_recent_ui_engagement(
    monkeypatch,
):
    mgr = _make_mgr()
    mgr.is_goodbye_silent = MagicMock(return_value=False)
    mgr.last_user_activity_time = 10.0
    mgr.last_user_engagement_time = 95.0
    # prepare_proactive_delivery's _user_active_recently reads time.time() in main_logic.core.proactive;
    # patch clock in that module only.
    patch_module_clock(monkeypatch, proactive_core, time=lambda: 100.0)

    prepared = await LLMSessionManager.prepare_proactive_delivery(
        mgr,
        min_idle_secs=10.0,
    )

    assert prepared is False


async def test_prepare_proactive_delivery_rechecks_ui_engagement_before_claim(
    monkeypatch,
):
    mgr = _make_mgr()
    mgr.is_goodbye_silent = MagicMock(return_value=False)
    mgr.is_active = False
    mgr.websocket = object()
    mgr.last_user_activity_time = 10.0
    mgr.last_user_engagement_time = None
    # prepare_proactive_delivery's _user_active_recently reads time.time() in main_logic.core.proactive;
    # patch clock in that module only.
    patch_module_clock(monkeypatch, proactive_core, time=lambda: 100.0)

    async def start_session_after_engagement(*_args, **_kwargs):
        mgr.session = SimpleNamespace(_conversation_history=[])
        mgr.last_user_engagement_time = 95.0

    mgr.start_session = AsyncMock(side_effect=start_session_after_engagement)

    prepared = await LLMSessionManager.prepare_proactive_delivery(
        mgr,
        min_idle_secs=10.0,
    )

    assert prepared is False
    assert mgr.current_speech_id is None


async def test_prepare_proactive_delivery_rechecks_engagement_after_claim_wait(
    monkeypatch,
):
    """A UI click while PROACTIVE_CLAIM waits must abort and release the phase."""
    mgr = _make_mgr()
    mgr.is_goodbye_silent = MagicMock(return_value=False)
    mgr.is_active = False
    mgr.websocket = object()
    mgr.session = SimpleNamespace(_conversation_history=[])
    mgr.last_user_activity_time = 10.0
    mgr.last_user_engagement_time = None
    # prepare_proactive_delivery's _user_active_recently reads time.time() in main_logic.core.proactive;
    # patch clock in that module only.
    patch_module_clock(monkeypatch, proactive_core, time=lambda: 100.0)
    assert await mgr.state.try_start_proactive() is True

    claim_started = asyncio.Event()
    release_claim = asyncio.Event()
    original_fire = mgr.state.fire

    async def blocked_fire(event, **payload):
        if event is SessionEvent.PROACTIVE_CLAIM:
            claim_started.set()
            await release_claim.wait()
        await original_fire(event, **payload)

    mgr.state.fire = blocked_fire
    prepare_task = asyncio.create_task(
        LLMSessionManager.prepare_proactive_delivery(
            mgr,
            min_idle_secs=10.0,
        )
    )
    await claim_started.wait()
    mgr.last_user_engagement_time = 95.0
    release_claim.set()

    prepared = await prepare_task

    assert prepared is False
    assert mgr.state.phase is ProactivePhase.IDLE


# ─────────────────────────────────────────────────────────────────────────────
# handle_text_data text-mode contextvar guard.
# ─────────────────────────────────────────────────────────────────────────────

async def test_handle_text_data_no_contextvar_always_passes():
    """User stream_text has contextvar=None and must not drop."""
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


async def test_handle_text_data_records_only_actual_publish_boundary():
    mgr = _make_mgr()
    mgr.current_speech_id = "s_proactive"
    published_chunks: list[str] = []

    async def publish_then_fail_websocket(*_args, **kwargs):
        assert kwargs["turn_id"] == "s_proactive"
        assert kwargs["expected_speech_id"] == "s_proactive"
        kwargs["on_published"](100.0)
        return False

    mgr.send_lanlan_response = AsyncMock(side_effect=publish_then_fail_websocket)
    sid_token = _proactive_expected_sid.set("s_proactive")
    published_token = _proactive_published_text_chunks.set(published_chunks)
    try:
        await LLMSessionManager.handle_text_data(mgr, "visible", is_first_chunk=False)
    finally:
        _proactive_published_text_chunks.reset(published_token)
        _proactive_expected_sid.reset(sid_token)

    assert published_chunks == ["visible"]
    mgr._enqueue_tts_text_chunk.assert_called_once_with("s_proactive", "visible")


async def test_handle_text_data_internal_publish_rejection_skips_tts():
    mgr = _make_mgr()
    mgr.current_speech_id = "s_proactive"
    mgr.send_lanlan_response = AsyncMock(return_value=None)
    published_chunks: list[str] = []
    sid_token = _proactive_expected_sid.set("s_proactive")
    published_token = _proactive_published_text_chunks.set(published_chunks)
    try:
        await LLMSessionManager.handle_text_data(mgr, "stale", is_first_chunk=False)
    finally:
        _proactive_published_text_chunks.reset(published_token)
        _proactive_expected_sid.reset(sid_token)

    assert published_chunks == []
    mgr._enqueue_tts_text_chunk.assert_not_called()


async def test_handle_text_data_contextvar_mismatch_drops_all_writes():
    """sid guard blocks frontend and TTS; proactive text never enters user bubbles."""
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
    """Regression: is_first_chunk clears TTS queue; drop path must never trigger it."""
    mgr = _make_mgr()
    mgr.current_speech_id = "s_user"
    mgr.tts_pending_chunks = [("s_user", "user_cached")]
    mgr.tts_response_queue.put(b"user audio bytes")

    token = _proactive_expected_sid.set("s_proactive")
    try:
        await LLMSessionManager.handle_text_data(mgr, "stale", is_first_chunk=True)
    finally:
        _proactive_expected_sid.reset(token)

    # User's pending chunks and queue must remain untouched.
    assert mgr.tts_pending_chunks == [("s_user", "user_cached")]
    assert not mgr.tts_response_queue.empty()


async def test_handle_text_data_preempted_while_waiting_does_not_clear_user_queue():
    mgr = _make_mgr()
    mgr.current_speech_id = "s_proactive"
    await mgr.tts_cache_lock.acquire()
    sid_token = _proactive_expected_sid.set("s_proactive")
    try:
        stale_task = asyncio.create_task(
            LLMSessionManager.handle_text_data(
                mgr, "stale proactive", is_first_chunk=True
            )
        )
        await asyncio.sleep(0)
        mgr.current_speech_id = "s_user"
        mgr.tts_pending_chunks = [("s_user", "user_cached")]
        mgr.tts_response_queue.put(b"user audio bytes")
        mgr.tts_cache_lock.release()
        await stale_task
    finally:
        if mgr.tts_cache_lock.locked():
            mgr.tts_cache_lock.release()
        _proactive_expected_sid.reset(sid_token)

    assert mgr.tts_pending_chunks == [("s_user", "user_cached")]
    assert not mgr.tts_response_queue.empty()
    mgr.send_lanlan_response.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# handle_output_transcript voice-mode contextvar guard.
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
# handle_output_transcript turn identity across await (second aspect of #2612).
#
# Falsifiable contracts spelled out in test names:
#   1. Text belongs to turn active at function entry, never whichever turn happens
#      active when publish returns. Ordinary voice lacks proactive contextvar but still has turn identity.
#   2. Turn replaced during publish → discard whole segment, never reassign new sid.
#   3. Frontend turn_id belongs to same turn.
# ─────────────────────────────────────────────────────────────────────────────


def _publish_blocks_until(mgr, gate: asyncio.Event) -> None:
    """Make send_lanlan_response hang the way a dead frontend socket does.

    That is how #2612 reproduces: the publish suspends on the WebSocket and
    the user starts a new turn while it is suspended.
    """

    async def _hangs_on_the_frontend(*_args, **_kwargs):
        await gate.wait()
        return True

    mgr.send_lanlan_response = AsyncMock(side_effect=_hangs_on_the_frontend)


async def test_output_transcript_queues_under_the_turn_it_started_in():
    mgr = _make_mgr()
    mgr.current_speech_id = "s_ai_turn"

    await LLMSessionManager.handle_output_transcript(mgr, "正文", is_first_chunk=False)

    mgr._enqueue_tts_text_chunk.assert_called_once_with("s_ai_turn", "正文")
    assert mgr.send_lanlan_response.call_args.kwargs["turn_id"] == "s_ai_turn"


async def test_output_transcript_dropped_when_a_user_turn_starts_mid_publish():
    mgr = _make_mgr()
    mgr.current_speech_id = "s_ai_turn"
    released = asyncio.Event()
    _publish_blocks_until(mgr, released)

    chunk = asyncio.create_task(
        LLMSessionManager.handle_output_transcript(mgr, "尾巴", is_first_chunk=False)
    )
    await asyncio.sleep(0)
    # handle_new_message assigns user's new sid in another task.
    mgr.current_speech_id = "s_user_new_turn"
    released.set()
    await asyncio.wait_for(chunk, timeout=1)

    mgr._enqueue_tts_text_chunk.assert_not_called()
    assert mgr.tts_pending_chunks == [], (
        "死掉那一轮的尾巴不能进新轮次的 TTS，缓存分支也一样"
    )


async def test_output_transcript_cached_under_the_turn_it_started_in():
    """The not-ready cache branch shares a source with the direct one;
    pinning only one of them is pinning neither."""
    mgr = _make_mgr()
    mgr.current_speech_id = "s_ai_turn"
    mgr.tts_ready = False
    released = asyncio.Event()
    _publish_blocks_until(mgr, released)

    chunk = asyncio.create_task(
        LLMSessionManager.handle_output_transcript(mgr, "正文", is_first_chunk=False)
    )
    await asyncio.sleep(0)
    released.set()
    await asyncio.wait_for(chunk, timeout=1)

    assert mgr.tts_pending_chunks == [("s_ai_turn", "正文")]


async def test_output_transcript_proactive_preempted_mid_publish_drops():
    """The entry contextvar check cannot see a preemption that happens
    during the publish."""
    mgr = _make_mgr()
    mgr.current_speech_id = "s_proactive"
    released = asyncio.Event()
    _publish_blocks_until(mgr, released)

    token = _proactive_expected_sid.set("s_proactive")
    try:
        chunk = asyncio.create_task(
            LLMSessionManager.handle_output_transcript(mgr, "搭话", is_first_chunk=False)
        )
        await asyncio.sleep(0)
        mgr.current_speech_id = "s_user_new_turn"
        released.set()
        await asyncio.wait_for(chunk, timeout=1)
    finally:
        _proactive_expected_sid.reset(token)

    mgr._enqueue_tts_text_chunk.assert_not_called()
    assert mgr.tts_pending_chunks == []


# ─────────────────────────────────────────────────────────────────────────────
# finish_proactive_delivery expected_speech_id.
# ─────────────────────────────────────────────────────────────────────────────

async def test_finish_proactive_delivery_no_expected_sid_runs_all():
    """Backward compatibility: omitted expected_speech_id delivers normally, returning True."""
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


async def test_finish_records_proactive_at_sync_publication_time(monkeypatch):
    """An immediate user response must remain newer than the delivered item."""
    import memory.anti_repeat as anti_repeat

    mgr = _make_mgr()
    mgr.current_speech_id = "s_proactive"
    mgr.last_user_engagement_time = None
    mgr.session = MagicMock()
    mgr.session._conversation_history = []
    # Two-stage: stage_output precedes completion signal, persistence follows through flush_staged_detached,
    # synchronous return with internal task, leaving no suspension here; cancellation cannot undo
    # delivered turn into undelivered (counterpart in test_anti_repeat).
    # Keep aflush_staged AsyncMock: reintroduced await on MagicMock raises,
    # caller except swallows it and test becomes vacuously always green.
    corpus = MagicMock()
    corpus.stage_output = MagicMock(return_value=("Test", {"window": []}, 1))
    corpus.flush_staged_detached = MagicMock()
    corpus.aflush_staged = AsyncMock()
    monkeypatch.setattr(
        anti_repeat,
        "get_anti_repeat_corpus",
        lambda: corpus,
    )

    async def publish_then_engage(*_args, **kwargs):
        kwargs["on_published"](100.0)
        mgr.last_user_engagement_time = 101.0
        return True

    mgr.send_lanlan_response = AsyncMock(side_effect=publish_then_engage)

    result = await LLMSessionManager.finish_proactive_delivery(
        mgr,
        "delivered before immediate reply",
        expected_speech_id="s_proactive",
        expected_user_engagement_time=None,
    )

    assert result is True
    corpus.stage_output.assert_called_once_with(
        "Test",
        "delivered before immediate reply",
        is_proactive=True,
        now=100.0,
    )
    corpus.flush_staged_detached.assert_called_once_with(
        corpus.stage_output.return_value
    )
    corpus.aflush_staged.assert_not_awaited()
    corpus.record_output.assert_not_called()


async def test_finish_proactive_delivery_sid_mismatch_skips_all_writes():
    """User interruption between Phase 2 and finish skips every finish side effect, returning False.

    Routes rely on False to short-circuit _record_proactive_chat / topic usage /
    surfaced reflections, avoiding undelivered content recorded as delivered.
    """
    mgr = _make_mgr()
    mgr.current_speech_id = "s_user"  # User takeover.
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


async def test_finish_proactive_delivery_engagement_mismatch_skips_all_writes():
    """UI-only engagement after TTS feed must block the final visible commit."""
    mgr = _make_mgr()
    mgr.current_speech_id = "s_proactive"
    mgr.last_user_engagement_time = 200.0
    mgr.session = MagicMock()
    mgr.session._conversation_history = []

    result = await LLMSessionManager.finish_proactive_delivery(
        mgr,
        "stale proactive",
        expected_speech_id="s_proactive",
        expected_user_engagement_time=100.0,
    )

    assert result is False
    mgr.send_lanlan_response.assert_not_called()
    assert mgr.session._conversation_history == []
    assert mgr._tts_done_queued_for_turn is False
    assert mgr.sync_message_queue.empty()


async def test_finish_proactive_delivery_rechecks_after_committing_wait():
    """Engagement during state.fire(COMMITTING) must lose at the publish guard."""
    mgr = _make_mgr()
    mgr.current_speech_id = "s_proactive"
    mgr.last_user_engagement_time = 100.0
    mgr.session = MagicMock()
    mgr.session._conversation_history = []
    assert await mgr.state.try_start_proactive() is True
    await mgr.state.fire(SessionEvent.PROACTIVE_CLAIM, sid="s_proactive")
    await mgr.state.fire(SessionEvent.PROACTIVE_PHASE2)

    committing_started = asyncio.Event()
    release_committing = asyncio.Event()
    original_fire = mgr.state.fire

    async def blocked_fire(event, **payload):
        if event is SessionEvent.PROACTIVE_COMMITTING:
            committing_started.set()
            await release_committing.wait()
        await original_fire(event, **payload)

    async def guarded_send(*_args, **kwargs):
        if (
            mgr.last_user_engagement_time
            != kwargs["expected_user_engagement_time"]
        ):
            return None
        return True

    mgr.state.fire = blocked_fire
    mgr.send_lanlan_response = AsyncMock(side_effect=guarded_send)
    finish_task = asyncio.create_task(
        LLMSessionManager.finish_proactive_delivery(
            mgr,
            "stale proactive",
            expected_speech_id="s_proactive",
            expected_user_engagement_time=100.0,
        )
    )
    await committing_started.wait()
    mgr.last_user_engagement_time = 200.0
    release_committing.set()

    committed = await finish_task

    assert committed is False
    assert mgr.session._conversation_history == []
    assert mgr.sync_message_queue.empty()


async def _owned_round(mgr, round_id="round-1"):
    """Start a proactive round the way the service does: SM claim + manager ID."""
    assert await mgr.state.try_start_proactive(round_id=round_id) is True
    await mgr.state.fire(SessionEvent.PROACTIVE_CLAIM, sid="s_proactive", round_id=round_id)
    await mgr.state.fire(SessionEvent.PROACTIVE_PHASE2, round_id=round_id)
    mgr._proactive_screenshot_request_id = round_id
    mgr.current_speech_id = "s_proactive"
    return round_id


async def _retire_round(mgr):
    """Session teardown: retires the round but leaves current_speech_id alone."""
    mgr._proactive_screenshot_request_id = None
    await mgr.state.reset(force=True)


class _SignalingLock(asyncio.Lock):
    """Signals when a caller starts waiting, i.e. after its pre-lock checks."""

    def __init__(self):
        super().__init__()
        self.waiting = asyncio.Event()

    async def acquire(self):
        self.waiting.set()
        return await super().acquire()


async def test_feed_tts_chunk_drops_when_round_retires_while_waiting():
    mgr = _make_mgr()
    round_id = await _owned_round(mgr)
    mgr.tts_cache_lock = _SignalingLock()

    await mgr.tts_cache_lock.acquire()
    mgr.tts_cache_lock.waiting.clear()
    feed_task = asyncio.create_task(
        LLMSessionManager.feed_tts_chunk(
            mgr, "stale proactive", expected_speech_id="s_proactive",
            expected_round_id=round_id,
        )
    )
    # Retire only once the task has passed the pre-lock check and is waiting,
    # so this case exercises the in-lock recheck.
    await mgr.tts_cache_lock.waiting.wait()
    await _retire_round(mgr)
    mgr.tts_cache_lock.release()

    assert await feed_task is False
    assert mgr.current_speech_id == "s_proactive", "the SID guard alone cannot see this"
    mgr._enqueue_tts_text_chunk.assert_not_called()
    assert mgr.tts_pending_chunks == []


async def test_feed_tts_chunk_enqueues_for_the_owned_round():
    mgr = _make_mgr()
    round_id = await _owned_round(mgr)
    assert await LLMSessionManager.feed_tts_chunk(
        mgr, "hi", expected_speech_id="s_proactive", expected_round_id=round_id,
    ) is True
    mgr._enqueue_tts_text_chunk.assert_called_once_with("s_proactive", "hi")


async def test_finish_proactive_delivery_skips_a_retired_round():
    mgr = _make_mgr()
    round_id = await _owned_round(mgr)
    mgr.session = MagicMock()
    mgr.session._conversation_history = []
    await _retire_round(mgr)

    result = await LLMSessionManager.finish_proactive_delivery(
        mgr, "stale proactive", expected_speech_id="s_proactive",
        expected_round_id=round_id,
    )

    assert result is False
    mgr.send_lanlan_response.assert_not_called()
    assert mgr.session._conversation_history == []


async def test_finish_proactive_delivery_rechecks_round_at_publish_boundary():
    """Teardown during state.fire(COMMITTING) must lose at the publish guard."""
    mgr = _make_mgr()
    round_id = await _owned_round(mgr)
    mgr.session = MagicMock()
    mgr.session._conversation_history = []

    committing_started = asyncio.Event()
    release_committing = asyncio.Event()
    original_fire = mgr.state.fire
    committing_payloads = []

    async def blocked_fire(event, **payload):
        if event is SessionEvent.PROACTIVE_COMMITTING:
            committing_payloads.append(payload)
            committing_started.set()
            await release_committing.wait()
        await original_fire(event, **payload)

    async def guarded_send(*_args, **kwargs):
        publish_if = kwargs.get("publish_if")
        if publish_if is not None and not publish_if():
            return None
        return True

    mgr.state.fire = blocked_fire
    mgr.send_lanlan_response = AsyncMock(side_effect=guarded_send)
    finish_task = asyncio.create_task(
        LLMSessionManager.finish_proactive_delivery(
            mgr, "stale proactive", expected_speech_id="s_proactive",
            expected_round_id=round_id,
        )
    )
    await committing_started.wait()
    await _retire_round(mgr)
    release_committing.set()

    assert await finish_task is False
    assert committing_payloads == [{"round_id": round_id}]
    assert mgr.session._conversation_history == []
    assert mgr.sync_message_queue.empty()


# ─────────────────────────────────────────────────────────────────────────────
# Per-task contextvar isolation, core correctness premise for this fix.
# ─────────────────────────────────────────────────────────────────────────────

async def test_contextvar_isolated_across_tasks():
    """Contextvar set by one task is invisible to another.

    Guard design foundation: proactive guard affects its own callback chain only;
    user stream_text callbacks run in another task, always reading None.
    """
    observed: dict[str, str | None] = {}

    async def proactive():
        token = _proactive_expected_sid.set("s_proactive")
        try:
            await asyncio.sleep(0)  # Yield.
            observed["proactive"] = _proactive_expected_sid.get()
        finally:
            _proactive_expected_sid.reset(token)

    async def user():
        await asyncio.sleep(0)  # Let proactive set first.
        observed["user"] = _proactive_expected_sid.get()

    await asyncio.gather(asyncio.create_task(proactive()), asyncio.create_task(user()))
    assert observed["proactive"] == "s_proactive"
    assert observed["user"] is None  # User task cannot see proactive contextvar.


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end race: proactive stream + user interruption.
# ─────────────────────────────────────────────────────────────────────────────

async def test_end_to_end_proactive_interrupted_by_user():
    """Reproduce proactive handle_text_data feeding TTS when user suddenly changes sid.

    Expect:
    - Pre-interruption proactive chunks enqueue with proactive sid
    - Residual post-interruption proactive chunks drop via guard
    - User reply chunks enqueue with user sid, unaffected
    """
    mgr = _make_mgr()
    mgr.current_speech_id = "s_proactive"
    published_chunks: list[str] = []

    async def publish(*_args, **kwargs):
        if kwargs["on_published"] is not None:
            kwargs["on_published"](100.0)
        return True

    mgr.send_lanlan_response = AsyncMock(side_effect=publish)

    user_started = asyncio.Event()
    proactive_can_continue = asyncio.Event()

    async def proactive_flow():
        sid_token = _proactive_expected_sid.set("s_proactive")
        published_token = _proactive_published_text_chunks.set(published_chunks)
        try:
            # First chunk: sid remains s_proactive, enqueue.
            await LLMSessionManager.handle_text_data(mgr, "p_early", is_first_chunk=False)
            # Yield to user, await sid change.
            user_started.set()
            await proactive_can_continue.wait()
            # Second chunk: user changed sid, guard blocks.
            await LLMSessionManager.handle_text_data(mgr, "p_late", is_first_chunk=False)
        finally:
            _proactive_published_text_chunks.reset(published_token)
            _proactive_expected_sid.reset(sid_token)

    async def user_flow():
        await user_started.wait()
        # Simulate stream_text sid change (real path also clears queue; guard semantics suffice here).
        mgr.current_speech_id = "s_user"
        # User reply chunks lack proactive contextvar.
        await LLMSessionManager.handle_text_data(mgr, "u_reply", is_first_chunk=False)
        proactive_can_continue.set()

    await asyncio.gather(
        asyncio.create_task(proactive_flow()),
        asyncio.create_task(user_flow()),
    )

    # Core assertion: discard p_late; p_early/u_reply each enqueue once.
    enqueued_texts = [c.args[1] for c in mgr._enqueue_tts_text_chunk.call_args_list]
    assert "p_early" in enqueued_texts
    assert "u_reply" in enqueued_texts
    assert "p_late" not in enqueued_texts
    assert len(enqueued_texts) == 2
    assert published_chunks == ["p_early"]

    # Enqueued sid must match current_speech_id at that moment.
    enqueued_sids = {c.args[1]: c.args[0] for c in mgr._enqueue_tts_text_chunk.call_args_list}
    assert enqueued_sids["p_early"] == "s_proactive"
    assert enqueued_sids["u_reply"] == "s_user"


@pytest.mark.parametrize("live_consent", [None, True, False])
async def test_finish_proactive_delivery_stages_screenshot_only_under_live_consent(live_consent):
    """A revoked round must not leave its screen for the next user turn."""
    mgr = _make_mgr()
    mgr.current_speech_id = "s_proactive"
    mgr._proactive_vision_enabled = live_consent
    mgr.session = MagicMock()
    mgr.session._conversation_history = []

    committed = await LLMSessionManager.finish_proactive_delivery(
        mgr, "看到你在写代码", expected_speech_id="s_proactive",
        vision_screenshot_b64="SHOT_B64",
    )

    assert committed is True
    mgr.session.set_proactive_screenshot.assert_called_once_with(
        None if live_consent is False else "SHOT_B64"
    )
