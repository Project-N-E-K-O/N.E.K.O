import json
from pathlib import Path

import pytest

from main_routers import pages_router


ROOT = Path(__file__).resolve().parents[2]
DRAWING_GUESS_TEMPLATE = ROOT / "templates" / "drawing_guess.html"
DRAWING_GUESS_SCRIPT = ROOT / "static" / "game" / "games" / "drawing_guess" / "drawing-guess.js"
I18N_SCRIPT = ROOT / "static" / "i18n-i18next.js"
LOCALES_DIR = ROOT / "static" / "locales"


class _FakePageRequest:
    query_params = {}


class _FakeTemplates:
    def TemplateResponse(self, template_name: str, context: dict):
        return {"template_name": template_name, "context": context}


def _html() -> str:
    return DRAWING_GUESS_TEMPLATE.read_text(encoding="utf-8")


def _script() -> str:
    return DRAWING_GUESS_SCRIPT.read_text(encoding="utf-8")


def _i18n_script() -> str:
    return I18N_SCRIPT.read_text(encoding="utf-8")


def _get_nested(payload: dict, dotted_key: str):
    node = payload
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


@pytest.mark.unit
@pytest.mark.asyncio
async def test_drawing_guess_page_renders_shell(monkeypatch):
    monkeypatch.setattr(pages_router, "get_templates", lambda: _FakeTemplates())

    result = await pages_router.drawing_guess_demo(_FakePageRequest())

    assert result["template_name"] == "templates/drawing_guess.html"
    assert "static_asset_version" in result["context"]


