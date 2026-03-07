from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from plugin.server.infrastructure.exceptions import register_exception_handlers
from plugin.server.routes.config import router as config_router


@pytest.fixture
def config_integration_app() -> FastAPI:
    app = FastAPI(title="plugin-config-integration-app")
    register_exception_handlers(app)
    app.include_router(config_router)
    return app


@pytest.fixture
async def config_integration_client(config_integration_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=config_integration_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


def _write_plugin_config(plugin_root: Path) -> None:
    (plugin_root / "demo").mkdir(parents=True, exist_ok=True)
    (plugin_root / "demo" / "plugin.toml").write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'demo'",
                "entry = 'plugin.main:Main'",
                "",
                "[plugin.config_profiles]",
                "active = 'dev'",
                "",
                "[plugin.config_profiles.files]",
                "dev = 'profiles/dev.toml'",
                "",
                "[runtime]",
                "enabled = true",
                "priority = 1",
            ]
        ),
        encoding="utf-8",
    )
    (plugin_root / "demo" / "profiles").mkdir(parents=True, exist_ok=True)
    (plugin_root / "demo" / "profiles" / "dev.toml").write_text(
        "[runtime]\npriority = 9\nmode = 'dev'\n",
        encoding="utf-8",
    )


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_config_http_full_flow_with_profile_ids_and_conflicts(
    config_integration_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from plugin.server.infrastructure import config_paths as config_paths_module

    root = tmp_path / "plugins"
    _write_plugin_config(root)
    monkeypatch.setattr(config_paths_module, "PLUGIN_CONFIG_ROOT", root)

    # 1) 基础读取：active=dev 生效
    resp_get = await config_integration_client.get("/plugin/demo/config")
    assert resp_get.status_code == 200
    config_1 = resp_get.json()["config"]
    assert config_1["runtime"]["priority"] == 9
    assert config_1["runtime"]["mode"] == "dev"

    # 2) 全量替换：不传 id/entry，服务端应回填受保护字段
    resp_replace = await config_integration_client.put(
        "/plugin/demo/config",
        json={"config": {"plugin": {"name": "DemoName"}, "runtime": {"enabled": False, "priority": 3}}},
    )
    assert resp_replace.status_code == 200
    replaced_cfg = resp_replace.json()["config"]
    assert replaced_cfg["plugin"]["id"] == "demo"
    assert replaced_cfg["plugin"]["entry"] == "plugin.main:Main"
    assert replaced_cfg["runtime"]["priority"] == 3

    # 3) 创建两个 profile id（1 / 01），验证 exact id 激活逻辑
    resp_upsert_1 = await config_integration_client.put(
        "/plugin/demo/config/profiles/1",
        json={"config": {"runtime": {"priority": 11}}, "make_active": False},
    )
    assert resp_upsert_1.status_code == 200

    resp_upsert_01 = await config_integration_client.put(
        "/plugin/demo/config/profiles/01",
        json={"config": {"runtime": {"priority": 22}}, "make_active": True},
    )
    assert resp_upsert_01.status_code == 200

    resp_profiles = await config_integration_client.get("/plugin/demo/config/profiles")
    assert resp_profiles.status_code == 200
    profiles_state = resp_profiles.json()["config_profiles"]
    assert profiles_state["active"] == "01"
    assert set(profiles_state["files"].keys()) >= {"1", "01"}

    resp_effective_01 = await config_integration_client.get("/plugin/demo/config")
    assert resp_effective_01.status_code == 200
    assert resp_effective_01.json()["config"]["runtime"]["priority"] == 22

    resp_activate_1 = await config_integration_client.post("/plugin/demo/config/profiles/1/activate")
    assert resp_activate_1.status_code == 200
    assert resp_activate_1.json()["config_profiles"]["active"] == "1"

    resp_effective_1 = await config_integration_client.get("/plugin/demo/config")
    assert resp_effective_1.status_code == 200
    assert resp_effective_1.json()["config"]["runtime"]["priority"] == 11

    # 4) 保护字段冲突：plugin.id / plugin.entry 变更应拒绝
    resp_conflict_id = await config_integration_client.put(
        "/plugin/demo/config",
        json={"config": {"plugin": {"id": "other"}}},
    )
    assert resp_conflict_id.status_code == 400
    assert "protected" in resp_conflict_id.json()["detail"].lower()

    resp_conflict_entry = await config_integration_client.put(
        "/plugin/demo/config",
        json={"config": {"plugin": {"entry": "x:y"}}},
    )
    assert resp_conflict_entry.status_code == 400
    assert "protected" in resp_conflict_entry.json()["detail"].lower()

    # 5) TOML parse/render/update 全流程
    resp_parse = await config_integration_client.post(
        "/plugin/demo/config/parse_toml",
        json={"toml": "[plugin]\nid='demo'\nentry='plugin.main:Main'\n[runtime]\npriority=33\n"},
    )
    assert resp_parse.status_code == 200
    assert resp_parse.json()["config"]["runtime"]["priority"] == 33

    resp_render = await config_integration_client.post(
        "/plugin/demo/config/render_toml",
        json={"config": {"plugin": {"id": "demo", "entry": "plugin.main:Main"}, "runtime": {"priority": 44}}},
    )
    assert resp_render.status_code == 200
    assert "priority = 44" in resp_render.json()["toml"]

    resp_update_toml = await config_integration_client.put(
        "/plugin/demo/config/toml",
        json={"toml": "[plugin]\nid='demo'\nentry='plugin.main:Main'\n[runtime]\npriority=55\n"},
    )
    assert resp_update_toml.status_code == 200

    resp_final = await config_integration_client.get("/plugin/demo/config")
    assert resp_final.status_code == 200
    assert resp_final.json()["config"]["runtime"]["priority"] == 11


@pytest.mark.plugin_integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"config": {"runtime": {"enabled": True}}},
        {"config": {"plugin": {"name": "ok", "author": {"email": "a@b.com"}}}},
        {"config": {"plugin": {"sdk": {"conflicts": True}}}},
        {"config": {"plugin": {"dependency": [{"id": "dep1", "providers": ["x"]}]}}},
    ],
)
async def test_config_http_boundary_legal_combinations(
    config_integration_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    from plugin.server.infrastructure import config_paths as config_paths_module

    root = tmp_path / "plugins"
    _write_plugin_config(root)
    monkeypatch.setattr(config_paths_module, "PLUGIN_CONFIG_ROOT", root)

    resp = await config_integration_client.put("/plugin/demo/config", json=payload)
    assert resp.status_code == 200
    assert resp.json()["success"] is True


@pytest.mark.plugin_integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload, expected_status",
    [
        ({"config": {"plugin": {"id": "other"}}}, 400),
        ({"config": {"plugin": {"entry": "x:y"}}}, 400),
        ({"config": {"plugin": {"author": {"email": "bad"}}}}, 400),
        ({"config": {"plugin": {"dependency": [{"providers": "bad"}]}}}, 400),
        ({"config": "bad"}, 422),
        ({}, 422),
    ],
)
async def test_config_http_boundary_illegal_combinations(
    config_integration_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: dict[str, object],
    expected_status: int,
) -> None:
    from plugin.server.infrastructure import config_paths as config_paths_module

    root = tmp_path / "plugins"
    _write_plugin_config(root)
    monkeypatch.setattr(config_paths_module, "PLUGIN_CONFIG_ROOT", root)

    resp = await config_integration_client.put("/plugin/demo/config", json=payload)
    assert resp.status_code == expected_status


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_config_http_profile_upsert_rejects_plugin_section(
    config_integration_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from plugin.server.infrastructure import config_paths as config_paths_module

    root = tmp_path / "plugins"
    _write_plugin_config(root)
    monkeypatch.setattr(config_paths_module, "PLUGIN_CONFIG_ROOT", root)

    resp = await config_integration_client.put(
        "/plugin/demo/config/profiles/dev",
        json={"config": {"plugin": {"id": "hijack"}}, "make_active": True},
    )
    assert resp.status_code == 400
