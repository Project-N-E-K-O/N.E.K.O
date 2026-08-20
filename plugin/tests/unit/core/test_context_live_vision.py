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
@pytest.mark.asyncio
async def test_set_live_frame_permission_async_sends_authenticated_plugin_request(
    tmp_path: Path,
) -> None:
    ctx = PluginContext(
        plugin_id="neko_wows",
        config_path=tmp_path / "neko_wows" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    seen: list[dict[str, object]] = []

    async def _send(**kwargs: object) -> dict[str, object]:
        seen.append(kwargs)
        return {
            "ok": True,
            "source_name": "neko_wows",
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
        plugin_id="neko_wows",
        config_path=tmp_path / "neko_wows" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )

    with pytest.raises(TypeError, match="enabled"):
        await ctx.set_live_frame_permission_async(
            token="generation-two",
            enabled="false",  # type: ignore[arg-type]
        )
