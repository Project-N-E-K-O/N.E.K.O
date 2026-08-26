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

        async def get(
            self,
            url: str,
            *,
            params: dict[str, str],
            headers: dict[str, str],
        ):
            seen.update({"url": url, "params": params, "headers": headers})
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
    }
    assert seen["headers"] == {
        "X-NEKO-Live-Frame-Token": "generation-one",
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

        async def post(
            self,
            url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
        ):
            seen.update({"url": url, "json": json, "headers": headers})
            return _Response()

    monkeypatch.setattr(
        service_module.httpx,
        "AsyncClient",
        lambda **_kwargs: _Client(),
    )
    monkeypatch.setenv("NEKO_PLUGIN_HOST_API_TOKEN", "host-secret")

    result = await service_module.LiveVisionQueryService().revoke_plugin_permissions(
        source_name="demo_plugin"
    )

    assert result["ok"] is True
    assert seen["json"] == {"source_name": "demo_plugin"}
    assert seen["headers"] == {"X-NEKO-Plugin-Host-Token": "host-secret"}
    assert str(seen["url"]).endswith("/api/system/plugin-permissions/revoke")


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "path"),
    [
        (
            "set_live_frame_permission",
            "/api/system/live-vision/attachment-permission",
        ),
        (
            "set_plugin_delivery_permission",
            "/api/system/plugin-callbacks/delivery-permission",
        ),
    ],
)
async def test_permission_updates_forward_the_plugin_host_credential(
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    path: str,
) -> None:
    seen: dict[str, object] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "ok": True,
                "source_name": "demo_plugin",
                "token": "generation-one",
                "enabled": True,
                "applied": False,
            }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
        ):
            seen.update({"url": url, "json": json, "headers": headers})
            return _Response()

    monkeypatch.setattr(
        service_module.httpx,
        "AsyncClient",
        lambda **_kwargs: _Client(),
    )
    monkeypatch.setenv("NEKO_PLUGIN_HOST_API_TOKEN", "host-secret")

    method = getattr(service_module.LiveVisionQueryService(), method_name)
    result = await method(
        source_name="demo_plugin",
        token="generation-one",
        enabled=True,
    )

    assert result["ok"] is True
    assert result["applied"] is False
    assert str(seen["url"]).endswith(path)
    assert seen["headers"] == {"X-NEKO-Plugin-Host-Token": "host-secret"}
