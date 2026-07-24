import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CARD_MAKER_JS = PROJECT_ROOT / "static" / "js" / "card_maker.js"
CARD_MAKER_CSS = PROJECT_ROOT / "static" / "css" / "card_maker.css"
CHARACTER_CARD_MANAGER_JS_DIR = PROJECT_ROOT / "static" / "js" / "character_card_manager"
MODEL_MANAGER_JS_DIR = PROJECT_ROOT / "static" / "js" / "model_manager"
PNGTUBER_CORE_JS = PROJECT_ROOT / "static" / "pngtuber-core.js"
MODEL_MANAGER_TEMPLATE = PROJECT_ROOT / "templates" / "model_manager.html"
WINDOW_CONTROLS_JS = PROJECT_ROOT / "static" / "js" / "window_controls.js"
CARD_MAKER_TEMPLATE = PROJECT_ROOT / "templates" / "card_maker.html"
LOCALE_DIR = PROJECT_ROOT / "static" / "locales"
CHARACTER_CARD_MANAGER_PART_NAMES = (
    "core-and-upload.js",
    "subscriptions-and-scan.js",
    "character-data-and-transfer.js",
    "card-list-and-panel.js",
    "card-form-and-actions.js",
    "workshop-card-and-upload.js",
    "model-previews.js",
    "master-profile.js",
    "card-companion.js",
    "sync-and-legacy-memory.js",
)
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
    return "".join(
        (MODEL_MANAGER_JS_DIR / part_name).read_text(encoding="utf-8")
        for part_name in MODEL_MANAGER_PART_NAMES
    )


def read_character_card_manager_source() -> str:
    return "".join(
        (CHARACTER_CARD_MANAGER_JS_DIR / part_name).read_text(encoding="utf-8")
        for part_name in CHARACTER_CARD_MANAGER_PART_NAMES
    )


def test_character_card_manager_parts_load_in_dependency_order():
    discovered_names = {path.name for path in CHARACTER_CARD_MANAGER_JS_DIR.glob("*.js")}
    assert discovered_names == set(CHARACTER_CARD_MANAGER_PART_NAMES)

    template = (PROJECT_ROOT / "templates" / "character_card_manager.html").read_text(encoding="utf-8")
    script_positions = [
        template.index(f"/static/js/character_card_manager/{part_name}")
        for part_name in CHARACTER_CARD_MANAGER_PART_NAMES
    ]
    assert script_positions == sorted(script_positions)


