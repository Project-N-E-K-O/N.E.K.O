from pathlib import Path

from main_routers import pages_router


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AVATAR_UI_BUTTONS_PATH = PROJECT_ROOT / "static" / "avatar-ui-buttons.js"
INDEX_CSS_PATH = PROJECT_ROOT / "static" / "css" / "index.css"
CAT1_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat1.gif"
CAT1_CLICK_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat1-click.gif"
CAT2_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat2.gif"
CAT2_CLICK_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat2-click.gif"
CAT3_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat3.gif"
CAT3_CLICK_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat3-click.gif"
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
    assert 'state.substate === profile.walkingSubstate && target.distance > profile.target.exitDistancePx' in source
    assert '_scheduleNekoIdleReturnSubactionSettle' in source
    assert '_settleNekoIdleReturnSubactionToIdle' in source
    assert 'durationMs - elapsedMs) + profile.settle.finalHoldMs' in source
    assert 'containerObserver' in source
    assert "attributeFilter: ['style', 'data-dragging']" in source
    assert '_scheduleNekoIdleCat1JourneySyncForContainer' in source
    assert '_dispatchNekoIdleReturnBallManualMove' in source
    assert '_getNekoIdleDesktopChatMinimizedRect' in source
    assert '_getNekoIdleChatMinimizedRect' in source
    assert "'neko:idle-chat-minimized-state'" in source
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
    assert 'resumeWalkAfterDrag' in source
    assert 'preserveResumeAfterDrag: true' in source
    assert '_prepareNekoIdleCat1ResumeAfterDragForContainer' in source
    assert 'state.pendingWalkReady = true' in source
    assert 'restoreArt: !resumeCat1Walking' in source
    assert "'neko:return-ball-manual-move'" in source
    assert "'neko:return-ball-manual-move'" in app_ui_source
    assert "detail.reason === 'return-ball-drag-start'" in source
    assert "resetArt: false" in source
    assert "'return-ball-drag-start'" in app_ui_source
    assert "'return-ball-drag-active'" in source
    assert "'return-ball-drag-active'" in app_ui_source
    assert "'return-ball-drag-end'" in source
    assert "'return-ball-drag-end'" in app_ui_source
    assert "this._setupReturnButtonDrag(returnButtonContainer)" in source
    assert "if (!window.__NEKO_MULTI_WINDOW__)" in source


def test_return_button_idle_tier_assets_are_version_tracked():
    for path in (CAT1_ASSET_PATH, CAT1_CLICK_ASSET_PATH,
                 CAT2_ASSET_PATH, CAT2_CLICK_ASSET_PATH,
                 CAT3_ASSET_PATH, CAT3_CLICK_ASSET_PATH,
                 CAT1_WALK_ASSET_PATH, CAT1_STRETCH_ASSET_PATH,
                 CAT1_INTERACTIVE_ASSET_PATH,
                 CAT1_DRAG_ASSET_PATH, CAT2_DRAG_ASSET_PATH, CAT3_DRAG_ASSET_PATH):
        assert path in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS
        assert path.is_file()


def test_no_box_shadow_or_border_in_base_return_btn_css():
    source = INDEX_CSS_PATH.read_text(encoding="utf-8")

    assert 'box-shadow' not in source or 'neko-idle-return-btn' not in _extract_neko_block(source, 'box-shadow')
    assert 'border' not in source or 'neko-idle-return-btn' not in _extract_neko_block(source, 'border')
    assert 'backdrop-filter' not in source or 'neko-idle-return-btn' not in _extract_neko_block(source, 'backdrop-filter')


def _extract_neko_block(source, keyword):
    lines = source.splitlines()
    in_neko = False
    result = []
    for line in lines:
        if '.neko-idle-return' in line:
            in_neko = True
        elif in_neko and line.strip() == '}':
            in_neko = False
            if any(keyword in l for l in result):
                return '.neko-idle-return-btn'
        if in_neko:
            result.append(line)
    return ''
