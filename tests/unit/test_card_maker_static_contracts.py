import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CARD_MAKER_JS = PROJECT_ROOT / "static" / "js" / "card_maker.js"
CARD_MAKER_CSS = PROJECT_ROOT / "static" / "css" / "card_maker.css"
CHARACTER_CARD_MANAGER_JS = PROJECT_ROOT / "static" / "js" / "character_card_manager.js"
MODEL_MANAGER_JS = PROJECT_ROOT / "static" / "js" / "model_manager.js"
WINDOW_CONTROLS_JS = PROJECT_ROOT / "static" / "js" / "window_controls.js"
CARD_MAKER_TEMPLATE = PROJECT_ROOT / "templates" / "card_maker.html"
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


def test_model_manager_default_card_face_fallback_uses_full_card_canvas():
    script = MODEL_MANAGER_JS.read_text(encoding="utf-8")

    assert "captureDefaultCardFaceModelImage(state, 600, 800)" in script
    assert "800 - Math.floor(800 / 6)" not in script


def test_model_manager_parameter_save_restores_unsaved_and_offers_card_face():
    script = MODEL_MANAGER_JS.read_text(encoding="utf-8")
    parameter_editor = (PROJECT_ROOT / "static" / "js" / "live2d_parameter_editor.js").read_text(encoding="utf-8")

    assert "window.localStorage" in parameter_editor
    assert "window.localStorage" in script
    assert "parameterEditorSavedNeedsModelSave" in script
    assert "restorePendingParameterEditorSaveState(savePositionBtn, {" in script
    assert "|| await restorePendingParameterEditorSaveState(savePositionBtn, { currentModelInfo })" in script
    assert "parameterEditedSinceSave ||" in script
    assert "offerCardFaceAfterModelSave" in script


def test_card_maker_supports_closeup_model_scale():
    script = CARD_MAKER_JS.read_text(encoding="utf-8")
    template = CARD_MAKER_TEMPLATE.read_text(encoding="utf-8")

    assert "const MODEL_OFFSET_X_MIN = -800;" in script
    assert "const MODEL_OFFSET_X_MAX = 800;" in script
    assert "const MODEL_OFFSET_Y_MIN = -1000;" in script
    assert "const MODEL_OFFSET_Y_MAX = 1000;" in script
    assert "const MODEL_SCALE_MAX = 600;" in script
    assert "MODEL_PREVIEW_MAX_SOURCE_SCALE = 5" in script
    assert "MODEL_EXPORT_MAX_SOURCE_SCALE = 8" in script
    assert 'id="offset-x" min="-800" max="800"' in template
    assert 'id="offset-y" min="-1000" max="1000"' in template
    assert 'id="portrait-scale" min="50" max="600"' in template


def test_card_maker_registers_variant_stickers():
    script = CARD_MAKER_JS.read_text(encoding="utf-8")
    template = CARD_MAKER_TEMPLATE.read_text(encoding="utf-8")

    for sticker_name in [
        "chat_sugar1.png",
        "chat_sugar3.png",
        "chat_hammer1.png",
        "chat_hammer2.png",
        "cat_moneny.png",
        "cat_claw1.png",
        "cat_claw2.png",
    ]:
        assert sticker_name in script
    assert "STICKER_VARIANT_GROUPS" in script
    assert "switchSelectedStickerVariant" in script
    assert 'id="sticker-switch-variant-btn"' in template
    assert "item.tabIndex = 0;" in script
    assert "item.setAttribute('role', 'button');" in script
    assert "item.addEventListener('keydown'" in script
    assert "event.key === 'Enter' || event.keyCode === 13" in script
    assert "event.key === ' ' || event.keyCode === 32" in script


def test_card_maker_preview_can_select_stickers_directly():
    script = CARD_MAKER_JS.read_text(encoding="utf-8")
    template = CARD_MAKER_TEMPLATE.read_text(encoding="utf-8")
    styles = CARD_MAKER_CSS.read_text(encoding="utf-8")

    assert "function getStickerDragTarget(hitSticker, event)" in script
    assert "function isPointerInsideStickerSelectionBox(s, clientX, clientY)" in script
    assert "function getStickersAtPointer(clientX, clientY)" in script
    assert "function cycleStickerSelectionAtPointer(event)" in script
    assert "previewEl.addEventListener('contextmenu'" in script
    assert "cycleStickerSelectionAtPointer(e);" in script
    assert "dragTarget = getStickerDragTarget(sticker, e);" in script
    assert "if (dragTarget.id !== selectedStickerId) {" in script
    assert "selectSticker(dragTarget.id);" in script
    assert "refreshLayerPanel();" in script
    assert "if (e.button !== 0) return;" in script
    assert "if (selectedStickerId !== sticker.id) return;" not in script
    assert "cardExport.stickerOverlapCycleHint" in template
    assert ".sticker-selection-hint" in styles


