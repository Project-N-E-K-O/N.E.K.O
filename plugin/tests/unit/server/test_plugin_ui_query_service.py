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
    _collect_hosted_tsx_modules_sync,
    _get_static_ui_config_from_meta,
    _hosted_plugin_not_running_message,
    _module_key,
    _PLUGIN_NOT_RUNNING_MESSAGES,
    _resolve_hosted_module_path,
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


def _write_hosted_surface_tree(root):
    """Build a plugin surface tree with named/default/transitive/type imports."""
    surfaces = root / "surfaces"
    surfaces.mkdir(parents=True)
    (surfaces / "panel.tsx").write_text(
        "import { useState } from '@neko/plugin-ui'\n"
        "import { callPlugin, text } from './shared'\n"
        "import NoteCard from './note_card'\n"
        "import type { NoteItem } from './note_card'\n"
        "export default function Panel() { return null }\n",
        encoding="utf-8",
    )
    (surfaces / "shared.ts").write_text(
        "import type { PluginSurfaceProps } from '@neko/plugin-ui'\n"
        "export function callPlugin() { return {} }\n"
        "export function text() { return '' }\n",
        encoding="utf-8",
    )
    # note_card depends transitively on shared and lives as a .tsx default export.
    (surfaces / "note_card.tsx").write_text(
        "import { text } from './shared'\n"
        "export default function NoteCard() { return null }\n",
        encoding="utf-8",
    )
    return surfaces / "panel.tsx"


def test_collect_hosted_modules_follows_relative_import_graph(tmp_path) -> None:
    root = (tmp_path / "demo_plugin").resolve()
    entry = _write_hosted_surface_tree(root)

    modules = _collect_hosted_tsx_modules_sync(entry, root)

    keys = {module["path"] for module in modules}
    # shared is reachable directly and transitively but appears exactly once.
    assert keys == {"surfaces/shared", "surfaces/note_card"}
    assert len(modules) == 2
    assert _module_key(entry, root) == "surfaces/panel"
    shared = next(m for m in modules if m["path"] == "surfaces/shared")
    assert "export function callPlugin" in shared["source"]


def test_collect_hosted_modules_ignores_commented_and_quoted_imports(tmp_path) -> None:
    root = (tmp_path / "demo_plugin").resolve()
    surfaces = root / "surfaces"
    surfaces.mkdir(parents=True)
    # A real file that must NOT be pulled in just because it is named in a comment.
    (surfaces / "scratch.ts").write_text("export const junk = 1\n", encoding="utf-8")
    entry = surfaces / "panel.tsx"
    entry.write_text(
        "// import { junk } from './scratch'\n"
        "const example = \"import { x } from './scratch'\"\n"
        "/* import './scratch' */\n"
        "export default function Panel() { return null }\n",
        encoding="utf-8",
    )

    modules = _collect_hosted_tsx_modules_sync(entry, root)

    assert modules == []


def test_collect_hosted_modules_follows_reexports_not_value_exports(tmp_path) -> None:
    root = (tmp_path / "demo_plugin").resolve()
    surfaces = root / "surfaces"
    surfaces.mkdir(parents=True)
    (surfaces / "scratch.ts").write_text("export const junk = 1\n", encoding="utf-8")
    (surfaces / "real.ts").write_text("export const value = 2\n", encoding="utf-8")
    entry = surfaces / "panel.tsx"
    entry.write_text(
        # value export that merely contains a path-like string — NOT a dependency
        "export const SCRATCH_PATH = './scratch'\n"
        # genuine re-export — IS a dependency
        "export { value } from './real'\n"
        "export default function Panel() { return null }\n",
        encoding="utf-8",
    )

    modules = _collect_hosted_tsx_modules_sync(entry, root)

    assert {m["path"] for m in modules} == {"surfaces/real"}


def test_collect_hosted_modules_fails_closed_when_graph_too_large(tmp_path) -> None:
    root = (tmp_path / "demo_plugin").resolve()
    surfaces = root / "surfaces"
    surfaces.mkdir(parents=True)
    # A single helper larger than the byte cap must reject the whole request
    # rather than silently shipping a truncated graph.
    (surfaces / "huge.ts").write_text(
        "export const blob = '" + ("x" * (600 * 1024)) + "'\n", encoding="utf-8"
    )
    entry = surfaces / "panel.tsx"
    entry.write_text(
        "import { blob } from './huge'\n"
        "export default function Panel() { return null }\n",
        encoding="utf-8",
    )

    with pytest.raises(ServerDomainError) as exc_info:
        _collect_hosted_tsx_modules_sync(entry, root)
    assert exc_info.value.code == "PLUGIN_UI_MODULE_GRAPH_TOO_LARGE"


def test_collect_hosted_modules_ignores_bare_and_escaping_specifiers(tmp_path) -> None:
    root = (tmp_path / "demo_plugin").resolve()
    surfaces = root / "surfaces"
    surfaces.mkdir(parents=True)
    # A secret file outside the plugin root must never be collected.
    (tmp_path / "secret.ts").write_text("export const token = 'leak'\n", encoding="utf-8")
    entry = surfaces / "panel.tsx"
    entry.write_text(
        "import { useState } from '@neko/plugin-ui'\n"
        "import { token } from '../../secret'\n"
        "export default function Panel() { return null }\n",
        encoding="utf-8",
    )

    modules = _collect_hosted_tsx_modules_sync(entry, root)

    assert modules == []


def test_resolve_hosted_module_path_tries_suffixes_and_index(tmp_path) -> None:
    root = (tmp_path / "demo_plugin").resolve()
    surfaces = root / "surfaces"
    nested = surfaces / "widgets"
    nested.mkdir(parents=True)
    (surfaces / "shared.ts").write_text("export const x = 1\n", encoding="utf-8")
    (nested / "index.tsx").write_text("export default function W() { return null }\n", encoding="utf-8")

    shared = _resolve_hosted_module_path("./shared", surfaces, root)
    assert shared is not None and shared.name == "shared.ts"

    barrel = _resolve_hosted_module_path("./widgets", surfaces, root)
    assert barrel is not None and barrel.name == "index.tsx"

    # A `./helper.js` ESM specifier resolves to the real `.ts`/`.tsx` source.
    (surfaces / "helper.ts").write_text("export const y = 2\n", encoding="utf-8")
    js_spec = _resolve_hosted_module_path("./helper.js", surfaces, root)
    assert js_spec is not None and js_spec.name == "helper.ts"

    # Escaping the plugin root is rejected.
    assert _resolve_hosted_module_path("../../secret", surfaces, root) is None
