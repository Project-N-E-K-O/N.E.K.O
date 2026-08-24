from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugin.server.runs import trigger_service as module
from plugin.sdk.shared.core.events import EventMeta


class _Host:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object], float | None]] = []

    async def trigger(
        self,
        entry_id: str,
        args: dict[str, object],
        timeout: float | None,
    ) -> dict[str, object]:
        self.calls.append((entry_id, args, timeout))
        return {"ok": True}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_execute_trigger_treats_metadata_timeout_zero_as_no_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _Host()
    handler = SimpleNamespace(
        meta=EventMeta(
            event_type="plugin_entry",
            id="run",
            timeout=0,
            metadata={"timeout": 0},
        ),
    )
    monkeypatch.setattr(
        module.state,
        "get_event_handlers_snapshot_cached",
        lambda timeout=1.0: {"dummy_plugin.run": handler},
    )

    response = await module._execute_trigger(
        host=host,
        plugin_id="dummy_plugin",
        entry_id="run",
        args={},
        trace_id="trace-1",
    )

    assert response == {"ok": True}
    assert host.calls == [("run", {}, None)]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_execute_trigger_treats_ctx_timeout_zero_as_no_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _Host()
    handler = SimpleNamespace(
        meta=EventMeta(
            event_type="plugin_entry",
            id="run",
            timeout=15,
            metadata={"timeout": 15},
        ),
    )
    monkeypatch.setattr(
        module.state,
        "get_event_handlers_snapshot_cached",
        lambda timeout=1.0: {"dummy_plugin.run": handler},
    )

    response = await module._execute_trigger(
        host=host,
        plugin_id="dummy_plugin",
        entry_id="run",
        args={"_ctx": {"entry_timeout": 0}},
        trace_id="trace-2",
    )

    assert response == {"ok": True}
    assert host.calls == [("run", {"_ctx": {"entry_timeout": 0}}, None)]


@pytest.mark.plugin_unit
def test_redact_trigger_args_honors_write_only_schema_and_sensitive_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = SimpleNamespace(
        meta=EventMeta(
            event_type="plugin_entry",
            id="save_settings",
            input_schema={
                "type": "object",
                "properties": {
                    "custom_private_value": {"type": "string", "writeOnly": True},
                    "permission_mode": {"type": "string"},
                },
            },
        ),
    )
    monkeypatch.setattr(
        module.state,
        "get_event_handlers_snapshot_cached",
        lambda timeout=1.0: {"dummy_plugin.save_settings": handler},
    )

    redacted = module._redact_trigger_args(
        plugin_id="dummy_plugin",
        entry_id="save_settings",
        args={
            "custom_private_value": "private",
            "sessdata": "cookie-lowercase",
            "SESSDATA": "cookie-uppercase",
            "permission_mode": "open",
            "nested": {"access_token": "token", "visible": True},
        },
    )

    assert redacted == {
        "custom_private_value": "<redacted>",
        "sessdata": "<redacted>",
        "SESSDATA": "<redacted>",
        "permission_mode": "open",
        "nested": {"access_token": "<redacted>", "visible": True},
    }


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_trigger_plugin_records_redacted_args_but_executes_with_raw_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_args = {"SESSDATA": "cookie-secret", "permission_mode": "open"}
    captured_event: dict[str, object] = {}
    executed_args: dict[str, object] = {}

    monkeypatch.setattr(module, "_entry_write_only_arguments", lambda *_args: set())
    monkeypatch.setattr(
        module,
        "_enqueue_trigger_event",
        lambda event: captured_event.update(event),
    )
    monkeypatch.setattr(module, "_resolve_host", lambda *_args: (_Host(), None))

    async def execute_trigger(**kwargs: object) -> dict[str, object]:
        executed_args.update(kwargs["args"])
        return {"success": True}

    monkeypatch.setattr(module, "_execute_trigger", execute_trigger)

    result = await module.trigger_plugin(
        plugin_id="dummy_plugin",
        entry_id="save_settings",
        args=raw_args,
    )

    assert executed_args == raw_args
    assert captured_event["args"] == {
        "SESSDATA": "<redacted>",
        "permission_mode": "open",
    }
    assert result.args == captured_event["args"]
