import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CARD_MAKER_JS = PROJECT_ROOT / "static" / "js" / "card_maker.js"
CHARACTER_CARD_MANAGER_JS = PROJECT_ROOT / "static" / "js" / "character_card_manager.js"
WINDOW_CONTROLS_JS = PROJECT_ROOT / "static" / "js" / "window_controls.js"
LOCALE_DIR = PROJECT_ROOT / "static" / "locales"


def test_new_character_auto_card_maker_enables_default_face_fallback_only_for_auto_popup():
    script = CHARACTER_CARD_MANAGER_JS.read_text(encoding="utf-8")

    assert "fallback_default_on_close: '1'" in script
    assert "const makerUrl = `/card_maker?${makerParams.toString()}`;" in script
    assert "const makerUrl = `/card_maker?name=${encodeURIComponent(currentName)}&mode=maker`;" in script


def test_card_maker_locks_controls_until_model_loads_and_guards_save():
    script = CARD_MAKER_JS.read_text(encoding="utf-8")

    assert "showLoading(true);" in script
    assert "updateCardMakerInteractivity(show);" in script
    assert "'.page-title-bar button, [data-neko-window-control]'" in script
    assert "exportFullBtn.disabled = primaryActionBusy || isModelLoading || !isModelLoaded;" in script
    assert "if (!isModelLoaded) {" in script
    assert "cardExport.modelStillLoading" in script
    assert "window.nekoBeforeWindowClose" in script
    assert "MODEL_LOADING_CLOSE_FALLBACK_MS = 8000" in script
    assert "return handled ? { handled: true } : undefined;" in script
    assert "if (isModelLoading && !canCloseWhileLoading()) return false;" in script
    assert "allowLoadingClose && isCloseControl" in script


def test_window_controls_support_page_close_hook():
    script = WINDOW_CONTROLS_JS.read_text(encoding="utf-8")

    assert "window.nekoBeforeWindowClose" in script
    assert "result === false || (result && result.handled === true)" in script
    assert "if (minimizeButton.disabled) return;" in script
    assert "if (maximizeButton.disabled) return;" in script
    assert "if (closeButton.disabled) return;" in script


def test_card_maker_model_loading_message_exists_in_all_locales():
    missing = []
    for locale_path in sorted(LOCALE_DIR.glob("*.json")):
        payload = json.loads(locale_path.read_text(encoding="utf-8"))
        card_export = payload.get("cardExport")
        if not isinstance(card_export, dict) or "modelStillLoading" not in card_export:
            missing.append(locale_path.name)

    assert missing == [], f"Missing cardExport.modelStillLoading in locale files: {', '.join(missing)}"
