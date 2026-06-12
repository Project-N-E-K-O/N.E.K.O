import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read(rel_path: str) -> str:
    return (PROJECT_ROOT / rel_path).read_text(encoding="utf-8")


def _css_block(css: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{(?P<body>.*?)\}}", css, re.S)
    assert match, f"Missing CSS block for {selector}"
    return match.group("body")


def test_pngtuber_backend_contracts_are_registered():
    config_manager = _read("utils/config_manager.py")
    main_server = _read("app/main_server.py")
    config_router = _read("main_routers/config_router.py")
    characters_router = _read("main_routers/characters_router.py")
    pngtuber_router = _read("main_routers/pngtuber_router.py")

    assert "self.pngtuber_dir = self.app_docs_dir / \"pngtuber\"" in config_manager
    assert "def ensure_pngtuber_directory" in config_manager
    assert "ensure_pngtuber_directory()" in main_server
    assert "app.mount(\"/user_pngtuber\"" in main_server
    assert "from main_routers.pngtuber_router import router as pngtuber_router" in main_server
    assert "app.include_router(pngtuber_router)" in main_server
    assert "PNGTUBER_USER_PATH = \"/user_pngtuber\"" in config_router
    assert "_resolve_pngtuber_image_path" in config_router
    assert "result[\"pngtuber\"]" in config_router
    assert "model_type_str not in ['live2d', 'vrm', 'live3d', 'pngtuber']" in characters_router
    assert "set_reserved(characters['猫娘'][name], 'avatar', 'model_type', 'pngtuber')" in characters_router
    assert "@router.post(\"/upload_model\")" in pngtuber_router
    assert "@router.get(\"/models\")" in pngtuber_router
    assert "@router.delete(\"/model\")" in pngtuber_router
    assert "model.json" in pngtuber_router


def test_pngtuber_frontend_runtime_is_wired_to_main_page():
    index_template = _read("templates/index.html")
    index_js = _read("static/js/index.js")
    app_character = _read("static/app-character.js")
    app_interpage = _read("static/app-interpage.js")
    runtime = _read("static/pngtuber-core.js")
    css = _read("static/css/index.css")

    assert 'id="pngtuber-container"' in index_template
    assert '/static/pngtuber-core.js' in index_template
    assert "window.PNGTuberManager = PNGTuberManager" in runtime
    assert "window.loadPNGTuberAvatar = loadPNGTuberAvatar" in runtime
    assert "drag_image" in runtime
    assert "click_image" in runtime
    assert "showDragImage()" in runtime
    assert "showClickImage()" in runtime
    assert "restoreStateImage()" in runtime
    assert "handleClick(event)" in runtime
    assert "attachDragListeners()" in runtime
    assert "detachDragListeners()" in runtime
    assert "saveCurrentConfig()" in runtime
    assert "handleWheelZoom(event)" in runtime
    assert "startTouchZoom(event)" in runtime
    assert "moveTouchZoom(event)" in runtime
    assert "getTouchCenter(touch1, touch2)" in runtime
    assert "setLocked(locked" in runtime
    assert "setupHTMLLockIcon()" in runtime
    assert "updateLockIconPosition()" in runtime
    assert "!document.getElementById('chat-container') || window.isViewerMode" in runtime
    assert "AvatarButtonMixin.apply(PNGTuberManager.prototype, 'pngtuber'" in runtime
    assert "AvatarPopupMixin.apply(PNGTuberManager.prototype, 'pngtuber'" in runtime
    assert "PNGTuberManager.prototype.setupFloatingButtons" in runtime
    assert "this.updateFloatingButtonsPosition" in runtime
    assert "pngtuber-floating-buttons" in runtime
    assert "pngtuber-model-loaded" in runtime
    assert "modelType === 'pngtuber'" in index_js
    assert "window.loadPNGTuberAvatar(lanlan_config.pngtuber" in index_js
    assert "effectiveModelType === 'pngtuber'" in app_character
    assert "window.loadPNGTuberAvatar(pngtuberConfig)" in app_character
    assert "newModelType === 'pngtuber'" in app_interpage
    assert "await window.loadPNGTuberAvatar(pngtuberConfig)" in app_interpage
    assert "#pngtuber-container" in css


def test_pngtuber_speech_uses_mouth_flap_animation():
    runtime = _read("static/pngtuber-core.js")

    assert "this.isSpeaking = false;" in runtime
    assert "this.speakingMouthTimer = null;" in runtime
    assert "scheduleSpeakingMouthFrame()" in runtime
    assert "startSpeakingMouthAnimation()" in runtime
    assert "stopSpeakingMouthAnimation()" in runtime
    assert "this.speakingMouthOpen = !this.speakingMouthOpen;" in runtime
    assert "this.setState(this.speakingMouthOpen ? 'talking' : 'idle');" in runtime
    assert "this.scheduleSpeakingMouthFrame();" in runtime

    set_speaking_start = runtime.index("setSpeaking(isSpeaking)")
    set_speaking_body = runtime[set_speaking_start:runtime.index("show()", set_speaking_start)]
    assert "this.startSpeakingMouthAnimation();" in set_speaking_body
    assert "this.stopSpeakingMouthAnimation();" in set_speaking_body

    hide_start = runtime.index("hide()")
    hide_body = runtime[hide_start:runtime.index("dispose()", hide_start)]
    assert "this.stopSpeakingMouthAnimation();" in hide_body


