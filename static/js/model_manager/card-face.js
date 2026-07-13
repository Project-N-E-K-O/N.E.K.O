
function canvasToPngBlob(canvas) {
    return new Promise((resolve, reject) => {
        canvas.toBlob(blob => {
            if (blob) resolve(blob);
            else reject(new Error('canvas_to_blob_failed'));
        }, 'image/png');
    });
}

function drawImageCover(ctx, source, dx, dy, dw, dh, options = {}) {
    const sw = source.width;
    const sh = source.height;
    const sourceRatio = sw / sh;
    const targetRatio = dw / dh;
    let sx = 0;
    let sy = 0;
    let cropW = sw;
    let cropH = sh;

    if (sourceRatio > targetRatio) {
        cropW = sh * targetRatio;
        sx = (sw - cropW) / 2;
    } else {
        cropH = sw / targetRatio;
        sy = (sh - cropH) / 2;
    }

    const focusPoint = options.focusPoint;
    const hasFocusPoint = focusPoint &&
        Number.isFinite(focusPoint.x) &&
        Number.isFinite(focusPoint.y);
    const zoom = Number(options.zoom || (hasFocusPoint ? 1.7 : 1));
    if (zoom > 1 || hasFocusPoint) {
        const focusX = hasFocusPoint
            ? clampCardFaceCrop(focusPoint.x / sw, 0, 1)
            : (Number.isFinite(options.focusX) ? options.focusX : 0.5);
        const focusY = hasFocusPoint
            ? clampCardFaceCrop(focusPoint.y / sh, 0, 1)
            : (Number.isFinite(options.focusY) ? options.focusY : 0.32);
        cropW = Math.max(1, cropW / zoom);
        cropH = Math.max(1, cropH / zoom);
        sx = clampCardFaceCrop(sw * focusX - cropW / 2, 0, sw - cropW);
        sy = clampCardFaceCrop(sh * focusY - cropH / 2, 0, sh - cropH);
    }

    ctx.drawImage(source, sx, sy, cropW, cropH, dx, dy, dw, dh);
}

function clampCardFaceCrop(value, min, max) {
    return Math.min(max, Math.max(min, value));
}

function getManagerHeadFocusInCanvas(manager, sourceCanvas) {
    if (!manager || !sourceCanvas || typeof manager.getHeadScreenAnchor !== 'function') {
        return null;
    }

    const anchor = manager.getHeadScreenAnchor();
    const canvas = manager.renderer?.domElement;
    const rect = canvas?.getBoundingClientRect?.();
    if (!anchor || !rect || rect.width <= 0 || rect.height <= 0) {
        return null;
    }

    const x = ((anchor.x - rect.left) / rect.width) * sourceCanvas.width;
    const y = ((anchor.y - rect.top) / rect.height) * sourceCanvas.height;
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
        return null;
    }

    return {
        x: clampCardFaceCrop(x, 0, sourceCanvas.width),
        y: clampCardFaceCrop(y, 0, sourceCanvas.height)
    };
}

function renderThreeSceneToCanvas(renderer, scene, camera) {
    const THREE = window.THREE;
    if (!THREE || !renderer || !scene || !camera) {
        throw new Error('three_context_not_ready');
    }

    const sourceCanvas = renderer.domElement;
    const width = sourceCanvas?.width || Math.round(sourceCanvas?.clientWidth || 0);
    const height = sourceCanvas?.height || Math.round(sourceCanvas?.clientHeight || 0);
    if (width <= 0 || height <= 0) {
        throw new Error('three_canvas_not_ready');
    }

    const renderTarget = new THREE.WebGLRenderTarget(width, height, {
        format: THREE.RGBAFormat,
        type: THREE.UnsignedByteType,
        depthBuffer: true,
        stencilBuffer: false
    });
    const previousTarget = renderer.getRenderTarget ? renderer.getRenderTarget() : null;
    const pixels = new Uint8Array(width * height * 4);

    try {
        renderer.setRenderTarget(renderTarget);
        renderer.clear(true, true, true);
        renderer.render(scene, camera);
        renderer.readRenderTargetPixels(renderTarget, 0, 0, width, height, pixels);
    } finally {
        renderer.setRenderTarget(previousTarget);
        renderTarget.dispose();
    }

    const output = document.createElement('canvas');
    output.width = width;
    output.height = height;
    const ctx = output.getContext('2d');
    if (!ctx) throw new Error('three_output_context_failed');

    const imageData = ctx.createImageData(width, height);
    const rowBytes = width * 4;
    for (let y = 0; y < height; y += 1) {
        const srcStart = (height - 1 - y) * rowBytes;
        const dstStart = y * rowBytes;
        imageData.data.set(pixels.subarray(srcStart, srcStart + rowBytes), dstStart);
    }
    ctx.putImageData(imageData, 0, 0);
    return output;
}

