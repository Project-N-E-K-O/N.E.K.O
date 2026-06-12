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
CAT2_SLEEP_SOUND_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat2-sleep1.mp3"
CAT2_SLEEP_SOUND2_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat2-sleep2.mp3"
CAT3_SLEEP_SOUND_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat3-sleep1.mp3"
CAT3_SLEEP_SOUND2_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat3-sleep2.mp3"
CAT1_WALK_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat4-1.gif"
CAT1_STRETCH_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat4-2.gif"
CAT1_INTERACTIVE_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat4-3.gif"
CAT1_DRAG_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat-move-1.gif"
CAT2_DRAG_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat-move-2.gif"
CAT3_DRAG_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat-move-3.gif"
CAT4_DRAG_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat-move-4.gif"
CAT_MODEL_CHANGE_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat_model_change.gif"
THOUGHT_BUBBLE_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "thought-items" / "cloud-thought-bubble.gif"
SLEEPING_THOUGHT_BUBBLE_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "thought-items" / "sleeping-zzz.gif"
THOUGHT_BUBBLE_ITEM_ASSET_PATHS = (
    PROJECT_ROOT / "static" / "assets" / "neko-idle" / "thought-items" / "catnip-pouch.png",
    PROJECT_ROOT / "static" / "assets" / "neko-idle" / "thought-items" / "fish-cookie.png",
    PROJECT_ROOT / "static" / "assets" / "neko-idle" / "thought-items" / "toy-mouse.png",
)


def _source_slice_between(source, start_marker, end_marker, block_name):
    start = source.find(start_marker)
    assert start != -1, f"{block_name} start marker not found: {start_marker}"
    end = source.find(end_marker, start + len(start_marker))
    assert end != -1, f"{block_name} end marker not found after start: {end_marker}"
    assert start < end, f"{block_name} start marker must precede end marker"
    return source[start:end]


def _assert_source_contains(block, expected, block_name):
    assert expected in block, f"{block_name} missing expected source: {expected}"


def _assert_source_order(block, block_name, *expected_markers):
    positions = []
    for marker in expected_markers:
        position = block.find(marker)
        assert position != -1, f"{block_name} missing expected source: {marker}"
        positions.append(position)
    assert positions == sorted(positions), f"{block_name} expected order: {' -> '.join(expected_markers)}"


def test_return_button_idle_tier_assets_are_mapped_in_source():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")
    app_ui_source = APP_UI_PATH.read_text(encoding="utf-8")

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
    assert "/static/assets/neko-idle/cat2-sleep1.mp3" in source
    assert "/static/assets/neko-idle/cat3-sleep1.mp3" in source
    assert "/static/assets/neko-idle/cat-idle-cat4-3.gif" in source
    assert "/static/assets/neko-idle/cat-idle-cat-move-1.gif" in source
    assert "/static/assets/neko-idle/cat-idle-cat-move-2.gif" in source
    assert "/static/assets/neko-idle/cat-idle-cat-move-3.gif" in source
    assert "/static/assets/neko-idle/cat-idle-cat-move-4.gif" in source
    assert "/static/assets/neko-idle/cat_model_change.gif" in app_ui_source
    assert '_getNekoIdleReturnClickAssetUrl' in source
    assert '_getNekoIdleReturnDragAssetUrl' in source
    assert "const _NEKO_IDLE_RETURN_DRAG_ASSET_URLS_BY_TIER = Object.freeze({" in source
    assert "[_NEKO_IDLE_TIER_CAT1]: Object.freeze([" in source
    assert "[_NEKO_IDLE_TIER_CAT2]: Object.freeze([" in source
    assert "[_NEKO_IDLE_TIER_CAT3]: Object.freeze([" in source
    assert "function _pickNekoIdleReturnDragAssetUrl(tier)" in source

    cat1_drag_pool = _source_slice_between(
        source,
        "[_NEKO_IDLE_TIER_CAT1]: Object.freeze([",
        "]),\n    [_NEKO_IDLE_TIER_CAT2]",
        "cat1 drag asset pool",
    )
    assert "cat-idle-cat-move-1.gif" in cat1_drag_pool
    assert "cat-idle-cat-move-2.gif" in cat1_drag_pool

    cat2_drag_pool = _source_slice_between(
        source,
        "[_NEKO_IDLE_TIER_CAT2]: Object.freeze([",
        "]),\n    [_NEKO_IDLE_TIER_CAT3]",
        "cat2 drag asset pool",
    )
    assert "cat-idle-cat-move-2.gif" in cat2_drag_pool
    assert "cat-idle-cat-move-3.gif" in cat2_drag_pool

    cat3_drag_pool = _source_slice_between(
        source,
        "[_NEKO_IDLE_TIER_CAT3]: Object.freeze([",
        "])\n});",
        "cat3 drag asset pool",
    )
    assert "cat-idle-cat-move-3.gif" in cat3_drag_pool
    assert "cat-idle-cat-move-4.gif" in cat3_drag_pool