def test_pngtuber_remix_one_bounce_animation_is_wired():
    runtime = _read("static/pngtuber-core.js")

    assert "speakingBounceConfig()" in runtime
    assert "current_mo_anim" in runtime
    assert "mouthAnimation.includes('bounce')" in runtime
    assert "settings?.bounceGravity" in runtime
    assert "settings?.bounceSlider" in runtime
    assert "currentSpeakingBounceTransform(" in runtime
    assert "startSpeakingBounceAnimation()" in runtime
    assert "stopSpeakingBounceAnimation()" in runtime
    assert "this.startSpeakingBounceAnimation();" in runtime
    assert "this.config.offset_y + bounce.y" in runtime
    assert "scale(${finalScaleX}, ${finalScaleY})" in runtime


def test_pngtuber_debug_state_api_is_wired():
    runtime = _read("static/pngtuber-core.js")

    assert "getDebugState()" in runtime
    assert "renderedLayerCountForState(stateName)" in runtime
    assert "renderedIdleLayerCount" in runtime
    assert "renderedTalkingLayerCount" in runtime
    assert "currentMoAnim" in runtime
    assert "currentMcAnim" in runtime
    assert "bounceActive" in runtime
    assert "bounceProgress" in runtime
    assert "mouthTimer" in runtime
    assert "bounceFrame" in runtime
    assert "layeredAnimationFrame" in runtime
    assert "container: {" in runtime
    assert "image: {" in runtime

    debug_start = runtime.index("getDebugState()")
    debug_end = runtime.index("setSpeaking(isSpeaking)", debug_start)
    debug_body = runtime[debug_start:debug_end]
    assert "this.setState(" not in debug_body
    assert "this.setSpeaking(" not in debug_body
    assert "this.startSpeaking" not in debug_body
    assert "requestAnimationFrame" not in debug_body
    assert "setTimeout" not in debug_body


def test_pngtuber_main_page_paths_do_not_fall_back_to_live2d():
    index_js = _read("static/js/index.js")
    app_interpage = _read("static/app-interpage.js")
    app_character = _read("static/app-character.js")
    live2d_init = _read("static/live2d-init.js")
    live2d_model = _read("static/live2d-model.js")
    pngtuber_runtime = _read("static/pngtuber-core.js")

    assert "const modelType = (data.model_type || 'live2d').toLowerCase()" in index_js
    assert "if (modelType === 'pngtuber')" in index_js
    assert "window.cubism4Model = \"\"" in index_js
    assert "window.loadPNGTuberAvatar(lanlan_config.pngtuber" in index_js

    assert "var newModelType = (data.model_type || 'live2d').toLowerCase()" in app_interpage
    assert "newModelType === 'pngtuber'" in app_interpage
    assert "window.cubism4Model = '';" in app_interpage
    assert "await window.loadPNGTuberAvatar(pngtuberConfig)" in app_interpage

    assert "let effectiveModelType = modelType" in app_character
    assert "effectiveModelType === 'pngtuber'" in app_character
    assert "window.lanlan_config.model_type = 'pngtuber'" in app_character
    assert "await window.loadPNGTuberAvatar(pngtuberConfig)" in app_character

    assert "(window.lanlan_config?.model_type || '').toLowerCase() === 'pngtuber'" in live2d_init
    assert "return;" in live2d_init

    assert "await window.live2dManager.removeModel({ skipCloseWindows: true });" in pngtuber_runtime
    assert "window.live2dManager._activeLoadToken = (window.live2dManager._activeLoadToken || 0) + 1;" in pngtuber_runtime
    assert "const isPNGTuberPageMode = (window.lanlan_config?.model_type || '').toLowerCase() === 'pngtuber';" in live2d_model
    assert "skipError.name = 'PNGTuberActiveLive2DSkip';" in live2d_model
    assert "cancelError.name = 'PNGTuberActiveLive2DSkip';" in live2d_model
    assert "不回退默认模型" in live2d_model


def test_pngtuber_backend_response_paths_preserve_model_type():
    config_router = _read("main_routers/config_router.py")
    characters_router = _read("main_routers/characters_router.py")
    config_manager = _read("utils/config_manager.py")

    assert "if model_type == 'pngtuber':" in config_router
    assert "raw_pngtuber = get_reserved(catgirl_config, 'avatar', 'pngtuber', default={})" in config_router
    assert "model_path = pngtuber_config.get('idle_image', '') or ''" in config_router
    assert "if model_type == 'pngtuber':\n            result[\"pngtuber\"] = pngtuber_config or {}" in config_router

    assert "for cat_name, cat_data in list(" in characters_router
    assert "_flatten_catgirl_for_response(cat_data)" in characters_router
    assert "model_type = get_reserved(result, \"avatar\", \"model_type\", default=\"live2d\")" in config_manager
    assert "result[\"model_type\"] = model_type" in config_manager
    assert "result[\"pngtuber\"] = dict(pngtuber_config)" in config_manager


def test_pngtuber_model_manager_import_is_wired():
    template = _read("templates/model_manager.html")
    model_manager = _read("static/js/model_manager.js")
    css = _read("static/css/model_manager.css")

    assert 'id="pngtuber-model-upload"' in template
    assert '<body class="model-manager-page">' in template
    assert 'id="pngtuber-model-select"' not in template
    assert 'id="pngtuber-model-group"' not in template
    assert 'id="model-select"' in template
    assert 'id="pngtuber-container"' in template
    assert '/static/pngtuber-core.js' in template
    assert "loadPNGTuberModels" in model_manager
    assert "/api/model/pngtuber/upload_model" in model_manager
    assert "/api/model/pngtuber/models" in model_manager
    assert "/api/model/pngtuber/model" in model_manager
    assert "option.setAttribute('data-model-type', 'pngtuber')" in model_manager
    assert "modelSelect.appendChild(option)" in model_manager
    assert "JSON.parse(selectedOption.getAttribute('data-pngtuber')" in model_manager
    assert "modelData.pngtuber" in model_manager
    assert "currentModelType === 'pngtuber'" in model_manager
    assert "window.loadPNGTuberAvatar" in model_manager
    assert "pngtuberModelSelect" not in model_manager
    assert "pngtuberModelManager" not in model_manager
    assert "pngtuberModelDropdown" not in model_manager
    assert "#pngtuber-container" in css
    assert "body.model-manager-page #pngtuber-container .pngtuber-image" in css
    assert "transform: translate(-50%, -50%)" in css


