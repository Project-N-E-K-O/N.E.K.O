// Runs with the production interaction and preview scripts in either a VM or a browser.
async function exercisePreviewCleanup({ cleanupFails = false, removeFails = false, destroyFails = false, legacy = false } = {}) {
    const manager = new Live2DManager();
    const canvas = document.getElementById('live2d-preview-canvas');
    const mainCanvas = document.getElementById('live2d-canvas');
    const cleanupError = new TypeError('expression cleanup failed');
    const removeError = new Error('model removal failed');
    const destroyError = new Error('PIXI destruction failed');
    const warnings = [];
    const errors = [];
    const order = [];
    const oldWarn = console.warn;
    const oldError = console.error;
    console.warn = (_message, error) => warnings.push(error);
    console.error = (_message, error) => errors.push(error);
    let revealFired = false;
    let resizeCalls = 0;
    let governorStops = 0;
    const onResize = () => { resizeCalls += 1; };
    canvas.style.transition = 'opacity 1s';
    canvas.style.opacity = '0';
    canvas.style.pointerEvents = 'auto';
    canvas.style.cursor = 'grab';
    mainCanvas.style.pointerEvents = 'auto';
    manager.isLocked = true;
    manager.currentModel = { interactive: true };
    manager.isInitialized = true;
    manager._activeLoadToken = 2;
    manager._isLoadingModel = true;
    manager._modelLoadState = 'loading';
    manager._isModelReadyForInteraction = true;
    manager._canvasRevealTimer = setTimeout(() => { revealFired = true; }, 5);
    manager._previewResizeHandlerBound = true;
    manager._previewResizeHandler = onResize;
    manager._screenChangeHandler = onResize;
    manager._displayChangeHandler = onResize;
    window.addEventListener('resize', onResize);
    window.addEventListener('electron-display-changed', onResize);
    manager._stopIdleFpsGovernor = () => { governorStops += 1; };
    manager.pixi_app = {
        renderer: { view: canvas }, view: canvas,
        destroy(removeView) {
            if (removeView !== true) throw new Error('expected canvas removal');
            order.push('destroy');
            if (destroyFails) throw destroyError;
        },
    };
    manager.removeModel = async () => {
        order.push('remove');
        if (removeFails) throw removeError;
        manager.currentModel = null;
    };
    // Fault injection at a real callee, retaining the production listener cleanup.
    manager._cancelTouchSetExpressionRestore = () => {
        order.push('cleanup');
        if (cleanupFails) throw cleanupError;
    };
    if (legacy) manager.cleanupEventListeners = undefined;
    else manager.syncLive2DEffectiveInputLock();
    live2dPreviewManager = manager;
    currentPreviewModel = manager.currentModel;
    try {
        await destroyLive2DPreviewContext();
        await destroyLive2DPreviewContext(); // repeated close must remain harmless
        window.dispatchEvent(new Event('resize'));
        window.dispatchEvent(new Event('electron-display-changed'));
        window.dispatchEvent(new Event('neko-edge-peek-lock-changed'));
        await new Promise(resolve => setTimeout(resolve, 15));
        return {
            order, governorStops, resizeCalls, revealFired,
            released: live2dPreviewManager === null && currentPreviewModel === null
                && manager.pixi_app === null && manager.currentModel === null,
            reset: !manager._isLoadingModel && manager._modelLoadState === 'idle'
                && !manager._isModelReadyForInteraction && !manager.isInitialized
                && manager._canvasRevealTimer === null
                && manager._lastPIXIContext.canvasId === null,
            listenersRemoved: !manager._edgePeekLockChangedListener
                && !manager._previewResizeHandlerBound && !manager._previewResizeHandler
                && !manager._screenChangeHandler && !manager._displayChangeHandler,
            stylesCleared: canvas.style.transition === '' && canvas.style.opacity === '',
            mainInteractive: mainCanvas.style.pointerEvents === 'auto',
            cleanupErrorReported: warnings.includes(cleanupError) && cleanupError instanceof TypeError,
            destroyErrorReported: warnings.includes(destroyError),
            removeErrorReported: errors.includes(removeError),
        };
    } finally {
        console.warn = oldWarn;
        console.error = oldError;
        if (manager._canvasRevealTimer) clearTimeout(manager._canvasRevealTimer);
    }
}

module.exports = { exercisePreviewCleanup };
