from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from plugin.core.communication import PluginCommunicationResourceManager
from plugin.core.host import _resolve_cancel_run_id, _resolve_trigger_task_key

pytestmark = pytest.mark.plugin_unit


@pytest.mark.parametrize(
    "bad_run_id",
    [[1], {"a": 1}, 123, True, False, None, "", "   ", "\t\n"],
)
def test_resolve_trigger_task_key_falls_back_on_invalid_run_id(bad_run_id: Any) -> None:
    msg = {
        "type": "TRIGGER",
        "req_id": "req-42",
        "args": {"_ctx": {"run_id": bad_run_id}},
    }
    key = _resolve_trigger_task_key(msg)
    assert key == "trigger:req-42"
    assert isinstance(hash(key), int)


@pytest.mark.parametrize(
    "bad_run_id",
    [[1], {"a": 1}, 123, True, False, None, "", "   ", "\t\n"],
)
def test_resolve_cancel_run_id_rejects_invalid_values(bad_run_id: Any) -> None:
    assert _resolve_cancel_run_id({"type": "CANCEL_RUN", "run_id": bad_run_id}) is None


def test_resolve_trigger_and_cancel_preserve_valid_string_run_id() -> None:
    trigger_msg = {
        "type": "TRIGGER",
        "req_id": "req-99",
        "args": {"_ctx": {"run_id": "  run-abc-123  "}},
    }
    cancel_msg = {"type": "CANCEL_RUN", "run_id": "  run-abc-123  "}
    assert _resolve_trigger_task_key(trigger_msg) == "run-abc-123"
    assert _resolve_cancel_run_id(cancel_msg) == "run-abc-123"


def test_resolve_trigger_task_key_handles_non_dict_args_and_ctx() -> None:
    assert _resolve_trigger_task_key({"req_id": "r1", "args": None}) == "trigger:r1"
    assert _resolve_trigger_task_key({"req_id": "r2", "args": {"_ctx": ["bad"]}}) == "trigger:r2"


@pytest.mark.asyncio
async def test_cancel_rpc_request_ignores_invalid_run_id() -> None:
    mgr = PluginCommunicationResourceManager.__new__(PluginCommunicationResourceManager)
    send_mock = AsyncMock()
    mgr.transport = type("T", (), {"send_command": send_mock})()

    await mgr._cancel_rpc_request(
        {
            "type": "TRIGGER",
            "request_id": "req-7",
            "args": {"_ctx": {"run_id": [1, 2]}},
        }
    )
    send_mock.assert_awaited_once_with({"type": "CANCEL_RUN", "run_id": "trigger:req-7"})
