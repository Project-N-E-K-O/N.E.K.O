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
    """Handler 成功完成 → outbox pending_ops 为空。"""
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
async def test_spawn_outbox_omits_language_when_request_declared_none(tmp_path):
    """A locale this process merely guessed must never be written into the payload."""
    # _activate_request_language 在请求没带 language 时会回落到
    # get_global_language_full()。那个回落值用于处理本次请求没问题，但一旦被持久化
    # 进 outbox.ndjson，重启 replay 就会一直复用这个「猜测」——即使探测本身后来修好
    # 也不会自愈。省掉这个键后 replay 会读取角色最新持久化的显式会话语言。
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
        task = await memory_server._spawn_outbox_post_turn_signals(
            "小天", [HumanMessage(content="喵")], language=None,
        )
        await task

    assert len(calls) == 1
    _name, payload = calls[0]
    assert "language" not in payload
    assert isinstance(payload["locale_order"], int)


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
async def test_replay_without_recorded_language_defers_locale_resolution():
    """Legacy entries leave locale selection to the post-turn operation wrapper."""
    # 这是升级用户唯一会走的路径：#1542 之前入队的条目都没有这个键。
    from app import memory_server
    from utils.llm_client import messages_to_dict

    runner = AsyncMock(return_value=None)
    payload = {"messages": messages_to_dict([HumanMessage(content="旧条目")])}

    with patch("app.memory_server.post_turn._run_post_turn_signals", runner):
        await memory_server._outbox_post_turn_signals_handler("小天", payload)

    runner.assert_awaited_once()
    assert runner.await_args.kwargs["language"] is None
    assert runner.await_args.kwargs["locale_order"] is None


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
            MagicMock(),
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
    """模拟进程重启场景：上一跑 outbox 里有 pending，启动 replay 应重跑 handler。"""
    ob, _ = _install_fresh_memory_state(str(tmp_path))
    from app import memory_server
    from memory.outbox import OP_POST_TURN_SIGNALS
    from utils.llm_client import messages_to_dict

    # 场景：上一跑在 append_pending 后崩溃，没跑完 handler
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
        # _replay_pending_outbox 直接返回 spawn 的 task 列表，无需扫
        # _BACKGROUND_TASKS 快照（之前的 sleep(0) drain 模式脆弱）
        spawned = await memory_server._replay_pending_outbox()
        if spawned:
            await asyncio.gather(*spawned, return_exceptions=True)

    assert len(replay_calls) == 1
    assert replay_calls[0][0] == "小天"
    # done 应被写入 → pending_ops 空
    assert await ob.apending_ops("小天") == []


@pytest.mark.asyncio
async def test_replay_skips_unknown_op_type(tmp_path):
    """未注册的 op type 不应让 replay 崩溃，该 op 静默跳过但 append_done
    不会被调用 → 保持 pending，等升级后兼容 handler 补跑。"""
    ob, _ = _install_fresh_memory_state(str(tmp_path))
    from app import memory_server

    op_id = await ob.aappend_pending("小天", "future_op_type_v2", {"x": 1})

    # clear handlers to ensure this type isn't registered
    with patch.dict(memory_server._OUTBOX_HANDLERS, {}, clear=True):
        spawned = await memory_server._replay_pending_outbox()
        if spawned:
            await asyncio.gather(*spawned, return_exceptions=True)

    # 仍 pending（handler 没跑、也没 append_done）
    pending = await ob.apending_ops("小天")
    assert len(pending) == 1
    assert pending[0]["op_id"] == op_id


@pytest.mark.asyncio
async def test_replay_respects_concurrency_semaphore(tmp_path):
    """启动补跑不应无限 fan-out：_REPLAY_CONCURRENCY=4 应限制同时在飞 handler 数。"""
    ob, _ = _install_fresh_memory_state(str(tmp_path))
    from app import memory_server
    from memory.outbox import OP_POST_TURN_SIGNALS

    # 登记 10 个 pending op
    for i in range(10):
        await ob.aappend_pending("小天", OP_POST_TURN_SIGNALS, {"i": i})

    in_flight = 0
    max_in_flight = 0

    async def _slow_handler(name: str, payload: dict):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        # 让调度器给其他 task 机会启动（多次 yield）
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
    """Codex PR#905 P2: 角色从 config 移除但 outbox 还有 pending → 必须仍补跑。"""
    ob, mock_cm = _install_fresh_memory_state(str(tmp_path))
    from app import memory_server
    from memory.outbox import OP_POST_TURN_SIGNALS

    # 登记一条 pending op（在 "小天" 的 outbox 里）
    await ob.aappend_pending("小天", OP_POST_TURN_SIGNALS, {"i": 1})

    # 模拟 config 被改成不再包含小天（但磁盘上的 outbox 还在）
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

    # 仍然补跑了小天，尽管 config 里没有
    assert calls == ["小天"]
    assert await ob.apending_ops("小天") == []


