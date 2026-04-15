/**
 * app-chat-avatar.js — 聊天框内的当前头像预览
 * 依赖：avatar-portrait.js
 */
(function () {
    'use strict';

    const mod = {};
    const S = window.appState;

    let isCapturing = false;
    let pendingAutoCapture = false;
    let activeCaptureToken = 0;
    let cachedPreview = null;
    let autoCaptureTimer = null;
    let lastScheduledCacheKey = '';
    // 多窗口模式：由 IPC 从 Pet 窗口注入的头像（/chat 页面无本地模型）
    let externalAvatarDataUrl = '';
    let externalAvatarModelType = '';

    const STORAGE_PREFIX = 'neko_avatar:';

    function translateLabel(key, fallback) {
        if (typeof window.safeT === 'function') {
            return window.safeT(key, fallback);
        }
        if (typeof window.t === 'function') {
            return window.t(key, fallback);
        }
        return fallback;
    }

    function getErrorMessage(error) {
        return error && error.message ? error.message : String(error || '');
    }

    // ——— per-character localStorage ———

    function getCurrentCharacterKey() {
        var name = window.lanlan_config && window.lanlan_config.lanlan_name;
        return name ? STORAGE_PREFIX + name : '';
    }

    function saveToStorage(preview) {
        var key = getCurrentCharacterKey();
        if (!key) return;
        try {
            localStorage.setItem(key, JSON.stringify({
                dataUrl: preview.dataUrl,
                modelType: preview.modelType,
                capturedAt: preview.capturedAt,
                cacheKey: preview.cacheKey || ''
            }));
        } catch (_) { /* quota exceeded — 静默失败 */ }
    }

    function loadFromStorage() {
        var key = getCurrentCharacterKey();
        if (!key) return null;
        try {
            var raw = localStorage.getItem(key);
            if (!raw) return null;
            var parsed = JSON.parse(raw);
            if (parsed && parsed.dataUrl) return parsed;
        } catch (_) { /* 损坏数据 — 忽略 */ }
        return null;
    }

    /** 清除已不存在的角色的头像缓存（fire-and-forget） */
    function purgeOrphanedAvatars() {
        // 清理旧版单 key 缓存
        try { localStorage.removeItem('neko_chat_avatar_cache'); } catch (_) {}

        fetch('/api/characters')
            .then(function (res) { return res.json(); })
            .then(function (data) {
                var validNames = new Set();
                var catgirls = data && data['\u732b\u5a18'];  // 猫娘
                if (catgirls && typeof catgirls === 'object') {
                    Object.keys(catgirls).forEach(function (n) { validNames.add(n); });
                }
                for (var i = localStorage.length - 1; i >= 0; i--) {
                    var k = localStorage.key(i);
                    if (k && k.indexOf(STORAGE_PREFIX) === 0) {
                        var charName = k.slice(STORAGE_PREFIX.length);
                        if (!validNames.has(charName)) {
                            localStorage.removeItem(k);
                        }
                    }
                }
            })
            .catch(function () { /* 静默失败 */ });
    }

    function normalizeModelLabel(modelType) {
        const type = String(modelType || '').toLowerCase();
        if (type === 'vrm') return 'VRM';
        if (type === 'mmd') return 'MMD';
        return 'Live2D';
    }

    // 当前打开弹窗的触发按钮（用于定位 + 激活态）
    let activeTrigger = null;

    function clearTriggerActive() {
        if (activeTrigger) {
            activeTrigger.classList.remove('is-active');
        }
        activeTrigger = null;
    }

    /**
     * 计算并设置弹窗相对触发按钮的位置。
     * 默认在按钮下方右对齐；空间不足时翻转到上方；始终限制在视口内。
     */
    function positionPopupNearTrigger(popup, trigger) {
        const margin = 8;
        const viewportW = window.innerWidth;
        const viewportH = window.innerHeight;

        // 把弹窗移到不可见区域做精准尺寸测量，临时关闭 transform 以便 offsetWidth 反映真实布局尺寸
        popup.style.left = '-9999px';
        popup.style.top = '-9999px';
        popup.style.transform = 'none';
        popup.style.transformOrigin = '';

        const popupW = popup.offsetWidth || 320;
        const popupH = popup.offsetHeight || 220;

        // 恢复 transform：is-visible 切换时才播放动画；此时清回空字符串，让 CSS 规则生效
        popup.style.transform = '';

        let anchorRect = null;
        if (trigger && typeof trigger.getBoundingClientRect === 'function') {
            anchorRect = trigger.getBoundingClientRect();
        }

        // 无触发按钮（例如通过 API 直接调用）：居中显示
        if (!anchorRect || (anchorRect.width === 0 && anchorRect.height === 0)) {
            const left = Math.max(margin, Math.round((viewportW - popupW) / 2));
            const top = Math.max(margin, Math.round((viewportH - popupH) / 2));
            popup.style.left = left + 'px';
            popup.style.top = top + 'px';
            popup.style.transformOrigin = 'center center';
            return;
        }

        // 优先：按钮下方右对齐
        let top = anchorRect.bottom + margin;
        let openUpward = false;
        if (top + popupH > viewportH - margin && anchorRect.top - margin - popupH >= margin) {
            top = anchorRect.top - margin - popupH;
            openUpward = true;
        }
        top = Math.max(margin, Math.min(top, viewportH - popupH - margin));

        let left = anchorRect.right - popupW;
        left = Math.max(margin, Math.min(left, viewportW - popupW - margin));

        popup.style.left = left + 'px';
        popup.style.top = top + 'px';
        popup.style.transformOrigin = openUpward ? 'bottom right' : 'top right';
    }

    // 退场过渡 fallback（略大于 CSS 里 opacity/transform transition 的最长时长 0.18s）
    const HIDE_TRANSITION_FALLBACK_MS = 220;
    let pendingHideTimer = null;
    let pendingHideHandler = null;
    let pendingShowRaf = null;

    function cancelPendingShow() {
        if (pendingShowRaf) {
            window.cancelAnimationFrame(pendingShowRaf);
            pendingShowRaf = null;
        }
    }

    function cancelPendingHide(card) {
        if (pendingHideTimer) {
            clearTimeout(pendingHideTimer);
            pendingHideTimer = null;
        }
        if (card && pendingHideHandler) {
            card.removeEventListener('transitionend', pendingHideHandler);
        }
        pendingHideHandler = null;
    }

    function setPreviewVisible(visible, trigger) {
        const card = S.dom.chatAvatarPreviewCard;
        if (!card) return;

        if (visible) {
            // 进场前把可能残留的退场监听清干净，避免刚打开又被 finalize 为 hidden
            cancelPendingHide(card);
            // 也清掉可能排队但尚未执行的进场 rAF，确保 is-visible 只被加一次
            cancelPendingShow();

            // 切换触发按钮的激活态
            if (activeTrigger && activeTrigger !== trigger) {
                activeTrigger.classList.remove('is-active');
            }
            activeTrigger = trigger || activeTrigger || null;
            if (activeTrigger) {
                activeTrigger.classList.add('is-active');
            }

            card.hidden = false;
            positionPopupNearTrigger(card, activeTrigger);
            // 触发进入动画（下一帧应用 is-visible）
            pendingShowRaf = window.requestAnimationFrame(function () {
                pendingShowRaf = null;
                // 守卫：如果这一帧之前已被切换到隐藏状态，就不要再加回 is-visible
                if (card.hidden) return;
                card.classList.add('is-visible');
            });
        } else {
            // 已经隐藏或正在隐藏 → 幂等退出
            if (card.hidden) return;
            if (pendingHideHandler) return;
            // 关键：关闭时先取消任何尚未执行的进场 rAF，否则它会把 is-visible 加回来
            cancelPendingShow();

            card.classList.remove('is-visible');
            clearTriggerActive();

            const finalizeHide = function () {
                if (pendingHideTimer) {
                    clearTimeout(pendingHideTimer);
                    pendingHideTimer = null;
                }
                card.removeEventListener('transitionend', finalizeHide);
                pendingHideHandler = null;
                // 可能在等待过渡期间又被重新打开；若已重新可见则不要强制 hidden
                if (!card.classList.contains('is-visible')) {
                    card.hidden = true;
                }
            };

            pendingHideHandler = finalizeHide;
            card.addEventListener('transitionend', finalizeHide);
            pendingHideTimer = window.setTimeout(finalizeHide, HIDE_TRANSITION_FALLBACK_MS);
        }
    }

    function setLoadingState(loading) {
        const legacyButton = S.dom.avatarPreviewButton;
        const headerButton = S.dom.avatarPreviewHeaderButton;
        const refreshButton = S.dom.chatAvatarPreviewRefreshButton;
        [legacyButton, headerButton].forEach(function (btn) {
            if (!btn) return;
            btn.classList.toggle('is-loading', loading);
            btn.disabled = loading;
        });
        if (refreshButton) {
            refreshButton.disabled = loading;
        }
    }

    function setPreviewStatus(text) {
        if (S.dom.chatAvatarPreviewStatus) {
            S.dom.chatAvatarPreviewStatus.textContent = text;
        }
    }

    function setPreviewNote(text) {
        if (S.dom.chatAvatarPreviewNote) {
            S.dom.chatAvatarPreviewNote.textContent = text;
        }
    }

    function setPreviewImage(dataUrl) {
        const image = S.dom.chatAvatarPreviewImage;
        const placeholder = S.dom.chatAvatarPreviewPlaceholder;
        const shell = S.dom.chatAvatarPreviewImageShell;
        if (!image || !placeholder || !shell) return;

        if (dataUrl) {
            image.src = dataUrl;
            image.hidden = false;
            placeholder.hidden = true;
            shell.classList.remove('is-empty');
            return;
        }

        image.hidden = true;
        image.removeAttribute('src');
        placeholder.hidden = false;
        shell.classList.add('is-empty');
    }

    // 作为独立弹窗后不再要求输入区可见；保留函数以便将来需要判断上下文。
    function isInlinePreviewAvailable() {
        return true;
    }

    function getCurrentModelType() {
        if (typeof window.avatarPortrait?.normalizeModelType === 'function') {
            return window.avatarPortrait.normalizeModelType();
        }
        const modelType = String(window.lanlan_config?.model_type || '').toLowerCase();
        if (modelType === 'live3d') {
            const subType = String(window.lanlan_config?.live3d_sub_type || '').toLowerCase();
            if (subType === 'mmd') return 'mmd';
            if (subType === 'vrm') return 'vrm';
        }
        if (modelType === 'vrm') return 'vrm';
        if (modelType === 'mmd') return 'mmd';
        return 'live2d';
    }

    function getCurrentModelCacheKey() {
        const modelType = getCurrentModelType();
        if (modelType === 'vrm') {
            return 'vrm:' + String(
                window.vrmManager?.currentModel?.url ||
                window.lanlan_config?.vrm ||
                ''
            );
        }
        if (modelType === 'mmd') {
            return 'mmd:' + String(
                window.mmdManager?.currentModel?.url ||
                window.mmdModel ||
                window.lanlan_config?.mmd ||
                ''
            );
        }
        return 'live2d:' + String(
            window.live2dManager?._lastLoadedModelPath ||
            window.cubism4Model ||
            ''
        );
    }

    function hasUsableCachedPreview() {
        return !!(
            cachedPreview &&
            cachedPreview.dataUrl &&
            cachedPreview.cacheKey &&
            cachedPreview.cacheKey === getCurrentModelCacheKey()
        );
    }

    function applyPreviewResult(result, cacheKey) {
        cachedPreview = {
            cacheKey,
            dataUrl: result.dataUrl,
            modelType: result.modelType || getCurrentModelType(),
            capturedAt: Date.now()
        };
        saveToStorage(cachedPreview);

        setPreviewImage(cachedPreview.dataUrl);
        setPreviewStatus(
            translateLabel('chat.avatarPreviewReady', '头像已更新') + ' · ' + normalizeModelLabel(cachedPreview.modelType)
        );
        setPreviewNote(translateLabel('chat.avatarPreviewReadyHint', '这是从当前模型画布实时提取的头像预览。'));
        window.dispatchEvent(new CustomEvent('chat-avatar-preview-updated', {
            detail: {
                cacheKey: cachedPreview.cacheKey,
                dataUrl: cachedPreview.dataUrl,
                modelType: cachedPreview.modelType,
                capturedAt: cachedPreview.capturedAt
            }
        }));
    }

    /** 仅清内存，让 scheduleAutoCapture 不被跳过；localStorage 按角色隔离，无需清除 */
    function invalidateCachedPreview() {
        cachedPreview = null;
        lastScheduledCacheKey = '';
    }

    /**
     * 从 Pet 窗口（Electron 多窗口模式）通过 IPC 请求头像预览。
     */
    function captureAvatarPreviewViaIpc() {
        return new Promise(function (resolve, reject) {
            var finished = false;
            var timerId = null;
            function cleanup() {
                window.removeEventListener('neko:avatar-preview-ipc-result', onResult);
                if (timerId) { clearTimeout(timerId); timerId = null; }
            }
            function onResult(event) {
                if (finished) return;
                finished = true;
                cleanup();
                var detail = event && event.detail;
                if (detail && detail.dataUrl) {
                    resolve({ dataUrl: detail.dataUrl, modelType: detail.modelType || '' });
                } else {
                    reject(new Error(translateLabel('chat.avatarPreviewFailed', '生成头像失败')));
                }
            }
            window.addEventListener('neko:avatar-preview-ipc-result', onResult);
            timerId = setTimeout(function () {
                if (finished) return;
                finished = true;
                cleanup();
                reject(new Error(translateLabel('chat.avatarPreviewFailed', '生成头像失败')));
            }, 10000);
            try {
                window.__nekoRequestAvatarPreview();
            } catch (err) {
                if (!finished) {
                    finished = true;
                    cleanup();
                    reject(err);
                }
            }
        });
    }

    async function captureAvatarPreview() {
        // 优先使用本地 avatarPortrait（index.html）；chat.html 里回退到 IPC。
        if (window.avatarPortrait && typeof window.avatarPortrait.capture === 'function') {
            return window.avatarPortrait.capture({
                width: 320,
                height: 320,
                padding: 0.035,
                shape: 'rounded',
                radius: 40,
                background: 'rgba(255, 255, 255, 0.96)',
                includeDataUrl: true
            });
        }
        if (window.__NEKO_MULTI_WINDOW__ && typeof window.__nekoRequestAvatarPreview === 'function') {
            return captureAvatarPreviewViaIpc();
        }
        throw new Error(translateLabel('chat.avatarPreviewUnavailable', '头像预览功能尚未就绪。'));
    }

    async function renderAvatarPreview(options = {}) {
        const forceRefresh = options.forceRefresh === true;
        const showCard = options.showCard !== false;
        const silent = options.silent === true;
        const trigger = options.trigger || null;

        if (isCapturing) {
            if (!showCard && silent) {
                pendingAutoCapture = true;
            }
            return;
        }

        if (!forceRefresh && hasUsableCachedPreview()) {
            if (showCard) {
                setPreviewVisible(true, trigger);
            }
            setPreviewImage(cachedPreview.dataUrl);
            setPreviewStatus(
                translateLabel('chat.avatarPreviewReady', '头像已更新') + ' · ' + normalizeModelLabel(cachedPreview.modelType)
            );
            setPreviewNote(translateLabel('chat.avatarPreviewCachedHint', '已显示当前模型的缓存头像，点击刷新可重新生成。'));
            return;
        }

        isCapturing = true;
        const token = ++activeCaptureToken;
        const cacheKey = getCurrentModelCacheKey();
        if (showCard) {
            setPreviewVisible(true, trigger);
        }
        setLoadingState(true);
        setPreviewStatus(forceRefresh
            ? translateLabel('chat.avatarPreviewRefreshing', '正在刷新当前头像...')
            : translateLabel('chat.avatarPreviewGenerating', '正在生成当前头像...'));
        setPreviewNote(translateLabel('chat.avatarPreviewCardNote', '将基于当前显示中的 Live2D / VRM / MMD 模型生成头像。'));

        try {
            const result = await captureAvatarPreview();
            if (token !== activeCaptureToken) return;

            applyPreviewResult(result, cacheKey);
        } catch (error) {
            if (token !== activeCaptureToken) return;

            if (showCard) {
                setPreviewImage('');
                setPreviewStatus(translateLabel('chat.avatarPreviewFailed', '生成头像失败'));
                setPreviewNote(getErrorMessage(error));
            }
            if (!silent && typeof window.showStatusToast === 'function') {
                window.showStatusToast(
                    translateLabel('chat.avatarPreviewFailed', '生成头像失败') + ': ' + getErrorMessage(error),
                    4500
                );
            }
        } finally {
            if (token === activeCaptureToken) {
                isCapturing = false;
                setLoadingState(false);
                if (pendingAutoCapture) {
                    pendingAutoCapture = false;
                    scheduleAutoCapture('pending-retry');
                }
            }
        }
    }

    function scheduleAutoCapture(reason) {
        const cacheKey = getCurrentModelCacheKey();
        if (!cacheKey || cacheKey.endsWith(':')) {
            return;
        }
        if (hasUsableCachedPreview()) {
            return;
        }
        if (lastScheduledCacheKey === cacheKey && isCapturing) {
            pendingAutoCapture = true;
            return;
        }

        lastScheduledCacheKey = cacheKey;
        if (autoCaptureTimer) {
            clearTimeout(autoCaptureTimer);
        }

        autoCaptureTimer = setTimeout(function () {
            autoCaptureTimer = null;
            window.requestAnimationFrame(function () {
                window.requestAnimationFrame(function () {
                    renderAvatarPreview({
                        forceRefresh: true,
                        silent: true,
                        showCard: false,
                        reason: reason || 'model-loaded'
                    }).catch(function (error) {
                        console.warn('[app-chat-avatar] 自动缓存头像失败:', error);
                    });
                });
            });
        }, 180);
    }

    function handleModelLoaded(reason) {
        var newCacheKey = getCurrentModelCacheKey();
        if (cachedPreview && cachedPreview.dataUrl && cachedPreview.cacheKey === newCacheKey) {
            return;
        }
        invalidateCachedPreview();
        scheduleAutoCapture(reason);
    }

    function bindModelLoadListeners() {
        const previousOnModelLoaded = window.live2dManager && typeof window.live2dManager.onModelLoaded === 'function'
            ? window.live2dManager.onModelLoaded
            : null;

        if (window.live2dManager) {
            window.live2dManager.onModelLoaded = function (model, modelPath) {
                if (previousOnModelLoaded) {
                    previousOnModelLoaded.call(window.live2dManager, model, modelPath);
                }
                handleModelLoaded('live2d-model-loaded');
            };
        }

        window.addEventListener('vrm-model-loaded', function () {
            handleModelLoaded('vrm-model-loaded');
        });

        window.addEventListener('mmd-model-loaded', function () {
            handleModelLoaded('mmd-model-loaded');
        });
    }

    function handleOutsidePointer(event) {
        const card = S.dom.chatAvatarPreviewCard;
        if (!card || card.hidden) return;
        if (card.contains(event.target)) return;
        // 点在任一已注册的触发按钮上 → 由触发按钮自己处理（点击切换）
        for (const trigger of triggerButtons) {
            if (trigger && trigger.contains(event.target)) return;
        }
        setPreviewVisible(false);
    }

    function handleEscapeKey(event) {
        if (event.key !== 'Escape') return;
        const card = S.dom.chatAvatarPreviewCard;
        if (!card || card.hidden) return;
        setPreviewVisible(false);
    }

    // 捕获模式下 scroll 会被文档内任意滚动容器触发（聊天消息列表、设置面板等），
    // 用 rAF 合并到每帧最多一次布局计算，避免频繁 getBoundingClientRect 触发回流。
    let viewportChangeRaf = null;
    function handleViewportChange() {
        if (viewportChangeRaf) return;
        viewportChangeRaf = window.requestAnimationFrame(function () {
            viewportChangeRaf = null;
            const card = S.dom.chatAvatarPreviewCard;
            if (!card || card.hidden || !activeTrigger) return;
            positionPopupNearTrigger(card, activeTrigger);
        });
    }

    // 已绑定的触发按钮集合（供外点击判断使用）
    const triggerButtons = new Set();

    function bindTriggerButton(button) {
        if (!button || triggerButtons.has(button)) return;
        triggerButtons.add(button);
        button.addEventListener('click', function (event) {
            event.stopPropagation();
            const card = S.dom.chatAvatarPreviewCard;
            // 同一触发按钮再次点击 → 关闭弹窗
            if (card && !card.hidden && activeTrigger === button) {
                setPreviewVisible(false);
                return;
            }
            renderAvatarPreview({ trigger: button });
        });
    }

    mod.init = function init() {
        S.dom.avatarPreviewButton = document.getElementById('avatarPreviewButton');
        S.dom.avatarPreviewHeaderButton = document.getElementById('avatarPreviewHeaderButton');
        // 保留旧字段名 chatAvatarPreviewCard 以兼容其他代码；实际指向新弹窗元素。
        S.dom.chatAvatarPreviewCard = document.getElementById('chat-avatar-preview-popup');
        S.dom.chatAvatarPreviewStatus = document.getElementById('chat-avatar-preview-status');
        S.dom.chatAvatarPreviewNote = document.getElementById('chat-avatar-preview-note');
        S.dom.chatAvatarPreviewImageShell = document.getElementById('chat-avatar-preview-image-shell');
        S.dom.chatAvatarPreviewImage = document.getElementById('chat-avatar-preview-image');
        S.dom.chatAvatarPreviewPlaceholder = document.getElementById('chat-avatar-preview-placeholder');
        S.dom.chatAvatarPreviewRefreshButton = document.getElementById('chatAvatarPreviewRefreshButton');
        S.dom.chatAvatarPreviewCloseButton = document.getElementById('chatAvatarPreviewCloseButton');

        // —— 数据层：不管有无预览 UI 都执行（chat.html 没有预览卡片但仍需头像数据） ——

        // 头像数据优先级：IPC 刚注入的内存缓存 > localStorage > 空态
        //   1) 模块加载时 __nekoPendingAvatar 被消费后，cachedPreview 会被预先填好，
        //      那才是 Pet 窗口当前的实时头像，比 localStorage 里上次会话的数据更新。
        //   2) 否则退回读取当前角色的持久化头像。
        //   3) 都没有，则进入等待态。
        var stored = loadFromStorage();
        if (cachedPreview && cachedPreview.dataUrl) {
            // 保留内存缓存；刷新 cacheKey 并补写一次 localStorage
            // （加载时 lanlan_config.lanlan_name 可能尚未就绪，保存会静默失败）。
            cachedPreview.cacheKey = getCurrentModelCacheKey();
            saveToStorage(cachedPreview);
            setPreviewImage(cachedPreview.dataUrl);
            setPreviewStatus(
                translateLabel('chat.avatarPreviewReady', '头像已更新') + ' · ' + normalizeModelLabel(cachedPreview.modelType)
            );
            setPreviewNote(translateLabel('chat.avatarPreviewReadyHint', '这是从当前模型画布实时提取的头像预览。'));
        } else if (stored) {
            cachedPreview = {
                cacheKey: stored.cacheKey || getCurrentModelCacheKey(),
                dataUrl: stored.dataUrl,
                modelType: stored.modelType,
                capturedAt: stored.capturedAt
            };
            setPreviewImage(cachedPreview.dataUrl);
            setPreviewStatus(
                translateLabel('chat.avatarPreviewReady', '头像已更新') + ' · ' + normalizeModelLabel(cachedPreview.modelType)
            );
            setPreviewNote(translateLabel('chat.avatarPreviewCachedHint', '已显示当前模型的缓存头像，点击刷新可重新生成。'));
            window.dispatchEvent(new CustomEvent('chat-avatar-preview-updated', {
                detail: {
                    dataUrl: cachedPreview.dataUrl,
                    modelType: cachedPreview.modelType,
                    capturedAt: cachedPreview.capturedAt,
                    source: 'storage'
                }
            }));
        } else {
            cachedPreview = null;
            setPreviewImage('');
            setPreviewStatus(translateLabel('chat.avatarPreviewWaiting', '等待当前模型头像缓存生成'));
            setPreviewNote(translateLabel('chat.avatarPreviewCardNote', '将基于当前显示中的 Live2D / VRM / MMD 模型生成头像。'));
        }

        bindModelLoadListeners();
        scheduleAutoCapture('init');

        // 清理已删除角色的残留头像（不阻塞初始化）
        purgeOrphanedAvatars();

        // —— UI 层：独立弹窗绑定 ——
        // 弹窗 DOM 在 chat.html / index.html 中都存在；避免重复绑定。
        const popup = S.dom.chatAvatarPreviewCard;
        const refreshButton = S.dom.chatAvatarPreviewRefreshButton;
        const closeButton = S.dom.chatAvatarPreviewCloseButton;

        if (!popup || !refreshButton || !closeButton) {
            return;
        }

        // 触发按钮：老版 index.html 聊天面板内的按钮 + 新版 React 聊天头部按钮
        bindTriggerButton(S.dom.avatarPreviewButton);
        bindTriggerButton(S.dom.avatarPreviewHeaderButton);

        refreshButton.addEventListener('click', function () {
            renderAvatarPreview({ forceRefresh: true, trigger: activeTrigger });
        });

        closeButton.addEventListener('click', function () {
            setPreviewVisible(false);
        });

        document.addEventListener('pointerdown', handleOutsidePointer, true);
        document.addEventListener('keydown', handleEscapeKey);
        window.addEventListener('resize', handleViewportChange);
        window.addEventListener('scroll', handleViewportChange, true);
    };

    /**
     * 外部 API：允许其他模块（如 React 聊天窗口）手动打开弹窗。
     * @param {HTMLElement} [trigger] - 触发按钮，用于定位
     * @param {Object} [options]
     */
    mod.showPopup = function showPopup(trigger, options) {
        const opts = Object.assign({ trigger: trigger || null }, options || {});
        return renderAvatarPreview(opts);
    };

    mod.hidePopup = function hidePopup() {
        setPreviewVisible(false);
    };

    /**
     * 外部 API：让其他脚本追加触发按钮（例如动态生成的 DOM）。
     */
    mod.registerTrigger = function registerTrigger(button) {
        bindTriggerButton(button);
    };

    mod.getCachedPreview = function getCachedPreview() {
        return cachedPreview ? {
            cacheKey: cachedPreview.cacheKey,
            dataUrl: cachedPreview.dataUrl,
            modelType: cachedPreview.modelType,
            capturedAt: cachedPreview.capturedAt
        } : null;
    };

    mod.getCurrentAvatarDataUrl = function getCurrentAvatarDataUrl() {
        if (hasUsableCachedPreview()) return cachedPreview.dataUrl || '';
        // 内存缓存被 invalidate（模型加载中）或 cacheKey 暂不匹配时，仍返回旧头像
        if (cachedPreview && cachedPreview.dataUrl) return cachedPreview.dataUrl;
        // 内存为空但 localStorage 有持久化头像（invalidate 后的 fallback）
        var stored = loadFromStorage();
        if (stored && stored.dataUrl) return stored.dataUrl;
        // 多窗口 fallback：使用 IPC 注入的头像
        if (externalAvatarDataUrl) return externalAvatarDataUrl;
        return '';
    };

    /**
     * 多窗口模式：由 preload / IPC 调用，设置从 Pet 窗口获取的头像
     * @param {string} dataUrl - base64 data URL
     * @param {string} [modelType] - 'live2d' | 'vrm' | 'mmd'
     */
    mod.setExternalAvatar = function setExternalAvatar(dataUrl, modelType) {
        externalAvatarDataUrl = dataUrl || '';
        externalAvatarModelType = modelType || '';

        // 把 IPC 注入的头像合并进 cachedPreview，让 renderAvatarPreview 的快路径直接命中，
        // 避免弹窗打开时再发一次 IPC（可能超时 → 用户看到失败态）。
        if (externalAvatarDataUrl) {
            cachedPreview = {
                cacheKey: getCurrentModelCacheKey(),
                dataUrl: externalAvatarDataUrl,
                modelType: externalAvatarModelType || getCurrentModelType(),
                capturedAt: Date.now()
            };
            saveToStorage(cachedPreview);
        }

        // 如果弹窗已打开且本地没有本窗口可采集的模型，就直接把 IPC 数据显示出来。
        const card = S.dom && S.dom.chatAvatarPreviewCard;
        const hasLocalPortrait = !!(window.avatarPortrait && typeof window.avatarPortrait.capture === 'function');
        if (externalAvatarDataUrl && card && !card.hidden && !hasLocalPortrait) {
            setPreviewImage(externalAvatarDataUrl);
            setPreviewStatus(
                translateLabel('chat.avatarPreviewReady', '头像已更新') + ' · ' + normalizeModelLabel(externalAvatarModelType)
            );
            setPreviewNote(translateLabel('chat.avatarPreviewReadyHint', '这是从当前模型画布实时提取的头像预览。'));
        }
        window.dispatchEvent(new CustomEvent('chat-avatar-preview-updated', {
            detail: { dataUrl: externalAvatarDataUrl, modelType: externalAvatarModelType, source: 'ipc' }
        }));
    };

    mod.getExternalAvatar = function getExternalAvatar() {
        return externalAvatarDataUrl ? { dataUrl: externalAvatarDataUrl, modelType: externalAvatarModelType } : null;
    };

    window.appChatAvatar = mod;

    // 消费 IPC 暂存的头像数据（neko:config-injected 可能在本脚本加载前触发）
    if (window.__nekoPendingAvatar) {
        mod.setExternalAvatar(window.__nekoPendingAvatar.dataUrl, window.__nekoPendingAvatar.modelType);
        delete window.__nekoPendingAvatar;
    }
})();
