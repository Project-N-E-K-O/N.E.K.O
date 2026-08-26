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
        host_generation="host-generation",
        token="generation-one",
        include_frame=True,
    )

    assert result["frame_b64"] == "frame"
    assert seen["params"] == {
        "include_frame": "true",
        "source_name": "demo_plugin",
        "host_generation": "host-generation",
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
        source_name="demo_plugin",
        host_generation="host-generation",
    )

    assert result["ok"] is True
    assert seen["json"] == {
        "source_name": "demo_plugin",
        "host_generation": "host-generation",
    }
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
        host_generation="host-generation",
        token="generation-one",
        enabled=True,
    )

    assert result["ok"] is True
    assert result["applied"] is False
    assert str(seen["url"]).endswith(path)
    assert seen["headers"] == {"X-NEKO-Plugin-Host-Token": "host-secret"}
    assert seen["json"] == {
        "source_name": "demo_plugin",
        "host_generation": "host-generation",
        "token": "generation-one",
        "enabled": True,
    }


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
async def test_active_permission_is_replayed_after_main_server_restart(
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    path: str,
) -> None:
    posts: list[tuple[str, dict[str, object]]] = []

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "ok": True,
                "source_name": "demo_plugin",
                "token": "generation-one",
                "enabled": True,
                "applied": True,
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
            assert headers == {"X-NEKO-Plugin-Host-Token": "host-secret"}
            posts.append((url, dict(json)))
            return _Response()

    monkeypatch.setattr(
        service_module.httpx,
        "AsyncClient",
        lambda **_kwargs: _Client(),
    )
    monkeypatch.setenv("NEKO_PLUGIN_HOST_API_TOKEN", "host-secret")
    service = service_module.LiveVisionQueryService()

    method = getattr(service, method_name)
    await method(
        source_name="demo_plugin",
        host_generation="host-generation",
        token="generation-one",
        enabled=True,
    )
    posts.clear()

    restored = await service.rehydrate_active_permissions()

    assert restored == 1
    assert len(posts) == 1
    assert posts[0][0].endswith(path)
    assert posts[0][1] == {
        "source_name": "demo_plugin",
        "host_generation": "host-generation",
        "token": "generation-one",
        "enabled": True,
    }


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_failed_revoke_tombstone_is_replayed_after_main_server_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posts: list[dict[str, object]] = []
    fail_revoke = True

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
            _url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
        ):
            nonlocal fail_revoke
            assert headers == {"X-NEKO-Plugin-Host-Token": "host-secret"}
            posts.append(dict(json))
            if fail_revoke:
                raise service_module.httpx.ConnectError("main server unavailable")
            return _Response()

    monkeypatch.setattr(
        service_module.httpx,
        "AsyncClient",
        lambda **_kwargs: _Client(),
    )
    monkeypatch.setenv("NEKO_PLUGIN_HOST_API_TOKEN", "host-secret")
    service = service_module.LiveVisionQueryService()
    service._active_permissions[("plugin delivery", "demo_plugin")] = {
        "kind": "plugin delivery",
        "path": "/api/system/plugin-callbacks/delivery-permission",
        "source_name": "demo_plugin",
        "host_generation": "stopped-generation",
        "token": "delivery-generation",
        "enabled": True,
    }

    with pytest.raises(RuntimeError, match="permission revoke unavailable"):
        await service.revoke_plugin_permissions(
            source_name="demo_plugin",
            host_generation="stopped-generation",
        )

    fail_revoke = False
    posts.clear()
    restored = await service.rehydrate_active_permissions()

    assert restored == 0
    assert posts == [
        {
            "source_name": "demo_plugin",
            "host_generation": "stopped-generation",
        }
    ]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_inactive_host_permissions_are_revoked_and_not_rehydrated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posts: list[tuple[str, dict[str, object]]] = []

    class _Response:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self._payload

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
            assert headers == {"X-NEKO-Plugin-Host-Token": "host-secret"}
            posts.append((url, dict(json)))
            if url.endswith("/api/system/plugin-permissions/revoke"):
                return _Response({"ok": True, "source_name": "dead_plugin"})
            return _Response(
                {
                    "ok": True,
                    "source_name": str(json.get("source_name") or ""),
                    "token": str(json.get("token") or ""),
                    "enabled": bool(json.get("enabled")),
                    "applied": True,
                }
            )

    monkeypatch.setattr(
        service_module.httpx,
        "AsyncClient",
        lambda **_kwargs: _Client(),
    )
    monkeypatch.setenv("NEKO_PLUGIN_HOST_API_TOKEN", "host-secret")
    service = service_module.LiveVisionQueryService()
    service._active_permissions[("live frame", "dead_plugin")] = {
        "kind": "live frame",
        "path": "/api/system/live-vision/attachment-permission",
        "source_name": "dead_plugin",
        "host_generation": "dead-generation",
        "token": "frame-generation",
        "enabled": True,
    }
    service._active_permissions[("plugin delivery", "live_plugin")] = {
        "kind": "plugin delivery",
        "path": "/api/system/plugin-callbacks/delivery-permission",
        "source_name": "live_plugin",
        "host_generation": "live-generation",
        "token": "delivery-generation",
        "enabled": True,
    }

    revoked = await service.revoke_inactive_permissions(
        {"live_plugin": "live-generation"}
    )
    posts.clear()
    restored = await service.rehydrate_active_permissions()

    assert revoked == 1
    assert restored == 1
    assert len(posts) == 2
    assert posts[0][0].endswith("/api/system/plugin-permissions/revoke")
    assert posts[0][1] == {
        "source_name": "dead_plugin",
        "host_generation": "dead-generation",
    }
    assert posts[1][0].endswith(
        "/api/system/plugin-callbacks/delivery-permission"
    )
    assert posts[1][1]["source_name"] == "live_plugin"
