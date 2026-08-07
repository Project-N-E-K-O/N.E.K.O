"""Tests for Main's local user-utterance mirror and Agent relay scheduling.

Covers ``LLMSessionManager._publish_user_utterance_to_plugin_bus`` — the
Main process keeps its existing local compatibility buckets and schedules one
best-effort event for the Agent process that serves plugin context memory.

We don't construct a real ``LLMSessionManager`` (heavy: needs config,
websocket, sessions). Instead, we call the unbound method with a tiny
SimpleNamespace that carries only the attributes the helper touches
(``lanlan_name``).
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from main_logic import agent_event_bus
from main_logic import core as core_facade
from main_logic.agent_event_bus import USER_UTTERANCE_CONTENT_MAX_CHARS
from main_logic.core import LLMSessionManager
from plugin.core.state import state as plugin_state


def _make_fake_core(lanlan_name: str = "皖萱") -> SimpleNamespace:
    """Minimal stand-in for LLMSessionManager — only the attributes the helper reads."""
    return SimpleNamespace(lanlan_name=lanlan_name)


def _drain_bucket(bucket_id: str) -> list[dict]:
    """Snapshot a bucket's events without affecting state."""
    return plugin_state.get_user_context(bucket_id=bucket_id, limit=500)


@pytest.fixture(autouse=True)
def _isolate_user_context():
    """Each test starts with empty user-context stores. Restore after."""
    snapshot_default = _drain_bucket("default")
    snapshot_lanlan = _drain_bucket("皖萱")
    plugin_state._user_context_store.clear()  # noqa: SLF001
    yield
    plugin_state._user_context_store.clear()  # noqa: SLF001
    # The state is module-global; we don't restore the prior snapshot to
    # keep tests independent (no test should depend on bucket contents
    # from prior tests).
    _ = (snapshot_default, snapshot_lanlan)  # silence lint


@pytest.mark.unit
def test_publish_writes_to_default_and_lanlan_buckets():
    fake = _make_fake_core(lanlan_name="皖萱")
    LLMSessionManager._publish_user_utterance_to_plugin_bus(
        fake, "你好啊", is_voice_source=True,
    )

    default_events = _drain_bucket("default")
    lanlan_events = _drain_bucket("皖萱")

    assert len(default_events) == 1
    assert len(lanlan_events) == 1
    for ev in (default_events[0], lanlan_events[0]):
        assert ev["type"] == "user_message"
        assert ev["content"] == "你好啊"
        assert ev["lanlan"] == "皖萱"
        assert ev["is_voice"] is True
        assert ev["source"] == "main_logic.core"
        assert isinstance(ev["_event_id"], str)
        assert isinstance(ev["_ts"], float)
    assert default_events[0]["_event_id"] == lanlan_events[0]["_event_id"]


@pytest.mark.unit
def test_publish_strips_whitespace_and_skips_empty():
    fake = _make_fake_core()
    LLMSessionManager._publish_user_utterance_to_plugin_bus(
        fake, "  hi  ", is_voice_source=False,
    )
    LLMSessionManager._publish_user_utterance_to_plugin_bus(
        fake, "   ", is_voice_source=False,
    )
    LLMSessionManager._publish_user_utterance_to_plugin_bus(
        fake, "", is_voice_source=False,
    )
    LLMSessionManager._publish_user_utterance_to_plugin_bus(
        fake, None, is_voice_source=False,
    )

    events = _drain_bucket("default")
    assert len(events) == 1
    assert events[0]["content"] == "hi"
    assert events[0]["is_voice"] is False


@pytest.mark.unit
def test_publish_handles_missing_lanlan_name_gracefully():
    """Empty/None lanlan_name should still write to default bucket."""
    fake = _make_fake_core(lanlan_name="")
    LLMSessionManager._publish_user_utterance_to_plugin_bus(
        fake, "ping", is_voice_source=True,
    )
    assert len(_drain_bucket("default")) == 1
    assert _drain_bucket("default")[0]["lanlan"] == "default"
    # No second bucket should be created when lanlan_name is empty.
    assert plugin_state._user_context_store.get("") is None  # noqa: SLF001


@pytest.mark.unit
def test_publish_non_string_input_is_ignored():
    fake = _make_fake_core()
    LLMSessionManager._publish_user_utterance_to_plugin_bus(
        fake, 12345, is_voice_source=True,  # type: ignore[arg-type]
    )
    LLMSessionManager._publish_user_utterance_to_plugin_bus(
        fake, ["not", "a", "string"], is_voice_source=True,  # type: ignore[arg-type]
    )
    assert _drain_bucket("default") == []


@pytest.mark.unit
def test_publish_dedupes_when_lanlan_name_equals_default():
    """If lanlan_name is literally "default", we must not write twice to it."""
    fake = _make_fake_core(lanlan_name="default")
    LLMSessionManager._publish_user_utterance_to_plugin_bus(
        fake, "echo me once", is_voice_source=True,
    )

    events = _drain_bucket("default")
    assert len(events) == 1
    assert events[0]["content"] == "echo me once"


