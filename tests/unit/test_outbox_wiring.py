# -*- coding: utf-8 -*-
"""
P1.c integration tests: outbox wiring inside memory_server.

Verifies:
  - _spawn_outbox_post_turn_signals appends a pending op, runs handler, marks done
  - _replay_pending_outbox picks up unfinished ops and re-runs handler
  - Handler not executing (e.g. no registered handler) → op remains pending
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from utils.llm_client import AIMessage, HumanMessage


@pytest.fixture(autouse=True)
def _isolate_prompt_locale_sidecar(monkeypatch, tmp_path):
    from app.memory_server import locale_state

    locale_path = tmp_path / "prompt_locale.json"
    monkeypatch.setattr(locale_state, "_locale_path", lambda _name: str(locale_path))
    locale_state._locale_cache.clear()
    locale_state._character_locale_admission_orders.clear()
    locale_state._character_locale_capture_offsets.clear()


def _install_fresh_memory_state(tmpdir: str):
    """Replace memory_server's outbox / config_manager with fresh instances backed by tmpdir."""
    from memory.outbox import Outbox
    from app import memory_server

    mock_cm = MagicMock()
    mock_cm.memory_dir = tmpdir
    _initial_characters = {"猫娘": {"小天": {}}, "当前猫娘": "小天"}
    mock_cm.load_characters = MagicMock(return_value=_initial_characters)
    # _replay_pending_outbox awaits the async variant; without an AsyncMock
    # the bare MagicMock attribute returns a MagicMock that can't be awaited.
    mock_cm.aload_characters = AsyncMock(return_value=_initial_characters)
    with patch("memory.outbox.get_config_manager", return_value=mock_cm):
        ob = Outbox()
    ob._config_manager = mock_cm

    memory_server.runtime.outbox = ob
    memory_server.runtime._config_manager = mock_cm
    # Reset event-loop-bound semaphore so each test gets a fresh one
    memory_server.outbox_infra._replay_semaphore = None
    return ob, mock_cm


@pytest.mark.asyncio
async def test_spawn_outbox_happy_path_marks_done(tmp_path):
    """Handler completed successfully → outbox pending_ops is empty."""
    ob, _ = _install_fresh_memory_state(str(tmp_path))
    from app import memory_server
    from memory.outbox import OP_POST_TURN_SIGNALS

    calls: list[tuple[str, dict]] = []

    async def _fake_handler(name: str, payload: dict):
        calls.append((name, payload))

    with patch.dict(
        memory_server._OUTBOX_HANDLERS,
        {OP_POST_TURN_SIGNALS: _fake_handler},
        clear=False,
    ):
        msgs = [HumanMessage(content="喵"), AIMessage(content="mrrp")]
        task = await memory_server._spawn_outbox_post_turn_signals(
            "小天", msgs, language="zh-TW",
        )
        await task

    assert len(calls) == 1
    name, payload = calls[0]
    assert name == "小天"
    # payload serialized via messages_to_dict → round-trippable
    assert isinstance(payload.get("messages"), list)
    assert len(payload["messages"]) == 2
    assert payload["language"] == "zh-TW"
    assert isinstance(payload["locale_order"], int)

    # Outbox should show no pending ops after success
    pending = await ob.apending_ops("小天")
    assert pending == []


@pytest.mark.asyncio
async def test_spawn_outbox_keeps_render_fallback_out_of_durable_language(tmp_path):
    """Only request render evidence may accompany an undeclared language."""
    # _activate_request_language falls back to
    # get_global_language_full() when the request omits language. That fallback is fine for this request, but once persisted
    # into outbox.ndjson, restart replay will keep reusing this "guess" — even if detection is later fixed,
    # it will never self-heal. Omitting this key makes replay use the character's latest persisted explicit session language.
    ob, _ = _install_fresh_memory_state(str(tmp_path))
    from app import memory_server
    from memory.outbox import OP_POST_TURN_SIGNALS

    calls: list[tuple[str, dict]] = []

    async def _fake_handler(name: str, payload: dict):
        calls.append((name, payload))

    with patch.dict(
        memory_server._OUTBOX_HANDLERS,
        {OP_POST_TURN_SIGNALS: _fake_handler},
        clear=False,
    ), patch.object(
        memory_server.locale_state,
        "allocate_character_prompt_locale_order",
    ) as allocate_locale, patch.object(
        memory_server.locale_state,
        "reserve_character_prompt_locale_order",
    ) as reserve_locale:
        task = await memory_server._spawn_outbox_post_turn_signals(
            "小天", [HumanMessage(content="喵")], language=None,
        )
        await task

        task = await memory_server._spawn_outbox_post_turn_signals(
            "小天",
            [HumanMessage(content="喵")],
            language=None,
            render_language="ja",
        )
        await task

    assert len(calls) == 2
    plain_payload = calls[0][1]
    render_payload = calls[1][1]
    for payload in (plain_payload, render_payload):
        assert "language" not in payload
        assert "locale_order" not in payload
        assert "locale_order_deferred" not in payload
        assert "locale_admission_order" not in payload
    assert "render_language" not in plain_payload
    assert render_payload["render_language"] == "ja"
    allocate_locale.assert_not_called()
    reserve_locale.assert_not_called()