function captureLive2DStageToCanvas() {
    const app = window.live2dManager?.pixi_app;
    if (!app?.renderer || !app?.stage) {
        const fallbackCanvas = document.getElementById('live2d-canvas');
        if (!fallbackCanvas) throw new Error('live2d_context_not_ready');
        return fallbackCanvas;
    }

    app.renderer.render(app.stage);

    const extract = app.renderer.extract || app.renderer.plugins?.extract;
    if (extract && typeof extract.canvas === 'function') {
        const extracted = extract.canvas(app.stage);
        if (extracted && extracted.width > 0 && extracted.height > 0) {
            return extracted;
        }
    }

    return app.renderer.view || document.getElementById('live2d-canvas');
}

async function captureCurrentModelManagerCanvas(state = {}) {
    let sourceCanvas = null;
    let sourceManager = null;
    const modelType = state.currentModelType || 'live2d';
    const live3dSubType = state.currentLive3dSubType || '';

    await new Promise(resolve => requestAnimationFrame(resolve));

    if (modelType === 'live3d' && live3dSubType === 'mmd') {
        sourceManager = window.mmdManager;
        const core = sourceManager?.core;
        if (typeof sourceManager?.waitForRenderFrame === 'function') {
            await sourceManager.waitForRenderFrame(1200);
        } else if (typeof core?.waitForRenderFrame === 'function') {
            await core.waitForRenderFrame(1200);
        }
        sourceCanvas = renderThreeSceneToCanvas(sourceManager?.renderer, sourceManager?.scene, sourceManager?.camera);
    } else if (modelType === 'live3d') {
        sourceManager = window.vrmManager;
        if (sourceManager?.controls) sourceManager.controls.update();
        sourceCanvas = renderThreeSceneToCanvas(sourceManager?.renderer, sourceManager?.scene, sourceManager?.camera);
    } else {
        sourceCanvas = captureLive2DStageToCanvas();
    }

    if (!sourceCanvas || sourceCanvas.width <= 0 || sourceCanvas.height <= 0) {
        throw new Error('model_canvas_not_ready');
    }

    const copy = document.createElement('canvas');
    copy.width = sourceCanvas.width;
    copy.height = sourceCanvas.height;
    const copyCtx = copy.getContext('2d');
    if (!copyCtx) throw new Error('copy_canvas_context_failed');
    copyCtx.drawImage(sourceCanvas, 0, 0);
    return {
        canvas: copy,
        manager: sourceManager,
        modelType,
        live3dSubType
    };
}

function getPNGTuberDrawableSize(drawable) {
    if (!drawable) return { width: 0, height: 0 };
    return {
        width: drawable.width || drawable.naturalWidth || drawable.clientWidth || 0,
        height: drawable.height || drawable.naturalHeight || drawable.clientHeight || 0
    };
}

