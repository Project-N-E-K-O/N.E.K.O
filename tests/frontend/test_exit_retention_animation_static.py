from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    """Override the repo-level autouse fixture: static frontend checks are file-only."""
    yield


@pytest.mark.frontend
def test_exit_retention_animation_module_is_loaded_before_app_ui():
    template = (REPO_ROOT / "templates" / "index.html").read_text(encoding="utf-8")

    module_include = '<script src="/static/app-exit-retention.js?v={{ static_asset_version }}"></script>'
    app_ui_include = '<script src="/static/app-ui.js?v={{ static_asset_version }}"></script>'

    assert module_include in template
    assert template.index(module_include) < template.index(app_ui_include)


@pytest.mark.frontend
def test_exit_retention_animation_public_hooks_are_optional_and_i18n_based():
    module_path = REPO_ROOT / "static" / "app-exit-retention.js"
    source = module_path.read_text(encoding="utf-8")

    assert "window.exitRetentionAnimation" in source
    assert "playGoodbye" in source
    assert "playReturn" in source
    assert "cleanup" in source
    assert "exitRetention.goodbyeBubble" in source
    assert "exitRetention.returnBubble" in source
    assert "exitRetention.stayButton" in source
    assert "neko-exit-retention-stay" in source
    assert "真的要我走吗" not in source
    assert "我回来了" not in source
    assert "留下" not in source


@pytest.mark.frontend
def test_app_ui_calls_exit_retention_hooks_without_requiring_module():
    source = (REPO_ROOT / "static" / "app-ui.js").read_text(encoding="utf-8")

    assert "window.exitRetentionAnimation" in source
    assert "playGoodbye" in source
    assert "playReturn" in source
    assert "cleanup" in source
    assert "_exitRetentionResetTimerId" in source
    assert "neko-exit-retention-stay" in source
    assert "clearExitRetentionResetTimer" in source


@pytest.mark.frontend
def test_exit_retention_animation_avoids_expensive_filter_animation():
    css = (REPO_ROOT / "static" / "css" / "index.css").read_text(encoding="utf-8")
    start = css.index("/* ===== Exit Retention Animation ===== */")
    section = css[start:]

    assert "nekoAvatarGoodbyeExit" not in section
    assert "neko-avatar-goodbye-exiting" not in section
    assert "filter:" not in section
    assert "backdrop-filter" not in section


@pytest.mark.frontend
def test_exit_retention_module_does_not_animate_avatar_container_directly():
    source = (REPO_ROOT / "static" / "app-exit-retention.js").read_text(encoding="utf-8")

    assert "container.classList.add(EXIT_CLASS)" not in source
    assert "classTimer = setTimeout" not in source


@pytest.mark.frontend
def test_exit_retention_assets_participate_in_static_asset_version():
    source = (REPO_ROOT / "main_routers" / "pages_router.py").read_text(encoding="utf-8")

    assert "_STATIC_ASSET_VERSION_PATHS" in source
    assert 'static/app-exit-retention.js"' in source
    assert 'static/app-ui.js"' in source
    assert 'static/css/index.css"' in source
    assert 'static/locales/zh-CN.json"' in source


@pytest.mark.frontend
def test_locale_cache_version_follows_loaded_i18n_script_version():
    source = (REPO_ROOT / "static" / "i18n-i18next.js").read_text(encoding="utf-8")

    assert "getLocaleVersionFromScript" in source
    assert "document.currentScript" in source
    assert "LOCALE_VERSION_FALLBACK" in source
    assert "const LOCALE_VERSION =" not in source
