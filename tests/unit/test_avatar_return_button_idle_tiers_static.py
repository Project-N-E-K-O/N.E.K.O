from pathlib import Path

from main_routers import pages_router


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AVATAR_UI_BUTTONS_PATH = PROJECT_ROOT / "static" / "avatar-ui-buttons.js"
APP_UI_PATH = PROJECT_ROOT / "static" / "app-ui.js"
APP_INTERPAGE_PATH = PROJECT_ROOT / "static" / "app-interpage.js"
APP_REACT_CHAT_WINDOW_PATH = PROJECT_ROOT / "static" / "app-react-chat-window.js"
COMMON_UI_HUD_PATH = PROJECT_ROOT / "static" / "common-ui-hud.js"
LIVE2D_UI_BUTTONS_PATH = PROJECT_ROOT / "static" / "live2d-ui-buttons.js"
VRM_UI_BUTTONS_PATH = PROJECT_ROOT / "static" / "vrm-ui-buttons.js"
MMD_UI_BUTTONS_PATH = PROJECT_ROOT / "static" / "mmd-ui-buttons.js"
INDEX_CSS_PATH = PROJECT_ROOT / "static" / "css" / "index.css"
CAT1_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat1.gif"
CAT1_CLICK_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat1-click.gif"
CAT2_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat2.gif"
CAT2_CLICK_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat2-click.gif"
CAT3_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat3.gif"
CAT3_CLICK_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat3-click.gif"
CAT1_VOICE_CLICK_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat1-voice-click.mp3"
CAT1_VOICE1_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat1-voice1.mp3"
CAT1_VOICE2_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat1-voice2.mp3"
CAT1_VOICE3_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat1-voice3.mp3"
CAT2_SLEEP_SOUND_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat2-sleep.mp3"
CAT3_SLEEP_SOUND_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat3-sleep.mp3"
CAT1_WALK_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat4-1.gif"
CAT1_STRETCH_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat4-2.gif"
CAT1_INTERACTIVE_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat4-3.gif"
CAT1_DRAG_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat-move-1.gif"
CAT2_DRAG_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat-move-2.gif"
CAT3_DRAG_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat-move-3.gif"


def test_return_button_idle_tier_assets_are_mapped_in_source():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    # Non-click states
    assert "/static/assets/neko-idle/cat-idle-cat1.gif" in source
    assert "/static/assets/neko-idle/cat-idle-cat2.gif" in source
    assert "/static/assets/neko-idle/cat-idle-cat3.gif" in source
    assert "/static/assets/neko-idle/cat-idle-cat4-1.gif" in source
    assert "/static/assets/neko-idle/cat-idle-cat4-2.gif" in source
    assert '_NEKO_IDLE_TIER_CAT1' in source
    assert '_NEKO_IDLE_TIER_CAT2' in source
    assert '_NEKO_IDLE_TIER_CAT3' in source

    # Click states
    assert "/static/assets/neko-idle/cat-idle-cat1-click.gif" in source
    assert "/static/assets/neko-idle/cat-idle-cat2-click.gif" in source
    assert "/static/assets/neko-idle/cat-idle-cat3-click.gif" in source
    assert "/static/assets/neko-idle/cat1-voice-click.mp3" in source
    assert "/static/assets/neko-idle/cat1-voice1.mp3" in source
    assert "/static/assets/neko-idle/cat1-voice2.mp3" in source
    assert "/static/assets/neko-idle/cat1-voice3.mp3" in source
    assert "/static/assets/neko-idle/cat2-sleep.mp3" in source
    assert "/static/assets/neko-idle/cat3-sleep.mp3" in source
    assert "/static/assets/neko-idle/cat-idle-cat4-3.gif" in source
    assert "/static/assets/neko-idle/cat-idle-cat-move-1.gif" in source
    assert "/static/assets/neko-idle/cat-idle-cat-move-2.gif" in source
    assert "/static/assets/neko-idle/cat-idle-cat-move-3.gif" in source
    assert '_getNekoIdleReturnClickAssetUrl' in source
    assert '_getNekoIdleReturnDragAssetUrl' in source


