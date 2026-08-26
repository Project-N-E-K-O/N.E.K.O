from __future__ import annotations

from pathlib import Path

import pytest

from plugin.core.context import PluginContext


class _Logger:
    def warning(self, *args, **kwargs) -> None:
        return None

    def debug(self, *args, **kwargs) -> None:
        return None


@pytest.mark.plugin_unit
def test_get_live_vision_sync_sends_the_frame_permission_token(
    tmp_path: Path,
) -> None:
    ctx = PluginContext(
        plugin_id="demo_plugin",
        config_path=tmp_path / "demo_plugin" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
        permission_generation="host-generation",
    )
    seen: list[dict[str, object]] = []

    def _send(**kwargs: object) -> dict[str, object]:
        seen.append(kwargs)
        return {"active": True, "frame_b64": "frame"}

    ctx._send_request_and_wait = _send  # type: ignore[method-assign]

    payload = ctx.get_live_vision_sync(
        include_frame=True,
        permission_token="generation-one",
        timeout=3.0,
    )

    assert payload["frame_b64"] == "frame"
    assert seen[0]["request_data"] == {
        "role": "",
        "include_frame": True,
        "host_generation": "host-generation",
        "token": "generation-one",
    }


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_get_live_vision_async_sends_the_host_generation(
    tmp_path: Path,
) -> None:
    ctx = PluginContext(
        plugin_id="demo_plugin",
        config_path=tmp_path / "demo_plugin" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
        permission_generation="host-generation",
    )
    seen: list[dict[str, object]] = []

    async def _send(**kwargs: object) -> dict[str, object]:
        seen.append(kwargs)
        return {"active": True, "frame_b64": "frame"}

    ctx._send_request_and_wait_async = _send  # type: ignore[method-assign]

    payload = await ctx.get_live_vision_async(
        include_frame=True,
        permission_token="generation-one",
        timeout=3.0,
    )

    assert payload["frame_b64"] == "frame"
    assert seen[0]["request_data"] == {
        "role": "",
        "include_frame": True,
        "host_generation": "host-generation",
        "token": "generation-one",
    }


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_set_live_frame_permission_async_sends_authenticated_plugin_request(
    tmp_path: Path,
) -> None:
    ctx = PluginContext(
        plugin_id="demo_plugin",
        config_path=tmp_path / "demo_plugin" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
        permission_generation="host-generation",
    )
    seen: list[dict[str, object]] = []

    async def _send(**kwargs: object) -> dict[str, object]:
        seen.append(kwargs)
        return {
            "ok": True,
            "source_name": "demo_plugin",
            "token": "generation-two",
            "enabled": False,
        }

    ctx._send_request_and_wait_async = _send  # type: ignore[method-assign]

    payload = await ctx.set_live_frame_permission_async(
        token="generation-two",
        enabled=False,
        timeout=3.0,
    )

    assert payload["ok"] is True
    assert seen == [{
        "method_name": "set_live_frame_permission",
        "request_type": "LIVE_FRAME_PERMISSION_SET",
        "request_data": {
            "host_generation": "host-generation",
            "token": "generation-two",
            "enabled": False,
        },
        "timeout": 3.0,
        "wrap_result": True,
        "error_log_template": None,
    }]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_set_live_frame_permission_async_rejects_non_boolean_enabled(
    tmp_path: Path,
) -> None:
    ctx = PluginContext(
        plugin_id="demo_plugin",
        config_path=tmp_path / "demo_plugin" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
        permission_generation="host-generation",
    )

    with pytest.raises(TypeError, match="enabled"):
        await ctx.set_live_frame_permission_async(
            token="generation-two",
            enabled="false",  # type: ignore[arg-type]
        )


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_set_plugin_delivery_permission_async_sends_authenticated_plugin_request(
    tmp_path: Path,
) -> None:
    ctx = PluginContext(
        plugin_id="demo_plugin",
        config_path=tmp_path / "demo_plugin" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
        permission_generation="host-generation",
    )
    seen: list[dict[str, object]] = []

    async def _send(**kwargs: object) -> dict[str, object]:
        seen.append(kwargs)
        return {
            "ok": True,
            "source_name": "demo_plugin",
            "token": "queued-generation",
            "enabled": False,
        }

    ctx._send_request_and_wait_async = _send  # type: ignore[method-assign]

    payload = await ctx.set_plugin_delivery_permission_async(
        token="queued-generation",
        enabled=False,
        timeout=3.0,
    )

    assert payload["ok"] is True
    assert seen == [{
        "method_name": "set_plugin_delivery_permission",
        "request_type": "PLUGIN_DELIVERY_PERMISSION_SET",
        "request_data": {
            "host_generation": "host-generation",
            "token": "queued-generation",
            "enabled": False,
        },
        "timeout": 3.0,
        "wrap_result": True,
        "error_log_template": None,
    }]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "request_type"),
    [
        ("set_live_frame_permission", "LIVE_FRAME_PERMISSION_SET"),
        ("set_plugin_delivery_permission", "PLUGIN_DELIVERY_PERMISSION_SET"),
    ],
)
async def test_public_permission_methods_forward_to_the_host_transport(
    tmp_path: Path,
    method_name: str,
    request_type: str,
) -> None:
    ctx = PluginContext(
        plugin_id="demo_plugin",
        config_path=tmp_path / "demo_plugin" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
        permission_generation="host-generation",
    )
    seen: list[dict[str, object]] = []

    async def _send(**kwargs: object) -> dict[str, object]:
        seen.append(kwargs)
        return {"ok": True, "applied": True}

    ctx._send_request_and_wait_async = _send  # type: ignore[method-assign]

    result = await getattr(ctx, method_name)(
        token="generation-one",
        enabled=True,
        timeout=4.0,
    )

    assert result == {"ok": True, "applied": True}
    assert seen == [{
        "method_name": method_name,
        "request_type": request_type,
        "request_data": {
            "host_generation": "host-generation",
            "token": "generation-one",
            "enabled": True,
        },
        "timeout": 4.0,
        "wrap_result": True,
        "error_log_template": None,
    }]