function isVisiblePNGTuberDrawable(drawable) {
    if (!drawable) return false;
    const size = getPNGTuberDrawableSize(drawable);
    if (!size.width || !size.height) return false;
    if (drawable.hidden || drawable.classList?.contains('hidden')) return false;
    if (drawable.style?.display === 'none') return false;
    if (typeof window.getComputedStyle === 'function') {
        const style = window.getComputedStyle(drawable);
        if (style.display === 'none' || style.visibility === 'hidden') return false;
    }
    return true;
}

function getPNGTuberCaptureDrawable() {
    const manager = window.pngtuberManager;
    if (manager && typeof manager.ensureContainer === 'function') {
        try {
            manager.ensureContainer();
        } catch (error) {
            console.warn('[model_manager] PNGTuber 容器准备失败:', error);
        }
    }

    if (isVisiblePNGTuberDrawable(manager?.image)) {
        return manager.image;
    }

    const container = document.getElementById('pngtuber-container');
    if (!container) return null;
    const drawables = Array.from(container.querySelectorAll('canvas.pngtuber-layered-canvas, img.pngtuber-image'));
    return drawables.find(isVisiblePNGTuberDrawable) || null;
}

function waitForPNGTuberImageDrawable(drawable) {
    if (!(drawable instanceof HTMLImageElement)) return Promise.resolve();
    if (drawable.complete && drawable.naturalWidth > 0 && drawable.naturalHeight > 0) {
        return Promise.resolve();
    }
    return new Promise((resolve, reject) => {
        const cleanup = () => {
            drawable.removeEventListener('load', onLoad);
            drawable.removeEventListener('error', onError);
        };
        const onLoad = () => {
            cleanup();
            resolve();
        };
        const onError = () => {
            cleanup();
            reject(new Error('pngtuber_image_load_failed'));
        };
        drawable.addEventListener('load', onLoad, { once: true });
        drawable.addEventListener('error', onError, { once: true });
    });
}

function isRemotePNGTuberDrawable(drawable) {
    if (!(drawable instanceof HTMLImageElement)) return false;
    const src = drawable.currentSrc || drawable.src || '';
    return /^https?:\/\//i.test(src) && !src.startsWith(window.location.origin);
}

async function capturePNGTuberPreviewToCanvas() {
    await new Promise(resolve => requestAnimationFrame(resolve));
    const drawable = getPNGTuberCaptureDrawable();
    if (!drawable) throw new Error('pngtuber_drawable_not_ready');
    await waitForPNGTuberImageDrawable(drawable);
    if (isRemotePNGTuberDrawable(drawable)) {
        throw new Error('pngtuber_remote_card_face_unsupported');
    }
    const { width, height } = getPNGTuberDrawableSize(drawable);
    if (!width || !height) throw new Error('pngtuber_drawable_not_ready');
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('pngtuber_canvas_context_failed');
    ctx.drawImage(drawable, 0, 0, width, height);
    return canvas;
}

function resolveDefaultCardFacePortraitModelType(state = {}) {
    const modelType = String(state.currentModelType || 'live2d').toLowerCase();
    const live3dSubType = String(state.currentLive3dSubType || '').toLowerCase();
    if (modelType === 'pngtuber') return 'pngtuber';
    if (modelType === 'live3d') {
        if (live3dSubType === 'mmd') return 'mmd';
        if (live3dSubType === 'vrm') return 'vrm';
        if (window.mmdManager?.currentModel?.mesh) return 'mmd';
        return 'vrm';
    }
    return modelType === 'mmd' || modelType === 'vrm' ? modelType : 'live2d';
}