def test_model_cat_transition_contract_is_present():
    source = APP_UI_PATH.read_text(encoding="utf-8")
    avatar_source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    assert "function playNekoModelCatTransition" in source
    assert "window.playNekoModelCatTransition = playNekoModelCatTransition" in source
    assert "let nekoModelCatTransitionActive = null" in source
    assert "function isNekoModelCatTransitionActive(direction = '')" in source
    assert "function reserveNekoModelCatTransition(direction)" in source
    assert "function releaseNekoModelCatTransition(token)" in source
    assert "function isNekoModelCatTransitionTokenCurrent(token)" in source
    assert "const goodbyeTransitionToken = reserveNekoModelCatTransition('model-to-cat')" in source
    assert "transitionToken: goodbyeTransitionToken" in source
    assert "releaseNekoModelCatTransition(goodbyeTransitionToken)" in source
    assert "window.isNekoModelCatTransitionActive = isNekoModelCatTransitionActive" in source
    assert "blocked: true" in source
    assert "isNekoModelCatTransitionActive()" in source
    assert "isNekoModelCatTransitionActive('model-to-cat')" in source
    assert "window.isNekoModelCatTransitionActive()" in avatar_source
    assert "data-neko-model-cat-transitioning" in source
    assert "function playModelReturnEnter(container, rect)" in source
    assert "window._nekoModelReturnEnterRect = returnRect || savedRect || null" in source
    assert "consumeModelReturnEnterRect()" in source
    assert "NEKO_MODEL_CAT_TRANSITION_MODEL_SCALE = 0.38" in source
    assert "function getModelCatTransitionScaleTransform()" in source
    assert "getModelCatTransitionScaleTransform()" in source
    assert "function prepareModelReturnContainer(container, rect, options = {})" in source
    assert "container.style.transform = 'scale(1) translateZ(0)'" in source
    assert "NEKO_MODEL_RETURN_ENTER_TRANSITION = 'opacity 1120ms ease-out, transform 1080ms cubic-bezier(0.22, 1, 0.36, 1)'" in source
    assert "NEKO_MODEL_RETURN_ENTER_CLEANUP_MS = 1160" in source
    assert "NEKO_MODEL_RETURN_CANVAS_FADE_TRANSITION = 'opacity 1.12s ease-out'" in source
    assert "NEKO_MODEL_RETURN_CANVAS_FADE_CLEANUP_MS = 1160" in source
    assert "1450" not in source
    return_enter_block = source[
        source.index("function playModelReturnEnter(container, rect)"):
        source.index("function mergeNekoTransitionAnchorRect(anchorRect, coverRect)")
    ]
    assert "}, NEKO_MODEL_RETURN_ENTER_CLEANUP_MS)" in return_enter_block
    assert "NEKO_MODEL_RETURN_CANVAS_FADE_CLEANUP_MS" not in return_enter_block
    assert "NEKO_MODEL_GOODBYE_VISUAL_FADE_TRANSITION = 'opacity 240ms ease-in'" in source
    assert "function getActiveModelTransitionRect()" in source
    assert "getModelScreenBounds" in source
    assert "savedGoodbyeRect = savedModelRect || savedGoodbyeRect" in source
    assert "NEKO_MODEL_CAT_REVEAL_BEFORE_SMOKE_HIDE_MS = 48" in source
    assert "NEKO_MODEL_CAT_TRANSITION_DURATION_MS = 850" in source
    assert "NEKO_MODEL_CAT_TRANSITION_LOOP_GUARD_MS = 70" in source
    assert "NEKO_MODEL_CAT_TRANSITION_LOAD_FALLBACK_MS = 1200" in source
    assert "NEKO_MODEL_CAT_TO_MODEL_LOCK_MS = 1120" in source
    assert "function getNekoModelCatOverlayVisibleMs()" in source
    assert "function getNekoModelCatSettleMs(direction)" in source
    assert "NEKO_MODEL_CAT_TRANSITION_DURATION_MS - NEKO_MODEL_CAT_TRANSITION_LOOP_GUARD_MS" in source
    assert "overflow: 'hidden'" in source
    assert "borderRadius: '50%'" in source
    assert "NEKO_MODEL_CAT_TRANSITION_EDGE_MASK = 'radial-gradient(circle at center" in source
    assert "rgba(0,0,0,0.18) 72%" in source
    assert "rgba(0,0,0,0) 88%" in source
    assert "function applyNekoTransitionMask(element)" in source
    assert "maskImage: NEKO_MODEL_CAT_TRANSITION_EDGE_MASK" in source
    assert "element.style.webkitMaskImage = NEKO_MODEL_CAT_TRANSITION_EDGE_MASK" in source
    assert "element.style.setProperty('-webkit-mask-image', NEKO_MODEL_CAT_TRANSITION_EDGE_MASK)" in source
    assert "function createNekoModelCatTransitionOverlay(rect, direction, token)" in source
    assert "applyNekoTransitionMask(overlay)" in source
    assert "applyNekoTransitionMask(image)" in source
    assert "const ensureOverlayVisible = () => {" in source
    assert "const startVisibleSmokePlayback = () => {" in source
    _assert_source_order(
        source,
        "transition preload ordering",
        "startVisibleSmokePlayback();",
        "preloadImage.src = src",
    )
    assert "parseGifDurationMs" not in source
    assert "getNekoModelCatTransitionDurationMs" not in source
    assert "NEKO_MODEL_CAT_TRANSITION_MIN_SIZE = 260" in source
    assert "NEKO_MODEL_CAT_TRANSITION_MAX_SIZE = 680" in source
    assert "NEKO_MODEL_CAT_TRANSITION_SIZE_FACTOR = 0.86" in source
    assert "Math.round(basis * NEKO_MODEL_CAT_TRANSITION_SIZE_FACTOR)" in source
    transition_rect_block = source[
        source.index("function normalizeNekoTransitionRect(anchorRect, container, coverRect)"):
        source.index("function clearNekoModelCatTransitionOverlay(keepToken = '')")
    ]
    assert "left: Math.round(centerX - size / 2)" in transition_rect_block
    assert "top: Math.round(centerY - size / 2)" in transition_rect_block
    assert "maxLeft" not in transition_rect_block
    assert "maxTop" not in transition_rect_block
    assert "const transitionAnchorRect = savedGoodbyeRect || activeReturnButtonContainer.getBoundingClientRect()" in source
    assert "function mergeNekoTransitionAnchorRect(anchorRect, coverRect)" in source
    assert "const coverRect = options.coverRect || null" in source
    assert "coverRect: window._savedGoodbyeRect || getActiveModelTransitionRect()" in source
    assert "coverRect: window._savedGoodbyeRect || null" in avatar_source
    assert "direction: 'model-to-cat'" in source
    assert "direction: 'cat-to-model'" in source
    assert "return-ball-model-cat-transition-done" in source
    assert "return-ball-model-cat-transition-fallback" in source
    assert "NEKO_MODEL_CAT_TRANSITION_MODEL_EXIT_WAIT_MS" not in source
    assert "dispatchClickEvent();" in source
    assert "window.playNekoModelCatTransition" in avatar_source
    assert "window.dispatchEvent(event);" in avatar_source
    assert "dispatchReturnEvent();" in avatar_source
    assert "let nekoModelCatRevealPlaybackToken = 0" in source
    assert "function buildNekoModelCatRevealPlaybackUrl(src, playbackToken)" in source
    assert "url.searchParams.set('reveal'" in source
    assert "function restartNekoModelCatRevealArt(container)" in source
    assert "const isCurrentTransition = () => isNekoModelCatTransitionTokenCurrent(token)" in source
    assert "if (!isCurrentTransition()) return;" in source

    transition_promise_start = source.index("const transitionPromise = new Promise((resolve) => {")
    transition_promise_block = source[
        transition_promise_start:
        source.index("if (nekoModelCatTransitionActive && nekoModelCatTransitionActive.token === token)", transition_promise_start)
    ]
    assert "const startTransitionPlayback = () => {" in transition_promise_block
    assert "const preloadImage = new Image()" in transition_promise_block
    assert "preloadImage.addEventListener('load'" in transition_promise_block
    assert "preloadImage.addEventListener('error'" in transition_promise_block
    assert "document.body.appendChild(overlay);" in transition_promise_block
    assert "image.removeAttribute('src')" not in transition_promise_block
    assert "imageLoadFallbackTimer = setTimeout" in transition_promise_block
    assert "image.src = src;" in transition_promise_block
    _assert_source_order(
        transition_promise_block,
        "transition playback scheduling",
        "image.src = src;",
        "playbackStartedAt = getNekoTransitionNowMs();",
        "scheduleTransitionTimers(resolve);",
    )

    reveal_active_block = source[
        source.index("const revealActiveReturnBall = (reason) => {"):
        source.index("requestAnimationFrame(() => {", source.index("const revealActiveReturnBall = (reason) => {"))
    ]
    assert reveal_active_block.index("restartNekoModelCatRevealArt(activeReturnButtonContainer)") < reveal_active_block.index("revealReturnBallContainer(activeReturnButtonContainer, reason)")


def test_return_button_idle_tier_styles_are_present():
    source = INDEX_CSS_PATH.read_text(encoding="utf-8")

    assert '.neko-idle-return-btn[data-neko-idle-tier="cat2"]' in source
    assert '.neko-idle-return-btn[data-neko-idle-tier="cat3"]' in source
    assert '.neko-idle-return-btn.is-cat1-facing-right' in source


