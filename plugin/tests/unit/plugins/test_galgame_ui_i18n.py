from __future__ import annotations

import asyncio
import builtins
import importlib
import json
import re
import sys
from pathlib import Path

import pytest


UI_I18N_DIR = (
    Path(__file__).resolve().parents[3]
    / "plugins"
    / "galgame_plugin"
    / "i18n"
    / "ui"
)
STATIC_I18N_JS = (
    Path(__file__).resolve().parents[3]
    / "plugins"
    / "galgame_plugin"
    / "static"
    / "i18n.js"
)
STATIC_INDEX_HTML = (
    Path(__file__).resolve().parents[3]
    / "plugins"
    / "galgame_plugin"
    / "static"
    / "index.html"
)

EXPECTED_BUNDLE_LOCALES = ["zh-CN", "zh-TW", "en", "ja", "ru", "ko"]


def _register_galgame_install_plugin(module, *, i18n_dir: Path = UI_I18N_DIR) -> None:
    module.register_install_plugin(
        "galgame_plugin",
        install_kinds={
            "textractor": module.InstallKindRegistration(
                entry_id="galgame_install_textractor",
                label="Textractor",
                queued_message="Textractor install queued",
            ),
            "rapidocr_models": module.InstallKindRegistration(
                entry_id="galgame_download_rapidocr_models",
                label="RapidOCR Models",
                queued_message="RapidOCR model download queued",
            ),
        },
        ui_i18n_dir=i18n_dir,
        tutorial_enabled=True,
    )


def test_galgame_ui_i18n_locale_bundles_have_same_keys() -> None:
    bundles = {
        locale: json.loads((UI_I18N_DIR / f"{locale}.json").read_text(encoding="utf-8"))
        for locale in EXPECTED_BUNDLE_LOCALES
    }
    expected_keys = set(bundles["zh-CN"])

    assert len(expected_keys) >= 100
    for locale, bundle in bundles.items():
        bundle_keys = set(bundle)
        missing = sorted(expected_keys - bundle_keys)
        extra = sorted(bundle_keys - expected_keys)
        assert bundle_keys == expected_keys, (
            f"{locale}: missing={missing[:20]} extra={extra[:20]}"
        )
        assert all(isinstance(value, str) and value for value in bundle.values())


def test_rapidocr_language_buttons_use_full_i18n_keys() -> None:
    html = STATIC_INDEX_HTML.read_text(encoding="utf-8")

    assert 'id="rapidocrLangChBtn"' in html
    assert 'data-i18n="ui.install.rapidocr.lang.ch"' in html
    assert 'data-i18n="ui.install.rapidocr.lang.japan"' in html
    assert 'data-i18n="ui.install.rapidocr.lang.korean"' in html
    assert 'data-i18n="ui.install.rapidocr.lang.en"' in html
    assert "ui.install.rapidocr.lang.ch_short" not in html
    assert "ui.install.rapidocr.lang.japan_short" not in html
    assert "ui.install.rapidocr.lang.korean_short" not in html
    assert "ui.install.rapidocr.lang.en_short" not in html


def test_galgame_ui_i18n_zh_tw_route_locale_normalization() -> None:
    from plugin.server.routes.plugin_install import _normalize_ui_locale

    assert _normalize_ui_locale("zh-TW") == "zh-TW"
    assert _normalize_ui_locale("zh-Hant") == "zh-TW"
    assert _normalize_ui_locale("zh-HK") == "zh-TW"
    assert _normalize_ui_locale("zh-MO") == "zh-TW"
    assert _normalize_ui_locale("zh") == "zh-CN"
    assert _normalize_ui_locale("es-ES") == "es"
    assert _normalize_ui_locale("pt-BR") == "pt"


def test_galgame_ui_locale_route_falls_back_when_language_utils_unavailable(monkeypatch) -> None:
    module_name = "plugin.server.routes.plugin_install"
    sys.modules.pop(module_name, None)
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "utils.language_utils":
            raise ImportError("language utils unavailable")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    try:
        module = importlib.import_module(module_name)
        _register_galgame_install_plugin(module)
        response = asyncio.run(module.get_plugin_ui_locale("galgame_plugin"))

        assert json.loads(response.body.decode("utf-8")) == {"locale": "en"}
    finally:
        sys.modules.pop(module_name, None)


