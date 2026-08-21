from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from app.agent_server import api_runtime
from main_logic.agent_event_bus import (
    USER_UTTERANCE_CONTENT_MAX_CHARS,
    USER_UTTERANCE_EVENT_ID_MAX_CHARS,
    USER_UTTERANCE_LANLAN_MAX_CHARS,
)
from plugin.core.state import state as plugin_state


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolate_user_utterance_runtime_state() -> Iterator[None]:
    """Keep both process-local replay and plugin-memory globals test-local."""
    with plugin_state._user_context_lock:  # noqa: SLF001
        stored_context = dict(plugin_state._user_context_store)  # noqa: SLF001
        plugin_state._user_context_store.clear()  # noqa: SLF001

    replay_cache = api_runtime._user_utterance_observed_event_ids  # noqa: SLF001
    stored_replay_ids = replay_cache.copy()
    replay_cache.clear()
    try:
        yield
    finally:
        with plugin_state._user_context_lock:  # noqa: SLF001
            plugin_state._user_context_store.clear()  # noqa: SLF001
            plugin_state._user_context_store.update(stored_context)  # noqa: SLF001
        replay_cache.clear()
        replay_cache.update(stored_replay_ids)


def _observed_event(
    *,
    event_id: str | None = None,
    lanlan_name: str = "皖萱",
    content: str = "你好",
    is_voice: bool = False,
) -> dict[str, Any]:
    return {
        "event_type": "user_utterance_observed",
        "event_id": event_id or str(uuid.uuid4()),
        "timestamp": time.time(),
        "lanlan_name": lanlan_name,
        "content": content,
        "is_voice": is_voice,
    }


def _bucket(bucket_id: str) -> list[dict[str, Any]]:
    return plugin_state.get_user_context(bucket_id=bucket_id, limit=100)


def _canonical_payload(event: dict[str, Any], *, lanlan_name: str) -> dict[str, Any]:
    return {
        "type": "user_message",
        "content": event["content"],
        "lanlan": lanlan_name,
        "is_voice": event["is_voice"],
        "source": "main_logic.core",
        "_event_id": event["event_id"],
    }


def _without_runtime_timestamp(event: dict[str, Any]) -> dict[str, Any]:
    copied = dict(event)
    timestamp = copied.pop("_ts")
    assert type(timestamp) is float
    return copied


@pytest.mark.asyncio
@pytest.mark.parametrize("is_voice", [False, True], ids=["text", "voice"])
async def test_observed_utterance_writes_default_and_normalized_lanlan_before_gates(
    monkeypatch: pytest.MonkeyPatch,
    is_voice: bool,
) -> None:
    monkeypatch.setattr(api_runtime.Modules, "analyzer_enabled", False)
    monkeypatch.setattr(
        api_runtime.Modules,
        "agent_flags",
        {"user_plugin_enabled": False},
    )
    monkeypatch.setattr(api_runtime.Modules, "plugin_lifecycle_started", False)
    event = _observed_event(
        lanlan_name="  皖萱  ",
        content="语音消息" if is_voice else "文字消息",
        is_voice=is_voice,
    )

    await api_runtime._on_session_event(event)

    expected = _canonical_payload(event, lanlan_name="皖萱")
    assert [_without_runtime_timestamp(item) for item in _bucket("default")] == [expected]
    assert [_without_runtime_timestamp(item) for item in _bucket("皖萱")] == [expected]
    assert _bucket("  皖萱  ") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("lanlan_name", ["", "   ", "default", " default "])
async def test_blank_or_default_lanlan_writes_default_bucket_once(
    lanlan_name: str,
) -> None:
    event = _observed_event(lanlan_name=lanlan_name)

    await api_runtime._on_session_event(event)

    assert len(_bucket("default")) == 1
    with plugin_state._user_context_lock:  # noqa: SLF001
        assert list(plugin_state._user_context_store) == ["default"]  # noqa: SLF001


_MALFORMED_FIELDS: list[tuple[str, object]] = [
    ("event_id", None),
    ("event_id", 7),
    ("event_id", True),
    ("event_id", ""),
    ("event_id", "   "),
    ("event_id", "e" * (USER_UTTERANCE_EVENT_ID_MAX_CHARS + 1)),
    ("timestamp", None),
    ("timestamp", "now"),
    ("timestamp", True),
    ("timestamp", float("nan")),
    ("timestamp", float("inf")),
    ("lanlan_name", None),
    ("lanlan_name", 7),
    ("lanlan_name", True),
    ("lanlan_name", "兰" * (USER_UTTERANCE_LANLAN_MAX_CHARS + 1)),
    ("content", None),
    ("content", 7),
    ("content", True),
    ("content", ""),
    ("content", "   "),
    ("content", "x" * (USER_UTTERANCE_CONTENT_MAX_CHARS + 1)),
    ("is_voice", None),
    ("is_voice", 0),
    ("is_voice", 1),
    ("is_voice", "false"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "bad_value"),
    _MALFORMED_FIELDS,
    ids=[f"{field}-{index}" for index, (field, _) in enumerate(_MALFORMED_FIELDS)],
)
async def test_malformed_observed_utterance_is_rejected(
    field: str,
    bad_value: object,
) -> None:
    event = _observed_event()
    event[field] = bad_value

    await api_runtime._on_session_event(event)

    with plugin_state._user_context_lock:  # noqa: SLF001
        assert not plugin_state._user_context_store  # noqa: SLF001
    assert not api_runtime._user_utterance_observed_event_ids  # noqa: SLF001


@pytest.mark.asyncio
async def test_replayed_observed_utterance_is_written_once() -> None:
    event = _observed_event()

    await api_runtime._on_session_event(event)
    await api_runtime._on_session_event(dict(event))

    assert len(_bucket("default")) == 1
    assert len(_bucket("皖萱")) == 1
    assert list(api_runtime._user_utterance_observed_event_ids) == [event["event_id"]]  # noqa: SLF001


@pytest.mark.asyncio
async def test_merged_process_loopback_does_not_duplicate_preseeded_event() -> None:
    event = _observed_event()
    payload = _canonical_payload(event, lanlan_name="皖萱")
    plugin_state.add_user_context_event("default", payload)
    plugin_state.add_user_context_event("皖萱", payload)

    await api_runtime._on_session_event(event)

    assert [_without_runtime_timestamp(item) for item in _bucket("default")] == [payload]
    assert [_without_runtime_timestamp(item) for item in _bucket("皖萱")] == [payload]