@pytest.mark.asyncio
async def test_spawn_outbox_preserves_route_admission_order(tmp_path):
    ob, _ = _install_fresh_memory_state(str(tmp_path))
    from app import memory_server
    from memory.outbox import OP_POST_TURN_SIGNALS

    calls: list[tuple[str, dict]] = []

    async def _fake_handler(name: str, payload: dict):
        calls.append((name, payload))

    with patch.dict(
        memory_server._OUTBOX_HANDLERS,
        {OP_POST_TURN_SIGNALS: _fake_handler},
        clear=False,
    ), patch.object(
        memory_server.locale_state,
        "allocate_character_prompt_locale_order",
        side_effect=AssertionError("route admission order must be reused"),
    ):
        task = await memory_server._spawn_outbox_post_turn_signals(
            "小天",
            [HumanMessage(content="喵")],
            language="zh-TW",
            locale_admission_order=314,
        )
        await task

    assert calls[0][1]["locale_order"] == 314
    assert await ob.apending_ops("小天") == []


@pytest.mark.asyncio
async def test_spawn_outbox_survives_post_commit_locale_reservation_fence(tmp_path):
    """A late locale fence must not make the durable conversation retry."""
    ob, _ = _install_fresh_memory_state(str(tmp_path))
    from app import memory_server
    from memory.outbox import OP_POST_TURN_SIGNALS
    from utils.cloudsave_runtime import MaintenanceModeError

    calls: list[tuple[str, dict]] = []

    async def _fake_handler(name: str, payload: dict):
        calls.append((name, payload))

    def blocked_reservation(_name, *, order=None):
        assert isinstance(order, int)
        raise MaintenanceModeError(
            "maintenance_readonly",
            operation="save",
            target="prompt_locale.json",
        )

    with patch.dict(
        memory_server._OUTBOX_HANDLERS,
        {OP_POST_TURN_SIGNALS: _fake_handler},
        clear=False,
    ), patch.object(
        memory_server.locale_state,
        "reserve_character_prompt_locale_order",
        side_effect=blocked_reservation,
    ):
        task = await memory_server._spawn_outbox_post_turn_signals(
            "小天",
            [HumanMessage(content="喵")],
            language="zh-TW",
        )
        await task

    assert len(calls) == 1
    _name, payload = calls[0]
    assert payload["language"] == "zh-TW"
    assert "locale_order" not in payload
    assert payload["locale_order_deferred"] is True
    assert isinstance(payload["locale_admission_order"], int)
    assert await ob.apending_ops("小天") == []


@pytest.mark.asyncio
async def test_spawn_outbox_defers_unpersisted_locale_reservation(tmp_path):
    ob, _ = _install_fresh_memory_state(str(tmp_path))
    from app import memory_server
    from app.memory_server.locale_state import PromptLocalePersistenceError
    from memory.outbox import OP_POST_TURN_SIGNALS

    calls: list[tuple[str, dict]] = []

    async def _fake_handler(name: str, payload: dict):
        calls.append((name, payload))

    with patch.dict(
        memory_server._OUTBOX_HANDLERS,
        {OP_POST_TURN_SIGNALS: _fake_handler},
        clear=False,
    ), patch.object(
        memory_server.locale_state,
        "reserve_character_prompt_locale_order",
        side_effect=PromptLocalePersistenceError("not committed"),
    ):
        task = await memory_server._spawn_outbox_post_turn_signals(
            "小天",
            [HumanMessage(content="喵")],
            language="zh-TW",
        )
        await task

    assert len(calls) == 1
    _name, payload = calls[0]
    assert payload["language"] == "zh-TW"
    assert "locale_order" not in payload
    assert payload["locale_order_deferred"] is True
    assert isinstance(payload["locale_admission_order"], int)
    assert await ob.apending_ops("小天") == []


@pytest.mark.asyncio
async def test_deferred_locale_reservation_retries_after_fence(monkeypatch):
    """Deferred locale allocation must resume after the cloud fence clears."""
    from app import memory_server
    from utils.cloudsave_runtime import MaintenanceModeError

    attempts = 0

    def reserve(_name, *, order=None):
        nonlocal attempts
        assert order == 41
        attempts += 1
        if attempts == 1:
            raise MaintenanceModeError(
                "maintenance_readonly",
                operation="save",
                target="prompt_locale.json",
            )
        return order

    sleep = AsyncMock()
    monkeypatch.setattr(
        memory_server.locale_state,
        "reserve_character_prompt_locale_order",
        reserve,
    )
    monkeypatch.setattr(memory_server.post_turn.asyncio, "sleep", sleep)

    locale_order = await memory_server.post_turn._wait_for_character_prompt_locale_order(
        "小天",
        admission_order=41,
    )

    assert locale_order == 41
    assert attempts == 2
    sleep.assert_awaited_once_with(0.25)


