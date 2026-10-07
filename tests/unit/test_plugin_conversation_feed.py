"""Conversation-bus publishing: user utterance + AI turn + message timestamps.

Contract: user text/voice -> ``user_message``; AI turn end -> one record with
the whole sentence (``assistant_message`` / ``proactive_reply``); both carry
``ts`` so consumers can order the conversation.
"""

from __future__ import annotations

import asyncio
import queue
import re
import time
from types import SimpleNamespace

import pytest

from main_logic.core import turn as turn_module
from main_logic.core.proactive import ProactiveMixin


class _StubManager(turn_module.TurnMixin, ProactiveMixin):
    """Carries only what the publish path touches; no real session manager."""

    def __init__(self) -> None:
        self.lanlan_name = "YUI"
        self.current_speech_id = "turn-user-1"
        self._current_ai_turn_text = ""
        self._current_ai_turn_id = ""
        self._current_ai_turn_started_at = 0.0
        self._current_ai_turn_client_owned = False
        self.emotion_pattern = re.compile("<(.*?)>")
        self.sync_message_queue = queue.Queue()
        self._active_text_request_id = None
        self.websocket = None
        self.websocket_lock = None
        self.session = None
        self.noted: list[str | None] = []
        self._bg: list[asyncio.Task] = []

    def _fire_task(self, coro):
        task = asyncio.ensure_future(coro)
        self._bg.append(task)
        return task

    def _note_ai_turn(self, *, text=None):
        self.noted.append(text)

    async def drain(self) -> None:
        if self._bg:
            await asyncio.gather(*self._bg, return_exceptions=True)
            await asyncio.sleep(0)  # 让 add_done_callback 排到本帧执行


@pytest.fixture
def published(monkeypatch):
    calls: list[dict] = []

    async def _fake(lanlan_name, **kwargs):
        calls.append({"lanlan_name": lanlan_name, **kwargs})
        return True

    monkeypatch.setattr(
        turn_module, "publish_conversation_turn_observed_best_effort", _fake,
    )
    return calls


def test_user_utterance_published_with_own_timestamp(published):
    async def _scenario():
        stub = _StubManager()
        before = time.time()
        turn_module.TurnMixin._publish_user_utterance_to_plugin_bus(
            stub, "  在吗  ", is_voice_source=True,
        )
        await stub.drain()
        return stub, before

    stub, before = asyncio.run(_scenario())
    assert len(published) == 1
    call = published[0]
    assert call["lanlan_name"] == "YUI"
    assert call["content"] == "在吗"
    assert call["turn_type"] == "user_message"
    assert call["conversation_id"] == "turn-user-1"
    assert call["metadata"]["role"] == "master"
    assert call["metadata"]["is_voice"] is True
    assert before <= call["ts"] <= time.time() + 1
    assert call["metadata"]["ts"] == call["ts"]
    assert call["message_count"] == 1


def test_blank_user_utterance_is_not_published(published):
    async def _scenario():
        stub = _StubManager()
        turn_module.TurnMixin._publish_user_utterance_to_plugin_bus(
            stub, "   ", is_voice_source=False,
        )
        await stub.drain()

    asyncio.run(_scenario())
    assert published == []


def test_failed_user_publish_does_not_create_pairing_state(monkeypatch):
    calls: list[dict] = []

    async def _fake_failed(lanlan_name, **kwargs):
        calls.append(kwargs)
        return False  # 总线没收到

    monkeypatch.setattr(
        turn_module, "publish_conversation_turn_observed_best_effort", _fake_failed,
    )

    async def _scenario():
        stub = _StubManager()
        turn_module.TurnMixin._publish_user_utterance_to_plugin_bus(
            stub, "发不出去的一句", is_voice_source=False,
        )
        await stub.drain()
        return stub

    stub = asyncio.run(_scenario())
    assert calls, "仍然尝试发布"
    assert not hasattr(stub, "_plugin_bus_user_turn_ids")