@pytest.mark.unit
def test_repeated_publishes_accumulate_in_chronological_order():
    fake = _make_fake_core(lanlan_name="兰兰")
    for i, msg in enumerate(["first", "second", "third"]):
        LLMSessionManager._publish_user_utterance_to_plugin_bus(
            fake, msg, is_voice_source=(i % 2 == 0),
        )

    contents = [ev["content"] for ev in _drain_bucket("兰兰")]
    assert contents == ["first", "second", "third"]


@pytest.mark.unit
def test_publish_bounds_content_before_local_dispatch():
    fake = _make_fake_core()
    oversized = "x" * (USER_UTTERANCE_CONTENT_MAX_CHARS + 50)

    LLMSessionManager._publish_user_utterance_to_plugin_bus(
        fake, oversized, is_voice_source=False,
    )

    assert _drain_bucket("default")[0]["content"] == (
        "x" * USER_UTTERANCE_CONTENT_MAX_CHARS
    )


@pytest.mark.unit
def test_missing_fire_task_does_not_create_publisher_coroutine(monkeypatch):
    calls = 0

    def fake_publish(**_kwargs):
        nonlocal calls
        calls += 1

        async def _publish():
            return True

        return _publish()

    monkeypatch.setattr(core_facade, "publish_user_utterance_observed", fake_publish)

    LLMSessionManager._publish_user_utterance_to_plugin_bus(
        _make_fake_core(), "no scheduler", is_voice_source=False,
    )

    assert calls == 0


@pytest.mark.unit
def test_publish_schedules_exactly_one_relay_without_running_it_inline(monkeypatch):
    scheduled = []
    published = []

    async def fake_publish(**kwargs):
        published.append(kwargs)
        return True

    fake = SimpleNamespace(lanlan_name="皖萱", _fire_task=scheduled.append)
    monkeypatch.setattr(core_facade, "publish_user_utterance_observed", fake_publish)

    LLMSessionManager._publish_user_utterance_to_plugin_bus(
        fake, "relayed text", is_voice_source=True,
    )

    assert len(scheduled) == 1
    assert published == []
    local_event = _drain_bucket("皖萱")[0]
    assert asyncio.run(scheduled.pop()) is True
    assert published[0]["event_id"] == local_event["_event_id"]
    assert type(published[0]["timestamp"]) is float
    assert published[0]["lanlan_name"] == "皖萱"
    assert published[0]["content"] == "relayed text"
    assert published[0]["is_voice"] is True


@pytest.mark.unit
def test_publish_constructor_failure_does_not_escape(monkeypatch):
    scheduled = []

    def fake_publish(**_kwargs):
        raise RuntimeError("publisher construction failed")

    fake = SimpleNamespace(lanlan_name="皖萱", _fire_task=scheduled.append)
    monkeypatch.setattr(core_facade, "publish_user_utterance_observed", fake_publish)

    LLMSessionManager._publish_user_utterance_to_plugin_bus(
        fake, "real input", is_voice_source=False,
    )

    assert scheduled == []
    assert _drain_bucket("皖萱")[0]["content"] == "real input"


@pytest.mark.unit
def test_publish_closes_coroutine_when_fire_task_raises(monkeypatch):
    created = []

    async def relay():
        return True

    def fake_publish(**_kwargs):
        coroutine = relay()
        created.append(coroutine)
        return coroutine

    def fail_schedule(_coroutine):
        raise RuntimeError("scheduler failed")

    fake = SimpleNamespace(lanlan_name="皖萱", _fire_task=fail_schedule)
    monkeypatch.setattr(core_facade, "publish_user_utterance_observed", fake_publish)

    LLMSessionManager._publish_user_utterance_to_plugin_bus(
        fake, "real input", is_voice_source=False,
    )

    assert inspect.getcoroutinestate(created[0]) == inspect.CORO_CLOSED


@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_utterance_publisher_returns_false_without_bridge(monkeypatch):
    monkeypatch.setattr(agent_event_bus, "_main_bridge_ref", None)

    assert await agent_event_bus.publish_user_utterance_observed(
        event_id="event-1",
        timestamp=1.0,
        lanlan_name="皖萱",
        content="hello",
        is_voice=False,
    ) is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_utterance_publisher_sends_only_canonical_transport_fields(
    monkeypatch,
):
    sent = []

    class Bridge:
        async def publish_session_event(self, event):
            sent.append(event)
            return True

    monkeypatch.setattr(agent_event_bus, "_main_bridge_ref", Bridge())

    assert await agent_event_bus.publish_user_utterance_observed(
        event_id=" event-2 ",
        timestamp=2.0,
        lanlan_name=" 皖萱 ",
        content=" hello ",
        is_voice=True,
    ) is True
    assert sent == [{
        "event_type": "user_utterance_observed",
        "event_id": "event-2",
        "timestamp": 2.0,
        "lanlan_name": "皖萱",
        "content": "hello",
        "is_voice": True,
    }]