def test_plugin_ui_i18n_route_uses_registered_i18n_dir(monkeypatch, tmp_path: Path) -> None:
    from plugin.server import install_registry
    from plugin.server.routes import plugin_install

    i18n_dir = tmp_path / "custom_i18n"
    i18n_dir.mkdir()
    expected_file = i18n_dir / "en.json"
    expected_file.write_text('{"custom":"ok"}', encoding="utf-8")
    monkeypatch.setattr(install_registry, "_install_plugin_registry", {})
    plugin_install.register_install_plugin(
        "custom_plugin",
        install_kinds={},
        ui_i18n_dir=i18n_dir,
    )

    response = asyncio.run(plugin_install.get_plugin_ui_i18n("custom_plugin", "en"))

    assert Path(response.path) == expected_file


def test_plugin_ui_i18n_route_bootstraps_builtin_registration(monkeypatch) -> None:
    from plugin.server import install_registry
    from plugin.server.routes import plugin_install

    expected_file = (
        Path(plugin_install.__file__).resolve().parents[2]
        / "plugins"
        / "study_companion"
        / "i18n"
        / "en.json"
    )
    monkeypatch.setattr(install_registry, "_install_plugin_registry", {})
    monkeypatch.setattr(install_registry, "_tutorial_migration_hooks", {})

    response = asyncio.run(plugin_install.get_plugin_ui_i18n("study_companion", "en"))

    assert Path(response.path) == expected_file


def test_tutorial_store_uses_runtime_data_root(monkeypatch, tmp_path: Path) -> None:
    from plugin.server.routes import plugin_install

    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    monkeypatch.setattr(plugin_install, "_tutorial_store_instance", None)

    store = plugin_install._tutorial_store()

    assert store == (
        runtime_root / "server" / "plugin_install" / "tutorial_progress.json"
    )


def test_tutorial_store_runs_registered_migration_hook(monkeypatch, tmp_path: Path) -> None:
    from plugin.server import install_registry
    from plugin.server.routes import plugin_install

    runtime_root = tmp_path / "runtime"
    expected_store = runtime_root / "server" / "plugin_install" / "tutorial_progress.json"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    monkeypatch.setattr(plugin_install, "_tutorial_store_instance", None)
    monkeypatch.setattr(install_registry, "_tutorial_migration_hooks", [])
    monkeypatch.setattr(plugin_install, "_tutorial_migrated_paths", set())

    def migrate(store_path: Path) -> None:
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text('{"completed": true}', encoding="utf-8")

    plugin_install.register_tutorial_migration_hook(migrate)

    assert plugin_install._tutorial_store() == expected_store
    assert json.loads(expected_store.read_text(encoding="utf-8")) == {"completed": True}


def test_tutorial_store_raises_when_migration_hook_fails(monkeypatch, tmp_path: Path) -> None:
    from plugin.server import install_registry
    from plugin.server.routes import plugin_install

    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    monkeypatch.setattr(plugin_install, "_tutorial_store_instance", None)
    monkeypatch.setattr(install_registry, "_tutorial_migration_hooks", [])
    monkeypatch.setattr(plugin_install, "_tutorial_migrated_paths", set())

    def migrate(_store_path: Path) -> None:
        raise OSError("blocked")

    plugin_install.register_tutorial_migration_hook(migrate)

    with pytest.raises(OSError):
        plugin_install._tutorial_store()


def test_tutorial_progress_routes_use_blocking_runner(monkeypatch, tmp_path: Path) -> None:
    from plugin.server import install_registry
    from plugin.server.routes import plugin_install

    runtime_root = tmp_path / "runtime"
    calls: list[str] = []

    async def _fake_run_blocking(func, *args, **kwargs):
        calls.append(getattr(func, "__name__", ""))
        return func(*args, **kwargs)

    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    monkeypatch.setattr(plugin_install, "_tutorial_store_instance", None)
    monkeypatch.setattr(plugin_install, "_tutorial_store_instances", {})
    monkeypatch.setattr(install_registry, "_install_plugin_registry", {})
    monkeypatch.setattr(plugin_install, "_run_blocking", _fake_run_blocking)
    _register_galgame_install_plugin(plugin_install)

    status_response = asyncio.run(plugin_install.get_tutorial_status("galgame_plugin"))
    save_response = asyncio.run(
        plugin_install.save_tutorial_progress(
            "galgame_plugin",
            plugin_install.TutorialProgressPayload(completed=True),
        )
    )

    assert json.loads(status_response.body.decode("utf-8"))["ok"] is True
    assert json.loads(save_response.body.decode("utf-8"))["ok"] is True
    assert "_read_tutorial_progress" in calls
    assert "_write_tutorial_progress" in calls