def test_ai_turn_publishes_whole_text_once(published):
    async def _scenario():
        stub = _StubManager()
        stub._current_ai_turn_text = "喵，我在的。"
        stub._current_ai_turn_id = "turn-42"
        stub._current_ai_turn_started_at = time.time() - 2.0
        started_at = stub._current_ai_turn_started_at
        turn_module.TurnMixin._flush_ai_turn_text_to_tracker(
            stub, turn_type="proactive_reply",
        )
        await stub.drain()
        return stub, started_at

    stub, started_at = asyncio.run(_scenario())
    assert len(published) == 1
    call = published[0]
    assert call["content"] == "喵，我在的。"
    assert call["turn_type"] == "proactive_reply"
    assert call["conversation_id"] == "turn-42"
    assert call["message_count"] == 1
    assert call["metadata"]["role"] == "cat"
    # ts 取首块（开口）时刻，不是 flush 时刻；ts_end 才是收尾时刻
    assert call["ts"] == call["metadata"]["ts"]
    assert call["ts"] == pytest.approx(started_at)
    assert call["metadata"]["ts_end"] >= call["ts"]
    # buffer 已清空，且 activity tracker 拿到同一份文本
    assert stub._current_ai_turn_text == ""
    assert stub._current_ai_turn_id == ""
    assert stub._current_ai_turn_started_at == 0.0
    assert stub.noted == ["喵，我在的。"]


def test_ai_turn_without_paired_user_message_stays_single(published):
    async def _scenario():
        stub = _StubManager()
        stub._current_ai_turn_text = "喵，自己说一句。"
        stub._current_ai_turn_id = "turn-self"
        turn_module.TurnMixin._flush_ai_turn_text_to_tracker(
            stub, turn_type="proactive_reply",
        )
        await stub.drain()

    asyncio.run(_scenario())
    assert published and published[0]["message_count"] == 1


def test_offline_ephemeral_turn_is_not_published_twice(published):
    """The offline client publishes ephemeral turns itself; the manager steps aside."""
    from main_logic.omni_offline_client import OmniOfflineClient

    async def _scenario():
        stub = _StubManager()
        stub.session = object.__new__(OmniOfflineClient)  # 只做 isinstance 判定
        stub._current_ai_turn_text = "喵，这条由客户端自己发。"
        stub._current_ai_turn_client_owned = True
        token = turn_module._proactive_expected_sid.set("turn-ephemeral")
        try:
            turn_module.TurnMixin._flush_ai_turn_text_to_tracker(
                stub, turn_type="proactive_reply",
            )
            await stub.drain()
        finally:
            turn_module._proactive_expected_sid.reset(token)

    asyncio.run(_scenario())
    assert published == [], "客户端已发布的主动轮不应再被管理层发布一次"


def test_offline_normal_turn_is_published(published):
    """Ordinary turns (no _proactive_expected_sid pinned) are still published."""
    from main_logic.omni_offline_client import OmniOfflineClient

    async def _scenario():
        stub = _StubManager()
        stub.session = object.__new__(OmniOfflineClient)
        stub._current_ai_turn_text = "普通回复。"
        turn_module.TurnMixin._flush_ai_turn_text_to_tracker(stub)
        await stub.drain()

    asyncio.run(_scenario())
    assert len(published) == 1


def test_identical_text_in_a_later_turn_is_still_published(published):
    async def _scenario():
        stub = _StubManager()
        stub._current_ai_turn_started_at = time.time()
        stub._current_ai_turn_text = "喵，我在的。"  # 与上一轮完全相同的文本
        turn_module.TurnMixin._flush_ai_turn_text_to_tracker(stub)
        await stub.drain()

    asyncio.run(_scenario())
    assert len(published) == 1, "相同文本属于新轮次时不能被当成重复丢掉"


def test_ai_flush_defaults_to_assistant_message(published):
    async def _scenario():
        stub = _StubManager()
        stub._current_ai_turn_text = "普通回复。"
        turn_module.TurnMixin._flush_ai_turn_text_to_tracker(stub)
        await stub.drain()

    asyncio.run(_scenario())
    assert published and published[0]["turn_type"] == "assistant_message"


