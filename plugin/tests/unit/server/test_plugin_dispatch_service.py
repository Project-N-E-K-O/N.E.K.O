from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from plugin.server.application.plugins import dispatch_service as dispatch_module
from plugin.server.application.plugins.dispatch_service import PluginDispatchService

pytestmark = pytest.mark.plugin_unit


class _Host:
    def __init__(
        self,
        result: dict[str, object],
        *,
        alive: bool = True,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.result = result
        self.alive = alive
        self.error = error
        self.delay = delay
        self.calls: list[tuple[str, str, dict[str, object], float]] = []

    def health_check(self) -> SimpleNamespace:
        return SimpleNamespace(alive=self.alive)

    async def trigger_custom_event(
        self,
        *,
        event_type: str,
        event_id: str,
        args: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        self.calls.append((event_type, event_id, args, timeout))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.result


class _BarrierHost(_Host):
    def __init__(
        self,
        result: dict[str, object],
        *,
        started: asyncio.Event,
        peer_started: asyncio.Event,
    ) -> None:
        super().__init__(result)
        self.started = started
        self.peer_started = peer_started

    async def trigger_custom_event(
        self,
        *,
        event_type: str,
        event_id: str,
        args: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        self.calls.append((event_type, event_id, args, timeout))
        self.started.set()
        await asyncio.wait_for(self.peer_started.wait(), timeout=0.2)
        return self.result


def _handler(event_type: str, event_id: str) -> SimpleNamespace:
    return SimpleNamespace(meta=SimpleNamespace(event_type=event_type, id=event_id))


def _patch_handlers_and_hosts(
    monkeypatch: pytest.MonkeyPatch,
    handlers: dict[str, SimpleNamespace],
    hosts: dict[str, _Host],
) -> None:
    monkeypatch.setattr(
        dispatch_module.state,
        "get_event_handlers_snapshot_cached",
        lambda timeout=1.0: handlers,
    )
    monkeypatch.setattr(
        dispatch_module.state,
        "get_plugin_hosts_snapshot_cached",
        lambda timeout=1.0: hosts,
    )


@pytest.mark.asyncio
async def test_trigger_custom_event_subscribers_dispatches_matching_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    study_host = _Host({"action": "prime_context", "context": "screen"})
    other_host = _Host({"action": "noop"})
    _patch_handlers_and_hosts(
        monkeypatch,
        {
            "study_companion:voice_transcript:handle_transcript": _handler(
                "voice_transcript",
                "handle_transcript",
            ),
            "other_plugin:voice_transcript:other_handler": _handler(
                "voice_transcript",
                "other_handler",
            ),
            "legacy.entry": _handler("plugin_entry", "entry"),
        },
        {
            "study_companion": study_host,
            "other_plugin": other_host,
        },
    )

    results = await PluginDispatchService().trigger_custom_event_subscribers(
        event_type="voice_transcript",
        args={"transcript": "Yui explain this"},
        timeout=0.25,
    )

    assert results == [
        {
            "plugin_id": "other_plugin",
            "event_id": "other_handler",
            "success": True,
            "result": {"action": "noop"},
        },
        {
            "plugin_id": "study_companion",
            "event_id": "handle_transcript",
            "success": True,
            "result": {"action": "prime_context", "context": "screen"},
        },
    ]
    assert study_host.calls == [
        (
            "voice_transcript",
            "handle_transcript",
            {"transcript": "Yui explain this"},
            0.25,
        )
    ]
    assert other_host.calls == [
        (
            "voice_transcript",
            "other_handler",
            {"transcript": "Yui explain this"},
            0.25,
        )
    ]


@pytest.mark.asyncio
async def test_trigger_custom_event_subscribers_keeps_per_plugin_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready_host = _Host({"action": "cancel_response"})
    stopped_host = _Host({"action": "noop"}, alive=False)
    _patch_handlers_and_hosts(
        monkeypatch,
        {
            "alpha:voice_transcript:handle_transcript": _handler(
                "voice_transcript",
                "handle_transcript",
            ),
            "beta:voice_transcript:handle_transcript": _handler(
                "voice_transcript",
                "handle_transcript",
            ),
        },
        {
            "alpha": stopped_host,
            "beta": ready_host,
        },
    )

    results = await PluginDispatchService().trigger_custom_event_subscribers(
        event_type="voice_transcript",
        args={},
        timeout=0.25,
    )

    assert results[0]["plugin_id"] == "alpha"
    assert results[0]["event_id"] == "handle_transcript"
    assert results[0]["success"] is False
    assert results[0]["code"] == "PLUGIN_NOT_READY"
    assert results[1] == {
        "plugin_id": "beta",
        "event_id": "handle_transcript",
        "success": True,
        "result": {"action": "cancel_response"},
    }
    assert ready_host.calls


@pytest.mark.asyncio
async def test_trigger_custom_event_subscribers_runs_handlers_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alpha_started = asyncio.Event()
    beta_started = asyncio.Event()
    alpha_host = _BarrierHost(
        {"action": "noop"},
        started=alpha_started,
        peer_started=beta_started,
    )
    beta_host = _BarrierHost(
        {"action": "prime_context", "context": "ready"},
        started=beta_started,
        peer_started=alpha_started,
    )
    _patch_handlers_and_hosts(
        monkeypatch,
        {
            "alpha:voice_transcript:one": _handler("voice_transcript", "one"),
            "beta:voice_transcript:two": _handler("voice_transcript", "two"),
        },
        {
            "alpha": alpha_host,
            "beta": beta_host,
        },
    )

    results = await PluginDispatchService().trigger_custom_event_subscribers(
        event_type="voice_transcript",
        args={},
        timeout=0.25,
    )

    assert [item["plugin_id"] for item in results] == ["alpha", "beta"]
    assert alpha_host.calls and beta_host.calls


@pytest.mark.asyncio
async def test_trigger_custom_event_subscribers_isolates_handler_crashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crashing_host = _Host({"action": "noop"}, error=ValueError("bad plugin"))
    ready_host = _Host({"action": "prime_context", "context": "ready"})
    _patch_handlers_and_hosts(
        monkeypatch,
        {
            "alpha:voice_transcript:handle_transcript": _handler(
                "voice_transcript",
                "handle_transcript",
            ),
            "beta:voice_transcript:handle_transcript": _handler(
                "voice_transcript",
                "handle_transcript",
            ),
        },
        {
            "alpha": crashing_host,
            "beta": ready_host,
        },
    )

    results = await PluginDispatchService().trigger_custom_event_subscribers(
        event_type="voice_transcript",
        args={},
        timeout=0.25,
    )

    assert results[0]["plugin_id"] == "alpha"
    assert results[0]["success"] is False
    assert results[0]["code"] == "PLUGIN_EVENT_DISPATCH_FAILED"
    assert results[1] == {
        "plugin_id": "beta",
        "event_id": "handle_transcript",
        "success": True,
        "result": {"action": "prime_context", "context": "ready"},
    }


@pytest.mark.asyncio
async def test_trigger_custom_event_subscribers_times_out_one_handler_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slow_host = _Host({"action": "noop"}, delay=0.2)
    ready_host = _Host({"action": "cancel_response"})
    _patch_handlers_and_hosts(
        monkeypatch,
        {
            "alpha:voice_transcript:handle_transcript": _handler(
                "voice_transcript",
                "handle_transcript",
            ),
            "beta:voice_transcript:handle_transcript": _handler(
                "voice_transcript",
                "handle_transcript",
            ),
        },
        {
            "alpha": slow_host,
            "beta": ready_host,
        },
    )

    results = await PluginDispatchService().trigger_custom_event_subscribers(
        event_type="voice_transcript",
        args={},
        timeout=0.05,
    )

    assert results[0]["plugin_id"] == "alpha"
    assert results[0]["success"] is False
    assert results[0]["error_type"] == "TimeoutError"
    assert results[1] == {
        "plugin_id": "beta",
        "event_id": "handle_transcript",
        "success": True,
        "result": {"action": "cancel_response"},
    }


@pytest.mark.asyncio
async def test_trigger_arbitrated_custom_event_returns_contract_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    noop_host = _Host({"action": "noop"})
    context_host = _Host(
        {
            "action": "prime_context",
            "context": "screen context",
            "priority": 5,
        }
    )
    _patch_handlers_and_hosts(
        monkeypatch,
        {
            "alpha:voice_transcript:noop_handler": _handler(
                "voice_transcript",
                "noop_handler",
            ),
            "beta:voice_transcript:context_handler": _handler(
                "voice_transcript",
                "context_handler",
            ),
        },
        {
            "alpha": noop_host,
            "beta": context_host,
        },
    )

    result = await PluginDispatchService().trigger_arbitrated_custom_event(
        event_type="voice_transcript",
        args={"transcript": "Yui explain this"},
        timeout=0.25,
    )

    assert result["action"] == "prime_context"
    assert result["context"] == "screen context"
    assert result["priority"] == 5.0
    assert result["source_plugin"] == "beta"
    assert result["source_event_id"] == "context_handler"