def test_model_goodbye_exit_shrinks_in_place_instead_of_sliding_right():
    source = INDEX_CSS_PATH.read_text(encoding="utf-8")
    app_ui_source = APP_UI_PATH.read_text(encoding="utf-8")

    assert "translateX(300px)" not in source
    assert "#live2d-container.minimized" in source
    assert "#vrm-container.minimized" in source
    assert "#mmd-container.minimized" in source
    assert "transform: scale(0.38) translateZ(0);" in source
    assert "--neko-model-opacity-transition: opacity 280ms ease-in;" in source
    assert "--neko-model-transform-transition: transform 400ms cubic-bezier(0.22, 1, 0.36, 1);" in source
    assert "--neko-model-visibility-delay: 400ms;" in source
    assert "transition: var(--neko-model-opacity-transition), var(--neko-model-transform-transition)" in source
    assert "height 0ms var(--neko-model-visibility-delay)" in source
    assert "transform-origin: var(--neko-model-exit-origin-x, 50%) var(--neko-model-exit-origin-y, 50%);" in source
    assert source.count("transform-origin: var(--neko-model-exit-origin-x, 50%) var(--neko-model-exit-origin-y, 50%);") >= 3
    assert "function setModelExitTransformOrigin(container, rect)" in app_ui_source
    assert "function playModelGoodbyeExit(container, rect)" in app_ui_source
    assert "function applyModelGoodbyeVisualFade(container, options = {})" in app_ui_source
    assert "visualLayer.style.transition = NEKO_MODEL_GOODBYE_VISUAL_FADE_TRANSITION" in app_ui_source
    assert "if (options.restart !== false)" in app_ui_source
    assert "visualLayer.style.opacity = '0'" in app_ui_source
    assert "applyModelGoodbyeVisualFade(container, { restart: false })" in app_ui_source
    assert "applyModelGoodbyeVisualFade(container, { restart: true })" in app_ui_source
    assert "const isGoodbyeExiting = container.getAttribute('data-neko-model-goodbye-exiting') === 'true';" in app_ui_source
    assert "if (live2dCanvasForHide && !isGoodbyeExiting)" in app_ui_source
    assert "container.style.transition = NEKO_MODEL_GOODBYE_EXIT_TRANSITION" not in app_ui_source
    assert "container.classList.add('minimized')" in app_ui_source
    assert "playModelGoodbyeExit(live2dContainerForGoodbye, savedGoodbyeRect)" in app_ui_source
    assert "playModelGoodbyeExit(vrmContainer, savedGoodbyeRect)" in app_ui_source
    assert "playModelGoodbyeExit(mmdContainer, savedGoodbyeRect)" in app_ui_source
    assert "mmdCanvas.style.transition = 'opacity 0.62s ease-out'" not in app_ui_source

    live2d_minimized_block = _extract_css_block(source, "#live2d-container.minimized")
    vrm_minimized_block = _extract_css_block(source, "#vrm-container.minimized")
    mmd_minimized_block = _extract_css_block(source, "#mmd-container.minimized")
    assert "visibility: hidden" not in live2d_minimized_block
    assert "visibility: hidden" not in vrm_minimized_block
    assert "visibility: hidden" not in mmd_minimized_block


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

    assert "MULTI_WINDOW_RETURN_BALL_DRAG_SHRINK_FALLBACK_MS = 220" in source
    assert "MULTI_WINDOW_RETURN_BALL_DRAG_RESTORE_FALLBACK_MS = 600" in source
    assert "MULTI_WINDOW_RETURN_BALL_REVEAL_FALLBACK_MS = 600" in source
    assert "continueOnFallback" in source
    assert "waitForViewportSize timed out; continuing best-effort cleanup" in source
    assert "keeping return-ball hidden until viewport is restored" in source
    assert "waitForViewportSize hard timeout; continuing best-effort cleanup" in source
    assert "clearMultiWindowReturnBallDeferredWork(state)" in source
    assert "state.viewportWaitFallbackTimer = setTimeout(pollViewportRestore, 50)" in source
    assert "runWhenStable({ timedOut: true })" in source
    assert "function revealReturnBallDragWindow()" in source
    assert "window.nekoPetDrag.reveal" in source
    assert "function dispatchReturnBallRevealFailed(reason, error)" in source
    assert "'return-ball-reveal-failed'" in source
    assert "'neko:return-ball-reveal-failed'" in source
    assert "Promise.resolve(revealResult)" in source
    assert "dispatchReturnBallRevealFailed('reveal-timeout')" in source
    assert "await revealReturnBallDragWindow()" not in source
    assert "function isNativeReturnBallDragDisabled()" in source
    assert "isNativeReturnBallDragDisabled() || !window.nekoPetDrag" in source
    assert "const dragStarted = window.nekoPetDrag.start(screenX, screenY)" in source
    assert "if (dragStarted === false)" in source

    begin_index = source.index("function beginDrag(screenX, screenY, event)")
    native_start_index = source.index("const dragStarted = window.nekoPetDrag.start(screenX, screenY)", begin_index)
    dispatch_start_index = source.index("reason: 'return-ball-drag-start'", begin_index)
    drag_style_index = source.index("document.body.dataset.nekoBallDrag = '1'", begin_index)

    assert begin_index < native_start_index < dispatch_start_index < drag_style_index

    finish_index = source.index("async function finishDrag(screenX, screenY)")
    no_move_start = source.index("if (!state.hasMoved) {", finish_index)
    no_move_end = source.index("const finalBounds = await resolveFinalWindowBounds", no_move_start)
    no_move_block = source[no_move_start:no_move_end]

    assert no_move_block.index("revealReturnBallDragWindow();") < no_move_block.index("dispatchReturnBallClick();")
    assert "reason: 'return-ball-drag-cancel'" not in no_move_block
    suppress_click_block = _source_slice_between(
        no_move_block,
        "if (suppressClick) {",
        "} else {",
        "no-move suppressed return-ball drag branch",
    )
    _assert_source_contains(
        suppress_click_block,
        "reason: 'return-ball-drag-end'",
        "no-move suppressed return-ball drag branch",
    )
    _assert_source_contains(
        suppress_click_block,
        "movedDistancePx: 0",
        "no-move suppressed return-ball drag branch",
    )
    _assert_source_contains(
        suppress_click_block,
        "dragCancelled: true",
        "no-move suppressed return-ball drag branch",
    )
    normal_click_block = no_move_block.split("} else {", 1)[1]
    assert "reason: 'return-ball-drag-end'" not in normal_click_block
    assert "dragCancelled: false" not in normal_click_block
    assert "dispatchReturnBallClick();" in normal_click_block


def test_desktop_return_ball_drag_recovers_when_mouse_release_is_lost():
    source = APP_UI_PATH.read_text(encoding="utf-8")

    assert "RETURN_BALL_DRAG_RECOVERY_POLL_MS = 250" in source
    assert "RETURN_BALL_DRAG_STALE_RECOVERY_MS = 12000" in source
    assert "function getReturnBallDragScreenCoordinate(value, fallback)" in source
    assert "Number.isFinite(value) ? value : fallback" in source
    assert "state.releaseScreenX || state.startScreenX" not in source
    assert "state.releaseScreenY || state.startScreenY" not in source
    assert "function finishDragIfMouseButtonReleased(event, reason)" in source
    assert "event.pointerType && event.pointerType !== 'mouse'" in source
    assert "event.buttons !== 0" in source
    window_blur_start = source.index("state.handleWindowBlur = () => {")
    window_blur_end = source.index("};", window_blur_start)
    window_blur_block = source[window_blur_start:window_blur_end]
    assert "cancelActiveDrag(" not in window_blur_block
    assert "scheduleReturnBallDragRecoveryCheck();" in window_blur_block
    assert "cancelActiveDrag('visibility-hidden')" in source
    assert "cancelActiveDrag('pagehide')" in source
    assert "cancelActiveDrag('pointercancel')" in source
    assert "cancelActiveDrag('stale-pointer-timeout')" in source
    assert "document.addEventListener('pointermove', state.handlePointerMove, true)" in source
    assert "document.addEventListener('pointerup', state.handlePointerUp, true)" in source
    assert "document.addEventListener('pointercancel', state.handlePointerCancel, true)" in source
    assert "window.addEventListener('blur', state.handleWindowBlur)" in source
    assert "document.addEventListener('visibilitychange', state.handleVisibilityChange)" in source
    assert "suppressClick ? 'return-ball-drag-cancel' : 'return-ball-drag-click'" in source
    assert "if (suppressClick)" in source
    assert "dragCancelled: true" in source
    assert "movedDistancePx: 0" in source
    assert "dispatchReturnBallClick();" in source
    assert "window.nekoPetDrag.stop(stopScreenX, stopScreenY)" in source
    # 已经移动过的拖拽被中断（截图/blur/超时）时也要传播取消标记，
    # 否则 moved 分支照常派发 drag-end，app-auto-goodbye 会当成真实释放降级猫档
    assert "dragCancelled: suppressClick" in source


