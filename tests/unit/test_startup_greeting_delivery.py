from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import main_logic.core as core_module
import main_logic.core.greeting as greeting_module
from config.prompts.prompts_proactive import _STARTUP_GREETING_VARIANTS
from main_logic.omni_offline_client import OmniOfflineClient
from main_logic.session_state import ProactivePhase, SessionEvent, SessionStateMachine
from memory.startup_greeting_history import (
    StartupGreetingHistory,
    StartupGreetingRecord,
)
from tests.fake_clock import patch_module_clock


class _Response:
    def __init__(self, payload, *, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300

    def json(self):
        return self._payload


class _GreetingMemoryClient:
    def __init__(self, *, topics=None, followup_error: Exception | None = None):
        self.topics = list(topics or [])
        self.followup_error = followup_error
        self.get_calls = []
        self.post_calls = []

    async def get(self, url, *, timeout):
        self.get_calls.append((url, timeout))
        if "/last_conversation_gap/" in url:
            return _Response({"gap_seconds": 4 * 60 * 60})
        if "/followup_topics/" in url:
            if self.followup_error:
                raise self.followup_error
            return _Response({"topics": self.topics})
        raise AssertionError(f"unexpected GET {url}")

    async def post(self, url, *, json, timeout):
        self.post_calls.append((url, json, timeout))
        return _Response({"ok": True})


class _GreetingSession(OmniOfflineClient):
    def __init__(
        self,
        *,
        delivered: bool = True,
        raise_after_commit: BaseException | None = None,
    ):
        self._is_responding = False
        self.delivered = delivered
        self.raise_after_commit = raise_after_commit
        self.instructions = []

    async def prompt_ephemeral(
        self,
        instruction,
        *,
        images=None,
        on_committed=None,
        on_committed_text=None,
    ):
        self.instructions.append(instruction)
        if self.delivered:
            if on_committed_text:
                on_committed_text("A different committed startup greeting.")
            if on_committed:
                on_committed()
        if self.raise_after_commit:
            raise self.raise_after_commit
        return self.delivered


class _HistoryDouble:
    def __init__(self, records=None):
        self.records = list(records or [])
        self.apreload = AsyncMock()
        self.stage_committed = MagicMock(return_value="history-stage")
        self.flush_staged_detached = MagicMock()
        self.try_reserve = MagicMock(return_value="reservation-token")
        self.release_reservation = MagicMock()

    def recent(self, *_args, **_kwargs):
        return list(self.records)


class _AntiRepeatDouble:
    def __init__(self):
        self.apreload = AsyncMock()
        self.stage_output = MagicMock(return_value="anti-repeat-stage")
        self.flush_staged_detached = MagicMock()


def _make_manager(session):
    manager = core_module.LLMSessionManager.__new__(core_module.LLMSessionManager)
    manager.lanlan_name = "Test"
    manager.master_name = "Master"
    manager.user_language = "en"
    manager.memory_server_port = 48912
    manager.state = SessionStateMachine(lanlan_name="Test")
    manager.session = session
    manager.websocket = None
    manager.lock = asyncio.Lock()
    manager._proactive_write_lock = asyncio.Lock()
    manager._voice_proactive_inject_lock = asyncio.Lock()
    manager.current_speech_id = None
    manager._tts_done_queued_for_turn = False
    manager._voice_playback_active = False
    manager.is_active = False
    manager.input_mode = "text"
    manager._starting_session_count = 0
    manager._starting_input_mode = None
    manager._takeover_active = False
    manager.goodbye_silent = False
    manager.goodbye_silent_reason = ""
    manager.goodbye_silent_updated_at = 0.0
    manager.last_user_engagement_time = None
    manager.start_session = AsyncMock()
    manager._post_commit_tasks = []

    def _fire(coro):
        task = asyncio.create_task(coro)
        manager._post_commit_tasks.append(task)
        return task

    manager._fire_task = _fire
    return manager


def _patch_dependencies(
    monkeypatch, memory_client, history, anti_repeat, holiday_commit
):
    monkeypatch.setattr(
        "utils.internal_http_client.get_internal_http_client",
        lambda: memory_client,
    )
    monkeypatch.setattr(
        greeting_module, "get_startup_greeting_history", lambda: history
    )
    monkeypatch.setattr(greeting_module, "get_anti_repeat_corpus", lambda: anti_repeat)
    monkeypatch.setattr(
        "utils.holiday_cache.preview_holiday_or_weekend_hint",
        AsyncMock(return_value=("Holiday hint", "holiday-token")),
    )
    monkeypatch.setattr(
        "utils.holiday_cache.commit_holiday_or_weekend_hint", holiday_commit
    )


@pytest.mark.asyncio
async def test_startup_greeting_records_followup_only_after_text_commit(
    monkeypatch, capsys
):
    session = _GreetingSession(delivered=True)
    manager = _make_manager(session)
    memory_client = _GreetingMemoryClient(
        topics=[{"id": "ref_book", "text": "Continue discussing the book ending."}]
    )
    history = _HistoryDouble()
    anti_repeat = _AntiRepeatDouble()
    holiday_commit = MagicMock()
    _patch_dependencies(
        monkeypatch, memory_client, history, anti_repeat, holiday_commit
    )

    await core_module.LLMSessionManager.trigger_greeting(manager)
    await asyncio.gather(*manager._post_commit_tasks)

    assert len(session.instructions) == 1
    assert "Continue discussing the book ending." in session.instructions[0]
    captured = capsys.readouterr()
    assert "Continue discussing the book ending." not in captured.out
    assert "Continue discussing the book ending." not in captured.err
    history.stage_committed.assert_called_once_with(
        "Test",
        "A different committed startup greeting.",
        variant_key="memory_followup",
        topic_key="ref_book",
        reservation_token="reservation-token",
    )
    history.flush_staged_detached.assert_called_once_with("history-stage")
    history.release_reservation.assert_called_once_with("Test", "reservation-token")
    anti_repeat.stage_output.assert_called_once_with(
        "Test", "A different committed startup greeting.", is_proactive=True
    )
    anti_repeat.flush_staged_detached.assert_called_once_with("anti-repeat-stage")
    assert memory_client.post_calls == [
        (
            "http://127.0.0.1:48912/record_surfaced/Test",
            {"reflection_ids": ["ref_book"]},
            5.0,
        )
    ]
    holiday_commit.assert_called_once_with("Test", "holiday-token")
    assert manager.state.phase is ProactivePhase.IDLE


@pytest.mark.asyncio
async def test_startup_greeting_preserves_traditional_chinese_prompt_locale(
    monkeypatch,
):
    session = _GreetingSession(delivered=False)
    manager = _make_manager(session)
    manager.user_language = "zh-TW"
    memory_client = _GreetingMemoryClient()
    history = _HistoryDouble()
    anti_repeat = _AntiRepeatDouble()
    holiday_commit = MagicMock()
    _patch_dependencies(
        monkeypatch, memory_client, history, anti_repeat, holiday_commit
    )

    await core_module.LLMSessionManager.trigger_greeting(manager)

    assert len(session.instructions) == 1
    assert "距離你和Master上次有記錄的對話" in session.instructions[0]
    assert "請結合已經載入的近期對話" in session.instructions[0]
    assert "======以上为啟動問候約束======" in session.instructions[0]


@pytest.mark.asyncio
async def test_memory_followup_cooldown_skips_followup_topics_request(monkeypatch):
    session = _GreetingSession(delivered=False)
    manager = _make_manager(session)
    memory_client = _GreetingMemoryClient(
        topics=[{"id": "unused", "text": "This endpoint should not be queried."}]
    )
    history = _HistoryDouble(
        records=[
            StartupGreetingRecord(
                ts=1000.0,
                text="A prior memory follow-up.",
                variant_key="memory_followup",
                topic_key="used-topic",
            )
        ]
    )
    anti_repeat = _AntiRepeatDouble()
    holiday_commit = MagicMock()
    _patch_dependencies(
        monkeypatch, memory_client, history, anti_repeat, holiday_commit
    )
    patch_module_clock(monkeypatch, greeting_module, time=lambda: 4000.0)

    await core_module.LLMSessionManager.trigger_greeting(manager)

    assert len(session.instructions) == 1
    assert all("/followup_topics/" not in url for url, _timeout in memory_client.get_calls)


@pytest.mark.asyncio
async def test_startup_greeting_failed_delivery_consumes_nothing(monkeypatch):
    session = _GreetingSession(delivered=False)
    manager = _make_manager(session)
    memory_client = _GreetingMemoryClient(
        topics=[{"id": "ref_book", "text": "Continue the book."}]
    )
    history = _HistoryDouble()
    anti_repeat = _AntiRepeatDouble()
    holiday_commit = MagicMock()
    _patch_dependencies(
        monkeypatch, memory_client, history, anti_repeat, holiday_commit
    )

    await core_module.LLMSessionManager.trigger_greeting(manager)

    history.stage_committed.assert_not_called()
    anti_repeat.stage_output.assert_not_called()
    assert memory_client.post_calls == []
    holiday_commit.assert_not_called()
    assert manager._post_commit_tasks == []
    assert manager.state.phase is ProactivePhase.IDLE


@pytest.mark.asyncio
async def test_followup_read_failure_falls_back_to_plain_greeting(monkeypatch):
    session = _GreetingSession(delivered=True)
    manager = _make_manager(session)
    memory_client = _GreetingMemoryClient(followup_error=TimeoutError("offline"))
    history = _HistoryDouble()
    anti_repeat = _AntiRepeatDouble()
    holiday_commit = MagicMock()
    _patch_dependencies(
        monkeypatch, memory_client, history, anti_repeat, holiday_commit
    )

    await core_module.LLMSessionManager.trigger_greeting(manager)
    await asyncio.gather(*manager._post_commit_tasks)

    assert len(session.instructions) == 1
    history.stage_committed.assert_called_once()
    assert history.stage_committed.call_args.kwargs["topic_key"] is None
    assert memory_client.post_calls == []
    holiday_commit.assert_called_once()


@pytest.mark.asyncio
async def test_committed_then_completion_failure_still_records_and_releases_sm(
    monkeypatch,
):
    session = _GreetingSession(
        delivered=True,
        raise_after_commit=RuntimeError("completion failed after visible text"),
    )
    manager = _make_manager(session)
    memory_client = _GreetingMemoryClient(
        topics=[{"id": "ref_book", "text": "Continue the book."}]
    )
    history = _HistoryDouble()
    anti_repeat = _AntiRepeatDouble()
    holiday_commit = MagicMock()
    _patch_dependencies(
        monkeypatch, memory_client, history, anti_repeat, holiday_commit
    )

    with pytest.raises(RuntimeError, match="completion failed"):
        await core_module.LLMSessionManager.trigger_greeting(manager)
    await asyncio.gather(*manager._post_commit_tasks)

    history.stage_committed.assert_called_once()
    anti_repeat.stage_output.assert_called_once()
    assert memory_client.post_calls
    holiday_commit.assert_called_once()
    assert manager.state.phase is ProactivePhase.IDLE


@pytest.mark.asyncio
async def test_both_avoidance_layers_reach_the_instruction_in_the_right_block(
    monkeypatch,
):
    """The 3-day read must be split, not just widened.

    Guards the call site: a single-window regression here still passes every
    policy-level unit test, because the splitter itself would stay correct.
    """

    now = 10 * 24 * 60 * 60.0
    session = _GreetingSession(delivered=True)
    manager = _make_manager(session)
    memory_client = _GreetingMemoryClient()
    history = _HistoryDouble(
        records=[
            StartupGreetingRecord(
                ts=now - 3600.0,
                text="STRICT-LAYER-OPENING",
                variant_key="simple_presence",
            ),
            StartupGreetingRecord(
                ts=now - 40 * 3600.0,
                text="EARLIER-LAYER-OPENING",
                variant_key="light_question",
            ),
        ]
    )
    anti_repeat = _AntiRepeatDouble()
    _patch_dependencies(
        monkeypatch, memory_client, history, anti_repeat, MagicMock()
    )
    patch_module_clock(monkeypatch, greeting_module, time=lambda: now)

    await core_module.LLMSessionManager.trigger_greeting(manager)
    await asyncio.gather(*manager._post_commit_tasks)

    assert len(session.instructions) == 1
    instruction = session.instructions[0]
    strict_block = instruction.split("<recent-startup-openings>")[1].split(
        "</recent-startup-openings>"
    )[0]
    earlier_block = instruction.split("<earlier-startup-openings>")[1].split(
        "</earlier-startup-openings>"
    )[0]

    assert "STRICT-LAYER-OPENING" in strict_block
    assert "EARLIER-LAYER-OPENING" not in strict_block
    assert "EARLIER-LAYER-OPENING" in earlier_block
    assert "STRICT-LAYER-OPENING" not in earlier_block


@pytest.mark.asyncio
async def test_variant_rotation_reads_only_the_strict_layer_at_the_call_site(
    monkeypatch,
):
    """Angles rotate against 1 day even though the read spans 3.

    Recorded angles are laid out so the two windows disagree: the strict layer
    still has unused angles, while the full recall set is exhausted and would
    fall through to round-robin. Asserting the rendered guidance pins which
    window the call site actually passes down.
    """

    now = 10 * 24 * 60 * 60.0
    session = _GreetingSession(delivered=True)
    manager = _make_manager(session)
    memory_client = _GreetingMemoryClient()
    history = _HistoryDouble(
        records=[
            StartupGreetingRecord(
                ts=now - 3600.0, text="Inside one day.", variant_key="light_question"
            ),
            StartupGreetingRecord(
                ts=now - 2 * 24 * 3600.0,
                text="Two days back.",
                variant_key="recent_continuity",
            ),
            StartupGreetingRecord(
                ts=now - 2 * 24 * 3600.0 - 1,
                text="Also two days back.",
                variant_key="personal_share",
            ),
            StartupGreetingRecord(
                ts=now - 2 * 24 * 3600.0 - 2,
                text="Oldest.",
                variant_key="simple_presence",
            ),
        ]
    )
    anti_repeat = _AntiRepeatDouble()
    _patch_dependencies(
        monkeypatch, memory_client, history, anti_repeat, MagicMock()
    )
    patch_module_clock(monkeypatch, greeting_module, time=lambda: now)

    await core_module.LLMSessionManager.trigger_greeting(manager)
    await asyncio.gather(*manager._post_commit_tasks)

    assert len(session.instructions) == 1
    instruction = session.instructions[0]
    # Strict layer holds only light_question, so the first unused angle wins.
    assert _STARTUP_GREETING_VARIANTS["recent_continuity"]["en"] in instruction
    # Rotating against all three days would exhaust every angle and land here.
    assert _STARTUP_GREETING_VARIANTS["simple_presence"]["en"] not in instruction


@pytest.mark.asyncio
async def test_memory_angle_reopens_after_a_day_with_a_different_topic(monkeypatch):
    """The memory angle is on a 1-day cooldown; its topics are on a 3-day one."""

    now = 10 * 24 * 60 * 60.0
    session = _GreetingSession(delivered=True)
    manager = _make_manager(session)
    memory_client = _GreetingMemoryClient(
        topics=[
            {"id": "ref_old", "text": "The topic already used two days ago."},
            {"id": "ref_fresh", "text": "A topic never surfaced before."},
        ]
    )
    history = _HistoryDouble(
        records=[
            StartupGreetingRecord(
                ts=now - 2 * 24 * 3600.0,
                text="Memory opening from two days ago.",
                variant_key="memory_followup",
                topic_key="ref_old",
            ),
        ]
    )
    anti_repeat = _AntiRepeatDouble()
    _patch_dependencies(
        monkeypatch, memory_client, history, anti_repeat, MagicMock()
    )
    patch_module_clock(monkeypatch, greeting_module, time=lambda: now)

    await core_module.LLMSessionManager.trigger_greeting(manager)
    await asyncio.gather(*manager._post_commit_tasks)

    assert len(session.instructions) == 1
    instruction = session.instructions[0]
    assert "<memory-cue>A topic never surfaced before.</memory-cue>" in instruction
    assert "The topic already used two days ago." not in instruction


@pytest.mark.asyncio
async def test_topic_cooldown_spans_the_full_three_day_recall_window(monkeypatch):
    """A topic used two days ago must not be offered again as a memory cue."""

    now = 10 * 24 * 60 * 60.0
    session = _GreetingSession(delivered=True)
    manager = _make_manager(session)
    memory_client = _GreetingMemoryClient(
        topics=[{"id": "ref_book", "text": "Continue discussing the book ending."}]
    )
    history = _HistoryDouble(
        records=[
            StartupGreetingRecord(
                ts=now - 2 * 24 * 3600.0,
                text="An opening from two days ago.",
                variant_key="memory_followup",
                topic_key="ref_book",
            ),
        ]
    )
    anti_repeat = _AntiRepeatDouble()
    _patch_dependencies(
        monkeypatch, memory_client, history, anti_repeat, MagicMock()
    )
    patch_module_clock(monkeypatch, greeting_module, time=lambda: now)

    await core_module.LLMSessionManager.trigger_greeting(manager)
    await asyncio.gather(*manager._post_commit_tasks)

    assert len(session.instructions) == 1
    assert "Continue discussing the book ending." not in session.instructions[0]
    assert "<memory-cue>" not in session.instructions[0]


@pytest.mark.asyncio
async def test_repeated_start_inside_thirty_minutes_is_silent(monkeypatch):
    session = _GreetingSession(delivered=True)
    manager = _make_manager(session)
    memory_client = _GreetingMemoryClient()
    history = _HistoryDouble(
        records=[
            StartupGreetingRecord(
                ts=900.0,
                text="A greeting already shown.",
                variant_key="simple_presence",
            )
        ]
    )
    anti_repeat = _AntiRepeatDouble()
    holiday_commit = MagicMock()
    _patch_dependencies(
        monkeypatch, memory_client, history, anti_repeat, holiday_commit
    )
    patch_module_clock(monkeypatch, greeting_module, time=lambda: 1000.0)

    await core_module.LLMSessionManager.trigger_greeting(manager)

    assert session.instructions == []
    assert len(memory_client.get_calls) == 1
    assert "/last_conversation_gap/" in memory_client.get_calls[0][0]
    history.stage_committed.assert_not_called()
    anti_repeat.stage_output.assert_not_called()
    assert manager.state.phase is ProactivePhase.IDLE


@pytest.mark.asyncio
async def test_user_sid_preemption_does_not_commit_hidden_local_text(monkeypatch):
    manager = None

    class _PreemptingSession(_GreetingSession):
        async def prompt_ephemeral(
            self,
            instruction,
            *,
            images=None,
            on_committed=None,
            on_committed_text=None,
        ):
            self.instructions.append(instruction)
            manager.current_speech_id = "user-sid"
            await manager.state.fire(SessionEvent.USER_INPUT, sid="user-sid")
            # The model built this text locally, but the sid guard means transport
            # deltas belong to the superseded proactive turn and are not visible.
            on_committed_text("This text was never published to the user.")
            return True

    session = _PreemptingSession()
    manager = _make_manager(session)
    memory_client = _GreetingMemoryClient(
        topics=[{"id": "ref_book", "text": "Continue the book."}]
    )
    history = _HistoryDouble()
    anti_repeat = _AntiRepeatDouble()
    holiday_commit = MagicMock()
    _patch_dependencies(
        monkeypatch, memory_client, history, anti_repeat, holiday_commit
    )

    await core_module.LLMSessionManager.trigger_greeting(manager)

    history.stage_committed.assert_not_called()
    anti_repeat.stage_output.assert_not_called()
    history.release_reservation.assert_called_once_with("Test", "reservation-token")
    assert memory_client.post_calls == []
    holiday_commit.assert_not_called()
    assert manager.state.phase is ProactivePhase.IDLE


@pytest.mark.asyncio
async def test_user_preemption_commits_only_already_published_greeting_prefix(
    monkeypatch,
):
    manager = None

    class _PartiallyPublishedSession(_GreetingSession):
        async def prompt_ephemeral(
            self,
            instruction,
            *,
            images=None,
            on_committed=None,
            on_committed_text=None,
        ):
            self.instructions.append(instruction)
            published_chunks = core_module._proactive_published_text_chunks.get()
            assert published_chunks is not None
            published_chunks.append("This prefix reached the user.")
            manager.current_speech_id = "user-sid"
            await manager.state.fire(SessionEvent.USER_INPUT, sid="user-sid")
            on_committed_text(
                "This prefix reached the user. This suffix stayed local."
            )
            return True

    session = _PartiallyPublishedSession()
    manager = _make_manager(session)
    memory_client = _GreetingMemoryClient(
        topics=[{"id": "ref_book", "text": "Continue the book."}]
    )
    history = _HistoryDouble()
    anti_repeat = _AntiRepeatDouble()
    holiday_commit = MagicMock()
    _patch_dependencies(
        monkeypatch, memory_client, history, anti_repeat, holiday_commit
    )

    await core_module.LLMSessionManager.trigger_greeting(manager)
    await asyncio.gather(*manager._post_commit_tasks)

    history.stage_committed.assert_called_once_with(
        "Test",
        "This prefix reached the user.",
        variant_key="memory_followup",
        topic_key="ref_book",
        reservation_token="reservation-token",
    )
    anti_repeat.stage_output.assert_called_once_with(
        "Test", "This prefix reached the user.", is_proactive=True
    )
    assert memory_client.post_calls
    holiday_commit.assert_called_once_with("Test", "holiday-token")
    history.release_reservation.assert_called_once_with(
        "Test", "reservation-token"
    )
    assert manager.state.phase is ProactivePhase.IDLE


@pytest.mark.asyncio
async def test_concurrent_managers_share_one_atomic_startup_reservation(
    monkeypatch, tmp_path
):
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    class _SlowGreetingSession(_GreetingSession):
        async def prompt_ephemeral(
            self,
            instruction,
            *,
            images=None,
            on_committed=None,
            on_committed_text=None,
        ):
            self.instructions.append(instruction)
            first_started.set()
            await release_first.wait()
            on_committed_text("Only one concurrent greeting is visible.")
            return True

    first_session = _SlowGreetingSession()
    second_session = _GreetingSession()
    first_manager = _make_manager(first_session)
    second_manager = _make_manager(second_session)
    memory_client = _GreetingMemoryClient()
    config_manager = MagicMock(memory_dir=str(tmp_path))
    history = StartupGreetingHistory(config_manager)
    anti_repeat = _AntiRepeatDouble()
    holiday_commit = MagicMock()
    _patch_dependencies(
        monkeypatch, memory_client, history, anti_repeat, holiday_commit
    )

    first_task = asyncio.create_task(
        core_module.LLMSessionManager.trigger_greeting(first_manager)
    )
    await asyncio.wait_for(first_started.wait(), timeout=2.0)
    await asyncio.wait_for(
        core_module.LLMSessionManager.trigger_greeting(second_manager),
        timeout=2.0,
    )
    release_first.set()
    await first_task
    await asyncio.gather(*first_manager._post_commit_tasks)
    await asyncio.gather(*list(history._detached_flushes))

    assert len(first_session.instructions) == 1
    assert second_session.instructions == []
    assert len(history.recent("Test")) == 1
    assert anti_repeat.stage_output.call_count == 1
