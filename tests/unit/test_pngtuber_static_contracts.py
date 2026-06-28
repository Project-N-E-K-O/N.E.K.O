from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PNGTUBER_CORE_PATH = PROJECT_ROOT / "static" / "pngtuber-core.js"
APP_AUDIO_PLAYBACK_PATH = PROJECT_ROOT / "static" / "app-audio-playback.js"
APP_INTERPAGE_PATH = PROJECT_ROOT / "static" / "app-interpage.js"
APP_UI_PATH = PROJECT_ROOT / "static" / "app-ui.js"
INDEX_CSS_PATH = PROJECT_ROOT / "static" / "css" / "index.css"


def test_pngtuber_mobile_web_detection_uses_canonical_width_predicate():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    block = source[
        source.index("function isPngtuberMobileWebPage()"):
        source.index("function canInteractWithAvatar()")
    ]

    assert "if (isModelManagerPage()) return false;" in block
    assert "document.body?.classList.contains('electron-chat-window')" in block
    assert "if (window.__LANLAN_IS_ELECTRON_PET__) return false;" in block
    assert "typeof window.isMobileWidth === 'function'" in block
    assert "return window.innerWidth <= 768;" in block


def test_pngtuber_config_keeps_separate_mobile_placement_fields():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    normalize_block = source[
        source.index("function normalizeConfig(config)"):
        source.index("class PNGTuberManager")
    ]

    assert "normalized.scale = clampNumber(source.scale, SCALE_MIN, SCALE_MAX, 1);" in normalize_block
    assert "normalized.offset_x = Number.isFinite(Number(source.offset_x)) ? Number(source.offset_x) : 0;" in normalize_block
    assert "normalized.offset_y = Number.isFinite(Number(source.offset_y)) ? Number(source.offset_y) : 0;" in normalize_block
    assert "normalized.mobile_scale = clampNumber(source.mobile_scale, SCALE_MIN, SCALE_MAX, Math.min(normalized.scale, 1));" in normalize_block
    assert "normalized.mobile_offset_x = Number.isFinite(Number(source.mobile_offset_x)) ? Number(source.mobile_offset_x) : 0;" in normalize_block
    assert "normalized.mobile_offset_y = Number.isFinite(Number(source.mobile_offset_y)) ? Number(source.mobile_offset_y) : 0;" in normalize_block
    assert "centerPreview ? 0" not in normalize_block


def test_pngtuber_transform_and_interactions_use_active_layout_fields():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    transform_block = source[
        source.index("applyTransform()"):
        source.index("applyScale(nextScale)")
    ]
    drag_block = source[
        source.index("        startDrag(event) {"):
        source.index("        handleClick(event) {")
    ]
    wheel_block = source[
        source.index("handleWheelZoom(event)"):
        source.index("getTouchDistance(touch1, touch2)")
    ]
    touch_block = source[
        source.index("startTouchZoom(event)"):
        source.index("async endTouchZoom()")
    ]
    save_block = source[
        source.index("async saveCurrentConfig()"):
        source.index("        scheduleSaveCurrentConfig")
    ]

    assert "getActiveLayoutFields()" in transform_block
    assert "getActivePlacement()" in transform_block
    assert "const renderPlacement = this.getRenderPlacement(placement);" in transform_block
    assert "renderPlacement.scale" in transform_block
    assert "renderPlacement.offsetX" in transform_block
    assert "renderPlacement.offsetY + bounce.y" in transform_block
    assert "this.config.offset_x}px" not in transform_block
    assert "this.config.scale * bounce" not in transform_block

    assert "const placement = this.getActivePlacement();" in drag_block
    assert "startOffsetX: placement.offsetX" in drag_block
    assert "this.setActiveOffsets(state.startOffsetX + dx, state.startOffsetY + dy);" in drag_block
    assert "const currentScale = this.getActivePlacement().scale;" in wheel_block
    assert "initialScale: placement.scale" in touch_block
    assert "this.setActiveOffsets(state.startOffsetX + dx, state.startOffsetY + dy);" in touch_block
    assert "this.config.mobile_offset_x" in save_block
    assert "this.config.mobile_offset_y" in save_block
    assert "this.config.mobile_scale" in save_block


