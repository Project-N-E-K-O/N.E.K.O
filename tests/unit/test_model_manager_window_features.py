from pathlib import Path
from tests.static_app_parts import read_js_parts


MODEL_MANAGER_PART_NAMES = (
    "runtime-loaders.js",
    "dropdown-manager.js",
    "page-bridge.js",
    "card-face.js",
    "path-request-fullscreen.js",
    "page-controller.js",
    "window-lifecycle.js",
)


def read_model_manager_source() -> str:
    parts_dir = Path("static/js/model_manager")
    return "".join(
        (parts_dir / part_name).read_text(encoding="utf-8")
        for part_name in MODEL_MANAGER_PART_NAMES
    )


def test_avatar_model_manager_popup_opens_fullscreen():
    source = Path("static/avatar/avatar-ui-popup.js").read_text(encoding="utf-8")

    assert "function buildAvatarFullscreenWindowFeatures()" in source
    assert "screenRef.availWidth || screenRef.width" in source
    assert "screenRef.availHeight || screenRef.height" in source
    assert "features = buildAvatarFullscreenWindowFeatures();" in source
    assert "openModelManagerWindow(finalUrl, windowName, features);" in source
    assert "window.handleHideMainUI()" not in source


def test_yui_model_manager_handoff_opens_fullscreen():
    source = Path("static/tutorial/yui-guide/page-handoff.js").read_text(encoding="utf-8")

    assert "function buildFullscreenWindowFeatures()" in source
    assert "function isModelManagerPageUrl(openUrl)" in source
    assert "if (isModelManagerPageUrl(openUrl))" in source
    assert "return buildFullscreenWindowFeatures();" in source
    start = source.index("function openModelManagerPage(")
    end = source.index("\n    function ", start + len("function openModelManagerPage("))
    model_manager_block = source[start:end]
    assert "buildFullscreenWindowFeatures()" in model_manager_block
    assert "{ keepMainUIVisible: true }" in model_manager_block


def test_model_manager_hide_show_cross_page_messages_are_removed():
    model_manager_source = read_model_manager_source()
    interpage_source = read_js_parts(Path("static/app/app-interpage"))

    assert "hide_main_ui" not in model_manager_source
    assert "show_main_ui" not in model_manager_source
    assert "hide_main_ui" not in interpage_source
    assert "show_main_ui" not in interpage_source


def test_model_manager_pngtuber_import_supports_package_files_and_folders():
    template = Path("templates/model_manager.html").read_text(encoding="utf-8")
    source = Path("static/js/model_manager.js").read_text(encoding="utf-8")

    assert 'id="pngtuber-model-upload" webkitdirectory directory multiple' in template
    assert 'id="pngtuber-package-upload"' in template
    assert '.pngremix,.pngRemix,.save,.veadomini,.veado' in template
    assert "const pngtuberPackageUpload = document.getElementById('pngtuber-package-upload');" in source
    assert "showPNGTuberUploadChoice()" in source
    assert "let pngtuberUploadChoiceOpeningPicker = false;" in source
    assert "if (pngtuberUploadChoiceOpeningPicker) return;" in source
    assert "menu.parentNode.removeChild(menu);" in source
    assert "async function uploadPNGTuberFiles(" in source
    assert "await uploadPNGTuberFiles(e.target.files, pngtuberModelUpload);" in source
    assert "await uploadPNGTuberFiles(e.target.files, pngtuberPackageUpload);" in source


def test_voice_clone_api_settings_uses_shared_named_window():
    source = Path("static/js/voice_clone.js").read_text(encoding="utf-8")
    common_source = Path("static/common_dialogs.js").read_text(encoding="utf-8")
    open_api_settings = source[source.index("function openApiSettings("):source.index("function openApiSettingsKeyBook(")]
    open_api_settings_key_book = source[source.index("function openApiSettingsKeyBook("):source.index("// 安全地解析 fetch 响应")]

    assert "function buildApiKeySettingsWindowFeatures(width = 1240, height = 940)" in common_source
    assert "window.buildApiKeySettingsWindowFeatures = buildApiKeySettingsWindowFeatures;" in common_source
    assert "const focusKeyBook = !!(options && options.focusKeyBook);" in open_api_settings
    assert "const url = focusKeyBook ? '/api_key?focus=key_book' : '/api_key';" in open_api_settings
    assert "const windowName = 'neko_api_key';" in open_api_settings
    assert "window.buildApiKeySettingsWindowFeatures()" in open_api_settings
    assert "window.openOrFocusWindow(url, windowName, features)" in open_api_settings
    assert "window.open(url, windowName, features)" in open_api_settings
    assert "win.focus()" in open_api_settings
    assert "function notifyApiSettingsKeyBookFocus(win)" in source
    assert "win.postMessage({ type: 'focus_api_key_book' }, window.location.origin);" in source
    assert "notifyApiSettingsKeyBookFocus(win);" in open_api_settings
    assert "openApiSettings({ focusKeyBook: true });" in open_api_settings_key_book
    assert "'apiSettings'" not in open_api_settings
    assert "width=820,height=700" not in source