def test_return_button_idle_tier_styles_are_present():
    source = INDEX_CSS_PATH.read_text(encoding="utf-8")

    assert '.neko-idle-return-btn[data-neko-idle-tier="cat2"]' in source
    assert '.neko-idle-return-btn[data-neko-idle-tier="cat3"]' in source
    assert '.neko-idle-return-btn.is-cat1-facing-right' in source


def test_desktop_return_ball_drag_viewport_preserves_measured_cat_size():
    source = APP_UI_PATH.read_text(encoding="utf-8")

    assert "MULTI_WINDOW_RETURN_BALL_DRAG_SHRINK_SIZE = 160" in source
    assert "container.style.setProperty('--neko-ball-drag-size', `${state.savedBallWidth}px`)" in source
    assert "--neko-idle-return-size:var(--neko-ball-drag-size)!important" in source
    assert "body[data-neko-ball-drag] .neko-idle-return-art" in source
    assert "container.style.removeProperty('--neko-ball-drag-size')" in source


def test_desktop_return_ball_drag_stops_native_drag_without_waiting_for_frame():
    source = APP_UI_PATH.read_text(encoding="utf-8")

    finish_index = source.index("async function finishDrag(screenX, screenY)")
    hide_index = source.index("container.style.visibility = 'hidden';", finish_index)
    flush_index = source.index("void container.offsetWidth;", hide_index)
    stop_index = source.index("await window.nekoPetDrag.stop(screenX, screenY)", flush_index)
    resolve_index = source.index("const finalBounds = await resolveFinalWindowBounds", flush_index)
    finish_body = source[finish_index:resolve_index]

    assert finish_index < hide_index < flush_index < stop_index
    assert finish_index < hide_index < flush_index < resolve_index
    assert "await waitForAnimationFrames(2);" not in finish_body
    assert "visibility: container.style.visibility" in source
    assert "container.style.visibility = savedStyle.visibility" in source
    assert "container.style.visibility = getSavedBallStyleValue('visibility')" in source


def test_desktop_return_ball_drag_lifecycle_waits_for_restored_viewport_before_reveal():
    source = APP_UI_PATH.read_text(encoding="utf-8")

    assert ": 600" in source
    assert "keeping return-ball hidden until viewport is restored" in source
    assert "waitForViewportSize hard timeout; continuing best-effort cleanup" in source
    assert "clearMultiWindowReturnBallDeferredWork(state)" in source
    assert "state.viewportWaitFallbackTimer = setTimeout(pollViewportRestore, 50)" in source
    assert "runWhenStable({ timedOut: true })" not in source
    assert "function revealReturnBallDragWindow()" in source
    assert "window.nekoPetDrag.reveal" in source
    assert "const dragStarted = window.nekoPetDrag.start(screenX, screenY)" in source
    assert "if (dragStarted === false)" in source

    begin_index = source.index("function beginDrag(screenX, screenY, event)")
    native_start_index = source.index("const dragStarted = window.nekoPetDrag.start(screenX, screenY)", begin_index)
    dispatch_start_index = source.index("reason: 'return-ball-drag-start'", begin_index)
    drag_style_index = source.index("document.body.dataset.nekoBallDrag = '1'", begin_index)

    assert begin_index < native_start_index < dispatch_start_index < drag_style_index


def test_return_button_drag_has_single_owner_per_runtime_path():
    avatar_source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")
    live2d_source = LIVE2D_UI_BUTTONS_PATH.read_text(encoding="utf-8")
    vrm_source = VRM_UI_BUTTONS_PATH.read_text(encoding="utf-8")
    mmd_source = MMD_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    assert "if (!window.__NEKO_MULTI_WINDOW__)" in avatar_source
    assert "this._setupReturnButtonDrag(returnButtonContainer)" in avatar_source
    assert "Live2DManager.prototype.setupReturnButtonContainerDrag = function(container)" in live2d_source
    assert "this.setupReturnButtonContainerDrag(returnButtonContainer)" not in live2d_source
    assert "this._setupReturnButtonDrag(container)" not in live2d_source
    assert "this._setupReturnButtonDrag(returnButtonContainer)" not in vrm_source
    assert "this._setupReturnButtonDrag(returnButtonContainer)" not in mmd_source

    vrm_handle_end = vrm_source[
        vrm_source.index("const handleEnd = () => {"):
        vrm_source.index("returnButtonContainer.addEventListener('mousedown'", vrm_source.index("const handleEnd = () => {"))
    ]
    assert vrm_handle_end.index("commitDragPosition();") < vrm_handle_end.index("const moved =")