def test_pngtuber_model_manager_preview_centering_does_not_mutate_saved_offsets():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    render_block = source[
        source.index("getRenderPlacement(placement) {"):
        source.index("        setActiveScale(nextScale)")
    ]

    assert "isModelManagerPage()" in render_block
    assert "!this.config.preserve_model_manager_position" in render_block
    assert "offsetX: 0" in render_block
    assert "offsetY: 0" in render_block
    assert "this.config.offset_x = 0" not in source
    assert "this.config.offset_y = 0" not in source
    assert "this.config.mobile_offset_x = 0" not in source
    assert "this.config.mobile_offset_y = 0" not in source


def test_pngtuber_container_pointer_events_stay_passthrough_on_restore_paths():
    core_source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    interpage_source = APP_INTERPAGE_PATH.read_text(encoding="utf-8")
    app_ui_source = APP_UI_PATH.read_text(encoding="utf-8")
    css_source = INDEX_CSS_PATH.read_text(encoding="utf-8")

    css_container_block = css_source[
        css_source.index("#pngtuber-container {"):
        css_source.index("#pngtuber-container.minimized")
    ]
    css_image_block = css_source[
        css_source.index("#pngtuber-container .pngtuber-image {"):
        css_source.index("#pngtuber-container .pngtuber-image.is-dragging")
    ]
    assert "pointer-events: none;" in css_container_block
    assert "pointer-events: auto;" in css_image_block
    assert "this.container.style.pointerEvents = 'none';" in core_source

    assert "restoredPngtuberContainer.style.pointerEvents = 'auto';" not in interpage_source
    assert "pngtuberContainer.style.pointerEvents = 'auto';" not in interpage_source
    assert "restoredPngtuberContainer.style.pointerEvents = 'none';" in interpage_source
    assert "pngtuberContainer.style.pointerEvents = 'none';" in interpage_source
    assert "pngtuberContainer.style.setProperty('pointer-events', 'none', 'important');" in app_ui_source
    assert "pngtuberContainer.style.setProperty('pointer-events', 'auto', 'important');" not in app_ui_source


def test_pngtuber_mouth_flap_does_not_restart_layered_motion_timeline():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    set_state_block = source[
        source.index("setState(state"):
        source.index("        currentRemixStateSettings()")
    ]
    animation_loop_block = source[
        source.index("startLayeredAnimationLoop(options = {})"):
        source.index("        motionValue(")
    ]
    schedule_block = source[
        source.index("scheduleSpeakingMouthFrame()"):
        source.index("        startSpeakingMouthAnimation()")
    ]
    start_block = source[
        source.index("startSpeakingMouthAnimation()"):
        source.index("        stopSpeakingMouthAnimation()")
    ]

    assert "restartLayeredAnimation !== false" in set_state_block
    assert source.count("this.layeredAnimationStart = performance.now();") == 1
    assert "this.layeredAnimationStart = performance.now();" in animation_loop_block
    assert "if (!options.preserveTimeline || !this.layeredAnimationStart)" in animation_loop_block
    assert "layeredAnimationStart = performance.now()" not in set_state_block
    assert "layeredAnimationStart = performance.now()" not in schedule_block
    assert "layeredAnimationStart = performance.now()" not in start_block
    assert set_state_block.count("this.restartLayeredAnimationLoop();") == 1
    assert (
        "if (options.restartLayeredAnimation !== false) {\n"
        "                    this.restartLayeredAnimationLoop();\n"
        "                } else if (!this.layeredAnimationFrame && this.hasMotionLayersForCurrentState()) {\n"
        "                    this.startLayeredAnimationLoop({ preserveTimeline: true });\n"
        "                }"
    ) in set_state_block
    assert "this.restartLayeredAnimationLoop();" not in schedule_block
    assert "this.restartLayeredAnimationLoop();" not in start_block
    assert "this.setState(this.speakingMouthOpen ? 'talking' : 'idle', { restartLayeredAnimation: false });" in schedule_block
    assert "this.setState('talking', { restartLayeredAnimation: false });" in start_block