def test_pngtuber_runtime_crops_sprite_sheets_without_implicit_animation():
    pngtuber_core = _read("static/pngtuber-core.js")

    assert "stateFrameInfo(layer, layerState, img" in pngtuber_core
    assert "const hasSheet = hframes > 1 || rows > 1;" in pngtuber_core
    assert "(hasSheet ? computedFrameWidth : layerWidth)" in pngtuber_core
    assert "(hasSheet ? computedFrameHeight : layerHeight)" in pngtuber_core
    assert "legacyOffsetX: legacyFullSheetX ? (imageWidth - frameWidth) / 2 : 0" in pngtuber_core
    assert "legacyOffsetY: legacyFullSheetY ? (imageHeight - frameHeight) / 2 : 0" in pngtuber_core
    assert "+ frame.legacyOffsetX" in pngtuber_core
    assert "+ frame.legacyOffsetY" in pngtuber_core
    assert "REMIX_FRAME_SPEED_MULTIPLIER" in pngtuber_core
    assert "speed * REMIX_FRAME_SPEED_MULTIPLIER" in pngtuber_core
    assert "layerState.non_animated_sheet !== true" in pngtuber_core
    assert "ctx.drawImage(\n                        img,\n                        frame.sx,\n                        frame.sy,\n                        frame.sw,\n                        frame.sh," in pngtuber_core


def test_pngtuber_model_manager_grouped_preview_controls_are_wired():
    template = _read("templates/model_manager.html")
    model_manager = _read("static/js/model_manager.js")
    css = _read("static/css/model_manager.css")

    assert 'id="pngtuber-preview-group"' in template
    assert 'id="pngtuber-basic-preview-section"' in template
    assert 'id="pngtuber-talk-preview-btn"' in template
    assert 'id="pngtuber-state-preview-section"' in template
    assert 'id="pngtuber-state-preview-list"' in template
    assert 'id="pngtuber-state-preview-select-btn"' in template
    assert 'id="pngtuber-state-preview-select"' in template
    assert 'id="pngtuber-state-preview-dropdown"' in template
    assert "测试说话" in template

    assert "function clearPNGTuberPreviewControls()" in model_manager
    assert "async function loadPNGTuberPreviewControls(pngtuberConfig)" in model_manager
    assert "async function fetchPNGTuberLayeredMetadata(pngtuberConfig)" in model_manager
    assert "function renderPNGTuberStatePreviewDropdown(metadata)" in model_manager
    assert "window.pngtuberManager.setSpeaking(true);" in model_manager
    assert "window.pngtuberManager.setSpeaking(false);" in model_manager
    assert "}, 1800);" in model_manager
    assert "pngtuberStatePreviewSelect.addEventListener('change'" in model_manager
    assert "window.playPNGTuberAnimation(stateNumber);" in model_manager
    assert "pngtuberStatePreviewManager = new DropdownManager" in model_manager
    assert "defaultText: '状态预览'" in model_manager
    assert "alwaysShowDefault: true" in model_manager
    assert "updatePNGTuberStatePreviewButtonText();" in model_manager
    assert "pngtuber-layered-state-changed" in model_manager
    assert "await loadPNGTuberPreviewControls(pngtuberConfig);" in model_manager
    assert "#pngtuber-talk-preview-btn," in css
    assert "#pngtuber-state-preview-select-btn" in css
    assert ".pngtuber-state-preview-dropdown" in css
    assert ".pngtuber-state-preview-dropdown .dropdown-item" in css
    assert "background: url('/static/icons/bar_bg_2.png')" in css
    assert "width: var(--model-manager-control-width) !important;" in css

    switch_start = model_manager.index("async function switchModelDisplay(type, subType)")
    pngtuber_branch = model_manager.index("if (type === 'pngtuber')", switch_start)
    live2d_branch = model_manager.index("} else if (type === 'live2d')", pngtuber_branch)
    vrm_branch = model_manager.index("} else { // VRM", live2d_branch)
    pngtuber_body = model_manager[pngtuber_branch:live2d_branch]
    live2d_body = model_manager[live2d_branch:vrm_branch]
    vrm_body = model_manager[vrm_branch:model_manager.index("// =====================================================================", vrm_branch)]

    assert "if (pngtuberPreviewGroup) pngtuberPreviewGroup.style.display = 'flex';" in pngtuber_body
    assert "clearPNGTuberPreviewControls();" in live2d_body
    assert "group.id !== 'pngtuber-preview-group'" in live2d_body
    assert "if (pngtuberPreviewGroup) pngtuberPreviewGroup.style.display = 'none';" in live2d_body
    assert "clearPNGTuberPreviewControls();" in vrm_body


def test_pngtuber_model_selection_enables_save_button():
    model_manager = _read("static/js/model_manager.js")

    listener_start = model_manager.index("modelSelect.addEventListener('change'")
    listener_end = model_manager.index("// 检查语音模式状态", listener_start)
    listener = model_manager[listener_start:listener_end]

    match = re.search(
        r"if \(currentModelType === 'pngtuber'\) \{(?P<body>.*?)\n\s*return;\n\s*\}",
        listener,
        re.S,
    )
    assert match, "Missing PNGTuber model-select branch"
    branch = match.group("body")

    assert "await loadSelectedPNGTuberOption(selectedOption" in branch
    assert "markDirty: !isSuppressedModelManagerChangeEvent(e)" in branch

    loader_start = model_manager.index("async function loadSelectedPNGTuberOption(selectedOption, options = {})")
    helper_start = model_manager.index("async function selectAndPreviewFirstPNGTuberModelAfterModeSwitch()")
    loader_body = model_manager[loader_start:helper_start]
    assert "if (options.markDirty)" in loader_body
    assert "window.hasUnsavedChanges = true;" in loader_body
    assert "savePositionBtn.disabled = false;" in loader_body
    assert "markModelChangedForCardFacePrompt();" in loader_body