def test_return_button_drag_has_single_owner_per_runtime_path():
    avatar_source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")
    live2d_source = LIVE2D_UI_BUTTONS_PATH.read_text(encoding="utf-8")
    vrm_source = VRM_UI_BUTTONS_PATH.read_text(encoding="utf-8")
    mmd_source = MMD_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    assert "function _isNekoNativeReturnBallDragDisabled()" in avatar_source
    assert "if (!window.__NEKO_MULTI_WINDOW__ || _isNekoNativeReturnBallDragDisabled())" in avatar_source
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


def test_cat1_walk_hover_invalidates_pending_playback_rate_source():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    play_hover_block = source[
        source.index('function _playNekoIdleHoverArt'):
        source.index('function _finishNekoIdleHoverArtAfterPlayback')
    ]

    assert '_clearNekoIdleGifPlaybackSource(art)' in play_hover_block
    assert play_hover_block.index('_clearNekoIdleGifPlaybackSource(art)') < play_hover_block.index('art.src = clickSrc')
    repeat_hover_block = play_hover_block[
        play_hover_block.index('if (art.__nekoIdleHoverSrc === clickSrc)'):
        play_hover_block.index('_clearNekoIdleHoverPlayback(art)')
    ]
    assert '_clearNekoIdleGifPlaybackSource(art)' in repeat_hover_block
    assert 'art.src = clickSrc' in repeat_hover_block


def test_idle_thought_bubble_hides_during_pending_long_press():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")
    app_ui_source = APP_UI_PATH.read_text(encoding="utf-8")
    css_source = INDEX_CSS_PATH.read_text(encoding="utf-8")

    assert "_NEKO_IDLE_RETURN_DRAG_PENDING_CLASS = 'is-drag-action-pending'" in source
    assert "function _setNekoIdleReturnDragPendingClasses(button, active)" in source
    assert "_setNekoIdleReturnDragPendingClasses(button, true);" in source
    assert "_setNekoIdleReturnDragPendingClasses(button, false);" in source

    prepare_block = _source_slice_between(
        source,
        "function _prepareNekoIdleReturnDragActionForContainer(container)",
        "function _startNekoIdleReturnDragActionForContainer(container)",
        "return button drag pending preparation",
    )
    _assert_source_contains(
        prepare_block,
        "_setNekoIdleReturnDragPendingClasses(button, true);",
        "return button drag pending preparation",
    )

    start_block = _source_slice_between(
        source,
        "function _startNekoIdleReturnDragActionForContainer(container)",
        "function _finishNekoIdleReturnDragAction(button, options = {})",
        "return button drag active start",
    )
    _assert_source_order(
        start_block,
        "return button drag active start",
        "_setNekoIdleReturnDragPendingClasses(button, false);",
        "_setNekoIdleReturnDragActionClasses(button, true);",
    )

    finish_block = _source_slice_between(
        source,
        "function _finishNekoIdleReturnDragAction(button, options = {})",
        "function _finishNekoIdleReturnDragActionForContainer(container, options = {})",
        "return button drag finish",
    )
    _assert_source_contains(
        finish_block,
        "_setNekoIdleReturnDragPendingClasses(button, false);",
        "return button drag finish",
    )

    assert "'return-ball-drag-cancel'" in app_ui_source
    cancel_handler = _source_slice_between(
        source,
        "if (detail.reason === 'return-ball-drag-cancel')",
        "if (detail.reason === 'return-ball-drag-start')",
        "return button drag cancel handler",
    )
    _assert_source_contains(
        cancel_handler,
        "_finishNekoIdleReturnDragActionForContainer(detail.container, { restoreArt: false });",
        "return button drag cancel handler",
    )
    assert ".neko-idle-return-btn.is-drag-action-pending .neko-idle-thought-bubble" in css_source


def test_return_button_drag_randomizes_asset_once_per_drag_action():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    set_drag_art_block = _source_slice_between(
        source,
        "function _setNekoIdleReturnDragActionArt(button, tier)",
        "function _prepareNekoIdleReturnDragActionForContainer(container)",
        "return drag action art",
    )
    _assert_source_contains(
        set_drag_art_block,
        "const dragSrc = button.__nekoIdleReturnDragAssetUrl || _pickNekoIdleReturnDragAssetUrl(tier);",
        "return drag action art",
    )
    _assert_source_contains(
        set_drag_art_block,
        "button.__nekoIdleReturnDragAssetUrl = dragSrc;",
        "return drag action art",
    )

    start_drag_block = _source_slice_between(
        source,
        "function _startNekoIdleReturnDragActionForContainer(container)",
        "function _finishNekoIdleReturnDragAction(button, options = {})",
        "return drag action start",
    )
    _assert_source_order(
        start_drag_block,
        "return drag action start",
        "state.tier = tier;",
        "button.__nekoIdleReturnDragAssetUrl = _pickNekoIdleReturnDragAssetUrl(tier);",
        "_setNekoIdleReturnDragActionArt(button, tier);",
    )
    assert "src: button.__nekoIdleReturnDragAssetUrl" in start_drag_block

    finish_drag_block = _source_slice_between(
        source,
        "function _finishNekoIdleReturnDragAction(button, options = {})",
        "function _finishNekoIdleReturnDragActionForContainer(container, options = {})",
        "return drag action finish",
    )
    _assert_source_contains(
        finish_drag_block,
        "button.__nekoIdleReturnDragAssetUrl = '';",
        "return drag action finish",
    )