def test_layered_pngtuber_speaking_bounce_does_not_transform_whole_canvas():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    bounce_config_block = source[
        source.index("speakingBounceConfig()"):
        source.index("        currentSpeakingBounceTransform(")
    ]
    apply_transform_block = source[
        source.index("applyTransform(timestamp = performance.now())"):
        source.index("        getActiveLayoutFields()")
    ]

    assert "if (this.isLayeredActive()) return null;" in bounce_config_block
    assert "const bounce = this.currentSpeakingBounceTransform();" in apply_transform_block
    assert "this.image.style.transform" in apply_transform_block


def test_layered_pngtuber_motion_requires_explicit_runtime_feature_flags():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    feature_block = source[
        source.index("layeredRuntimeFeatureEnabled("):
        source.index("        stateHasMotion(")
    ]
    state_motion_block = source[
        source.index("stateHasMotion(layerState)"):
        source.index("        stateFrameInfo(")
    ]
    current_motion_block = source[
        source.index("hasMotionLayersForCurrentState("):
        source.index("        startLayeredAnimationLoop(")
    ]
    frame_block = source[
        source.index("stateFrameInfo(layer, layerState, img"):
        source.index("        stateHasFrameAnimation(")
    ]
    draw_block = source[
        source.index("drawLayeredState(stateName"):
        source.index("        showTransientImage(")
    ]

    assert "return features[featureName] === true;" in feature_block
    assert "hasMotionLayersForCurrentState(stateName = this.state || 'idle')" in current_motion_block
    assert "this.shouldRenderLayer(layer, stateName)" in current_motion_block
    assert "this.layeredRuntimeFeatureEnabled('layer_motion')" in state_motion_block
    assert "this.layeredRuntimeFeatureEnabled('sprite_sheet_animation')" in state_motion_block
    assert "this.layeredRuntimeFeatureEnabled('sprite_sheet_animation')" in frame_block
    assert "const layerMotionEnabled = this.layeredRuntimeFeatureEnabled('layer_motion');" in draw_block
    assert "layerMotionEnabled ? this.motionValue(layerState.xAmp, layerState.xFrq" in draw_block
    assert "layerMotionEnabled ? this.motionValue(layerState.yAmp, layerState.yFrq" in draw_block
    assert "layerMotionEnabled ? this.motionValue(layerState.wiggle_amp, layerState.wiggle_freq || layerState.rot_frq" in draw_block


def test_layered_pngtuber_keeps_stable_breathing_without_raw_layer_motion():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    constructor_block = source[
        source.index("constructor(containerId = 'pngtuber-container')"):
        source.index("        ensureContainer()")
    ]
    timers_block = source[
        source.index("clearLayeredTimers()"):
        source.index("        attachLayeredHotkeys()")
    ]
    restart_block = source[
        source.index("restartLayeredAnimationLoop()"):
        source.index("        motionValue(")
    ]
    animation_loop_block = source[
        source.index("startLayeredAnimationLoop(options = {})"):
        source.index("        layeredBreathingEnabled()")
    ]
    breathing_enabled_block = source[
        source.index("layeredBreathingEnabled()"):
        source.index("        currentLayeredBreathingTransform(")
    ]
    breathing_transform_block = source[
        source.index("currentLayeredBreathingTransform("):
        source.index("        startLayeredBreathingLoop(")
    ]
    breathing_loop_block = source[
        source.index("        startLayeredBreathingLoop("):
        source.index("        motionValue(")
    ]
    apply_transform_block = source[
        source.index("applyTransform(timestamp = performance.now())"):
        source.index("        getActiveLayoutFields()")
    ]

    assert "this.layeredBreathingFrame = null;" in constructor_block
    assert "this.layeredBreathingStart = 0;" in constructor_block
    assert "this.stopLayeredBreathingLoop();" in timers_block
    assert "this.startLayeredAnimationLoop();" in restart_block
    assert "this.startLayeredBreathingLoop();" in animation_loop_block
    assert "features.layered_breathing === false" in breathing_enabled_block
    assert "return this.isLayeredActive();" in breathing_enabled_block
    assert "if (!this.layeredBreathingStart) return { y: 0, scaleX: 1, scaleY: 1 };" in breathing_transform_block
    assert "this.layeredBreathingStart = timestamp;" not in breathing_transform_block
    assert "scaleY" in breathing_transform_block
    assert "scaleX" in breathing_transform_block
    assert "this.applyAnimationTransform(timestamp);" in breathing_loop_block
    assert "const breathing = this.currentLayeredBreathingTransform(timestamp);" in apply_transform_block
    assert "bounce.y + breathing.y" in apply_transform_block
    assert "bounce.scaleX * breathing.scaleX" in apply_transform_block
    assert "bounce.scaleY * breathing.scaleY" in apply_transform_block