@pytest.mark.unit
def test_drawing_guess_static_route_contract():
    html = _html()
    script = _script()

    assert "/static/game/games/drawing_guess/drawing-guess.js" in html
    assert "var GAME_TYPE = 'drawing_guess';" in script
    assert "var ROUND_API = '/api/game/drawing_guess';" in script
    assert "lanlan_name: queryLanlan || ''" in html
    assert "lanlan_name: queryLanlan || 'drawing_guess_demo'" not in html
    assert "source: 'drawing_guess_demo'" not in html
    assert "source: 'drawing_guess_demo'" not in script
    assert "fetch('/api/characters/current_catgirl'" in script
    assert '<button id="start-button"' not in html
    assert '<button id="reload-character-button"' not in html
    assert 'data-i18n="drawingGuess.layout.modelTitle"' not in html
    assert "--dg-bg: #eef7ff;" in html
    assert "--dg-accent: #17a7ff;" in html
    assert "/static/icons/icon_systray.ico" in html
    assert 'id="debug-trigger" class="dg-header-icon"' in html
    assert 'id="debug-panel" class="dg-debug-panel"' in html
    assert 'id="debug-character-select"' in html
    assert 'id="debug-ai-round"' in html
    assert 'id="debug-user-round"' in html
    assert 'id="debug-rotate-rounds" type="checkbox" checked' in html
    assert 'id="debug-ai-guess-countdown"' in html
    assert 'id="debug-trigger-ai-guess"' in html
    assert ".dg-header-icon.is-shaking" in html
    assert ".dg-debug-panel" in html
    assert "/static/icons/paw_ui.png" not in html
    assert "/static/icons/image_icon.svg" in html
    assert "/static/icons/chat_icon.png" in html
    assert 'class="dg-side"' in html
    assert 'id="side-pane" class="dg-side"' in html
    assert 'id="side-resizer"' in html
    assert "grid-template-rows: minmax(220px, 64%) 12px minmax(280px, 1fr);" in html
    assert "min-height: 512px;" in html
    assert 'class="dg-model-column"' not in html
    assert 'class="dg-chat-column"' not in html
    assert 'class="dg-actions"' not in html
    assert 'class="dg-canvas-actions"' in html
    assert 'id="canvas-placeholder-detail" data-i18n="drawingGuess.layout.canvasWaiting"' in html
    assert ".dg-canvas-actions .dg-button:not(:disabled):hover" in html
    assert ".dg-button-danger:not(:disabled):hover" in html
    assert ".dg-toolbar {" in html
    assert "z-index: 18;" in html
    assert "overflow: visible;" in html
    assert ".dg-canvas-wrap {" in html
    assert "z-index: 1;" in html
    assert 'class="dg-tool-popover"' in html
    assert 'class="dg-tool-menu"' in html
    assert 'id="brush-size" class="dg-size"' in html
    assert 'id="eraser-size" class="dg-size"' in html
    assert 'id="brush-mode-brush"' in html
    assert 'id="brush-mode-bucket"' in html
    assert 'data-brush-tool="bucket"' in html
    assert 'data-brush-kind="brush"' in html
    assert 'id="color-panel-toggle"' in html
    assert 'id="color-panel" class="dg-color-panel"' in html
    assert 'id="color-wheel" class="dg-color-wheel"' in html
    assert 'id="brush-color" type="color"' in html
    assert 'id="color-panel-preview" class="dg-color-current"' in html
    assert 'id="color-history-colors" class="dg-color-grid dg-color-grid-history"' in html
    assert 'id="eyedropper-button"' in html
    assert 'data-i18n-aria="drawingGuess.tools.eyedropper"' in html
    assert 'data-i18n="drawingGuess.tools.basicColors"' in html
    assert 'data-i18n="drawingGuess.tools.historyColors"' in html
    assert 'class="dg-swatch"' not in html
    assert 'class="dg-tool-control dg-brush-size-control"' in html
    assert 'id="brush-size-preview" class="dg-size-preview" hidden' in html
    assert 'id="brush-size-preview-ring" class="dg-size-preview-ring" aria-hidden="true"' in html
    assert '<input id="brush-size" class="dg-size" type="range" min="2" max="28" value="7"' in html
    assert '<input id="eraser-size" class="dg-size" type="range" min="4" max="56" value="13"' in html
    assert "z-index: 18;" in html
    assert "inset: 0;" in html
    assert "place-items: center;" in html
    assert "display: none !important;" in html
    assert ".dg-size-preview-ring" in html
    assert "width: var(--dg-size-preview-diameter, 20px);" in html
    assert "border: var(--dg-size-preview-border, 2px) solid var(--dg-size-preview-color, var(--dg-accent-strong));" in html
    assert "border-style: dashed;" in html
    assert ".dg-tool-mode-group" in html
    assert ".dg-tool svg[hidden]" in html
    assert '.dg-tool[data-brush-kind="brush"] .dg-bucket-icon' in html
    assert '.dg-tool[data-brush-kind="bucket"] .dg-brush-icon' in html
    assert '.dg-tool[data-brush-kind="bucket"] .dg-bucket-icon' in html
    assert ".dg-color-panel" in html
    assert ".dg-color-wheel" in html
    assert ".dg-color-wheel::after" in html
    assert "pointer-events: none;" in html
    assert ".dg-color-chip" in html
    assert ".dg-color-grid-history" in html
    assert "min-height: 34px;" in html
    assert "background: transparent;" in html
    assert ".dg-color-eyedropper" in html
    assert ".dg-tool-menu[data-brush-kind=\"bucket\"] .dg-brush-size-control" in html
    assert ".dg-size-preview" in html
    assert "left: -220px;" in html
    assert "right: -32px;" in html
    assert "height: 48px;" in html
    assert ".dg-tool-popover.is-open .dg-tool-menu" in html
    assert ".dg-tool-popover.is-open::after" in html
    assert "pointer-events: auto;" in html
    assert "transition-delay: 0ms;" in html
    assert 'id="clear-canvas-button" class="dg-tool"' in html
    assert 'id="model-stage"' in html
    assert 'data-model-kind="loading" data-model-mood="idle" data-model-load-state="loading"' in html
    assert 'id="model-loading" class="dg-model-loading"' in html
    assert 'data-i18n="drawingGuess.modelStates.loading"' in html
    assert 'id="live2d-container"' in html
    assert 'id="live2d-canvas"' in html
    assert 'id="vrm-container"' in html
    assert 'id="vrm-canvas"' in html
    assert 'id="mmd-container"' in html
    assert 'id="mmd-canvas"' in html
    assert 'id="pngtuber-container"' in html
    assert 'class="dg-model-renderer dg-pngtuber-renderer hidden"' in html
    assert 'id="model-fallback-container" class="dg-model-fallback hidden" aria-hidden="true" hidden' in html
    assert ".dg-model-loading[hidden]" in html
    assert ".dg-pngtuber-renderer" in html
    assert "dg-model-loading-spin" in html
    assert "padding: 0;" in html
    assert "inset: 0;" in html
    assert 'id="model-state"' not in html
    assert 'id="model-kind"' not in html
    assert 'class="dg-model-status"' not in html
    assert 'class="dg-model-controls"' in html
    assert 'id="model-scale-control"' not in html
    assert 'id="model-x-control"' not in html
    assert 'id="model-y-control"' not in html
    assert 'id="model-reset-control"' in html
    assert "/static/live2d-core.js" in html
    assert "/static/live2d-model.js" in html
    assert "function setModelMood" in script
    assert "els.modelState.hidden = !shouldShowModelState;" not in script
    assert "function handleModelWheel" in script
    assert "function modelViewTranslateTarget" in script
    assert "function modelViewDragReferenceSize" in script
    assert "Math.min(width, state.modelFitBase.width || width)" in script
    assert "target.offsetWidth || target.clientWidth || width" in script
    assert "function beginModelDrag" in script
    assert "function moveModelDrag" in script
    assert "function endModelDrag" in script
    assert "addEventListener('wheel', handleModelWheel" in script
    assert "setModelView({ scale: current.scale * step }, true);" in script
    assert "var SIDE_SPLIT_DEFAULT_RATIO = 0.64;" in script
    assert "var SIDE_MODEL_MIN_HEIGHT = 220;" in script
    assert "var SIDE_CHAT_MIN_HEIGHT = 280;" in script
    assert "minmax(' + SIDE_CHAT_MIN_HEIGHT + 'px, 1fr)'" in script
    assert "function beginSideResize" in script
    assert "function moveSideResize" in script
    assert "function endSideResize" in script
    assert "function handleSideResizeKey" in script
    assert "els.sideResizer.addEventListener('pointerdown', beginSideResize);" in script
    assert "if (!state.sideResize)" in script
    assert "state.modelFitBase" in script
    assert "var fitWidth = Math.max(1, Math.min(stageWidth, state.modelFitBase.width));" in script
    assert "var fitHeight = Math.max(1, Math.min(stageHeight, state.modelFitBase.height));" in script
    assert "manager.pixi_app.renderer.resize(stageWidth, stageHeight);" in script
    assert "canvas.style.setProperty('width', stageWidth + 'px', 'important')" in script
    assert "clampNumber(view.scale, 0.5, 5000" in script
    assert "scale: 190" in script
    assert "y: 28" in script
    assert "function resetModelView" in script
    assert "function loadModelViewSettings" in script
    assert "model.anchor.set(0.5, 0.5)" in script
    assert "model.x = fitWidth * 0.5;" in script
    assert "model.y = fitHeight * 0.5;" in script
    assert "model.x += fitWidth * (view.x / 100);" in script
    assert "model.y += fitHeight * (view.y / 100);" in script
    assert "function pulseModelMood" in script
    assert "function applyLive2DMood" in script
    assert "function initModelSlotForCurrentCharacter" in script
    assert "['loading', els.modelLoading]" in script
    assert "['mmd', els.mmdContainer]" in script
    assert "['pngtuber', els.pngtuberContainer]" in script
    assert "showModelLayer('loading');" in script
    assert "function loadLive2DSlot" in script
    assert "function loadVRMSlot" in script
    assert "function loadMMDSlot" in script
    assert "function loadPNGTuberSlot" in script
    assert "fetchCharacterAvatarConfig" in script
    assert "current_live2d_model?catgirl_name=" in script
    assert "/static/pngtuber-core.js" in html
    assert "/static/vrm-init.js" in html
    assert "/static/mmd-init.js" in html
    assert '"@moeru/three-mmd"' in html
    assert "window.__DRAWING_GUESS_AVATAR_SLOT__ = true;" in html
    assert "window._cardExportPage = true;" in html
    assert "state.vrmManager.animation.startLipSync(analyser);" in script
    assert "state.mmdManager.animationModule.startLipSync(analyser);" in script
    assert "state.pngtuberManager.setSpeaking(true);" in script
    assert "lipSyncStopTimer: null" in script
    assert "function isSpeechPlaybackAudible" in script
    assert "function armDrawingGuessLipSyncStop" in script
    assert "if (isSpeechPlaybackAudible(detail))" in script
    assert "scheduleDrawingGuessLipSyncStart();\n      return;\n    }\n    var response" not in script
    assert "ROUTE_API + '/route/start'" in script
    assert "ROUTE_API + '/route/heartbeat'" in script
    assert "ROUTE_API + '/route/end'" in script
    assert "function pushCanvasContextForRoute" in script
    assert "canvasContextPayload(!!force)" in script
    assert "output.type === 'game_canvas_context_request'" in script
    assert "ROUND_API + '/round/start'" in script
    assert "ROUND_API + '/ai-draw'" in script
    assert "ROUND_API + '/input'" in script
    assert "ROUND_API + '/choose-word'" in script
    assert "ROUND_API + '/timeout'" in script
    assert "ROUND_API + '/vision-guess'" in script
    assert "var completedRoute = !!options.finalSummary || state.phase === 'summary' || state.phase === 'final_summary';" in script
    assert "reason: completedRoute ? 'drawing_guess_game_over' : 'drawing_guess_abandoned'" in script
    assert "roundCompleted: completedRoute" in script
    assert "function renderFinalSummary() {\n    // 最终结算是回合终态" in script
    assert "beginRoundFlow();\n    state.aiGuessInFlight = false;" in script
    assert "if (state.phase !== 'ai_drawing') return;" in script
    assert "function continueAfterAiDrawingHalf(res, flowToken) {\n    if (!isCurrentRoundFlow(flowToken)) return;" in script
    assert "function requestGuessTimeout(flowToken, attempt)" in script
    assert "state.guessTimeoutRetryTimer = setTimeout(function ()" in script
    assert "setPhase('loading_round');\n    requestGuessTimeout(flowToken, 0);" in script
    assert "function finishGame() {\n    renderFinalSummary();\n    showExitConfirm();\n  }" in script
    assert "return endRoute(false, { finalSummary: true }).finally(showExitConfirm);" not in script
    assert 'id="voice-route-button" class="dg-voice-button"' in html
    assert "function handleVoiceRouteButton" in script
    assert "game_voice_stt_gate" in script
    assert "function speechRecognitionCtor" not in script
    assert "function startInternalVoiceRecognition" not in script
    assert "function stopInternalVoiceRecognition" not in script
    assert "function submitInternalVoiceTranscript" not in script
    assert "browser_speech_recognition" not in script
    assert "debugGesture: []" in script
    assert "function recordDebugGesture" in script
    assert "state.debugGesture.join('') === 'LLRR'" in script
    assert "function startDebugAiRound" in script
    assert "function startDebugUserRound" in script
    assert "debug_start_phase: 'word_picking'" in script
    assert "function triggerDebugAiGuessNow" in script
    assert "function updateDebugGuessCountdown" in script
    assert "state.aiGuessNextAt = Date.now() + delay;" in script
    assert "roundFlowToken: 0" in script
    assert "client_round_token: state.roundFlowToken" in script
    assert "function ensureCurrentRoundFlow" in script
    assert "if (err && err.staleRoundFlow) return;" in script
    assert "!state.debugRotateRounds && state.debugRoundMode === 'ai'" in script
    assert "!state.debugRotateRounds && state.debugRoundMode === 'user'" in script
    assert "els.debugTrigger.addEventListener('contextmenu'" in script
    assert "els.debugRotateRounds.addEventListener('change'" in script
    assert 'value="saved"' not in html
    assert 'drawingGuess.tutorial.memorySaved' not in html
    assert "dg-tutorial-guide" in html
    assert "dg-tutorial-voice" in html
    assert "drawingGuess.tutorial.quickTitle" in html
    assert "drawingGuess.tutorial.quickDraw" in html
    assert "drawingGuess.tutorial.quickGuess" in html
    assert "drawingGuess.tutorial.quickSummary" in html
    assert "drawingGuess.tutorial.voiceHint" in html
    assert "function normalizeMemoryConsent" in script
    assert "memory_consent: state.memoryConsent" in script
    assert "gameStarted: state.phase !== 'tutorial'" in script
    assert "var tutorialOpen = !!els.tutorialOverlay && !els.tutorialOverlay.hidden;" in script
    assert "function isCanvasEditablePhase()" in script
    assert "els.doneButton.hidden = roundSummaryOpen || finalSummaryOpen;" in script
    assert "els.nextRoundButton.hidden = !roundSummaryOpen;" in script
    assert "els.endButton.hidden = finalSummaryOpen;" in script
    assert "els.doneButton.disabled = tutorialOpen || !routeReady || !canvasEditable;" in script
    assert "eraserSize: $('eraser-size')" in script
    assert "brushToolKind: 'brush'" in script
    assert "colorPanelDrag: null" in script
    assert "colorWheelPointerId: null" in script
    assert "brushModeBucket: $('brush-mode-bucket')" in script
    assert "modelLoading: $('model-loading')" in script
    assert "colorPanelToggle: $('color-panel-toggle')" in script
    assert "eyedropperButton: $('eyedropper-button')" in script
    assert "colorWheel: $('color-wheel')" in script
    assert "colorHistoryColors: $('color-history-colors')" in script
    assert "canvasStage: $('canvas-stage')" in script
    assert "sizePreview: $('brush-size-preview')" in script
    assert "sizePreviewRing: $('brush-size-preview-ring')" in script
    assert "function normalizeHexColor" in script
    assert "function currentBrushColor" in script
    assert "COLOR_HISTORY_VISIBLE_COUNT = 7" in script
    assert "COLOR_HISTORY_MAX_COUNT = 28" in script
    assert "function renderColorHistory" in script
    assert "function removeBrushColorFromHistory" in script
    assert "function setBrushColor" in script
    assert "function hsvToHex" in script
    assert "function colorWheelGeometry" in script
    assert "borderLeftWidth" not in script
    assert "angle * 180 / Math.PI + 450" in script
    assert "function pickColorFromWheel" in script
    assert "function beginColorWheelPick" in script
    assert "function requestEyeDropperColor" in script
    assert "new window.EyeDropper().open()" in script
    assert "function hexToRgba" in script
    assert "function floodFillCanvas" in script
    assert "state.brushMode === 'brush' && state.brushToolKind === 'bucket'" in script
    assert "function setBrushToolKind" in script
    assert "function syncBrushToolButton" in script
    assert "function toggleColorPanel" in script
    assert "function beginColorPanelDrag" in script
    assert "dataset.brushKind" in script
    assert "function showSizePreview" in script
    assert "Math.max(2, size * ((scaleX + scaleY) / 2))" in script
    assert "Math.max(1, Math.min(3, diameter / 5))" in script
    assert "els.sizePreview.style.setProperty('--dg-size-preview-diameter', diameter.toFixed(2) + 'px');" in script
    assert "els.sizePreview.style.setProperty('--dg-size-preview-border', borderWidth.toFixed(2) + 'px');" in script
    assert "Math.max(18" not in script
    assert "els.sizePreview.style.setProperty('--dg-size-preview-color', currentBrushColor());" in script
    assert "els.sizePreview.style.removeProperty('--dg-size-preview-color');" in script
    assert "els.canvas.classList.contains('dg-hidden')) return" not in script
    assert "setTimeout(hideSizePreview, 2600)" in script
    assert "els.ctx.lineWidth = Number(els.eraserSize.value || 13);" in script
    assert "els.ctx.lineWidth = Number(els.brushSize.value || 7);" in script
    assert "Number(els.brushSize.value || 7) * 1.8" not in script
    assert "function closeToolPopovers" in script
    assert "function openToolPopover" in script
    assert "function bindToolPopoverEvents" in script
    assert "document.querySelectorAll('.dg-tool-popover')" in script
    assert "popover.addEventListener('pointerleave'" in script
    assert "closeToolPopovers(popover);" in script
    assert "bindToolPopoverEvents();" in script
    assert "els.brushModeBucket.addEventListener('click', function () { setBrushToolKind('bucket'); });" in script
    assert "els.brushSize.addEventListener('pointerdown', function () { showSizePreview('brush'); });" in script
    assert "els.brushSize.addEventListener('input', function () { showSizePreview('brush'); });" in script
    assert "els.eraserSize.addEventListener('pointerdown', function () { showSizePreview('eraser'); });" in script
    assert "els.eraserSize.addEventListener('input', function () { showSizePreview('eraser'); });" in script
    assert "els.colorPanelToggle.addEventListener('click', toggleColorPanel);" in script
    assert "els.eyedropperButton.addEventListener('click', requestEyeDropperColor);" in script
    assert "els.colorWheel.addEventListener('pointerdown', beginColorWheelPick);" in script
    assert "els.colorWheel.addEventListener('pointermove', moveColorWheelPick);" in script
    assert "els.brushColor.addEventListener('change', function ()" in script
    assert 'id="exit-confirm"' in html
    assert 'id="exit-reopen-button"' in html
    assert "function showExitConfirm" in script
    assert "function deferExitConfirm" in script
    assert "function showExitReopenButton" in script
    assert "els.exitReopenButton.addEventListener('click', showExitConfirm);" in script
    assert "function leaveDrawingGuessPage" in script
    assert "var ROUND_FALLBACK_SECONDS = 5 * 60;" in script
    assert "var AI_DRAW_REQUEST_TIMEOUT_MS = 70 * 1000;" in script
    assert "var AI_GUESS_REQUEST_TIMEOUT_MS = ROUND_FALLBACK_SECONDS * 1000 + 10000;" in script
    assert "var AI_GUESS_MIN_DELAY_MS = 10000;" in script
    assert "var AI_GUESS_MAX_DELAY_MS = 60000;" in script
    assert "var AI_DRAWING_PLACEHOLDER_DELAY_MS = 1200;" in script
    assert "placeholderDotsTimer: null" in script
    assert "aiDrawingPlaceholderTimer: null" in script
    assert "placeholderDetail: $('canvas-placeholder-detail')" in script
    assert "function startDotAnimation" in script
    assert "function scheduleAiDrawingPlaceholderHint" in script
    assert "startCanvasPlaceholderDots('drawingGuess.messages.aiDrawingWaiting'" in script
    assert "scheduleAiDrawingPlaceholderHint();" in script
    assert "clearAiDrawingPlaceholderHint(true);" in script
    assert "startCountdown(res.guess_seconds || ROUND_FALLBACK_SECONDS" in script
    assert "post(ROUND_API + '/ai-draw', roundPayload(), AI_DRAW_REQUEST_TIMEOUT_MS)" in script
    assert "timeoutError.code = 'request_timeout';" in script
    assert "function readableRequestError" in script
    assert "startCountdown(seconds || ROUND_FALLBACK_SECONDS" in script
    assert "els.canvas.toDataURL('image/png')" in script
    assert "state.phase !== 'user_drawing'" in script
    assert "submitGameChat(value)" in script
    assert "addUserMessage(value)" in script
    assert "state.phase !== 'summary'" in script
    assert "startThinkingEventMessage('drawingGuess.messages.aiGuessing'" in script
    assert "stopThinkingEventMessage()" in script
    assert "setChatPlaceholder('drawingGuess.input.summaryPlaceholder'" in script
    assert "startCountdown(ROUND_FALLBACK_SECONDS, handleAiGuessTimeout)" in script
    assert "postVisionGuess('', { first_guess: true })" in script
    assert "function addAiGuessOutcomeMessage" in script
    assert "drawingGuess.messages.aiGuessCorrect" in script
    assert "drawingGuess.messages.aiGuessWrong" in script
    assert "drawingGuess.messages.aiGuessMissAnswer" in script
    assert "function triggerSupplementGuess" in script
    assert "drawingGuess.messages.userSupplemented" not in script
    assert "function captureUserCanvasPng" in script
    assert "function persistCurrentUserCanvasSnapshot" in script
    assert "persistCurrentUserCanvasSnapshot();\n    setPhase('summary');" in script
    assert "state.pendingSupplementImage = state.userPng || ''" in script
    assert "postVisionGuess('', { supplement: true, image_data_url: state.userPng })" in script
    assert "state.pendingSupplementGuess = true" in script
    assert "scheduleNextRandomAiGuess()" in script
    assert "state.pendingAutoGuess = true" in script
    assert "state.pendingAutoGuessImage = snapshot || state.userPng || ''" in script
    assert "triggerRandomAiGuess(autoImage)" in script
    assert "flushDeferredAiGuessWork()" in script
    assert "if (!isCurrentRoundFlow(flowToken)) return;\n      state.chatInFlight = false;" in script
    assert "if (!isCurrentRoundFlow(flowToken)) return;\n      state.aiGuessInFlight = false;" in script
    assert "resetCanvas();\n        pushCanvasContextForRoute(true);" in script
    assert "if (state.brushMode === 'brush') state.hasDrawn = true;" in script
    assert "settle_on_miss: !!(options && options.settle_on_miss)" in script
    assert "ZH_PLACEHOLDER_FALLBACKS" in script
    assert "\u5148\u7ee7\u7eed\u804a\u5929\uff1b\u60f3\u8ba9\u5979\u518d\u731c\u65f6\u518d\u7ed9\u63d0\u793a" in script
    assert "DRAW_PICK_DURATION_MS" in script
    assert "prepareUserDrawing(res.user_draw_options || res.user_draw_answer" in script
    assert "showDrawPickAnimation(drawOptions, seconds)" in script
    assert "chooseUserDrawWord" in script
    assert "drawingGuess.messages.drawingPickReady" in script
    assert "dg-draw-pick-revealed" in script
    assert "dg-draw-pick-spread" in script
    assert 'id="draw-pick"' in html
    assert "dg-pick-deck" in html
    assert "dg-pick-card" in html
    assert "dg-pick-card-inner" in script
    assert "dg-pick-face-front" in script
    assert "dg-pick-option" in script
    assert "dg-draw-pick-ready .dg-pick-card-1" in html
    assert "dg-message-user" in html
    assert "dg-message-neko" in html
    assert "dg-message-event" in html
    assert "}), AI_GUESS_REQUEST_TIMEOUT_MS)" in script
    assert "saveAiSvg" in script
    assert "saveAiPngFile" in script
    assert "saveUserPng" in script
    assert "svgMarkupToPngBlob" in script
    assert "context.fillStyle = '#fffdfa';" in script
    assert "dg-summary-evaluation" in script
    assert "state.roundSummaries" in script
    assert "state.aiAnswerLabel = ''" in script
    assert "aiAnswerLabel: state.aiAnswerLabel || ''" in script
    assert "function summaryArtworkTitle" in script
    assert "var nekoTitle = summaryArtworkTitle('drawingGuess.summary.nekoArt'" in script
    assert "var userTitle = summaryArtworkTitle('drawingGuess.summary.userArt'" in script
    assert "drawingGuess.summary.userDrawAnswer" not in script
    assert "function renderFinalSummary" in script
    assert "function finishGame" in script
    assert "endRoute(false, { finalSummary: true })" not in script
    assert "els.summary.classList.toggle('dg-summary-final', !!finalSummary);" in script
    assert "dg-summary-list" in script
    assert ".dg-summary.dg-summary-final" in html
    assert "display: block;" in html
    assert "padding-bottom: 16px;" in html
    assert ".dg-summary.dg-summary-final .dg-round-summary" in html
    assert ".dg-summary.dg-summary-final .dg-summary-grid" in html
    assert ".dg-summary.dg-summary-final .dg-thumb" in html
    assert "grid-template-rows: auto auto auto;" in html
    assert ".dg-summary.dg-summary-final .dg-thumb-preview" in html
    assert "aspect-ratio: 4 / 3;" in html
    assert "width: 100%;" in html
    assert "height: auto;" in html
    assert ".dg-summary:not(.dg-summary-final) .dg-summary-list" in html
    assert "overflow: hidden;" in html
    assert "state.phase !== 'final_summary'" in script
    assert "drawingGuess.summary.finalTitle" in script
    assert "drawingGuess.summary.roundLabel" in script
    assert "drawingGuess.summary.noRounds" in script
    assert "data-save-ai-svg-index" in script
    assert "data-save-ai-png-index" in script
    assert "data-save-user-png-index" in script
    assert "data-download-ai-index" not in script
    assert "data-download-user-index" not in script
    assert "drawingGuess.summary.score" not in script
    assert "drawingGuess.summary.outcome." not in script
    assert "animateAiDrawing" in script
    assert "state.aiSvg = serializeAiDrawingSvg(els.aiDrawing) || state.aiSvg" in script
    assert "function screenRectToSvgBounds" in script
    assert "function measureSvgContentMetrics" in script
    assert "function clampCenterForBounds" in script
    assert "var viewBoxRatio = 240 / 180;" in script
    assert "transform 180ms" not in script
    assert "prefers-reduced-motion: reduce" in script
    assert "navigator.sendBeacon" in script