def test_empty_ai_turn_publishes_nothing(published):
    async def _scenario():
        stub = _StubManager()
        turn_module.TurnMixin._flush_ai_turn_text_to_tracker(stub)
        await stub.drain()

    asyncio.run(_scenario())
    assert published == []


def test_publisher_event_carries_message_ts(monkeypatch):
    from main_logic import agent_event_bus as bus

    captured: list[dict] = []

    class _Bridge:
        async def publish_session_event_threadsafe(self, event):
            captured.append(event)
            return True

    monkeypatch.setattr(bus, "_main_bridge_ref", _Bridge())
    ok = asyncio.run(bus.publish_conversation_turn_observed_best_effort(
        "YUI",
        content="我在的。",
        turn_type="assistant_message",
        conversation_id="turn-1",
        source="main_logic.core",
        message_count=1,
        metadata={"role": "cat", "ts": 111.5},
        ts=111.5,
    ))
    assert ok is True
    assert captured and captured[0]["ts"] == 111.5
    assert captured[0]["metadata"]["role"] == "cat"


def test_forward_conversation_turn_keeps_producer_ts(monkeypatch):
    from app.agent_server import api_runtime
    from plugin.server.messaging import plane_bridge

    records: list[dict] = []
    monkeypatch.setattr(api_runtime, "_user_plugins_enabled", lambda: True)
    monkeypatch.setattr(
        plane_bridge, "publish_record",
        lambda *, store, record, topic: records.append(record) or True,
    )

    assert api_runtime._forward_conversation_turn({
        "content": "我在的。",
        "ts": 222.25,
        "turn_type": "assistant_message",
        "conversation_id": "turn-2",
        "lanlan_name": "YUI",
        "source": "main_logic.core",
    }) is True
    assert records and records[0]["metadata"]["ts"] == 222.25
    assert records[0]["timestamp"] > 222.25
    assert records[0]["content"] == "我在的。"


def test_forward_conversation_turn_falls_back_to_now(monkeypatch):
    from app.agent_server import api_runtime
    from plugin.server.messaging import plane_bridge

    records: list[dict] = []
    monkeypatch.setattr(api_runtime, "_user_plugins_enabled", lambda: True)
    monkeypatch.setattr(
        plane_bridge, "publish_record",
        lambda *, store, record, topic: records.append(record) or True,
    )
    before = time.time()
    api_runtime._forward_conversation_turn({"content": "没有 ts 的旧事件"})
    assert records and before <= records[0]["timestamp"] <= time.time() + 1


def test_non_finite_producer_ts_falls_back_to_now():
    from app.agent_server import api_runtime

    before = time.time()
    for bad in (float("nan"), float("inf"), float("-inf")):
        resolved = api_runtime._resolve_conversation_ts({"ts": bad})
        assert before <= resolved <= time.time() + 1, f"{bad} 必须回退到当前时间"
    assert api_runtime._resolve_conversation_ts({"ts": 123.5}) == 123.5


def test_late_ai_reply_remains_visible_to_timestamp_cursor(monkeypatch):
    """A reply can start before a user message but arrive after the cursor."""
    from app.agent_server import api_runtime
    from plugin.message_plane.stores import TopicStore
    from plugin.server.messaging import plane_bridge

    store = TopicStore(name="conversations", maxlen=16)
    monkeypatch.setattr(api_runtime, "_user_plugins_enabled", lambda: True)

    def publish(*, store: str, record: dict, topic: str):
        conversation_store.publish(topic, record)
        return True

    conversation_store = store
    monkeypatch.setattr(plane_bridge, "publish_record", publish)
    from tests.fake_clock import patch_module_clock

    arrivals = iter([15.0, 20.0])
    patch_module_clock(monkeypatch, api_runtime, time=lambda: next(arrivals))
    assert api_runtime._forward_conversation_turn({"content": "user", "ts": 12.0})
    first = store.query(topic="all")
    cursor = first[0]["index"]["timestamp"]
    assert api_runtime._forward_conversation_turn({"content": "reply", "ts": 10.0})
    second = store.query(topic="all", since_ts=cursor)
    assert any(item["payload"]["content"] == "reply" for item in second)
    reply = next(item for item in second if item["payload"]["content"] == "reply")
    assert reply["index"]["timestamp"] == 20.0
    assert reply["payload"]["metadata"]["ts"] == 10.0