def test_return_button_idle_tier_switch_uses_crossfade_motion():
    button_source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")
    css_source = INDEX_CSS_PATH.read_text(encoding="utf-8")

    assert '_NEKO_IDLE_RETURN_TRANSITION_MS = 820' in button_source
    assert '_setNekoIdleReturnArtSource' in button_source
    assert 'neko-idle-return-art-next' in button_source
    assert "button.classList.add('is-tier-transitioning')" in button_source
    assert '_shouldReduceNekoIdleMotion' in button_source

    assert '@keyframes nekoIdleTierOut' in css_source
    assert '@keyframes nekoIdleTierIn' in css_source
    assert '.neko-idle-return-btn.is-tier-transitioning' in css_source
    assert 'position: relative;' in _extract_neko_return_btn_block(css_source)
    assert '@media (prefers-reduced-motion: reduce)' in css_source


def test_return_button_hover_click_gif_finishes_before_restore():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    assert '_NEKO_IDLE_RETURN_GIF_DURATION_CACHE = new Map()' in source
    assert '_NEKO_IDLE_RETURN_GIF_PLAYBACK_SOURCE_CACHE = new Map()' in source
    assert '_parseGifDurationMs' in source
    assert '_patchGifDelayRate' in source
    assert '_getNekoIdleGifPlaybackSource' in source
    assert '_getNekoIdleGifDurationMs' in source
    assert '_playNekoIdleHoverArt' in source
    assert '_finishNekoIdleHoverArtAfterPlayback' in source
    assert '_clearNekoIdleHoverPlayback' in source
    assert '__nekoIdleHoverToken' in source
    assert '__nekoIdleHoverTimer' in source
    assert 'art.__nekoIdleHoverSrc === clickSrc' in source
    assert 'Math.max(0, durationMs - elapsedMs)' in source
    assert 'keepHoverPlayback' in source


def test_sleeping_cat_tiers_schedule_soft_random_sound_once_per_interval():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    assert "_NEKO_IDLE_SLEEP_SOUND_INTERVAL_MS = 5 * 60 * 1000" in source
    assert "_NEKO_IDLE_SLEEP_SOUND_VOLUME = 0.12" in source
    assert "function _playNekoIdleSound(state, src, volume)" in source
    assert "[_NEKO_IDLE_TIER_CAT2]" in source
    assert "[_NEKO_IDLE_TIER_CAT3]" in source
    assert "src: '/static/assets/neko-idle/cat2-sleep.mp3'" in source
    assert "src: '/static/assets/neko-idle/cat3-sleep.mp3'" in source
    assert "audio.volume = Math.max(0, Math.min(1, Number(volume) || 0.2))" in source
    assert "Math.random() * _NEKO_IDLE_SLEEP_SOUND_INTERVAL_MS" in source
    assert "_scheduleNekoIdleSleepSoundInterval(tier, startedAt + _NEKO_IDLE_SLEEP_SOUND_INTERVAL_MS)" in source
    assert "_syncNekoIdleSleepSoundForTier(detail.tier)" in source
    assert "_stopNekoIdleSleepSoundAudio()" in source
    assert "_clearNekoIdleSleepSoundTimer()" in source