def test_character_card_import_keeps_non_blocking_progress_visible_until_completion():
    core = (CHARACTER_CARD_MANAGER_JS_DIR / "core-and-upload.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "static" / "css" / "character_card_manager.css").read_text(encoding="utf-8")
    template = (PROJECT_ROOT / "templates" / "character_card_manager.html").read_text(encoding="utf-8")
    transfer = (CHARACTER_CARD_MANAGER_JS_DIR / "character-data-and-transfer.js").read_text(encoding="utf-8")
    master_profile = (CHARACTER_CARD_MANAGER_JS_DIR / "master-profile.js").read_text(encoding="utf-8")
    previews = (CHARACTER_CARD_MANAGER_JS_DIR / "model-previews.js").read_text(encoding="utf-8")
    workshop = (CHARACTER_CARD_MANAGER_JS_DIR / "workshop-card-and-upload.js").read_text(encoding="utf-8")

    assert template.count('id="message-area"') == 1
    assert template.index('id="message-area"') < template.index('id="uploadToWorkshopModal"')
    assert "messageArea.parentElement !== document.body" in core
    assert "if (type !== 'importing' && type !== 'import-error')" in core
    assert "icon: 'ccm-toast-spinner'" in core
    assert "icon: 'ccm-toast-error-icon'" in core
    assert "@keyframes ccm-toast-spin" in styles
    assert "card.dismiss = dismiss;" in core
    assert "showMessage(loadingText, 'importing', 0)" in transfer
    assert "showMessage(errorText, 'import-error')" in transfer
    assert "function showToast(" not in core
    assert "showAutoSaveToast" not in master_profile
    assert "auto-save-toast" not in template
    assert "auto-save-toast" not in styles
    assert "requestAnimationFrame(() => requestAnimationFrame(resolve))" in transfer
    assert transfer.count("importNotice.dismiss();") == 2
    assert "character.importCardSuccess" not in transfer
    assert "steam.characterCardsRefreshed" not in transfer
    assert "steam.scanningModels" not in workshop
    assert "steam.scanningVoices" not in workshop
    assert "steam.scanComplete" not in workshop
    assert "steam.characterCardLoaded" not in workshop
    assert "live2d.loadingModel" not in previews
    assert "live2d.modelLoadSuccess" not in previews
    assert "steam.vrmPreviewLoaded" not in previews
    assert "steam.mmdPreviewLoaded" not in previews
    assert "steam.live2dPreviewLoaded" not in previews
    assert "character.importCardFailed" in transfer
    assert "steam.voiceScanError" in workshop
    assert "live2d.modelLoadFailed" in previews


def test_workshop_publish_opens_item_in_system_browser():
    core = (CHARACTER_CARD_MANAGER_JS_DIR / "core-and-upload.js").read_text(encoding="utf-8")

    assert "function openPublishedWorkshopItem(webUrl)" in core
    assert "https://steamcommunity.com/sharedfiles/filedetails/?id=${published_id}" in core
    assert "window.electronShell.openExternal(webUrl)" in core
    assert "window.open(webUrl, '_blank', 'noopener,noreferrer')" in core
    assert "openPublishedWorkshopItem(webUrl);" in core
    assert "ActivateGameOverlayToWebPage" not in core
    assert "steam://url/CommunityFilePage" not in core


def test_model_manager_parts_load_in_dependency_order():
    discovered_names = {path.name for path in MODEL_MANAGER_JS_DIR.glob("*.js")}
    assert discovered_names == set(MODEL_MANAGER_PART_NAMES)

    template = MODEL_MANAGER_TEMPLATE.read_text(encoding="utf-8")
    script_positions = [
        template.index(f"/static/js/model_manager/{part_name}")
        for part_name in MODEL_MANAGER_PART_NAMES
    ]
    assert script_positions == sorted(script_positions)

    loaders = (MODEL_MANAGER_JS_DIR / MODEL_MANAGER_PART_NAMES[0]).read_text(encoding="utf-8")
    assert loaders.index("window._vrmModulesLoading = true;") < loaders.index(
        "'/static/vrm/vrm-init.js'"
    )
    assert loaders.index("(async function initVRMModules()") < loaders.index(
        "(async function initMMDModules()"
    )


def test_new_character_auto_card_maker_enables_default_face_fallback_only_for_auto_popup():
    script = read_character_card_manager_source()

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
    script = read_model_manager_source()

    assert "captureDefaultCardFaceModelImage(state, 600, 800)" in script
    assert "800 - Math.floor(800 / 6)" not in script


def test_model_manager_pngtuber_preview_dropdown_uses_i18n_config():
    script = read_model_manager_source()
    start = script.index("buttonId: 'pngtuber-state-preview-select-btn'")
    end = script.index("shouldSkipOption: (option) => !option.value", start)
    config_block = script[start:end]

    assert "defaultTextKey: 'live2d.pngtuberStatePreview'" in config_block
    assert "iconAltKey: 'live2d.pngtuberStatePreview'" in config_block


def test_model_manager_pngtuber_talk_preview_keeps_i18n_after_early_load():
    script = read_model_manager_source()
    update_block = script[
        script.index("function updatePNGTuberTalkPreviewButtonText()"):
        script.index("function refreshLocalizedInteractiveTexts()", script.index("function updatePNGTuberTalkPreviewButtonText()"))
    ]
    refresh_block = script[
        script.index("function refreshLocalizedInteractiveTexts()"):
        script.index("// 动作播放状态", script.index("function refreshLocalizedInteractiveTexts()"))
    ]
    controls_block = script[
        script.index("function clearPNGTuberPreviewControls()"):
        script.index("if (pngtuberTalkPreviewBtn) {", script.index("if (pngtuberTalkPreviewBtn) {") + 1)
    ]

    assert "t('live2d.pngtuberTalkPreview', '测试说话')" in update_block
    assert "setAttribute('data-i18n-title', 'live2d.pngtuberTalkPreview')" in update_block
    assert "setAttribute('data-i18n-aria', 'live2d.pngtuberTalkPreview')" in update_block
    assert "querySelector('[data-i18n=\"live2d.pngtuberTalkPreview\"]')" in update_block
    assert "|| pngtuberTalkPreviewBtn.querySelector('span')" in update_block
    assert "querySelector('[data-i18n=\"live2d.pngtuberTalkPreview\"], span')" not in update_block
    assert "textSpan.setAttribute('data-i18n', 'live2d.pngtuberTalkPreview')" in update_block
    assert "updatePNGTuberTalkPreviewButtonText();" in refresh_block
    assert controls_block.count("updatePNGTuberTalkPreviewButtonText();") >= 1


def test_model_manager_pngtuber_card_face_prefers_visible_drawable():
    script = read_model_manager_source()
    start = script.index("function getPNGTuberCaptureDrawable()")
    end = script.index("async function capturePNGTuberPreviewToCanvas()", start)
    capture_block = script[start:end]

    assert "manager?.image" in capture_block
    assert "drawables.find(isVisiblePNGTuberDrawable)" in capture_block
    assert "document.querySelector('#pngtuber-container canvas.pngtuber-layered-canvas" not in script


def test_model_manager_pngtuber_save_preserves_stored_placement():
    script = read_model_manager_source()
    start = script.index("function mergePNGTuberConfigForSave(")
    end = script.index("async function saveModelToCharacter(", start)
    merge_block = script[start:end]

    assert merge_block.index("currentConfig || {}") < merge_block.index("runtimeConfig || {}")
    assert "runtimeForSave[key] = currentConfig[key];" not in merge_block
    assert "mergePNGTuberConfigForSave(" in script
    assert "runtimePNGTuberConfig || {}" not in script[
        script.index("if (currentModelType === 'pngtuber')") :
        script.index("['adapter', 'layered_metadata', 'source_format', 'source_type']", script.index("if (currentModelType === 'pngtuber')"))
    ]


def test_model_manager_pngtuber_character_config_fallback_loads_preview():
    script = read_model_manager_source()
    timer_decl = "let pngtuberTalkPreviewTimer = null;"
    preview_block = script[
        script.index("async function previewPNGTuberConfig("):
        script.index("async function loadSelectedPNGTuberOption(", script.index("async function previewPNGTuberConfig("))
    ]
    select_block = script[
        script.index("async function selectAndPreviewFirstPNGTuberModelAfterModeSwitch("):
        script.index("function rememberSelectedPNGTuberModel(", script.index("async function selectAndPreviewFirstPNGTuberModelAfterModeSwitch("))
    ]
    current_character_block = script[
        script.index("if (modelType === 'pngtuber' && hasValidPNGTuber)"):
        script.index("if (modelType === 'live3d' && !hasValidVRMPath", script.index("if (modelType === 'pngtuber' && hasValidPNGTuber)"))
    ]

    assert script.index(timer_decl) < script.index("await switchModelDisplay(savedModelType, savedSubType);")
    assert script.count(timer_decl) == 1
    # 单写入者纪律：旗标只由 switchModelDisplay() 维护（恒等于当前真实 model type）；
    # previewPNGTuberConfig 不再写它，避免在非 pngtuber 页面被误置而让 live2d-init 跳过 Live2D/VRM 初始化
    assert "window._modelManagerCurrentAvatarType =" not in preview_block
    assert script.count("window._modelManagerCurrentAvatarType =") == 1
    assert "window._modelManagerCurrentAvatarType = type;" in script
    assert "window.lanlan_config.model_type = 'pngtuber';" not in preview_block
    assert "window.lanlan_config.pngtuber = Object.assign({}, pngtuberConfig);" not in preview_block
    assert "if (!pngtuberConfig || !pngtuberConfig.idle_image) return false;" in preview_block
    assert "await window.loadPNGTuberAvatar(pngtuberConfig);" in preview_block
    assert "throw new Error('PNGTuber runtime not loaded');" in preview_block
    assert "if (preferredConfig) {" in select_block
    assert "return await previewPNGTuberConfig(preferredConfig" in select_block
    assert "if (preferredConfig) return false;" not in select_block
    assert "await previewPNGTuberConfig(pngtuberConfig, {" in current_character_block


def test_pngtuber_model_manager_preview_does_not_auto_save():
    script = PNGTUBER_CORE_JS.read_text(encoding="utf-8")
    sync_block = script[
        script.index("syncGlobalConfig() {"):
        script.index("setLocked(locked", script.index("syncGlobalConfig() {"))
    ]
    save_block = script[
        script.index("async saveCurrentConfig() {"):
        script.index("scheduleSaveCurrentConfig", script.index("async saveCurrentConfig() {"))
    ]

    assert "if (isModelManagerPage()) return;" in sync_block
    assert "if (isModelManagerPage()) return false;" in save_block
    assert save_block.index("if (isModelManagerPage()) return false;") < save_block.index("fetch(`/api/characters/catgirl/l2d/")
def test_model_manager_pngtuber_upload_supports_project_file_without_removing_folder_upload():
    script = read_model_manager_source()
    template = MODEL_MANAGER_TEMPLATE.read_text(encoding="utf-8")

    assert 'id="pngtuber-model-upload" webkitdirectory directory multiple' in template
    assert 'id="pngtuber-package-upload" accept=".pngRemix,.pngremix,.save"' in template
    assert ".veadomini" not in template
    assert ".veado" not in template
    assert "const pngtuberPackageUpload = document.getElementById('pngtuber-package-upload');" in script
    assert "showPNGTuberUploadChoice()" in script
    assert "async function uploadPNGTuberFiles(files, inputElement = null)" in script
    assert "await uploadPNGTuberFiles(e.target.files, pngtuberModelUpload);" in script
    assert "await uploadPNGTuberFiles(e.target.files, pngtuberPackageUpload);" in script
    assert "inputElement.value = '';" in script
    assert "menu.addEventListener('keydown', handlePNGTuberUploadChoiceKeydown);" in script
    assert "menu.addEventListener('focusout', handlePNGTuberUploadChoiceFocusout);" in script
    assert "let pngtuberUploadChoiceOpeningPicker = false;" in script
    assert "if (pngtuberUploadChoiceOpeningPicker) return;" in script
    assert "menu.parentNode.removeChild(menu);" in script
    assert "pngtuberUploadChoiceMenu.remove();" not in script
    choice_item_block = script[
        script.index("function createPNGTuberUploadChoiceItem"):
        script.index("function showPNGTuberUploadChoice")
    ]
    assert re.search(
        r"try\s*\{\s*onSelect\(\);\s*\}\s*finally\s*\{\s*setTimeout\(\(\)\s*=>\s*\{\s*"
        r"pngtuberUploadChoiceOpeningPicker\s*=\s*false;\s*closePNGTuberUploadChoice\(\);\s*"
        r"\},\s*0\);\s*\}",
        choice_item_block,
    )
    assert "event.key === 'Escape'" in script
    assert "window.t('live2d.pngtuberImportProjectFile')" in script
    assert "window.t('live2d.pngtuberImportFolder')" in script
    assert "pngtuberPackageUpload.click();" in script
    assert "pngtuberModelUpload.click();" in script


def test_card_maker_rejects_remote_pngtuber_assets_before_export():
    script = CARD_MAKER_JS.read_text(encoding="utf-8")

    assert "function assertExportablePNGTuberConfig(config)" in script
    assert "remote_pngtuber_export_unsupported" in script
    assert "assertExportablePNGTuberConfig(pngtuberConfig);" in script
    assert "function assertExportablePNGTuberDrawable(source)" in script
    assert "assertExportablePNGTuberDrawable(source);" in script


def test_model_manager_parameter_save_restores_unsaved_and_offers_card_face():
    script = read_model_manager_source()
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
        "lollipop-primary-icon.png",
        "lollipop-tertiary-icon.png",
        "hammer-primary-icon.png",
        "hammer-secondary-icon.png",
        "fist-reward-drop.png",
        "fist-primary-icon.png",
        "fist-secondary-icon.png",
    ]:
        assert sticker_name in script
    assert "/static/assets/avatar-tools/lollipop/primary-icon.png" in script
    assert "/static/assets/avatar-tools/lollipop/tertiary-icon.png" in script
    assert "/static/assets/avatar-tools/hammer/primary-icon.png" in script
    assert "/static/assets/avatar-tools/hammer/secondary-icon.png" in script
    assert "/static/assets/avatar-tools/fist/reward-drop.png" in script
    assert "/static/assets/avatar-tools/fist/primary-icon.png" in script
    assert "/static/assets/avatar-tools/fist/secondary-icon.png" in script
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