def test_pngtuber_save_uses_live2d_save_success_prompt():
    model_manager = _read("static/js/model_manager.js")

    save_start = model_manager.index("savePositionBtn.addEventListener('click'")
    pngtuber_branch = model_manager.index("if (currentModelType === 'pngtuber')", save_start)
    live3d_branch = model_manager.index("} else if (currentModelType === 'live3d')", pngtuber_branch)
    live2d_branch = model_manager.index("} else {", live3d_branch)
    pngtuber_body = model_manager[pngtuber_branch:live3d_branch]
    live3d_body = model_manager[live3d_branch:live2d_branch]

    assert "位置和模型设置保存成功!" in pngtuber_body
    assert "showModelManagerToast(message, 2600, 'success');" in pngtuber_body
    assert "window.hasUnsavedChanges = false;" in pngtuber_body
    assert "模型设置保存成功!" in live3d_body


def test_pngtuber_save_merges_runtime_config_last():
    model_manager = _read("static/js/model_manager.js")

    save_start = model_manager.index("async function saveModelToCharacter")
    pngtuber_branch = model_manager.index("if (currentModelType === 'pngtuber')", save_start)
    live3d_branch = model_manager.index("} else if (currentModelType === 'live3d')", pngtuber_branch)
    pngtuber_body = model_manager[pngtuber_branch:live3d_branch]

    selected_idx = pngtuber_body.index("selectedPNGTuberConfig || {}")
    current_idx = pngtuber_body.index("currentPNGTuberConfig || {}")
    runtime_idx = pngtuber_body.index("runtimePNGTuberConfig || {}")

    assert "window.pngtuberManager.config" in pngtuber_body
    assert selected_idx < current_idx < runtime_idx
    assert "modelData.pngtuber = pngtuberConfig;" in pngtuber_body
    assert "'adapter', 'layered_metadata', 'source_format', 'source_type'" in pngtuber_body
    assert "currentPNGTuberConfig && currentPNGTuberConfig[key]" in pngtuber_body


def test_live2d_mode_switch_reloads_selected_live2d_model():
    model_manager = _read("static/js/model_manager.js")

    helper_start = model_manager.index("async function reloadSelectedLive2DModelAfterModeSwitch()")
    helper_end = model_manager.index("// 先加载模型列表", helper_start)
    helper_body = model_manager[helper_start:helper_end]

    assert "currentModelType !== 'live2d'" in helper_body
    assert "selectedOption.dataset.modelType !== 'live2d'" in helper_body
    assert "option.dataset.modelType === 'live2d'" in helper_body
    assert "modelSelect.value = selectedOption.value;" in helper_body
    assert "updateLive2DModelDropdown();" in helper_body
    assert "updateLive2DModelSelectButtonText();" in helper_body
    assert "await loadSelectedPNGTuberOption(selectedOption, { markDirty: false });" in helper_body

    switch_start = model_manager.index("async function switchModelDisplay(type, subType)")
    pngtuber_branch = model_manager.index("if (type === 'pngtuber')", switch_start)
    live2d_branch = model_manager.index("} else if (type === 'live2d')", pngtuber_branch)
    vrm_branch = model_manager.index("} else { // VRM", live2d_branch)
    switch_header = model_manager[switch_start:pngtuber_branch]
    live2d_body = model_manager[live2d_branch:vrm_branch]

    assert "const previousModelType = currentModelType;" in switch_header
    assert "window._modelManagerCurrentAvatarType = type;" in switch_header
    assert "await loadLive2DModelOptions({ showLoadedStatus: false });" in live2d_body
    assert "window.pngtuberManager.hide()" in live2d_body
    assert "live2dContainer.classList.remove('hidden');" in live2d_body
    assert "live2dContainer.style.visibility = 'visible';" in live2d_body
    assert "live2dCanvas.style.visibility = 'visible';" in live2d_body
    assert "live2dCanvas.style.pointerEvents = 'auto';" in live2d_body
    assert "await window.live2dManager.ensurePIXIReady('live2d-canvas', 'live2d-container');" in live2d_body
    assert "if (previousModelType !== 'live2d')" in live2d_body
    assert "await reloadSelectedLive2DModelAfterModeSwitch();" in live2d_body