def test_cat1_voice_sounds_are_limited_to_non_drag_and_drag_states():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    assert "_NEKO_IDLE_CAT1_AMBIENT_SOUND_INTERVAL_MS = 3 * 60 * 1000" in source
    assert "_NEKO_IDLE_CAT1_AMBIENT_SOUND_VOLUME = 0.14" in source
    assert "_NEKO_IDLE_CAT1_DRAG_SOUND_VOLUME = 0.16" in source
    assert "_NEKO_IDLE_CAT1_DRAG_SOUND_FADE_OUT_MS = 900" in source
    assert "'/static/assets/neko-idle/cat1-voice1.mp3'" in source
    assert "'/static/assets/neko-idle/cat1-voice2.mp3'" in source
    assert "'/static/assets/neko-idle/cat1-voice3.mp3'" in source
    assert "_NEKO_IDLE_CAT1_DRAG_SOUND_URL = '/static/assets/neko-idle/cat1-voice-click.mp3'" in source
    assert "Math.random() * _NEKO_IDLE_CAT1_AMBIENT_SOUND_INTERVAL_MS" in source
    assert "urls[Math.floor(Math.random() * urls.length)]" in source
    assert "_scheduleNekoIdleCat1AmbientSoundInterval(startedAt + _NEKO_IDLE_CAT1_AMBIENT_SOUND_INTERVAL_MS)" in source
    assert "normalizedTier !== _NEKO_IDLE_TIER_CAT1 || _isAnyNekoIdleReturnDragActionActive()" in source
    assert "_playNekoIdleCat1DragSound(tier)" in source
    assert "_fadeOutNekoIdleCat1DragSound()" in source
    assert "_fadeOutNekoIdleSoundAudio(_nekoIdleCat1DragSoundState, _NEKO_IDLE_CAT1_DRAG_SOUND_FADE_OUT_MS)" in source
    assert "audio.volume = Math.max(0, startVolume * (1 - progress))" in source
    assert "_normalizeNekoIdleReturnTier(tier) !== _NEKO_IDLE_TIER_CAT1" in source
    assert "_syncNekoIdleCat1AmbientSoundForTier(detail.tier)" in source
    assert "_stopNekoIdleCat1AmbientSound()" in source


