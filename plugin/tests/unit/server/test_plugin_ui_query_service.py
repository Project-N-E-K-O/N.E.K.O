from __future__ import annotations

import asyncio

import pytest

from plugin.core.state import state
from plugin.core.ui_manifest import normalize_plugin_ui_manifest
from plugin._types.exceptions import PluginExecutionError
from plugin.server.application import config as config_application
from plugin.server.domain.errors import ServerDomainError
from plugin.server.application.plugins.ui_query_service import (
    _build_plugin_list_actions_from_meta,
    _get_static_ui_config_from_meta,
    _hosted_plugin_not_running_message,
    _PLUGIN_NOT_RUNNING_MESSAGES,
    PluginUiQueryService,
)


def test_static_ui_config_infers_from_config_path_when_missing(tmp_path) -> None:
    root = tmp_path
    plugin_dir = root / "demo_plugin"
    static_dir = plugin_dir / "static"
    static_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")

    config = _get_static_ui_config_from_meta({
        "id": "demo",
        "config_path": str(plugin_dir / "plugin.toml"),
    })

    assert config is not None
    assert config["enabled"] is True
    assert config["directory"] == str(static_dir)
    assert config["inferred"] is True


def test_build_plugin_list_actions_infers_open_ui_and_normalizes_custom_actions(tmp_path) -> None:
    plugin_dir = tmp_path / "demo_plugin"
    static_dir = plugin_dir / "static"
    static_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")

    actions = _build_plugin_list_actions_from_meta(
        "demo",
        {
            "id": "demo",
            "config_path": str(plugin_dir / "plugin.toml"),
            "list_actions": [
                {"id": "docs", "kind": "url", "label": "Docs", "target": "https://example.com/{plugin_id}"},
                {"id": "delete", "kind": "builtin", "confirm_mode": "hold", "danger": True},
                {"id": "broken"},
                "invalid",
            ],
        },
    )

    assert actions == [
        {
            "id": "docs",
            "kind": "url",
            "label": "Docs",
            "target": "https://example.com/demo",
        },
        {
            "id": "delete",
            "kind": "builtin",
            "confirm_mode": "hold",
            "danger": True,
        },
        {
            "id": "open_panel",
            "kind": "route",
            "target": "/plugins/demo?tab=panel",
        },
    ]


def test_surface_context_includes_config_snapshot_only_with_permission(monkeypatch, tmp_path) -> None:
    plugin_dir = tmp_path / "demo_plugin"
    plugin_dir.mkdir()
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    plugin_ui = normalize_plugin_ui_manifest(
        {
            "plugin": {
                "ui": {
                    "panel": [{
                        "id": "main",
                        "entry": "ui/panel.tsx",
                        "permissions": ["state:read", "config:read"],
                    }],
                },
            },
        },
        plugin_id="demo",
    )

    class _ConfigService:
        async def get_plugin_config(self, *, plugin_id: str) -> dict[str, object]:
            assert plugin_id == "demo"
            return {
                "plugin_id": "demo",
                "config": {"plugin": {"id": "demo"}, "feature": {"enabled": True}},
                "last_modified": "2026-01-01T00:00:00",
                "profiles_state": {"config_profiles": {"active": None}},
            }

    monkeypatch.setattr(config_application, "ConfigQueryService", _ConfigService)
    with state.acquire_plugins_write_lock():
        previous = state.plugins.get("demo")
        state.plugins["demo"] = {
            "id": "demo",
            "config_path": str(config_path),
            "plugin_ui": plugin_ui,
            "entries": [],
        }
    try:
        context = asyncio.run(PluginUiQueryService().get_surface_context("demo", kind="panel", surface_id="main"))
    finally:
        with state.acquire_plugins_write_lock():
            if previous is None:
                state.plugins.pop("demo", None)
            else:
                state.plugins["demo"] = previous

    assert context["config"]["value"]["feature"] == {"enabled": True}
    assert context["config"]["readonly"] is True
    assert context["actions"] == []


