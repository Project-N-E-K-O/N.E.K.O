from __future__ import annotations

import pytest

from plugin.server.application.messages import live_vision_service as service_module


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_frame_query_forwards_plugin_identity_and_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "ok": True,
                "active": True,
                "source": "screen",
                "frame_b64": "frame",
            }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, url: str, *, params: dict[str, str]):
            seen.update({"url": url, "params": params})
            return _Response()

    monkeypatch.setattr(
        service_module.httpx,
        "AsyncClient",
        lambda **_kwargs: _Client(),
    )

    result = await service_module.LiveVisionQueryService().get_live_vision(
        source_name="demo_plugin",
        token="generation-one",
        include_frame=True,
    )

    assert result["frame_b64"] == "frame"
    assert seen["params"] == {
        "include_frame": "true",
        "source_name": "demo_plugin",
        "token": "generation-one",
    }


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_source_revoke_forwards_the_plugin_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"ok": True, "source_name": "demo_plugin"}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, *, json: dict[str, object]):
            seen.update({"url": url, "json": json})
            return _Response()

    monkeypatch.setattr(
        service_module.httpx,
        "AsyncClient",
        lambda **_kwargs: _Client(),
    )

    result = await service_module.LiveVisionQueryService().revoke_plugin_permissions(
        source_name="demo_plugin"
    )

    assert result["ok"] is True
    assert seen["json"] == {"source_name": "demo_plugin"}
    assert str(seen["url"]).endswith("/api/system/plugin-permissions/revoke")
