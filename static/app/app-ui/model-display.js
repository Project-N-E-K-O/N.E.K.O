/**
 * app-ui/model-display.js
 * UI display helpers extracted from app.js.
 *
 * Exposed as window.appUi.
 * Dependencies:
 * - window.appState (S) - shared mutable state
 * - window.appConst (C) - frozen constants
 * - window.appUtils - utility helpers
 * - window.t / window.safeT - i18n
 * - window.lanlan_config - character config
 * Load all parts in filename order; this is a classic global script (no import/export).
 */
(function () {
    'use strict';

    window.appUi = window.appUi || {};
    const I = window.__appUiParts || (window.__appUiParts = {});
    I.hideLive2d = function hideLive2d() {
        console.log('[App] hideLive2d函数被调用');
        const container = document.getElementById('live2d-container');
        console.log('[App] hideLive2d调用前，容器类列表:', container.classList.toString());

        // 首先清除任何可能干扰动画的强制显示样式
        container.style.removeProperty('visibility');
        container.style.removeProperty('display');
        container.style.removeProperty('opacity');
        container.style.removeProperty('transform');

        // 取消 return 渐入的清理定时器（防止与退出动画冲突）
        if (window._returnFadeTimer) {
            clearTimeout(window._returnFadeTimer);
            window._returnFadeTimer = null;
        }
        // 重置 PIXI model alpha 到 1（确保退出动画时模型不透明）
        if (window.live2dManager) {
            const fadeModel = window.live2dManager.getCurrentModel();
            if (fadeModel && !fadeModel.destroyed) {
                fadeModel.alpha = 1;
            }
        }
        const isGoodbyeExiting = container.getAttribute('data-neko-model-goodbye-exiting') === 'true';
        // 清除 canvas 上的渐入动画残留样式。model-to-cat 退出过程中不要清除，
        // 否则 resetSessionButton 触发的 hideLive2d 会打断提前透明。
        const live2dCanvasForHide = document.getElementById('live2d-canvas');
        if (live2dCanvasForHide && !isGoodbyeExiting) {
            live2dCanvasForHide.style.transition = '';
            live2dCanvasForHide.style.opacity = '';
        }

        // 添加minimized类，触发CSS过渡动画
        I.playModelGoodbyeExit(container, I.getActiveModelTransitionRect());
        console.log('[App] hideLive2d调用后，容器类列表:', container.classList.toString());

        // 添加一个延迟检查，确保类被正确添加
        setTimeout(() => {
            console.log('[App] 延迟检查容器类列表:', container.classList.toString());
        }, 100);
    }

    I.mod.hideLive2d = I.hideLive2d;

    function shouldPreserveYuiGuideLive2DPreparing() {
        return window.nekoYuiGuideLive2dPreparing === true
            || (
                window.isInTutorial === true
                && typeof document !== 'undefined'
                && document.body
                && document.body.classList
                && document.body.classList.contains('yui-guide-live2d-preparing')
            );
    }

    function hideYuiGuideLive2DPreparingControls() {
        [
            'live2d-floating-buttons',
            'live2d-lock-icon',
            'live2d-return-button-container'
        ].forEach((id) => {
            const element = document.getElementById(id);
            if (!element || !element.style || typeof element.style.removeProperty !== 'function') {
                return;
            }
            element.style.setProperty('display', 'none', 'important');
            element.style.setProperty('visibility', 'hidden', 'important');
            element.style.setProperty('opacity', '0', 'important');
            element.style.setProperty('pointer-events', 'none', 'important');
        });
    }

    function restoreYuiGuideLive2DPreparingControls() {
        [
            'live2d-floating-buttons',
            'live2d-lock-icon'
        ].forEach((id) => {
            const element = document.getElementById(id);
            if (!element || !element.style || typeof element.style.removeProperty !== 'function') {
                return;
            }
            element.style.removeProperty('display');
            element.style.removeProperty('visibility');
            element.style.removeProperty('opacity');
            element.style.removeProperty('pointer-events');
        });
    }

    I.keepAvatarRootContainerPassthrough = function keepAvatarRootContainerPassthrough(container) {
        if (!container || !container.id || !container.style) return false;
        if (container.id !== 'live2d-container' && container.id !== 'pngtuber-container') return false;
        container.style.setProperty('pointer-events', 'none', 'important');
        return true;
    }

    function restoreLive2DDisplaySurface(reason) {
        const preserveAvatarCornerPeekOpacity = window.nekoYuiGuideAvatarCornerPeekActive === true;
        const preserveYuiGuidePreparing = shouldPreserveYuiGuideLive2DPreparing();
        if (!preserveYuiGuidePreparing) {
            restoreYuiGuideLive2DPreparingControls();
        }
        if (document.body && document.body.classList) {
            if (!preserveYuiGuidePreparing) {
                document.body.classList.remove('yui-guide-live2d-preparing');
            }
            document.body.classList.remove('yui-guide-return-petal-fade');
        }
        if (document.body && document.body.style && typeof document.body.style.removeProperty === 'function') {
            document.body.style.removeProperty('--yui-guide-return-avatar-opacity');
        }

        const live2dContainer = document.getElementById('live2d-container');
        if (live2dContainer) {
            live2dContainer.classList.remove('hidden');
            live2dContainer.classList.remove('minimized');
            live2dContainer.removeAttribute('data-neko-model-goodbye-exiting');
            live2dContainer.style.display = 'block';
            live2dContainer.style.visibility = 'visible';
            live2dContainer.style.removeProperty('transition');
            if (preserveYuiGuidePreparing) {
                // 新手教程开场演出会在首句动作起点统一 reveal。
            } else if (!preserveAvatarCornerPeekOpacity) {
                live2dContainer.style.removeProperty('opacity');
            }
            I.keepAvatarRootContainerPassthrough(live2dContainer);
        }

        const live2dCanvas = document.getElementById('live2d-canvas');
        if (live2dCanvas) {
            live2dCanvas.classList.remove('minimized');
            live2dCanvas.style.display = 'block';
            live2dCanvas.style.removeProperty('transition');
            if (preserveYuiGuidePreparing) {
                live2dCanvas.style.removeProperty('pointer-events');
            } else if (!preserveAvatarCornerPeekOpacity) {
                live2dCanvas.style.setProperty('opacity', '1', 'important');
                live2dCanvas.style.setProperty('pointer-events', 'auto', 'important');
            }
            live2dCanvas.style.setProperty('visibility', 'visible', 'important');
        }
    }

    function activateLive2DRenderForDisplay(reason) {
        const preserveAvatarCornerPeekOpacity = window.nekoYuiGuideAvatarCornerPeekActive === true;
        const manager = window.live2dManager || null;
        const app = manager && manager.pixi_app;
        const ticker = app && app.ticker;
        const model = manager && (typeof manager.getCurrentModel === 'function'
            ? manager.getCurrentModel()
            : manager.currentModel);

        try {
            if (model) {
                model.visible = true;
                if (!preserveAvatarCornerPeekOpacity) {
                    model.alpha = 1;
                }
                if (model.renderable !== undefined) {
                    model.renderable = true;
                }
            }
            if (app && app.stage) {
                app.stage.visible = true;
                if (!preserveAvatarCornerPeekOpacity) {
                    app.stage.alpha = 1;
                }
                if (app.stage.renderable !== undefined) {
                    app.stage.renderable = true;
                }
            }
            if (ticker) {
                if (!ticker.started && typeof ticker.start === 'function') {
                    ticker.start();
                }
                if (typeof ticker.update === 'function') {
                    ticker.update();
                }
            }
            if (app && app.renderer && app.stage && typeof app.renderer.render === 'function') {
                app.renderer.render(app.stage);
            }
        } catch (error) {
            console.warn('[App] Live2D render activation failed:', reason || 'show-live2d', error);
        }
    }

    function scheduleLive2DDisplayActivation(reason) {
        activateLive2DRenderForDisplay(reason || 'show-live2d');
        [80, 300].forEach((delayMs) => {
            window.setTimeout(() => {
                activateLive2DRenderForDisplay((reason || 'show-live2d') + ':delay-' + delayMs);
            }, delayMs);
        });
    }

    // --- showLive2d ---
    I.showLive2d = function showLive2d() {
        console.log('[App] showLive2d函数被调用');

        if ((window.lanlan_config?.model_type || '').toLowerCase() === 'pngtuber') {
            console.log('[App] showLive2d: 当前为 PNGTuber 模式，跳过 Live2D 显示');
            const live2dContainerForPngtuber = document.getElementById('live2d-container');
            if (live2dContainerForPngtuber) {
                live2dContainerForPngtuber.style.display = 'none';
                live2dContainerForPngtuber.classList.add('hidden');
            }
            const live2dCanvasForPngtuber = document.getElementById('live2d-canvas');
            if (live2dCanvasForPngtuber) {
                live2dCanvasForPngtuber.style.visibility = 'hidden';
                live2dCanvasForPngtuber.style.pointerEvents = 'none';
            }
            document.querySelectorAll('#live2d-floating-buttons, #live2d-lock-icon, #live2d-return-button-container')
                .forEach(el => el.remove());
            return;
        }

        // 检查是否处于"请她离开"状态
        if (window.live2dManager && window.live2dManager._goodbyeClicked) {
            console.log('[App] showLive2d: 当前处于"请她离开"状态，跳过显示逻辑');
            return;
        }

        if (window.pngtuberManager && typeof window.pngtuberManager.hide === 'function') {
            window.pngtuberManager.hide();
        }
        if (window.cleanupPNGTuberOverlayUI && typeof window.cleanupPNGTuberOverlayUI === 'function') {
            window.cleanupPNGTuberOverlayUI();
        } else {
            document.querySelectorAll('#pngtuber-floating-buttons, #pngtuber-lock-icon, #pngtuber-return-button-container')
                .forEach(el => el.remove());
        }
        const pngtuberContainerForLive2d = document.getElementById('pngtuber-container');
        if (pngtuberContainerForLive2d) {
            pngtuberContainerForLive2d.style.display = 'none';
            pngtuberContainerForLive2d.classList.add('hidden');
        }

        const container = document.getElementById('live2d-container');
        console.log('[App] showLive2d调用前，容器类列表:', container.classList.toString());
        const preserveYuiGuidePreparing = shouldPreserveYuiGuideLive2DPreparing();
        if (preserveYuiGuidePreparing) {
            hideYuiGuideLive2DPreparingControls();
        }

        // 检测模型是否已经可见（避免不必要的淡入动画导致闪烁）
        const isAlreadyVisible = container &&
            !container.classList.contains('minimized') &&
            !container.classList.contains('hidden') &&
            container.style.display !== 'none' &&
            getComputedStyle(container).display !== 'none';

        // 检查Live2D浮动按钮是否存在，如果不存在则重新创建
        let floatingButtons = document.getElementById('live2d-floating-buttons');
        console.log('[showLive2d] 检查浮动按钮 - 存在:', !!floatingButtons, 'live2dManager:', !!window.live2dManager);

        if (!floatingButtons && window.live2dManager) {
            console.log('[showLive2d] Live2D浮动按钮不存在，准备重新创建');
            const currentModel = window.live2dManager.getCurrentModel();
            console.log('[showLive2d] currentModel:', !!currentModel, 'setupFloatingButtons:', typeof window.live2dManager.setupFloatingButtons);

            if (currentModel && typeof window.live2dManager.setupFloatingButtons === 'function') {
                console.log('[showLive2d] 调用 setupFloatingButtons');
                window.live2dManager.setupFloatingButtons(currentModel);
                floatingButtons = document.getElementById('live2d-floating-buttons');
                console.log('[showLive2d] 创建后按钮存在:', !!floatingButtons);
            } else {
                console.warn('[showLive2d] 无法重新创建按钮 - currentModel或setupFloatingButtons不可用');
            }
        }

        // 确保浮动按钮显示
        if (!preserveYuiGuidePreparing && floatingButtons) {
            floatingButtons.style.setProperty('display', 'flex', 'important');
            floatingButtons.style.setProperty('visibility', 'visible', 'important');
            floatingButtons.style.setProperty('opacity', '1', 'important');
            floatingButtons.style.setProperty('pointer-events', 'auto', 'important');
        }

        const lockIcon = document.getElementById('live2d-lock-icon');
        if (!preserveYuiGuidePreparing && lockIcon) {
            lockIcon.style.removeProperty('display');
            lockIcon.style.removeProperty('visibility');
            lockIcon.style.removeProperty('opacity');
            lockIcon.style.removeProperty('pointer-events');
        } else if (preserveYuiGuidePreparing) {
            hideYuiGuideLive2DPreparingControls();
        }

        // 原生按钮和status栏应该永不出现，保持隐藏状态
        const sidebar = document.getElementById('sidebar');
        const sidebarbox = document.getElementById('sidebarbox');

        if (sidebar) {
            sidebar.style.setProperty('display', 'none', 'important');
            sidebar.style.setProperty('visibility', 'hidden', 'important');
            sidebar.style.setProperty('opacity', '0', 'important');
        }

        if (sidebarbox) {
            sidebarbox.style.setProperty('display', 'none', 'important');
            sidebarbox.style.setProperty('visibility', 'hidden', 'important');
            sidebarbox.style.setProperty('opacity', '0', 'important');
        }

        const sideButtons = document.querySelectorAll('.side-btn');
        sideButtons.forEach(btn => {
            btn.style.setProperty('display', 'none', 'important');
            btn.style.setProperty('visibility', 'hidden', 'important');
            btn.style.setProperty('opacity', '0', 'important');
        });

        const statusElement = document.getElementById('status');
        if (statusElement) {
            statusElement.style.setProperty('display', 'none', 'important');
            statusElement.style.setProperty('visibility', 'hidden', 'important');
            statusElement.style.setProperty('opacity', '0', 'important');
        }

        // 取消"请她离开"的延迟隐藏定时器
        if (window._goodbyeHideTimerId) {
            clearTimeout(window._goodbyeHideTimerId);
            window._goodbyeHideTimerId = null;
            console.log('[App] showLive2d: 已取消 goodbye 延迟隐藏定时器');
        }

        // 取消上一次 return 渐入的清理定时器
        if (window._returnFadeTimer) {
            clearTimeout(window._returnFadeTimer);
            window._returnFadeTimer = null;
        }

        // 如果模型已经可见，跳过淡入动画
        if (isAlreadyVisible) {
            console.log('[App] showLive2d: 模型已可见，跳过淡入动画');
            const fadeModel = window.live2dManager ? window.live2dManager.getCurrentModel() : null;
            if (fadeModel && !fadeModel.destroyed) {
                fadeModel.alpha = 1;
            }
            restoreLive2DDisplaySurface('show-live2d-fast-path');
            const pixiApp = window.live2dManager ? window.live2dManager.pixi_app : null;
            if (pixiApp && pixiApp.ticker && !pixiApp.ticker.started) {
                pixiApp.ticker.start();
            }
            scheduleLive2DDisplayActivation('show-live2d-fast-path');
            console.log('[App] showLive2d调用后（快速路径），容器类列表:', container.classList.toString());
            return;
        }

        // 渐入动画 - 复刻 _configureLoadedModel 的 CSS 揭示机制
        const fadeModel = window.live2dManager ? window.live2dManager.getCurrentModel() : null;
        if (fadeModel && !fadeModel.destroyed) {
            fadeModel.alpha = 1;
        }

        const live2dCanvas = document.getElementById('live2d-canvas');
        if (live2dCanvas) {
            live2dCanvas.style.transition = 'none';
            live2dCanvas.style.opacity = '0.001';
        }
        const modelReturnEnterRect = I.consumeModelReturnEnterRect();

        I.prepareModelReturnContainer(container, modelReturnEnterRect);

        if (live2dCanvas) {
            live2dCanvas.style.setProperty('visibility', 'visible', 'important');
            live2dCanvas.style.setProperty('pointer-events', 'auto', 'important');
        }

        // 强制浏览器刷新布局
        if (live2dCanvas) {
            void live2dCanvas.offsetWidth;
        }

        container.style.transition = '';
        if (modelReturnEnterRect) {
            I.playModelReturnEnter(container, modelReturnEnterRect);
        }

        // 确保 PIXI ticker 在运行
        const pixiApp = window.live2dManager ? window.live2dManager.pixi_app : null;
        if (pixiApp && pixiApp.ticker && !pixiApp.ticker.started) {
            pixiApp.ticker.start();
        }

        // 触发 CSS transition 淡入
        if (live2dCanvas) {
            scheduleLive2DDisplayActivation('show-live2d');
            live2dCanvas.style.transition = I.NEKO_MODEL_RETURN_CANVAS_FADE_TRANSITION;
            live2dCanvas.style.opacity = '1';

            window._returnFadeTimer = setTimeout(() => {
                if (live2dCanvas) {
                    live2dCanvas.style.transition = '';
                    live2dCanvas.style.opacity = '';
                }
                // 清除容器的内联 opacity，使 CSS class（如 locked-hover-fade）能正常生效
                container.style.removeProperty('opacity');
                window._returnFadeTimer = null;
            }, I.NEKO_MODEL_RETURN_CANVAS_FADE_CLEANUP_MS);
        }

        if (container.classList.length === 0) {
            container.removeAttribute('class');
        }

        console.log('[App] showLive2d调用后，容器类列表:', container.classList.toString());
    }

    I.mod.showLive2d = I.showLive2d;

    // --- viewport helpers ---
    function isMobileViewport() {
        return typeof window.isMobileWidth === 'function'
            ? window.isMobileWidth()
            : (window.innerWidth <= 768);
    }

    const NEKO_MODEL_VIEWPORT_RESTORE_FALLBACK_MS = 900;
    const NEKO_MODEL_VIEWPORT_RESTORE_RETRY_MS = 300;
    const NEKO_NATIVE_RETURN_BALL_SHRINK_VIEWPORT_SIZE = 160;
    let pendingNativeModelViewportRestoreBounds = null;

    function normalizeModelViewportBounds(bounds) {
        const candidate = bounds && typeof bounds === 'object'
            ? (bounds.requestedBounds || bounds.bounds || bounds)
            : null;
        if (!candidate) return null;
        const x = Number.isFinite(Number(candidate.x))
            ? Math.round(Number(candidate.x))
            : (Number.isFinite(Number(window.screenX)) ? Math.round(Number(window.screenX)) : 0);
        const y = Number.isFinite(Number(candidate.y))
            ? Math.round(Number(candidate.y))
            : (Number.isFinite(Number(window.screenY)) ? Math.round(Number(window.screenY)) : 0);
        const width = Math.round(Number(candidate.width));
        const height = Math.round(Number(candidate.height));
        if (![x, y, width, height].every(Number.isFinite) || width <= 1 || height <= 1) {
            return null;
        }
        if (I.isNativeReturnBallViewportSize(width, height)) {
            return null;
        }
        return { x, y, width, height };
    }

    I.setPendingNativeModelViewportRestoreBounds = function setPendingNativeModelViewportRestoreBounds(bounds) {
        pendingNativeModelViewportRestoreBounds = normalizeModelViewportBounds(bounds);
        return pendingNativeModelViewportRestoreBounds;
    }

    I.isNativeReturnBallViewportSize = function isNativeReturnBallViewportSize(width, height) {
        const w = Math.round(Number(width));
        const h = Math.round(Number(height));
        if (!Number.isFinite(w) || !Number.isFinite(h)) return false;
        return Math.abs(w - NEKO_NATIVE_RETURN_BALL_SHRINK_VIEWPORT_SIZE) <= 2
            && Math.abs(h - NEKO_NATIVE_RETURN_BALL_SHRINK_VIEWPORT_SIZE) <= 2;
    }

    I.isModelViewportRestored = function isModelViewportRestored(bounds) {
        const target = normalizeModelViewportBounds(bounds);
        if (!target) return true;
        const tolerance = 2;
        return Math.abs((window.innerWidth || 0) - target.width) <= tolerance &&
            Math.abs((window.innerHeight || 0) - target.height) <= tolerance;
    }

    function waitForModelViewportRestore(bounds, options = {}) {
        const target = normalizeModelViewportBounds(bounds);
        if (!target || I.isModelViewportRestored(target)) {
            return I.waitForAnimationFrames(2).then(() => ({ restored: true, skipped: !target }));
        }

        const timeoutMs = Number.isFinite(options.timeoutMs)
            ? Math.max(0, Number(options.timeoutMs))
            : NEKO_MODEL_VIEWPORT_RESTORE_FALLBACK_MS;
        const deadline = Date.now() + timeoutMs;

        return new Promise((resolve) => {
            let timerId = null;
            let finished = false;
            const finish = (restored, timedOut) => {
                if (finished) return;
                finished = true;
                if (timerId) {
                    clearTimeout(timerId);
                    timerId = null;
                }
                window.removeEventListener('resize', check);
                I.waitForAnimationFrames(2).then(() => resolve({
                    restored: !!restored,
                    timedOut: !!timedOut
                }));
            };
            const check = () => {
                if (I.isModelViewportRestored(target)) {
                    finish(true, false);
                    return;
                }
                if (Date.now() >= deadline) {
                    finish(false, true);
                    return;
                }
                timerId = setTimeout(check, 16);
            };
            window.addEventListener('resize', check);
            timerId = setTimeout(check, 16);
        });
    }

    function recoverLive2DRendererFromReturnBallViewport(reason) {
        try {
            if (!window.live2dManager ||
                typeof window.live2dManager.recoverRendererFromReturnBallViewport !== 'function') {
                return false;
            }
            const recovered = window.live2dManager.recoverRendererFromReturnBallViewport(reason);
            return !!recovered;
        } catch (error) {
            console.warn('[showCurrentModel] recover Live2D renderer from return-ball viewport failed:', error);
            return false;
        }
    }

    I.getPendingModelViewportRestoreBounds = function getPendingModelViewportRestoreBounds() {
        const pending = normalizeModelViewportBounds(pendingNativeModelViewportRestoreBounds);
        if (pending) return pending;
        if (I.multiWindowReturnBallDragState) {
            const width = Math.round(Number(I.multiWindowReturnBallDragState.savedWindowW));
            const height = Math.round(Number(I.multiWindowReturnBallDragState.savedWindowH));
            if (Number.isFinite(width) && Number.isFinite(height) && width > 1 && height > 1) {
                return {
                    x: Number.isFinite(Number(window.screenX)) ? Math.round(Number(window.screenX)) : 0,
                    y: Number.isFinite(Number(window.screenY)) ? Math.round(Number(window.screenY)) : 0,
                    width,
                    height
                };
            }
        }
        return null;
    }

    I.ensureModelViewportReadyBeforeShowCurrentModel = async function ensureModelViewportReadyBeforeShowCurrentModel() {
        const restoreBounds = I.getPendingModelViewportRestoreBounds();
        if (!restoreBounds) {
            if (I.isNativeReturnBallViewportSize(window.innerWidth, window.innerHeight)) {
                return {
                    ready: false,
                    restored: false,
                    missingRestoreBounds: true,
                    returnBallViewport: true
                };
            }
            recoverLive2DRendererFromReturnBallViewport('ensure-model-viewport-ready:no-restore-bounds');
            return { ready: true, skipped: true };
        }
        if (I.isModelViewportRestored(restoreBounds)) {
            pendingNativeModelViewportRestoreBounds = null;
            recoverLive2DRendererFromReturnBallViewport('ensure-model-viewport-ready:already-restored');
            return { ready: true, restored: true };
        }

        if (window.nekoPetDrag && typeof window.nekoPetDrag.reveal === 'function') {
            try {
                const revealResult = await Promise.resolve(window.nekoPetDrag.reveal());
                if (revealResult === false) {
                    await waitForModelViewportRestore(restoreBounds, {
                        timeoutMs: NEKO_MODEL_VIEWPORT_RESTORE_RETRY_MS
                    });
                }
            } catch (error) {
                console.warn('[showCurrentModel] restore model viewport reveal retry failed:', error);
            }
        }

        const viewportWait = await waitForModelViewportRestore(restoreBounds);
        if (viewportWait.restored) {
            pendingNativeModelViewportRestoreBounds = null;
            recoverLive2DRendererFromReturnBallViewport('ensure-model-viewport-ready:after-wait');
            return { ready: true, restored: true, viewportWait };
        }

        console.warn('[showCurrentModel] blocked model display because Pet viewport is still return-ball sized:', {
            target: restoreBounds,
            current: {
                width: window.innerWidth,
                height: window.innerHeight
            },
            returnBallViewport: I.isNativeReturnBallViewportSize(window.innerWidth, window.innerHeight)
        });
        return { ready: false, restored: false, viewportWait, restoreBounds };
    }

    // --- showCurrentModel ---
    I.showCurrentModel = async function showCurrentModel() {
        // 检查"请她离开"状态
        if (window.live2dManager && window.live2dManager._goodbyeClicked) {
            console.log('[showCurrentModel] 当前处于"请她离开"状态，跳过显示逻辑');
            return;
        }
        if (window.vrmManager && window.vrmManager._goodbyeClicked) {
            console.log('[showCurrentModel] 当前处于"请她离开"状态（VRM），跳过显示逻辑');
            return;
        }
        if (window.mmdManager && window.mmdManager._goodbyeClicked) {
            console.log('[showCurrentModel] 当前处于"请她离开"状态（MMD），跳过显示逻辑');
            return;
        }

        const modelViewportReady = await I.ensureModelViewportReadyBeforeShowCurrentModel();
        if (!modelViewportReady.ready) {
            return false;
        }

        // 重置 goodbye 标志
        if (window.live2dManager) {
            window.live2dManager._goodbyeClicked = false;
        }
        if (window.vrmManager) {
            window.vrmManager._goodbyeClicked = false;
        }
        if (window.mmdManager) {
            window.mmdManager._goodbyeClicked = false;
        }

        try {
            // 运行时检测当前已加载且可见的模型，用于 API 失败时的回退
            // 需同时检查模型引用和容器可见性（goodbye 流程中模型引用存在但容器已隐藏）
            const _vrmEl = document.getElementById('vrm-container');
            const _mmdEl = document.getElementById('mmd-container');
            const isVrmCurrentlyActive = window.vrmManager && window.vrmManager.currentModel
                && _vrmEl && _vrmEl.style.display !== 'none' && !_vrmEl.classList.contains('hidden');
            const isMmdCurrentlyActive = window.mmdManager && window.mmdManager.currentModel
                && _mmdEl && _mmdEl.style.display !== 'none' && !_mmdEl.classList.contains('hidden');

            const charResponse = await fetch('/api/characters');
            if (!charResponse.ok) {
                console.warn('[showCurrentModel] 无法获取角色配置');
                // 如果当前已有 VRM/MMD 模型在运行，保持当前状态而非回退到 Live2D
                if (isVrmCurrentlyActive || isMmdCurrentlyActive) {
                    console.log('[showCurrentModel] 保持当前已加载的模型');
                    return;
                }
                I.showLive2d();
                return;
            }

            const charactersData = await charResponse.json();
            const currentCatgirl = lanlan_config.lanlan_name;
            const catgirlConfig = charactersData['猫娘']?.[currentCatgirl];

            if (!catgirlConfig) {
                console.warn('[showCurrentModel] 未找到角色配置');
                if (isVrmCurrentlyActive || isMmdCurrentlyActive) {
                    console.log('[showCurrentModel] 保持当前已加载的模型');
                    return;
                }
                I.showLive2d();
                return;
            }

            const modelType = catgirlConfig.model_type || (catgirlConfig.vrm ? 'vrm' : 'live2d');

            // 解析 live3d 子类型
            // 优先使用 live3d_sub_type（后端权威来源），与 vrm-init.js / live2d-init.js 保持一致
            // 旧逻辑仅通过 mmd/vrm 路径字段猜测，当两个字段同时存在时会误判
            let effectiveModelType = modelType;
            if (modelType === 'live3d') {
                const subType = (
                    window.lanlan_config?.live3d_sub_type
                    || catgirlConfig._reserved?.avatar?.live3d_sub_type
                    || catgirlConfig.live3d_sub_type
                    || ''
                ).toString().trim().toLowerCase();

                if (subType === 'vrm') {
                    effectiveModelType = 'vrm';
                } else if (subType === 'mmd') {
                    effectiveModelType = 'mmd';
                } else {
                    // sub_type 缺失时回退到路径探测
                    const _sanitize = (v) => {
                        if (v === undefined || v === null) return '';
                        const s = String(v).trim();
                        const lower = s.toLowerCase();
                        if (!s || lower === 'undefined' || lower === 'null') return '';
                        return s;
                    };
                    const mmdPath = _sanitize(catgirlConfig.mmd)
                        || _sanitize(catgirlConfig._reserved?.avatar?.mmd?.model_path)
                        || '';
                    const vrmPath = _sanitize(catgirlConfig.vrm)
                        || _sanitize(catgirlConfig._reserved?.avatar?.vrm?.model_path)
                        || '';
                    if (mmdPath && !vrmPath) {
                        effectiveModelType = 'mmd';
                    } else if (vrmPath) {
                        effectiveModelType = 'vrm';
                    }
                }
            }
            console.log('[showCurrentModel] 当前角色模型类型:', modelType, '有效类型:', effectiveModelType);

            if (effectiveModelType === 'vrm') {
                console.log('[showCurrentModel] 开始显示VRM模型');

                const vrmContainer = document.getElementById('vrm-container');
                console.log('[showCurrentModel] vrmContainer存在:', !!vrmContainer);
                if (vrmContainer) {
                    // 取消延迟隐藏定时器
                    if (window._goodbyeHideTimerId) {
                        clearTimeout(window._goodbyeHideTimerId);
                        window._goodbyeHideTimerId = null;
                    }
                    // 取消上一次 VRM canvas 渐入动画
                    if (window._vrmCanvasFadeInId) {
                        clearTimeout(window._vrmCanvasFadeInId);
                        window._vrmCanvasFadeInId = null;
                    }
                    if (window._vrmCanvasFadeInListener) {
                        const prevCanvas = document.getElementById('vrm-canvas');
                        if (prevCanvas) {
                            prevCanvas.removeEventListener('transitionend', window._vrmCanvasFadeInListener);
                        }
                        window._vrmCanvasFadeInListener = null;
                    }

                    const isVrmAlreadyVisible =
                        !vrmContainer.classList.contains('minimized') &&
                        !vrmContainer.classList.contains('hidden') &&
                        vrmContainer.style.display !== 'none' &&
                        getComputedStyle(vrmContainer).display !== 'none';
                    const modelReturnEnterRect = !isVrmAlreadyVisible ? I.consumeModelReturnEnterRect() : null;

                    const vrmCanvasInner = document.getElementById('vrm-canvas');
                    if (!isVrmAlreadyVisible) {
                        if (vrmCanvasInner) {
                            vrmCanvasInner.style.transition = 'none';
                            vrmCanvasInner.style.opacity = '0';
                        }
                    }

                    I.prepareModelReturnContainer(vrmContainer, modelReturnEnterRect, { clearPointerEvents: true });

                    void vrmContainer.offsetWidth;
                    vrmContainer.style.transition = '';
                    if (modelReturnEnterRect) {
                        I.playModelReturnEnter(vrmContainer, modelReturnEnterRect);
                    }

                    if (vrmCanvasInner) {
                        vrmCanvasInner.style.setProperty('visibility', 'visible', 'important');
                        vrmCanvasInner.style.setProperty('pointer-events', 'auto', 'important');

                        if (!isVrmAlreadyVisible) {
                            void vrmCanvasInner.offsetWidth;

                            vrmCanvasInner.style.transition = I.NEKO_MODEL_RETURN_CANVAS_FADE_TRANSITION;
                            vrmCanvasInner.style.opacity = '1';

                            const cleanupFadeIn = () => {
                                vrmCanvasInner.removeEventListener('transitionend', window._vrmCanvasFadeInListener);
                                window._vrmCanvasFadeInListener = null;
                                if (window._vrmCanvasFadeInId) {
                                    clearTimeout(window._vrmCanvasFadeInId);
                                    window._vrmCanvasFadeInId = null;
                                }
                                vrmCanvasInner.style.transition = '';
                                vrmCanvasInner.style.opacity = '';
                            };
                            window._vrmCanvasFadeInListener = (e) => {
                                if (e.propertyName === 'opacity') cleanupFadeIn();
                            };
                            vrmCanvasInner.addEventListener('transitionend', window._vrmCanvasFadeInListener);
                            window._vrmCanvasFadeInId = setTimeout(cleanupFadeIn, I.NEKO_MODEL_RETURN_CANVAS_FADE_CLEANUP_MS);
                        }
                    }
                    console.log('[showCurrentModel] 已设置vrmContainer可见', isVrmAlreadyVisible ? '（跳过淡入动画）' : '（带canvas渐入动画）');
                }

                // 恢复 VRM canvas 的可见性
                const vrmCanvas = document.getElementById('vrm-canvas');
                console.log('[showCurrentModel] vrmCanvas存在:', !!vrmCanvas);
                if (vrmCanvas) {
                    vrmCanvas.style.setProperty('visibility', 'visible', 'important');
                    vrmCanvas.style.setProperty('pointer-events', 'auto', 'important');
                    console.log('[showCurrentModel] 已设置vrmCanvas可见');
                }

                // 确保Live2D隐藏
                const live2dContainer = document.getElementById('live2d-container');
                if (live2dContainer) {
                    live2dContainer.style.display = 'none';
                    live2dContainer.classList.add('hidden');
                }

                // 检查VRM浮动按钮是否存在
                let vrmFloatingButtons = document.getElementById('vrm-floating-buttons');
                console.log('[showCurrentModel] VRM浮动按钮存在:', !!vrmFloatingButtons, 'vrmManager存在:', !!window.vrmManager);

                if (!vrmFloatingButtons && window.vrmManager && typeof window.vrmManager.setupFloatingButtons === 'function') {
                    console.log('[showCurrentModel] VRM浮动按钮不存在，重新创建');
                    window.vrmManager.setupFloatingButtons();
                    vrmFloatingButtons = document.getElementById('vrm-floating-buttons');
                    console.log('[showCurrentModel] 创建后VRM浮动按钮存在:', !!vrmFloatingButtons);
                }

                if (vrmFloatingButtons) {
                    if (isMobileViewport()) {
                        vrmFloatingButtons.style.removeProperty('display');
                        vrmFloatingButtons.style.removeProperty('visibility');
                        vrmFloatingButtons.style.removeProperty('opacity');
                    } else {
                        vrmFloatingButtons.style.display = 'none';
                        vrmFloatingButtons.style.visibility = 'hidden';
                        vrmFloatingButtons.style.opacity = '0';
                    }
                }

                const vrmLockIcon = document.getElementById('vrm-lock-icon');
                if (vrmLockIcon) {
                    if (isMobileViewport()) {
                        vrmLockIcon.style.removeProperty('display');
                        vrmLockIcon.style.removeProperty('visibility');
                        vrmLockIcon.style.removeProperty('opacity');
                    } else {
                        vrmLockIcon.style.display = 'none';
                        vrmLockIcon.style.visibility = 'hidden';
                        vrmLockIcon.style.opacity = '0';
                    }
                }

                if (window.vrmManager && window.vrmManager.core && typeof window.vrmManager.core.setLocked === 'function') {
                    window.vrmManager.core.setLocked(false);
                }

                // 隐藏Live2D浮动按钮和锁图标
                const live2dFloatingButtons = document.getElementById('live2d-floating-buttons');
                if (live2dFloatingButtons && !window.isInTutorial) {
                    live2dFloatingButtons.style.display = 'none';
                }
                const live2dLockIcon = document.getElementById('live2d-lock-icon');
                if (live2dLockIcon) {
                    live2dLockIcon.style.display = 'none';
                }

                // 隐藏原生按钮和status栏
                const sidebar = document.getElementById('sidebar');
                const sidebarbox = document.getElementById('sidebarbox');
                if (sidebar) {
                    sidebar.style.setProperty('display', 'none', 'important');
                    sidebar.style.setProperty('visibility', 'hidden', 'important');
                    sidebar.style.setProperty('opacity', '0', 'important');
                }
                if (sidebarbox) {
                    sidebarbox.style.setProperty('display', 'none', 'important');
                    sidebarbox.style.setProperty('visibility', 'hidden', 'important');
                    sidebarbox.style.setProperty('opacity', '0', 'important');
                }
                const sideButtons = document.querySelectorAll('.side-btn');
                sideButtons.forEach(btn => {
                    btn.style.setProperty('display', 'none', 'important');
                    btn.style.setProperty('visibility', 'hidden', 'important');
                    btn.style.setProperty('opacity', '0', 'important');
                });
                const statusElement = document.getElementById('status');
                if (statusElement) {
                    statusElement.style.setProperty('display', 'none', 'important');
                    statusElement.style.setProperty('visibility', 'hidden', 'important');
                    statusElement.style.setProperty('opacity', '0', 'important');
                }

                // 隐藏 MMD 容器和按钮
                const mmdContainerVrm = document.getElementById('mmd-container');
                if (mmdContainerVrm) { mmdContainerVrm.style.display = 'none'; mmdContainerVrm.classList.add('hidden'); }
                const mmdCanvasVrm = document.getElementById('mmd-canvas');
                if (mmdCanvasVrm) { mmdCanvasVrm.style.visibility = 'hidden'; mmdCanvasVrm.style.pointerEvents = 'none'; }
                const mmdFloatingButtonsVrm = document.getElementById('mmd-floating-buttons');
                if (mmdFloatingButtonsVrm) { mmdFloatingButtonsVrm.style.display = 'none'; }
                const mmdLockIconVrm = document.getElementById('mmd-lock-icon');
                if (mmdLockIconVrm) { mmdLockIconVrm.style.display = 'none'; }

            } else if (effectiveModelType === 'mmd') {
                // ═══════════════ 显示 MMD 模型 ═══════════════
                console.log('[showCurrentModel] 开始显示MMD模型');

                // 显示 MMD 容器
                const mmdContainer = document.getElementById('mmd-container');
                const modelReturnEnterRect = mmdContainer ? I.consumeModelReturnEnterRect() : null;
                if (mmdContainer) {
                    I.prepareModelReturnContainer(mmdContainer, modelReturnEnterRect, { clearPointerEvents: true });
                    mmdContainer.style.transition = '';
                    if (modelReturnEnterRect) {
                        I.playModelReturnEnter(mmdContainer, modelReturnEnterRect);
                    }
                }
                const mmdCanvas = document.getElementById('mmd-canvas');
                if (mmdCanvas) {
                    mmdCanvas.style.setProperty('visibility', 'visible', 'important');
                    mmdCanvas.style.setProperty('pointer-events', 'auto', 'important');
                    // 渐入动画
                    mmdCanvas.style.transition = 'none';
                    mmdCanvas.style.opacity = '0';
                    void mmdCanvas.offsetWidth;
                    mmdCanvas.style.transition = I.NEKO_MODEL_RETURN_CANVAS_FADE_TRANSITION;
                    mmdCanvas.style.opacity = '1';
                    if (window._mmdCanvasFadeInId) clearTimeout(window._mmdCanvasFadeInId);
                    window._mmdCanvasFadeInId = setTimeout(() => {
                        if (mmdCanvas) {
                            mmdCanvas.style.transition = '';
                            mmdCanvas.style.opacity = '';
                        }
                        window._mmdCanvasFadeInId = null;
                    }, I.NEKO_MODEL_RETURN_CANVAS_FADE_CLEANUP_MS);
                }

                // 隐藏 VRM
                const vrmContainerMmd = document.getElementById('vrm-container');
                if (vrmContainerMmd) { vrmContainerMmd.style.display = 'none'; vrmContainerMmd.classList.add('hidden'); }
                const vrmCanvasMmd = document.getElementById('vrm-canvas');
                if (vrmCanvasMmd) { vrmCanvasMmd.style.visibility = 'hidden'; vrmCanvasMmd.style.pointerEvents = 'none'; }

                // 隐藏 Live2D
                const live2dContainerMmd = document.getElementById('live2d-container');
                if (live2dContainerMmd) { live2dContainerMmd.style.display = 'none'; live2dContainerMmd.classList.add('hidden'); }

                // 显示 MMD 浮动按钮
                let mmdFloatingButtons = document.getElementById('mmd-floating-buttons');
                if (!mmdFloatingButtons && window.mmdManager && typeof window.mmdManager.setupFloatingButtons === 'function') {
                    window.mmdManager.setupFloatingButtons();
                    mmdFloatingButtons = document.getElementById('mmd-floating-buttons');
                }
                if (mmdFloatingButtons) {
                    const isMmdMobile = typeof window.isMobileWidth === 'function'
                        ? window.isMobileWidth()
                        : (window.innerWidth <= 768);
                    if (isMmdMobile) {
                        mmdFloatingButtons.style.removeProperty('display');
                        mmdFloatingButtons.style.removeProperty('visibility');
                        mmdFloatingButtons.style.removeProperty('opacity');
                    } else {
                        mmdFloatingButtons.style.display = 'none';
                        mmdFloatingButtons.style.visibility = 'hidden';
                        mmdFloatingButtons.style.opacity = '0';
                    }
                }

                // 隐藏 VRM / Live2D 浮动按钮
                const vrmFloatingButtonsMmd = document.getElementById('vrm-floating-buttons');
                if (vrmFloatingButtonsMmd) { vrmFloatingButtonsMmd.style.display = 'none'; }
                const live2dFloatingButtonsMmd = document.getElementById('live2d-floating-buttons');
                if (live2dFloatingButtonsMmd) { live2dFloatingButtonsMmd.style.display = 'none'; }
                const vrmLockIconMmd = document.getElementById('vrm-lock-icon');
                if (vrmLockIconMmd) { vrmLockIconMmd.style.display = 'none'; }
                const live2dLockIconMmd = document.getElementById('live2d-lock-icon');
                if (live2dLockIconMmd) { live2dLockIconMmd.style.display = 'none'; }

                // 隐藏原生按钮和status栏
                const sidebarMmd = document.getElementById('sidebar');
                const sidebarboxMmd = document.getElementById('sidebarbox');
                if (sidebarMmd) {
                    sidebarMmd.style.setProperty('display', 'none', 'important');
                    sidebarMmd.style.setProperty('visibility', 'hidden', 'important');
                    sidebarMmd.style.setProperty('opacity', '0', 'important');
                }
                if (sidebarboxMmd) {
                    sidebarboxMmd.style.setProperty('display', 'none', 'important');
                    sidebarboxMmd.style.setProperty('visibility', 'hidden', 'important');
                    sidebarboxMmd.style.setProperty('opacity', '0', 'important');
                }
                document.querySelectorAll('.side-btn').forEach(btn => {
                    btn.style.setProperty('display', 'none', 'important');
                    btn.style.setProperty('visibility', 'hidden', 'important');
                    btn.style.setProperty('opacity', '0', 'important');
                });
                const statusElementMmd = document.getElementById('status');
                if (statusElementMmd) {
                    statusElementMmd.style.setProperty('display', 'none', 'important');
                    statusElementMmd.style.setProperty('visibility', 'hidden', 'important');
                    statusElementMmd.style.setProperty('opacity', '0', 'important');
                }

            } else if (effectiveModelType === 'pngtuber') {
                const pngtuberContainer = document.getElementById('pngtuber-container');
                const basePngtuberConfig = catgirlConfig.pngtuber || catgirlConfig._reserved?.avatar?.pngtuber || window.lanlan_config?.pngtuber || {};
                const pngtuberConfig = I.pendingPngtuberReturnConfig
                    ? Object.assign({}, basePngtuberConfig, I.pendingPngtuberReturnConfig)
                    : basePngtuberConfig;
                I.pendingPngtuberReturnConfig = null;

                if (window.loadPNGTuberAvatar) {
                    await window.loadPNGTuberAvatar(pngtuberConfig);
                }
                if (window.pngtuberManager && typeof window.pngtuberManager.show === 'function') {
                    window.pngtuberManager.show();
                } else if (pngtuberContainer) {
                    pngtuberContainer.classList.remove('hidden');
                    pngtuberContainer.style.removeProperty('display');
                    pngtuberContainer.style.display = 'block';
                    pngtuberContainer.style.visibility = 'visible';
                }

                const modelReturnEnterRect = pngtuberContainer ? I.consumeModelReturnEnterRect() : null;
                if (pngtuberContainer) {
                    I.prepareModelReturnContainer(pngtuberContainer, modelReturnEnterRect, { clearPointerEvents: true });
                    if (modelReturnEnterRect) {
                        I.playModelReturnEnter(pngtuberContainer, modelReturnEnterRect);
                    }
                    pngtuberContainer.style.setProperty('pointer-events', 'none', 'important');
                    pngtuberContainer.querySelectorAll('.pngtuber-image').forEach((pngtuberImage) => {
                        pngtuberImage.style.removeProperty('transition');
                        pngtuberImage.style.removeProperty('opacity');
                        pngtuberImage.style.setProperty('visibility', 'visible', 'important');
                        pngtuberImage.style.setProperty('pointer-events', 'auto', 'important');
                    });
                }

                const live2dContainerPngtuber = document.getElementById('live2d-container');
                if (live2dContainerPngtuber) { live2dContainerPngtuber.style.display = 'none'; live2dContainerPngtuber.classList.add('hidden'); }
                const vrmContainerPngtuber = document.getElementById('vrm-container');
                if (vrmContainerPngtuber) { vrmContainerPngtuber.style.display = 'none'; vrmContainerPngtuber.classList.add('hidden'); }
                const vrmCanvasPngtuber = document.getElementById('vrm-canvas');
                if (vrmCanvasPngtuber) { vrmCanvasPngtuber.style.visibility = 'hidden'; vrmCanvasPngtuber.style.pointerEvents = 'none'; }
                const mmdContainerPngtuber = document.getElementById('mmd-container');
                if (mmdContainerPngtuber) { mmdContainerPngtuber.style.display = 'none'; mmdContainerPngtuber.classList.add('hidden'); }
                const mmdCanvasPngtuber = document.getElementById('mmd-canvas');
                if (mmdCanvasPngtuber) { mmdCanvasPngtuber.style.visibility = 'hidden'; mmdCanvasPngtuber.style.pointerEvents = 'none'; }

                ['live2d', 'vrm', 'mmd'].forEach(prefix => {
                    const floatingButtons = document.getElementById(`${prefix}-floating-buttons`);
                    if (floatingButtons) floatingButtons.style.display = 'none';
                    const lockIcon = document.getElementById(`${prefix}-lock-icon`);
                    if (lockIcon) lockIcon.style.display = 'none';
                });

                if (window.pngtuberManager && typeof window.pngtuberManager.setupFloatingButtons === 'function') {
                    window.pngtuberManager.setupFloatingButtons();
                }
            } else {
                // 显示 Live2D 模型
                I.showLive2d();

                // 确保VRM隐藏
                const vrmContainer = document.getElementById('vrm-container');
                if (vrmContainer) {
                    vrmContainer.style.display = 'none';
                    vrmContainer.classList.add('hidden');
                }
                const vrmCanvas = document.getElementById('vrm-canvas');
                if (vrmCanvas) {
                    vrmCanvas.style.visibility = 'hidden';
                    vrmCanvas.style.pointerEvents = 'none';
                }

                // 隐藏VRM浮动按钮和锁图标
                const vrmFloatingButtons = document.getElementById('vrm-floating-buttons');
                if (vrmFloatingButtons) {
                    vrmFloatingButtons.style.display = 'none';
                }
                const vrmLockIcon = document.getElementById('vrm-lock-icon');
                if (vrmLockIcon) {
                    vrmLockIcon.style.display = 'none';
                }

                // 隐藏MMD容器和按钮
                const mmdContainerL2d = document.getElementById('mmd-container');
                if (mmdContainerL2d) { mmdContainerL2d.style.display = 'none'; mmdContainerL2d.classList.add('hidden'); }
                const mmdCanvasL2d = document.getElementById('mmd-canvas');
                if (mmdCanvasL2d) { mmdCanvasL2d.style.visibility = 'hidden'; mmdCanvasL2d.style.pointerEvents = 'none'; }
                const mmdFloatingButtonsL2d = document.getElementById('mmd-floating-buttons');
                if (mmdFloatingButtonsL2d) { mmdFloatingButtonsL2d.style.display = 'none'; }
                const mmdLockIconL2d = document.getElementById('mmd-lock-icon');
                if (mmdLockIconL2d) { mmdLockIconL2d.style.display = 'none'; }
            }
        } catch (error) {
            console.error('[showCurrentModel] 失败:', error);
            // 出错时检查是否有 VRM/MMD 正在运行且可见，如果有则保持当前状态
            const vrmEl = document.getElementById('vrm-container');
            const mmdEl = document.getElementById('mmd-container');
            const isVrmRunning = window.vrmManager && window.vrmManager.currentModel
                && vrmEl && vrmEl.style.display !== 'none' && !vrmEl.classList.contains('hidden');
            const isMmdRunning = window.mmdManager && window.mmdManager.currentModel
                && mmdEl && mmdEl.style.display !== 'none' && !mmdEl.classList.contains('hidden');
            if (isVrmRunning || isMmdRunning) {
                console.log('[showCurrentModel] 保持当前已加载的模型');
                return;
            }
            I.showLive2d();
        }
    }

    I.mod.showCurrentModel = I.showCurrentModel;

    // ================================================================
    //  4. Floating button sync, goodbye/return, event listeners
    //     (app.js lines 6078-6785)
    // ================================================================

    Object.assign(window.appUi, I.mod || {});
})();