@pytest.mark.asyncio
async def test_end_to_end_kill_then_replay_persists_side_effect(tmp_path):
    """端到端：handler 把 fact 写入假 FactStore 但在 append_done 前"崩溃"，
    新进程加载 outbox → 重跑 → side effect 最终落盘。"""
    ob, _ = _install_fresh_memory_state(str(tmp_path))
    from app import memory_server
    from memory.outbox import OP_POST_TURN_SIGNALS

    # 第一跑：handler 写"fact"到 side-effect 状态但在 append_done 前进程死
    side_effect_log: list[str] = []

    async def _handler_run1(name: str, payload: dict):
        side_effect_log.append(f"run1:{name}:{payload.get('tag')}")
        raise RuntimeError("process killed")  # 模拟 append_done 前崩

    with patch.dict(
        memory_server._OUTBOX_HANDLERS,
        {OP_POST_TURN_SIGNALS: _handler_run1},
        clear=False,
    ):
        await ob.aappend_pending("小天", OP_POST_TURN_SIGNALS, {"tag": "rebuttal_msg"})
        # 直接触发一次 replay 模拟 "崩溃发生在第一次 replay 调用期间"
        spawned = await memory_server._replay_pending_outbox()
        await asyncio.gather(*spawned, return_exceptions=True)

    assert side_effect_log == ["run1:小天:rebuttal_msg"]
    # op 仍 pending：因为 handler raise，_run_outbox_op 不会 append_done
    pending = await ob.apending_ops("小天")
    assert len(pending) == 1

    # 第二跑：新进程（fresh outbox 实例 + handler 改为正常版本）
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

    # side effect 在重启后被重放
    assert "run2:小天:rebuttal_msg" in side_effect_log
    # done 写入，不再 pending
    assert await fresh_ob.apending_ops("小天") == []


@pytest.mark.asyncio
async def test_append_pending_failure_falls_back_to_in_memory(tmp_path):
    """Outbox 写失败 → 降级为传统内存任务；主流程不应崩溃。"""
    ob, _ = _install_fresh_memory_state(str(tmp_path))
    from app import memory_server

    # 强制 aappend_pending 抛异常
    async def _boom(*a, **kw):
        raise OSError("disk full")

    ob.aappend_pending = _boom  # type: ignore[assignment]

    # 同时 patch _run_post_turn_signals 成 noop，避免真 LLM 调用
    noop = AsyncMock(return_value=None)
    with patch("app.memory_server.post_turn._run_post_turn_signals", noop):
        task = await memory_server._spawn_outbox_post_turn_signals(
            "小天", [HumanMessage(content="hi")]
        )
        await task

    # 降级路径：函数被调用过
    noop.assert_called_once()
    # 没有 pending 记录产生（写盘本来就失败了）
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

    # reconcile 必须是 await 到底的：换成 create_task / spawn 就又重叠了
    awaited = {id(a.value) for a in ast.walk(fn) if isinstance(a, ast.Await)}
    assert all(id(g) in awaited for g in gathers), \
        'reconcile 的 gather 没有被 await，补跑会和重放重叠'

    replay_lines = {c.lineno for c in replay_calls}
    gather_lines = {g.lineno for g in gathers}

    def _covers(stmt, lines):
        return any(getattr(n, 'lineno', None) in lines for n in ast.walk(stmt))

    # 在语句序列层面比先后，而不是比行号：谁被包在 try/if 里都不影响判定
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
                # 两者落在同一条语句里 = 这是个外层容器（async with / try），
                # 它只说明"都在里面"，判不了先后，继续往内层找。
                continue
            checked += 1
            assert max(idx_gather) < min(idx_replay), (
                'outbox 补跑排在了 reconcile 前面：补跑的 op 是后台 task，'
                '会和重放并发写同一批 view 文件，重放结果可能被静默整覆盖'
            )
    assert checked, 'reconcile 与 outbox 补跑不在同一层语句序列里，前后顺序无从判定'