@pytest.mark.parametrize("proactive", [False, True])
def test_emit_turn_end_labels_realtime_owner(published, proactive):
    """The common realtime completion path preserves the proactive label."""
    async def scenario():
        stub = _StubManager()
        stub.state = SimpleNamespace(owner=(
            turn_module.TurnOwner.PROACTIVE if proactive else turn_module.TurnOwner.USER
        ))
        stub._current_ai_turn_text = "reply"
        stub._queue_turn_end = lambda *args, **kwargs: {"data": "turn end"}

        async def send(message):
            pass

        stub._send_turn_end_to_frontend = send
        await stub._emit_turn_end(None)
        await stub.drain()

    asyncio.run(scenario())
    assert published[0]["turn_type"] == ("proactive_reply" if proactive else "assistant_message")


def test_proactive_label_survives_owner_change(published):
    async def scenario():
        stub = _StubManager()
        stub.state = SimpleNamespace(owner=turn_module.TurnOwner.USER)
        stub._current_ai_turn_type = "proactive_reply"
        stub._current_ai_turn_text = "interrupted proactive reply"
        stub._flush_ai_turn_text_to_tracker()
        await stub.drain()
        assert stub._current_ai_turn_type is None

    asyncio.run(scenario())
    assert published[0]["turn_type"] == "proactive_reply"


def test_probe_keeps_since_filter_across_follow_polls(monkeypatch):
    """Sequence deduplication must not discard the requested time filter."""
    import sys

    from scripts import plugin_conversation_probe as probe
    from tests.fake_clock import patch_module_clock

    queries = []

    def query(sock, op, args, req_id, **kwargs):
        if op == "bus.query":
            queries.append(args)
        return {"ok": True, "result": {"items": []}}

    monkeypatch.setattr(sys, "argv", ["probe", "--follow", "--show-all", "--since-ts", "10", "--seconds", "1"])
    monkeypatch.setattr(probe, "_query", query)
    monkeypatch.setattr(probe, "_connect", lambda endpoint: object())
    ticks = iter([0.0, 0.0, 2.0])
    patch_module_clock(monkeypatch, probe, time=lambda: next(ticks), sleep=lambda seconds: None)
    assert probe.main() == 0
    assert len(queries) == 2
    assert all(args["since_ts"] == 10.0 for args in queries)


@pytest.mark.parametrize("kind", ["response", "agent_callback"])
def test_offline_proactive_close_from_other_context_does_not_duplicate(published, kind):
    """The interruption caller does not inherit the proactive task's context."""
    from main_logic.omni_offline_client import OmniOfflineClient

    async def scenario():
        stub = _StubManager()
        stub.session = object.__new__(OmniOfflineClient)
        stub.state = SimpleNamespace(owner=turn_module.TurnOwner.PROACTIVE)
        stub._queue_turn_end = lambda *args, **kwargs: {"data": "turn end"}
        stub._queue_agent_callback_turn_end = lambda *args: {"data": "callback end"}

        async def produce():
            token = turn_module._proactive_expected_sid.set("offline-proactive")
            try:
                await stub.send_lanlan_response("committed partial reply")
            finally:
                turn_module._proactive_expected_sid.reset(token)

        await asyncio.create_task(produce())
        assert turn_module._proactive_expected_sid.get() is None
        assert stub._current_ai_turn_client_owned is True
        stub._close_interrupted_offline_turn(kind)
        await stub.drain()
        assert stub._current_ai_turn_client_owned is False
        assert stub.noted == ["committed partial reply"]
        # The same offline session's next ordinary turn must still be published.
        stub.state.owner = turn_module.TurnOwner.USER
        await stub.send_lanlan_response("ordinary reply")
        stub._flush_ai_turn_text_to_tracker()
        await stub.drain()

    asyncio.run(scenario())
    assert [record["content"] for record in published] == ["ordinary reply"]