def test_pngtuber_mode_switch_auto_previews_selected_model():
    model_manager = _read("static/js/model_manager.js")

    loader_start = model_manager.index("async function loadSelectedPNGTuberOption(selectedOption, options = {})")
    helper_start = model_manager.index("async function selectAndPreviewFirstPNGTuberModelAfterModeSwitch()")
    loader_body = model_manager[loader_start:helper_start]
    helper_end = model_manager.index("function rememberSelectedPNGTuberModel", helper_start)
    helper_body = model_manager[helper_start:helper_end]
    remember_start = helper_end
    remember_end = model_manager.index("// 先加载模型列表", remember_start)
    remember_body = model_manager[remember_start:remember_end]

    assert "currentModelType !== 'pngtuber'" in helper_body
    assert "localStorage.getItem('lastPNGTuberModelSelection')" in helper_body
    assert "const rememberedOption = findRememberedOption();" in helper_body
    assert "modelSelect.value = rememberedOption.value;" in helper_body
    assert "selectedOption.dataset.modelType !== 'pngtuber'" in helper_body
    assert "option.dataset.modelType === 'pngtuber'" in helper_body
    assert "modelSelect.value = selectedOption.value;" in helper_body
    assert "await loadSelectedPNGTuberOption(selectedOption, { markDirty: false });" in helper_body
    assert "dispatchModelManagerChange(modelSelect, { suppress: true });" not in helper_body
    assert "await window.loadPNGTuberAvatar(pngtuberConfig);" in loader_body
    assert "await loadPNGTuberPreviewControls(pngtuberConfig);" in loader_body
    assert "if (options.markDirty)" in loader_body
    assert "localStorage.setItem('lastPNGTuberModelSelection'" in remember_body
    assert "idle_image" in remember_body

    switch_start = model_manager.index("async function switchModelDisplay(type, subType)")
    pngtuber_branch = model_manager.index("if (type === 'pngtuber')", switch_start)
    live2d_branch = model_manager.index("} else if (type === 'live2d')", pngtuber_branch)
    pngtuber_body = model_manager[pngtuber_branch:live2d_branch]

    assert "await loadPNGTuberModels();" in pngtuber_body
    assert "await selectAndPreviewFirstPNGTuberModelAfterModeSwitch();" in pngtuber_body
    assert "rememberSelectedPNGTuberModel(matchedOption, pngtuberConfig);" in model_manager

    model_select_start = model_manager.index("modelSelect.addEventListener('change'")
    model_select_end = model_manager.index("// 检查语音模式状态", model_select_start)
    model_select_pngtuber_body = model_manager[model_select_start:model_select_end]
    assert "await loadSelectedPNGTuberOption(selectedOption" in model_select_pngtuber_body
    assert "markDirty: !isSuppressedModelManagerChangeEvent(e)" in model_select_pngtuber_body


def test_pngtuber_async_load_cannot_hide_live2d_after_mode_switch():
    runtime = _read("static/pngtuber-core.js")

    helper_start = runtime.index("function hideOtherAvatarRuntimesForPNGTuber()")
    helper_end = runtime.index("async function loadPNGTuberAvatar(config)", helper_start)
    helper_body = runtime[helper_start:helper_end]
    load_body = runtime[helper_end:]

    assert "document.body?.classList.contains('model-manager-page')" in helper_body
    assert "window._modelManagerCurrentAvatarType !== 'pngtuber'" in helper_body
    assert "return;" in helper_body
    assert "window._modelManagerCurrentAvatarType !== 'pngtuber'" in load_body
    assert "window.pngtuberManager.hide();" in load_body
    assert "return window.pngtuberManager;" in load_body


def test_pngtuber_runtime_recognizes_model_manager_without_url_dependency():
    runtime = _read("static/pngtuber-core.js")

    assert "document.body?.classList.contains('model-manager-page')" in runtime
    assert "document.getElementById('vrm-model-select') !== null" in runtime
    assert "const centerPreview = isModelManagerPage() && !source.preserve_model_manager_position" in runtime
    assert "normalized.offset_x = centerPreview ? 0" in runtime
    assert "normalized.offset_y = centerPreview ? 0" in runtime
    assert "? 'translate(-50%, -50%)'" in runtime
    assert ": 'translate(-100%, -100%)'" in runtime


def test_pngtuber_model_manager_preview_is_centered():
    css = _read("static/css/model_manager.css")
    model_manager = _read("static/js/model_manager.js")
    runtime = _read("static/pngtuber-core.js")

    stage_block = _css_block(css, "#pngtuber-container")
    assert "position: fixed;" in stage_block
    assert "right: 0px;" in stage_block
    assert "bottom: 0px;" in stage_block
    assert "width: 100%;" in stage_block
    assert "height: 100%;" in stage_block
    assert "display: block;" in stage_block

    image_block = _css_block(css, "#pngtuber-container .pngtuber-image")
    assert "position: absolute;" in image_block
    assert "left: 50%;" in image_block
    assert "top: 50%;" in image_block
    assert "right: auto;" in image_block
    assert "bottom: auto;" in image_block
    assert "transform-origin: center center;" in image_block
    assert "align-items: flex-end;" not in stage_block

    assert "window.location.pathname.includes('model_manager')" in runtime
    assert "function isModelManagerPage()" in runtime
    assert "function canInteractWithAvatar()" in runtime
    assert "if (isModelManagerPage()) return true;" in runtime
    assert "? 'translate(-50%, -50%)'" in runtime
    assert ": 'translate(-100%, -100%)'" in runtime
    assert "left: '50%'" in runtime
    assert "top: '50%'" in runtime
    assert "transformOrigin: 'center center'" in runtime
    assert "if (!canInteractWithAvatar()) return;" in runtime
    assert "pngtuberContainer.style.display = 'block';" in model_manager
    assert "pngtuberContainer.style.display = 'flex';" not in model_manager