async function captureDefaultCardFaceModelImage(state = {}, width, height) {
    const portraitModelType = resolveDefaultCardFacePortraitModelType(state);
    if (portraitModelType === 'pngtuber') {
        return {
            canvas: await capturePNGTuberPreviewToCanvas(),
            drawOptions: {
                zoom: 1.2,
                focusY: 0.45
            }
        };
    }

    if (window.avatarPortrait && typeof window.avatarPortrait.capture === 'function') {
        try {
            const portrait = await window.avatarPortrait.capture({
                width,
                height,
                padding: 0.035,
                background: 'transparent',
                shape: 'square',
                radius: 0,
                cropMode: 'headshot',
                modelType: portraitModelType
            });

            if (portrait?.canvas && portrait.canvas.width > 0 && portrait.canvas.height > 0) {
                return {
                    canvas: portrait.canvas,
                    drawOptions: {}
                };
            }
        } catch (error) {
            console.warn('[模型管理] 默认卡面头像裁切失败，回退模型画布截图:', error);
        }
    }

    const capture = await captureCurrentModelManagerCanvas(state);
    const sourceCanvas = capture.canvas;
    const headFocus = getManagerHeadFocusInCanvas(capture.manager, sourceCanvas);
    return {
        canvas: sourceCanvas,
        drawOptions: headFocus
            ? {
                // 回退路径：优先对齐 3D 模型头部骨骼锚点。
                zoom: 1.7,
                focusPoint: headFocus
            }
            : {
                // 回退路径：无头像识别能力时使用偏上构图。
                zoom: 1.45,
                focusY: 0.32
            }
    };
}

async function generateDefaultCardFaceFromModelManager(lanlanName, state = {}, options = {}) {
    const abortSignal = options.signal || null;
    const shouldCancel = typeof options.shouldCancel === 'function' ? options.shouldCancel : null;
    const waitingMessage = modelManagerText('cardExport.autoSavingDefaultCardFace', '正在生成默认卡面...');
    const finishSettingsWaiting = options.skipSettingsWaiting
        ? null
        : beginModelManagerSettingsWaiting(waitingMessage);
    const throwIfCancelled = () => {
        if ((shouldCancel && shouldCancel()) || abortSignal?.aborted) {
            const error = new Error('默认卡面生成已取消');
            error.name = 'AbortError';
            throw error;
        }
    };

    try {
        throwIfCancelled();
        setModelManagerStatusText(waitingMessage);

        const cardW = 600;
        const cardH = 800;
        const modelImage = options.modelImage || await captureDefaultCardFaceModelImage(state, cardW, cardH);
        throwIfCancelled();
        const sourceCanvas = modelImage.canvas;
        const output = document.createElement('canvas');
        output.width = cardW;
        output.height = cardH;
        const ctx = output.getContext('2d');
        if (!ctx) throw new Error('card_canvas_context_failed');

        ctx.fillStyle = '#E8F4F8';
        ctx.fillRect(0, 0, cardW, cardH);

        drawImageCover(
            ctx,
            sourceCanvas,
            0,
            0,
            cardW,
            cardH,
            modelImage.drawOptions || {}
        );

        const cardBlob = await canvasToPngBlob(output);
        throwIfCancelled();
        const formData = new FormData();
        formData.append('image', cardBlob, 'card_face.png');

        const controller = new AbortController();
        const abortFallbackUpload = () => controller.abort();
        if (abortSignal) {
            if (abortSignal.aborted) {
                abortFallbackUpload();
            } else {
                abortSignal.addEventListener('abort', abortFallbackUpload, { once: true });
            }
        }
        const timeoutId = setTimeout(() => controller.abort(), 20000);
        let response;
        try {
            throwIfCancelled();
            response = await fetch(
                `/api/characters/catgirl/${encodeURIComponent(lanlanName)}/card-face`,
                { method: 'PUT', body: formData, signal: controller.signal }
            );
        } catch (error) {
            if (error && error.name === 'AbortError') {
                if ((shouldCancel && shouldCancel()) || abortSignal?.aborted) {
                    const abortError = new Error('默认卡面生成已取消');
                    abortError.name = 'AbortError';
                    throw abortError;
                }
                throw new Error('默认卡面上传超时，请稍后重试');
            }
            throw error;
        } finally {
            clearTimeout(timeoutId);
            if (abortSignal) {
                abortSignal.removeEventListener('abort', abortFallbackUpload);
            }
        }
        throwIfCancelled();
        if (!response.ok) {
            const errData = await response.json().catch(() => ({}));
            throw new Error(errData.error || `HTTP ${response.status}`);
        }

        notifyCardFaceUpdatedFromModelManager(lanlanName);
    } finally {
        if (typeof finishSettingsWaiting === 'function') {
            finishSettingsWaiting();
        }
    }
}