def test_cat1_walk_to_minimized_chat_contract_is_present():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")
    app_ui_source = (PROJECT_ROOT / "static" / "app-ui.js").read_text(encoding="utf-8")

    assert "_NEKO_IDLE_CAT1_SUBSTATE_WALKING = 'walking-to-chat'" in source
    assert "_NEKO_IDLE_CAT1_SUBSTATE_STRETCH = 'stretch-near-chat'" in source
    assert '_NEKO_IDLE_CAT1_WALK_SPEED_PX_PER_SEC = 101' in source
    assert '_NEKO_IDLE_CAT1_WALK_MAX_SPEED_RATE = 1.5' in source
    assert '_NEKO_IDLE_CAT1_WALK_DISTANCE_INCREASE_THRESHOLD_PX' in source
    assert '_NEKO_IDLE_CAT1_WALK_DISTANCE_GROWTH_FOR_MAX_RATE_PX' in source
    assert '_NEKO_IDLE_CAT1_STRETCH_FINAL_HOLD_MS = 700' in source
    assert '_NEKO_IDLE_CAT1_WALK_ENTER_DISTANCE_PX' in source
    assert '_NEKO_IDLE_CAT1_WALK_EXIT_DISTANCE_PX' in source
    assert '_NEKO_IDLE_CAT1_RECHECK_MOVE_DISTANCE_PX' in source
    assert '_NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW' in source
    assert '_NEKO_IDLE_RETURN_SUBACTION_PROFILES' in source
    assert '_getNekoIdleReturnSubactionProfile' in source
    assert '_getNekoIdleReturnSubactionState' in source
    assert 'preserveObservers' in source
    assert "{ resetArt: true, preserveObservers: true }" in source
    assert source.count("{ resetArt: true, preserveObservers: true }") >= 2
    assert '_getNekoIdleCat1Target' in source
    assert '_startNekoIdleCat1Walk' in source
    assert '_stepNekoIdleCat1Walk' in source
    assert '_scheduleNekoIdleCat1WalkStart' in source
    assert '_updateNekoIdleCat1WalkSpeedRate' in source
    assert '_resetNekoIdleCat1WalkSpeed' in source
    assert 'profile.target.speedPxPerSec * speedRate * elapsedMs' in source
    assert 'data-neko-gif-playback-rate' in source
    assert '--neko-idle-gif-playback-rate' in source
    assert '_applyNekoIdleGifPlaybackRate' in source
    assert '_clearNekoIdleGifPlaybackSource' in source
    assert 'Math.round(originalDelayCs / playbackRate)' in source
    assert '_pickNekoIdleReturnSubactionStartDelayMs' in source
    assert 'startDelay' in source
    assert 'pendingWalkTimer' in source
    assert 'pendingWalkReady' in source
    assert '_cancelNekoIdleReturnPendingWalk' in source
    assert '_NEKO_IDLE_CAT1_WALK_LONG_DELAY_MAX_MS = 5 * 60 * 1000' in source
    assert '_NEKO_IDLE_CAT1_PAIR_MOVE_SHORT_DELAY_MIN_MS = 5 * 1000' in source
    assert '_NEKO_IDLE_CAT1_PAIR_MOVE_SHORT_DELAY_MAX_MS = 90 * 1000' in source
    assert '_NEKO_IDLE_CAT1_PAIR_MOVE_LONG_DELAY_MAX_MS = 5 * 60 * 1000' in source
    assert 'pairMove: Object.freeze' in source
    assert 'intervalChoices' in source
    assert 'pairMoveTimer' in source
    assert 'pairMoveFrame' in source
    assert 'pairMovePlan' in source
    assert '_scheduleNekoIdleCat1PairMove' in source
    assert '_startNekoIdleCat1PairMove' in source
    assert '_stepNekoIdleCat1PairMove' in source
    assert '_finishNekoIdleCat1PairMove' in source
    assert '_cancelNekoIdleCat1PairMove' in source
    assert '_getNekoIdleReactChatMinimizedShell' in source
    assert '_getNekoIdleReactChatExpandedShell' in source
    assert '_isNekoIdleDesktopChatExpandedRecent' in source
    assert '_canNekoIdleCat1MoveSoloWithExpandedChat' in source
    assert '_getNekoIdleCat1PairMoveChatTarget' in source
    assert '_pickNekoIdleCat1MoveVector' in source
    assert '_hasNekoIdleCat1MoveVectorSpace' in source
    assert '_clampNekoIdleCat1MoveVector' in source
    assert '_dispatchNekoIdleDesktopChatPairMoveBounds' in source
    assert "action: 'idle_chat_pair_move_bounds'" in source
    assert "chatMode: chatTarget ? chatTarget.mode : 'solo'" in source
    assert "dy: moveVector.dy" in source
    assert '_setNekoIdleCat1PairMoveChatPosition' in source
    assert "shell.style.right = ''" in source
    assert "shell.style.bottom = ''" in source
    assert "plan.chatMode === 'dom'" in source
    assert "plan.chatMode === 'desktop'" in source
    assert '_canNekoIdleCat1MoveSoloWithExpandedChat()' in source
    assert '_applyNekoIdleCat1PairMovePlan(plan, progress)' in source
    assert 'plan.catStartTop + offsetY' in source
    assert 'plan.chatStartScreenTop + offsetY' in source
    assert 'if (!_startNekoIdleCat1PairMove(button))' in source
    assert '_finishNekoIdleHoverArtAfterPlayback(art, profile.tier)' in source
    assert '_setNekoIdleReturnArtSource(art, state.profile.assets.walking()' in source
    assert 'state.substate === profile.idleSubstate && state.actionSettled' in source
    assert 'state.substate === profile.idleSubstate && !state.actionSettled' in source
    assert 'state.actionSettled = true' in source
    assert 'state.substate === profile.walkingSubstate && target.distance > profile.target.exitDistancePx' in source
    assert '_scheduleNekoIdleReturnSubactionSettle' in source
    assert '_settleNekoIdleReturnSubactionToIdle' in source
    assert 'durationMs - elapsedMs) + profile.settle.finalHoldMs' in source
    assert 'containerObserver' in source
    assert "attributeFilter: ['style', 'data-dragging']" in source
    assert '_scheduleNekoIdleCat1JourneySyncForContainer' in source
    assert '_shouldRecheckNekoIdleCat1AfterManualMove' in source
    assert '_getNekoIdleRectCenterMoveDistance' in source
    assert '_isNekoIdleCat1Walking' in source
    assert 'movedDistancePx' in source
    assert 'isSmallDesktopChatMove' in source
    assert 'if (isSmallDesktopChatMove && !_isNekoIdleCat1Walking(button)) return;' in source
    assert '_dispatchNekoIdleReturnBallManualMove' in source
    assert '_getNekoIdleDesktopChatMinimizedRect' in source
    assert '_getNekoIdleChatMinimizedRect' in source
    assert "'neko:idle-chat-minimized-state'" in source
    assert "currentState && (currentState.pairMovePlan || currentState.pairMoveFrame)" in source
    assert '_NEKO_IDLE_DESKTOP_CHAT_RECT_STALE_MS' in source
    assert '_pauseNekoIdleCat1Journey' in source
    assert '_resumeNekoIdleCat1Journey' in source
    assert '_getNekoIdleReturnCurrentArtUrl' in source
    assert '_startNekoIdleReturnDragActionForContainer' in source
    assert '_finishNekoIdleReturnDragActionForContainer' in source
    assert 'state.actionSettled = true' in source
    assert '{ animate: true }' in source
    assert 'is-cat1-facing-right' in source
    assert 'state.paused = true' in source
    assert 'state.paused = false' in source
    assert 'state.substate !== profile.walkingSubstate' in source
    walk_start = source[
        source.index('function _startNekoIdleCat1Walk'):
        source.index('function _scheduleNekoIdleCat1WalkStart')
    ]
    assert '_stepNekoIdleCat1Walk(button, timestamp)' in walk_start
    assert 'window.requestAnimationFrame((timestamp)' not in walk_start
    assert 'resumeWalkAfterDrag' not in source
    assert 'preserveResumeAfterDrag' not in source
    assert '_prepareNekoIdleCat1ResumeAfterDragForContainer' not in source
    assert 'restoreArt: !resumeCat1Walking' not in source
    assert "'neko:return-ball-manual-move'" in source
    assert "'neko:return-ball-manual-move'" in app_ui_source
    assert "detail.reason === 'return-ball-drag-start'" in source
    assert "resetArt: false" in source
    assert "'return-ball-drag-start'" in app_ui_source
    assert "'return-ball-drag-active'" in source
    assert "'return-ball-drag-active'" in app_ui_source
    assert "'return-ball-drag-end'" in source
    assert "'return-ball-drag-end'" in app_ui_source
    assert "movedDistancePx: movedDistancePx" in app_ui_source
    assert "this._setupReturnButtonDrag(returnButtonContainer)" in source
    assert "if (!window.__NEKO_MULTI_WINDOW__)" in source