def test_pngtuber_homepage_floating_buttons_are_reused():
    runtime = _read("static/pngtuber-core.js")
    avatar_buttons = _read("static/avatar-ui-buttons.js")
    app_interpage = _read("static/app-interpage.js")
    tutorial = _read("static/universal-tutorial-manager.js")

    assert "this.setupFloatingButtonsBase()" in runtime
    assert "this.getDefaultButtonConfigs()" in runtime
    assert "this.createButtonElement(config, buttonsContainer)" in runtime
    assert "this.createPopup(config.id)" in runtime
    assert "const buttonWidth = 82;" in runtime
    assert "const targetX = rect.right * 0.8 + rect.left * 0.2;" in runtime
    assert "window.innerWidth - buttonWidth - 12" in runtime
    assert "if (typeof this.updateFloatingButtonsPosition === 'function')" in runtime
    assert "window.dispatchEvent(new CustomEvent(`live2d-${config.id}-toggle`" in runtime
    assert "window.dispatchEvent(new CustomEvent('live2d-goodbye-click'))" in runtime
    assert "pngtuber-return-button-container" in runtime
    assert "pngtuber-btn-return" in runtime
    assert "pngtuber-lock-icon" in runtime
    assert "this.setLocked(!this.isLocked)" in runtime
    assert "this.updateLockIconPosition();" in runtime
    assert "const lockGap = 28;" in runtime
    assert "const lockVerticalGap = 80;" in runtime
    assert "const targetX = rect.right * 0.7 + rect.left * 0.3 + lockGap;" in runtime
    assert "const targetY = rect.top * 0.3 + rect.bottom * 0.7 + lockVerticalGap;" in runtime
    assert "document.querySelectorAll('[id^=\"pngtuber-popup-\"]')" in runtime
    assert "lockIcon.style.opacity = shouldFade ? '0.12' : (isOverlapped ? '0.3' : '');" in runtime

    assert "'pngtuber-floating-buttons', 'pngtuber-lock-icon', 'pngtuber-return-button-container'" in avatar_buttons
    assert "['live2d', 'vrm', 'mmd', 'pngtuber']" in avatar_buttons
    assert "window.pngtuberManager" in avatar_buttons

    assert "body.neko-main-ui-hidden-by-model-manager #pngtuber-floating-buttons," in app_interpage
    assert "body.neko-main-ui-hidden-by-model-manager #pngtuber-lock-icon," in app_interpage
    assert "body.neko-main-ui-hidden-by-model-manager #pngtuber-return-button-container" in app_interpage
    assert "cleanupPNGTuberOverlayUI" in app_interpage

    assert "document.getElementById('pngtuber-floating-buttons')" in tutorial
    assert "['live2d', 'vrm', 'mmd', 'pngtuber']" in tutorial


def test_pngtuber_homepage_position_and_hide_contracts():
    css = _read("static/css/index.css")
    app_interpage = _read("static/app-interpage.js")

    stage_block = _css_block(css, "#pngtuber-container")
    assert "position: fixed;" in stage_block
    assert "right: 0;" in stage_block
    assert "bottom: 0;" in stage_block
    assert "width: 100%;" in stage_block
    assert "height: 100%;" in stage_block
    assert "z-index: 10;" in stage_block
    assert "pointer-events: none;" in stage_block
    assert "background: transparent !important;" in stage_block

    image_block = _css_block(css, "#pngtuber-container .pngtuber-image")
    assert "position: absolute;" in image_block
    assert "left: calc(100% - 48px);" in image_block
    assert "top: calc(100% - 18px);" in image_block
    assert "right: auto;" in image_block
    assert "bottom: auto;" in image_block
    assert "right: 5vw;" not in image_block
    assert "max-width: min(56vw, 560px);" in image_block
    assert "max-height: 82vh;" in image_block
    assert "object-fit: contain;" in image_block
    assert "pointer-events: auto;" in image_block
    assert "touch-action: none;" in image_block
    assert "cursor: grab;" in image_block
    assert "transform-origin: right bottom;" in image_block
    runtime = _read("static/pngtuber-core.js")
    assert "translate(-100%, -100%)" in runtime
    assert "document.body.classList.add('neko-model-dragging')" in runtime
    assert "document.body.classList.remove('neko-model-dragging')" in runtime
    assert "this.config.offset_x" in runtime
    assert "this.config.offset_y" in runtime
    assert "this.config.scale" in runtime
    assert "this.showDragImage();" in runtime
    assert "this.showClickImage();" in runtime
    assert "this.restoreStateImage();" in runtime
    assert "this.image.addEventListener('click', this._boundClick);" in runtime
    assert "this._suppressNextClick = true;" in runtime
    assert "startCenterX: center.x" in runtime
    assert "startCenterY: center.y" in runtime
    assert "startOffsetX: Number(this.config.offset_x) || 0" in runtime
    assert "this.config.offset_x = Math.max(-5000, Math.min(5000, state.startOffsetX + dx));" in runtime
    assert "state.changed = Math.abs(scaleChange - 1) > 0.01 || Math.hypot(dx, dy) > 4;" in runtime
    assert "if (this.isLocked) return;" in runtime
    assert "this.image.style.pointerEvents = this.isLocked ? 'none' : 'auto';" in runtime
    assert "this._floatingButtonsContainer.style.display = this.isLocked ? 'none' : 'flex';" in runtime
    assert "this.image.addEventListener('wheel', this._boundWheelZoom" in runtime
    assert "this.image.addEventListener('touchstart', this._boundTouchStart" in runtime
    assert "this.image.addEventListener('touchmove', this._boundTouchMove" in runtime
    assert "this.applyScale(nextScale);" in runtime
    assert "this.scheduleSaveCurrentConfig();" in runtime
    assert "this.updateFloatingButtonsPosition();" in runtime
    assert "model_type: 'pngtuber'" in runtime
    assert "pngtuber: Object.assign({}, this.config)" in runtime
    assert "window.lanlan_config.model_type = 'pngtuber'" not in runtime
    assert "/api/characters/catgirl/l2d/" in runtime
    assert "#pngtuber-container .pngtuber-image.is-dragging" in css

    assert "#pngtuber-container.locked-hover-fade" in css
    assert "body.neko-main-ui-hidden-by-model-manager #pngtuber-container," in app_interpage
    assert "body.neko-main-ui-hidden-by-model-manager #pngtuber-container .pngtuber-image," in app_interpage


def test_pngtuber_drag_without_custom_image_uses_current_avatar():
    runtime = _read("static/pngtuber-core.js")

    assert "DEFAULT_DRAG_IMAGE" not in runtime
    assert "cat-idle-cat-move-1.gif" not in runtime
    assert "normalized.drag_image = normalized.drag_image || normalized.idle_image;" in runtime


