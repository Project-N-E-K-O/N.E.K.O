from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.sdk.memory import MemoryClient
from plugin.sdk.state import _deserialize_extended_type, _serialize_extended_type
from plugin.sdk.system_info import SystemInfo


class _Color(Enum):
    RED = "red"


@pytest.mark.plugin_unit
def test_memory_client_query_and_get(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = SimpleNamespace(
        query_memory=lambda lanlan_name, query, timeout: {"ok": True, "q": query},
    )
    client = MemoryClient(ctx=ctx)

    result = client.query("lan", "hello")
    assert result["ok"] is True

    monkeypatch.setattr(client, "_bus", lambda: SimpleNamespace(get=lambda **kwargs: {"items": []}))
    assert client.get("bucket") == {"items": []}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_system_info_async_methods_and_env() -> None:
    async def _get_system_config(timeout: float = 5.0):
        return {"data": {"config": {"A": 1}}}

    system_info = SystemInfo(ctx=SimpleNamespace(get_system_config=_get_system_config))
    payload = await system_info.get_system_config()
    assert "data" in payload

    settings = await system_info.get_server_settings()
    assert settings == {"A": 1}

    env = system_info.get_python_env()
    assert "python" in env
    assert "os" in env


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 1, 1, 0, 0, 0),
        date(2026, 1, 1),
        timedelta(seconds=10),
        _Color.RED,
        {"a", "b"},
        frozenset({"a", "b"}),
        Path("/tmp/a"),
    ],
)
def test_state_extended_type_roundtrip(value: object) -> None:
    encoded = _serialize_extended_type(value)
    assert isinstance(encoded, dict)

    decoded = _deserialize_extended_type(encoded)
    if isinstance(value, set):
        assert set(decoded) == value
    elif isinstance(value, frozenset):
        assert frozenset(decoded) == value
    elif isinstance(value, Enum):
        assert decoded == value
    else:
        assert decoded == value