def test_audio_playback_routes_lip_sync_to_pngtuber_when_active():
    source = APP_AUDIO_PLAYBACK_PATH.read_text(encoding="utf-8")
    model_type_block = source[
        source.index("function getActiveAvatarModelType()"):
        source.index("    function clearPendingAudioMetaStallTimer()")
    ]
    stop_block = source[
        source.index("function stopActiveLipSync()"):
        source.index("    function maybeFinalizeAssistantSpeech(")
    ]
    schedule_block = source[
        source.index("function scheduleAudioChunks()"):
        source.index("                    var scheduledStartTime = S.nextChunkTime;")
    ]

    assert "pngtuber-container" in model_type_block
    assert "modelType === 'pngtuber'" in model_type_block
    assert "return 'pngtuber';" in model_type_block
    assert "activeModelType === 'pngtuber'" in schedule_block
    assert "typeof window.pngtuberManager.startLipSync === 'function'" in schedule_block
    assert "window.pngtuberManager.startLipSync(S.globalAnalyser)" in schedule_block
    assert "S.lipSyncActive = true;" in schedule_block
    assert "activeModelType === 'pngtuber'" in stop_block
    assert "typeof window.pngtuberManager.stopLipSync === 'function'" in stop_block
    assert "window.pngtuberManager.stopLipSync()" in stop_block
    assert "S.lipSyncActive = false;" in stop_block


def test_pngtuber_analyser_lip_sync_uses_hysteresis_and_timer_mutex():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    constructor_block = source[
        source.index("constructor(containerId = 'pngtuber-container')"):
        source.index("        ensureContainer()")
    ]
    lip_sync_block = source[
        source.index("startLipSync(analyser)"):
        source.index("        stopLipSync()")
    ]
    stop_lip_sync_block = source[
        source.index("stopLipSync()"):
        source.index("        scheduleSpeakingMouthFrame()")
    ]
    schedule_block = source[
        source.index("scheduleSpeakingMouthFrame()"):
        source.index("        startSpeakingMouthAnimation()")
    ]

    assert "this.lipSyncFrame = null;" in constructor_block
    assert "this.lipSyncMouthOpen = 0;" in constructor_block
    assert "this.lipSyncMouthState = false;" in constructor_block
    assert "this.lipSyncLastStateChangeAt = 0;" in constructor_block
    assert "this.lipSyncNextPulseAt = 0;" in constructor_block
    assert "this.lipSyncPulseCloseAt = 0;" in constructor_block
    assert "clearTimeout(this.speakingMouthTimer);" in lip_sync_block
    assert "this.startSpeakingMouthAnimation();" in lip_sync_block
    assert "const sampleSize = Math.max(32, Number(analyser.fftSize) || 2048);" in lip_sync_block
    assert "frequencyBinCount" not in lip_sync_block
    assert "analyser.getByteTimeDomainData(dataArray);" in lip_sync_block
    assert "Math.sqrt(sum / dataArray.length)" in lip_sync_block
    assert "const activeThreshold = 0.16;" in lip_sync_block
    assert "const quietThreshold = 0.07;" in lip_sync_block
    assert "const pulseOpenMs = Math.max(42, Math.min(72, 42 + this.lipSyncMouthOpen * 34));" in lip_sync_block
    assert "const pulseGapMs = Math.max(45, Math.min(135, 135 - this.lipSyncMouthOpen * 90));" in lip_sync_block
    assert "timestamp >= this.lipSyncPulseCloseAt" in lip_sync_block
    assert "timestamp >= this.lipSyncNextPulseAt" in lip_sync_block
    assert "this.lipSyncPulseCloseAt = timestamp + pulseOpenMs;" in lip_sync_block
    assert "this.lipSyncNextPulseAt = timestamp + pulseGapMs;" in lip_sync_block
    assert "this.applyLipSyncMouthState(true);" in lip_sync_block
    assert "this.applyLipSyncMouthState(false);" in lip_sync_block
    assert "this.lipSyncFrame = requestAnimationFrame(tick);" in lip_sync_block
    assert "cancelAnimationFrame(this.lipSyncFrame);" in stop_lip_sync_block
    assert "this.lipSyncFrame = null;" in stop_lip_sync_block
    assert "this.lipSyncMouthOpen = 0;" in stop_lip_sync_block
    assert "this.lipSyncNextPulseAt = 0;" in stop_lip_sync_block
    assert "this.lipSyncPulseCloseAt = 0;" in stop_lip_sync_block
    assert "if (this.lipSyncFrame) return;" in schedule_block
    assert "if (!this.isSpeaking || this.lipSyncFrame) return;" in schedule_block