def test_return_button_idle_tier_assets_are_version_tracked():
    for path in (APP_UI_PATH, APP_INTERPAGE_PATH, COMMON_UI_HUD_PATH,
                 APP_REACT_CHAT_WINDOW_PATH,
                 CAT1_ASSET_PATH, CAT1_CLICK_ASSET_PATH,
                 CAT2_ASSET_PATH, CAT2_CLICK_ASSET_PATH,
                 CAT3_ASSET_PATH, CAT3_CLICK_ASSET_PATH,
                 CAT1_VOICE_CLICK_PATH, CAT1_VOICE1_PATH,
                 CAT1_VOICE2_PATH, CAT1_VOICE3_PATH,
                 CAT2_SLEEP_SOUND_PATH, CAT3_SLEEP_SOUND_PATH,
                 CAT1_WALK_ASSET_PATH, CAT1_STRETCH_ASSET_PATH,
                 CAT1_INTERACTIVE_ASSET_PATH,
                 CAT1_DRAG_ASSET_PATH, CAT2_DRAG_ASSET_PATH, CAT3_DRAG_ASSET_PATH):
        assert path in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS
        assert path.is_file()


def test_no_box_shadow_or_border_in_base_return_btn_css():
    source = INDEX_CSS_PATH.read_text(encoding="utf-8")

    base_block = _extract_neko_return_btn_block(source)
    assert base_block
    assert 'box-shadow' not in base_block
    assert 'border' not in base_block
    assert 'backdrop-filter' not in base_block


def _extract_neko_return_btn_block(source):
    selector = '.neko-idle-return-btn'
    start = source.find(selector)
    while start != -1:
        suffix_start = start + len(selector)
        prev_index = start - 1
        while prev_index >= 0 and source[prev_index].isspace():
            prev_index -= 1
        if prev_index >= 0 and source[prev_index] != '}':
            start = source.find(selector, suffix_start)
            continue
        open_brace = source.find('{', suffix_start)
        next_selector = source.find(selector, suffix_start)
        if open_brace == -1 or (next_selector != -1 and next_selector < open_brace):
            start = next_selector
            continue
        if source[suffix_start:open_brace].strip():
            start = source.find(selector, suffix_start)
            continue
        depth = 0
        for index in range(open_brace, len(source)):
            char = source[index]
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    return source[open_brace + 1:index]
        return ''
    return ''