@pytest.mark.asyncio
async def test_deferred_locale_reservation_retries_invalidated_write(monkeypatch):
    from app import memory_server
    from app.memory_server.locale_state import PromptLocaleInvalidatedError

    attempts = 0

    def reserve(_name, *, order=None):
        nonlocal attempts
        assert order == 41
        attempts += 1
        if attempts == 1:
            raise PromptLocaleInvalidatedError("invalidated")
        return order

    sleep = AsyncMock()
    monkeypatch.setattr(
        memory_server.locale_state,
        "reserve_character_prompt_locale_order",
        reserve,
    )
    monkeypatch.setattr(memory_server.post_turn.asyncio, "sleep", sleep)

    locale_order = await memory_server.post_turn._wait_for_character_prompt_locale_order(
        "小天",
        admission_order=41,
    )

    assert locale_order == 41
    assert attempts == 2
    sleep.assert_awaited_once_with(0.25)


@pytest.mark.asyncio
async def test_deferred_locale_reservation_propagates_permanent_failure(monkeypatch):
    from app import memory_server
    from app.memory_server.locale_state import PromptLocalePersistenceError

    def reserve(_name, *, order=None):
        assert order == 41
        raise PromptLocalePersistenceError("disk full")

    sleep = AsyncMock()
    monkeypatch.setattr(
        memory_server.locale_state,
        "reserve_character_prompt_locale_order",
        reserve,
    )
    monkeypatch.setattr(memory_server.post_turn.asyncio, "sleep", sleep)

    with pytest.raises(PromptLocalePersistenceError, match="disk full"):
        await memory_server.post_turn._wait_for_character_prompt_locale_order(
            "小天",
            admission_order=41,
        )

    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_turn_locale_record_retries_fence_before_counter(monkeypatch):
    from app import memory_server
    from utils.cloudsave_runtime import MaintenanceModeError

    attempts = 0

    def persist(_name, *, language, locale_order):
        nonlocal attempts
        attempts += 1
        assert language == "zh-TW"
        assert locale_order == 42
        if attempts == 1:
            raise MaintenanceModeError(
                "maintenance_readonly",
                operation="save",
                target="prompt_locale.json",
            )

    record_turn = MagicMock()
    sleep = AsyncMock()
    fact_store = MagicMock()
    fact_store.extract_facts = AsyncMock(return_value=None)
    reflection_engine = MagicMock()
    reflection_engine.aload_surfaced = AsyncMock(return_value=[])

    monkeypatch.setattr(
        memory_server.post_turn,
        "_extract_user_messages",
        lambda _messages: ["請記住我喜歡草莓"],
    )
    monkeypatch.setattr(memory_server.post_turn, "_extract_ai_response", lambda _messages: "")
    monkeypatch.setattr(
        memory_server.gates,
        "_ais_powerful_memory_enabled",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        memory_server.signal_extraction,
        "_signal_check_persist_locale",
        persist,
    )
    monkeypatch.setattr(
        memory_server.signal_extraction,
        "_signal_check_record_turn",
        record_turn,
    )
    monkeypatch.setattr(memory_server.post_turn.asyncio, "sleep", sleep)
    monkeypatch.setattr(memory_server.runtime, "fact_store", fact_store)
    monkeypatch.setattr(memory_server.runtime, "reflection_engine", reflection_engine)

    await memory_server._run_post_turn_signals(
        [HumanMessage(content="請記住我喜歡草莓")],
        "小天",
        language="zh-TW",
        locale_order=42,
    )

    assert attempts == 2
    sleep.assert_awaited_once_with(0.25)
    record_turn.assert_called_once_with("小天")