@pytest.mark.unit
def test_drawing_guess_locale_cache_version_bumped_for_save_art_actions():
    script = _i18n_script()

    assert "2026-07-11-drawing-guess-merge-doubao-speaker-id-model-type-3d-label-i18n" in script


@pytest.mark.unit
def test_drawing_guess_i18n_keys_exist_in_all_static_locales():
    required_keys = (
        "drawingGuess.title",
        "drawingGuess.status.loadingCharacter",
        "drawingGuess.status.active",
        "drawingGuess.layout.canvasTitle",
        "drawingGuess.layout.sideResize",
        "drawingGuess.tools.color",
        "drawingGuess.tools.bucket",
        "drawingGuess.tools.eyedropper",
        "drawingGuess.tools.basicColors",
        "drawingGuess.tools.historyColors",
        "drawingGuess.tutorial.memoryNone",
        "drawingGuess.tutorial.memorySummary",
        "drawingGuess.tutorial.quickTitle",
        "drawingGuess.tutorial.quickDraw",
        "drawingGuess.tutorial.quickGuess",
        "drawingGuess.tutorial.quickSummary",
        "drawingGuess.tutorial.voiceHint",
        "drawingGuess.actions.done",
        "drawingGuess.actions.saveNekoSvg",
        "drawingGuess.actions.saveNekoPng",
        "drawingGuess.actions.saveUserPng",
        "drawingGuess.exitConfirm.title",
        "drawingGuess.exitConfirm.message",
        "drawingGuess.exitConfirm.stay",
        "drawingGuess.exitConfirm.leave",
        "drawingGuess.exitConfirm.reopen",
        "drawingGuess.actions.start",
        "drawingGuess.memory.noneShort",
        "drawingGuess.phases.drawing_pick",
        "drawingGuess.phases.user_drawing",
        "drawingGuess.phases.final_summary",
        "drawingGuess.modelStates.idle",
        "drawingGuess.modelStates.loading",
        "drawingGuess.modelStates.drawing",
        "drawingGuess.modelStates.thinking",
        "drawingGuess.modelStates.guessing",
        "drawingGuess.modelStates.talking",
        "drawingGuess.modelStates.happy",
        "drawingGuess.modelControls.group",
        "drawingGuess.modelControls.scale",
        "drawingGuess.modelControls.x",
        "drawingGuess.modelControls.y",
        "drawingGuess.modelControls.reset",
        "drawingGuess.timer.seconds",
        "drawingGuess.messages.drawingPickTitle",
        "drawingGuess.messages.drawingPickSubtitle",
        "drawingGuess.messages.drawingPickReady",
        "drawingGuess.messages.drawingPickReveal",
        "drawingGuess.messages.aiDrawingWaiting",
        "drawingGuess.messages.aiGuessLine",
        "drawingGuess.messages.aiGuessCorrect",
        "drawingGuess.messages.aiGuessWrong",
        "drawingGuess.messages.aiGuessMissAnswer",
        "drawingGuess.messages.summaryReady",
        "drawingGuess.messages.userSupplemented",
        "drawingGuess.messages.requestTimeout",
        "drawingGuess.input.placeholder",
        "drawingGuess.input.hintPlaceholder",
        "drawingGuess.input.summaryPlaceholder",
        "drawingGuess.voice.connected",
        "drawingGuess.voice.connectHint",
        "drawingGuess.voice.connectedNotice",
        "drawingGuess.voice.connectHintNotice",
        "drawingGuess.voice.routeNotReady",
        "drawingGuess.summary.title",
        "drawingGuess.summary.finalTitle",
        "drawingGuess.summary.roundLabel",
        "drawingGuess.summary.noRounds",
        "drawingGuess.summary.userDrawAnswer",
        "drawingGuess.summary.nekoArt",
    )
    locale_files = sorted(LOCALES_DIR.glob("*.json"))
    assert {path.name for path in locale_files} >= {
        "en.json",
        "ja.json",
        "ko.json",
        "zh-CN.json",
        "zh-TW.json",
        "ru.json",
        "pt.json",
        "es.json",
    }

    for path in locale_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for key in required_keys:
            value = _get_nested(payload, key)
            assert isinstance(value, str) and value.strip(), f"{path.name} missing {key}"

        drawing_guess = payload["drawingGuess"]
        assert "???" not in drawing_guess["layout"]["canvasWaiting"]
        assert "???" not in drawing_guess["messages"]["aiDrawingWaiting"]
        assert "???" not in drawing_guess["messages"]["userSupplemented"]
        assert "???" not in drawing_guess["messages"]["requestTimeout"]
        assert "???" not in drawing_guess["exitConfirm"]["message"]
        assert "???" not in drawing_guess["exitConfirm"]["reopen"]
        assert "???" not in drawing_guess["summary"]["finalTitle"]
        assert "memorySaved" not in drawing_guess["tutorial"]
        assert "savedShort" not in drawing_guess["memory"]