def test_pngtuber_lip_sync_state_changes_use_layered_safe_mouth_pulses():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    helper_block = source[
        source.index("applyLipSyncMouthState(open)"):
        source.index("        startLipSync(analyser)")
    ]

    assert "if (this.lipSyncMouthState === open && this.speakingMouthOpen === open) return;" in helper_block
    assert "this.lipSyncMouthState = open;" in helper_block
    assert "this.speakingMouthOpen = open;" in helper_block
    assert "this.startSpeakingBounceAnimation();" in helper_block
    assert "this.setState(open ? 'talking' : 'idle', { restartLayeredAnimation: false });" in helper_block


def test_pngtuber_debug_state_exposes_lip_sync_timer():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    debug_block = source[
        source.index("getDebugState()"):
        source.index("        setSpeaking(isSpeaking)")
    ]

    assert "lipSyncFrame: !!this.lipSyncFrame" in debug_block


def test_pngtuber_talking_hop_moves_whole_avatar_while_speaking():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    constructor_block = source[
        source.index("constructor(containerId = 'pngtuber-container')"):
        source.index("        ensureContainer()")
    ]
    hop_transform_block = source[
        source.index("currentTalkingHopTransform("):
        source.index("        startTalkingHopAnimation(")
    ]
    hop_loop_block = source[
        source.index("startTalkingHopAnimation("):
        source.index("        applyLipSyncMouthState(open)")
    ]
    stop_block = source[
        source.index("stopTalkingHopAnimation()"):
        source.index("        applyLipSyncMouthState(open)")
    ]
    apply_transform_block = source[
        source.index("applyTransform(timestamp = performance.now())"):
        source.index("        getActiveLayoutFields()")
    ]
    start_block = source[
        source.index("startSpeakingMouthAnimation()"):
        source.index("        stopSpeakingMouthAnimation()")
    ]
    lip_sync_block = source[
        source.index("startLipSync(analyser)"):
        source.index("        stopLipSync()")
    ]
    stop_speaking_block = source[
        source.index("stopSpeakingMouthAnimation()"):
        source.index("        renderedLayerCountForState(")
    ]
    debug_block = source[
        source.index("getDebugState()"):
        source.index("        setSpeaking(isSpeaking)")
    ]

    assert "this.talkingHopFrame = null;" in constructor_block
    assert "this.talkingHopStart = 0;" in constructor_block
    assert "this.talkingHopAmplitude = 0;" in constructor_block
    assert "this.talkingHopPeriodMs = 0;" in constructor_block
    assert "return { y: 0, scaleX: 1, scaleY: 1 };" in hop_transform_block
    assert "const wave = Math.sin(progress * Math.PI);" in hop_transform_block
    assert "y: -this.talkingHopAmplitude * wave" in hop_transform_block
    assert "scaleY: 1 + 0.004 * wave" in hop_transform_block
    assert "if (this.talkingHopFrame || !this.isSpeaking || !this.isLayeredActive()) return;" in hop_loop_block
    assert "this.talkingHopAmplitude = 4.5;" in hop_loop_block
    assert "this.talkingHopPeriodMs = 260;" in hop_loop_block
    assert "this.applyAnimationTransform(timestamp);" in hop_loop_block
    assert "this.talkingHopFrame = requestAnimationFrame(tick);" in hop_loop_block
    assert "cancelAnimationFrame(this.talkingHopFrame);" in stop_block
    assert "this.talkingHopFrame = null;" in stop_block
    assert "this.talkingHopStart = 0;" in stop_block
    assert "const talkingHop = this.currentTalkingHopTransform(timestamp);" in apply_transform_block
    assert "bounce.y + breathing.y + talkingHop.y" in apply_transform_block
    assert "bounce.scaleX * breathing.scaleX * talkingHop.scaleX" in apply_transform_block
    assert "bounce.scaleY * breathing.scaleY * talkingHop.scaleY" in apply_transform_block
    assert "this.startTalkingHopAnimation();" in start_block
    assert "this.startTalkingHopAnimation();" in lip_sync_block
    assert "this.stopTalkingHopAnimation();" in stop_speaking_block
    assert "talkingHopFrame: !!this.talkingHopFrame" in debug_block