def test_pngtuber_layered_adapter_runtime_contract():
    runtime = _read("static/pngtuber-core.js")

    assert "layered_canvas_v1" in runtime
    assert "canvas.pngtuber-layered-canvas" in runtime
    assert "async setupLayeredAdapter()" in runtime
    assert "fetch(this.config.layered_metadata" in runtime
    assert "drawLayeredState" in runtime
    assert "startLayeredBlinkLoop" in runtime
    assert "shouldRenderLayer" in runtime
    assert "this.config.adapter === 'layered_canvas_v1'" in runtime
    assert "this.showTransientImage(this.config.drag_image || this.config.idle_image);" in runtime
    assert "handleLayeredHotkey" in runtime
    assert "window.addEventListener('keydown', this._boundLayeredHotkey, true)" in runtime
    assert "this.layeredStateIndex" in runtime
    assert "layerStateForCurrentIndex" in runtime
    assert "window.removeEventListener('keydown', this._boundLayeredHotkey, true)" in runtime
    assert "pngtuber-play-animation" in runtime
    assert "playLayeredAnimation(target" in runtime
    assert "setLayeredStateIndex(index" in runtime
    assert "returnToDefaultAfterMs" in runtime
    assert "window.playPNGTuberAnimation = playPNGTuberAnimation" in runtime
    assert "pngtuber-layered-state-changed" in runtime
    assert "restartLayeredAnimationLoop" in runtime
    assert "requestAnimationFrame(tick)" in runtime
    assert "cancelAnimationFrame(this.layeredAnimationFrame)" in runtime
    assert "motionValue(layerState.xAmp, layerState.xFrq" in runtime
    assert "motionValue(layerState.yAmp, layerState.yFrq" in runtime
    assert "hasWiggleMotion" in runtime
    assert "if (layerState.folder) return false;" in runtime
    assert "if (layerState.visible === false) return false;" in runtime
    assert "layerState.ancestor_visible === false" in runtime
    assert "layerState.effective_should_talk" in runtime
    assert "layerState.effective_open_mouth" in runtime
    assert "layerState.effective_should_blink" in runtime
    assert "layerState.effective_open_eyes" in runtime
    assert "base_scale" in runtime
    assert "relativeFlipX" in runtime


def test_pngtuber_save_merges_selected_layered_metadata():
    manager = _read("static/js/model_manager.js")

    assert "selectedPNGTuberConfig" in manager
    assert "currentPNGTuberConfig" in manager
    assert "'adapter', 'layered_metadata', 'source_format', 'source_type'" in manager
    assert "pngtuberConfig[key] = selectedPNGTuberConfig[key];" in manager


def test_pngtuber_main_page_chat_interaction_is_not_blocked():
    runtime = _read("static/pngtuber-core.js")
    app_ui = _read("static/app-ui.js")
    css = _read("static/css/index.css")

    pngtuber_stage = _css_block(css, "#pngtuber-container")
    pngtuber_image = _css_block(css, "#pngtuber-container .pngtuber-image")
    chat_main_css = css[css.index("/* 看板娘聊天框-容器 */"):]

    assert "z-index: 10;" in pngtuber_stage
    assert "pointer-events: none;" in pngtuber_stage
    assert "pointer-events: auto;" in pngtuber_image
    assert "#chat-container" in chat_main_css
    assert "z-index: 20;" in chat_main_css
    assert "pointer-events: auto;" in chat_main_css
    assert "function isEventOverBlockingMainUi" not in runtime
    assert "MutationObserver" not in runtime

    assert "pngtuber: isPngtuberActiveForState && window.pngtuberManager ? window.pngtuberManager.isLocked : false" in app_ui
    assert "isPngtuberActiveForState && window.pngtuberManager" in app_ui
    assert "const isPngtuberActive = (window.lanlan_config?.model_type || '').toLowerCase() === 'pngtuber' && pngtuberContainer" in app_ui
    assert "playModelGoodbyeExit(pngtuberContainer, savedGoodbyeRect)" in app_ui
    assert "pngtuberContainer.style.setProperty('display', 'none', 'important')" in app_ui
    assert "const usePngtuberReturn = isPngtuberActive" in app_ui
    assert "pngtuber-return-button-container" in app_ui
    assert "effectiveModelType === 'pngtuber'" in app_ui
    assert "await window.loadPNGTuberAvatar(pngtuberConfig)" in app_ui
    assert "const isReturningToPngtuber = (window.lanlan_config?.model_type || '').toLowerCase() === 'pngtuber';" in app_ui
    assert "if (isReturningToPngtuber && pngtuberFloatingButtons)" in app_ui
    assert "if (isReturningToPngtuber && window.pngtuberManager" in app_ui


