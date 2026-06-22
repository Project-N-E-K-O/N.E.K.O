from pathlib import Path

from PIL import Image

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
LIVE2D_CORE_PATH = PROJECT_ROOT / "static" / "live2d-core.js"
LIVE2D_INTERACTION_PATH = PROJECT_ROOT / "static" / "live2d-interaction.js"
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
CAT1_RAPID_DRAG_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat-move-5.gif"
CAT1_RAPID_DRAG_SOUND_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat1-voice-funny.mp3"
CAT_MODEL_CHANGE_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat_model_change.gif"
THOUGHT_BUBBLE_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "thought-items" / "cloud-thought-bubble.gif"
THOUGHT_BUBBLE_POP_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "thought-items" / "cloud-thought-bubble-pop.gif"
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
    assert "/static/assets/neko-idle/cat-idle-cat-move-5.gif" in source
    assert "/static/assets/neko-idle/cat1-voice-funny.mp3" in source
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
    assert "function ensureModelViewportReadyBeforeShowCurrentModel()" in source
    assert "function restoreReturnBallAfterBlockedModelViewport(event)" in source
    assert "function shouldBlockCatToModelTransitionForModelViewport(direction)" in source
    assert "const modelViewportReady = await ensureModelViewportReadyBeforeShowCurrentModel();" in source
    assert "blocked model display because Pet viewport is still return-ball sized" in source
    assert "function setPendingNativeModelViewportRestoreBounds(bounds)" in source
    assert "if (isNativeReturnBallViewportSize(width, height)) {" in source
    assert "const pendingRestoreBounds = restoreBounds || {" in source
    assert "setPendingNativeModelViewportRestoreBounds(pendingRestoreBounds);" in source
    assert "setPendingNativeModelViewportRestoreBounds(finalBounds || {" in source
    assert "bounds.requestedBounds || (" in source
    assert "NEKO_MODEL_CAT_TRANSITION_MODEL_SCALE = 0.38" in source
    assert "function getModelCatTransitionScaleTransform()" in source
    assert "getModelCatTransitionScaleTransform()" in source
    assert "function prepareModelReturnContainer(container, rect, options = {})" in source
    assert "container.style.transform = 'scale(1) translateZ(0)'" in source
    assert "function startModelReturnEnterWait(container)" in source
    assert "function waitForModelReturnEnterToSettle()" in source
    assert "resolveModelReturnEnter('cleanup')" in source
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
    _assert_source_order(
        return_enter_block,
        "model return enter exposes completion before cleanup",
        "startModelReturnEnterWait(container);",
        "container.style.transform = getModelCatTransitionScaleTransform();",
        "container.style.transform = 'scale(1) translateZ(0)';",
        "window._nekoModelReturnEnterTimer = setTimeout(() => {",
        "resolveModelReturnEnter('cleanup')",
    )
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
    assert "returnButtonContainer.setAttribute('data-neko-model-cat-transitioning', 'cat-to-model');" not in avatar_source
    assert "let nekoModelCatRevealPlaybackToken = 0" in source
    assert "function buildNekoModelCatRevealPlaybackUrl(src, playbackToken)" in source
    assert "url.searchParams.set('reveal'" in source
    assert "function restartNekoModelCatRevealArt(container)" in source
    assert "const isCurrentTransition = () => isNekoModelCatTransitionTokenCurrent(token)" in source
    assert "if (!isCurrentTransition()) return;" in source
    transition_function_block = source[
        source.index("function playNekoModelCatTransition(options = {})"):
        source.index("function resetReturnBallTemporaryStyle(container)")
    ]
    _assert_source_order(
        transition_function_block,
        "cat-to-model transition blocks before reserving transition state",
        "if (shouldBlockCatToModelTransitionForModelViewport(direction)) {",
        "reason: 'model-viewport-not-restored'",
        "if (nekoModelCatTransitionActive) {",
        "container.setAttribute('data-neko-model-cat-transitioning', direction);",
    )
    viewport_guard_start = source.index("const modelViewportReady = await ensureModelViewportReadyBeforeShowCurrentModel();")
    show_current_model_block = source[
        viewport_guard_start:
        source.index("try {", viewport_guard_start)
    ]
    _assert_source_order(
        show_current_model_block,
        "showCurrentModel viewport guard ordering",
        "const modelViewportReady = await ensureModelViewportReadyBeforeShowCurrentModel();",
        "if (!modelViewportReady.ready) {",
        "return false;",
    )
    goodbye_reset_index = source.index("// 重置 goodbye 标志")
    assert viewport_guard_start < goodbye_reset_index
    return_handler_guard_start = source.index("let modelDisplayReady = true;")
    return_handler_block = source[
        return_handler_guard_start:
        source.index("await settleReturnedModelBounds(returnModelWasMoved);", return_handler_guard_start)
    ]
    _assert_source_order(
        return_handler_block,
        "return handler stops when model viewport is still shrunken",
        "let modelDisplayReady = true;",
        "modelDisplayReady = await showCurrentModel();",
        "if (modelDisplayReady === false) {",
        "return;",
    )
    return_handler_start = source.index("const handleReturnClick = async (event) => {")
    return_handler_full_block = source[
        return_handler_start:
        source.index("await settleReturnedModelBounds(returnModelWasMoved);", return_handler_start)
    ]
    pre_return_guard_start = return_handler_full_block.index("const preReturnViewportReady = await ensureModelViewportReadyBeforeShowCurrentModel();")
    return_handler_start_block = return_handler_full_block[
        pre_return_guard_start:
        return_handler_full_block.index("const isReturningToPngtuber")
    ]
    _assert_source_order(
        return_handler_start_block,
        "return handler preserves cat state until viewport can restore",
        "const preReturnViewportReady = await ensureModelViewportReadyBeforeShowCurrentModel();",
        "if (!preReturnViewportReady.ready) {",
        "restoreReturnBallAfterBlockedModelViewport(event);",
        "return;",
    )
    assert return_handler_full_block.index("const preReturnViewportReady = await ensureModelViewportReadyBeforeShowCurrentModel();") < return_handler_full_block.index("window.live2dManager._goodbyeClicked = false;")
    restore_block = source[
        source.index("function restoreReturnBallAfterBlockedModelViewport(event)"):
        source.index("// 请她回来按钮（统一处理函数）")
    ]
    assert "String(event && event.type || '')" in restore_block
    assert "document.getElementById(`${match[1]}-return-button-container`)" in restore_block
    assert "revealReturnBallContainer(container, 'return-ball-model-viewport-blocked')" in restore_block
    assert "showReturnBallContainer(container, returnRect)" in restore_block
    settle_block = source[
        source.index("async function settleReturnedModelBounds(shouldSaveWhenUnchanged)"):
        source.index("function cancelReturnBallReveal(container)")
    ]
    _assert_source_order(
        settle_block,
        "return settle waits for model enter animation before snap/save",
        "await waitForModelReturnEnterToSettle();",
        "await waitForAnimationFrames(2);",
        "if (window.mmdManager && window.mmdManager.currentModel",
        "if (window.vrmManager && window.vrmManager.currentModel",
        "if (window.live2dManager) {",
    )
    viewport_ready_block = source[
        source.index("async function ensureModelViewportReadyBeforeShowCurrentModel()"):
        source.index("// --- showCurrentModel ---")
    ]
    _assert_source_order(
        viewport_ready_block,
        "model viewport guard blocks raw return-ball viewport without trusting invalid target",
        "if (!restoreBounds) {",
        "isNativeReturnBallViewportSize(window.innerWidth, window.innerHeight)",
        "ready: false",
        "return { ready: true, skipped: true };",
    )

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


def test_pngtuber_return_restores_pointer_events():
    source = APP_UI_PATH.read_text(encoding="utf-8")
    branch = source[
        source.index("} else if (effectiveModelType === 'pngtuber') {"):
        source.index("const live2dContainerPngtuber = document.getElementById('live2d-container');")
    ]

    assert "prepareModelReturnContainer(pngtuberContainer, consumeModelReturnEnterRect(), { clearPointerEvents: true });" in branch
    assert "pngtuberContainer.style.setProperty('pointer-events', 'auto', 'important');" in branch
    assert "pngtuberImage.style.setProperty('pointer-events', 'auto', 'important');" in branch
    assert "pngtuberContainer.style.pointerEvents = 'none';" not in branch