def test_pngtuber_animation_loops_throttle_overlay_position_updates():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    constructor_block = source[
        source.index("constructor(containerId = 'pngtuber-container')"):
        source.index("        ensureContainer()")
    ]
    helper_block = source[
        source.index("updateOverlayPositionsForAnimation("):
        source.index("        currentLayeredBreathingTransform(")
    ]
    breathing_loop_block = source[
        source.index("        startLayeredBreathingLoop() {"):
        source.index("        stopLayeredBreathingLoop()")
    ]
    bounce_loop_block = source[
        source.index("        startSpeakingBounceAnimation() {"):
        source.index("        currentTalkingHopTransform(")
    ]
    hop_loop_block = source[
        source.index("        startTalkingHopAnimation() {"):
        source.index("        stopTalkingHopAnimation()")
    ]

    assert "this.lastOverlayPositionUpdateAt = 0;" in constructor_block
    assert "this.lastAnimationTransformAt = 0;" in constructor_block
    assert "const minIntervalMs = 120;" in helper_block
    assert "timestamp - this.lastOverlayPositionUpdateAt < minIntervalMs" in helper_block
    assert "this.updateLockIconPosition();" in helper_block
    assert "this.updateFloatingButtonsPosition();" in helper_block
    assert "applyAnimationTransform(timestamp = performance.now())" in helper_block
    assert "if (this.lastAnimationTransformAt === timestamp) return;" in helper_block
    assert "this.lastAnimationTransformAt = timestamp;" in helper_block
    assert "this.applyTransform(timestamp);" in helper_block
    assert "this.applyAnimationTransform(timestamp);" in breathing_loop_block
    assert "this.applyAnimationTransform(timestamp);" in bounce_loop_block
    assert "this.applyAnimationTransform(timestamp);" in hop_loop_block
    assert "this.updateOverlayPositionsForAnimation(timestamp);" in breathing_loop_block
    assert "this.updateOverlayPositionsForAnimation(timestamp);" in bounce_loop_block
    assert "this.updateOverlayPositionsForAnimation(timestamp);" in hop_loop_block
    assert "this.updateLockIconPosition();" not in breathing_loop_block
    assert "this.updateLockIconPosition();" not in bounce_loop_block
    assert "this.updateLockIconPosition();" not in hop_loop_block
