from __future__ import annotations

import asyncio
import logging
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from plugin._types.exceptions import PluginExecutionError
from plugin.core.entry_executor import EntryExecutor, execute_entry_command
from plugin.sdk.shared.models import Err


def executor(entries, metadata=None):
    return EntryExecutor(
        plugin_id="test",
        context=SimpleNamespace(
            _handler_scope=lambda _: nullcontext(),
            _run_scope=lambda _: nullcontext(),
            _lanlan_scope=lambda _: nullcontext(),
        ),
        instance=SimpleNamespace(),
        entries=entries,
        metadata=metadata or {},
        events={},
        default_timeout=1,
        refresh=lambda: None,
        should_persist=lambda _: False,
        persist=None,
        logger=logging.getLogger(__name__),
    )


async def test_parameter_models_and_host_context_have_one_invocation_contract():
    class Params(BaseModel):
        query: str

    async def entry(params):
        return {"query": params.query}

    runtime = executor(
        {"entry": entry}, {"entry": SimpleNamespace(params=Params, model_validate=True)}
    )
    assert await runtime.invoke(
        "plugin_entry", "entry", {"query": "hello", "_ctx": {"run_id": "1"}}
    ) == {"query": "hello"}


async def test_entry_failure_does_not_prevent_later_invocation():
    async def bad():
        return Err(ValueError("bad input"))

    async def good():
        return "ok"

    runtime = executor({"bad": bad, "good": good})
    with pytest.raises(PluginExecutionError, match="bad input"):
        await runtime.invoke("plugin_entry", "bad", {})
    assert await runtime.invoke("plugin_entry", "good", {}) == "ok"


async def test_rpc_deadline_still_bounds_an_unlimited_entry():
    cancelled = asyncio.Event()

    async def hang():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    runtime = executor({"hang": hang}, {"hang": SimpleNamespace(timeout=0)})
    with pytest.raises(TimeoutError):
        await runtime.invoke(
            "plugin_entry",
            "hang",
            {},
            timeout=1,
            deadline=asyncio.get_running_loop().time() + 0.01,
        )
    assert cancelled.is_set()


async def test_failed_result_still_persists_state():
    saved = []

    async def entry():
        return Err(ValueError("rejected"))

    async def save(reason):
        saved.append(reason)

    runtime = executor({"entry": entry})
    runtime._should_persist = lambda method: True
    runtime._persist = save
    with pytest.raises(PluginExecutionError):
        await runtime.invoke("plugin_entry", "entry", {})
    assert saved == ["auto"]


async def test_validation_input_is_returned_but_not_logged(caplog):
    class Params(BaseModel):
        count: int

    async def entry(params):
        return params.count

    runtime = executor(
        {"entry": entry}, {"entry": SimpleNamespace(params=Params, model_validate=True)}
    )
    reply = await execute_entry_command(
        runtime,
        {
            "req_id": "1",
            "entry_id": "entry",
            "args": {"count": "PRIVATE_DIALOG_SENTINEL"},
        },
    )
    assert reply["success"] is False
    assert "PRIVATE_DIALOG_SENTINEL" in reply["error"]["message"]
    assert "PRIVATE_DIALOG_SENTINEL" not in caplog.text


async def test_entry_internal_timeout_is_distinct_from_budget_expiry():
    async def entry():
        raise TimeoutError("database timeout")

    reply = await execute_entry_command(
        executor({"entry": entry}),
        {"req_id": "1", "entry_id": "entry", "args": {}, "timeout": 10},
    )
    assert reply["error"]["code"] == "OPERATION_TIMEOUT"
    assert reply["error"]["message"] == "database timeout"


async def test_custom_trigger_reaches_only_registered_handlers():
    async def _load_config():
        return {"token": "secret"}

    async def registered():
        return "ok"

    runtime = executor({})
    runtime._instance = SimpleNamespace(_load_config=_load_config)
    runtime._events = {"plugin_entry": {"registered": registered}}
    blocked = await execute_entry_command(
        runtime,
        {
            "type": "TRIGGER_CUSTOM",
            "req_id": "1",
            "event_type": "plugin_entry",
            "event_id": "_load_config",
            "args": {},
        },
    )
    assert blocked["success"] is False
    assert "secret" not in str(blocked)
    allowed = await execute_entry_command(
        runtime,
        {
            "type": "TRIGGER_CUSTOM",
            "req_id": "2",
            "event_type": "plugin_entry",
            "event_id": "registered",
            "args": {},
        },
    )
    assert allowed["data"] == "ok"


async def test_expired_before_execution_is_a_deadline_fault():
    async def entry():
        pytest.fail("Expired request must not execute")

    reply = await execute_entry_command(
        executor({"entry": entry}),
        {
            "req_id": "1",
            "entry_id": "entry",
            "args": {},
            "_request_deadline_monotonic": asyncio.get_running_loop().time() - 1,
        },
    )
    assert reply["error"]["code"] == "DEADLINE_EXCEEDED"