def test_surface_source_includes_same_plugin_relative_dependencies(tmp_path) -> None:
    plugin_dir = tmp_path / "demo_plugin"
    ui_dir = plugin_dir / "ui"
    nested_dir = ui_dir / "nested"
    nested_dir.mkdir(parents=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    (ui_dir / "panel.tsx").write_text(
        "import { label, suffix } from './barrel'\n"
        "export default function Panel() { return <strong>{label} {suffix}</strong> }\n",
        encoding="utf-8",
    )
    (ui_dir / "barrel.ts").write_text(
        "export { label } from './shared'\n"
        "export * from './nested/suffix'\n",
        encoding="utf-8",
    )
    (ui_dir / "shared.ts").write_text(
        "import { suffix } from './nested/suffix'\n"
        "export const label = `shared ${suffix}`\n",
        encoding="utf-8",
    )
    (nested_dir / "suffix.ts").write_text(
        "export const suffix = 'wrong extension'\n",
        encoding="utf-8",
    )
    (nested_dir / "suffix.tsx").write_text("export const suffix = 'ok'\n", encoding="utf-8")
    plugin_ui = normalize_plugin_ui_manifest(
        {
            "plugin": {
                "ui": {
                    "panel": [{
                        "id": "main",
                        "entry": "ui/panel.tsx",
                        "permissions": ["state:read"],
                    }],
                },
            },
        },
        plugin_id="demo",
    )

    plugins_backup = dict(state.plugins)
    try:
        with state.acquire_plugins_write_lock():
            state.plugins.clear()
            state.plugins["demo"] = {
                "id": "demo",
                "config_path": str(config_path),
                "plugin_ui": plugin_ui,
                "entries": [],
            }

        payload = asyncio.run(PluginUiQueryService().get_surface_source("demo", kind="panel", surface_id="main"))
    finally:
        with state.acquire_plugins_write_lock():
            state.plugins.clear()
            state.plugins.update(plugins_backup)

    assert payload["source"].startswith("import { label, suffix }")
    assert payload["dependencies"] == [
        {"path": "ui/nested/suffix.tsx", "source": "export const suffix = 'ok'\n"},
        {
            "path": "ui/shared.ts",
            "source": "import { suffix } from './nested/suffix'\nexport const label = `shared ${suffix}`\n",
        },
        {
            "path": "ui/barrel.ts",
            "source": "export { label } from './shared'\nexport * from './nested/suffix'\n",
        },
    ]


def test_surface_source_rejects_unresolved_relative_dependencies(tmp_path) -> None:
    plugin_dir = tmp_path / "demo_plugin"
    ui_dir = plugin_dir / "ui"
    ui_dir.mkdir(parents=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    (ui_dir / "panel.tsx").write_text(
        "import { label } from './missing'\n"
        "export default function Panel() { return <strong>{label}</strong> }\n",
        encoding="utf-8",
    )
    plugin_ui = normalize_plugin_ui_manifest(
        {
            "plugin": {
                "ui": {
                    "panel": [{
                        "id": "main",
                        "entry": "ui/panel.tsx",
                        "permissions": ["state:read"],
                    }],
                },
            },
        },
        plugin_id="demo",
    )

    plugins_backup = dict(state.plugins)
    try:
        with state.acquire_plugins_write_lock():
            state.plugins.clear()
            state.plugins["demo"] = {
                "id": "demo",
                "config_path": str(config_path),
                "plugin_ui": plugin_ui,
                "entries": [],
            }

        with pytest.raises(ServerDomainError) as exc_info:
            asyncio.run(PluginUiQueryService().get_surface_source("demo", kind="panel", surface_id="main"))
    finally:
        with state.acquire_plugins_write_lock():
            state.plugins.clear()
            state.plugins.update(plugins_backup)

    assert exc_info.value.code == "PLUGIN_UI_DEPENDENCY_NOT_FOUND"
    assert exc_info.value.status_code == 404
    assert exc_info.value.details == {
        "specifier": "./missing",
        "importer": "ui/panel.tsx",
    }


def test_surface_source_ignores_commented_and_string_imports(tmp_path) -> None:
    plugin_dir = tmp_path / "demo_plugin"
    ui_dir = plugin_dir / "ui"
    ui_dir.mkdir(parents=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    (ui_dir / "panel.tsx").write_text(
        "/* import { fake } from './missing-block' */\n"
        "// export { fake } from './missing-line'\n"
        "const sample = `import { fake } from './missing-template'`\n"
        r"const re = /import { fake } from '.\/missing-regex'/" "\n"
        "import { label } from './shared'\n"
        "export default function Panel() { return <strong>{label + sample + re.source}</strong> }\n",
        encoding="utf-8",
    )
    (ui_dir / "shared.ts").write_text("export const label = 'ok'\n", encoding="utf-8")
    plugin_ui = normalize_plugin_ui_manifest(
        {
            "plugin": {
                "ui": {
                    "panel": [{
                        "id": "main",
                        "entry": "ui/panel.tsx",
                        "permissions": ["state:read"],
                    }],
                },
            },
        },
        plugin_id="demo",
    )

    plugins_backup = dict(state.plugins)
    try:
        with state.acquire_plugins_write_lock():
            state.plugins.clear()
            state.plugins["demo"] = {
                "id": "demo",
                "config_path": str(config_path),
                "plugin_ui": plugin_ui,
                "entries": [],
            }

        payload = asyncio.run(PluginUiQueryService().get_surface_source("demo", kind="panel", surface_id="main"))
    finally:
        with state.acquire_plugins_write_lock():
            state.plugins.clear()
            state.plugins.update(plugins_backup)

    assert payload["dependencies"] == [
        {"path": "ui/shared.ts", "source": "export const label = 'ok'\n"},
    ]


def test_surface_source_ignores_jsx_text_imports(tmp_path) -> None:
    plugin_dir = tmp_path / "demo_plugin"
    ui_dir = plugin_dir / "ui"
    ui_dir.mkdir(parents=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    (ui_dir / "panel.tsx").write_text(
        "const sample = <pre>\n"
        "import ghost from './missing-top-level-jsx'\n"
        "</pre>\n"
        "export default function Panel() {\n"
        "  return <pre>\n"
        "import ghost from './missing-jsx-text'\n"
        "</pre>\n"
        "}\n",
        encoding="utf-8",
    )
    plugin_ui = normalize_plugin_ui_manifest(
        {
            "plugin": {
                "ui": {
                    "panel": [{
                        "id": "main",
                        "entry": "ui/panel.tsx",
                        "permissions": ["state:read"],
                    }],
                },
            },
        },
        plugin_id="demo",
    )

    plugins_backup = dict(state.plugins)
    try:
        with state.acquire_plugins_write_lock():
            state.plugins.clear()
            state.plugins["demo"] = {
                "id": "demo",
                "config_path": str(config_path),
                "plugin_ui": plugin_ui,
                "entries": [],
            }

        payload = asyncio.run(PluginUiQueryService().get_surface_source("demo", kind="panel", surface_id="main"))
    finally:
        with state.acquire_plugins_write_lock():
            state.plugins.clear()
            state.plugins.update(plugins_backup)

    assert payload["dependencies"] == []


def test_surface_source_allows_comments_after_from_keyword(tmp_path) -> None:
    plugin_dir = tmp_path / "demo_plugin"
    ui_dir = plugin_dir / "ui"
    ui_dir.mkdir(parents=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    (ui_dir / "panel.tsx").write_text(
        "import { label } from /* dependency hint */ './shared'\n"
        "export { suffix } from /* re-export hint */ './suffix'\n"
        "export default function Panel() { return <strong>{label}</strong> }\n",
        encoding="utf-8",
    )
    (ui_dir / "shared.ts").write_text("export const label = 'ok'\n", encoding="utf-8")
    (ui_dir / "suffix.ts").write_text("export const suffix = '!'\n", encoding="utf-8")
    plugin_ui = normalize_plugin_ui_manifest(
        {
            "plugin": {
                "ui": {
                    "panel": [{
                        "id": "main",
                        "entry": "ui/panel.tsx",
                        "permissions": ["state:read"],
                    }],
                },
            },
        },
        plugin_id="demo",
    )

    plugins_backup = dict(state.plugins)
    try:
        with state.acquire_plugins_write_lock():
            state.plugins.clear()
            state.plugins["demo"] = {
                "id": "demo",
                "config_path": str(config_path),
                "plugin_ui": plugin_ui,
                "entries": [],
            }

        payload = asyncio.run(PluginUiQueryService().get_surface_source("demo", kind="panel", surface_id="main"))
    finally:
        with state.acquire_plugins_write_lock():
            state.plugins.clear()
            state.plugins.update(plugins_backup)

    assert payload["dependencies"] == [
        {"path": "ui/shared.ts", "source": "export const label = 'ok'\n"},
        {"path": "ui/suffix.ts", "source": "export const suffix = '!'\n"},
    ]


@pytest.mark.parametrize(
    ("sources", "expected_cycle"),
    [
        (
            {
                "panel.tsx": "import { value } from './a'\nexport default function Panel() { return value }\n",
                "a.ts": "import Panel from './panel'\nexport const value = String(Panel)\n",
            },
            ["ui/panel.tsx", "ui/a.ts", "ui/panel.tsx"],
        ),
        (
            {
                "panel.tsx": "import { value } from './a'\nexport default function Panel() { return value }\n",
                "a.ts": "import { value as next } from './b'\nexport const value = next\n",
                "b.ts": "import { value } from './a'\nexport const value = String(value)\n",
            },
            ["ui/a.ts", "ui/b.ts", "ui/a.ts"],
        ),
    ],
)
def test_surface_source_rejects_circular_relative_dependencies(tmp_path, sources, expected_cycle) -> None:
    plugin_dir = tmp_path / "demo_plugin"
    ui_dir = plugin_dir / "ui"
    ui_dir.mkdir(parents=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    for filename, source in sources.items():
        (ui_dir / filename).write_text(source, encoding="utf-8")
    plugin_ui = normalize_plugin_ui_manifest(
        {
            "plugin": {
                "ui": {
                    "panel": [{
                        "id": "main",
                        "entry": "ui/panel.tsx",
                        "permissions": ["state:read"],
                    }],
                },
            },
        },
        plugin_id="demo",
    )

    plugins_backup = dict(state.plugins)
    try:
        with state.acquire_plugins_write_lock():
            state.plugins.clear()
            state.plugins["demo"] = {
                "id": "demo",
                "config_path": str(config_path),
                "plugin_ui": plugin_ui,
                "entries": [],
            }

        with pytest.raises(ServerDomainError) as exc_info:
            asyncio.run(PluginUiQueryService().get_surface_source("demo", kind="panel", surface_id="main"))
    finally:
        with state.acquire_plugins_write_lock():
            state.plugins.clear()
            state.plugins.update(plugins_backup)

    assert exc_info.value.code == "PLUGIN_UI_DEPENDENCY_CYCLE"
    assert exc_info.value.status_code == 400
    assert exc_info.value.details == {"cycle": expected_cycle}


def test_call_surface_action_preserves_plugin_entry_error(monkeypatch, tmp_path) -> None:
    plugin_dir = tmp_path / "demo_plugin"
    plugin_dir.mkdir()
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    plugin_ui = normalize_plugin_ui_manifest(
        {
            "plugin": {
                "ui": {
                    "panel": [{
                        "id": "main",
                        "entry": "ui/panel.tsx",
                        "permissions": ["action:call"],
                    }],
                },
            },
        },
        plugin_id="demo",
    )

    class _Host:
        def is_alive(self) -> bool:
            return True

        async def get_ui_context(self, context_id: str) -> dict[str, object]:
            assert context_id == "main"
            return {
                "actions": [
                    {"id": "add_server", "entry_id": "add_server"},
                ],
            }

        async def trigger(self, entry_id: str, args: dict[str, object]) -> object:
            assert entry_id == "add_server"
            raise PluginExecutionError("demo", entry_id, "Failed to save server config: access denied")

    plugins_backup = dict(state.plugins)
    hosts_backup = dict(state.plugin_hosts)
    try:
        with state.acquire_plugins_write_lock():
            state.plugins.clear()
            state.plugins["demo"] = {
                "id": "demo",
                "config_path": str(config_path),
                "plugin_ui": plugin_ui,
                "entries": [{"id": "add_server", "name": "Add Server"}],
            }
        with state.acquire_plugin_hosts_write_lock():
            state.plugin_hosts.clear()
            state.plugin_hosts["demo"] = _Host()

        with pytest.raises(ServerDomainError) as exc_info:
            asyncio.run(
                PluginUiQueryService().call_surface_action(
                    "demo",
                    action_id="add_server",
                    args={},
                    kind="panel",
                    surface_id="main",
                )
            )
    finally:
        with state.acquire_plugins_write_lock():
            state.plugins.clear()
            state.plugins.update(plugins_backup)
        with state.acquire_plugin_hosts_write_lock():
            state.plugin_hosts.clear()
            state.plugin_hosts.update(hosts_backup)

    assert exc_info.value.code == "PLUGIN_UI_ACTION_FAILED"
    assert exc_info.value.message == "Failed to save server config: access denied"


def test_call_surface_action_localizes_plugin_not_running(tmp_path) -> None:
    plugin_dir = tmp_path / "demo_plugin"
    plugin_dir.mkdir()
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    plugin_ui = normalize_plugin_ui_manifest(
        {
            "plugin": {
                "ui": {
                    "panel": [{
                        "id": "main",
                        "entry": "ui/panel.tsx",
                        "permissions": ["action:call"],
                    }],
                },
            },
        },
        plugin_id="demo",
    )

    plugins_backup = dict(state.plugins)
    hosts_backup = dict(state.plugin_hosts)
    try:
        with state.acquire_plugins_write_lock():
            state.plugins.clear()
            state.plugins["demo"] = {
                "id": "demo",
                "config_path": str(config_path),
                "plugin_ui": plugin_ui,
                "entries": [{"id": "add_server", "name": "Add Server"}],
            }
        with state.acquire_plugin_hosts_write_lock():
            state.plugin_hosts.clear()

        with pytest.raises(ServerDomainError) as exc_info:
            asyncio.run(
                PluginUiQueryService().call_surface_action(
                    "demo",
                    action_id="add_server",
                    args={},
                    kind="panel",
                    surface_id="main",
                    locale="zh-CN",
                )
            )
    finally:
        with state.acquire_plugins_write_lock():
            state.plugins.clear()
            state.plugins.update(plugins_backup)
        with state.acquire_plugin_hosts_write_lock():
            state.plugin_hosts.clear()
            state.plugin_hosts.update(hosts_backup)

    assert exc_info.value.code == "PLUGIN_NOT_RUNNING"
    assert exc_info.value.status_code == 409
    assert exc_info.value.message == "插件未运行。请先启动该插件，再执行这个操作。"


@pytest.mark.parametrize(
    ("locale", "expected_key"),
    [
        ("zh-CN", "zh-CN"),
        ("zh-Hans", "zh-CN"),
        ("zh", "zh-CN"),
        ("zh-TW", "zh-TW"),
        ("zh-HK", "zh-TW"),
        ("zh-MO", "zh-TW"),
        ("zh-Hant", "zh-TW"),
        ("zh-Hant-TW", "zh-TW"),
        ("zh_Hant", "zh-TW"),
        ("ja-JP", "ja"),
        ("ko", "ko"),
        ("es-ES", "es"),
        ("pt-BR", "pt"),
        ("ru", "ru"),
        ("en-US", "en"),
        ("fr-FR", "en"),
        (None, "en"),
        ("", "en"),
    ],
)
def test_hosted_plugin_not_running_message_locale_mapping(locale, expected_key) -> None:
    assert _hosted_plugin_not_running_message(locale) == _PLUGIN_NOT_RUNNING_MESSAGES[expected_key]
