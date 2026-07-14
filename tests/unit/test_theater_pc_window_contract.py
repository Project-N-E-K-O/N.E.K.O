from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PC_PROJECT_CANDIDATES = (
    PROJECT_ROOT / "N.E.K.O.-PC",
    PROJECT_ROOT.parent / "N.E.K.O.-PC",
)


def _read_project_file(path: str) -> str:
    """读取 N.E.K.O 仓库文件，集中处理 UTF-8 文本读取。"""  # noqa: DOCSTRING_CJK
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def _pc_project_root() -> Path:
    """定位同机的 N.E.K.O.-PC 项目，兼容子目录和同级目录两种开发布局。"""  # noqa: DOCSTRING_CJK
    for candidate in PC_PROJECT_CANDIDATES:
        if candidate.exists():
            return candidate
    pytest.skip("N.E.K.O.-PC project is not available for desktop window contract checks")


def _read_pc_file(path: str) -> str:
    """读取 N.E.K.O.-PC 文件，供 theater 桌面窗口契约测试使用。"""  # noqa: DOCSTRING_CJK
    return (_pc_project_root() / path).read_text(encoding="utf-8")


def test_theater_page_uses_shared_window_control_surface():
    """小剧场页面必须复用通用窗口按钮，避免单独实现一套桌面 chrome。"""  # noqa: DOCSTRING_CJK
    template = _read_project_file("templates/theater.html")

    assert "/static/css/window_controls.css?v={{ static_asset_version }}" in template
    assert "/static/js/window_controls.js?v={{ static_asset_version }}" in template
    assert "class=\"theater-title-bar page-title-bar\"" in template
    assert "data-neko-window-control=\"minimize\"" in template
    assert "data-neko-window-control=\"maximize\"" in template
    assert "data-neko-window-control=\"close\"" in template


def test_pc_child_windows_expose_host_controls_for_theater_window():
    """PC 同源子窗口需要暴露宿主窗口能力，保证 theater 的标题栏按钮能落到 Electron。"""  # noqa: DOCSTRING_CJK
    preload_child = _read_pc_file("src/preload-child.js")
    preload_common = _read_pc_file("src/preload-common.js")
    window_manager = _read_pc_file("src/window-manager.js")
    window_controls = _read_project_file("static/js/window_controls.js")

    assert "setupHostCapabilityBridge();" in preload_child
    assert "bridge.closeWindow = () => ipcRenderer.invoke('neko:host:close-window');" in preload_common
    assert "contextBridge.exposeInMainWorld('nekoWindowControl'" in preload_child
    assert "minimize: () => ipcRenderer.invoke('neko:host:minimize-window')" in preload_child
    assert "maximize: () => ipcRenderer.invoke('neko:host:maximize-window')" in preload_child
    assert "isMaximized: () => ipcRenderer.invoke('neko:host:is-maximized')" in preload_child
    assert "shouldAttachSameOriginChildPreload(win, details.url)" in window_manager
    assert "getPreloadPath('preload-child', isPackaged)" in window_manager
    assert "function closeCurrentWindowViaHost()" in window_controls
    assert "const host = window.nekoHost;" in window_controls
    assert "typeof host.closeWindow !== 'function'" in window_controls
    assert "const result = await host.closeWindow();" in window_controls
    assert "if (await closeCurrentWindowViaHost())" in window_controls


def test_pc_opens_theater_on_primary_display_without_settings_classification():
    """小剧场复用通用设置的主屏定位，但不能被误标为 settings 窗口。"""  # noqa: DOCSTRING_CJK
    pet_lifecycle = _read_pc_file("src/main/pet-window-lifecycle.js")

    assert "const PRIMARY_DISPLAY_POPUP_PATHS = new Set([\n  '/theater'," in pet_lifecycle
    assert "function isPrimaryDisplayPopupUrl(childUrl)" in pet_lifecycle
    assert "isManagerSettingsPopupUrl(details.url) || isPrimaryDisplayPopupUrl(details.url)" in pet_lifecycle
    assert "...getManagerPopupInitialBounds(screen, details.features)" in pet_lifecycle
    assert "childWindow._nekoKind = 'theater';" in pet_lifecycle
    assert "childWindow.on('focus', centerTheaterOnPrimaryDisplay);" in pet_lifecycle
    assert "if (childWindow.isMaximized() || childWindow.isFullScreen()) return;" in pet_lifecycle
    assert "childWindow.setBounds(target);" in pet_lifecycle
    manager_paths = pet_lifecycle.split("const MANAGER_SETTINGS_POPUP_PATHS", 1)[1].split("]);", 1)[0]
    assert "'/theater'" not in manager_paths