@pytest.mark.parametrize("delivered", [True, False])
def test_real_voice_nudge_labels_next_reply_without_sm_claim(published, delivered):
    from main_logic.omni_realtime_client import OmniRealtimeClient

    async def scenario():
        stub = _StubManager()
        session = object.__new__(OmniRealtimeClient)
        stub.session = session
        stub.state = SimpleNamespace(owner=turn_module.TurnOwner.USER)
        stub.is_active = True
        stub.user_language = "en"
        stub._takeover_active = False
        stub.is_hot_swap_imminent = False
        stub._voice_proactive_inject_lock = asyncio.Lock()
        stub.is_goodbye_silent = lambda: False
        stub._independent_asr_user_turn_active = lambda: False
        session._proactive_inject_awaiting_outcome = False

        async def inject(**kwargs):
            if delivered:
                await asyncio.create_task(stub.send_lanlan_response("voice nudge reply"))
            return delivered

        session.prompt_ephemeral = inject
        assert await stub.trigger_voice_proactive_nudge() is delivered
        assert getattr(stub, "_plugin_bus_voice_proactive", None) is None
        if not delivered:
            await stub.send_lanlan_response("ordinary reply after rejected nudge")
        stub._flush_ai_turn_text_to_tracker()
        await stub.drain()

    asyncio.run(scenario())
    assert published[0]["turn_type"] == ("proactive_reply" if delivered else "assistant_message")


def test_user_timestamp_can_preserve_transcript_arrival(published):
    async def scenario():
        stub = _StubManager()
        stub._publish_user_utterance_to_plugin_bus("transcript", is_voice_source=True, ts=123.5)
        await stub.drain()

    asyncio.run(scenario())
    assert published[0]["metadata"]["ts"] == 123.5


@pytest.mark.parametrize("rejected", [False, True])
def test_real_voice_callback_injection_marks_or_clears_next_reply(published, rejected):
    from main_logic.core import LLMSessionManager
    from tests.unit.test_proactive_sm_integration import _make_mgr, _make_voice_sess

    async def scenario():
        reject_handler = None

        async def inject(instruction, **kwargs):
            nonlocal reject_handler
            reject_handler = kwargs["on_rejected"]

        session = _make_voice_sess(inject=inject)
        mgr = _make_mgr(session=session)
        mgr.pending_agent_callbacks = [{"status": "completed", "summary": "task complete"}]
        assert await LLMSessionManager.trigger_agent_callbacks(mgr) is True
        assert mgr._plugin_bus_voice_proactive[0] is session
        if rejected:
            reject_handler("response_already_active")
            assert mgr._plugin_bus_voice_proactive is None
        # Run the actual first-chunk publisher on the same manager that injected.
        minimal = _StubManager()
        for field in (
            "emotion_pattern", "sync_message_queue", "_active_text_request_id",
            "websocket", "websocket_lock", "_current_ai_turn_text", "_bg", "noted",
        ):
            setattr(mgr, field, getattr(minimal, field))
        mgr._fire_task = lambda coro: mgr._bg.append(asyncio.create_task(coro))
        mgr._note_ai_turn = lambda *, text=None: mgr.noted.append(text)
        await turn_module.TurnMixin.send_lanlan_response(mgr, "callback or ordinary reply")
        mgr._flush_ai_turn_text_to_tracker()
        await asyncio.gather(*mgr._bg)

    asyncio.run(scenario())
    assert published[0]["turn_type"] == ("assistant_message" if rejected else "proactive_reply")