def test_idle_thought_bubble_is_sound_triggered_with_fade():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")
    css_source = INDEX_CSS_PATH.read_text(encoding="utf-8")

    assert "_NEKO_IDLE_THOUGHT_BUBBLE_ACTIVE_CLASS = 'is-thought-bubble-active'" in source
    assert "_NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_CLASS = 'is-thought-bubble-sleeping'" in source
    assert "_NEKO_IDLE_THOUGHT_BUBBLE_ASSET_URL = '/static/assets/neko-idle/thought-items/cloud-thought-bubble.gif'" in source
    assert "_NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_ASSET_URL = '/static/assets/neko-idle/thought-items/sleeping-zzz.gif'" in source
    assert "const _NEKO_IDLE_THOUGHT_BUBBLE_ITEM_ASSET_URLS = Object.freeze([" in source
    assert "'/static/assets/neko-idle/thought-items/catnip-pouch.png'" in source
    assert "'/static/assets/neko-idle/thought-items/fish-cookie.png'" in source
    assert "'/static/assets/neko-idle/thought-items/toy-mouse.png'" in source
    assert "fish-cookie-transparent.png" not in source
    assert "_NEKO_IDLE_THOUGHT_BUBBLE_VISIBLE_MS = 5000" in source
    assert "_NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_FALLBACK_VISIBLE_MS = 8000" in source
    assert "_NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_VISIBLE_MS" not in source
    assert "function _pickNekoIdleThoughtBubbleBgAsset(tier)" in source
    assert "normalizedTier === _NEKO_IDLE_TIER_CAT2 && roll < 1 / 3" in source
    assert "normalizedTier === _NEKO_IDLE_TIER_CAT3 && roll < 2 / 3" in source
    assert "function _getNekoIdleAudioRemainingMs(audio)" in source
    assert "function _getNekoIdleThoughtBubbleVisibleMs(bubbleConfig, audio)" in source
    assert "function _scheduleNekoIdleThoughtBubbleHide(button, token, visibleMs)" in source
    assert "if (audio) return _getNekoIdleAudioRemainingMs(audio) || _NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_FALLBACK_VISIBLE_MS;" in source
    assert "function _getNekoIdleThoughtBubbleBgAssetUrl(assetUrl, restartToken = 0)" in source
    assert "function _getNekoIdleThoughtBubbleItemAssetUrl(assetUrl)" in source
    assert "function _pickNekoIdleThoughtBubbleItemAssetUrl(previousAssetUrl = '')" in source
    assert "const availableUrls = urls.length > 1 && previousAssetUrl" in source
    assert "urls.filter((url) => url !== previousAssetUrl)" in source
    assert "function _restartNekoIdleThoughtBubbleArt(button, tier)" in source
    assert "function _clearNekoIdleThoughtBubble(button)" in source
    assert "function _showNekoIdleThoughtBubbleForSound(tier, audio = null)" in source
    assert "button.__nekoIdleThoughtBubbleTier = normalizedTier;" in source
    assert "if (button.__nekoIdleThoughtBubbleTier && button.__nekoIdleThoughtBubbleTier !== normalizedTier)" in source
    assert "_NEKO_IDLE_THOUGHT_BUBBLE_VISIBLE_MS" in source

    bubble_helper_block = _source_slice_between(
        source,
        "function _showNekoIdleThoughtBubbleForSound(tier, audio = null)",
        "function _clearNekoIdleSleepSoundTimer()",
        "thought bubble helper",
    )
    _assert_source_order(
        bubble_helper_block,
        "thought bubble helper",
        "const bubbleConfig = _restartNekoIdleThoughtBubbleArt(button, normalizedTier);",
        "button.classList.add(_NEKO_IDLE_THOUGHT_BUBBLE_ACTIVE_CLASS);",
        "const visibleMs = _getNekoIdleThoughtBubbleVisibleMs(bubbleConfig, audio);",
        "_scheduleNekoIdleThoughtBubbleHide(button, timerToken, visibleMs);",
    )
    _assert_source_contains(
        bubble_helper_block,
        "audio.addEventListener('loadedmetadata'",
        "thought bubble helper",
    )
    _assert_source_contains(
        bubble_helper_block,
        "audio.addEventListener('ended'",
        "thought bubble helper",
    )
    _assert_source_contains(
        bubble_helper_block,
        "audio.addEventListener('error'",
        "thought bubble helper",
    )
    bubble_restart_block = _source_slice_between(
        source,
        "function _restartNekoIdleThoughtBubbleArt(button, tier)",
        "function _showNekoIdleThoughtBubbleForSound(tier, audio = null)",
        "thought bubble restart helper",
    )
    _assert_source_contains(
        bubble_restart_block,
        "button.classList.remove(_NEKO_IDLE_THOUGHT_BUBBLE_ACTIVE_CLASS);",
        "thought bubble restart helper",
    )
    _assert_source_contains(
        bubble_restart_block,
        "const bubbleConfig = _pickNekoIdleThoughtBubbleBgAsset(tier);",
        "thought bubble restart helper",
    )
    _assert_source_contains(
        bubble_restart_block,
        "button.classList.toggle(_NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_CLASS, !!bubbleConfig.sleeping);",
        "thought bubble restart helper",
    )
    _assert_source_contains(
        bubble_restart_block,
        "bg.src = _getNekoIdleThoughtBubbleBgAssetUrl(bubbleConfig.assetUrl, button.__nekoIdleThoughtBubbleRestartToken);",
        "thought bubble restart helper",
    )
    _assert_source_contains(
        bubble_restart_block,
        "const item = button.querySelector('.neko-idle-thought-bubble-item');",
        "thought bubble restart helper",
    )
    _assert_source_contains(
        bubble_restart_block,
        "const itemAssetUrl = _pickNekoIdleThoughtBubbleItemAssetUrl(button.__nekoIdleThoughtBubbleItemAssetUrl);",
        "thought bubble restart helper",
    )
    _assert_source_contains(
        bubble_restart_block,
        "button.__nekoIdleThoughtBubbleItemAssetUrl = itemAssetUrl;",
        "thought bubble restart helper",
    )
    _assert_source_contains(
        bubble_restart_block,
        "item.src = _getNekoIdleThoughtBubbleItemAssetUrl(itemAssetUrl);",
        "thought bubble restart helper",
    )
    _assert_source_contains(
        bubble_restart_block,
        "return bubbleConfig;",
        "thought bubble restart helper",
    )
    _assert_source_contains(
        bubble_restart_block,
        "void button.offsetWidth;",
        "thought bubble restart helper",
    )

    sleep_play_block = _source_slice_between(
        source,
        "function _playNekoIdleSleepSound(tier, token)",
        "function _scheduleNekoIdleSleepSoundInterval(tier, intervalStartedAt)",
        "sleep sound playback",
    )
    _assert_source_order(
        sleep_play_block,
        "sleep sound playback",
        "const audio = _playNekoIdleSound(_nekoIdleSleepSoundState, _pickNekoIdleSleepSoundSrc(config), config.volume);",
        "if (audio) _showNekoIdleThoughtBubbleForSound(tier, audio);",
    )

    ambient_play_block = _source_slice_between(
        source,
        "function _playNekoIdleCat1AmbientSound(token)",
        "function _scheduleNekoIdleCat1AmbientSoundInterval(intervalStartedAt)",
        "cat1 ambient sound playback",
    )
    _assert_source_order(
        ambient_play_block,
        "cat1 ambient sound playback",
        "const audio = _playNekoIdleSound(",
        "if (audio) _showNekoIdleThoughtBubbleForSound(_NEKO_IDLE_TIER_CAT1);",
        "_playNekoIdleCat1SoundReaction();",
    )

    bubble_block = _extract_css_block(css_source, ".neko-idle-thought-bubble")
    assert "opacity: 0;" in bubble_block
    assert "visibility: hidden;" in bubble_block
    assert "transition: opacity 360ms ease, visibility 0s linear 360ms;" in bubble_block

    active_block = _extract_css_block(
        css_source,
        ".neko-idle-return-btn.is-thought-bubble-active .neko-idle-thought-bubble",
    )
    assert "opacity: 1;" in active_block
    assert "visibility: visible;" in active_block
    assert "transition-delay: 0s;" in active_block

    assert "const thoughtBubble = document.createElement('span');" in source
    assert "const thoughtBubbleBg = document.createElement('img');" in source
    assert "thoughtBubbleBg.className = 'neko-idle-thought-bubble-bg';" in source
    assert "thoughtBubbleBg.src = _getNekoIdleThoughtBubbleBgAssetUrl(_NEKO_IDLE_THOUGHT_BUBBLE_ASSET_URL);" in source
    assert "const thoughtBubbleItem = document.createElement('img');" in source
    assert "thoughtBubbleItem.className = 'neko-idle-thought-bubble-item';" in source
    assert "thoughtBubbleItem.src = _getNekoIdleThoughtBubbleItemAssetUrl(_NEKO_IDLE_THOUGHT_BUBBLE_ITEM_ASSET_URLS[0]);" in source
    assert "thoughtBubble.appendChild(thoughtBubbleBg);" in source
    assert "thoughtBubble.appendChild(thoughtBubbleItem);" in source

    bubble_bg_block = _extract_css_block(css_source, ".neko-idle-thought-bubble-bg")
    assert "position: absolute;" in bubble_bg_block
    assert "inset: 0;" in bubble_bg_block
    assert "width: 100%;" in bubble_bg_block
    assert "height: 100%;" in bubble_bg_block
    assert "object-fit: contain;" in bubble_bg_block

    bubble_item_block = _extract_css_block(css_source, ".neko-idle-thought-bubble-item")
    assert "position: absolute;" in bubble_item_block
    assert "left: 50%;" in bubble_item_block
    assert "top: 36%;" in bubble_item_block
    assert "width: 47%;" in bubble_item_block
    assert "max-height: 38%;" in bubble_item_block
    assert "transform: translate(-50%, -50%);" in bubble_item_block
    assert "animation:" not in bubble_item_block
    active_item_block = _extract_css_block(
        css_source,
        ".neko-idle-return-btn.is-thought-bubble-active .neko-idle-thought-bubble-item",
    )
    assert "animation: neko-idle-thought-bubble-item-float 3600ms steps(24, end) infinite;" in active_item_block
    assert "@keyframes neko-idle-thought-bubble-item-float" in css_source
    assert "25% { transform: translate(-50%, calc(-50% - 1px)); }" in css_source
    assert "75% { transform: translate(-50%, calc(-50% + 1px)); }" in css_source
    thought_bubble_reduced_motion_block = _source_slice_between(
        css_source,
        "@media (prefers-reduced-motion: reduce) {\n    .neko-idle-return-btn.is-thought-bubble-active .neko-idle-thought-bubble-item",
        ".neko-idle-return-btn[data-neko-idle-tier=\"cat1\"] .neko-idle-thought-bubble",
        "thought bubble reduced motion block",
    )
    assert "animation: none;" in thought_bubble_reduced_motion_block
    sleeping_item_block = _extract_css_block(
        css_source,
        ".neko-idle-return-btn.is-thought-bubble-sleeping .neko-idle-thought-bubble-item",
    )
    assert "display: none;" in sleeping_item_block