def test_galgame_tutorial_migration_copies_runtime_store_progress(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from plugin.plugins.galgame_plugin import _tutorial_migration

    runtime_root = tmp_path / "runtime"
    runtime_store = runtime_root / "plugins" / "galgame_plugin" / "data" / "galgame_store.json"
    new_store = runtime_root / "server" / "plugin_install" / "tutorial_progress.json"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    runtime_store.parent.mkdir(parents=True, exist_ok=True)
    runtime_store.write_text(
        '{"tutorial_progress": {"completed": true, "last_step_index": 4}}',
        encoding="utf-8",
    )

    class _Store:
        def __init__(self, store_path: Path, _logger) -> None:
            self.store_path = store_path

        def load_tutorial_progress(self) -> dict[str, object] | None:
            raw = json.loads(self.store_path.read_text(encoding="utf-8"))
            return raw.get("tutorial_progress")

    monkeypatch.setattr(_tutorial_migration, "GalgameStore", _Store)

    _tutorial_migration.copy_legacy_tutorial_progress_if_missing(new_store)

    assert json.loads(new_store.read_text(encoding="utf-8")) == {
        "completed": True,
        "last_step_index": 4,
    }


def test_galgame_tutorial_migration_skips_unreadable_legacy_store(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from plugin.plugins.galgame_plugin import _tutorial_migration

    corrupt_store = tmp_path / "corrupt_galgame_store.json"
    valid_store = tmp_path / "valid_galgame_store.json"
    new_store = tmp_path / "tutorial_progress.json"
    corrupt_store.write_text("not json", encoding="utf-8")
    valid_store.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        _tutorial_migration,
        "_legacy_store_paths",
        lambda: (corrupt_store, valid_store),
    )

    class _Store:
        def __init__(self, store_path: Path, _logger) -> None:
            self.store_path = store_path

        def load_tutorial_progress(self) -> dict[str, object] | None:
            if self.store_path == corrupt_store:
                raise ValueError("legacy store is corrupt")
            return {"completed": True, "last_step_index": 2}

    monkeypatch.setattr(_tutorial_migration, "GalgameStore", _Store)

    _tutorial_migration.copy_legacy_tutorial_progress_if_missing(new_store)

    assert json.loads(new_store.read_text(encoding="utf-8")) == {
        "completed": True,
        "last_step_index": 2,
    }


def test_galgame_ui_i18n_zh_tw_is_traditional_chinese_not_zh_cn_copy() -> None:
    zh_cn = json.loads((UI_I18N_DIR / "zh-CN.json").read_text(encoding="utf-8"))
    zh_tw = json.loads((UI_I18N_DIR / "zh-TW.json").read_text(encoding="utf-8"))

    assert zh_tw != zh_cn
    assert zh_tw["ui.app.title"] == "Galgame 遊玩助手"
    assert zh_tw["ui.app.subtitle"] == "讓貓娘陪你一起玩 Galgame"

    simplified_fragments = [
        "游玩",
        "让猫娘",
        "获取",
        "设置",
        "窗口",
        "进程",
        "识别",
        "截图",
        "当前",
        "状态",
        "后台",
        "点击",
        "发送",
    ]
    for key, value in zh_tw.items():
        assert not any(fragment in value for fragment in simplified_fragments), (key, value)


def test_galgame_ui_i18n_has_install_and_static_shell_keys() -> None:
    bundle = json.loads((UI_I18N_DIR / "en.json").read_text(encoding="utf-8"))

    assert bundle["ui.app.title"] == "Galgame Play Assistant"
    assert bundle["ui.button.collapse"] == "Collapse"
    assert bundle["ui.install.rapidocr.download_models.action"] == "Download Models Now"
    assert bundle["ui.install.rapidocr.version_label"] == "Model version"
    assert bundle["ui.install.rapidocr.version_v5"] == "PP-OCRv5"
    assert "PP-OCRv4" in bundle["ui.install.rapidocr.v5_japan_note"]
    assert bundle["ui.first_run.action.show_rapidocr_models_guide"] == "View Manual Download Guide"
    assert bundle["ui.flash.plugin_not_started"].startswith("Plugin not started")


def test_galgame_ui_i18n_rapidocr_copy_is_not_left_half_deleted() -> None:
    forbidden_fragments = [
        "stable capture,.",
        "fell back to. Reason",
        "回退到了。原因",
        "优先 兜底",
        "では にフォールバック",
        "し をフォールバック",
        "에서는로",
        "우선하고를",
        "откат на. Причина",
        "приоритетом RapidOCR и резервом",
    ]
    for locale in EXPECTED_BUNDLE_LOCALES:
        bundle = json.loads((UI_I18N_DIR / f"{locale}.json").read_text(encoding="utf-8"))
        for key in [
            "ui.install.ocr_desc",
            "ui.install.ocr_auto.title",
            "ui.install.rapidocr.fallback_body",
            "ui.install.rapidocr.ready_body",
        ]:
            value = bundle[key]
            assert not any(fragment in value for fragment in forbidden_fragments), (locale, key, value)


def test_galgame_ui_i18n_has_dynamic_dashboard_keys() -> None:
    bundle = json.loads((UI_I18N_DIR / "en.json").read_text(encoding="utf-8"))

    for key in [
        "ui.field.connection_state",
        "ui.field.ocr_reader_status",
        "ui.field.memory_reader_process",
        "ui.agent_status.paused_window_not_foreground",
        "ui.connection_state.active",
        "ui.mode_label.choice_advisor",
        "ui.reader_mode.auto",
        "ui.capture_profile.match_source.bucket_exact",
        "ui.action.select_ocr_window",
    ]:
        assert key in bundle


def test_galgame_ui_i18n_script_prefers_query_locale_with_api_fallback() -> None:
    script = STATIC_I18N_JS.read_text(encoding="utf-8")

    assert "new URLSearchParams(location.search).get('locale')" in script
    assert "const queryLocale = this._queryLocale();" in script
    assert "const storageLocale = this._storageLocale();" in script
    assert "if (queryLocale) {" in script
    assert "this.setLang(queryLocale);" in script
    assert "else if (storageLocale) {" in script
    assert "this.setLang(storageLocale);" in script
    assert "localStorage.getItem('locale')" in script
    assert "value === 'auto' ? this._browserLocale() : value" in script
    assert "else {" in script
    assert "/ui-api/locale" in script
    assert "/ui-api/i18n/ui/" in script
    assert "i18n-ready" in script


def test_galgame_ui_i18n_script_maps_manager_locales_to_ui_bundles() -> None:
    script = STATIC_I18N_JS.read_text(encoding="utf-8")

    for expected in [
        "add('zh-CN');",
        "add('en');",
        "add('ja');",
        "add('ko');",
        "add('ru');",
    ]:
        assert expected in script


def test_galgame_ui_first_run_has_manual_rapidocr_model_cta() -> None:
    script = (
        Path(__file__).resolve().parents[3]
        / "plugins"
        / "galgame_plugin"
        / "static"
        / "main.js"
    ).read_text(encoding="utf-8")

    assert "show_rapidocr_models_guide" in script
    assert "ui.first_run.action.show_rapidocr_models_guide" in script
    assert "ui.flash.rapidocr_manual_guide_revealed" in script


def test_galgame_ui_has_rapidocr_version_toggle() -> None:
    static_root = Path(__file__).resolve().parents[3] / "plugins" / "galgame_plugin" / "static"
    html = (static_root / "index.html").read_text(encoding="utf-8")
    script = (static_root / "main.js").read_text(encoding="utf-8")

    assert "rapidocrVersionBar" in html
    assert "rapidocrVersionV4Btn" in html
    assert "rapidocrVersionV5Btn" in html
    assert "renderRapidOcrVersionBar" in script
    assert re.search(r"setRapidOcrLang\s*\(\s*\{\s*ocr_version\s*:\s*version\s*\}\s*\)", script)


def test_galgame_ui_first_run_dxcam_prompt_requires_dxcam_backend() -> None:
    script = (
        Path(__file__).resolve().parents[3]
        / "plugins"
        / "galgame_plugin"
        / "static"
        / "main.js"
    ).read_text(encoding="utf-8")

    assert re.search(r"function\s+requiresDxcamBackend\s*\(", script)
    assert "dxcamRequired" in script
    assert "dxcam.installed" in script
    assert "install_dxcam" in script
    assert re.search(r"hasInstallFlow\s*\(\s*['\"]dxcam['\"]\s*\)", script)
