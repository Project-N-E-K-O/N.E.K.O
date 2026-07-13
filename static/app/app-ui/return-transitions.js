/**
 * app-ui/return-transitions.js
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
    I.MULTI_WINDOW_RETURN_BALL_DRAG_SHRINK_SIZE = 160;
    I.RETURN_BALL_DRAG_RECOVERY_POLL_MS = 250;
    I.RETURN_BALL_DRAG_STALE_RECOVERY_MS = 12000;
    I.MULTI_WINDOW_RETURN_BALL_DRAG_SHRINK_FALLBACK_MS = 220;
    I.MULTI_WINDOW_RETURN_BALL_DRAG_RESTORE_FALLBACK_MS = 600;
    I.MULTI_WINDOW_RETURN_BALL_REVEAL_FALLBACK_MS = 600;
    I.multiWindowReturnBallDragState = null;
    let idleReturnBallDesktopDragStateFrame = 0;
    let idleReturnBallDesktopDragStatePending = null;
    I.pendingPngtuberReturnConfig = null;

    function resolveModelReturnEnter(reason) {
        const resolve = window._nekoModelReturnEnterResolve;
        window._nekoModelReturnEnterResolve = null;
        window._nekoModelReturnEnterPromise = null;
        window._nekoModelReturnEnterContainer = null;
        if (typeof resolve === 'function') {
            try {
                resolve({ reason });
            } catch (_) {}
        }
    }

    function startModelReturnEnterWait(container) {
        resolveModelReturnEnter('replaced');
        let resolveWait = null;
        const promise = new Promise(resolve => {
            resolveWait = resolve;
        });
        window._nekoModelReturnEnterContainer = container || null;
        window._nekoModelReturnEnterResolve = resolveWait;
        window._nekoModelReturnEnterPromise = promise;
        return promise;
    }

    async function waitForModelReturnEnterToSettle() {
        const promise = window._nekoModelReturnEnterPromise;
        if (promise && typeof promise.then === 'function') {
            let timeoutId = null;
            await Promise.race([
                promise,
                new Promise(resolve => {
                    timeoutId = setTimeout(resolve, I.NEKO_MODEL_RETURN_ENTER_CLEANUP_MS + I.NEKO_MODEL_RETURN_ENTER_SETTLE_BUFFER_MS);
                })
            ]);
            if (timeoutId) {
                clearTimeout(timeoutId);
            }
        }
        await I.waitForAnimationFrames(2);
    }

    I.waitForAnimationFrames = function waitForAnimationFrames(count) {
        const remaining = Math.max(1, Number(count) || 1);
        return new Promise(resolve => {
            const step = (left) => {
                if (left <= 0) {
                    resolve();
                    return;
                }
                requestAnimationFrame(() => step(left - 1));
            };
            step(remaining);
        });
    }

    function isModelContainerVisible(containerId) {
        const el = document.getElementById(containerId);
        if (!el || el.classList.contains('hidden')) return false;
        const style = window.getComputedStyle ? getComputedStyle(el) : el.style;
        return style.display !== 'none' && style.visibility !== 'hidden';
    }

    function clampPngtuberOffset(value) {
        return Math.max(-5000, Math.min(5000, Number(value) || 0));
    }

    function getPngtuberManager() {
        return window.pngtuberManager || null;
    }

    function syncPngtuberReturnConfig(config) {
        if (!config) return null;
        const manager = getPngtuberManager();
        if (manager && manager.config) {
            manager.config = Object.assign({}, manager.config, config);
            if (typeof manager.applyTransform === 'function') {
                manager.applyTransform();
            }
            if (typeof manager.syncGlobalConfig === 'function') {
                manager.syncGlobalConfig();
            } else if (window.lanlan_config && typeof window.lanlan_config === 'object') {
                window.lanlan_config.pngtuber = Object.assign({}, manager.config);
            }
            if (typeof manager.updateFloatingButtonsPosition === 'function') {
                manager.updateFloatingButtonsPosition();
            }
            if (typeof manager.updateLockIconPosition === 'function') {
                manager.updateLockIconPosition();
            }
            return Object.assign({}, manager.config);
        }
        if (window.lanlan_config && typeof window.lanlan_config === 'object') {
            window.lanlan_config.pngtuber = Object.assign({}, window.lanlan_config.pngtuber || {}, config);
            return Object.assign({}, window.lanlan_config.pngtuber);
        }
        return Object.assign({}, config);
    }

    I.applyPngtuberScreenDelta = function applyPngtuberScreenDelta(screenDx, screenDy) {
        const manager = getPngtuberManager();
        const baseConfig = Object.assign(
            {},
            window.lanlan_config && window.lanlan_config.pngtuber ? window.lanlan_config.pngtuber : {},
            manager && manager.config ? manager.config : {}
        );
        baseConfig.offset_x = clampPngtuberOffset((Number(baseConfig.offset_x) || 0) + screenDx);
        baseConfig.offset_y = clampPngtuberOffset((Number(baseConfig.offset_y) || 0) + screenDy);
        return syncPngtuberReturnConfig(baseConfig);
    }

    function getPngtuberScreenRect() {
        const manager = getPngtuberManager();
        const candidates = [
            manager && manager.image,
            manager && manager.canvasElement,
            manager && manager.imageElement,
            document.querySelector('#pngtuber-container .pngtuber-image'),
            document.getElementById('pngtuber-container')
        ];
        for (const candidate of candidates) {
            if (!candidate || typeof candidate.getBoundingClientRect !== 'function') continue;
            try {
                const rect = normalizeNekoScreenRect(candidate.getBoundingClientRect());
                if (rect) return rect;
            } catch (_) {}
        }
        return null;
    }

    function getPngtuberSnapDelta(rect) {
        const normalized = normalizeNekoScreenRect(rect);
        if (!normalized) return null;
        const viewportWidth = Math.max(1, window.innerWidth || document.documentElement.clientWidth || 1);
        const viewportHeight = Math.max(1, window.innerHeight || document.documentElement.clientHeight || 1);
        const margin = 12;

        let dx = 0;
        let dy = 0;
        if (normalized.width <= viewportWidth - margin * 2) {
            if (normalized.left < margin) dx = margin - normalized.left;
            if (normalized.right + dx > viewportWidth - margin) dx = viewportWidth - margin - normalized.right;
        } else {
            dx = viewportWidth / 2 - (normalized.left + normalized.width / 2);
        }
        if (normalized.height <= viewportHeight - margin * 2) {
            if (normalized.top < margin) dy = margin - normalized.top;
            if (normalized.bottom + dy > viewportHeight - margin) dy = viewportHeight - margin - normalized.bottom;
        } else {
            dy = viewportHeight / 2 - (normalized.top + normalized.height / 2);
        }

        if (Math.abs(dx) < 1) dx = 0;
        if (Math.abs(dy) < 1) dy = 0;
        return { dx, dy };
    }

    async function snapPngtuberIntoScreen() {
        const manager = getPngtuberManager();
        if (!manager || !manager.config || !isModelContainerVisible('pngtuber-container')) {
            return false;
        }
        const delta = getPngtuberSnapDelta(getPngtuberScreenRect());
        if (!delta || (!delta.dx && !delta.dy)) return false;
        I.applyPngtuberScreenDelta(delta.dx, delta.dy);
        await I.waitForAnimationFrames(1);
        return true;
    }

    async function saveReturnModelPosition(modelType) {
        try {
            if (modelType === 'mmd') {
                const interaction = window.mmdManager && window.mmdManager.interaction;
                if (interaction && typeof interaction._savePositionAfterInteraction === 'function') {
                    await interaction._savePositionAfterInteraction();
                }
                return;
            }
            if (modelType === 'vrm') {
                const interaction = window.vrmManager && window.vrmManager.interaction;
                if (interaction && typeof interaction._savePositionAfterInteraction === 'function') {
                    await interaction._savePositionAfterInteraction();
                }
                return;
            }
            if (modelType === 'pngtuber') {
                const manager = getPngtuberManager();
                if (manager && typeof manager.saveCurrentConfig === 'function') {
                    await manager.saveCurrentConfig();
                }
                return;
            }
            if (window.live2dManager && typeof window.live2dManager._savePositionAfterInteraction === 'function') {
                await window.live2dManager._savePositionAfterInteraction();
            }
        } catch (error) {
            console.warn('[App] 保存回来后的模型位置失败:', error);
        }
    }

    I.settleReturnedModelBounds = async function settleReturnedModelBounds(shouldSaveWhenUnchanged) {
        // showCurrentModel 会恢复容器和 canvas；等布局提交后再读边界，避免拿到隐藏态尺寸。
        await waitForModelReturnEnterToSettle();
        await I.waitForAnimationFrames(2);

        let activeModelType = null;
        try {
            if (window.mmdManager && window.mmdManager.currentModel && isModelContainerVisible('mmd-container')) {
                activeModelType = 'mmd';
                const interaction = window.mmdManager.interaction;
                if (interaction && typeof interaction._snapModelIntoScreen === 'function') {
                    const snapped = await interaction._snapModelIntoScreen({ animate: true });
                    if (!snapped && shouldSaveWhenUnchanged) {
                        await saveReturnModelPosition('mmd');
                    }
                } else if (shouldSaveWhenUnchanged) {
                    await saveReturnModelPosition('mmd');
                }
                return;
            }

            if (window.vrmManager && window.vrmManager.currentModel && isModelContainerVisible('vrm-container')) {
                activeModelType = 'vrm';
                const interaction = window.vrmManager.interaction;
                if (interaction && typeof interaction._snapModelIntoScreen === 'function') {
                    const snapped = await interaction._snapModelIntoScreen({ animate: true });
                    if (snapped) {
                        // VRM 的回弹方法只负责动画，最终位置需要由外层保存。
                        await saveReturnModelPosition('vrm');
                    } else if (shouldSaveWhenUnchanged) {
                        await saveReturnModelPosition('vrm');
                    }
                } else if (shouldSaveWhenUnchanged) {
                    await saveReturnModelPosition('vrm');
                }
                return;
            }

            if ((window.lanlan_config?.model_type || '').toLowerCase() === 'pngtuber'
                && getPngtuberManager()
                && isModelContainerVisible('pngtuber-container')) {
                activeModelType = 'pngtuber';
                const snapped = await snapPngtuberIntoScreen();
                if (snapped || shouldSaveWhenUnchanged) {
                    await saveReturnModelPosition('pngtuber');
                }
                return;
            }

            if (window.live2dManager) {
                activeModelType = 'live2d';
                const liveModel = typeof window.live2dManager.getCurrentModel === 'function'
                    ? window.live2dManager.getCurrentModel() : null;
                if (liveModel && !liveModel.destroyed && typeof window.live2dManager._checkAndPerformSnap === 'function') {
                    const snapped = await window.live2dManager._checkAndPerformSnap(liveModel, { allowWhenNotReady: true });
                    if (!snapped && shouldSaveWhenUnchanged) {
                        await saveReturnModelPosition('live2d');
                    }
                } else if (shouldSaveWhenUnchanged) {
                    await saveReturnModelPosition('live2d');
                }
            }
        } catch (error) {
            console.warn('[App] 回来后的边界回弹计算失败:', error);
            if (shouldSaveWhenUnchanged && activeModelType) {
                await saveReturnModelPosition(activeModelType);
            }
        }
    }

    function cancelReturnBallReveal(container) {
        if (!container) return;
        const revealFrameId = container.__nekoReturnBallRevealFrame;
        if (typeof revealFrameId === 'number') {
            cancelAnimationFrame(revealFrameId);
        }
        container.__nekoReturnBallRevealFrame = null;
    }

    I.restoreSavedReturnBallStyle = function restoreSavedReturnBallStyle(container, state) {
        if (!container) return false;
        const dragState = state ||
            (I.multiWindowReturnBallDragState && I.multiWindowReturnBallDragState.container === container
                ? I.multiWindowReturnBallDragState
                : null);
        const savedStyle = dragState && dragState.savedBallStyle;
        if (!savedStyle) return false;

        container.style.left = savedStyle.left;
        container.style.top = savedStyle.top;
        container.style.right = savedStyle.right;
        container.style.bottom = savedStyle.bottom;
        container.style.transform = savedStyle.transform;
        container.style.opacity = savedStyle.opacity;
        container.style.visibility = savedStyle.visibility;
        container.style.transition = savedStyle.transition;
        container.style.willChange = savedStyle.willChange;
        container.style.removeProperty('--neko-ball-drag-size');
        dragState.savedBallStyle = null;
        return true;
    }

    function buildNekoModelCatTransitionAssetUrl(playbackToken = '') {
        const params = [];
        if (I.NEKO_MODEL_CAT_TRANSITION_VERSION) {
            params.push(`v=${encodeURIComponent(I.NEKO_MODEL_CAT_TRANSITION_VERSION)}`);
        }
        if (playbackToken) {
            params.push(`play=${encodeURIComponent(String(playbackToken))}`);
        }
        if (!params.length) {
            return I.NEKO_MODEL_CAT_TRANSITION_ASSET;
        }
        const separator = I.NEKO_MODEL_CAT_TRANSITION_ASSET.includes('?') ? '&' : '?';
        return `${I.NEKO_MODEL_CAT_TRANSITION_ASSET}${separator}${params.join('&')}`;
    }

    function buildNekoModelCatRevealPlaybackUrl(src, playbackToken) {
        if (!src) return '';
        try {
            const url = new URL(src, window.location.href);
            if (!/\/static\/assets\/neko-idle\/.+\.gif$/i.test(url.pathname)) {
                return src;
            }
            url.searchParams.set('reveal', String(playbackToken || Date.now()));
            return url.href;
        } catch (_) {
            return src;
        }
    }

    I.restartNekoModelCatRevealArt = function restartNekoModelCatRevealArt(container) {
        const art = container && typeof container.querySelector === 'function'
            ? container.querySelector('.neko-idle-return-art:not(.neko-idle-return-art-next)')
            : null;
        if (!art) return false;
        const currentSrc = art.getAttribute('src') || art.currentSrc || '';
        if (!currentSrc) return false;
        const nextSrc = buildNekoModelCatRevealPlaybackUrl(
            currentSrc,
            ++I.nekoModelCatRevealPlaybackToken
        );
        art.__nekoIdleGifPlaybackToken = (art.__nekoIdleGifPlaybackToken || 0) + 1;
        art.__nekoIdleHoverToken = (art.__nekoIdleHoverToken || 0) + 1;
        art.removeAttribute('src');
        void art.offsetWidth;
        art.src = nextSrc || currentSrc;
        return true;
    }

    function normalizeNekoScreenRect(rect) {
        if (!rect) return null;
        const left = Number(rect.left);
        const top = Number(rect.top);
        const width = Number(rect.width);
        const height = Number(rect.height);
        const right = Number.isFinite(Number(rect.right)) ? Number(rect.right) : left + width;
        const bottom = Number.isFinite(Number(rect.bottom)) ? Number(rect.bottom) : top + height;
        const normalizedWidth = Number.isFinite(width) && width > 0 ? width : right - left;
        const normalizedHeight = Number.isFinite(height) && height > 0 ? height : bottom - top;
        if (![left, top, normalizedWidth, normalizedHeight].every(Number.isFinite)) return null;
        if (normalizedWidth <= 1 || normalizedHeight <= 1) return null;
        return {
            left: left,
            top: top,
            right: left + normalizedWidth,
            bottom: top + normalizedHeight,
            width: normalizedWidth,
            height: normalizedHeight
        };
    }

    function getModelRectFromManager(manager) {
        if (!manager) return null;
        if (typeof manager.getModelScreenBounds === 'function') {
            try {
                const rect = normalizeNekoScreenRect(manager.getModelScreenBounds());
                if (rect) return rect;
            } catch (_) {}
        }

        if (manager.image && typeof manager.image.getBoundingClientRect === 'function') {
            try {
                const rect = normalizeNekoScreenRect(manager.image.getBoundingClientRect());
                if (rect) return rect;
            } catch (_) {}
        }

        const model = typeof manager.getCurrentModel === 'function'
            ? manager.getCurrentModel()
            : manager.currentModel;
        if (model && typeof model.getBounds === 'function') {
            try {
                const rect = normalizeNekoScreenRect(model.getBounds());
                if (rect) return rect;
            } catch (_) {}
        }
        return null;
    }

    function isModelContainerActive(containerId) {
        const container = document.getElementById(containerId);
        if (!container) return false;
        return container.style.display !== 'none' && !container.classList.contains('hidden');
    }

    I.getActiveModelTransitionRect = function getActiveModelTransitionRect() {
        const isPngtuberModelActive = (window.lanlan_config?.model_type || '').toLowerCase() === 'pngtuber'
            && isModelContainerActive('pngtuber-container');
        const candidates = [
            { active: isPngtuberModelActive, manager: window.pngtuberManager },
            { active: isModelContainerActive('mmd-container'), manager: window.mmdManager },
            { active: isModelContainerActive('vrm-container'), manager: window.vrmManager },
            { active: true, manager: window.live2dManager }
        ];

        for (const candidate of candidates) {
            if (!candidate.active) continue;
            const rect = getModelRectFromManager(candidate.manager);
            if (rect) return rect;
        }
        return null;
    }

    function setModelExitTransformOrigin(container, rect) {
        if (!container || !rect) return;
        const normalizedRect = normalizeNekoScreenRect(rect);
        if (!normalizedRect) return;
        const originX = normalizedRect.left + normalizedRect.width / 2;
        const originY = normalizedRect.top + normalizedRect.height / 2;
        container.style.setProperty('--neko-model-exit-origin-x', `${Math.round(originX)}px`);
        container.style.setProperty('--neko-model-exit-origin-y', `${Math.round(originY)}px`);
    }

    function getNekoTransitionNowMs() {
        try {
            return window.performance && typeof window.performance.now === 'function'
                ? window.performance.now()
                : Date.now();
        } catch (_) {
            return Date.now();
        }
    }

    function getModelCatTransitionScaleTransform() {
        return `scale(${I.NEKO_MODEL_CAT_TRANSITION_MODEL_SCALE}) translateZ(0)`;
    }

    function applyNekoTransitionMask(element) {
        if (!element || !element.style) return;
        Object.assign(element.style, {
            maskImage: I.NEKO_MODEL_CAT_TRANSITION_EDGE_MASK,
            maskRepeat: 'no-repeat',
            maskPosition: 'center',
            maskSize: '100% 100%',
        });
        element.style.webkitMaskImage = I.NEKO_MODEL_CAT_TRANSITION_EDGE_MASK;
        element.style.webkitMaskRepeat = 'no-repeat';
        element.style.webkitMaskPosition = 'center';
        element.style.webkitMaskSize = '100% 100%';
        element.style.setProperty('-webkit-mask-image', I.NEKO_MODEL_CAT_TRANSITION_EDGE_MASK);
        element.style.setProperty('-webkit-mask-repeat', 'no-repeat');
        element.style.setProperty('-webkit-mask-position', 'center');
        element.style.setProperty('-webkit-mask-size', '100% 100%');
    }

    I.prepareModelReturnContainer = function prepareModelReturnContainer(container, rect, options = {}) {
        if (!container) return false;
        const hasReturnRect = !!rect;
        container.style.transition = 'none';
        if (options.removeHidden !== false) {
            container.classList.remove('hidden');
        }
        container.classList.remove('minimized');
        container.removeAttribute('data-neko-model-goodbye-exiting');
        container.style.visibility = 'visible';
        container.style.display = options.display || 'block';
        container.style.opacity = hasReturnRect ? '0' : '1';
        container.style.transform = hasReturnRect ? getModelCatTransitionScaleTransform() : 'none';
        if (options.clearPointerEvents) {
            if (!I.keepAvatarRootContainerPassthrough(container)) {
                container.style.removeProperty('pointer-events');
            }
        }
        return true;
    }

    function applyModelGoodbyeVisualFade(container, options = {}) {
        const visualLayer = container && typeof container.querySelector === 'function'
            ? container.querySelector('canvas')
            : null;
        if (!visualLayer) return false;
        visualLayer.style.transition = I.NEKO_MODEL_GOODBYE_VISUAL_FADE_TRANSITION;
        if (options.restart !== false) {
            visualLayer.style.opacity = '1';
            void visualLayer.offsetWidth;
        }
        visualLayer.style.opacity = '0';
        return true;
    }

    I.playModelGoodbyeExit = function playModelGoodbyeExit(container, rect) {
        if (!container) return;
        if (container.getAttribute('data-neko-model-goodbye-exiting') === 'true') {
            applyModelGoodbyeVisualFade(container, { restart: false });
            return;
        }
        setModelExitTransformOrigin(container, rect);
        container.setAttribute('data-neko-model-goodbye-exiting', 'true');
        container.classList.remove('minimized');
        container.style.removeProperty('visibility');
        container.style.removeProperty('display');
        container.style.removeProperty('transition');
        container.style.removeProperty('opacity');
        container.style.removeProperty('transform');
        void container.offsetWidth;
        container.classList.add('minimized');
        applyModelGoodbyeVisualFade(container, { restart: true });
    }

    I.consumeModelReturnEnterRect = function consumeModelReturnEnterRect() {
        const rect = normalizeNekoScreenRect(window._nekoModelReturnEnterRect);
        window._nekoModelReturnEnterRect = null;
        return rect;
    }

    I.playModelReturnEnter = function playModelReturnEnter(container, rect) {
        if (!container || !rect) return false;
        setModelExitTransformOrigin(container, rect);
        if (window._nekoModelReturnEnterTimer) {
            clearTimeout(window._nekoModelReturnEnterTimer);
            window._nekoModelReturnEnterTimer = null;
            resolveModelReturnEnter('timer-cleared');
        }
        startModelReturnEnterWait(container);

        container.style.transition = 'none';
        container.style.opacity = '0';
        container.style.transform = getModelCatTransitionScaleTransform();
        void container.offsetWidth;

        requestAnimationFrame(() => {
            if (!container || !container.isConnected) {
                resolveModelReturnEnter('disconnected-before-raf');
                return;
            }
            container.style.transition = I.NEKO_MODEL_RETURN_ENTER_TRANSITION;
            container.style.opacity = '1';
            container.style.transform = 'scale(1) translateZ(0)';
            window._nekoModelReturnEnterTimer = setTimeout(() => {
                if (container && container.isConnected) {
                    container.style.removeProperty('transition');
                    container.style.removeProperty('opacity');
                    container.style.removeProperty('transform');
                }
                window._nekoModelReturnEnterTimer = null;
                resolveModelReturnEnter('cleanup');
            }, I.NEKO_MODEL_RETURN_ENTER_CLEANUP_MS);
        });
        return true;
    }

    function mergeNekoTransitionAnchorRect(anchorRect, coverRect) {
        const anchor = normalizeNekoScreenRect(anchorRect);
        const cover = normalizeNekoScreenRect(coverRect);
        if (!anchor || !cover) return anchor || cover || null;
        return {
            left: anchor.left + anchor.width / 2 - cover.width / 2,
            top: anchor.top + anchor.height / 2 - cover.height / 2,
            right: anchor.left + anchor.width / 2 + cover.width / 2,
            bottom: anchor.top + anchor.height / 2 + cover.height / 2,
            width: cover.width,
            height: cover.height
        };
    }

    function normalizeNekoTransitionRect(anchorRect, container, coverRect) {
        let rect = anchorRect || null;
        if (!rect && container && typeof container.getBoundingClientRect === 'function') {
            try {
                rect = container.getBoundingClientRect();
            } catch (_) {
                rect = null;
            }
        }
        rect = mergeNekoTransitionAnchorRect(rect, coverRect);

        const width = Math.max(1, Number(rect && rect.width) || 0);
        const height = Math.max(1, Number(rect && rect.height) || 0);
        const centerX = Number.isFinite(Number(rect && rect.left))
            ? Number(rect.left) + width / 2
            : window.innerWidth - 80;
        const centerY = Number.isFinite(Number(rect && rect.top))
            ? Number(rect.top) + height / 2
            : window.innerHeight - 160;
        const basis = Math.max(width, height, I.NEKO_MODEL_CAT_TRANSITION_MIN_SIZE);
        const size = Math.max(
            I.NEKO_MODEL_CAT_TRANSITION_MIN_SIZE,
            Math.min(I.NEKO_MODEL_CAT_TRANSITION_MAX_SIZE, Math.round(basis * I.NEKO_MODEL_CAT_TRANSITION_SIZE_FACTOR))
        );

        return {
            left: Math.round(centerX - size / 2),
            top: Math.round(centerY - size / 2),
            width: size,
            height: size,
        };
    }

    function clearNekoModelCatTransitionOverlay(keepToken = '') {
        document.querySelectorAll('#neko-model-cat-transition').forEach((existing) => {
            if (keepToken && existing.getAttribute('data-neko-model-cat-transition-token') === String(keepToken)) {
                return;
            }
            if (existing.parentNode) {
                existing.parentNode.removeChild(existing);
            }
        });
    }

    function getNekoModelCatOverlayVisibleMs() {
        return Math.max(0, I.NEKO_MODEL_CAT_TRANSITION_DURATION_MS - I.NEKO_MODEL_CAT_TRANSITION_LOOP_GUARD_MS);
    }

    function getNekoModelCatSettleMs(direction) {
        return direction === 'cat-to-model'
            ? Math.max(I.NEKO_MODEL_CAT_TRANSITION_DURATION_MS, I.NEKO_MODEL_CAT_TO_MODEL_LOCK_MS)
            : I.NEKO_MODEL_CAT_TRANSITION_DURATION_MS;
    }

    function createNekoModelCatTransitionOverlay(rect, direction, token) {
        const overlay = document.createElement('div');
        overlay.id = 'neko-model-cat-transition';
        overlay.setAttribute('data-neko-model-cat-transition-direction', direction);
        overlay.setAttribute('data-neko-model-cat-transition-token', String(token || ''));
        Object.assign(overlay.style, {
            position: 'fixed',
            left: `${rect.left}px`,
            top: `${rect.top}px`,
            width: `${rect.width}px`,
            height: `${rect.height}px`,
            zIndex: '100080',
            pointerEvents: 'none',
            overflow: 'hidden',
            borderRadius: '50%',
            opacity: '1',
            transform: 'translateZ(0)',
            willChange: 'opacity, transform',
        });
        applyNekoTransitionMask(overlay);

        const image = document.createElement('img');
        image.alt = '';
        image.draggable = false;
        Object.assign(image.style, {
            width: '100%',
            height: '100%',
            display: 'block',
            objectFit: 'contain',
            objectPosition: 'center',
            pointerEvents: 'none',
            userSelect: 'none',
        });
        applyNekoTransitionMask(image);
        overlay.appendChild(image);
        return { overlay, image };
    }

    I.isNekoModelCatTransitionActive = function isNekoModelCatTransitionActive(direction = '') {
        if (!I.nekoModelCatTransitionActive) return false;
        if (!direction) return true;
        return I.nekoModelCatTransitionActive.direction === direction;
    }

    I.reserveNekoModelCatTransition = function reserveNekoModelCatTransition(direction) {
        if (I.nekoModelCatTransitionActive) return null;
        const token = ++I.nekoModelCatTransitionToken;
        I.nekoModelCatTransitionActive = {
            token,
            direction: direction || 'model-to-cat',
            reserved: true,
            promise: null
        };
        return token;
    }

    I.releaseNekoModelCatTransition = function releaseNekoModelCatTransition(token) {
        if (I.nekoModelCatTransitionActive && I.nekoModelCatTransitionActive.token === token) {
            I.nekoModelCatTransitionActive = null;
        }
    }

    function isNekoModelCatTransitionTokenCurrent(token) {
        return !!(
            I.nekoModelCatTransitionActive &&
            I.nekoModelCatTransitionActive.token === token
        );
    }

    function shouldBlockCatToModelTransitionForModelViewport(direction) {
        if (direction !== 'cat-to-model') return false;
        const restoreBounds = I.getPendingModelViewportRestoreBounds();
        if (restoreBounds && !I.isModelViewportRestored(restoreBounds)) {
            return true;
        }
        const blockRawShrink = I.isNativeReturnBallViewportSize(window.innerWidth, window.innerHeight);
        return blockRawShrink;
    }

    I.playNekoModelCatTransition = function playNekoModelCatTransition(options = {}) {
        const container = options.container || null;
        const anchorRect = options.anchorRect || null;
        const coverRect = options.coverRect || null;
        const direction = options.direction || 'model-to-cat';
        const transitionToken = options.transitionToken || null;
        const onBeforeOverlayCleanup = typeof options.onBeforeOverlayCleanup === 'function'
            ? options.onBeforeOverlayCleanup
            : null;
        const beforeOverlayCleanupMs = Number.isFinite(Number(options.beforeOverlayCleanupMs))
            ? Math.max(0, Number(options.beforeOverlayCleanupMs))
            : I.NEKO_MODEL_CAT_REVEAL_BEFORE_SMOKE_HIDE_MS;
        let token = transitionToken;
        if (shouldBlockCatToModelTransitionForModelViewport(direction)) {
            return Promise.resolve({
                blocked: true,
                direction,
                reason: 'model-viewport-not-restored'
            });
        }
        if (I.nekoModelCatTransitionActive) {
            const ownsActiveTransition = transitionToken &&
                I.nekoModelCatTransitionActive.token === transitionToken &&
                I.nekoModelCatTransitionActive.direction === direction;
            if (!ownsActiveTransition) {
                return Promise.resolve({
                    blocked: true,
                    direction: I.nekoModelCatTransitionActive.direction
                });
            }
        } else {
            token = I.reserveNekoModelCatTransition(direction);
        }
        if (!token) {
            return Promise.resolve({
                blocked: true,
                direction: I.nekoModelCatTransitionActive ? I.nekoModelCatTransitionActive.direction : direction
            });
        }
        const rect = normalizeNekoTransitionRect(anchorRect, container, coverRect);
        const src = buildNekoModelCatTransitionAssetUrl(token);

        if (container) {
            container.setAttribute('data-neko-model-cat-transitioning', direction);
            container.style.pointerEvents = 'none';
            if (direction === 'cat-to-model') {
                container.style.opacity = '0';
                container.style.visibility = 'hidden';
            }
        }

        clearNekoModelCatTransitionOverlay(token);
        const { overlay, image } = createNekoModelCatTransitionOverlay(rect, direction, token);
        let playbackStartedAt = getNekoTransitionNowMs();
        let overlayCleanupTimer = null;
        let beforeOverlayCleanupTimer = null;
        let finishTimer = null;
        let imageLoadFallbackTimer = null;
        let didImageLoad = false;
        let didStartPlayback = false;
        let didSchedulePlayback = false;
        let didCallBeforeOverlayCleanup = false;
        let didCleanupOverlay = false;
        let didFinish = false;
        const isCurrentTransition = () => isNekoModelCatTransitionTokenCurrent(token);
        const runBeforeOverlayCleanup = () => {
            if (!isCurrentTransition()) return;
            if (didFinish || didCallBeforeOverlayCleanup || !onBeforeOverlayCleanup) return;
            didCallBeforeOverlayCleanup = true;
            try {
                onBeforeOverlayCleanup();
            } catch (error) {
                console.warn('[App] model/cat transition before-overlay-cleanup callback failed:', error);
            }
        };
        const cleanupOverlay = () => {
            if (!isCurrentTransition()) return;
            if (didCleanupOverlay) return;
            didCleanupOverlay = true;
            if (overlay.parentNode) {
                overlay.parentNode.removeChild(overlay);
            }
        };
        const finishTransition = (resolve) => {
            if (didFinish) return;
            didFinish = true;
            if (imageLoadFallbackTimer) {
                clearTimeout(imageLoadFallbackTimer);
                imageLoadFallbackTimer = null;
            }
            if (isCurrentTransition()) {
                cleanupOverlay();
                if (container && container.isConnected) {
                    container.removeAttribute('data-neko-model-cat-transitioning');
                    if (direction === 'cat-to-model') {
                        container.style.removeProperty('visibility');
                    }
                }
                I.releaseNekoModelCatTransition(token);
            }
            resolve({ completed: true, direction });
        };
        const scheduleTransitionTimers = (resolve) => {
            if (didFinish) return;
            if (didSchedulePlayback) return;
            didSchedulePlayback = true;
            if (imageLoadFallbackTimer) {
                clearTimeout(imageLoadFallbackTimer);
                imageLoadFallbackTimer = null;
            }
            if (overlayCleanupTimer) clearTimeout(overlayCleanupTimer);
            if (beforeOverlayCleanupTimer) clearTimeout(beforeOverlayCleanupTimer);
            if (finishTimer) clearTimeout(finishTimer);
            const elapsedMs = Math.max(0, getNekoTransitionNowMs() - playbackStartedAt);
            const visibleDurationMs = getNekoModelCatOverlayVisibleMs();
            const settleDurationMs = getNekoModelCatSettleMs(direction);
            const overlayRemainingMs = Math.max(0, visibleDurationMs - elapsedMs);
            const finishRemainingMs = Math.max(0, settleDurationMs - elapsedMs);
            if (onBeforeOverlayCleanup && didImageLoad && !didCallBeforeOverlayCleanup) {
                const revealRemainingMs = Math.max(0, overlayRemainingMs - beforeOverlayCleanupMs);
                beforeOverlayCleanupTimer = setTimeout(runBeforeOverlayCleanup, revealRemainingMs);
            }
            overlayCleanupTimer = setTimeout(cleanupOverlay, overlayRemainingMs);
            finishTimer = setTimeout(() => {
                finishTransition(resolve);
            }, finishRemainingMs);
        };
        const transitionPromise = new Promise((resolve) => {
            const ensureOverlayVisible = () => {
                if (!overlay.parentNode) {
                    document.body.appendChild(overlay);
                }
            };
            const startVisibleSmokePlayback = () => {
                if (didFinish || didStartPlayback) return;
                didStartPlayback = true;
                if (!isCurrentTransition()) return;
                image.src = src;
                playbackStartedAt = getNekoTransitionNowMs();
                ensureOverlayVisible();
            };
            const startTransitionPlayback = () => {
                if (didFinish || didSchedulePlayback) return;
                if (imageLoadFallbackTimer) {
                    clearTimeout(imageLoadFallbackTimer);
                    imageLoadFallbackTimer = null;
                }
                if (!isCurrentTransition()) {
                    finishTransition(resolve);
                    return;
                }
                startVisibleSmokePlayback();
                scheduleTransitionTimers(resolve);
            };
            const preloadImage = new Image();
            preloadImage.addEventListener('load', () => {
                didImageLoad = true;
                startTransitionPlayback();
            }, { once: true });
            preloadImage.addEventListener('error', () => {
                didImageLoad = true;
                startTransitionPlayback();
            }, { once: true });
            startVisibleSmokePlayback();
            preloadImage.src = src;
            imageLoadFallbackTimer = setTimeout(() => {
                didImageLoad = true;
                startTransitionPlayback();
            }, I.NEKO_MODEL_CAT_TRANSITION_LOAD_FALLBACK_MS);
        });
        if (I.nekoModelCatTransitionActive && I.nekoModelCatTransitionActive.token === token) {
            I.nekoModelCatTransitionActive.promise = transitionPromise;
            I.nekoModelCatTransitionActive.reserved = false;
        }
        return transitionPromise;
    }

    window.isNekoModelCatTransitionActive = I.isNekoModelCatTransitionActive;
    window.playNekoModelCatTransition = I.playNekoModelCatTransition;

    function getNekoGoodbyeIdleAppearance() {
        return I.normalizeNekoGoodbyeIdleAppearance(window.__nekoGoodbyeIdleAppearance || I.nekoGoodbyeIdleAppearance);
    }

    window.getNekoGoodbyeIdleAppearance = getNekoGoodbyeIdleAppearance;

    function getReturnButtonElement(container) {
        return container && typeof container.querySelector === 'function'
            ? container.querySelector('.neko-idle-return-btn')
            : null;
    }

    function getReturnButtonArtElement(container) {
        return container && typeof container.querySelector === 'function'
            ? container.querySelector('.neko-idle-return-art:not(.neko-idle-return-art-next)')
            : null;
    }

    I.getReturnButtonAppearance = function getReturnButtonAppearance(container) {
        const button = getReturnButtonElement(container);
        const raw = (container && container.getAttribute(I.NEKO_GOODBYE_IDLE_APPEARANCE_ATTR)) ||
            (button && button.getAttribute(I.NEKO_GOODBYE_IDLE_APPEARANCE_ATTR)) ||
            getNekoGoodbyeIdleAppearance();
        return I.normalizeNekoGoodbyeIdleAppearance(raw);
    }

    function getCurrentNekoIdleReturnTier() {
        try {
            if (window.nekoAutoGoodbye && typeof window.nekoAutoGoodbye.getState === 'function') {
                const state = window.nekoAutoGoodbye.getState();
                const tier = state && state.visualTier;
                return (tier === 'cat2' || tier === 'cat3' || tier === 'none') ? tier : 'cat1';
            }
        } catch (_) {}
        return 'cat1';
    }

    function normalizeRestorableNekoIdleReturnTier(tier) {
        return (tier === 'cat1' || tier === 'cat2' || tier === 'cat3') ? tier : '';
    }

    function getRestorableNekoIdleReturnTier(fallbackTier = '') {
        const currentTier = normalizeRestorableNekoIdleReturnTier(getCurrentNekoIdleReturnTier());
        return currentTier || normalizeRestorableNekoIdleReturnTier(fallbackTier) || 'cat1';
    }

    function clearReturnButtonBallOnlyVisualState(container) {
        const button = getReturnButtonElement(container);
        if (!button || !button.classList) return;
        [
            'is-tier-transitioning',
            'is-thought-bubble-active',
            'is-thought-bubble-sleeping',
            'is-thought-bubble-popping',
            'is-cat1-facing-right',
            'is-cat1-walking',
            'is-cat1-stretching',
            'is-cat1-playing',
            'is-cat1-eating',
            'is-cat1-hover-paused',
            'is-drag-action',
            'is-drag-action-pending'
        ].forEach((className) => button.classList.remove(className));
        if (typeof I.clearNekoIdleCat1EdgePeek === 'function') {
            I.clearNekoIdleCat1EdgePeek(container);
        }
    }

    function setGoodbyeIdleAppearanceAttributes(container, appearance) {
        const button = getReturnButtonElement(container);
        const nextAppearance = I.normalizeNekoGoodbyeIdleAppearance(appearance);
        if (container) {
            container.setAttribute(I.NEKO_GOODBYE_IDLE_APPEARANCE_ATTR, nextAppearance);
        }
        if (button) {
            button.setAttribute(I.NEKO_GOODBYE_IDLE_APPEARANCE_ATTR, nextAppearance);
        }
    }

    I.applyGoodbyeIdleAppearanceToReturnButton = function applyGoodbyeIdleAppearanceToReturnButton(container, appearance = getNekoGoodbyeIdleAppearance()) {
        if (!container) return;
        const nextAppearance = I.normalizeNekoGoodbyeIdleAppearance(appearance);
        const button = getReturnButtonElement(container);
        const art = getReturnButtonArtElement(container);
        setGoodbyeIdleAppearanceAttributes(container, nextAppearance);

        if (nextAppearance === I.NEKO_GOODBYE_IDLE_APPEARANCE_BALL) {
            clearReturnButtonBallOnlyVisualState(container);
            const previousTier = button
                ? normalizeRestorableNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier'))
                : '';
            if (button) {
                button.dataset.nekoGoodbyeIdleCatTier = getRestorableNekoIdleReturnTier(
                    previousTier || button.dataset.nekoGoodbyeIdleCatTier
                );
                button.setAttribute('data-neko-idle-tier', 'none');
            }
            container.setAttribute('data-neko-idle-tier', 'none');
            if (art) {
                // avatar-ui-buttons 的监听器先运行，会预存该 tier 规范待机图的快照
                // （DOM src 此刻可能是一次性 GIF，不可信）；这里只在 avatar 侧
                // 未预存时兜底快照当前 src。tier 标签用按钮当时的真实 tier：
                // 标签为空（tier none）的快照在恢复时不回写
                const currentSrc = art.getAttribute('src') || art.currentSrc || '';
                if (currentSrc && currentSrc.indexOf(I.NEKO_GOODBYE_IDLE_BALL_ASSET) === -1 && !art.dataset.nekoGoodbyeIdleCatSrc) {
                    art.dataset.nekoGoodbyeIdleCatSrc = currentSrc;
                    art.dataset.nekoGoodbyeIdleCatSrcTier = previousTier;
                }
                if ((art.getAttribute('src') || '') !== I.NEKO_GOODBYE_IDLE_BALL_ASSET) {
                    art.src = I.NEKO_GOODBYE_IDLE_BALL_ASSET;
                }
                art.setAttribute('data-neko-idle-tier', 'none');
                art.setAttribute('aria-hidden', 'true');
                if (art.__nekoIdleTransitionTimer) {
                    clearTimeout(art.__nekoIdleTransitionTimer);
                    art.__nekoIdleTransitionTimer = 0;
                }
                if (art.__nekoIdleHoverTimer) {
                    clearTimeout(art.__nekoIdleHoverTimer);
                    art.__nekoIdleHoverTimer = 0;
                }
                // 必须同时递增 token 并清掉 hover tier，否则挂起中的 gif 时长 promise
                // 仍能通过 token/tier 校验，把猫图写回来盖掉球图
                art.__nekoIdleHoverToken = (art.__nekoIdleHoverToken || 0) + 1;
                delete art.__nekoIdleHoverSrc;
                delete art.__nekoIdleHoverTier;
                delete art.__nekoIdleHoverStartedAt;
                delete art.__nekoIdleTransitionTo;
            }
            if (button && typeof button.querySelectorAll === 'function') {
                button.querySelectorAll('.neko-idle-return-art-next').forEach((nextArt) => nextArt.remove());
            }
            return;
        }

        const restoredTier = getRestorableNekoIdleReturnTier(button && button.dataset.nekoGoodbyeIdleCatTier);
        if (button) {
            button.setAttribute('data-neko-idle-tier', restoredTier);
            delete button.dataset.nekoGoodbyeIdleCatTier;
        }
        container.setAttribute('data-neko-idle-tier', restoredTier);
        if (art && art.dataset.nekoGoodbyeIdleCatSrc) {
            // 快照的 tier 标签与恢复 tier 一致才回写；球形态期间 tier 推进过或
            // 快照来源不明时，保留 avatar 侧监听器已按当前 tier 重画的图
            const savedSrcTier = normalizeRestorableNekoIdleReturnTier(art.dataset.nekoGoodbyeIdleCatSrcTier);
            if (savedSrcTier && savedSrcTier === restoredTier) {
                art.src = art.dataset.nekoGoodbyeIdleCatSrc;
            }
            delete art.dataset.nekoGoodbyeIdleCatSrc;
            delete art.dataset.nekoGoodbyeIdleCatSrcTier;
        }
        if (art) {
            art.setAttribute('data-neko-idle-tier', restoredTier);
            art.removeAttribute('aria-hidden');
        }
    }

    function syncGoodbyeIdleAppearanceForReturnButtons(reason = 'goodbye-idle-appearance') {
        const appearance = getNekoGoodbyeIdleAppearance();
        document.querySelectorAll('[id$="-return-button-container"]').forEach((container) => {
            I.applyGoodbyeIdleAppearanceToReturnButton(container, appearance);
        });
        I.scheduleIdleReturnBallDesktopBridge(reason);
    }

    function resetReturnBallTemporaryStyle(container) {
        if (!container) return;
        container.style.removeProperty('opacity');
        container.style.removeProperty('visibility');
        container.style.removeProperty('transition');
        container.style.removeProperty('will-change');
        container.style.removeProperty('--neko-ball-drag-size');
        container.setAttribute('data-dragging', 'false');
    }

    I.hideReturnBallContainer = function hideReturnBallContainer(container, reason = 'return-ball-hide') {
        if (!container) return;
        cancelReturnBallReveal(container);
        I.restoreSavedReturnBallStyle(container);
        resetReturnBallTemporaryStyle(container);
        container.removeAttribute('data-neko-return-visible');
        container.style.display = 'none';
        container.style.pointerEvents = 'none';
        container.style.removeProperty('visibility');
        I.scheduleIdleReturnBallDesktopBridge(reason || 'return-ball-hide', container);
    }

    window.hideNekoReturnBallContainer = I.hideReturnBallContainer;

    function positionReturnBallContainer(container, anchorRect) {
        if (!container) return;

        container.style.left = '';
        container.style.top = '';
        container.style.right = '';
        container.style.bottom = '';
        container.style.transform = 'none';

        if (anchorRect) {
            const containerWidth = Math.round(container.offsetWidth) || 64;
            const containerHeight = Math.round(container.offsetHeight) || 64;
            const maxLeft = Math.max(0, window.innerWidth - containerWidth);
            const maxTop = Math.max(0, window.innerHeight - containerHeight);
            const left = Math.round(anchorRect.left + (anchorRect.width - containerWidth) / 2);
            const top = Math.round(anchorRect.top + (anchorRect.height - containerHeight) / 2);
            container.style.left = `${Math.max(0, Math.min(left, maxLeft))}px`;
            container.style.top = `${Math.max(0, Math.min(top, maxTop))}px`;
            return;
        }

        container.style.right = '16px';
        container.style.bottom = '116px';
    }

    I.revealReturnBallContainer = function revealReturnBallContainer(container, reason = 'return-ball-revealed') {
        if (!container || container.style.display === 'none') return;
        container.__nekoReturnBallRevealFrame = null;
        container.removeAttribute('data-neko-model-cat-transitioning');
        container.style.visibility = 'visible';
        container.style.pointerEvents = 'auto';
        container.style.opacity = '1';
        container.style.transform = 'none';
        I.scheduleIdleReturnBallDesktopBridge(reason, container);
    }

    I.showReturnBallContainer = function showReturnBallContainer(container, anchorRect, options = {}) {
        if (!container) return null;

        cancelReturnBallReveal(container);
        I.restoreSavedReturnBallStyle(container);
        resetReturnBallTemporaryStyle(container);
        container.setAttribute('data-neko-return-visible', 'true');
        container.style.display = 'flex';
        container.style.visibility = 'hidden';
        container.style.pointerEvents = 'none';
        I.applyGoodbyeIdleAppearanceToReturnButton(container);
        positionReturnBallContainer(container, anchorRect);
        container.style.opacity = '0';
        container.style.transform = 'translate3d(0, 8px, 0) scale(0.94)';
        container.style.transition = 'opacity 325ms cubic-bezier(0.22, 1, 0.36, 1), transform 400ms cubic-bezier(0.22, 1, 0.36, 1)';
        container.style.willChange = 'opacity, transform';

        void container.offsetWidth;

        if (!options.deferReveal) {
            const revealFrameId = requestAnimationFrame(() => {
                if (container.__nekoReturnBallRevealFrame !== revealFrameId) return;
                I.revealReturnBallContainer(container, 'return-ball-revealed');
            });
            container.__nekoReturnBallRevealFrame = revealFrameId;
            I.scheduleIdleReturnBallDesktopBridge('return-ball-show', container);
        }
        return container;
    }

    I.getVisibleIdleReturnBallContainer = function getVisibleIdleReturnBallContainer() {
        return document.querySelector('[id$="-return-button-container"][data-neko-return-visible="true"]');
    }

    function getIdleReturnBallScreenRect(container) {
        if (!container || typeof container.getBoundingClientRect !== 'function') return null;
        const rect = container.getBoundingClientRect();
        if (!rect || rect.width <= 0 || rect.height <= 0) return null;
        const screenLeft = Number.isFinite(window.screenX) ? window.screenX : 0;
        const screenTop = Number.isFinite(window.screenY) ? window.screenY : 0;
        return {
            left: Math.round(screenLeft + rect.left),
            top: Math.round(screenTop + rect.top),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
            right: Math.round(screenLeft + rect.left + rect.width),
            bottom: Math.round(screenTop + rect.top + rect.height)
        };
    }

    I.isIdleCat1PlaygroundActiveForReturnBallDesktopBridge = function isIdleCat1PlaygroundActiveForReturnBallDesktopBridge() {
        const buttons = document.querySelectorAll('.neko-idle-return-btn');
        for (let i = 0; i < buttons.length; i += 1) {
            const state = buttons[i].__nekoIdleCat1PlaygroundDropState;
            if (state && state.active && !state.released) return true;
            if (buttons[i].__nekoIdleCat1PlaygroundPendingEntry) return true;
        }
        return false;
    }

    function canPostIdleReturnBallDesktopState() {
        const body = document.body;
        return !(body && body.classList && body.classList.contains('electron-chat-window')) &&
            !I.isIdleCat1PlaygroundActiveForReturnBallDesktopBridge();
    }

    function postIdleReturnBallDesktopState(reason, container, overrideScreenRect) {
        if (!canPostIdleReturnBallDesktopState()) return;
        const target = container || I.getVisibleIdleReturnBallContainer();
        const visible = !!(target &&
            target.getAttribute('data-neko-return-visible') === 'true' &&
            target.style.display !== 'none' &&
            target.style.visibility !== 'hidden' &&
            target.style.opacity !== '0');
        const tier = visible
            ? (target.getAttribute('data-neko-idle-tier') || 'none')
            : 'none';
        const appearance = visible
            ? I.getReturnButtonAppearance(target)
            : getNekoGoodbyeIdleAppearance();
        const screenRect = overrideScreenRect || (visible ? getIdleReturnBallScreenRect(target) : null);
        const payload = {
            action: 'idle_return_ball_state',
            source: 'pet-window',
            reason: reason || 'sync',
            visible: visible,
            tier: tier,
            appearance: appearance,
            screenRect: visible ? screenRect : null,
            lanlan_name: (window.lanlan_config && window.lanlan_config.lanlan_name) || '',
            timestamp: Date.now()
        };

        window.dispatchEvent(new CustomEvent('neko:idle-return-ball-state', { detail: payload }));

        const channel = window.appInterpage && window.appInterpage.nekoBroadcastChannel;
        if (channel && typeof channel.postMessage === 'function') {
            try {
                channel.postMessage(payload);
            } catch (_) {}
        }
    }

    I.scheduleIdleReturnBallDesktopBridge = function scheduleIdleReturnBallDesktopBridge(reason, container, overrideScreenRect) {
        if (!canPostIdleReturnBallDesktopState()) return;
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                postIdleReturnBallDesktopState(reason, container, overrideScreenRect);
            });
        });
    }

    function clearIdleReturnBallDesktopDragStateFrame() {
        if (idleReturnBallDesktopDragStateFrame) {
            cancelAnimationFrame(idleReturnBallDesktopDragStateFrame);
            idleReturnBallDesktopDragStateFrame = 0;
        }
        idleReturnBallDesktopDragStatePending = null;
    }

    I.scheduleIdleReturnBallDesktopDragState = function scheduleIdleReturnBallDesktopDragState(container, overrideScreenRect) {
        if (!canPostIdleReturnBallDesktopState()) return;
        idleReturnBallDesktopDragStatePending = {
            container: container,
            overrideScreenRect: overrideScreenRect
        };
        if (idleReturnBallDesktopDragStateFrame) return;
        idleReturnBallDesktopDragStateFrame = requestAnimationFrame(() => {
            idleReturnBallDesktopDragStateFrame = 0;
            const pending = idleReturnBallDesktopDragStatePending;
            idleReturnBallDesktopDragStatePending = null;
            if (!pending) return;
            postIdleReturnBallDesktopState(
                'return-ball-dragging',
                pending.container,
                pending.overrideScreenRect
            );
        });
    }

    I.getReturnBallDragScreenRect = function getReturnBallDragScreenRect(screenX, screenY, width, height) {
        const w = Math.max(1, Math.round(width || 64));
        const h = Math.max(1, Math.round(height || 64));
        const left = Math.round(screenX - w / 2);
        const top = Math.round(screenY - h / 2);
        return {
            left: left,
            top: top,
            width: w,
            height: h,
            right: left + w,
            bottom: top + h
        };
    }

    function syncIdleReturnBallDesktopStateFromManualMove(detail) {
        if (!detail || typeof detail !== 'object') return;
        const reason = typeof detail.reason === 'string' ? detail.reason : '';
        if (!reason || !reason.startsWith('return-ball-drag-')) return;
        const container = detail.container || I.getVisibleIdleReturnBallContainer();
        if (!container) return;
        if (reason === 'return-ball-drag-motion') {
            const sx = Number(detail.screenX);
            const sy = Number(detail.screenY);
            const width = container.offsetWidth || Number(detail.width) || 64;
            const height = container.offsetHeight || Number(detail.height) || 64;
            const screenRect = Number.isFinite(sx) && Number.isFinite(sy)
                ? I.getReturnBallDragScreenRect(sx, sy, width, height)
                : null;
            I.scheduleIdleReturnBallDesktopDragState(container, screenRect);
            I.scheduleIdleReturnBallDesktopBridge('return-ball-dragging', container);
            return;
        }
        I.scheduleIdleReturnBallDesktopBridge(reason, container);
    }

    window.addEventListener('neko:return-ball-manual-move', (event) => {
        syncIdleReturnBallDesktopStateFromManualMove(event && event.detail);
    });

    window.addEventListener('neko:auto-goodbye:state-change', (event) => {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
        if (!detail || detail.type !== 'visual-tier') return;
        if (getNekoGoodbyeIdleAppearance() === I.NEKO_GOODBYE_IDLE_APPEARANCE_BALL) {
            syncGoodbyeIdleAppearanceForReturnButtons('goodbye-idle-appearance-visual-tier');
            return;
        }
        I.scheduleIdleReturnBallDesktopBridge(
            detail.source === 'return-ball-drag-demotion' ? 'return-ball-drag-demotion' : 'visual-tier'
        );
    });
    window.addEventListener('neko:goodbye-idle-appearance', (event) => {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
        const mode = detail && typeof detail.mode === 'string' ? detail.mode : '';
        I.nekoGoodbyeIdleAppearance = I.normalizeNekoGoodbyeIdleAppearance(mode);
        window.__nekoGoodbyeIdleAppearance = I.nekoGoodbyeIdleAppearance;
        syncGoodbyeIdleAppearanceForReturnButtons(
            detail && detail.reason ? `goodbye-idle-appearance-${detail.reason}` : 'goodbye-idle-appearance'
        );
    });
    window.addEventListener('resize', () => {
        I.scheduleIdleReturnBallDesktopBridge('viewport-resize');
    });

    I.clearMultiWindowReturnBallDeferredWork = function clearMultiWindowReturnBallDeferredWork(state) {
        clearIdleReturnBallDesktopDragStateFrame();
        if (!state) return;
        if (state.viewportWaitFallbackTimer) {
            clearTimeout(state.viewportWaitFallbackTimer);
            state.viewportWaitFallbackTimer = null;
        }
        if (state.transitionCleanupTimer) {
            clearTimeout(state.transitionCleanupTimer);
            state.transitionCleanupTimer = null;
        }
        if (state.viewportWaitOnResize) {
            window.removeEventListener('resize', state.viewportWaitOnResize);
            state.viewportWaitOnResize = null;
        }
    }

    I.clearReturnBallDragRecoveryTimer = function clearReturnBallDragRecoveryTimer(state) {
        if (!state || !state.dragRecoveryTimer) return;
        clearTimeout(state.dragRecoveryTimer);
        state.dragRecoveryTimer = null;
    }

    I.getReturnBallDragScreenCoordinate = function getReturnBallDragScreenCoordinate(value, fallback) {
        return Number.isFinite(value) ? value : fallback;
    }

    const NEKO_IDLE_CAT1_EDGE_PEEK_TRIGGER_RATIO = 0.025;
    const NEKO_IDLE_CAT1_EDGE_PEEK_HIDDEN_RATIO = 0.4;
    const NEKO_IDLE_CAT1_EDGE_PEEK_CLASSES = [
        'is-cat1-edge-peek-left',
        'is-cat1-edge-peek-right',
        'is-cat1-edge-peek-top',
        'is-cat1-edge-peek-bottom',
        'is-cat1-edge-peek-top-left',
        'is-cat1-edge-peek-top-right',
        'is-cat1-edge-peek-bottom-left',
        'is-cat1-edge-peek-bottom-right'
    ];

    function clampNekoIdleCat1EdgePeekCoordinate(value, minValue, maxValue) {
        const normalized = Number(value);
        const min = Number(minValue);
        const max = Number(maxValue);
        if (!Number.isFinite(normalized)) return Number.isFinite(min) ? min : 0;
        if (!Number.isFinite(min) || !Number.isFinite(max) || max < min) return normalized;
        return Math.max(min, Math.min(normalized, max));
    }

    function getNekoIdleCat1EdgePeekButton(container) {
        return container && typeof container.querySelector === 'function'
            ? container.querySelector('.neko-idle-return-btn')
            : null;
    }

    I.clearNekoIdleCat1EdgePeek = function clearNekoIdleCat1EdgePeek(container) {
        const button = getNekoIdleCat1EdgePeekButton(container);
        if (!button) return;
        NEKO_IDLE_CAT1_EDGE_PEEK_CLASSES.forEach((className) => {
            button.classList.remove(className);
        });
        const art = button.querySelector('.neko-idle-return-art');
        if (art) {
            art.style.removeProperty('--neko-idle-return-edge-visual-shift-y');
        }
    }

    I.isNekoIdleCat1EdgePeekEligible = function isNekoIdleCat1EdgePeekEligible(container) {
        const button = getNekoIdleCat1EdgePeekButton(container);
        return (button && button.getAttribute('data-neko-idle-tier')) === 'cat1';
    }

    I.getNekoIdleCat1EdgePeekPlacement = function getNekoIdleCat1EdgePeekPlacement(left, top, width, height, viewportWidth, viewportHeight) {
        const w = Math.max(1, Number(width) || 0);
        const h = Math.max(1, Number(height) || 0);
        const viewportW = Math.max(w, Number(viewportWidth) || 0);
        const viewportH = Math.max(h, Number(viewportHeight) || 0);
        const currentLeft = Number(left);
        const currentTop = Number(top);
        if (!Number.isFinite(currentLeft) || !Number.isFinite(currentTop)) return null;

        const nearLeft = currentLeft <= w * NEKO_IDLE_CAT1_EDGE_PEEK_TRIGGER_RATIO;
        const nearRight = viewportW - (currentLeft + w) <= w * NEKO_IDLE_CAT1_EDGE_PEEK_TRIGGER_RATIO;
        const nearTop = currentTop <= h * NEKO_IDLE_CAT1_EDGE_PEEK_TRIGGER_RATIO;
        const nearBottom = viewportH - (currentTop + h) <= h * NEKO_IDLE_CAT1_EDGE_PEEK_TRIGGER_RATIO;
        if (!nearLeft && !nearRight && !nearTop && !nearBottom) return null;

        let edge = '';
        const centerX = currentLeft + w / 2;
        if (nearTop) {
            if (nearLeft || centerX <= w) edge = 'top-left';
            else if (nearRight || centerX >= viewportW - w) edge = 'top-right';
            else edge = 'top';
        } else if (nearBottom) {
            if (nearLeft || centerX <= w) edge = 'bottom-left';
            else if (nearRight || centerX >= viewportW - w) edge = 'bottom-right';
            else edge = 'bottom';
        } else if (nearLeft) {
            edge = 'left';
        } else if (nearRight) {
            edge = 'right';
        }

        const hiddenX = w * NEKO_IDLE_CAT1_EDGE_PEEK_HIDDEN_RATIO;
        const hiddenY = h * NEKO_IDLE_CAT1_EDGE_PEEK_HIDDEN_RATIO;
        const nextLeft = edge === 'left' || edge === 'top-left' || edge === 'bottom-left'
            ? -hiddenX
            : (edge === 'right' || edge === 'top-right' || edge === 'bottom-right'
                ? viewportW - w + hiddenX
                : clampNekoIdleCat1EdgePeekCoordinate(currentLeft, 0, viewportW - w));
        const nextTop = edge === 'top' || edge === 'top-left' || edge === 'top-right'
            ? -hiddenY
            : (edge === 'bottom' || edge === 'bottom-left' || edge === 'bottom-right'
                ? viewportH - h + hiddenY
                : clampNekoIdleCat1EdgePeekCoordinate(currentTop, 0, viewportH - h));

        return {
            edge,
            left: Math.round(nextLeft),
            top: Math.round(nextTop)
        };
    }

    I.applyNekoIdleCat1EdgePeek = function applyNekoIdleCat1EdgePeek(container, placement) {
        const button = getNekoIdleCat1EdgePeekButton(container);
        if (!container || !button || !placement || !placement.edge) return false;
        I.clearNekoIdleCat1EdgePeek(container);
        button.classList.add(`is-cat1-edge-peek-${placement.edge}`);
        const visualShiftY = Number(placement.visualShiftY);
        if (Number.isFinite(visualShiftY) && visualShiftY !== 0) {
            const art = button.querySelector('.neko-idle-return-art');
            if (art) {
                art.style.setProperty('--neko-idle-return-edge-visual-shift-y', `${Math.round(visualShiftY)}px`);
            }
        }
        container.style.left = `${placement.left}px`;
        container.style.top = `${placement.top}px`;
        container.style.right = '';
        container.style.bottom = '';
        container.style.transform = 'none';
        return true;
    }

    I._getNekoIdleCat1EdgePeekVisualShiftY = function _getNekoIdleCat1EdgePeekVisualShiftY(bounds, height) {
        const offsetY = Number(bounds && bounds.actualBoundsOffset && bounds.actualBoundsOffset.y);
        if (!Number.isFinite(offsetY) || offsetY <= 0) return 0;
        const maxShift = Math.max(0, Math.round(Number(height) || 0));
        return -Math.min(Math.round(offsetY), maxShift || Math.round(offsetY));
    }

    I.restoreNekoIdleCat1EdgePeekBeforeDrag = function restoreNekoIdleCat1EdgePeekBeforeDrag(container) {
        if (!container) return;
        I.clearNekoIdleCat1EdgePeek(container);
        if (!I.isNekoIdleCat1EdgePeekEligible(container)) return;
        const w = container.offsetWidth || 64;
        const h = container.offsetHeight || 64;
        const rect = container.getBoundingClientRect && container.getBoundingClientRect();
        const rawLeft = parseFloat(container.style.left);
        const rawTop = parseFloat(container.style.top);
        const currentLeft = Number.isFinite(rawLeft) ? rawLeft : (rect ? rect.left : 0);
        const currentTop = Number.isFinite(rawTop) ? rawTop : (rect ? rect.top : 0);
        container.style.left = `${Math.round(clampNekoIdleCat1EdgePeekCoordinate(currentLeft, 0, (window.innerWidth || w) - w))}px`;
        container.style.top = `${Math.round(clampNekoIdleCat1EdgePeekCoordinate(currentTop, 0, (window.innerHeight || h) - h))}px`;
        container.style.right = '';
        container.style.bottom = '';
        container.style.transform = 'none';
    }

    I.isNativeReturnBallDragDisabled = function isNativeReturnBallDragDisabled() {
        const runtime = window.__NEKO_DESKTOP_RUNTIME__ || {};
        return !!(
            window.__NEKO_DISABLE_NATIVE_RETURN_BALL_DRAG__ ||
            runtime.disableNativeReturnBallDrag
        );
    }

    I.isNiriPhysicalCropReturnBallDragActive = function isNiriPhysicalCropReturnBallDragActive() {
        const cropApi = window.__nekoNiriPetPhysicalCrop;
        if (!cropApi) return false;
        try {
            if (typeof cropApi.isActive === 'function' && cropApi.isActive()) return true;
        } catch (_) {}
        try {
            const cropState = typeof cropApi.getState === 'function' ? cropApi.getState() : null;
            if (cropState && cropState.enabled) return true;
        } catch (_) {}
        try {
            if (document.documentElement &&
                document.documentElement.classList.contains('neko-niri-pet-physical-crop')) {
                return true;
            }
        } catch (_) {}
        return false;
    }

    I.cleanupMultiWindowReturnBallDrag = function cleanupMultiWindowReturnBallDrag() {
        const state = I.multiWindowReturnBallDragState;
        if (!state) return;

        const shouldStopNativeDrag = state.isDragging;
        const stopScreenX = I.getReturnBallDragScreenCoordinate(state.releaseScreenX, state.startScreenX);
        const stopScreenY = I.getReturnBallDragScreenCoordinate(state.releaseScreenY, state.startScreenY);

        state.dragSessionToken += 1;
        state.isDragging = false;
        I.clearReturnBallDragRecoveryTimer(state);
        I.clearMultiWindowReturnBallDeferredWork(state);
        if (state.container) {
            state.container.removeEventListener('mousedown', state.handleMouseDown, true);
            state.container.removeEventListener('touchstart', state.handleTouchStart, true);
            state.container.removeEventListener('click', state.handleClick, true);
        }
        document.removeEventListener('mousemove', state.handleMouseMove);
        document.removeEventListener('mouseup', state.handleMouseUp);
        document.removeEventListener('pointermove', state.handlePointerMove, true);
        document.removeEventListener('pointerup', state.handlePointerUp, true);
        document.removeEventListener('pointercancel', state.handlePointerCancel, true);
        document.removeEventListener('touchmove', state.handleTouchMove);
        document.removeEventListener('touchend', state.handleTouchEnd);
        document.removeEventListener('touchcancel', state.handleTouchEnd);
        window.removeEventListener('blur', state.handleWindowBlur);
        window.removeEventListener('pagehide', state.handlePageHide);
        document.removeEventListener('visibilitychange', state.handleVisibilityChange);
        if (state.suppressDomClickTimer) {
            clearTimeout(state.suppressDomClickTimer);
            state.suppressDomClickTimer = null;
        }

        if (state.container) {
            I.restoreSavedReturnBallStyle(state.container, state);
            resetReturnBallTemporaryStyle(state.container);
            state.container.setAttribute('data-dragging', 'false');
            state.container.removeAttribute('data-neko-return-click-suppressed');
        }
        delete document.body.dataset.nekoBallDrag;
        I.multiWindowReturnBallDragState = null;

        if (shouldStopNativeDrag && window.nekoPetDrag && typeof window.nekoPetDrag.stop === 'function') {
            Promise.resolve()
                .then(() => window.nekoPetDrag.stop(stopScreenX, stopScreenY))
                .finally(() => {
                    if (window.nekoPetDrag && typeof window.nekoPetDrag.reveal === 'function') {
                        return window.nekoPetDrag.reveal();
                    }
                    return null;
                })
                .catch(() => {});
        }
    }

    Object.assign(window.appUi, I.mod || {});
})();