def test_card_maker_layer_order_matches_visual_stacking():
    script = CARD_MAKER_JS.read_text(encoding="utf-8")

    assert "const layerInsertIndex = getStickerInsertIndexForCurrentLayer();" in script
    assert "layerOrder.splice(layerInsertIndex, 0, { type: 'sticker', id });" in script
    assert "function getStickerInsertIndexForCurrentLayer()" in script
    assert "ordered.slice().reverse().forEach" in script
    assert "canvas 需要从下到上绘制" in script


def test_card_maker_selected_sticker_uses_overlay_selection_frame():
    script = CARD_MAKER_JS.read_text(encoding="utf-8")
    styles = CARD_MAKER_CSS.read_text(encoding="utf-8")

    assert "function updateStickerSelectionFrame(s)" in script
    assert "sticker-selection-frame" in styles
    assert "el.style.pointerEvents = (activeTab === 'decor-tab' && !modelLayerSelected) ? 'auto' : 'none';" in script
    assert "const target = (s.layer === 'below') ? below : above;" in script


def test_card_maker_deleting_selected_sticker_inherits_selection():
    script = CARD_MAKER_JS.read_text(encoding="utf-8")

    assert "function getStickerSelectionSuccessorId(deletedId)" in script
    assert "const nextStickerId = deletingSelectedSticker ? getStickerSelectionSuccessorId(id) : null;" in script
    assert "selectSticker(nextStickerId);" in script
    assert "selectModelLayer({ refresh: false });" in script
    assert "function selectModelLayer(options = {})" in script


def test_card_maker_model_loading_message_exists_in_all_locales():
    missing = []
    for locale_path in sorted(LOCALE_DIR.glob("*.json")):
        payload = json.loads(locale_path.read_text(encoding="utf-8"))
        card_export = payload.get("cardExport")
        required_keys = ["modelStillLoading", "switchStickerVariant", "stickerOverlapCycleHint"]
        if not isinstance(card_export, dict) or any(key not in card_export for key in required_keys):
            missing.append(locale_path.name)

    assert missing == [], f"Missing cardExport keys in locale files: {', '.join(missing)}"


def test_model_manager_parameter_save_message_exists_in_all_locales():
    missing = []
    for locale_path in sorted(LOCALE_DIR.glob("*.json")):
        payload = json.loads(locale_path.read_text(encoding="utf-8"))
        model_manager = payload.get("modelManager")
        if not isinstance(model_manager, dict) or "parameterEditorSavedNeedsModelSave" not in model_manager:
            missing.append(locale_path.name)

    assert missing == [], f"Missing modelManager parameter-save keys in locale files: {', '.join(missing)}"


def test_workshop_add_character_card_messages_exist_in_all_locales():
    required_keys = [
        "workshopAddCharacterCard",
        "workshopAddingCharacterCard",
        "unknownCharacterCard",
        "characterCardAlreadyExistsTitle",
        "characterCardAlreadyExistsMessage",
        "workshopCharacterAdded",
        "workshopCharacterNotFound",
        "workshopCharacterAddFailed",
        "characterCardsRefreshFailed",
    ]
    placeholder_checks = {
        "characterCardAlreadyExistsMessage": "{{names}}",
        "workshopCharacterAdded": "{{names}}",
        "workshopCharacterAddFailed": "{{error}}",
    }
    missing_keys = []
    missing_placeholders = []
    for locale_path in sorted(LOCALE_DIR.glob("*.json")):
        payload = json.loads(locale_path.read_text(encoding="utf-8"))
        steam = payload.get("steam")
        if not isinstance(steam, dict) or any(key not in steam for key in required_keys):
            missing_keys.append(locale_path.name)
            continue
        if any(
            not isinstance(steam.get(key), str) or placeholder not in steam.get(key, "")
            for key, placeholder in placeholder_checks.items()
        ):
            missing_placeholders.append(locale_path.name)

    assert missing_keys == [], f"Missing workshop add-card keys in locale files: {', '.join(missing_keys)}"
    assert missing_placeholders == [], (
        "Missing workshop add-card placeholders in locale files: "
        f"{', '.join(missing_placeholders)}"
    )


def test_card_maker_japanese_sticker_variant_translation_is_consistent():
    payload = json.loads((LOCALE_DIR / "ja.json").read_text(encoding="utf-8"))

    assert payload["cardExport"]["switchStickerVariant"] == "形態を切り替え"