def test_sleeping_cat_tiers_schedule_soft_random_sound_once_per_interval():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    assert "_NEKO_IDLE_SLEEP_SOUND_INTERVAL_MS = 5 * 60 * 1000" in source
    assert "_NEKO_IDLE_SLEEP_SOUND_VOLUME = 0.09" in source
    assert "function _playNekoIdleSound(state, src, volume)" in source
    assert "[_NEKO_IDLE_TIER_CAT2]" in source
    assert "[_NEKO_IDLE_TIER_CAT3]" in source
    assert "srcs: Object.freeze([" in source
    assert "'/static/assets/neko-idle/cat2-sleep1.mp3'" in source
    assert "'/static/assets/neko-idle/cat2-sleep2.mp3'" in source
    assert "'/static/assets/neko-idle/cat3-sleep1.mp3'" in source
    assert "'/static/assets/neko-idle/cat3-sleep2.mp3'" in source
    assert "function _pickNekoIdleSleepSoundSrc(config)" in source
    assert "Math.floor(Math.random() * srcs.length)" in source
    assert "_playNekoIdleSound(_nekoIdleSleepSoundState, _pickNekoIdleSleepSoundSrc(config), config.volume)" in source
    assert "audio.volume = Math.max(0, Math.min(1, Number(volume) || 0.2))" in source
    assert "audio.dispatchEvent(new Event('error'));" in source
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
    assert "_playNekoIdleCat1SoundReaction()" in source
    assert "state.targetKind !== _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE" in source
    assert "_playNekoIdleHoverArt(art, _NEKO_IDLE_TIER_CAT1);" in source
    assert "const reactionSrc = art.__nekoIdleHoverSrc;" in source
    assert "const reactionStartedAt = Math.max(0, Number(art.__nekoIdleHoverStartedAt) || Date.now());" in source
    assert "_finishNekoIdleHoverArtAfterPlayback(art, _NEKO_IDLE_TIER_CAT1);" in source
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
    assert '_NEKO_IDLE_CAT1_CHAT_GAP_PX = -12' in source
    assert '_NEKO_IDLE_CAT1_MINIMIZED_RIGHT_TO_LEFT_APPROACH_PX = 35' in source
    assert 'function _getNekoIdleCat1MinimizedSideApproachOffsetPx(facingRight, chatRect)' in source
    assert 'if (facingRight) return 0;' in source
    assert 'chatRect.right + profile.target.gapPx - approachOffsetPx' in source
    assert 'approachOffsetPx: approachOffsetPx' in source
    assert '_NEKO_IDLE_CAT1_WALK_SPEED_PX_PER_SEC = 82' in source
    assert '_NEKO_IDLE_CAT1_WALK_EXIT_DISTANCE_PX = 14' in source
    assert '_NEKO_IDLE_CAT1_WALK_MAX_SPEED_RATE = 1.5' in source
    assert '_NEKO_IDLE_CAT1_WALK_DISTANCE_INCREASE_THRESHOLD_PX' in source
    assert '_NEKO_IDLE_CAT1_WALK_DISTANCE_GROWTH_FOR_MAX_RATE_PX' in source
    assert '_NEKO_IDLE_CAT1_STRETCH_FINAL_HOLD_MS = 700' in source
    assert '_NEKO_IDLE_CAT1_WALK_ENTER_DISTANCE_PX' in source
    assert '_NEKO_IDLE_CAT1_WALK_EXIT_DISTANCE_PX' in source
    assert '_NEKO_IDLE_CAT1_RECHECK_MOVE_DISTANCE_PX' in source
    assert '_NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_STICK_MAX_SPEED_PX_PER_SEC = 1100' in source
    assert '_NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_STICK_MAX_STEP_PX = 210' in source
    assert '_NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_DROP_FAST_MOVE_COUNT = 3' in source
    assert 'compactTopEdgeFastMoveCount: 0' in source
    assert 'state.compactTopEdgeFastMoveCount = 0' in source
    assert 'state.compactTopEdgeFastMoveCount >= _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_DROP_FAST_MOVE_COUNT' in source
    assert "function _postNekoIdleCat1CompactMirrorState(payload)" in source
    assert "new CustomEvent('neko:idle-cat1-compact-mirror-state'" in source
    assert "via: 'local'" in source
    assert "return dispatchedLocal;" in source
    assert "assetUrl: options.assetUrl || _getNekoIdleReturnAssetUrl(_NEKO_IDLE_TIER_CAT1)" in source
    mirror_state_block = source.split("function _setNekoIdleCat1CompactMirrorActive", 1)[1].split(
        "const surfaceScreenRect = _getNekoIdleScreenRectFromCompactSurfaceRect(options.surfaceRect)",
        1,
    )[0]
    assert "inactiveReason === 'compact-surface-settled'" in mirror_state_block
    assert "clearTimeout(container.__nekoIdleCat1CompactMirrorSettleTimer);" in mirror_state_block
    immediate_clear_index = mirror_state_block.rindex("clearTimeout(container.__nekoIdleCat1CompactMirrorSettleTimer);")
    assert mirror_state_block.index("inactiveReason === 'compact-surface-settled'") < immediate_clear_index
    assert immediate_clear_index < mirror_state_block.index(
        "if (!container.__nekoIdleCat1CompactMirrorActive) return true;"
    )
    assert "_syncNekoIdleCat1CompactMirrorReaction(button, container, reactionSrc, 'cat1-sound-reaction')" in source
    assert "_getNekoIdleGifDurationMs(reactionSrc)" in source
    assert "const remainingMs = Math.max(0, (Number(durationMs) || 0) - elapsedMs);" in source
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
    walk_start_block = source[
        source.index('function _scheduleNekoIdleCat1WalkStart'):
        source.index('function _canScheduleNekoIdleCat1PairMove')
    ]
    assert "if (art && art.__nekoIdleHoverSrc)" in walk_start_block
    assert "_finishNekoIdleHoverArtAfterPlayback(art, profile.tier)" in walk_start_block
    assert walk_start_block.index("if (art && art.__nekoIdleHoverSrc)") < walk_start_block.index("if (state.pendingWalkReady)")
    assert '_NEKO_IDLE_CAT1_WALK_LONG_DELAY_MAX_MS = 5 * 60 * 1000' in source
    assert '_NEKO_IDLE_CAT1_PAIR_MOVE_SHORT_DELAY_MIN_MS = 5 * 1000' in source
    assert '_NEKO_IDLE_CAT1_PAIR_MOVE_SHORT_DELAY_MAX_MS = 90 * 1000' in source
    assert '_NEKO_IDLE_CAT1_PAIR_MOVE_LONG_DELAY_MAX_MS = 5 * 60 * 1000' in source
    assert '_NEKO_IDLE_CAT1_PAIR_MOVE_SPEED_PX_PER_SEC = 82' in source
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
    assert "if (!window.__NEKO_MULTI_WINDOW__ || _isNekoNativeReturnBallDragDisabled())" in source


