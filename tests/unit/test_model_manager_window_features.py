import re
from pathlib import Path


MODEL_MANAGER_PART_NAMES = (
    "named-window-registration.js",
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


def test_vrm_catalog_preview_preserves_selected_idle_and_stops_preview_rotation():
    source = Path("static/js/model_manager/page-controller.js").read_text(
        encoding="utf-8"
    )
    preview = source.split("async function playSelectedVrmAnimationOption", 1)[1].split(
        "// VRM动作选择按钮点击事件",
        1,
    )[0]
    assert re.search(
        r"vrmMotionCatalogPlayer\.setSavedRestAnimations\(\s*"
        r"getSelectedIdleAnimations\('vrm-idle-animation-multiselect'\)\s*\);",
        preview,
    )
    assert re.search(
        r"vrmMotionCatalogPlayer\.playAsset\(\s*assetId\s*,\s*\{\s*"
        r"scheduleNext:\s*false\s*\}\s*\)",
        preview,
    )
    preview_start = "isVrmAnimationPlaying = true;"
    preview_play = "const played = await vrmMotionCatalogPlayer.playAsset"
    assert preview_start in preview
    assert preview.index(preview_start) < preview.index(preview_play)

    idle_switch = source.split("async function _playIdleAnimation", 1)[1].split(
        "async function restoreVrmIdleAnimation",
        1,
    )[0]
    assert "const idlePlaybackStarted = await vrmManager.playVRMAAnimation" in idle_switch
    assert "if (idlePlaybackStarted !== true) return;" in idle_switch


def test_main_vrm_idle_rotation_ignores_cancelled_playback_completion():
    source = Path("static/vrm/vrm-init.js").read_text(encoding="utf-8")
    idle_rotation = source.split("function _startVrmIdleRotation", 1)[1].split(
        "function _stopVrmIdleRotation", 1
    )[0]

    playback = "const played = await mgr.playVRMAAnimation"
    stale_guard = "if (played !== true) return;"
    state_update = "_vrmIdleLastUrl = url;"
    assert playback in idle_rotation
    assert stale_guard in idle_rotation
    assert idle_rotation.index(playback) < idle_rotation.index(stale_guard)
    assert idle_rotation.index(stale_guard) < idle_rotation.index(state_update)


def test_vrm_catalog_preview_pause_does_not_resume_catalog_base_motion():
    source = Path("static/js/model_manager/page-controller.js").read_text(
        encoding="utf-8"
    )
    play_button_handler = source.split("if (playVrmAnimationBtn) {", 1)[1].split(
        "// ======================== MMD 模型/动画列表",
        1,
    )[0]
    pause_branch = play_button_handler.split("if (isVrmAnimationPlaying) {", 1)[
        1
    ].split("} else {", 1)[0]

    assert (
        "vrmMotionCatalogPlayer.cancel('model_manager_pause', { resume: false });"
        in pause_branch
    )
    assert "vrmManager.stopVRMAAnimation();" in pause_branch
    assert pause_branch.index("cancel('model_manager_pause'") < pause_branch.index(
        "vrmManager.stopVRMAAnimation();"
    )


def test_vrm_animation_picker_separates_catalog_and_direct_playback():
    source = Path("static/js/model_manager/page-controller.js").read_text(
        encoding="utf-8"
    )
    playback = source.split(
        "async function playSelectedVrmAnimationOption",
        1,
    )[1].split("// VRM动作选择按钮点击事件", 1)[0]

    assert "if (assetId && isCatalogMotion)" in playback
    assert "if (played !== true)" in playback
    assert "model_manager_direct_playback" in playback
    assert playback.index("model_manager_direct_playback") < playback.index(
        "vrmManager.playVRMAAnimation"
    )


def test_vrm_animation_picker_persists_official_gzip_only_from_allowed_directory():
    source = Path("static/js/model_manager/page-controller.js").read_text(
        encoding="utf-8"
    )

    assert "static\\/vrm\\/animation|user_vrm\\/animation" in source
    assert "isCatalogMotion && !isPersistableAnimation" in source


def test_vrm_catalog_options_preserve_motion_pack_urls():
    source = Path("static/js/model_manager/page-controller.js").read_text(
        encoding="utf-8"
    )
    option_build = source.split(
        "const isCatalogMotion = anim.systemMotion === true;", 1
    )[1].split("vrmAnimationSelect.appendChild(option);", 1)[0]

    assert "const finalUrl = isCatalogMotion" in option_build
    assert "? animPath" in option_build
    assert ": ModelPathHelper.vrmToUrl(animPath, 'animation');" in option_build


def test_vrm_saved_legacy_url_normalization_ignores_query_and_hash_suffixes():
    source = Path("static/js/model_manager/page-controller.js").read_text(
        encoding="utf-8"
    )
    helper = source.split("function normalizeBundledVrmAnimationUrl", 1)[1].split(
        "function mergeVrmAnimationLists", 1
    )[0]

    assert "\\.vrma(?:[?#]|$)" in helper
    assert "decodeURIComponent(assetName)" in helper
    assert "'/static/vrm/animation/' + assetName + '.vrma.gz'" in helper


def test_vrm_catalog_player_resets_before_loading_a_new_model():
    source = Path("static/js/model_manager/page-controller.js").read_text(
        encoding="utf-8"
    )
    load_block = source.split("// 在加载新模型前，显式停止之前的动作并清理", 1)[1].split(
        "// 加载新模型后，重置播放状态", 1
    )[0]

    cancel = "vrmMotionCatalogPlayer.cancel('model_manager_model_load', { resume: false });"
    assert cancel in load_block
    assert load_block.index(cancel) < load_block.index("await vrmManager.loadModel(modelUrl")


def test_vrm_preview_ignores_stale_playback_completions():
    source = Path("static/js/model_manager/page-controller.js").read_text(
        encoding="utf-8"
    )
    assert "let vrmAnimationPlaybackRequestId = 0;" in source
    assert source.count("if (playbackRequestId !== vrmAnimationPlaybackRequestId) return;") >= 4
    playback = source.split(
        "async function playSelectedVrmAnimationOption", 1
    )[1].split("// VRM动作选择按钮点击事件", 1)[0]
    assert "const requestIsCurrent = () =>" in playback
    assert "if (!requestIsCurrent()) return false;" in playback
    assert playback.index("vrmManager.stopVRMAAnimation()") < playback.index(
        "await loadVrmMotionCatalog()"
    )
    assert playback.index("await loadVrmMotionCatalog()") < playback.index(
        "if (!requestIsCurrent()) return false;"
    )
    assert len(re.findall(
        r"playSelectedVrmAnimationOption\(\s*selectedOption,\s*playbackRequestId\s*\)",
        source,
    )) == 3  # function declaration plus both callers


def test_vrm_catalog_hold_pose_remains_stoppable():
    source = Path("static/js/model_manager/page-controller.js").read_text(
        encoding="utf-8"
    )
    playback = source.split(
        "async function playSelectedVrmAnimationOption", 1
    )[1].split("// VRM动作选择按钮点击事件", 1)[0]

    assert "['loop', 'hold'].includes(" in playback


def test_static_asset_version_tracks_vrm_motion_player():
    source = Path("main_routers/pages_router.py").read_text(encoding="utf-8")
    assert '_PROJECT_ROOT / "static/vrm/motion/player.js"' in source


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


def test_model_manager_hides_main_model_only_while_fully_covered():
    model_manager_source = read_model_manager_source()
    interpage_source = Path(
        "static/app/app-interpage/bootstrap-resources-and-model-reload.js"
    ).read_text(encoding="utf-8")
    overlap_start = interpage_source.index("function refreshModelManagerWindowOverlap()")
    overlap_end = interpage_source.index(
        "function scheduleModelManagerWindowOverlapRefresh()", overlap_start
    )
    overlap_body = interpage_source[overlap_start:overlap_end]
    overlap_style_start = interpage_source.index(
        "function ensureModelManagerOverlapHiddenStyle()"
    )
    overlap_style_end = interpage_source.index(
        "function setModelManagerOverlapModelHidden(", overlap_style_start
    )
    overlap_style_body = interpage_source[overlap_style_start:overlap_style_end]
    screen_rect_start = interpage_source.index(
        "function getModelManagerActiveModelScreenRect()"
    )
    screen_rect_end = interpage_source.index(
        "function refreshModelManagerWindowOverlap()", screen_rect_start
    )
    screen_rect_body = interpage_source[screen_rect_start:screen_rect_end]
    client_rect_start = interpage_source.index(
        "function getModelManagerActiveModelClientRect("
    )
    client_rect_end = interpage_source.index(
        "function getModelManagerBrowserContentScreenOrigin()", client_rect_start
    )
    client_rect_body = interpage_source[client_rect_start:client_rect_end]
    reload_success_start = interpage_source.index("if (reloadSucceeded) {")
    reload_success_end = interpage_source.index(
        "} else {", reload_success_start
    )
    reload_success_body = interpage_source[
        reload_success_start:reload_success_end
    ]

    assert "model_manager_window_state" in model_manager_source
    assert "getModelManagerWindowScreenBounds" in model_manager_source
    assert "nekoModelManagerVisibility" in model_manager_source
    assert "document.hasFocus()" in model_manager_source
    assert "const MODEL_MANAGER_VISIBILITY_HEARTBEAT_MS = 400;" in model_manager_source
    assert "window.sendMessageToMainPage('model_manager_window_state'" in model_manager_source
    assert model_manager_source.count("if (quiet) return;") >= 1
    assert (
        "return modelManagerRectFullyCovers(state.bounds, modelBounds);"
        in overlap_body
    )
    assert "clipModelManagerClientRectToViewport" in interpage_source
    assert "getModelManagerActiveModelScreenRect" in interpage_source
    assert "modelManagerCachedModelClientBounds" in interpage_source
    assert (
        "getModelManagerActiveModelClientRect(modelManagerOverlapHidden)"
        in screen_rect_body
    )
    assert "isModelManagerActiveModelDragging" not in interpage_source
    assert "configuredModelType === 'live3d'" in client_rect_body
    assert "activeModelType === 'live2d'" in client_rect_body
    assert "activeModelType === 'vrm'" in client_rect_body
    assert "activeModelType === 'mmd'" in client_rect_body
    assert "activeModelType === 'pngtuber'" in client_rect_body
    assert "setModelManagerOverlapModelHidden(shouldHide);" in overlap_body
    assert "setModelManagerOverlapModelHidden(false);" in overlap_body
    assert "I.handleHideMainUI(" not in overlap_body
    assert "I.handleShowMainUI(" not in overlap_body
    assert "#live2d-container" in overlap_style_body
    assert "#pngtuber-container" in overlap_style_body
    assert "#react-chat-window-overlay" not in overlap_style_body
    assert "-floating-buttons" not in overlap_style_body
    assert "-lock-icon" not in overlap_style_body
    assert "display: none" not in overlap_style_body
    assert "scheduleModelManagerWindowOverlapRefresh()" in interpage_source
    assert (
        "if (_isModelHostPage()) {\n"
        "        I.yuiGuideInterpageResources.setInterval("
        "refreshModelManagerWindowOverlap, 500);\n"
        "    }"
    ) in interpage_source
    assert "function invalidateModelManagerOverlapBounds()" in interpage_source
    assert "invalidateModelManagerOverlapBounds();" in reload_success_body
    assert "mainUIHideOwners = Object.create(null)" in interpage_source
    assert "delete mainUIHideOwners[getMainUIHideOwner(options)]" in interpage_source
    assert overlap_body.index("if (!visibleModelManagerStates.length)") < overlap_body.index(
        "getModelManagerActiveModelScreenRect()"
    )


def test_model_manager_uses_one_non_focusing_window_instance():
    model_manager_source = read_model_manager_source()
    parameter_editor_source = Path(
        "static/js/live2d_parameter_editor.js"
    ).read_text(encoding="utf-8")
    common_dialogs = Path("static/common_dialogs.js").read_text(encoding="utf-8")
    character_manager = Path(
        "static/js/character_card_manager/character-data-and-transfer.js"
    ).read_text(encoding="utf-8")
    tutorial_handoff = Path("static/tutorial/yui-guide/page-handoff.js").read_text(
        encoding="utf-8"
    )
    reuse_start = character_manager.index("if (reusedModelManagerWindow)")
    reuse_end = character_manager.index(
        "window._openSettingsWindows[url] = popup;", reuse_start
    )
    reuse_body = character_manager[reuse_start:reuse_end]
    cached_reuse_start = character_manager.index(
        "if (existingWindow && !existingWindow.closed)"
    )
    cached_reuse_end = character_manager.index(
        "delete window._openSettingsWindows[url];", cached_reuse_start
    )
    cached_reuse_body = character_manager[cached_reuse_start:cached_reuse_end]
    registration_start = model_manager_source.index(
        "(function registerModelManagerNamedWindow()"
    )
    registration_end = model_manager_source.index("})();", registration_start) + len(
        "})();"
    )
    registration_body = model_manager_source[registration_start:registration_end]
    model_manager_template = Path("templates/model_manager.html").read_text(
        encoding="utf-8"
    )
    parameter_editor_template = Path(
        "templates/live2d_parameter_editor.html"
    ).read_text(encoding="utf-8")
    send_start = model_manager_source.index("function sendMessageToMainPage(")
    send_end = model_manager_source.index(
        "function isModelManagerPopupWindow()", send_start
    )
    send_body = model_manager_source[send_start:send_end]

    assert "MODEL_MANAGER_SINGLETON_WINDOW_NAME" in common_dialogs
    assert "pathname === '/model_manager' || pathname === '/l2d'" in common_dialogs
    assert "requestOpenedWindowRestoreIfMinimized(existingWindow)" in common_dialogs
    assert "if (!isModelManager) requestOpenedWindowRestore(newWindow);" in common_dialogs
    assert "neko:restore-window-if-minimized" in common_dialogs
    assert "window.open(url, '_blank'" not in character_manager
    assert "requestOpenedWindowRestoreIfMinimized(existingWindow)" in character_manager
    assert "targetWindow.document.hidden === true" in common_dialogs
    assert "if (!hasNativeRestoreBridge)" in common_dialogs
    assert "onReuse: () => { reusedModelManagerWindow = true; }" in character_manager
    assert "await rollbackAutoCreatedCatgirl(form);" in reuse_body
    assert (
        "await rollbackAutoCreatedCatgirl(form, form._autoCreatedDetachedName);"
        in cached_reuse_body
    )
    assert "form._autoCreatedDependentPopup = existingWindow" in cached_reuse_body
    assert "neko:named-window:" in registration_body
    assert "neko:named-window-focus:" in registration_body
    assert "window.localStorage.setItem(registryKey" in registration_body
    assert "setInterval(markModelManagerNamedWindowActive, 1000)" in registration_body
    assert (
        "window.opener === null || window.name !== MODEL_MANAGER_SINGLETON_WINDOW_NAME"
        in registration_body
    )
    assert "window.addEventListener('storage'" in registration_body
    assert "window.addEventListener('pageshow', () => {" in registration_body
    assert "window.addEventListener('pagehide', () => {" in registration_body
    assert "stopModelManagerVisibilityTracking();" in registration_body
    assert "stopModelManagerNamedWindowRegistration();" in registration_body
    assert "startModelManagerNamedWindowRegistration();" in registration_body
    assert "startModelManagerVisibilityTracking();" in registration_body
    assert "publishModelManagerWindowState(false);" in registration_body
    assert "window.addEventListener('unload'" not in registration_body
    assert "data.windowName !== MODEL_MANAGER_SINGLETON_WINDOW_NAME" in registration_body
    assert "api.restoreIfMinimized()" in registration_body
    assert "if (document.hidden === true) window.focus();" in registration_body
    assert "named-window-registration.js" in model_manager_template
    assert "named-window-registration.js" in parameter_editor_template
    parameter_editor_send_start = parameter_editor_source.index(
        "function sendMessageToMainPage("
    )
    parameter_editor_send_end = parameter_editor_source.index(
        "// 翻译辅助函数", parameter_editor_send_start
    )
    parameter_editor_send_body = parameter_editor_source[
        parameter_editor_send_start:parameter_editor_send_end
    ]
    assert (
        "const quiet = action === 'model_manager_window_state';"
        in parameter_editor_send_body
    )
    assert (
        parameter_editor_send_body.index("if (quiet) return;")
        < parameter_editor_send_body.index(
            "localStorage.setItem('nekopage_message'"
        )
    )
    assert "if (!quiet) {" in parameter_editor_send_body
    assert "function isModelManagerHostPageWindow(targetWindow)" in send_body
    assert (
        "if (quiet && isModelManagerHostPageWindow(window.opener)) return;"
        in send_body
    )
    assert (
        send_body.index("isModelManagerHostPageWindow(window.opener)")
        < send_body.index("localStorage.setItem('nekopage_message'")
    )
    assert "if (!isModelManagerPageUrl(targetUrl))" in tutorial_handoff
    assert "pathname === '/model_manager' || pathname === '/l2d'" in tutorial_handoff
    assert "handleHideMainUI({ owner: 'yui-page-handoff' })" in tutorial_handoff
    assert "handleShowMainUI({ owner: 'yui-page-handoff' })" in tutorial_handoff


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