async function offerCardFaceAfterModelSave(state = {}) {
    if (window._modelManagerCardFacePromptActive) return;
    cleanupCardMakerCloseFallbackWatcher();
    window._modelManagerCardFacePromptActive = true;
    try {
        const lanlanName = await resolveModelManagerLanlanName();
        if (!lanlanName) return;

        const cardFaceChoice = await showDecisionPrompt({
            title: modelManagerText('modelManager.editCardFaceAfterModelSaveTitle', '编辑卡面'),
            message: modelManagerText('modelManager.editCardFaceAfterModelSaveMessage', '模型设置已保存。是否要现在编辑卡面？'),
            buttons: [
                {
                    value: 'edit',
                    text: modelManagerText('modelManager.editCardFaceNow', '编辑卡面'),
                    variant: 'primary'
                },
                {
                    value: 'default',
                    text: modelManagerText('modelManager.createDefaultCardFace', '生成默认卡面'),
                    variant: 'secondary'
                }
            ]
        });

        if (cardFaceChoice === 'edit') {
            const fallbackToken = createCardMakerFallbackToken();
            const makerWindow = openCardMakerFromModelManager(lanlanName, {
                fallbackDefaultOnClose: true,
                fallbackToken
            });
            if (!makerWindow) {
                const message = modelManagerText('cardExport.popupBlocked', '弹窗被阻止，请允许弹窗后重试');
                setModelManagerStatusText(message);
                try {
                    await generateDefaultCardFaceFromModelManager(lanlanName, state);
                } catch (error) {
                    console.error('[模型管理] 弹窗被阻止后的默认卡面兜底生成失败:', error);
                    setModelManagerStatusText(
                        error && error.message
                            ? error.message
                            : modelManagerText('cardExport.autoSaveDefaultCardFaceFailed', '默认卡面生成失败')
                    );
                }
            } else {
                watchCardMakerCloseForDefaultCardFace(makerWindow, lanlanName, state, { fallbackToken });
            }
        } else if (cardFaceChoice === 'default') {
            try {
                await generateDefaultCardFaceFromModelManager(lanlanName, state);
            } catch (error) {
                console.error('[模型管理] 生成默认卡面失败:', error);
                setModelManagerStatusText(
                    error && error.message
                        ? error.message
                        : modelManagerText('cardExport.autoSaveDefaultCardFaceFailed', '默认卡面生成失败')
                );
            }
        }
        // 不管走哪条分支（用户取消、卡面生成失败也好），模型本身已经保存成功，
        // 都要走下面的统一收尾，否则主界面不会刷新、未保存标记残留，会反复弹同一个提示喵。

        window.hasUnsavedChanges = false;
        await notifyMainPageModelReload();
        window._modelManagerModelChangedSinceSave = false;
        window._modelManagerLoadedFallbackModel = false;
    } finally {
        window._modelManagerCardFacePromptActive = false;
    }
}
/**
 * ===== 代码质量改进：路径处理统一化 (DRY 原则) =====
 *
 * ModelPathHelper: 统一处理所有模型路径标准化逻辑
 *
 * 改进原因：
 * - 之前路径处理逻辑分散在多个地方（上传回调、模型选择、加载等）
 * - 重复代码导致维护困难，容易出现不一致
 *
 * 功能：
 * - normalizeModelPath(): 标准化模型路径，处理 Windows 反斜杠、/user_vrm/ 前缀等
 * - vrmToUrl(): VRM 专用路径转换（内部调用 normalizeModelPath）
 *
 * 使用位置：
 * - loadCurrentCharacterModel()
 * - vrmModelSelect change 事件监听器
 * - saveModelToCharacter()
 * - 以及其他所有需要路径标准化的地方
 */