def test_cat1_walk_is_blocked_while_return_ball_drag_is_active_or_pending():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    drag_setup = _source_slice_between(
        source,
        "ManagerPrototype._setupReturnButtonDrag = function(container) {",
        "container.addEventListener('mousedown'",
        "return button drag setup",
    )
    handle_start = _source_slice_between(
        source,
        "const handleStart = (clientX, clientY) => {",
        "const handleMove = (clientX, clientY) => {",
        "return button drag start handler",
    )
    handle_end = _source_slice_between(
        source,
        "const handleEnd = () => {",
        "container.addEventListener('mousedown'",
        "return button drag end handler",
    )

    for expected in (
        "let dragSafetyTimer = 0;",
        "let dragSafetyToken = 0;",
        "const clearDragSafetyTimer = () => {",
        "const resetDragStateAfterMissingEnd = (safetyToken) => {",
        "if (dragSafetyToken !== safetyToken || !isDragging) return;",
        "const finishDragState = (moved, safetyToken) => {",
        "if (safetyToken !== dragSafetyToken) return;",
        "container.setAttribute('data-dragging', 'false');",
        "_dispatchNekoIdleReturnBallManualMove(container, 'return-ball-drag-end'",
    ):
        _assert_source_contains(drag_setup, expected, "return button drag setup")
    _assert_source_order(
        drag_setup,
        "return button drag setup helpers",
        "const finishDragState = (moved, safetyToken) => {",
        "const resetDragStateAfterMissingEnd = (safetyToken) => {",
        "finishDragState(moved, safetyToken);",
    )
    _assert_source_contains(
        handle_start,
        "container.setAttribute('data-dragging', 'pending')",
        "return button drag start handler",
    )
    _assert_source_contains(handle_start, "const safetyToken = dragSafetyToken + 1", "return button drag start handler")
    _assert_source_contains(handle_start, "dragSafetyTimer = setTimeout(() => {", "return button drag start handler")
    _assert_source_contains(
        handle_start,
        "resetDragStateAfterMissingEnd(safetyToken);",
        "return button drag start handler",
    )
    _assert_source_contains(handle_start, "}, 5000);", "return button drag start handler")
    _assert_source_order(
        handle_start,
        "return button drag start handler",
        "clearDragSafetyTimer();",
        "container.setAttribute('data-dragging', 'pending')",
        "dragSafetyTimer = setTimeout(() => {",
    )
    _assert_source_contains(handle_end, "clearDragSafetyTimer();", "return button drag end handler")
    _assert_source_contains(handle_end, "const safetyToken = dragSafetyToken;", "return button drag end handler")
    _assert_source_contains(
        handle_end,
        "finishDragState(moved, safetyToken);",
        "return button drag end handler",
    )
    _assert_source_order(
        handle_end,
        "return button drag end handler",
        "clearDragSafetyTimer();",
        "if (isDragging) {",
        "const safetyToken = dragSafetyToken;",
        "finishDragState(moved, safetyToken);",
    )

    sync_block = _source_slice_between(
        source,
        "function _syncNekoIdleCat1Journey",
        "function _scheduleNekoIdleCat1JourneySync(button)",
        "cat1 journey sync",
    )
    for expected in (
        "const initialContainer = _getNekoIdleReturnContainerFromButton(button)",
        "const initialDragging = initialContainer && initialContainer.getAttribute('data-dragging')",
        "if (initialDragging && initialDragging !== 'false') return",
    ):
        _assert_source_contains(sync_block, expected, "cat1 journey sync")
    _assert_source_order(
        sync_block,
        "cat1 journey sync drag guard",
        "if (_isNekoIdleCompactSurfaceDragging()) return",
        "if (initialDragging && initialDragging !== 'false') return",
        "const normalizedTier",
    )

    container_observer = _source_slice_between(
        source,
        "state.containerObserver = new MutationObserver(() => {",
        "state.containerObserver.observe(container",
        "cat1 container observer",
    )
    _assert_source_contains(
        container_observer,
        "const observerDragging = container.getAttribute('data-dragging');",
        "cat1 container observer",
    )
    _assert_source_contains(
        container_observer,
        "if (observerDragging && observerDragging !== 'false') return;",
        "cat1 container observer",
    )

    walk_start = _source_slice_between(
        source,
        "function _startNekoIdleCat1Walk",
        "function _scheduleNekoIdleCat1WalkStart",
        "cat1 walk start",
    )
    for expected in (
        "if (_isNekoIdleReturnDragActionActive(button)) return",
        "const walkContainer = _getNekoIdleReturnContainerFromButton(button)",
        "const walkDragging = walkContainer && walkContainer.getAttribute('data-dragging')",
        "if (walkDragging && walkDragging !== 'false') return",
    ):
        _assert_source_contains(walk_start, expected, "cat1 walk start")
    _assert_source_order(
        walk_start,
        "cat1 walk start drag guard",
        "if (!state) return",
        "if (_isNekoIdleReturnDragActionActive(button)) return",
        "const profile = state.profile",
    )


