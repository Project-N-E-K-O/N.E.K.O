from __future__ import annotations

from pathlib import Path

import asyncio

import pytest

from plugin.core import context as context_module
from plugin.core.context import PluginContext


class _Logger:
    def warning(self, *args, **kwargs) -> None:
        self.last_warning = (args, kwargs)

    def debug(self, *args, **kwargs) -> None:
        self.last_debug = (args, kwargs)


class _Instance:
    def __init__(self) -> None:
        self.refreshed: list[dict[str, object]] = []

    def refresh_runtime_config(self, effective_config: dict[str, object]) -> None:
        self.refreshed.append(effective_config)


@pytest.mark.plugin_unit
def test_push_message_fast_mode_true_emits_runtime_deprecation_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(context_module, "zmq", None)
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )

    with pytest.warns(DeprecationWarning, match="fast_mode.*v0.9"):
        ctx.push_message(parts=[], fast_mode=True)


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_effective_config_refreshes_plugin_runtime_helpers(tmp_path: Path) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    instance = _Instance()
    ctx._instance = instance

    async def _local_payload(**kwargs: object) -> dict[str, object]:
        return {"config": {"plugin": {"store": {"enabled": True}}}}

    ctx._get_local_config_payload = _local_payload  # type: ignore[method-assign]

    payload = await ctx.get_own_effective_config()

    assert payload == {"config": {"plugin": {"store": {"enabled": True}}}}
    assert ctx._effective_config == {"plugin": {"store": {"enabled": True}}}
    assert instance.refreshed == [{"plugin": {"store": {"enabled": True}}}]


@pytest.mark.plugin_unit
def test_context_optimistic_merge_honors_delete_and_replace_markers() -> None:
    base = {
        "mcp_servers": {
            "fetch": {"url": "https://example.test"},
            "keep": {"url": "https://keep.test"},
        },
        "plugin": {"store": {"enabled": False, "kind": "old"}},
    }

    merged = PluginContext._merge_config_copy(
        base,
        {
            "mcp_servers": {"fetch": "__DELETE__"},
            "plugin": {"store": {"__replace__": True, "enabled": True}},
        },
    )

    assert merged == {
        "mcp_servers": {"keep": {"url": "https://keep.test"}},
        "plugin": {"store": {"enabled": True}},
    }
    assert base["mcp_servers"]["fetch"] == {"url": "https://example.test"}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_update_own_config_timeout_uses_host_merge_markers(tmp_path: Path) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    ctx._effective_config = {
        "mcp_servers": {
            "fetch": {"url": "https://example.test"},
            "keep": {"url": "https://keep.test"},
        }
    }

    async def _timeout(**kwargs: object) -> dict[str, object]:
        raise TimeoutError("slow persistence")

    ctx._send_request_and_wait_async = _timeout  # type: ignore[method-assign]

    payload = await ctx.update_own_config({"mcp_servers": {"fetch": "__DELETE__"}})

    assert payload["persisted"] is False
    assert payload["config"] == {"mcp_servers": {"keep": {"url": "https://keep.test"}}}
    assert ctx._effective_config == {"mcp_servers": {"keep": {"url": "https://keep.test"}}}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_update_own_config_rolls_back_rejected_updates(tmp_path: Path) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    instance = _Instance()
    ctx._instance = instance
    ctx._effective_config = {"plugin": {"store": {"enabled": False}}}

    async def _reject(**kwargs: object) -> dict[str, object]:
        raise RuntimeError("protected config key")

    ctx._send_request_and_wait_async = _reject  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="protected config key"):
        await ctx.update_own_config({"plugin": {"store": {"enabled": True}}})

    assert ctx._effective_config == {"plugin": {"store": {"enabled": False}}}
    assert instance.refreshed == [
        {"plugin": {"store": {"enabled": True}}},
        {"plugin": {"store": {"enabled": False}}},
    ]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_update_own_config_rolls_back_cancelled_updates(tmp_path: Path) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    instance = _Instance()
    ctx._instance = instance
    ctx._effective_config = {"plugin": {"store": {"enabled": False}}}

    async def _cancel(**kwargs: object) -> dict[str, object]:
        raise asyncio.CancelledError()

    ctx._send_request_and_wait_async = _cancel  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        await ctx.update_own_config({"plugin": {"store": {"enabled": True}}})

    assert ctx._effective_config == {"plugin": {"store": {"enabled": False}}}
    assert instance.refreshed == [
        {"plugin": {"store": {"enabled": True}}},
        {"plugin": {"store": {"enabled": False}}},
    ]