@pytest.mark.asyncio
async def test_assistant_only_post_turn_persists_locale_without_signal_counter(
    monkeypatch,
):
    from app import memory_server

    persist = AsyncMock()
    record_turn = MagicMock()
    persona_manager = MagicMock()
    persona_manager.arecord_mentions = AsyncMock(return_value=None)
    reflection_engine = MagicMock()
    reflection_engine.arecord_mentions = AsyncMock(return_value=None)
    reflection_engine.aload_surfaced = AsyncMock(return_value=[])

    monkeypatch.setattr(
        memory_server.post_turn,
        "_wait_for_signal_locale_persistence",
        persist,
    )
    monkeypatch.setattr(
        memory_server.signal_extraction,
        "_signal_check_record_turn",
        record_turn,
    )
    monkeypatch.setattr(
        memory_server.gates,
        "_ais_powerful_memory_enabled",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(memory_server.runtime, "persona_manager", persona_manager)
    monkeypatch.setattr(memory_server.runtime, "reflection_engine", reflection_engine)

    await memory_server._run_post_turn_signals(
        [AIMessage(content="先来打个招呼")],
        "小天",
        language="zh-TW",
        locale_order=42,
    )

    persist.assert_awaited_once_with(
        "小天",
        language="zh-TW",
        locale_order=42,
    )
    record_turn.assert_not_called()


@pytest.mark.asyncio
async def test_post_turn_locale_record_retries_invalidated_write(monkeypatch):
    from app import memory_server
    from app.memory_server.locale_state import PromptLocaleInvalidatedError

    attempts = 0

    def persist(_name, *, language, locale_order):
        nonlocal attempts
        assert language == "zh-TW"
        assert locale_order == 42
        attempts += 1
        if attempts == 1:
            raise PromptLocaleInvalidatedError("invalidated")

    sleep = AsyncMock()
    monkeypatch.setattr(
        memory_server.signal_extraction,
        "_signal_check_persist_locale",
        persist,
    )
    monkeypatch.setattr(memory_server.post_turn.asyncio, "sleep", sleep)

    await memory_server.post_turn._wait_for_signal_locale_persistence(
        "小天",
        language="zh-TW",
        locale_order=42,
    )

    assert attempts == 2
    sleep.assert_awaited_once_with(0.25)


@pytest.mark.asyncio
async def test_post_turn_locale_record_propagates_permanent_failure(monkeypatch):
    from app import memory_server
    from app.memory_server.locale_state import PromptLocalePersistenceError

    def persist(_name, *, language, locale_order):
        assert language == "zh-TW"
        assert locale_order == 42
        raise PromptLocalePersistenceError("disk full")

    sleep = AsyncMock()
    monkeypatch.setattr(
        memory_server.signal_extraction,
        "_signal_check_persist_locale",
        persist,
    )
    monkeypatch.setattr(memory_server.post_turn.asyncio, "sleep", sleep)

    with pytest.raises(PromptLocalePersistenceError, match="disk full"):
        await memory_server.post_turn._wait_for_signal_locale_persistence(
            "小天",
            language="zh-TW",
            locale_order=42,
        )

    sleep.assert_not_awaited()


def test_deferred_locale_admission_order_cannot_overwrite_newer_turn(tmp_path):
    _install_fresh_memory_state(str(tmp_path))
    from app.memory_server import locale_state

    older_order = locale_state.allocate_character_prompt_locale_order("小天")
    newer_order = locale_state.reserve_character_prompt_locale_order("小天")
    assert older_order < newer_order

    assert locale_state.record_character_prompt_locale(
        "小天",
        "zh-TW",
        order=newer_order,
    ) == "zh-TW"
    assert locale_state.reserve_character_prompt_locale_order(
        "小天",
        order=older_order,
    ) == older_order
    assert locale_state.record_character_prompt_locale(
        "小天",
        "zh-CN",
        order=older_order,
    ) == "zh-TW"
    assert locale_state.get_character_prompt_locale("小天") == "zh-TW"


@pytest.mark.asyncio
async def test_post_turn_context_prefers_current_durable_then_recorded_render(
    tmp_path,
    monkeypatch,
):
    _install_fresh_memory_state(str(tmp_path))
    from app import memory_server
    from app.memory_server import locale_state
    from utils.language_utils import get_global_language_full, language_context

    older_order = locale_state.allocate_character_prompt_locale_order("小天")
    newer_order = locale_state.reserve_character_prompt_locale_order("小天")
    locale_state.record_character_prompt_locale(
        "小天",
        "zh-TW",
        order=newer_order,
    )

    observed = []

    class StopAfterLocale(RuntimeError):
        pass

    async def observe_locale():
        observed.append(get_global_language_full())
        raise StopAfterLocale

    monkeypatch.setattr(
        memory_server.gates,
        "_ais_powerful_memory_enabled",
        observe_locale,
    )

    with language_context("en"), pytest.raises(StopAfterLocale):
        await memory_server._run_post_turn_signals(
            [HumanMessage(content="請記住")],
            "小天",
            language="zh-CN",
            locale_order=older_order,
        )

    assert observed == ["zh-TW"]
    assert locale_state.get_character_prompt_locale("小天") == "zh-TW"

    observed.clear()
    with language_context("en"), pytest.raises(StopAfterLocale):
        await memory_server._run_post_turn_signals(
            [HumanMessage(content="請記住")],
            "小天",
            render_language="ja",
        )
    assert observed == ["zh-TW"]

    monkeypatch.setattr(
        memory_server.locale_state,
        "get_character_prompt_locale",
        lambda _name: None,
    )
    observed.clear()
    with language_context("en"), pytest.raises(StopAfterLocale):
        await memory_server._run_post_turn_signals(
            [HumanMessage(content="請記住")],
            "小天",
            render_language="ja",
        )
    assert observed == ["ja"]


@pytest.mark.asyncio
async def test_post_turn_outbox_replay_resolves_deferred_locale_order():
    """A durable deferred marker must reserve an order before signal writes."""
    from app import memory_server
    from utils.llm_client import messages_to_dict

    runner = AsyncMock(return_value=None)
    payload = {
        "messages": messages_to_dict([HumanMessage(content="請記住我喜歡草莓")]),
        "language": "zh-TW",
        "locale_order_deferred": True,
        "locale_admission_order": 41,
    }

    with patch(
        "app.memory_server.post_turn._run_post_turn_signals_after_locale_reservation",
        runner,
    ):
        await memory_server._outbox_post_turn_signals_handler("小天", payload)

    runner.assert_awaited_once()
    assert runner.await_args.args[1] == "小天"
    assert runner.await_args.kwargs["language"] == "zh-TW"
    assert runner.await_args.kwargs["admission_order"] == 41


@pytest.mark.asyncio
async def test_empty_post_turn_outbox_replay_persists_explicit_locale():
    from app import memory_server

    runner = AsyncMock(return_value=None)
    payload = {
        "messages": [],
        "language": "zh-TW",
        "locale_order": 42,
    }

    with patch("app.memory_server.post_turn._run_post_turn_signals", runner):
        await memory_server._outbox_post_turn_signals_handler("小天", payload)

    runner.assert_awaited_once_with(
        [],
        "小天",
        language="zh-TW",
        locale_order=42,
        locale_only=True,
    )


@pytest.mark.asyncio
async def test_legacy_deferred_locale_replay_sorts_before_newer_turn(monkeypatch):
    """A legacy deferred row must not receive a fresh order during replay."""
    from app import memory_server

    reserve = MagicMock(return_value=0)
    monkeypatch.setattr(
        memory_server.locale_state,
        "reserve_character_prompt_locale_order",
        reserve,
    )

    assert await memory_server.post_turn._wait_for_character_prompt_locale_order(
        "小天",
        admission_order=None,
    ) == 0
    reserve.assert_called_once_with("小天", order=0)


@pytest.mark.asyncio
async def test_replay_without_durable_language_restores_optional_render_context():
    """Legacy rows defer resolution; new rows also carry their render fallback."""
    # This is the only path for upgraded users: queue entries created before #1542 do not have this key.
    from app import memory_server
    from utils.llm_client import messages_to_dict

    runner = AsyncMock(return_value=None)
    messages = messages_to_dict([HumanMessage(content="旧条目")])

    with patch("app.memory_server.post_turn._run_post_turn_signals", runner):
        await memory_server._outbox_post_turn_signals_handler(
            "小天",
            {"messages": messages},
        )
        await memory_server._outbox_post_turn_signals_handler(
            "小天",
            {"messages": messages, "render_language": "ja"},
        )

    legacy_call, render_call = runner.await_args_list
    assert legacy_call.kwargs == {"language": None, "locale_order": None}
    assert render_call.kwargs == {
        "language": None,
        "locale_order": None,
        "render_language": "ja",
    }


@pytest.mark.asyncio
async def test_post_turn_outbox_replay_restores_recorded_language():
    """Replay the language recorded at enqueue time instead of the server locale."""
    from app import memory_server
    from utils.llm_client import messages_to_dict

    runner = AsyncMock(return_value=None)
    payload = {
        "messages": messages_to_dict([HumanMessage(content="请记住我喜欢草莓")]),
        "language": "zh-CN",
        "locale_order": 42,
    }

    with patch("app.memory_server.post_turn._run_post_turn_signals", runner):
        await memory_server._outbox_post_turn_signals_handler("小天", payload)

    runner.assert_awaited_once()
    assert runner.await_args.args[1] == "小天"
    assert runner.await_args.kwargs["language"] == "zh-CN"
    assert runner.await_args.kwargs["locale_order"] == 42


@pytest.mark.asyncio
async def test_concurrent_post_turn_tasks_keep_their_recorded_language():
    """Per-conversation locales must not overwrite one another while awaiting."""
    from app import memory_server
    from utils.language_utils import get_global_language_full

    baseline = get_global_language_full()
    both_entered = asyncio.Event()
    observed: dict[str, list[str]] = {}

    async def extract_facts(_messages, lanlan_name):
        observed[lanlan_name] = [get_global_language_full()]
        if len(observed) == 2:
            both_entered.set()
        await both_entered.wait()
        await asyncio.sleep(0)
        observed[lanlan_name].append(get_global_language_full())

    fact_store = MagicMock()
    fact_store.extract_facts = extract_facts
    reflection_engine = MagicMock()
    reflection_engine.aload_surfaced = AsyncMock(return_value=[])

    with (
        patch.object(memory_server.post_turn, "_extract_user_messages", return_value=["hi"]),
        patch.object(memory_server.post_turn, "_extract_ai_response", return_value=""),
        patch.object(
            memory_server.gates,
            "_ais_powerful_memory_enabled",
            AsyncMock(return_value=False),
        ),
        patch.object(
            memory_server.signal_extraction,
            "_signal_check_record_turn",
            MagicMock(),
        ),
        patch.object(
            memory_server.signal_extraction,
            "_signal_check_persist_locale",
            MagicMock(
                side_effect=lambda _name, *, language, locale_order: language,
            ),
        ),
        patch.object(memory_server.runtime, "fact_store", fact_store),
        patch.object(memory_server.runtime, "reflection_engine", reflection_engine),
    ):
        await asyncio.gather(
            memory_server._run_post_turn_signals([], "繁中", language="zh-TW"),
            memory_server._run_post_turn_signals([], "日本語", language="ja"),
        )

    assert observed == {
        "繁中": ["zh-TW", "zh-TW"],
        "日本語": ["ja", "ja"],
    }
    assert get_global_language_full() == baseline


@pytest.mark.asyncio
async def test_handler_failure_keeps_op_pending(tmp_path):
    """Handler raises → op stays pending (next startup replays it)."""
    ob, _ = _install_fresh_memory_state(str(tmp_path))
    from app import memory_server
    from memory.outbox import OP_POST_TURN_SIGNALS

    async def _bad_handler(name: str, payload: dict):
        raise RuntimeError("simulated LLM crash mid-call")

    with patch.dict(
        memory_server._OUTBOX_HANDLERS,
        {OP_POST_TURN_SIGNALS: _bad_handler},
        clear=False,
    ):
        task = await memory_server._spawn_outbox_post_turn_signals(
            "小天", [HumanMessage(content="hi")]
        )
        await task

    pending = await ob.apending_ops("小天")
    assert len(pending) == 1
    assert pending[0]["type"] == OP_POST_TURN_SIGNALS


@pytest.mark.asyncio
async def test_replay_reinvokes_pending_handler(tmp_path):
    """Simulate a process restart: the previous run left a pending entry in the outbox, so startup replay should rerun the handler."""
    ob, _ = _install_fresh_memory_state(str(tmp_path))
    from app import memory_server
    from memory.outbox import OP_POST_TURN_SIGNALS
    from utils.llm_client import messages_to_dict

    # Scenario: previous run crashed after append_pending, before the handler finished
    payload = {"messages": messages_to_dict([HumanMessage(content="反驳：不喜欢咖啡")])}
    await ob.aappend_pending("小天", OP_POST_TURN_SIGNALS, payload)

    replay_calls: list[tuple[str, dict]] = []

    async def _replay_handler(name: str, payload: dict):
        replay_calls.append((name, payload))

    with patch.dict(
        memory_server._OUTBOX_HANDLERS,
        {OP_POST_TURN_SIGNALS: _replay_handler},
        clear=False,
    ):
        # _replay_pending_outbox returns the spawned task list directly; no need to scan
        # a _BACKGROUND_TASKS snapshot (the previous sleep(0) drain approach was fragile)
        spawned = await memory_server._replay_pending_outbox()
        if spawned:
            await asyncio.gather(*spawned, return_exceptions=True)

    assert len(replay_calls) == 1
    assert replay_calls[0][0] == "小天"
    # done should be written → pending_ops empty
    assert await ob.apending_ops("小天") == []


@pytest.mark.asyncio
async def test_replay_skips_unknown_op_type(tmp_path):
    """An unregistered op type must not crash replay; silently skip it without calling append_done,
    leaving it pending until an upgraded compatible handler can replay it."""
    ob, _ = _install_fresh_memory_state(str(tmp_path))
    from app import memory_server

    op_id = await ob.aappend_pending("小天", "future_op_type_v2", {"x": 1})

    # clear handlers to ensure this type isn't registered
    with patch.dict(memory_server._OUTBOX_HANDLERS, {}, clear=True):
        spawned = await memory_server._replay_pending_outbox()
        if spawned:
            await asyncio.gather(*spawned, return_exceptions=True)

    # Still pending (handler did not run and append_done was not called)
    pending = await ob.apending_ops("小天")
    assert len(pending) == 1
    assert pending[0]["op_id"] == op_id


@pytest.mark.asyncio
async def test_replay_respects_concurrency_semaphore(tmp_path):
    """Startup replay must not fan out without limit: _REPLAY_CONCURRENCY=4 should cap the number of handlers in flight."""
    ob, _ = _install_fresh_memory_state(str(tmp_path))
    from app import memory_server
    from memory.outbox import OP_POST_TURN_SIGNALS

    # Register 10 pending ops
    for i in range(10):
        await ob.aappend_pending("小天", OP_POST_TURN_SIGNALS, {"i": i})

    in_flight = 0
    max_in_flight = 0

    async def _slow_handler(name: str, payload: dict):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        # Give the scheduler a chance to start other tasks (yield several times)
        for _ in range(3):
            await asyncio.sleep(0)
        in_flight -= 1

    with patch.dict(
        memory_server._OUTBOX_HANDLERS,
        {OP_POST_TURN_SIGNALS: _slow_handler},
        clear=False,
    ):
        spawned = await memory_server._replay_pending_outbox()
        await asyncio.gather(*spawned, return_exceptions=True)

    assert max_in_flight <= memory_server._REPLAY_CONCURRENCY, \
        f"观察到 {max_in_flight} 个同时在飞，超出 {memory_server._REPLAY_CONCURRENCY}"
    assert await ob.apending_ops("小天") == []


@pytest.mark.asyncio
async def test_locale_replay_does_not_hold_llm_replay_semaphore(tmp_path):
    ob, _ = _install_fresh_memory_state(str(tmp_path))
    from app import memory_server
    from memory.outbox import OP_PERSIST_PROMPT_LOCALE, OP_POST_TURN_SIGNALS

    await ob.aappend_pending("小天", OP_PERSIST_PROMPT_LOCALE, {"i": 1})
    await ob.aappend_pending("小天", OP_POST_TURN_SIGNALS, {"i": 2})
    observed = []

    async def capture(_name, op, semaphore=None):
        observed.append((op["type"], semaphore))

    with patch.object(memory_server.outbox_infra, "_run_outbox_op", capture):
        spawned = await memory_server._replay_pending_outbox()
        await asyncio.gather(*spawned)

    by_type = dict(observed)
    assert by_type[OP_PERSIST_PROMPT_LOCALE] is None
    assert (
        by_type[OP_POST_TURN_SIGNALS]
        is memory_server.outbox_infra._replay_semaphore
    )


@pytest.mark.asyncio
async def test_replay_scans_disk_for_characters_not_in_config(tmp_path):
    """Codex PR#905 P2: if a character is removed from config while the outbox has pending entries, replay must still run them."""
    ob, mock_cm = _install_fresh_memory_state(str(tmp_path))
    from app import memory_server
    from memory.outbox import OP_POST_TURN_SIGNALS

    # Register one pending op (in "小天"'s outbox)
    await ob.aappend_pending("小天", OP_POST_TURN_SIGNALS, {"i": 1})

    # Simulate config changing so it no longer includes 小天 (the on-disk outbox remains)
    _empty_characters = {"猫娘": {}, "当前猫娘": None}
    mock_cm.load_characters = MagicMock(return_value=_empty_characters)
    mock_cm.aload_characters = AsyncMock(return_value=_empty_characters)

    calls: list[str] = []

    async def _handler(name: str, payload: dict):
        calls.append(name)

    with patch.dict(
        memory_server._OUTBOX_HANDLERS,
        {OP_POST_TURN_SIGNALS: _handler},
        clear=False,
    ):
        spawned = await memory_server._replay_pending_outbox()
        await asyncio.gather(*spawned, return_exceptions=True)

    # 小天 was still replayed even though it is absent from config
    assert calls == ["小天"]
    assert await ob.apending_ops("小天") == []


@pytest.mark.asyncio
async def test_end_to_end_kill_then_replay_persists_side_effect(tmp_path):
    """End to end: the handler writes a fact to a fake FactStore but \"crashes\" before append_done;
    a new process loads the outbox and replays it, eventually persisting the side effect."""
    ob, _ = _install_fresh_memory_state(str(tmp_path))
    from app import memory_server
    from memory.outbox import OP_POST_TURN_SIGNALS

    # First run: handler writes "fact" to side-effect state, but the process dies before append_done
    side_effect_log: list[str] = []

    async def _handler_run1(name: str, payload: dict):
        side_effect_log.append(f"run1:{name}:{payload.get('tag')}")
        raise RuntimeError("process killed")  # Simulate a crash before append_done

    with patch.dict(
        memory_server._OUTBOX_HANDLERS,
        {OP_POST_TURN_SIGNALS: _handler_run1},
        clear=False,
    ):
        await ob.aappend_pending("小天", OP_POST_TURN_SIGNALS, {"tag": "rebuttal_msg"})
        # Trigger replay directly to simulate "the crash happened during the first replay call"
        spawned = await memory_server._replay_pending_outbox()
        await asyncio.gather(*spawned, return_exceptions=True)

    assert side_effect_log == ["run1:小天:rebuttal_msg"]
    # Op remains pending: because the handler raises, _run_outbox_op does not call append_done
    pending = await ob.apending_ops("小天")
    assert len(pending) == 1

    # Second run: new process (fresh outbox instance + fixed handler)
    fresh_ob, _ = _install_fresh_memory_state(str(tmp_path))

    async def _handler_run2(name: str, payload: dict):
        side_effect_log.append(f"run2:{name}:{payload.get('tag')}")

    with patch.dict(
        memory_server._OUTBOX_HANDLERS,
        {OP_POST_TURN_SIGNALS: _handler_run2},
        clear=False,
    ):
        spawned = await memory_server._replay_pending_outbox()
        await asyncio.gather(*spawned, return_exceptions=True)

    # Side effect is replayed after restart
    assert "run2:小天:rebuttal_msg" in side_effect_log
    # done is written and the op is no longer pending
    assert await fresh_ob.apending_ops("小天") == []


@pytest.mark.asyncio
async def test_append_pending_failure_falls_back_to_in_memory(tmp_path):
    """Outbox write failure → fall back to the traditional in-memory task; the main flow must not crash."""
    ob, _ = _install_fresh_memory_state(str(tmp_path))
    from app import memory_server

    # Force aappend_pending to raise
    async def _boom(*a, **kw):
        raise OSError("disk full")

    ob.aappend_pending = _boom  # type: ignore[assignment]

    # Also patch _run_post_turn_signals to a no-op to avoid a real LLM call
    noop = AsyncMock(return_value=None)
    with patch("app.memory_server.post_turn._run_post_turn_signals", noop):
        task = await memory_server._spawn_outbox_post_turn_signals(
            "小天", [HumanMessage(content="hi")]
        )
        await task

    # Degraded path: the function was called
    noop.assert_called_once()
    # No pending record was created (the disk write had already failed)
    assert not os.path.exists(ob._outbox_path("小天"))


@pytest.mark.asyncio
async def test_deferred_append_failure_preserves_locale_admission_order(tmp_path):
    ob, _ = _install_fresh_memory_state(str(tmp_path))
    from app import memory_server
    from utils.cloudsave_runtime import MaintenanceModeError

    async def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    def blocked_reservation(_name, *, order=None):
        assert isinstance(order, int)
        raise MaintenanceModeError(
            "maintenance_readonly",
            operation="save",
            target="prompt_locale.json",
        )

    ob.aappend_pending = _boom  # type: ignore[assignment]
    fallback = AsyncMock(return_value=None)
    with patch.object(
        memory_server.locale_state,
        "reserve_character_prompt_locale_order",
        side_effect=blocked_reservation,
    ), patch(
        "app.memory_server.post_turn._run_post_turn_signals_after_locale_reservation",
        fallback,
    ):
        task = await memory_server._spawn_outbox_post_turn_signals(
            "小天",
            [HumanMessage(content="hi")],
            language="zh-TW",
        )
        await task

    fallback.assert_awaited_once()
    assert fallback.await_args.kwargs["language"] == "zh-TW"
    assert isinstance(fallback.await_args.kwargs["admission_order"], int)
    assert not os.path.exists(ob._outbox_path("小天"))


# ── startup ordering: reconcile must finish before the outbox is resumed ──


def _startup_function_ast():
    """AST of ensure_memory_server_runtime_initialized (the startup sequencer)."""
    import ast
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[2]
              / 'app' / 'memory_server' / 'runtime.py')
    tree = ast.parse(source.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if (isinstance(node, ast.AsyncFunctionDef)
                and node.name == 'ensure_memory_server_runtime_initialized'):
            return ast, node
    raise AssertionError('未找到 ensure_memory_server_runtime_initialized，断言失效')


def _calls_named(ast, node, name):
    return [c for c in ast.walk(node)
            if isinstance(c, ast.Call) and getattr(c.func, 'attr', None) == name]


def _reconcile_gathers(ast, node):
    """gather(...) calls whose arguments mention the per-character reconcile."""
    out = []
    for call in _calls_named(ast, node, 'gather'):
        names = {n.id for n in ast.walk(call) if isinstance(n, ast.Name)}
        if '_reconcile_one' in names:
            out.append(call)
    return out


def test_startup_reconciles_before_it_resumes_the_outbox():
    """Reconciliation must be complete before any outbox op is resumed.

    Both write the same view files, but their read points are asymmetric: a
    replay handler loads/mutates/saves inside the EventLog lock, while the
    reflection and persona live writers load their whole snapshot outside it
    and only then hand it to record_and_save. Any overlap leaves a window
    where a resumed op saves a pre-replay snapshot over a just-completed
    repair — and the sentinel is already past that event, so no later boot
    replays it again. Resumed ops are fire-and-forget background tasks, so
    the only thing separating them from the replay is this ordering.
    """
    ast, fn = _startup_function_ast()

    replay_calls = _calls_named(ast, fn, '_replay_pending_outbox')
    assert replay_calls, 'startup 里找不到 outbox 补跑调用，断言失效'
    gathers = _reconcile_gathers(ast, fn)
    assert gathers, 'startup 里找不到 per-character reconcile 的 gather，断言失效'

    # Reconciliation must be fully awaited: using create_task / spawn would reintroduce overlap
    awaited = {id(a.value) for a in ast.walk(fn) if isinstance(a, ast.Await)}
    assert all(id(g) in awaited for g in gathers), \
        'reconcile 的 gather 没有被 await，补跑会和重放重叠'

    replay_lines = {c.lineno for c in replay_calls}
    gather_lines = {g.lineno for g in gathers}

    def _covers(stmt, lines):
        return any(getattr(n, 'lineno', None) in lines for n in ast.walk(stmt))

    # Compare ordering at the statement-sequence level, not by line number; wrapping either side in try/if does not change the result
    checked = 0
    for node in ast.walk(fn):
        for field in ('body', 'orelse', 'finalbody'):
            block = getattr(node, field, None)
            if not isinstance(block, list) or not block:
                continue
            if not all(isinstance(s, ast.stmt) for s in block):
                continue
            idx_replay = [i for i, s in enumerate(block) if _covers(s, replay_lines)]
            idx_gather = [i for i, s in enumerate(block) if _covers(s, gather_lines)]
            if not idx_replay or not idx_gather:
                continue
            if set(idx_replay) & set(idx_gather):
                # If both are in the same statement, it is an outer container (async with / try),
                # which only says "both are inside" and cannot determine order; continue into the inner statements.
                continue
            checked += 1
            assert max(idx_gather) < min(idx_replay), (
                'outbox 补跑排在了 reconcile 前面：补跑的 op 是后台 task，'
                '会和重放并发写同一批 view 文件，重放结果可能被静默整覆盖'
            )
    assert checked, 'reconcile 与 outbox 补跑不在同一层语句序列里，前后顺序无从判定'