def test_return_button_local_no_move_release_clears_pending_drag_state():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    drag_setup = _source_slice_between(
        source,
        "ManagerPrototype._setupReturnButtonDrag = function(container) {",
        "ManagerPrototype._addReturnButtonBreathingAnimation = function()",
        "return button drag setup",
    )
    finish_drag_state = _source_slice_between(
        drag_setup,
        "const finishDragState = (moved, safetyToken) => {",
        "const resetDragStateAfterMissingEnd = (safetyToken) => {",
        "return button drag finish helper",
    )

    _assert_source_order(
        finish_drag_state,
        "return button no-move release cleanup branch",
        "if (moved) {",
        "_dispatchNekoIdleReturnBallManualMove(container, 'return-ball-drag-end'",
        "} else {",
        "_dispatchNekoIdleReturnBallManualMove(container, 'return-ball-drag-cancel'",
    )


def test_cat1_minimized_ball_inside_cat_finishes_without_side_retarget_jitter():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    assert "function _isNekoIdleRectCenterInsideRect(innerRect, outerRect)" in source
    assert "function _makeNekoIdleCat1CurrentSideTarget(rect, chatRect, options)" in source
    assert "function _getNekoIdleRectDirectionalOverlapPx" not in source
    assert "function _getNekoIdleCat1MinimizedContactTargetOverlapPx" not in source

    side_target_block = source.split("function _getNekoIdleCat1SideTarget(container, chatRect)", 1)[1].split(
        "function _getNekoIdleCat1CompactTopEdgeBounds",
        1,
    )[0]
    # #1758 把“按朝向算侧位点”的计算抽到了 _computeNekoIdleCat1SideTargetForLook /
    # _pickNekoIdleCat1ForwardSideTarget，_getNekoIdleCat1SideTarget 里只剩“提交侧 + 滞回”选向，
    # 再对最终 target（无论来自首帧 forward-pick 还是提交侧）套用本贴球护栏——避免贴球时倒退取侧抽搐。
    assert "_pickNekoIdleCat1ForwardSideTarget(rect, chatRect)" in side_target_block
    assert "_computeNekoIdleCat1SideTargetForLook(rect, chatRect, lookFacingRight)" in side_target_block
    assert "_isNekoIdleRectCenterInsideRect(chatRect, rect)" in side_target_block
    assert "target.moveFacingRight !== target.lookFacingRight" in side_target_block
    assert "_makeNekoIdleCat1CurrentSideTarget(rect, chatRect" in side_target_block
    assert "contactDistance <= profile.target.exitDistancePx" not in side_target_block
    # 护栏必须在解析出 target 之后、提交/返回之前生效，且 inside 判定先于取“原地侧目标”。
    assert side_target_block.index("const target = lookFacingRight === null") < side_target_block.index("_isNekoIdleRectCenterInsideRect(chatRect, rect)")
    assert side_target_block.index("_isNekoIdleRectCenterInsideRect(chatRect, rect)") < side_target_block.index("_makeNekoIdleCat1CurrentSideTarget(rect, chatRect")

    center_inside_block = source.split("function _isNekoIdleRectCenterInsideRect(innerRect, outerRect)", 1)[1].split(
        "function _makeNekoIdleCat1CurrentSideTarget",
        1,
    )[0]
    assert "const innerCenterX = innerLeft + innerWidth / 2;" in center_inside_block
    assert "const innerCenterY = innerTop + innerHeight / 2;" in center_inside_block
    assert "return innerCenterX >= outerLeft && innerCenterX <= outerRight &&" in center_inside_block
    assert "innerCenterY >= outerTop && innerCenterY <= outerBottom;" in center_inside_block

    current_target_block = source.split("function _makeNekoIdleCat1CurrentSideTarget(rect, chatRect, options)", 1)[1].split(
        "function _getNekoIdleCat1SideTarget",
        1,
    )[0]
    assert "left: rect.left" in current_target_block
    assert "top: rect.top" in current_target_block
    assert "distance: 0" in current_target_block
    assert "moveFacingRight: null" in current_target_block
    assert "approachOffsetPx" in current_target_block
    assert "approachOffsetPx: _getNekoIdleCat1MinimizedSideApproachOffsetPx(facingRight, chatRect)" in current_target_block
    assert "kind: _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE" in current_target_block


def test_return_button_idle_tier_assets_are_version_tracked():
    for path in (APP_UI_PATH, APP_INTERPAGE_PATH, COMMON_UI_HUD_PATH,
                 APP_REACT_CHAT_WINDOW_PATH,
                 CAT1_ASSET_PATH, CAT1_CLICK_ASSET_PATH,
                 CAT2_ASSET_PATH, CAT2_CLICK_ASSET_PATH,
                 CAT3_ASSET_PATH, CAT3_CLICK_ASSET_PATH,
                 CAT1_VOICE_CLICK_PATH, CAT1_VOICE1_PATH,
                 CAT1_VOICE2_PATH, CAT1_VOICE3_PATH,
                 CAT2_SLEEP_SOUND_PATH, CAT2_SLEEP_SOUND2_PATH,
                 CAT3_SLEEP_SOUND_PATH, CAT3_SLEEP_SOUND2_PATH,
                 CAT1_WALK_ASSET_PATH, CAT1_STRETCH_ASSET_PATH,
                 CAT1_INTERACTIVE_ASSET_PATH,
                 CAT1_DRAG_ASSET_PATH, CAT2_DRAG_ASSET_PATH,
                 CAT3_DRAG_ASSET_PATH, CAT4_DRAG_ASSET_PATH,
                 CAT_MODEL_CHANGE_ASSET_PATH,
                 THOUGHT_BUBBLE_ASSET_PATH, SLEEPING_THOUGHT_BUBBLE_ASSET_PATH,
                 *THOUGHT_BUBBLE_ITEM_ASSET_PATHS):
        assert path in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS
        assert path.is_file()


def test_sleep_sound_assets_match_current_tier_assignment():
    assert CAT2_SLEEP_SOUND_PATH.is_file()
    assert CAT2_SLEEP_SOUND2_PATH.is_file()
    assert CAT3_SLEEP_SOUND_PATH.is_file()
    assert CAT3_SLEEP_SOUND2_PATH.is_file()
    assert CAT2_SLEEP_SOUND_PATH.stat().st_size > 0
    assert CAT2_SLEEP_SOUND2_PATH.stat().st_size > 0
    assert CAT3_SLEEP_SOUND_PATH.stat().st_size > 0
    assert CAT3_SLEEP_SOUND2_PATH.stat().st_size > 0


def test_no_box_shadow_or_border_in_base_return_btn_css():
    source = INDEX_CSS_PATH.read_text(encoding="utf-8")

    base_block = _extract_neko_return_btn_block(source)
    assert base_block
    assert 'box-shadow' not in base_block
    assert 'border' not in base_block
    assert 'backdrop-filter' not in base_block


def _extract_css_block(source, selector):
    """Extract a selector's CSS block body from source.

    Args:
        source: CSS source string to search.
        selector: Selector text whose following brace-delimited block is read.

    Returns:
        The string inside the matched braces, or '' when the selector/opening
        brace/closing brace is not found. Nested braces are handled with
        brace-depth tracking.
    """
    start = source.find(selector)
    if start == -1:
        return ''
    open_brace = source.find('{', start + len(selector))
    if open_brace == -1:
        return ''
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