def test_return_button_idle_tier_styles_are_present():
    source = INDEX_CSS_PATH.read_text(encoding="utf-8")
    app_ui_source = APP_UI_PATH.read_text(encoding="utf-8")

    assert '.neko-idle-return-btn[data-neko-idle-tier="cat2"]' in source
    assert '.neko-idle-return-btn[data-neko-idle-tier="cat3"]' in source
    assert '.neko-idle-return-btn.is-cat1-facing-right' in source
    assert '.neko-idle-return-btn.is-cat1-edge-peek-left > .neko-idle-return-art' in source
    assert '.neko-idle-return-btn.is-cat1-edge-peek-right > .neko-idle-return-art' in source
    assert '.neko-idle-return-btn.is-cat1-edge-peek-top > .neko-idle-return-art' in source
    assert '.neko-idle-return-btn.is-cat1-edge-peek-bottom > .neko-idle-return-art' in source
    assert '.neko-idle-return-btn.is-cat1-edge-peek-top-left > .neko-idle-return-art' in source
    assert '.neko-idle-return-btn.is-cat1-edge-peek-top-right > .neko-idle-return-art' in source
    assert '.neko-idle-return-btn.is-cat1-edge-peek-bottom-left > .neko-idle-return-art' in source
    assert '.neko-idle-return-btn.is-cat1-edge-peek-bottom-right > .neko-idle-return-art' in source
    assert "--neko-idle-return-edge-transform: rotate(0deg);" in source
    assert "--neko-idle-return-edge-transform: rotate(60deg);" in source
    assert "--neko-idle-return-edge-transform: rotate(-60deg);" in source
    assert "--neko-idle-return-edge-transform: rotate(180deg);" in source
    assert "--neko-idle-return-edge-transform: rotate(120deg);" in source
    assert "--neko-idle-return-edge-transform: rotate(240deg);" in source
    assert "--neko-idle-return-edge-visual-shift-y: 0px;" in source
    assert "top: var(--neko-idle-return-edge-visual-shift-y);" in source
    assert "transform: var(--neko-idle-return-facing-transform) var(--neko-idle-return-edge-transform);" in source
    assert "function _getNekoIdleCat1EdgePeekVisualShiftY" in app_ui_source
    assert "actualBoundsOffset" in app_ui_source
    assert "placement.visualShiftY" in app_ui_source
    assert "art.style.setProperty('--neko-idle-return-edge-visual-shift-y'" in app_ui_source
    assert "art.style.removeProperty('--neko-idle-return-edge-visual-shift-y')" in app_ui_source
    assert "button.style.setProperty('--neko-idle-return-edge-visual-shift-y'" not in app_ui_source
    assert "--neko-idle-return-edge-visual-shift-y:-30px" not in app_ui_source


