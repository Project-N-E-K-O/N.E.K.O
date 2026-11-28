from __future__ import annotations

import httpx
import pytest

import plugin.plugins.proactive_controller as pc_mod
from plugin.plugins.proactive_controller import ProactiveControllerPlugin


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.infos: list[str] = []

    def warning(self, msg: str, *args: object) -> None:
        self.warnings.append(msg.format(*args) if args else msg)

    def info(self, msg: str, *args: object) -> None:
        self.infos.append(msg.format(*args) if args else msg)


class _Config:
    def __init__(self, cfg: dict | None = None) -> None:
        self._cfg = cfg or {}

    async def dump(self, timeout: float = 5.0) -> dict:
        del timeout
        return dict(self._cfg)


def _make_plugin(cfg: dict | None = None) -> ProactiveControllerPlugin:
    plugin = ProactiveControllerPlugin.__new__(ProactiveControllerPlugin)
    plugin.logger = _Logger()
    plugin.config = _Config(cfg)
    plugin._api_base = "http://127.0.0.1"
    plugin._api_port = 48911
    plugin._api_timeout = 1.0
    return plugin


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    ["http_500", "success_false", "malformed_json", "non_dict_settings", "timeout"],
)
async def test_startup_skips_default_mode_when_fetch_settings_fails(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    posted: list[tuple[str, dict]] = []

    class _FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def get(self, url: str):
            del url
            if scenario == "timeout":
                raise httpx.ReadTimeout("timed out")
            if scenario == "http_500":
                return httpx.Response(500, text="server error")
            if scenario == "success_false":
                return httpx.Response(
                    200,
                    json={"success": False, "error": "read error", "settings": {}},
                )
            if scenario == "malformed_json":
                return httpx.Response(200, text="not json")
            return httpx.Response(200, json={"success": True, "settings": ["bad"]})

        async def post(self, url: str, json: dict):
            posted.append((url, json))
            return httpx.Response(200, json={"success": True})

    monkeypatch.setattr(pc_mod.httpx, "AsyncClient", _FakeAsyncClient)
    plugin = _make_plugin({"proactive_controller": {"default_mode_on_first_run": "off"}})

    res = await plugin.startup()

    assert res.is_ok()
    assert posted == []


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_startup_applies_default_mode_on_confirmed_empty_first_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted: list[tuple[str, dict]] = []

    class _FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def get(self, url: str):
            del url
            return httpx.Response(200, json={"success": True, "settings": {}})

        async def post(self, url: str, json: dict):
            posted.append((url, json))
            return httpx.Response(200, json={"success": True})

    monkeypatch.setattr(pc_mod.httpx, "AsyncClient", _FakeAsyncClient)
    plugin = _make_plugin({"proactive_controller": {"default_mode_on_first_run": "off"}})

    res = await plugin.startup()

    assert res.is_ok()
    assert len(posted) == 1
    assert posted[0][0].endswith("/api/proactive/mode")
    assert posted[0][1] == {"mode": "off"}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_startup_preserves_existing_proactive_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted: list[tuple[str, dict]] = []

    class _FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def get(self, url: str):
            del url
            return httpx.Response(
                200,
                json={"success": True, "settings": {"proactiveChatEnabled": True}},
            )

        async def post(self, url: str, json: dict):
            posted.append((url, json))
            return httpx.Response(200, json={"success": True})

    monkeypatch.setattr(pc_mod.httpx, "AsyncClient", _FakeAsyncClient)
    plugin = _make_plugin({"proactive_controller": {"default_mode_on_first_run": "off"}})

    res = await plugin.startup()

    assert res.is_ok()
    assert posted == []