def test_live2d_paths_hide_pngtuber_overlays():
    app_ui = _read("static/app-ui.js")
    app_interpage = _read("static/app-interpage.js")
    live2d_init = _read("static/live2d-init.js")
    runtime = _read("static/pngtuber-core.js")

    show_live2d_start = app_ui.index("function showLive2d()")
    show_current_model_start = app_ui.index("async function showCurrentModel()")
    show_live2d_body = app_ui[show_live2d_start:show_current_model_start]

    assert "window.pngtuberManager.hide()" in show_live2d_body
    assert "window.cleanupPNGTuberOverlayUI" in show_live2d_body
    assert "#pngtuber-floating-buttons, #pngtuber-lock-icon, #pngtuber-return-button-container" in show_live2d_body
    assert "pngtuberContainerForLive2d.style.display = 'none';" in show_live2d_body
    assert "pngtuberContainerForLive2d.classList.add('hidden');" in show_live2d_body

    live2d_mode_start = app_interpage.index("// Live2D mode")
    live2d_mode_end = app_interpage.index("// Show & reload Live2D", live2d_mode_start)
    live2d_mode_body = app_interpage[live2d_mode_start:live2d_mode_end]

    assert "window.pngtuberManager.hide()" in live2d_mode_body
    assert "cleanupPNGTuberOverlayUI();" in live2d_mode_body
    assert "pngtuberContainer2.style.display = 'none';" in live2d_mode_body
    assert "pngtuberContainer2.classList.add('hidden');" in live2d_mode_body

    assert "window.pngtuberManager.hide();" in live2d_init
    assert "window.cleanupPNGTuberOverlayUI" in live2d_init
    assert "pngtuberContainer.style.display = 'none';" in live2d_init
    assert "pngtuberContainer.classList.add('hidden');" in live2d_init

    helper_start = runtime.index("function hideOtherAvatarRuntimesForPNGTuber()")
    load_start = runtime.index("async function loadPNGTuberAvatar(config)")
    helper_body = runtime[helper_start:load_start]
    load_body = runtime[load_start:]
    assert "live2dContainer.style.display = 'none';" in helper_body
    assert "live2dContainer.classList.add('hidden');" in helper_body
    assert "live2dCanvas.style.visibility = 'hidden';" in helper_body
    assert "#live2d-floating-buttons, #live2d-lock-icon, #live2d-return-button-container" in helper_body
    assert "window.hideOtherAvatarRuntimesForPNGTuber = hideOtherAvatarRuntimesForPNGTuber;" in runtime
    assert load_body.count("hideOtherAvatarRuntimesForPNGTuber();") >= 3
    assert "window.pngtuberManager.show();" in load_body


def test_live2d_show_and_init_are_guarded_in_pngtuber_mode():
    app_ui = _read("static/app-ui.js")
    live2d_init = _read("static/live2d-init.js")
    index_js = _read("static/js/index.js")

    show_live2d_start = app_ui.index("function showLive2d()")
    show_current_model_start = app_ui.index("async function showCurrentModel()")
    show_live2d_body = app_ui[show_live2d_start:show_current_model_start]
    assert "当前为 PNGTuber 模式，跳过 Live2D 显示" in show_live2d_body
    assert "live2dContainerForPngtuber.style.display = 'none';" in show_live2d_body
    assert "live2dCanvasForPngtuber.style.visibility = 'hidden';" in show_live2d_body
    assert "#live2d-floating-buttons, #live2d-lock-icon, #live2d-return-button-container" in show_live2d_body

    display_guard = live2d_init.index("当前为 PNGTuber 模式，取消 Live2D 显示与初始化")
    display_call = live2d_init.index("if (live2dContainer) live2dContainer.style.display = 'block';")
    pixi_call = live2d_init.index("await window.live2dManager.ensurePIXIReady")
    assert display_guard < display_call < pixi_call
    assert "当前已切换到 PNGTuber 模式，跳过 PIXI 初始化" in live2d_init

    assert "window.hideOtherAvatarRuntimesForPNGTuber();" in index_js
    assert "live2dCanvas.style.visibility = 'hidden';" in index_js
    assert "pngtuberC.style.display = 'none';" in index_js


def test_model_reload_reuses_duplicate_in_flight_requests():
    app_interpage = _read("static/app-interpage.js")

    reload_start = app_interpage.index("async function handleModelReload")
    reload_end = app_interpage.index("function _injectMotionGroupSafely", reload_start)
    reload_body = app_interpage[reload_start:reload_end]

    assert "var reloadKey = JSON.stringify" in reload_body
    assert "window._modelReloadKey === reloadKey" in reload_body
    assert "return window._modelReloadPromise;" in reload_body
    assert "window._pendingModelReload = { targetLanlanName: targetLanlanName, reloadOptions: reloadOptions };" in reload_body
    assert "window._modelReloadKey = reloadKey;" in reload_body
    assert "return handleModelReload(targetLanlanName, reloadOptions);" not in reload_body
    assert "handleModelReload(pendingReload.targetLanlanName, pendingReload.reloadOptions)" in reload_body


def test_show_main_ui_hides_inactive_avatar_runtimes():
    app_interpage = _read("static/app-interpage.js")

    show_start = app_interpage.index("function handleShowMainUI()")
    show_end = app_interpage.index("var VOICE_CONFIG_SWITCH_STALE_MS", show_start)
    show_body = app_interpage[show_start:show_end]

    assert "var activeUiPrefix = currentModelType === 'pngtuber'" in show_body
    assert "function hideInactiveAvatarRuntime(prefix)" in show_body
    assert "window.pngtuberManager.hide()" in show_body
    assert "cleanupPNGTuberOverlayUI();" in show_body
    assert "pngtuberContainerToHide.style.display = 'none';" in show_body
    assert "pngtuberContainerToHide.style.visibility = 'hidden';" in show_body
    assert "pngtuberImageToHide.style.visibility = 'hidden';" in show_body
    assert "['live2d', 'vrm', 'mmd', 'pngtuber'].forEach" in show_body
    assert "if (prefix !== activeUiPrefix) hideInactiveAvatarRuntime(prefix);" in show_body
    assert "el.id.indexOf(activeUiPrefix + '-') === 0" in show_body
    assert "el.id.indexOf(activeUiPrefix + '-') !== 0" in show_body


def test_pngtuber_does_not_fall_through_to_heavy_runtimes():
    live2d_init = _read("static/live2d-init.js")
    vrm_init = _read("static/vrm-init.js")

    assert "modelType === 'pngtuber'" in live2d_init
    assert "activeType === 'pngtuber'" in live2d_init
    assert "PNGTuber 模式，跳过 VRM 加载" in vrm_init