def test_cat1_edge_peek_only_applies_after_drag_release():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")
    app_ui_source = APP_UI_PATH.read_text(encoding="utf-8")

    assert "_NEKO_IDLE_CAT1_EDGE_PEEK_TRIGGER_RATIO = 0.025" in source
    assert "_NEKO_IDLE_CAT1_EDGE_PEEK_HIDDEN_RATIO = 0.4" in source
    assert "NEKO_IDLE_CAT1_EDGE_PEEK_TRIGGER_RATIO = 0.025" in app_ui_source
    assert "NEKO_IDLE_CAT1_EDGE_PEEK_HIDDEN_RATIO = 0.4" in app_ui_source
    for class_name in (
        "is-cat1-edge-peek-left",
        "is-cat1-edge-peek-right",
        "is-cat1-edge-peek-top",
        "is-cat1-edge-peek-bottom",
        "is-cat1-edge-peek-top-left",
        "is-cat1-edge-peek-top-right",
        "is-cat1-edge-peek-bottom-left",
        "is-cat1-edge-peek-bottom-right",
    ):
        assert class_name in source
        assert class_name in app_ui_source

    placement_block = _source_slice_between(
        source,
        "function _getNekoIdleCat1EdgePeekPlacement(left, top, width, height, viewportWidth, viewportHeight)",
        "function _applyNekoIdleCat1EdgePeek(container, placement)",
        "cat1 edge peek placement",
    )
    _assert_source_order(
        placement_block,
        "cat1 edge peek top corner priority",
        "const centerX = currentLeft + w / 2;",
        "if (nearTop) {",
        "if (nearLeft || centerX <= w) edge = 'top-left';",
        "else if (nearRight || centerX >= viewportW - w) edge = 'top-right';",
        "else edge = 'top';",
        "} else if (nearBottom) {",
        "if (nearLeft || centerX <= w) edge = 'bottom-left';",
        "else if (nearRight || centerX >= viewportW - w) edge = 'bottom-right';",
        "else edge = 'bottom';",
        "} else if (nearLeft) {",
        "edge = 'left';",
    )
    assert "const horizontalThreshold = w * _NEKO_IDLE_CAT1_EDGE_PEEK_TRIGGER_RATIO;" in placement_block
    assert "const verticalThreshold = h * _NEKO_IDLE_CAT1_EDGE_PEEK_TRIGGER_RATIO;" in placement_block
    assert "const hiddenX = w * _NEKO_IDLE_CAT1_EDGE_PEEK_HIDDEN_RATIO;" in placement_block
    assert "const hiddenY = h * _NEKO_IDLE_CAT1_EDGE_PEEK_HIDDEN_RATIO;" in placement_block
    assert "nextLeft = -hiddenX;" in placement_block
    assert "nextLeft = viewportW - w + hiddenX;" in placement_block
    assert "nextTop = -hiddenY;" in placement_block
    assert "nextTop = viewportH - h + hiddenY;" in placement_block
    assert "const centerX = currentLeft + w / 2;" in app_ui_source
    assert "if (nearLeft || centerX <= w) edge = 'top-left';" in app_ui_source
    assert "else if (nearRight || centerX >= viewportW - w) edge = 'top-right';" in app_ui_source
    assert "if (nearLeft || centerX <= w) edge = 'bottom-left';" in app_ui_source
    assert "else if (nearRight || centerX >= viewportW - w) edge = 'bottom-right';" in app_ui_source

    edge_apply_block = _source_slice_between(
        source,
        "function _applyNekoIdleCat1EdgePeekAfterDrag(container, left, top, viewportWidth, viewportHeight)",
        "function _restoreNekoIdleCat1EdgePeekBeforeDrag(container)",
        "cat1 edge peek apply after drag",
    )
    assert "if (!container || !_isNekoIdleCat1EdgePeekEligible(container)) return false;" in edge_apply_block
    assert "_getNekoIdleCat1EdgePeekPlacement(left, top, w, h, viewportWidth, viewportHeight)" in edge_apply_block
    assert "function _isNekoIdleCat1EdgePeekActive(containerOrButton)" in source
    assert "function _getNekoIdleCat1EdgePeekActiveEdge(containerOrButton)" in source
    assert "function _reclampNekoIdleCat1EdgePeekToViewport(containerOrButton)" in source
    assert "return button.classList.contains(className);" in source
    assert "function _clearNekoIdleCat1EdgePeekForTierExit(container)" in source

    apply_edge_block = _source_slice_between(
        source,
        "function _applyNekoIdleCat1EdgePeek(container, placement)",
        "function _applyNekoIdleCat1EdgePeekAfterDrag(container, left, top, viewportWidth, viewportHeight)",
        "cat1 edge peek apply",
    )
    _assert_source_order(
        apply_edge_block,
        "cat1 edge peek cancels queued automatic movement",
        "button.classList.add(`is-cat1-edge-peek-${placement.edge}`);",
        "_cancelNekoIdleCat1Journey(button, { resetArt: false, preserveObservers: true });",
        "container.style.left = `${placement.left}px`;",
    )

    edge_reclamp_block = _source_slice_between(
        source,
        "function _reclampNekoIdleCat1EdgePeekToViewport(containerOrButton)",
        "function _restoreNekoIdleCat1EdgePeekBeforeDrag(container)",
        "cat1 edge peek viewport reclamp",
    )
    _assert_source_order(
        edge_reclamp_block,
        "cat1 edge peek viewport reclamp preserves the active edge",
        "const edge = _getNekoIdleCat1EdgePeekActiveEdge(button);",
        "const viewportW = Math.max(w, window.innerWidth || 0);",
        "const viewportH = Math.max(h, window.innerHeight || 0);",
        "const nextLeft = edge.includes('left')",
        "? -hiddenX",
        "? viewportW - w + hiddenX",
        ": _clampNekoIdleCat1EdgePeekCoordinate(currentLeft, 0, viewportW - w));",
        "const nextTop = edge.includes('top')",
        "? -hiddenY",
        "? viewportH - h + hiddenY",
        ": _clampNekoIdleCat1EdgePeekCoordinate(currentTop, 0, viewportH - h));",
    )
    _assert_source_order(
        edge_reclamp_block,
        "cat1 edge peek viewport reclamp rewrites fixed position",
        "container.style.left = `${Math.round(nextLeft)}px`;",
        "container.style.top = `${Math.round(nextTop)}px`;",
        "container.style.right = '';",
        "container.style.bottom = '';",
        "container.style.transform = 'none';",
    )

    finish_drag_block = _source_slice_between(
        source,
        "const finishDragState = (moved, safetyToken) => {",
        "const resetDragStateAfterMissingEnd = (safetyToken) => {",
        "return button drag finish",
    )
    _assert_source_order(
        finish_drag_block,
        "cat1 edge peek before drag-end event",
        "if (moved) {",
        "const finalLeft = parseFloat(container.style.left);",
        "const finalTop = parseFloat(container.style.top);",
        "_applyNekoIdleCat1EdgePeekAfterDrag(",
        "Number.isFinite(finalLeft) ? finalLeft : containerStartX,",
        "Number.isFinite(finalTop) ? finalTop : containerStartY,",
        "container.setAttribute('data-dragging', 'false');",
        "const dispatchLeft = parseFloat(container.style.left);",
        "const dispatchTop = parseFloat(container.style.top);",
        "_dispatchNekoIdleReturnBallManualMove(container, 'return-ball-drag-end'",
        "Number.isFinite(dispatchLeft) ? dispatchLeft : containerStartX",
        "Number.isFinite(dispatchTop) ? dispatchTop : containerStartY",
    )

    journey_sync_block = _source_slice_between(
        source,
        "function _syncNekoIdleCat1Journey(button, tier)",
        "function _pauseNekoIdleCat1Journey(button)",
        "cat1 journey sync",
    )
    _assert_source_order(
        journey_sync_block,
        "cat1 edge peek blocks automatic walk",
        "if (!button) return;",
        "if (_isNekoIdleCat1EdgePeekActive(button)) {",
        "_cancelNekoIdleCat1Journey(button, { resetArt: false, preserveObservers: true });",
        "if (_isNekoIdleCompactSurfaceDragging()) return;",
    )
    assert (
        "_cancelNekoIdleCat1Journey(button, { resetArt: false, preserveObservers: true });\n"
        "        return;\n"
        "    }\n"
        "    if (_isNekoIdleCompactSurfaceDragging()) return;"
    ) in journey_sync_block

    walk_start_block = _source_slice_between(
        source,
        "function _startNekoIdleCat1Walk(button, target)",
        "function _scheduleNekoIdleCat1WalkStart(button, target)",
        "cat1 walk start",
    )
    _assert_source_order(
        walk_start_block,
        "cat1 edge peek blocks already queued walk start",
        "if (!state) return;",
        "if (_isNekoIdleCat1EdgePeekActive(button)) {",
        "_cancelNekoIdleCat1Journey(button, { resetArt: false, preserveObservers: true });",
    )
    assert (
        "_cancelNekoIdleCat1Journey(button, { resetArt: false, preserveObservers: true });\n"
        "        return;\n"
        "    }\n"
        "    if (_isNekoIdleReturnDragActionActive(button)) return;"
    ) in walk_start_block

    schedule_walk_block = _source_slice_between(
        source,
        "function _scheduleNekoIdleCat1WalkStart(button, target)",
        "function _canScheduleNekoIdleCat1PairMove(button, state)",
        "cat1 walk scheduling",
    )
    _assert_source_order(
        schedule_walk_block,
        "cat1 edge peek blocks new walk scheduling",
        "if (!state || state.paused) return;",
        "if (_isNekoIdleCat1EdgePeekActive(button)) {",
        "_cancelNekoIdleCat1Journey(button, { resetArt: false, preserveObservers: true });",
    )
    assert (
        "_cancelNekoIdleCat1Journey(button, { resetArt: false, preserveObservers: true });\n"
        "        return;\n"
        "    }\n"
        "    const profile = state.profile || _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;"
    ) in schedule_walk_block

    pair_move_gate_block = _source_slice_between(
        source,
        "function _canScheduleNekoIdleCat1PairMove(button, state)",
        "function _finishNekoIdleCat1PairMove(button)",
        "cat1 pair move scheduling gate",
    )
    _assert_source_order(
        pair_move_gate_block,
        "cat1 edge peek blocks random pair move scheduling",
        "if (!button || !state || state.paused || state.pairMovePlan || state.pairMoveFrame) return false;",
        "if (_isNekoIdleCat1EdgePeekActive(button)) return false;",
        "const profile = state.profile || _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;",
    )

    start_pair_move_block = _source_slice_between(
        source,
        "function _startNekoIdleCat1PairMove(button)",
        "function _scheduleNekoIdleCat1PairMove(button)",
        "cat1 pair move start",
    )
    _assert_source_order(
        start_pair_move_block,
        "cat1 edge peek blocks already queued pair move",
        "const state = _getNekoIdleCat1Journey(button);",
        "if (_isNekoIdleCat1EdgePeekActive(button)) {",
        "_cancelNekoIdleCat1Journey(button, { resetArt: false, preserveObservers: true });",
    )
    assert (
        "_cancelNekoIdleCat1Journey(button, { resetArt: false, preserveObservers: true });\n"
        "        return false;\n"
        "    }\n"
        "    if (!state || !_canScheduleNekoIdleCat1PairMove(button, state)) {"
    ) in start_pair_move_block

    journey_schedule_block = _source_slice_between(
        source,
        "function _scheduleNekoIdleCat1JourneySync(button)",
        "function _pauseNekoIdleCat1Journey(button)",
        "cat1 journey sync scheduling",
    )
    _assert_source_order(
        journey_schedule_block,
        "cat1 edge peek reclamps before blocking queued journey sync",
        "if (_isNekoIdleCat1EdgePeekActive(button)) {",
        "_reclampNekoIdleCat1EdgePeekToViewport(button);",
        "_cancelNekoIdleCat1Journey(button, { resetArt: false, preserveObservers: true });",
        "const state = _getNekoIdleCat1Journey(button);",
        "if (!state || state.syncFrame) return;",
    )
    assert (
        "_reclampNekoIdleCat1EdgePeekToViewport(button);\n"
        "        _cancelNekoIdleCat1Journey(button, { resetArt: false, preserveObservers: true });\n"
        "        return;\n"
        "    }\n"
        "    const state = _getNekoIdleCat1Journey(button);\n"
        "    if (!state || state.syncFrame) return;\n"
        "    if (_isNekoIdleCompactSurfaceDragging() || _nekoIdleCompactSurfaceSettleTimer) return;"
    ) in journey_schedule_block

    drag_start_block = _source_slice_between(
        source,
        "const handleStart = (clientX, clientY, pointerType = 'mouse') => {",
        "const handleMove = (clientX, clientY, sourceEvent = null) => {",
        "return button drag start",
    )
    _assert_source_order(
        drag_start_block,
        "cat1 edge peek clears before drag",
        "_restoreNekoIdleCat1EdgePeekBeforeDrag(container);",
        "_dispatchNekoIdleReturnBallManualMove(container, 'return-ball-drag-start');",
    )

    presentation_block = _source_slice_between(
        source,
        "function _applyNekoIdleReturnPresentation(button, tier)",
        "function _readNekoAutoGoodbyeVisualTier()",
        "return presentation",
    )
    assert "if (normalizedTier !== _NEKO_IDLE_TIER_CAT1) {" in presentation_block
    assert "_clearNekoIdleCat1EdgePeekForTierExit(container);" in presentation_block

    tier_exit_block = _source_slice_between(
        source,
        "function _clearNekoIdleCat1EdgePeekForTierExit(container)",
        "function _getNekoIdleCat1RapidDragAssetUrl(button, tier)",
        "cat1 edge peek tier exit",
    )
    _assert_source_order(
        tier_exit_block,
        "cat1 edge peek tier exit clamps the non-cat1 position on-screen",
        "const wasEdgePeekActive = _isNekoIdleCat1EdgePeekActive(container);",
        "_clearNekoIdleCat1EdgePeek(container);",
        "if (!wasEdgePeekActive) return;",
        "const w = container.offsetWidth || 64;",
        "const viewportW = Math.max(w, window.innerWidth || 0);",
        "container.style.left = `${Math.round(_clampNekoIdleCat1EdgePeekCoordinate(currentLeft, 0, viewportW - w))}px`;",
        "container.style.top = `${Math.round(_clampNekoIdleCat1EdgePeekCoordinate(currentTop, 0, viewportH - h))}px`;",
    )

    manual_move_block = _source_slice_between(
        source,
        "window.addEventListener('neko:return-ball-manual-move', (event) => {",
        "window.addEventListener('neko:idle-chat-minimized-state'",
        "return-ball manual move handler",
    )
    _assert_source_order(
        manual_move_block,
        "cat1 edge peek skips drag-end recheck",
        "if (detail.reason === 'return-ball-drag-end') {",
        "_finishNekoIdleReturnDragActionForContainer(detail.container);",
        "if (_isNekoIdleCat1EdgePeekActive(detail.container)) {",
        "_cancelNekoIdleCat1JourneyForContainer(detail.container, {",
        "resetArt: false,",
        "preserveObservers: true",
        "_updateNekoIdleCat1CompactTopEdgeRearmAfterManualMove(detail.container);",
    )
    assert (
        "_cancelNekoIdleCat1JourneyForContainer(detail.container, {\n"
        "                    resetArt: false,\n"
        "                    preserveObservers: true\n"
        "                });\n"
        "                return;\n"
        "            }\n"
        "            const compactTopEdgeRearmState = _updateNekoIdleCat1CompactTopEdgeRearmAfterManualMove(detail.container);"
    ) in manual_move_block

    native_finish_block = _source_slice_between(
        app_ui_source,
        "async function finishDrag(screenX, screenY)",
        "function isThoughtBubbleEventTarget(event) {",
        "native return-ball drag finish",
    )
    _assert_source_order(
        native_finish_block,
        "native cat1 edge peek before desktop drag end",
        "const placement = isNekoIdleCat1EdgePeekEligible(container)",
        "if (!applyNekoIdleCat1EdgePeek(container, placement)) {",
        "scheduleIdleReturnBallDesktopBridge('return-ball-drag-end', container);",
    )
    assert "getNekoIdleCat1EdgePeekPlacement(" in native_finish_block

    native_begin_block = _source_slice_between(
        app_ui_source,
        "function beginDrag(screenX, screenY, event)",
        "function updateDrag(screenX, screenY, sourcePoint = null)",
        "native return-ball drag start",
    )
    _assert_source_order(
        native_begin_block,
        "native return-ball drag start restores edge peek before dispatch",
        "const dragStarted = window.nekoPetDrag.start(screenX, screenY);",
        "restoreNekoIdleCat1EdgePeekBeforeDrag(container);",
        "window.dispatchEvent(new CustomEvent('neko:return-ball-manual-move', {",
        "state.isDragging = true;",
    )


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

    _assert_source_order(
        no_move_block,
        "no-move drag records pending bounds only while the drag token is current",
        "const pendingRestoreBounds = restoreBounds || {",
        "if (!isActiveDragToken(dragToken)) return;",
        "setPendingNativeModelViewportRestoreBounds(pendingRestoreBounds);",
    )
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
        "const cachedDragSrc = button.__nekoIdleReturnDragAssetTier === normalizedTier",
        "return drag action art",
    )
    _assert_source_contains(
        set_drag_art_block,
        "const dragSrc = rapidSrc || cachedDragSrc || _pickNekoIdleReturnDragAssetUrl(normalizedTier);",
        "return drag action art",
    )
    _assert_source_contains(
        set_drag_art_block,
        "button.__nekoIdleReturnDragAssetUrl = dragSrc;",
        "return drag action art",
    )
    _assert_source_contains(
        set_drag_art_block,
        "button.__nekoIdleReturnDragAssetTier = normalizedTier;",
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
        "button.__nekoIdleReturnDragAssetTier = tier;",
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
    _assert_source_contains(
        finish_drag_block,
        "button.__nekoIdleReturnDragAssetTier = _NEKO_IDLE_TIER_NONE;",
        "return drag action finish",
    )


def test_local_return_button_drag_safety_timer_does_not_end_active_drag():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    safety_block = _source_slice_between(
        source,
        "const resetDragStateAfterMissingEnd = (safetyToken) => {",
        "const handleStart = (clientX, clientY, pointerType = 'mouse') => {",
        "local return-ball drag safety timer",
    )
    _assert_source_order(
        safety_block,
        "local return-ball drag safety timer",
        "const moved = container.getAttribute('data-dragging') === 'true';",
        "if (moved) return;",
        "finishDragState(moved, safetyToken);",
    )


def test_local_return_button_drag_recovers_lost_release_without_active_timeout():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    drag_setup = _source_slice_between(
        source,
        "ManagerPrototype._setupReturnButtonDrag = function(container) {",
        "ManagerPrototype._addReturnButtonBreathingAnimation = function() {",
        "local return-ball drag setup",
    )
    _assert_source_contains(
        drag_setup,
        "let dragPointerType = '';",
        "local return-ball drag setup",
    )
    _assert_source_contains(
        drag_setup,
        "const cancelDragState = () => {",
        "local return-ball drag setup",
    )
    mouse_move_block = _source_slice_between(
        drag_setup,
        "mouseMove: (e) => {",
        "mouseUp: handleEnd,",
        "local return-ball mousemove handler",
    )
    _assert_source_order(
        mouse_move_block,
        "local return-ball lost mouseup recovery",
        "if (isDragging && dragPointerType === 'mouse' && e.buttons === 0) {",
        "handleEnd();",
        "return;",
        "handleMove(e.clientX, e.clientY, e);",
    )
    _assert_source_order(
        drag_setup,
        "local return-ball cancel recovery",
        "touchCancel: cancelDragState,",
        "windowBlur: cancelDragState,",
        "visibilityChange: () => {",
        "if (document.hidden) cancelDragState();",
    )
    assert "_NEKO_IDLE_RETURN_BALL_ACTIVE_DRAG_STALE_MS" not in source
    assert "scheduleActiveDragStaleRecovery" not in source


def test_cat1_rapid_drag_reaction_is_same_drag_motion_only():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")
    app_ui_source = APP_UI_PATH.read_text(encoding="utf-8")

    assert "_NEKO_IDLE_CAT1_RAPID_DRAG_ASSET_URL = '/static/assets/neko-idle/cat-idle-cat-move-5.gif'" in source
    assert "_NEKO_IDLE_CAT1_RAPID_DRAG_SOUND_URL = '/static/assets/neko-idle/cat1-voice-funny.mp3'" in source
    assert "_NEKO_IDLE_CAT1_RAPID_DRAG_REACTION_MS = 5000" in source
    assert "_NEKO_IDLE_CAT1_RAPID_DRAG_WINDOW_MS = 1100" in source
    assert "_NEKO_IDLE_CAT1_RAPID_DRAG_MIN_DISTANCE_PX = 28" in source
    assert "_NEKO_IDLE_CAT1_RAPID_DRAG_MIN_SPAN_MS = 420" in source
    assert "_NEKO_IDLE_CAT1_RAPID_DRAG_MIN_SUSTAINED_SPEED_PX_PER_SEC = 800" in source
    assert "_NEKO_IDLE_CAT1_RAPID_DRAG_REQUIRED_REVERSALS = 6" in source
    assert "_NEKO_IDLE_CAT1_RAPID_DRAG_REVERSE_DOT_THRESHOLD = 0" in source
    assert "function _handleNekoIdleCat1RapidDragMotionForContainer(container, detail)" in source
    assert "function _activateNekoIdleCat1RapidDragReaction(button, tier)" in source
    assert "function _clearNekoIdleCat1RapidDragReaction(button)" in source
    assert "function _isNekoIdleCat1RapidDragCurrentTier(button)" in source
    assert "function _restoreNekoIdleCat1NormalDragArt(button)" in source
    assert "function _isNekoIdleCat1RapidDragWindowReady(reversals)" in source
    assert "function _getNekoIdleCat1RapidDragVector(point, motion)" in source
    assert "function _isNekoIdleCat1RapidDragReversal(previousVector, currentVector)" in source
    assert "const speed = distance / (elapsedMs / 1000);" not in source
    assert "lastAxis" not in source
    assert "lastDirection" not in source
    assert "axis === motion.lastAxis" not in source
    assert "direction !== motion.lastDirection" not in source
    assert "totalDistance / (spanMs / 1000)" in source
    assert "if (spanMs < _NEKO_IDLE_CAT1_RAPID_DRAG_MIN_SPAN_MS) return false;" in source
    assert "sustainedSpeed >= _NEKO_IDLE_CAT1_RAPID_DRAG_MIN_SUSTAINED_SPEED_PX_PER_SEC" in source
    assert "dot / (previousLength * currentLength)" in source
    assert "cosine <= _NEKO_IDLE_CAT1_RAPID_DRAG_REVERSE_DOT_THRESHOLD" in source

    current_tier_block = _source_slice_between(
        source,
        "function _isNekoIdleCat1RapidDragCurrentTier(button)",
        "function _resetNekoIdleCat1RapidDragMotion(button)",
        "cat1 rapid drag current tier gate",
    )
    _assert_source_contains(
        current_tier_block,
        "return _normalizeNekoIdleReturnTier(button && button.getAttribute('data-neko-idle-tier')) === _NEKO_IDLE_TIER_CAT1;",
        "cat1 rapid drag current tier gate",
    )

    restore_drag_block = _source_slice_between(
        source,
        "function _restoreNekoIdleCat1NormalDragArt(button)",
        "function _clearNekoIdleCat1RapidDragReaction(button)",
        "cat1 rapid drag restore",
    )
    _assert_source_order(
        restore_drag_block,
        "cat1 rapid drag restore gates against current tier",
        "const currentTier = _normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier'));",
        "if (currentTier !== _NEKO_IDLE_TIER_CAT1 || state.tier !== _NEKO_IDLE_TIER_CAT1) return;",
        "_setNekoIdleReturnDragActionArt(button, currentTier);",
        "_playNekoIdleCat1DragSound(currentTier);",
    )
    assert "state.tier || button.getAttribute('data-neko-idle-tier')" not in restore_drag_block

    activate_drag_block = _source_slice_between(
        source,
        "function _activateNekoIdleCat1RapidDragReaction(button, tier)",
        "function _getNekoIdleDragMotionPoint(detail)",
        "cat1 rapid drag activation",
    )
    _assert_source_order(
        activate_drag_block,
        "cat1 rapid drag activation gates against current tier",
        "if (!button || normalizedTier !== _NEKO_IDLE_TIER_CAT1) return false;",
        "if (!_isNekoIdleCat1RapidDragCurrentTier(button)) return false;",
        "const state = _getNekoIdleReturnDragActionState(button);",
    )
    rapid_timer_block = _source_slice_between(
        activate_drag_block,
        "state.rapidTimer = setTimeout(() => {",
        "}, _NEKO_IDLE_CAT1_RAPID_DRAG_REACTION_MS);",
        "cat1 rapid drag timer",
    )
    _assert_source_order(
        rapid_timer_block,
        "cat1 rapid drag timer clears without restoring after tier drift",
        "if (state.rapidToken !== rapidToken || !state.active || state.tier !== _NEKO_IDLE_TIER_CAT1) return;",
        "const currentTier = _normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier'));",
        "state.rapidTimer = 0;",
        "state.rapidActive = false;",
        "_resetNekoIdleCat1RapidDragMotion(button);",
        "if (currentTier !== _NEKO_IDLE_TIER_CAT1) return;",
        "_restoreNekoIdleCat1NormalDragArt(button);",
    )

    motion_point_block = _source_slice_between(
        source,
        "function _getNekoIdleDragMotionPoint(detail)",
        "function _isNekoIdleCat1RapidDragWindowReady(reversals)",
        "cat1 rapid drag motion point",
    )
    _assert_source_order(
        motion_point_block,
        "cat1 rapid drag motion uses screen coordinates before client fallback",
        "const screenX = Number(detail && detail.screenX);",
        "const screenY = Number(detail && detail.screenY);",
        "const clientX = Number(detail && detail.clientX);",
        "const clientY = Number(detail && detail.clientY);",
        "const hasScreenPoint = Number.isFinite(screenX) && Number.isFinite(screenY);",
        "const rawX = hasScreenPoint ? screenX : clientX;",
        "const rawY = hasScreenPoint ? screenY : clientY;",
    )

    motion_block = _source_slice_between(
        source,
        "function _handleNekoIdleCat1RapidDragMotionForContainer(container, detail)",
        "function _setNekoIdleReturnDragActionArt(button, tier)",
        "cat1 rapid drag motion handler",
    )
    _assert_source_order(
        motion_block,
        "cat1 rapid drag motion gates against current tier before tracking movement",
        "const tier = _normalizeNekoIdleReturnTier(state && state.tier);",
        "if (!state || !state.active || tier !== _NEKO_IDLE_TIER_CAT1 || state.rapidActive) return false;",
        "if (!_isNekoIdleCat1RapidDragCurrentTier(button)) return false;",
        "const point = _getNekoIdleDragMotionPoint(detail);",
    )

    set_drag_art_block = _source_slice_between(
        source,
        "function _setNekoIdleReturnDragActionArt(button, tier)",
        "function _prepareNekoIdleReturnDragActionForContainer(container)",
        "return drag action art",
    )
    _assert_source_order(
        set_drag_art_block,
        "return drag action art",
        "const normalizedTier = _normalizeNekoIdleReturnTier(tier);",
        "const rapidSrc = _getNekoIdleCat1RapidDragAssetUrl(button, normalizedTier);",
        "const cachedDragSrc = button.__nekoIdleReturnDragAssetTier === normalizedTier",
        "const dragSrc = rapidSrc || cachedDragSrc || _pickNekoIdleReturnDragAssetUrl(normalizedTier);",
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
        "_resetNekoIdleCat1RapidDragMotion(button);",
        "button.__nekoIdleReturnDragAssetTier = tier;",
        "button.__nekoIdleReturnDragAssetUrl = _pickNekoIdleReturnDragAssetUrl(tier);",
    )

    finish_drag_block = _source_slice_between(
        source,
        "function _finishNekoIdleReturnDragAction(button, options = {})",
        "function _finishNekoIdleReturnDragActionForContainer(container, options = {})",
        "return drag action finish",
    )
    _assert_source_order(
        finish_drag_block,
        "return drag action finish",
        "_clearNekoIdleCat1RapidDragReaction(button);",
        "button.__nekoIdleReturnDragAssetUrl = '';",
        "button.__nekoIdleReturnDragAssetTier = _NEKO_IDLE_TIER_NONE;",
    )

    presentation_block = _source_slice_between(
        source,
        "function _applyNekoIdleReturnPresentation(button, tier)",
        "function _readNekoAutoGoodbyeVisualTier()",
        "return presentation",
    )
    _assert_source_order(
        presentation_block,
        "tier changes clear cat1 rapid drag state before repaint",
        "const dragState = button.__nekoIdleReturnDragActionState;",
        "const dragActive = _isNekoIdleReturnDragActionActive(button);",
        "if (dragActive && normalizedTier !== _NEKO_IDLE_TIER_CAT1) {",
        "const wasCat1Drag = dragState && dragState.tier === _NEKO_IDLE_TIER_CAT1;",
        "_clearNekoIdleCat1RapidDragReaction(button);",
        "if (wasCat1Drag) _fadeOutNekoIdleCat1DragSound();",
        "button.setAttribute('data-neko-idle-tier', normalizedTier);",
        "_setNekoIdleReturnDragActionArt(button, normalizedTier);",
    )

    manual_move_handler = _source_slice_between(
        source,
        "window.addEventListener('neko:return-ball-manual-move', (event) => {",
        "window.addEventListener('neko:idle-chat-minimized-state'",
        "return-ball manual move handler",
    )
    _assert_source_contains(
        manual_move_handler,
        "if (detail.reason === 'return-ball-drag-motion')",
        "return-ball manual move handler",
    )
    _assert_source_contains(
        manual_move_handler,
        "_handleNekoIdleCat1RapidDragMotionForContainer(detail.container, detail);",
        "return-ball manual move handler",
    )

    local_drag_setup = _source_slice_between(
        source,
        "ManagerPrototype._setupReturnButtonDrag = function(container) {",
        "ManagerPrototype._addReturnButtonBreathingAnimation = function()",
        "return button drag setup",
    )
    _assert_source_order(
        local_drag_setup,
        "return button drag setup",
        "_dispatchNekoIdleReturnBallManualMove(container, 'return-ball-drag-active');",
        "_dispatchNekoIdleReturnBallManualMove(container, 'return-ball-drag-motion'",
    )
    _assert_source_contains(
        local_drag_setup,
        "const handleMove = (clientX, clientY, sourceEvent = null) => {",
        "return button drag setup",
    )
    _assert_source_order(
        local_drag_setup,
        "local return-ball drag motion emits client and screen coordinates",
        "clientX: clientX,",
        "clientY: clientY,",
        "screenX: sourceEvent && Number.isFinite(sourceEvent.screenX) ? sourceEvent.screenX : clientX,",
        "screenY: sourceEvent && Number.isFinite(sourceEvent.screenY) ? sourceEvent.screenY : clientY,",
        "deltaX: deltaX,",
        "deltaY: deltaY,",
        "timestamp: Date.now()",
    )
    _assert_source_contains(
        local_drag_setup,
        "handleMove(e.clientX, e.clientY, e);",
        "return button drag setup",
    )
    _assert_source_contains(
        local_drag_setup,
        "handleMove(e.touches[0].clientX, e.touches[0].clientY, e.touches[0]);",
        "return button drag setup",
    )

    native_drag_motion_block = _source_slice_between(
        app_ui_source,
        "function updateDrag(screenX, screenY, sourcePoint = null)",
        "async function finishDrag(screenX, screenY)",
        "native return-ball drag motion",
    )
    _assert_source_order(
        native_drag_motion_block,
        "native return-ball drag motion emits client and screen coordinates",
        "reason: 'return-ball-drag-motion'",
        "clientX: sourcePoint && Number.isFinite(sourcePoint.clientX) ? sourcePoint.clientX : screenX,",
        "clientY: sourcePoint && Number.isFinite(sourcePoint.clientY) ? sourcePoint.clientY : screenY,",
        "screenX: screenX",
        "screenY: screenY",
        "deltaX: dx",
        "deltaY: dy",
        "timestamp: Date.now()",
    )
    assert "updateDrag(event.screenX, event.screenY, event);" in app_ui_source
    assert "updateDrag(point.x, point.y, event.touches[0]);" in app_ui_source


def test_idle_thought_bubble_is_sound_triggered_with_fade():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")
    app_ui_source = APP_UI_PATH.read_text(encoding="utf-8")
    css_source = INDEX_CSS_PATH.read_text(encoding="utf-8")

    assert "_NEKO_IDLE_THOUGHT_BUBBLE_ACTIVE_CLASS = 'is-thought-bubble-active'" in source
    assert "_NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_CLASS = 'is-thought-bubble-sleeping'" in source
    assert "_NEKO_IDLE_THOUGHT_BUBBLE_POPPING_CLASS = 'is-thought-bubble-popping'" in source
    assert "_NEKO_IDLE_THOUGHT_BUBBLE_ASSET_URL = '/static/assets/neko-idle/thought-items/cloud-thought-bubble.gif'" in source
    assert "_NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_ASSET_URL = '/static/assets/neko-idle/thought-items/sleeping-zzz.gif'" in source
    assert "_NEKO_IDLE_THOUGHT_BUBBLE_POP_ASSET_URL = '/static/assets/neko-idle/thought-items/cloud-thought-bubble-pop.gif'" in source
    assert "const _NEKO_IDLE_THOUGHT_BUBBLE_ITEM_ASSET_URLS = Object.freeze([" in source
    assert "'/static/assets/neko-idle/thought-items/catnip-pouch.png'" in source
    assert "'/static/assets/neko-idle/thought-items/fish-cookie.png'" in source
    assert "'/static/assets/neko-idle/thought-items/toy-mouse.png'" in source
    assert "fish-cookie-transparent.png" not in source
    assert "_NEKO_IDLE_THOUGHT_BUBBLE_VISIBLE_MS = 5000" in source
    assert "_NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_FALLBACK_VISIBLE_MS = 8000" in source
    assert "_NEKO_IDLE_THOUGHT_BUBBLE_POP_VISIBLE_MS = 540" in source
    assert "_NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_VISIBLE_MS" not in source
    assert "function _pickNekoIdleThoughtBubbleBgAsset(tier)" in source
    assert "normalizedTier === _NEKO_IDLE_TIER_CAT2 && roll < 1 / 3" in source
    assert "normalizedTier === _NEKO_IDLE_TIER_CAT3 && roll < 2 / 3" in source
    assert "function _getNekoIdleAudioRemainingMs(audio)" in source
    assert "function _getNekoIdleThoughtBubbleVisibleMs(bubbleConfig, audio)" in source
    assert "function _scheduleNekoIdleThoughtBubbleHide(button, token, visibleMs)" in source
    assert "let _nekoIdleThoughtBubblePopPreloadImage = null;" in source
    assert "function _preloadNekoIdleThoughtBubblePopAsset()" in source
    assert "function _setNekoIdleThoughtBubbleFocusable(button, focusable)" in source
    assert "function _isNekoIdleThoughtBubbleEventTarget(event)" in source
    assert "function _isNekoIdleThoughtBubbleEventHit(button, event)" in source
    assert "if (audio) return _getNekoIdleAudioRemainingMs(audio) || _NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_FALLBACK_VISIBLE_MS;" in source
    assert "function _getNekoIdleThoughtBubbleBgAssetUrl(assetUrl, restartToken = 0)" in source
    assert "function _getNekoIdleThoughtBubbleItemAssetUrl(assetUrl)" in source
    assert "function _pickNekoIdleThoughtBubbleItemAssetUrl(previousAssetUrl = '')" in source
    assert "const availableUrls = urls.length > 1 && previousAssetUrl" in source
    assert "urls.filter((url) => url !== previousAssetUrl)" in source
    assert "function _restartNekoIdleThoughtBubbleArt(button, tier)" in source
    assert "function _dispatchNekoIdleThoughtBubblePop(button, detail = {})" in source
    assert "function _popNekoIdleThoughtBubble(button, detail = {})" in source
    assert "function _handleNekoIdleThoughtBubbleClick(button, event)" in source
    assert "function _clearNekoIdleThoughtBubble(button)" in source
    assert "function _showNekoIdleThoughtBubbleForSound(tier, audio = null)" in source
    assert "function _runAfterNekoIdleSoundStarted(state, audio, callback)" in source
    assert "audio.__nekoIdlePlayStarted = playStarted;" in source
    assert "if (state.audio !== audio || audio.paused || audio.ended) return;" in source
    assert "playStarted.then(run).catch(() => {});" in source
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
        "_preloadNekoIdleThoughtBubblePopAsset();",
        "button.classList.add(_NEKO_IDLE_THOUGHT_BUBBLE_ACTIVE_CLASS);",
        "_setNekoIdleThoughtBubbleFocusable(button, true);",
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
        "button.classList.remove(_NEKO_IDLE_THOUGHT_BUBBLE_POPPING_CLASS);",
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
    bubble_clear_block = _source_slice_between(
        source,
        "function _clearNekoIdleThoughtBubble(button)",
        "function _getNekoIdleAudioRemainingMs(audio)",
        "thought bubble clear helper",
    )
    _assert_source_contains(
        bubble_clear_block,
        "button.classList.remove(_NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_CLASS);",
        "thought bubble clear helper",
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
        "_runAfterNekoIdleSoundStarted(_nekoIdleSleepSoundState, audio, () => {",
        "if (token !== _nekoIdleSleepSoundState.token || _nekoIdleSleepSoundState.tier !== tier) return;",
        "_showNekoIdleThoughtBubbleForSound(tier, audio);",
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
        "_runAfterNekoIdleSoundStarted(_nekoIdleCat1AmbientSoundState, audio, () => {",
        "_showNekoIdleThoughtBubbleForSound(_NEKO_IDLE_TIER_CAT1, audio);",
        "_playNekoIdleCat1SoundReaction();",
    )
    _assert_source_contains(
        ambient_play_block,
        "token !== _nekoIdleCat1AmbientSoundState.token ||",
        "cat1 ambient sound playback",
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
    assert "pointer-events: auto;" in active_block
    assert "cursor: pointer;" in active_block

    popping_block = _extract_css_block(
        css_source,
        ".neko-idle-return-btn.is-thought-bubble-popping .neko-idle-thought-bubble",
    )
    assert "pointer-events: none;" in popping_block
    popping_item_block = _extract_css_block(
        css_source,
        ".neko-idle-return-btn.is-thought-bubble-popping .neko-idle-thought-bubble-item",
    )
    assert "display: none;" in popping_item_block
    assert "scale(1.18)" not in css_source

    assert "const thoughtBubble = document.createElement('span');" in source
    assert "thoughtBubble.setAttribute('role', 'button');" in source
    assert "thoughtBubble.setAttribute('tabindex', '-1');" in source
    assert "const thoughtBubbleAriaLabel = typeof window.t === 'function'" in source
    assert "? window.t('buttons.thoughtBubblePop')" in source
    assert ": 'Pop thought bubble';" in source
    assert "thoughtBubble.setAttribute('aria-label', thoughtBubbleAriaLabel);" in source
    assert "thoughtBubble.setAttribute('data-i18n-aria', 'buttons.thoughtBubblePop');" in source
    assert "const stopThoughtBubblePointerStart = (event) => {" in source
    assert "thoughtBubble.addEventListener('mousedown', stopThoughtBubblePointerStart);" in source
    assert "event.preventDefault();" in source
    assert "thoughtBubble.addEventListener('touchstart', stopThoughtBubblePointerStart, { passive: false });" in source
    assert "thoughtBubble.addEventListener('touchend', (event) => {" in source
    assert "_handleNekoIdleThoughtBubbleClick(returnBtn, event);" in source
    assert "const thoughtBubbleBg = document.createElement('img');" in source
    assert "thoughtBubbleBg.className = 'neko-idle-thought-bubble-bg';" in source
    assert "thoughtBubbleBg.src = _getNekoIdleThoughtBubbleBgAssetUrl(_NEKO_IDLE_THOUGHT_BUBBLE_ASSET_URL);" in source
    assert "const thoughtBubbleItem = document.createElement('img');" in source
    assert "thoughtBubbleItem.className = 'neko-idle-thought-bubble-item';" in source
    assert "thoughtBubbleItem.src = _getNekoIdleThoughtBubbleItemAssetUrl(_NEKO_IDLE_THOUGHT_BUBBLE_ITEM_ASSET_URLS[0]);" in source
    assert "thoughtBubble.appendChild(thoughtBubbleBg);" in source
    assert "thoughtBubble.appendChild(thoughtBubbleItem);" in source

    bubble_pop_block = _source_slice_between(
        source,
        "function _popNekoIdleThoughtBubble(button, detail = {})",
        "function _handleNekoIdleThoughtBubbleClick(button, event)",
        "thought bubble pop helper",
    )
    _assert_source_order(
        bubble_pop_block,
        "thought bubble pop helper",
        "_preloadNekoIdleThoughtBubblePopAsset();",
        "button.__nekoIdleThoughtBubbleTimerToken = (button.__nekoIdleThoughtBubbleTimerToken || 0) + 1;",
        "button.classList.remove(_NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_CLASS);",
        "button.classList.add(_NEKO_IDLE_THOUGHT_BUBBLE_POPPING_CLASS);",
        "_setNekoIdleThoughtBubbleFocusable(button, false);",
        "_NEKO_IDLE_THOUGHT_BUBBLE_POP_ASSET_URL,",
        "_dispatchNekoIdleThoughtBubblePop(button, detail);",
        "_scheduleNekoIdleThoughtBubbleHide(button, timerToken, _NEKO_IDLE_THOUGHT_BUBBLE_POP_VISIBLE_MS);",
    )
    _assert_source_contains(
        bubble_pop_block,
        "button.__nekoIdleThoughtBubbleAudio = null;",
        "thought bubble pop helper",
    )
    dispatch_pop_block = _source_slice_between(
        source,
        "function _dispatchNekoIdleThoughtBubblePop(button, detail = {})",
        "function _popNekoIdleThoughtBubble(button, detail = {})",
        "thought bubble pop dispatch helper",
    )
    assert "new CustomEvent('neko:thought-bubble-pop'" in dispatch_pop_block
    assert "source: detail.source || 'click'" in dispatch_pop_block
    hide_bubble_block = _source_slice_between(
        source,
        "function _hideNekoIdleThoughtBubble(button, token)",
        "function _restartNekoIdleThoughtBubbleArt(button, tier)",
        "thought bubble hide helper",
    )
    assert "_NEKO_IDLE_THOUGHT_BUBBLE_POPPING_CLASS" not in hide_bubble_block
    assert "_NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_CLASS" not in hide_bubble_block

    return_click_block = _source_slice_between(
        source,
        "returnBtn.addEventListener('click', (e) => {",
        "const thoughtBubble = document.createElement('span');",
        "return button click handler before thought bubble",
    )
    _assert_source_order(
        return_click_block,
        "return button ignores thought bubble clicks",
        "if (_isNekoIdleThoughtBubbleEventHit(returnBtn, e)) {",
        "e.preventDefault();",
        "e.stopPropagation();",
        "return;",
        "_finishNekoIdleReturnDragAction(returnBtn, { restoreArt: false });",
    )
    assert "returnBtn.addEventListener('mouseenter', (event) => {" in source
    assert "if (_isNekoIdleThoughtBubbleEventHit(returnBtn, event)) return;" in source
    native_drag_block = _source_slice_between(
        app_ui_source,
        "function isThoughtBubbleEventTarget(event) {",
        "state.handleMouseMove = (event) => {",
        "desktop native return-ball drag thought bubble guard",
    )
    _assert_source_order(
        native_drag_block,
        "desktop native return-ball drag thought bubble guard",
        "const bubble = target.closest('.neko-idle-thought-bubble');",
        "return !!(bubble && bubble.closest('.neko-idle-return-btn.is-thought-bubble-active'));",
        "state.handleMouseDown = (event) => {",
        "if (isThoughtBubbleEventTarget(event)) return;",
        "beginDrag(event.screenX, event.screenY, event);",
    )
    assert "state.handleTouchStart = (event) => {\n            if (isThoughtBubbleEventTarget(event)) return;" in app_ui_source

    bubble_bg_block = _extract_css_block(css_source, ".neko-idle-thought-bubble-bg")
    assert "position: absolute;" in bubble_bg_block
    assert "inset: 0;" in bubble_bg_block
    assert "width: 100%;" in bubble_bg_block
    assert "height: 100%;" in bubble_bg_block
    assert "object-fit: contain;" in bubble_bg_block
    assert "transform-origin: center center;" in bubble_bg_block

    with Image.open(THOUGHT_BUBBLE_POP_ASSET_PATH) as pop_asset:
        pop_durations = []
        pop_bboxes = []
        for frame_index in range(pop_asset.n_frames):
            pop_asset.seek(frame_index)
            pop_durations.append(pop_asset.info.get("duration"))
            pop_bboxes.append(pop_asset.convert("RGBA").getchannel("A").getbbox())
        assert pop_asset.size == (248, 244)
        assert pop_asset.n_frames == 6
        assert pop_durations == [80, 90, 100, 120, 150, 520]
        assert sum(pop_durations) == 1060
        assert sum(duration for duration, bbox in zip(pop_durations, pop_bboxes) if bbox is not None) == 540
        assert pop_bboxes[-1] is None
        assert pop_bboxes[0] == (8, 49, 248, 202)

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

    assert "Dev-only short interval for CAT2/CAT3 sleep sounds and their thought bubble." not in source
    assert "_NEKO_IDLE_SLEEP_SOUND_INTERVAL_MS = 5 * 60 * 1000" in source
    assert "_NEKO_IDLE_SLEEP_SOUND_VOLUME = 0.06" in source
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
    assert "audio.__nekoIdlePlayStarted = playStarted;" in source
    assert "audio.dispatchEvent(new Event('error'));" in source
    assert "Math.random() * _NEKO_IDLE_SLEEP_SOUND_INTERVAL_MS" in source
    assert "_scheduleNekoIdleSleepSoundInterval(tier, startedAt + _NEKO_IDLE_SLEEP_SOUND_INTERVAL_MS)" in source
    assert "_syncNekoIdleSleepSoundForTier(detail.tier)" in source
    assert "_stopNekoIdleSleepSoundAudio()" in source
    assert "_clearNekoIdleSleepSoundTimer()" in source


def test_cat1_voice_sounds_are_limited_to_non_drag_and_drag_states():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    assert "Dev-only short interval for tuning cat sounds and the linked thought bubble." not in source
    assert "_NEKO_IDLE_CAT1_AMBIENT_SOUND_INTERVAL_MS = 3 * 60 * 1000" in source
    assert "_NEKO_IDLE_CAT1_EAT_SOUND_VOLUME = 0.12" in source
    assert "_NEKO_IDLE_CAT1_PLAY_SOUND_VOLUME = 0.10" in source
    assert "_NEKO_IDLE_CAT1_AMBIENT_SOUND_VOLUME = 0.10" in source
    assert "_NEKO_IDLE_CAT1_DRAG_SOUND_VOLUME = 0.12" in source
    assert "_NEKO_IDLE_CAT1_DRAG_SOUND_FADE_OUT_MS = 900" in source
    assert "'/static/assets/neko-idle/cat1-voice1.mp3'" in source
    assert "'/static/assets/neko-idle/cat1-voice2.mp3'" in source
    assert "'/static/assets/neko-idle/cat1-voice3.mp3'" in source
    assert "_NEKO_IDLE_CAT1_DRAG_SOUND_URL = '/static/assets/neko-idle/cat1-voice-click.mp3'" in source
    assert "_NEKO_IDLE_CAT1_RAPID_DRAG_SOUND_URL = '/static/assets/neko-idle/cat1-voice-funny.mp3'" in source
    assert "const _nekoIdleCat1RapidDragSoundState = {" in source
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
    assert "_fadeOutNekoIdleSoundAudio(_nekoIdleCat1RapidDragSoundState, _NEKO_IDLE_CAT1_DRAG_SOUND_FADE_OUT_MS)" in source
    assert "audio.volume = Math.max(0, startVolume * (1 - progress))" in source
    assert "_normalizeNekoIdleReturnTier(tier) !== _NEKO_IDLE_TIER_CAT1" in source
    assert "_syncNekoIdleCat1AmbientSoundForTier(detail.tier)" in source
    assert "_stopNekoIdleCat1AmbientSound()" in source

    rapid_drag_sound_block = _source_slice_between(
        source,
        "function _playNekoIdleCat1RapidDragSound(tier)",
        "function _fadeOutNekoIdleCat1DragSound()",
        "cat1 rapid drag sound",
    )
    _assert_source_order(
        rapid_drag_sound_block,
        "cat1 rapid drag sound",
        "_stopNekoIdleSoundAudio(_nekoIdleCat1DragSoundState);",
        "_playNekoIdleSound(",
        "_nekoIdleCat1RapidDragSoundState,",
        "_NEKO_IDLE_CAT1_RAPID_DRAG_SOUND_URL,",
    )

    normal_drag_sound_block = _source_slice_between(
        source,
        "function _playNekoIdleCat1DragSound(tier)",
        "function _playNekoIdleCat1RapidDragSound(tier)",
        "cat1 normal drag sound",
    )
    _assert_source_order(
        normal_drag_sound_block,
        "cat1 normal drag sound",
        "_stopNekoIdleSoundAudio(_nekoIdleCat1RapidDragSoundState);",
        "_playNekoIdleSound(",
        "_nekoIdleCat1DragSoundState,",
        "_NEKO_IDLE_CAT1_DRAG_SOUND_URL,",
    )


def test_cat1_walk_to_minimized_chat_contract_is_present():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")
    app_ui_source = (PROJECT_ROOT / "static" / "app-ui.js").read_text(encoding="utf-8")

    assert "_NEKO_IDLE_CAT1_SUBSTATE_WALKING = 'walking-to-chat'" in source
    assert "_NEKO_IDLE_CAT1_SUBSTATE_STRETCH = 'stretch-near-chat'" in source
    assert '_NEKO_IDLE_CAT1_CHAT_GAP_PX = -5' in source
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
    assert '_isNekoIdleCat1SettledOnMinimizedSide' in source
    assert 'if (isSmallDesktopChatMove && !_isNekoIdleCat1Walking(button) && !settledMinimizedSide) return;' in source
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
        "const handleStart = (clientX, clientY, pointerType = 'mouse') => {",
        "const handleMove = (clientX, clientY, sourceEvent = null) => {",
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


def test_live2d_renderer_ignores_and_recovers_return_ball_viewport_size():
    core_source = LIVE2D_CORE_PATH.read_text(encoding="utf-8")
    interaction_source = LIVE2D_INTERACTION_PATH.read_text(encoding="utf-8")
    app_ui_source = APP_UI_PATH.read_text(encoding="utf-8")

    assert "const LIVE2D_RETURN_BALL_VIEWPORT_MAX_SIZE = 200;" in core_source
    assert "function isLive2DReturnBallViewportSize(width, height)" in core_source
    assert "if (!window.__LANLAN_IS_ELECTRON_PET__) return false;" in core_source
    assert "recoverRendererFromReturnBallViewport(reason = 'manual')" in core_source

    resize_block = _source_slice_between(
        core_source,
        "const doResize = (reason) => {",
        "this._screenChangeHandler = () => {",
        "live2d renderer resize",
    )
    resize_guard_block = resize_block[
        resize_block.index("const newW = Math.max(window.innerWidth || window.screen.width || 1, 1);"):
    ]
    _assert_source_order(
        resize_guard_block,
        "live2d renderer skips temporary return-ball viewport before resize",
        "const newW = Math.max(window.innerWidth || window.screen.width || 1, 1);",
        "if (isLive2DReturnBallViewportSize(newW, newH)) {",
        "return;",
        "renderer.resize(newW, newH);",
    )
    recovery_resize_block = resize_block[
        resize_block.index("const restoringFromReturnBallViewport ="):
    ]
    _assert_source_order(
        recovery_resize_block,
        "live2d renderer recovers polluted viewport before pending-display branch",
        "const restoringFromReturnBallViewport =",
        "renderer.resize(newW, newH);",
        "if (this._pendingDisplaySwitch || restoringFromReturnBallViewport) {",
    )
    pending_branch_block = resize_block[
        resize_block.index("if (this._pendingDisplaySwitch || restoringFromReturnBallViewport) {"):
    ]
    _assert_source_order(
        pending_branch_block,
        "live2d renderer skips model scaling after return-ball recovery",
        "if (this._pendingDisplaySwitch || restoringFromReturnBallViewport) {",
        "restoringFromReturnBallViewport",
        "return;",
        "this.currentModel.x *= wRatio;",
    )

    screen_change_block = _source_slice_between(
        core_source,
        "this._screenChangeHandler = () => {",
        "this._displayChangeHandler = () => {",
        "live2d window resize handler",
    )
    _assert_source_contains(
        screen_change_block,
        "const shouldRecoverReturnBallRenderer = !!(renderer && renderer.screen &&",
        "live2d window resize handler",
    )
    _assert_source_contains(
        screen_change_block,
        "isLive2DReturnBallViewportSize(renderer.screen.width, renderer.screen.height)",
        "live2d window resize handler",
    )
    _assert_source_contains(
        screen_change_block,
        "'window.resize:return-ball-renderer-recovery'",
        "live2d window resize handler",
    )

    recovery_method_block = _source_slice_between(
        core_source,
        "recoverRendererFromReturnBallViewport(reason = 'manual')",
        "// 加载用户偏好",
        "live2d return-ball renderer recovery method",
    )
    _assert_source_order(
        recovery_method_block,
        "live2d return-ball renderer recovery only touches renderer size",
        "if (isLive2DReturnBallViewportSize(currentW, currentH)) return false;",
        "isLive2DReturnBallViewportSize(renderer.screen.width, renderer.screen.height)",
        "renderer.resize(targetW, targetH);",
        "return true;",
    )
    assert "currentModel.x" not in recovery_method_block
    assert "currentModel.scale" not in recovery_method_block

    save_block = _source_slice_between(
        interaction_source,
        "Live2DManager.prototype._savePositionAfterInteraction = async function () {",
        "// 防抖动保存位置的辅助函数",
        "live2d save position",
    )
    _assert_source_order(
        save_block,
        "live2d save recovers renderer before reading viewportInfo",
        "this.recoverRendererFromReturnBallViewport('save-position-before');",
        "const position = { x: this.currentModel.x, y: this.currentModel.y };",
        "let viewportInfo = null;",
        "viewportInfo = { width: rw, height: rh };",
        "this.saveUserPreferences",
    )

    viewport_ready_block = _source_slice_between(
        app_ui_source,
        "function recoverLive2DRendererFromReturnBallViewport(reason)",
        "// --- showCurrentModel ---",
        "app-ui model viewport ready",
    )
    assert "window.live2dManager.recoverRendererFromReturnBallViewport(reason)" in viewport_ready_block
    assert "recoverLive2DRendererFromReturnBallViewport('ensure-model-viewport-ready:no-restore-bounds')" in viewport_ready_block
    assert "recoverLive2DRendererFromReturnBallViewport('ensure-model-viewport-ready:already-restored')" in viewport_ready_block
    assert "recoverLive2DRendererFromReturnBallViewport('ensure-model-viewport-ready:after-wait')" in viewport_ready_block


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
    for path in (APP_UI_PATH, COMMON_UI_HUD_PATH,
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
                 CAT1_RAPID_DRAG_ASSET_PATH, CAT1_RAPID_DRAG_SOUND_PATH,
                 CAT_MODEL_CHANGE_ASSET_PATH,
                 THOUGHT_BUBBLE_ASSET_PATH, THOUGHT_BUBBLE_POP_ASSET_PATH,
                 SLEEPING_THOUGHT_BUBBLE_ASSET_PATH,
                 *THOUGHT_BUBBLE_ITEM_ASSET_PATHS):
        assert path in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS
        assert path.is_file()

    # app-interpage.js follows the static/tutorial asset version because it owns tutorial bridges.
    assert APP_INTERPAGE_PATH in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS
    assert APP_INTERPAGE_PATH not in pages_router._REACT_CHAT_ASSET_VERSION_PATHS
    assert APP_INTERPAGE_PATH.is_file()


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
