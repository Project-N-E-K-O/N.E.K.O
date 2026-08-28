/**
 * app-screen.js — Screen sharing, video streaming, and desktop source selector
 *
 * Extracted from the monolithic app.js.
 * Follows the IIFE + window global pattern used by all app-*.js modules.
 *
 * Exports: window.appScreen
 * Backward-compat globals:
 *   window.startScreenSharing, window.stopScreenSharing,
 *   window.switchScreenSharing, window.switchMicCapture,
 *   window.selectScreenSource, window.getSelectedScreenSourceId,
 *   window.renderFloatingScreenSourceList
 */
(function () {
    'use strict';

    const mod = {};
    const S = window.appState;
    const C = window.appConst;
    const safeT = window.safeT;
    const isMobile = window.appUtils.isMobile;
    const SCREEN_SOURCE_TITLE_MATCH_ENABLED_KEY = 'screenSourceTitleMatchEnabled';
    const SCREEN_SOURCE_WINDOW_TITLE_KEY = 'selectedScreenWindowTitle';
    const MAX_REMEMBERED_WINDOW_TITLE_LENGTH = 512;
    var screenSourceSelectionGeneration = 0;
    var explicitScreenSourceSelectionGeneration = null;
    var explicitScreenSourceSelectionTitle = null;

    function markScreenSourceSelectionChanged() {
        screenSourceSelectionGeneration += 1;
        explicitScreenSourceSelectionGeneration = null;
        explicitScreenSourceSelectionTitle = null;
    }

    function markCurrentScreenSourceSelectionExplicit(sourceTitle) {
        explicitScreenSourceSelectionGeneration = screenSourceSelectionGeneration;
        explicitScreenSourceSelectionTitle = normalizeScreenSourceTitle(sourceTitle);
    }

    function isCurrentScreenSourceSelectionExplicit(source) {
        var normalizedExplicitTitle = normalizeScreenSourceTitle(
            explicitScreenSourceSelectionTitle
        );
        return explicitScreenSourceSelectionGeneration === screenSourceSelectionGeneration
            && !!normalizedExplicitTitle
            && normalizedExplicitTitle === normalizeScreenSourceTitle(source && source.name);
    }

    function currentExplicitScreenSourceSelectionMatches(expectedTitle) {
        var normalizedExplicitTitle = normalizeScreenSourceTitle(
            explicitScreenSourceSelectionTitle
        );
        var normalizedExpectedTitle = normalizeScreenSourceTitle(expectedTitle);
        return explicitScreenSourceSelectionGeneration === screenSourceSelectionGeneration
            && typeof S.selectedScreenSourceId === 'string'
            && S.selectedScreenSourceId.startsWith('window:')
            && !!normalizedExplicitTitle
            && (!normalizedExpectedTitle
                || normalizedExplicitTitle === normalizedExpectedTitle);
    }

    function normalizeScreenSourceTitle(value) {
        return String(value || '').normalize('NFC').trim().replace(/\s+/g, ' ').toLowerCase();
    }

    function isScreenSourceTitleMatchEnabled() {
        try {
            return localStorage.getItem(SCREEN_SOURCE_TITLE_MATCH_ENABLED_KEY) === 'true';
        } catch (_) {
            return false;
        }
    }

    function readRememberedWindowTitle() {
        try {
            var title = localStorage.getItem(SCREEN_SOURCE_WINDOW_TITLE_KEY) || '';
            if (!title || title.length > MAX_REMEMBERED_WINDOW_TITLE_LENGTH) return '';
            return title;
        } catch (_) {
            return '';
        }
    }

    function storeRememberedWindowTitle(title) {
        try {
            var value = String(title || '').trim();
            if (!value || value.length > MAX_REMEMBERED_WINDOW_TITLE_LENGTH) {
                localStorage.removeItem(SCREEN_SOURCE_WINDOW_TITLE_KEY);
                return false;
            }
            localStorage.setItem(SCREEN_SOURCE_WINDOW_TITLE_KEY, value);
            return true;
        } catch (error) {
            console.warn('[屏幕源] 无法保存窗口标题:', error);
            return false;
        }
    }

    function clearRememberedWindowTitle() {
        try { localStorage.removeItem(SCREEN_SOURCE_WINDOW_TITLE_KEY); } catch (_) { }
    }

    function updateScreenSourceTitleMatchToggleState() {
        var enabled = isScreenSourceTitleMatchEnabled();
        document.querySelectorAll('.neko-screen-source-title-match-toggle').forEach(function (input) {
            input.checked = enabled;
            input.dispatchEvent(new CustomEvent('neko:toggle-visual-sync'));
        });
    }

    function rememberCurrentlySelectedWindowFromDom() {
        var selectedId = S.selectedScreenSourceId;
        if (!selectedId || !selectedId.startsWith('window:')) return;
        if (!currentExplicitScreenSourceSelectionMatches('')) return;
        var options = document.querySelectorAll('.screen-source-option');
        for (var i = 0; i < options.length; i += 1) {
            if (options[i].dataset.sourceId === selectedId
                && options[i].getClientRects().length > 0) {
                storeRememberedWindowTitle(options[i].dataset.sourceName || '');
                return;
            }
        }
        storeRememberedWindowTitle(explicitScreenSourceSelectionTitle);
    }

    function setScreenSourceTitleMatchEnabled(enabled) {
        try {
            if (enabled) {
                localStorage.setItem(SCREEN_SOURCE_TITLE_MATCH_ENABLED_KEY, 'true');
            } else {
                localStorage.removeItem(SCREEN_SOURCE_TITLE_MATCH_ENABLED_KEY);
            }
        } catch (error) {
            console.warn('[屏幕源] 无法保存标题匹配设置:', error);
        }
        if (enabled) {
            rememberCurrentlySelectedWindowFromDom();
        } else {
            clearRememberedWindowTitle();
        }
        updateScreenSourceTitleMatchToggleState();
    }

    window.isScreenSourceTitleMatchEnabled = isScreenSourceTitleMatchEnabled;
    window.setScreenSourceTitleMatchEnabled = setScreenSourceTitleMatchEnabled;

    function resolveDesktopCaptureProvider() {
        return typeof window.getDesktopCaptureProvider === 'function'
            ? window.getDesktopCaptureProvider()
            : null;
    }

    function isNativeFrameProvider(provider) {
        return !!(provider && provider.nativeFrameCapture
            && typeof provider.captureSourceAsDataUrl === 'function');
    }

    function desktopSourceEnumerationMayPrompt(provider) {
        return !!(provider && provider.sourceEnumerationMayPrompt === true);
    }

    async function requestWindowsGraphicsCaptureFallback(provider, error, sourceId) {
        if (!provider || typeof provider.requestWindowsGraphicsCaptureFallback !== 'function') {
            return null;
        }
        try {
            return await provider.requestWindowsGraphicsCaptureFallback({
                name: String(error && error.name || ''),
                message: String(error && error.message || ''),
                deferRestartUntilConfirmed: true,
                sourceType: typeof sourceId === 'string' && sourceId.startsWith('window:')
                    ? 'window'
                    : 'screen'
            });
        } catch (fallbackRequestError) {
            console.warn('[屏幕源] 请求 Windows Graphics Capture 兼容模式失败:', fallbackRequestError);
            return null;
        }
    }

    async function confirmWindowsGraphicsCaptureFallback(provider, fallback) {
        if (!fallback || fallback.restartApproved !== true || !fallback.restartToken
            || !provider || typeof provider.restartWindowsGraphicsCaptureFallback !== 'function') {
            return fallback;
        }
        try {
            return await provider.restartWindowsGraphicsCaptureFallback(fallback.restartToken);
        } catch (restartError) {
            console.warn('[屏幕源] 确认 Windows Graphics Capture 兼容模式重启失败:', restartError);
            return {
                prompted: false,
                restarting: false,
                reason: 'restart-confirmation-failed'
            };
        }
    }

    function hasVisibleModelSurface() {
        var modelContainerIds = [
            'live2d-container',
            'vrm-container',
            'mmd-container',
            'pngtuber-container'
        ];
        for (var i = 0; i < modelContainerIds.length; i += 1) {
            var container = document.getElementById(modelContainerIds[i]);
            if (!container || container.classList.contains('hidden') || container.classList.contains('minimized')) {
                continue;
            }
            var computed = window.getComputedStyle ? getComputedStyle(container) : null;
            if (container.style.display === 'none' || (computed && computed.display === 'none')) continue;
            if (container.style.visibility === 'hidden' || (computed && computed.visibility === 'hidden')) continue;
            return true;
        }
        return false;
    }

    async function ensureModelVisibleForScreenSharing() {
        // 屏幕分享不应改变当前模型/毛线球状态。只有模型确实没有可见容器时，
        // 才执行历史兼容的恢复逻辑，避免 showCurrentModel 重新触发视口和边界同步。
        if (hasVisibleModelSurface() || typeof window.showCurrentModel !== 'function') return;
        await window.showCurrentModel();
    }

    var nativeCaptureGeneration = 0;
    var activeNativeCaptureSourceId = null;

    // ======================== DOM refs (lazy, filled on first use) ========================
    function dom(id) {
        return document.getElementById(id);
    }
    function screenButton()       { return dom('screenButton'); }
    function micButton()          { return dom('micButton'); }
    function muteButton()         { return dom('muteButton'); }
    function stopButton()         { return dom('stopButton'); }
    function resetSessionButton() { return dom('resetSessionButton'); }

    // ======================== Restore persisted screen source ========================
    S.selectedScreenSourceId = (function () {
        try {
            var saved = localStorage.getItem('selectedScreenSourceId');
            return saved || null;
        } catch (e) {
            return null;
        }
    })();

    // ======================== pushSelectedSourceToMain ========================
    /**
     * 将渲染器端的 selectedScreenSourceId 同步到主进程，供 main.js 的
     * setDisplayMediaRequestHandler 回调使用；任何修改 S.selectedScreenSourceId
     * 的代码点都应调用此函数，保证 getDisplayMedia 兜底也能认用户的选择。
     * fire-and-forget，不阻塞调用方。
     */
    function pushSelectedSourceToMain(sourceId) {
        try {
            var provider = resolveDesktopCaptureProvider();
            if (provider && typeof provider.setSelectedSource === 'function') {
                Promise.resolve(provider.setSelectedSource(sourceId || null))
                    .catch(function (e) { console.warn('[屏幕源] 同步选中源到主进程失败:', e); });
            }
        } catch (e) {
            console.warn('[屏幕源] 同步选中源到主进程异常:', e);
        }
    }
    mod.pushSelectedSourceToMain = pushSelectedSourceToMain;

    // ======================== clearSelectedScreenSource ========================
    /**
     * 统一清除已失效的选中屏幕源 ID：渲染器 state + localStorage + 主进程三处一起清，
     * 并同步 popup UI 高亮状态。用在检测到 selectedScreenSourceId 对应的窗口/屏幕
     * 已不复存在（HWND 失效、窗口被关、屏幕被拔掉）时，防止下一次截图仍拿同一个
     * 过期 ID 去走必然失败的快路径。
     */
    function clearSelectedScreenSource(reason) {
        if (S.selectedScreenSourceId == null) return;
        try {
            console.log('[屏幕源] 清除失效的选中源' + (reason ? ' (' + reason + ')' : ''), S.selectedScreenSourceId);
        } catch (_) { }
        S.selectedScreenSourceId = null;
        markScreenSourceSelectionChanged();
        try { localStorage.removeItem('selectedScreenSourceId'); } catch (_) { }
        pushSelectedSourceToMain(null);
        try {
            if (typeof updateScreenSourceListSelection === 'function') {
                updateScreenSourceListSelection();
            }
        } catch (_) { }
    }
    mod.clearSelectedScreenSource = clearSelectedScreenSource;

    function reconcileRememberedWindowSource(sources) {
        var result = {
            enabled: isScreenSourceTitleMatchEnabled(),
            hadRememberedTitle: false,
            status: 'disabled',
            sourceId: S.selectedScreenSourceId
        };
        if (!result.enabled) return result;

        sources = Array.isArray(sources) ? sources : [];
        var selectedSource = sources.find(function (source) {
            return source.id === S.selectedScreenSourceId;
        });
        var rememberedTitle = readRememberedWindowTitle();
        var normalizedRememberedTitle = normalizeScreenSourceTitle(rememberedTitle);
        result.hadRememberedTitle = !!normalizedRememberedTitle;

        if (normalizedRememberedTitle) {
            var titleMatches = sources.filter(function (source) {
                return source.id.startsWith('window:')
                    && normalizeScreenSourceTitle(source.name) === normalizedRememberedTitle;
            });
            var explicitSelectedTitleMatch = selectedSource
                && selectedSource.id.startsWith('window:')
                && titleMatches.some(function (source) {
                    return source.id === selectedSource.id;
                })
                && isCurrentScreenSourceSelectionExplicit(selectedSource);
            if (titleMatches.length === 1) {
                if (S.selectedScreenSourceId !== titleMatches[0].id) {
                    var previousSourceId = S.selectedScreenSourceId;
                    S.selectedScreenSourceId = titleMatches[0].id;
                    markScreenSourceSelectionChanged();
                    try { localStorage.setItem('selectedScreenSourceId', titleMatches[0].id); } catch (_) { }
                    pushSelectedSourceToMain(titleMatches[0].id);
                    restartActiveCaptureForSourceRemap(previousSourceId, titleMatches[0].id);
                    console.log('[屏幕源] 已通过唯一窗口标题恢复来源:', rememberedTitle);
                }
                result.status = 'matched';
            } else if (explicitSelectedTitleMatch) {
                result.status = 'matched';
            } else {
                stopActiveCaptureForRememberedSourceRejection();
                if (S.selectedScreenSourceId != null) {
                    clearSelectedScreenSource(
                        titleMatches.length > 1 ? '窗口标题存在多个匹配' : '窗口标题未匹配到来源'
                    );
                }
                result.status = titleMatches.length > 1 ? 'ambiguous' : 'missing';
            }
            result.sourceId = S.selectedScreenSourceId;
            return result;
        }

        if (selectedSource) {
            if (selectedSource.id.startsWith('window:')) {
                if (!isCurrentScreenSourceSelectionExplicit(selectedSource)) {
                    stopActiveCaptureForRememberedSourceRejection();
                    clearSelectedScreenSource('恢复的窗口来源缺少可信标题或本轮显式选择');
                    result.status = 'untrusted-restored-window';
                    result.sourceId = S.selectedScreenSourceId;
                    return result;
                }
                storeRememberedWindowTitle(selectedSource.name || '');
                result.status = 'adopted-current-window';
            } else {
                clearRememberedWindowTitle();
                result.status = 'current-screen';
            }
        } else {
            if (S.selectedScreenSourceId != null) {
                clearSelectedScreenSource('已保存的来源 ID 不在当前枚举结果中');
            }
            result.status = 'no-preference';
        }
        result.sourceId = S.selectedScreenSourceId;
        return result;
    }
    mod.reconcileRememberedWindowSource = reconcileRememberedWindowSource;

    async function prepareRememberedWindowCapture() {
        var rememberedTitle = normalizeScreenSourceTitle(readRememberedWindowTitle());
        var titleMatchEnabled = isScreenSourceTitleMatchEnabled();
        var selectedWindowIsBounded = typeof S.selectedScreenSourceId === 'string'
            && S.selectedScreenSourceId.startsWith('window:');
        var required = titleMatchEnabled && (!!rememberedTitle || selectedWindowIsBounded);

        function buildCaptureResult(allowed, status) {
            var expectedGeneration = screenSourceSelectionGeneration;
            var expectedSourceId = S.selectedScreenSourceId;
            var expectedTitle = normalizeScreenSourceTitle(readRememberedWindowTitle());
            var expectedEnabled = isScreenSourceTitleMatchEnabled();
            return {
                required: required,
                allowed: allowed,
                sourceId: expectedSourceId,
                status: status,
                isCurrent: function () {
                    return expectedGeneration === screenSourceSelectionGeneration
                        && expectedSourceId === S.selectedScreenSourceId
                        && expectedTitle === normalizeScreenSourceTitle(readRememberedWindowTitle())
                        && expectedEnabled === isScreenSourceTitleMatchEnabled();
                }
            };
        }

        if (!required) {
            return { required: false, allowed: true, sourceId: S.selectedScreenSourceId };
        }

        var provider = resolveDesktopCaptureProvider();
        if (!provider || desktopSourceEnumerationMayPrompt(provider)
            || typeof provider.getSources !== 'function') {
            // Portal-style providers cannot be silently enumerated without opening
            // another OS picker. Only the current renderer's explicit selection is
            // trustworthy here; a restored snapshot ID may already name another window.
            var promptSelectionIsExplicit =
                currentExplicitScreenSourceSelectionMatches(rememberedTitle);
            return buildCaptureResult(
                promptSelectionIsExplicit,
                promptSelectionIsExplicit
                    ? 'prompt-required'
                    : 'untrusted-prompt-source'
            );
        }

        var resolutionGeneration = screenSourceSelectionGeneration;
        var resolutionSourceId = S.selectedScreenSourceId;
        try {
            var sources = await window.invokeDesktopCaptureWithTimeout(
                provider,
                'getSources',
                [{
                    types: ['window', 'screen'],
                    thumbnailSize: { width: 0, height: 0 }
                }]
            );
            if (resolutionGeneration !== screenSourceSelectionGeneration
                || resolutionSourceId !== S.selectedScreenSourceId
                || rememberedTitle !== normalizeScreenSourceTitle(readRememberedWindowTitle())
                || !isScreenSourceTitleMatchEnabled()) {
                return buildCaptureResult(false, 'superseded');
            }

            var selectedBeforeReconcile = S.selectedScreenSourceId;
            var resolution = reconcileRememberedWindowSource(sources);
            if (selectedBeforeReconcile !== S.selectedScreenSourceId && S.screenCaptureStream) {
                try {
                    S.screenCaptureStream.getTracks().forEach(function (track) {
                        try { track.stop(); } catch (_) { }
                    });
                } catch (_) { }
                S.screenCaptureStream = null;
                S.screenCaptureStreamLastUsed = null;
            }
            return buildCaptureResult(
                resolution.status === 'matched'
                    || resolution.status === 'adopted-current-window',
                resolution.status
            );
        } catch (error) {
            console.warn('[屏幕源] 无法确认记忆窗口，停止本次截图:', error);
            return buildCaptureResult(false, 'enumeration-failed');
        }
    }
    mod.prepareRememberedWindowCapture = prepareRememberedWindowCapture;

    // ======================== maybeClearSourceOnNotFound ========================
    /**
     * 通用兜底：主进程 captureSourceAsDataUrl 返回 { error: 'Source not found' }
     * 时统一清掉失效的 selectedScreenSourceId。所有调用 captureSourceAsDataUrl 的
     * 路径（截图、隐藏NEKO 重截、主动搭话）共用同一份语义，避免漏处理。
     * 返回 true 表示已清理（调用方可据此判断要不要走下一个兜底）。
     */
    function maybeClearSourceOnNotFound(direct, reason) {
        if (!direct || direct.error !== 'Source not found') return false;
        clearSelectedScreenSource(reason);
        return true;
    }
    mod.maybeClearSourceOnNotFound = maybeClearSourceOnNotFound;

    // 模块初始化：立刻将还原的选择推送到主进程，覆盖上次会话遗留的值
    pushSelectedSourceToMain(S.selectedScreenSourceId);

    // ======================== 跨窗口同步 selectedScreenSourceId ========================
    // 在多窗口场景下（Pet 窗口有下拉菜单、独立 Chat 窗口只有截图按钮），两个窗口是
    // 两个渲染进程，各自持有独立的 window.appState。Pet 窗口更新选择后，Chat 窗口
    // 的 S.selectedScreenSourceId 仍是启动时的旧值 —— 导致 Chat 截图总是截"启动后
    // 首次选择的那个窗口"。
    //
    // 修复：localStorage 在 Pet / Chat 两个同源窗口间共享，任一窗口 setItem 时
    // 另一窗口会触发 storage 事件（w3c 规范）。监听它把 S 拉回最新值。
    // 注意：storage 事件在写入它的那个窗口内部并不触发，所以不会产生回环。
    window.addEventListener('storage', function (e) {
        if (e.key === SCREEN_SOURCE_TITLE_MATCH_ENABLED_KEY) {
            updateScreenSourceTitleMatchToggleState();
            return;
        }
        if (e.key !== 'selectedScreenSourceId') return;
        var newId = e.newValue || null;
        if (S.selectedScreenSourceId === newId) return;
        var oldId = S.selectedScreenSourceId;
        S.selectedScreenSourceId = newId;
        markScreenSourceSelectionChanged();
        try {
            if (typeof updateScreenSourceListSelection === 'function') {
                updateScreenSourceListSelection();
            }
        } catch (_) { }
        // 源切换时释放本窗口缓存的旧流或原生帧发送循环，强制下次用新源。
        if ((S.screenCaptureStream || activeNativeCaptureSourceId) && oldId !== newId) {
            // 先停掉可能仍在跑的发送循环，否则 startScreenVideoStreaming 创建的临时
            // <video> 会保留在旧流上，interval 继续向 WebSocket 推送冻结帧；tracks 停止
            // 后 UI 和后端都会收到"还在分享但画面不动"的矛盾状态。
            stopScreening();
            if (S.screenCaptureStream) {
                try {
                    if (typeof S.screenCaptureStream.getTracks === 'function') {
                        S.screenCaptureStream.getTracks().forEach(function (track) {
                            try { track.stop(); } catch (_) { }
                        });
                    }
                } catch (_) { }
            }
            S.screenCaptureStream = null;
            S.screenCaptureStreamLastUsed = null;
            if (S.screenCaptureStreamIdleTimer) {
                clearTimeout(S.screenCaptureStreamIdleTimer);
                S.screenCaptureStreamIdleTimer = null;
            }
            // 旧源已停止推流，所有分享控件也必须回到未分享状态。
            resetScreenSharingControls();
        }
        console.log('[屏幕源] 从其它窗口同步了新选择:', newId);
        // 不要再写 localStorage 或 pushSelectedSourceToMain —— 源窗口已经做过了，
        // 再做会产生回环/重复 IPC。
    });

    // ======================== scheduleScreenCaptureIdleCheck ========================
    function scheduleScreenCaptureIdleCheck() {
        // 清除现有定时器
        if (S.screenCaptureStreamIdleTimer) {
            clearTimeout(S.screenCaptureStreamIdleTimer);
            S.screenCaptureStreamIdleTimer = null;
        }

        // 如果没有屏幕流，不需要调度
        if (!S.screenCaptureStream || !S.screenCaptureStreamLastUsed) {
            return;
        }

        var IDLE_TIMEOUT = C.SCREEN_IDLE_TIMEOUT;     // 5 min
        var CHECK_INTERVAL = C.SCREEN_CHECK_INTERVAL;  // 1 min

        S.screenCaptureStreamIdleTimer = setTimeout(async function () {
            if (S.screenCaptureStream && S.screenCaptureStreamLastUsed) {
                var idleTime = Date.now() - S.screenCaptureStreamLastUsed;
                if (idleTime >= IDLE_TIMEOUT) {
                    // 主动视觉活跃时，不释放屏幕流（避免 macOS 反复弹窗 getDisplayMedia）
                    var proactiveVisionActive = S.proactiveVisionEnabled && (
                        S.isRecording || (S.proactiveVisionChatEnabled && S.proactiveChatEnabled)
                    );
                    var isManualScreenShare = screenButton() && screenButton().classList.contains('active');
                    if (proactiveVisionActive && !isManualScreenShare) {
                        console.log('[屏幕流闲置] 主动视觉活跃中，跳过释放并续约定时器');
                        S.screenCaptureStreamLastUsed = Date.now();
                        scheduleScreenCaptureIdleCheck();
                        return;
                    }

                    // 达到闲置阈值，调用 stopScreenSharing 统一释放资源并同步 UI
                    console.log(safeT('console.screenShareIdleDetected', 'Screen share idle detected, releasing resources'));
                    try {
                        await stopScreenSharing();
                    } catch (e) {
                        console.warn(safeT('console.screenShareAutoReleaseFailed', 'Screen share auto-release failed'), e);
                        // stopScreenSharing 失败时，手动清理残留状态防止 double-teardown
                        if (S.screenCaptureStream) {
                            try {
                                if (typeof S.screenCaptureStream.getTracks === 'function') {
                                    S.screenCaptureStream.getTracks().forEach(function (track) {
                                        try { track.stop(); } catch (err) { }
                                    });
                                }
                            } catch (err) {
                                console.warn('Failed to stop tracks in catch block', err);
                            }
                        }
                        S.screenCaptureStream = null;
                        S.screenCaptureStreamLastUsed = null;
                        S.screenCaptureStreamIdleTimer = null;
                    }
                } else {
                    // 未达到阈值，继续调度下一次检查
                    scheduleScreenCaptureIdleCheck();
                }
            }
        }, CHECK_INTERVAL);
    }
    mod.scheduleScreenCaptureIdleCheck = scheduleScreenCaptureIdleCheck;

    // ======================== captureCanvasFrame ========================
    /**
     * 统一的截图辅助函数：从video元素捕获一帧到canvas，统一720p节流和JPEG压缩
     * @param {HTMLVideoElement} video - 视频源元素
     * @param {number} jpegQuality - JPEG压缩质量 (0-1)，默认0.8
     * @param {boolean} detectBlack - 是否检测纯黑帧（窗口最小化等），默认false
     * @param {boolean} [fullResolution] - true 时保留原生分辨率不缩放（手动截图用）
     * @returns {{dataUrl: string, width: number, height: number}|null}
     *   canvas 绘制/编码失败（如超大虚拟显示器超出 canvas 上限）时返回 null，由调用方兜底
     */
    function captureCanvasFrame(video, jpegQuality, detectBlack, fullResolution) {
        if (jpegQuality === undefined) jpegQuality = 0.8;

        // 流无效时 videoWidth/videoHeight 为 0，直接返回 null 避免生成空图
        if (!video.videoWidth || !video.videoHeight) {
            return null;
        }

        var canvas = document.createElement('canvas');
        var ctx = canvas.getContext('2d');

        // 计算缩放后的尺寸（保持宽高比，限制到720p）。
        // fullResolution=true 时保留原生分辨率不缩放 —— 手动截图走这条路，让裁剪在全
        // 分辨率上进行，720p 压缩在裁剪后入列前统一做（见 compressScreenshotDataUrlTo720p）。
        var targetWidth = video.videoWidth;
        var targetHeight = video.videoHeight;

        if (!fullResolution && (targetWidth > C.MAX_SCREENSHOT_WIDTH || targetHeight > C.MAX_SCREENSHOT_HEIGHT)) {
            var widthRatio = C.MAX_SCREENSHOT_WIDTH / targetWidth;
            var heightRatio = C.MAX_SCREENSHOT_HEIGHT / targetHeight;
            var scale = Math.min(widthRatio, heightRatio);
            targetWidth = Math.round(targetWidth * scale);
            targetHeight = Math.round(targetHeight * scale);
        }

        canvas.width = targetWidth;
        canvas.height = targetHeight;

        // 绘制 + 黑帧检测 + 编码整段做防御：fullResolution 下 canvas 尺寸不再受 720p 约束，
        // 超大/虚拟显示器可能超出浏览器 canvas 上限，导致 drawImage/getImageData/toDataURL
        // 抛错或返回空。这里捕获后返回 null，让调用方走兜底（同流退 720p / 后端抓屏），
        // 而不是把可恢复的失败变成硬失败。
        var dataUrl;
        try {
            ctx.drawImage(video, 0, 0, targetWidth, targetHeight);

            // 黑帧检测：采样中心16x16区域，全黑则返回null（窗口最小化等场景）
            if (detectBlack) {
                var sw = Math.min(16, targetWidth), sh = Math.min(16, targetHeight);
                var sx = Math.floor((targetWidth - sw) / 2);
                var sy = Math.floor((targetHeight - sh) / 2);
                var sample = ctx.getImageData(sx, sy, sw, sh);
                var allBlack = true;
                for (var i = 0; i < sample.data.length; i += 4) {
                    if (sample.data[i] > 2 || sample.data[i + 1] > 2 || sample.data[i + 2] > 2) {
                        allBlack = false;
                        break;
                    }
                }
                if (allBlack) return null;
            }

            // 手动截图（fullResolution）走无损 PNG —— 这帧会被前端置顶预览并在其上裁剪/标注，
            // JPEG 0.8 会肉眼可见地发糊（尤其文字边缘）。发后端的 720p/JPEG 压缩在裁剪后下游单独做。
            // 实时取流（720p 节流）仍用 JPEG，控带宽。
            dataUrl = fullResolution
                ? canvas.toDataURL('image/png')
                : canvas.toDataURL('image/jpeg', jpegQuality);
        } catch (e) {
            console.warn('[截图] canvas 绘制/编码失败（可能分辨率超出上限），返回 null 交由调用方兜底:', e);
            return null;
        }

        // toDataURL 在部分实现下对超限 canvas 不抛错而返回 'data:,' 空串，这里一并视为失败
        if (!dataUrl || dataUrl.length < 'data:image/jpeg;base64,'.length) {
            console.warn('[截图] canvas 编码返回空结果（可能分辨率超出上限），返回 null 交由调用方兜底');
            return null;
        }

        return { dataUrl: dataUrl, width: targetWidth, height: targetHeight };
    }
    mod.captureCanvasFrame = captureCanvasFrame;

    /**
     * 将桌面壳原生截图统一编码成后端屏幕流要求的 JPEG。
     * Electron 的 NativeImage.toDataURL() 返回 PNG，而 stream_data 的
     * 屏幕数据校验只接受 data:image/jpeg;base64,...。
     */
    function normalizeNativeCaptureDataUrlForStream(dataUrl) {
        if (typeof dataUrl !== 'string' || !dataUrl.startsWith('data:image/')) {
            return Promise.resolve(null);
        }
        if (dataUrl.startsWith('data:image/jpeg;base64,')) {
            return Promise.resolve(dataUrl);
        }

        return new Promise(function (resolve) {
            var image = new Image();
            var settled = false;

            function finish(result) {
                if (settled) return;
                settled = true;
                image.onload = null;
                image.onerror = null;
                image.src = '';
                resolve(result);
            }

            image.onload = function () {
                var width = image.naturalWidth || image.width;
                var height = image.naturalHeight || image.height;
                if (!width || !height) {
                    finish(null);
                    return;
                }

                var maxWidth = C.MAX_SCREENSHOT_WIDTH || 1280;
                var maxHeight = C.MAX_SCREENSHOT_HEIGHT || 720;
                if (width > maxWidth || height > maxHeight) {
                    var scale = Math.min(maxWidth / width, maxHeight / height);
                    width = Math.max(1, Math.round(width * scale));
                    height = Math.max(1, Math.round(height * scale));
                }

                try {
                    var canvas = document.createElement('canvas');
                    canvas.width = width;
                    canvas.height = height;
                    var context = canvas.getContext('2d');
                    context.drawImage(image, 0, 0, width, height);
                    var jpegDataUrl = canvas.toDataURL('image/jpeg', 0.8);
                    finish(jpegDataUrl.startsWith('data:image/jpeg;base64,') ? jpegDataUrl : null);
                } catch (error) {
                    console.warn('[屏幕源] 原生截图转 JPEG 失败:', error);
                    finish(null);
                }
            };
            image.onerror = function () {
                console.warn('[屏幕源] 原生截图图片加载失败');
                finish(null);
            };
            image.src = dataUrl;
        });
    }
    mod.normalizeNativeCaptureDataUrlForStream = normalizeNativeCaptureDataUrlForStream;

    // ======================== captureFrameFromStream ========================
    /**
     * 从MediaStream提取单帧截图（创建临时video元素，用后即销毁）
     * @param {MediaStream} stream - 媒体流
     * @param {number} jpegQuality - JPEG压缩质量 (0-1)
     * @param {boolean} [fullResolution] - true 时保留原生分辨率（手动截图用），不缩放到720p
     * @returns {Promise<{dataUrl: string, width: number, height: number}|null>}
     */
    async function captureFrameFromStream(stream, jpegQuality, fullResolution) {
        if (!stream || !stream.active) return null;
        var video = document.createElement('video');
        video.srcObject = stream;
        video.autoplay = true;
        video.muted = true;
        try { await video.play(); } catch (e) { /* 某些情况下不需要 play() 成功也能读取帧 */ }
        if (video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
            await new Promise(function (resolve) {
                video.addEventListener('loadeddata', resolve, { once: true });
            });
        }
        var frame = captureCanvasFrame(video, jpegQuality, true, fullResolution); // detectBlack=true
        video.srcObject = null;
        video.remove();
        return frame; // {dataUrl, width, height} or null
    }
    mod.captureFrameFromStream = captureFrameFromStream;

    // ======================== acquireOrReuseCachedStream ========================
    /**
     * 统一的流获取函数：优先缓存流 → Electron sourceId → getDisplayMedia → null
     * @param {Object} opts
     * @param {boolean} opts.allowPrompt - 是否允许 getDisplayMedia 弹窗（用户手势上下文传true）
     * @returns {Promise<MediaStream|null>}
     */
    async function acquireOrReuseCachedStream(opts) {
        if (!opts) opts = {};

        var desktopProvider = resolveDesktopCaptureProvider();
        var rememberedTitleRequired = isScreenSourceTitleMatchEnabled()
            && !!normalizeScreenSourceTitle(readRememberedWindowTitle());
        var rememberedCaptureRequired = isScreenSourceTitleMatchEnabled()
            && (rememberedTitleRequired
                || (typeof S.selectedScreenSourceId === 'string'
                    && S.selectedScreenSourceId.startsWith('window:')));
        var rememberedResolutionGeneration = screenSourceSelectionGeneration;
        var rememberedResolutionSourceId = S.selectedScreenSourceId;
        var rememberedResolutionTitle = normalizeScreenSourceTitle(
            readRememberedWindowTitle()
        );
        var currentSources = null;

        // Electron source IDs are enumeration snapshots and can later be reused for
        // another window. On providers that can enumerate without prompting, resolve
        // the remembered title before trusting either a cached stream or an ID.
        if (rememberedTitleRequired && desktopProvider
            && !desktopSourceEnumerationMayPrompt(desktopProvider)) {
            try {
                currentSources = await window.invokeDesktopCaptureWithTimeout(
                    desktopProvider,
                    'getSources',
                    [{
                        types: ['window', 'screen'],
                        thumbnailSize: { width: 0, height: 0 }
                    }]
                );
                if (rememberedResolutionGeneration !== screenSourceSelectionGeneration
                    || rememberedResolutionSourceId !== S.selectedScreenSourceId
                    || rememberedResolutionTitle !== normalizeScreenSourceTitle(
                        readRememberedWindowTitle()
                    )
                    || !isScreenSourceTitleMatchEnabled()) {
                    return null;
                }
                var selectedBeforeReconcile = S.selectedScreenSourceId;
                var rememberedResolution = reconcileRememberedWindowSource(currentSources);
                if (selectedBeforeReconcile !== S.selectedScreenSourceId && S.screenCaptureStream) {
                    try {
                        S.screenCaptureStream.getTracks().forEach(function (track) {
                            try { track.stop(); } catch (_) { }
                        });
                    } catch (_) { }
                    S.screenCaptureStream = null;
                    S.screenCaptureStreamLastUsed = null;
                }
                if (rememberedResolution.status !== 'matched') {
                    return null;
                }
            } catch (error) {
                console.warn('[acquireStream] 无法确认记忆窗口，停止本次捕获:', error);
                return null;
            }
        }

        // 1. 缓存流有效且 tracks live → 直接返回（~0ms）
        if (S.screenCaptureStream && S.screenCaptureStream.active) {
            var tracks = S.screenCaptureStream.getVideoTracks();
            if (tracks.length > 0 && tracks.some(function (t) { return t.readyState === 'live'; })) {
                S.screenCaptureStreamLastUsed = Date.now();
                scheduleScreenCaptureIdleCheck();
                return S.screenCaptureStream;
            }
            // tracks 已结束，废弃流
            console.warn('[acquireStream] 缓存流 tracks 已结束，废弃');
            try { S.screenCaptureStream.getTracks().forEach(function (t) { try { t.stop(); } catch (e) { } }); } catch (e) { }
            S.screenCaptureStream = null;
            S.screenCaptureStreamLastUsed = null;
        }

        // 2. Electron selectedScreenSourceId → getUserMedia(chromeMediaSource).
        // Native-frame providers such as Tauri do not expose a MediaStream and
        // must skip this Chromium-only branch.
        var selectedSourceId = S.selectedScreenSourceId;
        if (selectedSourceId && desktopProvider && !isNativeFrameProvider(desktopProvider)) {
            try {
                var timedOut = false;
                var acquisitionGeneration = screenSourceSelectionGeneration;
                var acquisitionSelectionId = S.selectedScreenSourceId;
                var newStream = await Promise.race([
                    (async function () {
                        var captureSourceId = selectedSourceId;
                        // Linux desktopCapturer may be backed by xdg-desktop-portal;
                        // even a "validation" enumeration can open another system
                        // sharing dialog. Trust the source selected by the preceding
                        // user gesture and let getUserMedia report a stale id instead.
                        if (!desktopSourceEnumerationMayPrompt(desktopProvider)) {
                            if (!currentSources) {
                                currentSources = await window.invokeDesktopCaptureWithTimeout(
                                    desktopProvider,
                                    'getSources',
                                    [{
                                        types: ['window', 'screen'],
                                        thumbnailSize: { width: 1, height: 1 }
                                    }]
                                );
                            }
                            var sourceExists = currentSources.some(function (s) { return s.id === selectedSourceId; });

                            if (!sourceExists) {
                                console.warn('[acquireStream] 选中的源已不可用，尝试回退到全屏源');
                                // 把失效的 ID 从 state / localStorage / 主进程一起清掉，
                                // 否则下次截图还会拿这个过期 ID 去走 Priority 1 (主进程
                                // 直接捕获 "Source not found") 和 Priority 2 的 Electron
                                // getUserMedia（会跑到 500ms 超时），整条失败链路每次重放。
                                clearSelectedScreenSource('getSources 未找到该源');
                                if (rememberedCaptureRequired) {
                                    console.warn('[acquireStream] 记忆窗口已失效，停止全屏源回退');
                                    return null;
                                }
                                acquisitionGeneration = screenSourceSelectionGeneration;
                                acquisitionSelectionId = S.selectedScreenSourceId;
                                var screenSources = currentSources.filter(function (s) { return s.id.startsWith('screen:'); });
                                if (screenSources.length > 0) {
                                    captureSourceId = screenSources[0].id;
                                } else {
                                    return null; // 无可用源
                                }
                            }
                        }

                        var stream = await navigator.mediaDevices.getUserMedia({
                            audio: false,
                            video: {
                                mandatory: {
                                    chromeMediaSource: 'desktop',
                                    chromeMediaSourceId: captureSourceId,
                                    maxFrameRate: 1
                                }
                            }
                        });
                        // 超时后晚到的流需要立即释放，防止资源泄漏
                        if (timedOut) {
                            console.warn('[acquireStream] getUserMedia 在超时后返回，释放晚到的流');
                            stream.getTracks().forEach(function (t) { t.stop(); });
                            return null;
                        }
                        if (acquisitionGeneration !== screenSourceSelectionGeneration
                            || S.selectedScreenSourceId !== acquisitionSelectionId) {
                            console.warn('[acquireStream] 来源选择已变化，释放晚到的旧流');
                            stream.getTracks().forEach(function (t) { t.stop(); });
                            return null;
                        }
                        return stream;
                    })(),
                    new Promise(function (_, reject) {
                        setTimeout(function () { timedOut = true; reject(new Error('Electron capture timeout')); }, 500);
                    })
                ]);

                if (newStream) {
                    S.screenCaptureStream = newStream;
                    S.screenCaptureStreamLastUsed = Date.now();
                    S.screenCaptureAutoPromptFailed = false;
                    scheduleScreenCaptureIdleCheck();

                    // 添加 ended 监听
                    newStream.getVideoTracks().forEach(function (track) {
                        track.addEventListener('ended', function () {
                            console.log('[acquireStream] 流被终止');
                            if (S.screenCaptureStream === newStream) {
                                S.screenCaptureStream = null;
                                S.screenCaptureStreamLastUsed = null;
                                if (S.screenCaptureStreamIdleTimer) {
                                    clearTimeout(S.screenCaptureStreamIdleTimer);
                                    S.screenCaptureStreamIdleTimer = null;
                                }
                            }
                        });
                    });

                    console.log('[acquireStream] Electron 源获取成功');
                    return newStream;
                }
            } catch (electronErr) {
                console.warn('[acquireStream] Electron 源获取失败:', electronErr.message);
            }
        }

        if (rememberedCaptureRequired) {
            console.warn('[acquireStream] 记忆窗口捕获失败，停止无约束 picker 回退');
            return null;
        }

        // 3. getDisplayMedia（仅 web/Electron 流 provider；Tauri 原生帧不支持 Chromium picker）
        if (opts.allowPrompt && !isNativeFrameProvider(desktopProvider)
            && !S.screenCaptureAutoPromptFailed &&
            navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia) {
            try {
                var pickerGeneration = screenSourceSelectionGeneration;
                var pickerSelectionId = S.selectedScreenSourceId;
                var pickerRememberedTitle = normalizeScreenSourceTitle(
                    readRememberedWindowTitle()
                );
                var pickerRememberedEnabled = isScreenSourceTitleMatchEnabled();
                var displayStream = await navigator.mediaDevices.getDisplayMedia({
                    video: { cursor: 'always', frameRate: { max: 1 } },
                    audio: false,
                });

                if (pickerGeneration !== screenSourceSelectionGeneration
                    || pickerSelectionId !== S.selectedScreenSourceId
                    || pickerRememberedTitle !== normalizeScreenSourceTitle(
                        readRememberedWindowTitle()
                    )
                    || pickerRememberedEnabled !== isScreenSourceTitleMatchEnabled()) {
                    console.warn('[acquireStream] 来源选择已变化，释放晚到的 picker 流');
                    displayStream.getTracks().forEach(function (track) {
                        try { track.stop(); } catch (_) { }
                    });
                    return null;
                }

                S.screenCaptureStream = displayStream;
                S.screenCaptureStreamLastUsed = Date.now();
                S.screenCaptureAutoPromptFailed = false;
                scheduleScreenCaptureIdleCheck();

                displayStream.getVideoTracks().forEach(function (track) {
                    track.addEventListener('ended', function () {
                        console.log('[acquireStream] getDisplayMedia 流被用户终止');
                        if (S.screenCaptureStream === displayStream) {
                            S.screenCaptureStream = null;
                            S.screenCaptureStreamLastUsed = null;
                            if (S.screenCaptureStreamIdleTimer) {
                                clearTimeout(S.screenCaptureStreamIdleTimer);
                                S.screenCaptureStreamIdleTimer = null;
                            }
                        }
                    });
                });

                console.log('[acquireStream] getDisplayMedia 获取成功');
                return displayStream;
            } catch (displayErr) {
                console.warn('[acquireStream] getDisplayMedia 失败:', displayErr);
                // 仅当非用户手势上下文时才标记自动弹窗失败，防止用户手势失败后
                // 误抑制后续用户主动触发的 getDisplayMedia 重试
                // 注意：当前 allowPrompt=true 只有用户手势上下文才会传入，
                // 所以此处不设置 screenCaptureAutoPromptFailed
            }
        }

        // 4. 返回 null，调用者自行 fallback 到 pyautogui
        return null;
    }
    mod.acquireOrReuseCachedStream = acquireOrReuseCachedStream;

    async function buildLocalSecureHeaders() {
        var helper = window.nekoLocalMutationSecurity;
        if (!helper || typeof helper.getMutationHeaders !== 'function') {
            return {};
        }
        try {
            return await helper.getMutationHeaders();
        } catch (e) {
            console.warn('[截图] 获取本地安全请求头失败:', e);
            return {};
        }
    }

    async function isLocalCsrfFailure(resp) {
        if (!resp || resp.status !== 403) return false;
        try {
            var cloned = typeof resp.clone === 'function' ? resp.clone() : resp;
            var payload = await cloned.json();
            return !!(payload && payload.error_code === 'csrf_validation_failed');
        } catch (_) {
            return false;
        }
    }

    async function secureLocalScreenshotFetch(url, options) {
        var helper = window.nekoLocalMutationSecurity;
        var requestOptions = options || {};
        var baseHeaders = Object.assign({}, requestOptions.headers);

        async function send(headers) {
            return fetch(url, {
                method: requestOptions.method || 'POST',
                headers: headers,
                body: requestOptions.body,
                cache: requestOptions.cache,
            });
        }

        var headers = Object.assign({}, baseHeaders, await buildLocalSecureHeaders());
        var resp = await send(headers);
        if (
            await isLocalCsrfFailure(resp)
            && helper
            && typeof helper.refreshToken === 'function'
        ) {
            try {
                await helper.refreshToken();
                headers = Object.assign({}, baseHeaders, await buildLocalSecureHeaders());
                resp = await send(headers);
            } catch (e) {
                console.warn('[截图] 刷新本地安全 token 失败:', e);
            }
        }
        return resp;
    }

    // ======================== fetchBackendScreenshot ========================
    /**
     * 后端单帧截图兜底：供截图、主动视觉等一次性取帧场景使用。
     * 持续屏幕分享不得轮询此接口：系统截图工具可能产生闪光/声音，而且全桌面
     * 截图无法保持用户选择的窗口来源，存在隐私语义变化。
     * 安全限制：仅当页面来自 localhost / 127.0.0.1 / 0.0.0.0 时才调用。
     * @returns {Promise<{dataUrl: string|null, status: number|null, reason: string|null}>}
     */
    async function fetchBackendScreenshot() {
        var h = window.location.hostname;
        if (h !== 'localhost' && h !== '127.0.0.1' && h !== '0.0.0.0') {
            return { dataUrl: null, status: null, reason: null };
        }
        try {
            var resp = await secureLocalScreenshotFetch('/api/screenshot', { method: 'POST' });
            var json = null;
            try {
                json = await resp.json();
            } catch (_) {
                json = null;
            }
            if (!resp.ok) {
                return {
                    dataUrl: null,
                    status: resp.status,
                    reason: (json && json.reason) ? json.reason : null
                };
            }
            if (json && json.success && json.data) {
                console.log('[截图] 后端 pyautogui 截图成功,', json.size, 'bytes');
                return { dataUrl: json.data, status: 200, reason: null };
            }
            return {
                dataUrl: null,
                status: resp.status,
                reason: (json && json.reason) ? json.reason : null
            };
        } catch (e) {
            console.warn('[截图] 后端截图请求失败:', e);
            return { dataUrl: null, status: null, reason: null };
        }
    }
    mod.fetchBackendScreenshot = fetchBackendScreenshot;

    /**
     * 后端系统原生交互截图：触发操作系统级的全桌面框选截图。
     * 仅适合用户手势触发的场景（如点击聊天截图按钮）。
     * @returns {Promise<{dataUrl: string|null, status: number|null, canceled?: boolean, error?: string|null}>}
     */
    async function fetchBackendInteractiveScreenshot() {
        var h = window.location.hostname;
        if (h !== 'localhost' && h !== '127.0.0.1' && h !== '0.0.0.0') {
            return { dataUrl: null, status: null, canceled: false, error: null };
        }
        try {
            var resp = await secureLocalScreenshotFetch('/api/screenshot/interactive', { method: 'POST' });
            var json = null;
            try {
                json = await resp.json();
            } catch (_) {
                json = null;
            }
            if (json && json.canceled) {
                console.log('[截图] 系统原生交互截图已取消');
                return { dataUrl: null, status: resp.status, canceled: true, error: null };
            }
            if (resp.ok && json && json.success && json.data) {
                console.log('[截图] 系统原生交互截图成功,', json.size, 'bytes');
                return { dataUrl: json.data, status: resp.status, canceled: false, error: null };
            }
            return {
                dataUrl: null,
                status: resp.status,
                canceled: false,
                error: (json && json.error) ? json.error : null
            };
        } catch (e) {
            console.warn('[截图] 系统原生交互截图请求失败:', e);
            return { dataUrl: null, status: null, canceled: false, error: e && e.message ? e.message : null };
        }
    }
    mod.fetchBackendInteractiveScreenshot = fetchBackendInteractiveScreenshot;

    // ======================== stopScreening ========================
    function stopScreening() {
        nativeCaptureGeneration += 1;
        activeNativeCaptureSourceId = null;
        if (S.videoSenderInterval) {
            clearInterval(S.videoSenderInterval);
            clearTimeout(S.videoSenderInterval);
            S.videoSenderInterval = null;
        }
    }
    mod.stopScreening = stopScreening;

    // ======================== syncFloatingScreenButtonState ========================
    function syncFloatingScreenButtonState(isActive) {
        // 更新所有存在的 manager 的按钮状态
        var managers = [window.live2dManager, window.vrmManager, window.mmdManager, window.pngtuberManager];

        for (var i = 0; i < managers.length; i++) {
            var manager = managers[i];
            if (!manager || !manager._floatingButtons) continue;
            var screenRef = manager._floatingButtons.screen;
            var quickRef = manager._floatingButtons['screen-share-quick'];
            if (!screenRef && !quickRef) continue;

            if (typeof manager.setButtonActive === 'function') {
                manager.setButtonActive('screen', isActive);
                continue;
            }

            if (screenRef) {
                var ref = screenRef;
                var button = ref.button;
                var imgOff = ref.imgOff;
                var imgOn = ref.imgOn;
                if (button) {
                    button.dataset.active = isActive ? 'true' : 'false';
                    if (imgOff && imgOn) {
                        imgOff.style.opacity = isActive ? '0' : '0.75';
                        imgOn.style.opacity = isActive ? '1' : '0';
                    }
                    if (typeof manager.updateSeparatePopupTriggerIcon === 'function') {
                        manager.updateSeparatePopupTriggerIcon('screen');
                    }
                }
            }
            if (quickRef && typeof quickRef.updateState === 'function') {
                quickRef.updateState(isActive);
            }
        }
    }
    mod.syncFloatingScreenButtonState = syncFloatingScreenButtonState;

    function resetScreenSharingControls() {
        var mic = micButton();
        var mute = muteButton();
        var screen = screenButton();
        var stop = stopButton();
        var reset = resetSessionButton();

        if (S.isRecording) {
            if (mic) mic.disabled = true;
            if (mute) mute.disabled = false;
            if (screen) screen.disabled = false;
            if (stop) stop.disabled = true;
            if (reset) reset.disabled = false;
        }
        if (screen) screen.classList.remove('active');
        syncFloatingScreenButtonState(false);
    }

    // ======================== buildStreamDataMessage ========================
    /**
     * 构造屏幕/相机分享的 stream_data 消息，并在适用时附带 Avatar 位置元数据。
     * 与主动搭话截图（app-proactive.js）口径保持一致：仅桌面/全屏分享叠加注解，
     * 窗口分享 / 移动相机不含 Avatar（captureType 为 null → 不附带）。
     *
     * @param {string} dataUrl 已归一化成 JPEG 的画面数据
     * @param {string} input_type 'screen' | 'camera'
     * @param {string|null} [sourceId] 原生帧的显式源 ID
     * @param {'screen'|'viewport'|null} [explicitCaptureType]
     *        调用方已经知道这一帧来自哪种画面来源时显式传入；传 null 表示
     *        「已确认无法判定」→ 不叠加。省略时按 sourceId / 缓存流推断。
     */
    function buildStreamDataMessage(dataUrl, input_type, sourceId, explicitCaptureType) {
        var msg = { action: 'stream_data', data: dataUrl, input_type: input_type };
        // 仅屏幕分享可能包含 Avatar；移动相机拍的是现实画面，无 Avatar
        if (input_type === 'screen') {
            var captureType;
            if (typeof explicitCaptureType !== 'undefined') {
                // 单帧路径的画面可能来自缓存流 / 原生帧 / 后端整屏兜底，三者互相回退。
                // 发送时的 S.screenCaptureStream / S.selectedScreenSourceId 描述的是
                // 「本会话配置抓什么」，不是「这一帧抓到了什么」，推不出来。
                captureType = explicitCaptureType;
            } else if (sourceId) {
                // 原生帧按显式源判定。
                captureType = detectScreenshotCaptureType(null, sourceId);
            } else {
                // 有前端流时按流/已选源判定；两者都没有时按全屏处理，
                // 持续屏幕分享不会再进入后端整屏截图轮询。
                captureType = S.screenCaptureStream
                    ? detectScreenshotCaptureType(S.screenCaptureStream, S.selectedScreenSourceId)
                    : 'screen';
            }
            var avatarPos = getAvatarScreenPosition(captureType);
            if (avatarPos) {
                msg.avatar_position = avatarPos;
            }
        }
        return msg;
    }
    mod.buildStreamDataMessage = buildStreamDataMessage;

    function getLiveVisionStreamBlockedReason(inputType) {
        if (inputType !== 'screen' && inputType !== 'camera') {
            return '';
        }
        if (typeof window.isNekoGoodbyeModeActive === 'function' && window.isNekoGoodbyeModeActive()) {
            return 'goodbye_active';
        }
        if (!S.isRecording) {
            return 'recording_stopped';
        }
        if (!S.voiceChatActive) {
            return 'voice_session_inactive';
        }
        return '';
    }
    mod.getLiveVisionStreamBlockedReason = getLiveVisionStreamBlockedReason;

    function canSendLiveVisionStreamFrame(inputType) {
        if (inputType !== 'screen' && inputType !== 'camera') {
            return true;
        }
        if (getLiveVisionStreamBlockedReason(inputType)) return false;
        return true;
    }
    mod.canSendLiveVisionStreamFrame = canSendLiveVisionStreamFrame;

    async function stopLiveVisionStreamIfBlocked(inputType) {
        var blockedReason = getLiveVisionStreamBlockedReason(inputType);
        if (!blockedReason) {
            return false;
        }
        await stopScreenSharing(blockedReason === 'goodbye_active');
        return true;
    }
    mod.stopLiveVisionStreamIfBlocked = stopLiveVisionStreamIfBlocked;

    // ======================== startScreenVideoStreaming ========================
    function startScreenVideoStreaming(stream, input_type) {
        var generation = nativeCaptureGeneration;

        function isCurrentStream() {
            return generation === nativeCaptureGeneration
                && stream === S.screenCaptureStream;
        }

        // 更新最后使用时间并调度闲置检查
        if (isCurrentStream()) {
            S.screenCaptureStreamLastUsed = Date.now();
            scheduleScreenCaptureIdleCheck();
        }

        var video = document.createElement('video');
        video.srcObject = stream;
        video.autoplay = true;
        video.muted = true;

        S.videoTrack = stream.getVideoTracks()[0];

        // 定时抓取当前帧并编码为jpeg（使用统一的 captureCanvasFrame）
        video.play().then(async function () {
            if (!isCurrentStream()) return;
            if (await stopLiveVisionStreamIfBlocked(input_type)) {
                return;
            }
            if (!isCurrentStream()) return;
            if (video.videoWidth && video.videoHeight) {
                var vw = video.videoWidth, vh = video.videoHeight;
                if (vw > C.MAX_SCREENSHOT_WIDTH || vh > C.MAX_SCREENSHOT_HEIGHT) {
                    var scale = Math.min(C.MAX_SCREENSHOT_WIDTH / vw, C.MAX_SCREENSHOT_HEIGHT / vh);
                    console.log('屏幕共享：原尺寸 ' + vw + 'x' + vh + ' -> 缩放到 ' + Math.round(vw * scale) + 'x' + Math.round(vh * scale));
                }
            }

            var senderInterval = setInterval(async function () {
                if (!isCurrentStream()) {
                    clearInterval(senderInterval);
                    if (S.videoSenderInterval === senderInterval) {
                        S.videoSenderInterval = null;
                    }
                    return;
                }
                if (await stopLiveVisionStreamIfBlocked(input_type)) {
                    return;
                }
                if (!isCurrentStream()) return;
                var frame = captureCanvasFrame(video, 0.8);
                if (frame && frame.dataUrl && S.socket && S.socket.readyState === WebSocket.OPEN) {
                    S.socket.send(JSON.stringify(buildStreamDataMessage(frame.dataUrl, input_type)));

                    // 刷新最后使用时间，防止活跃屏幕分享被误释放
                    if (isCurrentStream()) {
                        S.screenCaptureStreamLastUsed = Date.now();
                    }
                }
            }, 1000);
            if (!isCurrentStream()) {
                clearInterval(senderInterval);
                return;
            }
            S.videoSenderInterval = senderInterval;
        }); // 每1000ms一帧
    }
    mod.startScreenVideoStreaming = startScreenVideoStreaming;

    async function startNativeScreenStreaming(provider, sourceId, inputType) {
        stopScreening();
        var generation = nativeCaptureGeneration;
        var captureSocket = S.socket;
        activeNativeCaptureSourceId = sourceId;

        function isCurrentNativeCapture() {
            return generation === nativeCaptureGeneration
                && activeNativeCaptureSourceId === sourceId;
        }

        function isCaptureSocketOpen() {
            return !!(captureSocket
                && captureSocket === S.socket
                && captureSocket.readyState === WebSocket.OPEN);
        }

        async function captureAndSend() {
            if (!isCurrentNativeCapture()) return false;
            if (!isCaptureSocketOpen()) {
                await stopScreenSharing(true);
                return false;
            }
            if (await stopLiveVisionStreamIfBlocked(inputType)) {
                return false;
            }
            if (!isCurrentNativeCapture()) return false;
            if (!isCaptureSocketOpen()) {
                await stopScreenSharing(true);
                return false;
            }
            var result = await window.captureDesktopSourceWithTimeout(
                provider,
                'captureSourceAsDataUrl',
                sourceId,
                {
                    maxWidth: C.MAX_SCREENSHOT_WIDTH || 1280,
                    quality: 80
                }
            );
            // stop/restart/source-switch may happen while native capture awaits.
            // Never let that obsolete frame reach the replacement session.
            if (!isCurrentNativeCapture()) return false;
            if (!isCaptureSocketOpen()) {
                await stopScreenSharing(true);
                return false;
            }
            if (!result || !result.success || !result.dataUrl) {
                var errorMessage = result && result.error ? result.error : 'Screen capture failed';
                if (errorMessage === 'Source not found') {
                    clearSelectedScreenSource('原生屏幕捕获源已失效');
                }
                throw new Error(errorMessage);
            }
            var streamDataUrl = await normalizeNativeCaptureDataUrlForStream(result.dataUrl);
            if (!isCurrentNativeCapture()) return false;
            if (!isCaptureSocketOpen()) {
                await stopScreenSharing(true);
                return false;
            }
            if (!streamDataUrl) {
                throw new Error('Native screen capture image conversion failed');
            }
            if (canSendLiveVisionStreamFrame(inputType) && isCaptureSocketOpen()) {
                captureSocket.send(JSON.stringify(
                    buildStreamDataMessage(streamDataUrl, inputType, sourceId)
                ));
            } else if (isCurrentNativeCapture()) {
                stopScreening();
                return false;
            }
            return true;
        }

        // Wait for the first frame so permission and stale-source failures are
        // reported by the user-initiated start action.
        var firstFrameSent;
        try {
            firstFrameSent = await captureAndSend();
        } catch (error) {
            if (isCurrentNativeCapture()) {
                stopScreening();
            }
            throw error;
        }
        if (!firstFrameSent) {
            return false;
        }

        async function scheduleNextFrame() {
            if (generation !== nativeCaptureGeneration) return;
            try {
                var shouldContinue = await captureAndSend();
                if (!shouldContinue) return;
            } catch (error) {
                console.warn('[屏幕源] 原生帧捕获失败:', error);
                if (generation === nativeCaptureGeneration) {
                    await stopScreenSharing(true);
                    window.showStatusToast(
                        safeT(
                            'app.screenSource.captureFailed',
                            '屏幕捕获已停止，请检查系统权限或重新选择来源'
                        ),
                        5000
                    );
                }
                return;
            }
            if (generation === nativeCaptureGeneration) {
                S.videoSenderInterval = setTimeout(scheduleNextFrame, 1000);
            }
        }

        S.videoSenderInterval = setTimeout(scheduleNextFrame, 1000);
        return true;
    }
    mod.startNativeScreenStreaming = startNativeScreenStreaming;

    function restartActiveNativeCaptureForSourceRemap(previousSourceId, nextSourceId) {
        if (!previousSourceId || !nextSourceId || previousSourceId === nextSourceId
            || activeNativeCaptureSourceId !== previousSourceId) {
            return;
        }

        var provider = resolveDesktopCaptureProvider();
        var pendingStartWasActive = typeof isScreenSharingStartPending === 'function'
            && isScreenSharingStartPending();
        stopScreening();
        if (!isNativeFrameProvider(provider)) return;

        if (pendingStartWasActive) {
            cancelPendingScreenSharingStart();
            Promise.resolve(startScreenSharing()).catch(function (error) {
                console.warn('[屏幕源] 标题恢复后重启原生捕获失败:', error);
                stopScreenSharing(true);
                window.showStatusToast(
                    safeT(
                        'app.screenSource.captureFailed',
                        '屏幕捕获已停止，请检查系统权限或重新选择来源'
                    ),
                    5000
                );
            });
            return;
        }

        Promise.resolve(startNativeScreenStreaming(provider, nextSourceId, 'screen'))
            .catch(async function (error) {
                console.warn('[屏幕源] 标题恢复后重启原生捕获失败:', error);
                await stopScreenSharing(true);
                window.showStatusToast(
                    safeT(
                        'app.screenSource.captureFailed',
                        '屏幕捕获已停止，请检查系统权限或重新选择来源'
                    ),
                    5000
                );
            });
    }

    function releaseActiveScreenCaptureForSourceChange() {
        stopScreening();
        if (S.screenCaptureStream) {
            try {
                if (typeof S.screenCaptureStream.getTracks === 'function') {
                    S.screenCaptureStream.getTracks().forEach(function (track) {
                        try { track.stop(); } catch (_) { }
                    });
                }
            } catch (_) { }
        }
        S.screenCaptureStream = null;
        S.screenCaptureStreamLastUsed = null;
        if (S.screenCaptureStreamIdleTimer) {
            clearTimeout(S.screenCaptureStreamIdleTimer);
            S.screenCaptureStreamIdleTimer = null;
        }
        resetScreenSharingControls();
    }

    function isScreenSharingActiveForSourceChange() {
        if (activeNativeCaptureSourceId || S.videoSenderInterval) return true;
        var stop = stopButton();
        if (stop && !stop.disabled) return true;
        var screen = screenButton();
        return !!(screen && screen.classList && screen.classList.contains('active'));
    }

    function restartActiveCaptureForSourceRemap(previousSourceId, nextSourceId) {
        if (!previousSourceId || !nextSourceId || previousSourceId === nextSourceId) return;

        if (activeNativeCaptureSourceId === previousSourceId) {
            restartActiveNativeCaptureForSourceRemap(previousSourceId, nextSourceId);
            return;
        }

        if (!S.screenCaptureStream) return;
        var shouldRestart = isScreenSharingActiveForSourceChange();
        releaseActiveScreenCaptureForSourceChange();
        if (!shouldRestart) return;
        Promise.resolve(startScreenSharing()).catch(function (error) {
            console.warn('[屏幕源] 标题恢复后重启流捕获失败:', error);
            window.showStatusToast(
                safeT(
                    'app.screenSource.captureFailed',
                    '屏幕捕获已停止，请检查系统权限或重新选择来源'
                ),
                5000
            );
        });
    }

    function stopActiveCaptureForRememberedSourceRejection() {
        if (!isScreenSharingActiveForSourceChange() && !S.screenCaptureStream) return;
        releaseActiveScreenCaptureForSourceChange();
    }

    // ======================== getMobileCameraStream ========================
    async function getMobileCameraStream() {
        var makeConstraints = function (facing) {
            return {
                video: {
                    facingMode: facing,
                    frameRate: { ideal: 1, max: 1 },
                },
                audio: false,
            };
        };

        var attempts = [
            { label: 'rear', constraints: makeConstraints({ ideal: 'environment' }) },
            { label: 'front', constraints: makeConstraints('user') },
            { label: 'any', constraints: { video: { frameRate: { ideal: 1, max: 1 } }, audio: false } },
        ];

        var lastError;

        for (var i = 0; i < attempts.length; i++) {
            var attempt = attempts[i];
            try {
                console.log((window.t('console.tryingCamera')) + ' ' + attempt.label + ' ' + (window.t('console.cameraLabel')) + ' 1' + (window.t('console.cameraFps')));
                return await navigator.mediaDevices.getUserMedia(attempt.constraints);
            } catch (err) {
                console.warn(attempt.label + ' ' + (window.t('console.cameraFailed')), err);
                lastError = err;
            }
        }

        if (lastError) {
            window.showStatusToast(lastError.toString(), 4000);
            throw lastError;
        }
    }
    mod.getMobileCameraStream = getMobileCameraStream;

    // ======================== startScreenSharing ========================
    // 所有入口共享同一次启动尝试，避免授权弹窗未返回时重复创建捕获流。
    // attempt 上的 cancelled 标记让“停止”可以否决尚未返回的系统授权弹窗；
    // getDisplayMedia 本身不可中断，因此晚到的流会在返回后立即释放。
    var screenSharingStartAttempt = null;

    function isScreenSharingStartPending() {
        return !!screenSharingStartAttempt && !screenSharingStartAttempt.cancelled;
    }
    mod.isScreenSharingStartPending = isScreenSharingStartPending;

    function cancelPendingScreenSharingStart() {
        var attempt = screenSharingStartAttempt;
        if (!attempt) return false;

        attempt.cancelled = true;
        // If acquisition already completed but activation is still awaiting a
        // guard, release that attempt's stream before another start can reuse it.
        discardCancelledScreenSharingStart(attempt);
        // Detach immediately so the user can retry without waiting for an
        // already-open browser chooser that JavaScript cannot dismiss.
        if (screenSharingStartAttempt === attempt) {
            screenSharingStartAttempt = null;
        }
        return true;
    }
    mod.cancelPendingScreenSharingStart = cancelPendingScreenSharingStart;

    function rememberScreenSharingAttemptStream(attempt, stream) {
        if (attempt && stream && stream !== attempt.initialStream) {
            attempt.acquiredStream = stream;
        }
        return stream;
    }

    function discardCancelledScreenSharingStart(attempt) {
        if (!attempt || !attempt.cancelled) {
            return false;
        }

        var stream = attempt.acquiredStream;
        if (stream && stream !== attempt.initialStream) {
            try {
                var videoTrack = stream.getVideoTracks && stream.getVideoTracks()[0];
                if (videoTrack) videoTrack.onended = null;
                if (typeof stream.getTracks === 'function') {
                    stream.getTracks().forEach(function (track) {
                        try { track.stop(); } catch (e) { }
                    });
                }
            } catch (e) {
                console.warn(
                    safeT('console.screenShareStopTracksFailed', '屏幕共享停止轨道失败'),
                    e
                );
            }

            if (S.screenCaptureStream === stream) {
                S.screenCaptureStream = attempt.initialStream || null;
                S.screenCaptureStreamLastUsed = null;
                if (S.screenCaptureStreamIdleTimer) {
                    clearTimeout(S.screenCaptureStreamIdleTimer);
                    S.screenCaptureStreamIdleTimer = null;
                }
            }
            attempt.acquiredStream = null;
        }
        return true;
    }

    async function startScreenSharing() {
        if (isScreenSharingStartPending()) {
            return screenSharingStartAttempt.promise;
        }
        // Defensive cleanup for attempts created before immediate detaching was
        // introduced. Their own finally/cleanup still retains the attempt object.
        if (screenSharingStartAttempt && screenSharingStartAttempt.cancelled) {
            screenSharingStartAttempt = null;
        }

        var attempt = {
            cancelled: false,
            initialStream: S.screenCaptureStream,
            acquiredStream: null,
            promise: null
        };
        attempt.promise = startScreenSharingOnce(attempt);
        screenSharingStartAttempt = attempt;
        try {
            return await attempt.promise;
        } finally {
            if (screenSharingStartAttempt === attempt) {
                screenSharingStartAttempt = null;
            }
        }
    }

    async function startScreenSharingOnce(attempt) {
        // 检查是否在录音状态
        if (!S.isRecording) {
            window.showStatusToast(window.t ? window.t('app.micRequired') : '请先开启麦克风录音！', 3000);
            return;
        }

        var windowsGraphicsCapturePrompted = false;
        try {
            var nativeCapture = null;
            // Capture into a local reference first. A cancelled browser picker may
            // return after proactive vision has already installed another stream;
            // it must never overwrite that newer global stream.
            var captureStream = attempt.initialStream;

            // 初始化音频播放上下文
            await ensureModelVisibleForScreenSharing();
            if (discardCancelledScreenSharingStart(attempt)) return;
            if (typeof window.ensureAudioPlayerContext === 'function') {
                await window.ensureAudioPlayerContext();
            } else if (!S.audioPlayerContext) {
                // Backward-compatible fallback for isolated route/test harnesses.
                S.audioPlayerContext = new (window.AudioContext || window.webkitAudioContext)();
                if (typeof window.syncAudioGlobals === 'function') {
                    window.syncAudioGlobals();
                }
            }
            if (discardCancelledScreenSharingStart(attempt)) return;

            // 如果上下文被暂停，则恢复它
            if (S.audioPlayerContext.state === 'suspended') {
                await S.audioPlayerContext.resume();
                if (discardCancelledScreenSharingStart(attempt)) return;
            }

            // A stream retained by proactive vision belongs to the source identity
            // that acquired it. Validate that remembered-window identity before a
            // manual share adopts the stream and starts its sender lifecycle.
            if (captureStream && !isMobile()) {
                var initialRememberedCapture = await prepareRememberedWindowCapture();
                if (discardCancelledScreenSharingStart(attempt)) return;
                if (initialRememberedCapture.required) {
                    var initialCaptureIsCurrent = typeof initialRememberedCapture.isCurrent !== 'function'
                        || initialRememberedCapture.isCurrent();
                    var initialStreamStillOwned = S.screenCaptureStream === captureStream;
                    if (!initialRememberedCapture.allowed
                        || !initialCaptureIsCurrent
                        || !initialStreamStillOwned) {
                        if (initialStreamStillOwned) {
                            releaseActiveScreenCaptureForSourceChange();
                        } else {
                            try {
                                captureStream.getTracks().forEach(function (track) {
                                    try { track.stop(); } catch (_) { }
                                });
                            } catch (_) { }
                        }
                        attempt.initialStream = null;
                        captureStream = null;
                        if (!initialRememberedCapture.allowed || !initialCaptureIsCurrent) {
                            window.showStatusToast(
                                safeT(
                                    'app.screenSource.rememberedWindowUnavailable',
                                    '无法唯一找到记住的窗口，请重新选择屏幕来源'
                                ),
                                4000
                            );
                            return;
                        }
                        var replacementStream = S.screenCaptureStream;
                        if (replacementStream && replacementStream.active) {
                            var replacementTracks = replacementStream.getVideoTracks();
                            if (replacementTracks.some(function (track) {
                                return track.readyState === 'live';
                            })) {
                                attempt.initialStream = replacementStream;
                                captureStream = replacementStream;
                            }
                        }
                    }
                }
            }

            if (captureStream == null) {
                if (isMobile()) {
                    // 移动端使用摄像头
                    var tmp = await getMobileCameraStream();
                    if (tmp instanceof MediaStream) {
                        captureStream = rememberScreenSharingAttemptStream(attempt, tmp);
                    } else {
                        // 保持原有错误处理路径：让 catch 去接手
                        throw (tmp instanceof Error ? tmp : new Error('无法获取摄像头流'));
                    }
                } else {

                    // Desktop/laptop: capture the user's chosen screen / window / tab.
                    var selectedSourceId = window.getSelectedScreenSourceId ? window.getSelectedScreenSourceId() : null;
                    var desktopProvider = resolveDesktopCaptureProvider();
                    var sourceEnumerationMayPrompt = desktopSourceEnumerationMayPrompt(desktopProvider);
                    var rememberedWindowNeedsSelection = false;
                    var hasRememberedWindowTitle = isScreenSourceTitleMatchEnabled()
                        && !!normalizeScreenSourceTitle(readRememberedWindowTitle());

                    // Native-frame shells do not expose Chromium's picker.
                    // Default to the first monitor when no source is persisted.
                    if (!selectedSourceId && !hasRememberedWindowTitle
                        && isNativeFrameProvider(desktopProvider)) {
                        try {
                            var initialScreens = await desktopProvider.getSources({ types: ['screen'] });
                            if (initialScreens && initialScreens.length > 0) {
                                selectedSourceId = initialScreens[0].id;
                                S.selectedScreenSourceId = selectedSourceId;
                                try { localStorage.setItem('selectedScreenSourceId', selectedSourceId); } catch (e) { }
                                updateScreenSourceListSelection();
                            }
                        } catch (initialSourceError) {
                            console.warn('[屏幕源] 无法取得原生默认屏幕源:', initialSourceError);
                        }
                        if (discardCancelledScreenSharingStart(attempt)) return;
                    }

                    var rememberedWindowWasBounded = isScreenSourceTitleMatchEnabled()
                        && (hasRememberedWindowTitle
                            || (typeof selectedSourceId === 'string'
                                && selectedSourceId.startsWith('window:')));
                    if (rememberedWindowWasBounded && sourceEnumerationMayPrompt) {
                        var promptedRememberedCapture = await prepareRememberedWindowCapture();
                        if (discardCancelledScreenSharingStart(attempt)) return;
                        var promptedCaptureIsCurrent = typeof promptedRememberedCapture.isCurrent !== 'function'
                            || promptedRememberedCapture.isCurrent();
                        if (promptedRememberedCapture.required
                            && (!promptedRememberedCapture.allowed || !promptedCaptureIsCurrent)) {
                            window.showStatusToast(
                                safeT(
                                    'app.screenSource.rememberedWindowUnavailable',
                                    '无法确认之前选择的窗口，请重新选择屏幕来源'
                                ),
                                5000
                            );
                            return;
                        }
                        selectedSourceId = S.selectedScreenSourceId;
                    }
                    if ((selectedSourceId || hasRememberedWindowTitle)
                        && desktopProvider && !sourceEnumerationMayPrompt
                        && typeof desktopProvider.getSources === 'function') {
                        // 验证选中的源是否仍然存在（窗口可能已关闭）
                        try {
                            var manualResolutionGeneration = screenSourceSelectionGeneration;
                            var manualResolutionSourceId = S.selectedScreenSourceId;
                            var manualResolutionTitle = normalizeScreenSourceTitle(
                                readRememberedWindowTitle()
                            );
                            var manualResolutionEnabled = isScreenSourceTitleMatchEnabled();
                            var currentSources = await window.invokeDesktopCaptureWithTimeout(
                                desktopProvider,
                                'getSources',
                                [{
                                    types: ['window', 'screen'],
                                    thumbnailSize: { width: 0, height: 0 }
                                }]
                            );
                            if (manualResolutionGeneration !== screenSourceSelectionGeneration
                                || manualResolutionSourceId !== S.selectedScreenSourceId
                                || manualResolutionTitle !== normalizeScreenSourceTitle(
                                    readRememberedWindowTitle()
                                )
                                || manualResolutionEnabled !== isScreenSourceTitleMatchEnabled()) {
                                console.warn('[屏幕源] 来源选择已变化，停止过期的屏幕分享启动');
                                return;
                            }
                            var titleResolution = reconcileRememberedWindowSource(currentSources);
                            if (titleResolution.status === 'adopted-current-window') {
                                // This attempt is already constrained to the explicitly
                                // selected window even if persisting its title failed.
                                // Never widen its acquisition fallback to a monitor/picker.
                                hasRememberedWindowTitle = true;
                            }
                            selectedSourceId = S.selectedScreenSourceId;
                            var sourceStillExists = currentSources.some(function (s) { return s.id === selectedSourceId; });
                            var rememberedWindowNeedsPicker = titleResolution.hadRememberedTitle
                                && titleResolution.status !== 'matched';
                            if (rememberedWindowWasBounded
                                && titleResolution.status !== 'matched'
                                && titleResolution.status !== 'adopted-current-window') {
                                rememberedWindowNeedsPicker = true;
                            }

                            if (!sourceStillExists && !rememberedWindowNeedsPicker) {
                                console.warn('[屏幕源] 选中的源已不可用 (ID:', selectedSourceId, ')，自动回退到全屏');
                                window.showStatusToast(
                                    safeT('app.screenSource.sourceLost', '屏幕分享无法找到之前选择窗口，已切换为全屏分享'),
                                    3000
                                );
                                // 查找第一个全屏源作为回退
                                var screenSources = currentSources.filter(function (s) { return s.id.startsWith('screen:'); });
                                if (screenSources.length > 0) {
                                    selectedSourceId = screenSources[0].id;
                                    S.selectedScreenSourceId = selectedSourceId;
                                    try { localStorage.setItem('selectedScreenSourceId', selectedSourceId); } catch (e) { }
                                    pushSelectedSourceToMain(selectedSourceId);
                                    updateScreenSourceListSelection();
                                } else {
                                    // 连全屏源都拿不到，清空选择让下面走 getDisplayMedia
                                    selectedSourceId = null;
                                    S.selectedScreenSourceId = null;
                                    try { localStorage.removeItem('selectedScreenSourceId'); } catch (e) { }
                                    pushSelectedSourceToMain(null);
                                }
                            } else if (rememberedWindowNeedsPicker) {
                                selectedSourceId = null;
                                rememberedWindowNeedsSelection = true;
                                console.warn('[屏幕源] 记住的窗口标题无法唯一匹配，停止本次启动并等待用户重新选择');
                            }
                        } catch (validateErr) {
                            if (manualResolutionGeneration !== screenSourceSelectionGeneration
                                || manualResolutionSourceId !== S.selectedScreenSourceId
                                || manualResolutionTitle !== normalizeScreenSourceTitle(
                                    readRememberedWindowTitle()
                                )
                                || manualResolutionEnabled !== isScreenSourceTitleMatchEnabled()) {
                                console.warn('[屏幕源] 来源选择已变化，停止过期的屏幕分享启动');
                                return;
                            }
                            if (rememberedWindowWasBounded) {
                                selectedSourceId = null;
                                rememberedWindowNeedsSelection = true;
                                console.warn('[屏幕源] 记忆窗口来源验证失败，停止本次启动:', validateErr);
                            } else {
                                console.warn('[屏幕源] 验证源可用性失败，继续尝试使用保存的源:', validateErr);
                            }
                        }
                        if (discardCancelledScreenSharingStart(attempt)) return;
                    }

                    if (rememberedWindowNeedsSelection) {
                        window.showStatusToast(
                            safeT(
                                'app.screenSource.rememberedWindowUnavailable',
                                '无法唯一找到记住的窗口，请重新选择屏幕来源'
                            ),
                            4000
                        );
                        return;
                    }

                    if (selectedSourceId && isNativeFrameProvider(desktopProvider)) {
                        nativeCapture = {
                            provider: desktopProvider,
                            sourceId: selectedSourceId
                        };
                        console.log('[屏幕源] 使用原生帧捕获源:', selectedSourceId);
                    } else if (selectedSourceId && desktopProvider) {
                        // Electron uses the selected Chromium desktop source.
                        var manualCaptureGeneration = screenSourceSelectionGeneration;
                        var manualCaptureSourceId = S.selectedScreenSourceId;
                        var manualCaptureTitle = normalizeScreenSourceTitle(
                            readRememberedWindowTitle()
                        );
                        var manualCaptureEnabled = isScreenSourceTitleMatchEnabled();
                        function manualCaptureIdentityIsCurrent() {
                            return manualCaptureGeneration === screenSourceSelectionGeneration
                                && manualCaptureSourceId === S.selectedScreenSourceId
                                && manualCaptureTitle === normalizeScreenSourceTitle(
                                    readRememberedWindowTitle()
                                )
                                && manualCaptureEnabled === isScreenSourceTitleMatchEnabled();
                        }
                        function discardSupersededManualCapture() {
                            if (manualCaptureIdentityIsCurrent()) return false;
                            console.warn('[屏幕源] 来源选择已变化，释放晚到的屏幕分享流');
                            attempt.cancelled = true;
                            discardCancelledScreenSharingStart(attempt);
                            return true;
                        }
                        try {
                            captureStream = rememberScreenSharingAttemptStream(attempt, await navigator.mediaDevices.getUserMedia({
                                audio: false,
                                video: {
                                    mandatory: {
                                        chromeMediaSource: 'desktop',
                                        chromeMediaSourceId: selectedSourceId,
                                        maxFrameRate: 1
                                    }
                                }
                            }));
                            if (discardSupersededManualCapture()) return;
                        } catch (captureErr) {
                            if (discardCancelledScreenSharingStart(attempt)) return;
                            if (!manualCaptureIdentityIsCurrent()) {
                                console.warn('[屏幕源] 来源选择已变化，忽略过期的屏幕分享失败');
                                return;
                            }
                            console.warn('[屏幕源] 指定源捕获失败:', captureErr);
                            var fallbackSucceeded = false;

                            // Remember-window is a fail-closed privacy boundary: once
                            // the named window was resolved, an acquisition failure must
                            // not silently widen capture to a monitor or unconstrained picker.
                            if (hasRememberedWindowTitle) {
                                console.warn('[屏幕源] 记忆窗口捕获失败，停止本次启动而不扩大捕获范围');
                            } else if (!sourceEnumerationMayPrompt) {
                                // 回退策略1: 非 Portal 平台可静默枚举其它全屏源。
                                // Linux Portal 每次枚举都可能再次弹系统窗口，因此直接进入
                                // 一次 getDisplayMedia，让用户重新选择来源。
                                try {
                                    var fallbackSources = await desktopProvider.getSources({
                                        types: ['screen'],
                                        thumbnailSize: { width: 1, height: 1 }
                                    });
                                    if (discardCancelledScreenSharingStart(attempt)) return;
                                    if (!manualCaptureIdentityIsCurrent()) return;
                                    if (fallbackSources.length > 0) {
                                        captureStream = rememberScreenSharingAttemptStream(attempt, await navigator.mediaDevices.getUserMedia({
                                            audio: false,
                                            video: {
                                                mandatory: {
                                                    chromeMediaSource: 'desktop',
                                                    chromeMediaSourceId: fallbackSources[0].id,
                                                    maxFrameRate: 1
                                                }
                                            }
                                        }));
                                        if (discardCancelledScreenSharingStart(attempt)) return;
                                        if (discardSupersededManualCapture()) return;
                                        S.selectedScreenSourceId = fallbackSources[0].id;
                                        try { localStorage.setItem('selectedScreenSourceId', fallbackSources[0].id); } catch (e) { }
                                        pushSelectedSourceToMain(fallbackSources[0].id);
                                        window.showStatusToast(
                                            safeT('app.screenSource.sourceLost', '屏幕分享无法找到之前选择窗口，已切换为全屏分享'),
                                            3000
                                        );
                                        fallbackSucceeded = true;
                                    }
                                } catch (fallback1Err) {
                                    if (!manualCaptureIdentityIsCurrent()) return;
                                    console.warn('[屏幕源] chromeMediaSource 全屏回退也失败:', fallback1Err);
                                }
                            }

                            // 回退策略2: chromeMediaSource 在该系统上完全不可用，降级到 getDisplayMedia
                            if (!hasRememberedWindowTitle && !fallbackSucceeded) {
                                if (discardCancelledScreenSharingStart(attempt)) return;
                                if (!manualCaptureIdentityIsCurrent()) return;
                                try {
                                    console.log('[屏幕源] chromeMediaSource 不可用，降级到 getDisplayMedia');
                                    captureStream = rememberScreenSharingAttemptStream(attempt, await navigator.mediaDevices.getDisplayMedia({
                                        video: { cursor: 'always', frameRate: 1 },
                                        audio: false,
                                    }));
                                    if (discardCancelledScreenSharingStart(attempt)) return;
                                    if (discardSupersededManualCapture()) return;
                                    S.selectedScreenSourceId = null;
                                    try { localStorage.removeItem('selectedScreenSourceId'); } catch (e) { }
                                    pushSelectedSourceToMain(null);
                                    fallbackSucceeded = true;
                                } catch (fallback2Err) {
                                    if (discardCancelledScreenSharingStart(attempt)) return;
                                    if (!manualCaptureIdentityIsCurrent()) return;
                                    // 用户关闭系统选择器时沿用标准路径的取消语义，
                                    // 不要把此前的指定源失败误报为 WGC 故障。
                                    if (fallback2Err.name === 'NotAllowedError') throw fallback2Err;
                                    console.warn('[屏幕源] getDisplayMedia 回退也失败:', fallback2Err);
                                }
                            }

                            if (!fallbackSucceeded) {
                                var windowsGraphicsCaptureFallback = await requestWindowsGraphicsCaptureFallback(
                                    desktopProvider,
                                    captureErr,
                                    selectedSourceId
                                );
                                windowsGraphicsCapturePrompted = !!(
                                    windowsGraphicsCaptureFallback
                                    && windowsGraphicsCaptureFallback.prompted
                                );
                                if (windowsGraphicsCaptureFallback
                                    && windowsGraphicsCaptureFallback.restartApproved === true) {
                                    if (discardCancelledScreenSharingStart(attempt)) return;
                                    if (!manualCaptureIdentityIsCurrent()) return;
                                    windowsGraphicsCaptureFallback = await confirmWindowsGraphicsCaptureFallback(
                                        desktopProvider,
                                        windowsGraphicsCaptureFallback
                                    );
                                    windowsGraphicsCapturePrompted = !!(
                                        windowsGraphicsCaptureFallback
                                        && windowsGraphicsCaptureFallback.prompted
                                    );
                                }
                                if (windowsGraphicsCaptureFallback && windowsGraphicsCaptureFallback.restarting) {
                                    return;
                                }
                                console.warn('[屏幕源] 所有前端持续流方式均失败，停止屏幕分享');
                            }
                        }
                        if (captureStream) {
                            console.log(window.t('console.screenShareUsingSource'), selectedSourceId);
                        }
                    } else if (!isNativeFrameProvider(desktopProvider)) {
                        // 使用标准的getDisplayMedia（显示系统选择器）
                        var displayCaptureGeneration = screenSourceSelectionGeneration;
                        try {
                            captureStream = rememberScreenSharingAttemptStream(attempt, await navigator.mediaDevices.getDisplayMedia({
                                video: {
                                    cursor: 'always',
                                    frameRate: 1,
                                },
                                audio: false,
                            }));
                        } catch (displayErr) {
                            if (discardCancelledScreenSharingStart(attempt)) return;
                            // 用户主动取消则直接抛出，不兜底
                            if (displayErr.name === 'NotAllowedError') throw displayErr;
                            console.warn('[屏幕源] getDisplayMedia 失败，停止屏幕分享:', displayErr);
                            var displayWgcFallback = await requestWindowsGraphicsCaptureFallback(
                                desktopProvider,
                                displayErr,
                                null
                            );
                            windowsGraphicsCapturePrompted = !!(
                                displayWgcFallback
                                && displayWgcFallback.prompted
                            );
                            if (displayWgcFallback && displayWgcFallback.restartApproved === true) {
                                if (discardCancelledScreenSharingStart(attempt)) return;
                                if (displayCaptureGeneration !== screenSourceSelectionGeneration) return;
                                displayWgcFallback = await confirmWindowsGraphicsCaptureFallback(
                                    desktopProvider,
                                    displayWgcFallback
                                );
                                windowsGraphicsCapturePrompted = !!(
                                    displayWgcFallback
                                    && displayWgcFallback.prompted
                                );
                            }
                            if (displayWgcFallback && displayWgcFallback.restarting) {
                                return;
                            }
                        }
                    }
                }
            }

            if (discardCancelledScreenSharingStart(attempt)) return;
            if (captureStream !== attempt.initialStream) {
                S.screenCaptureStream = captureStream;
            }

            if (nativeCapture) {
                var nativeStreamStarted = await startNativeScreenStreaming(
                    nativeCapture.provider,
                    nativeCapture.sourceId,
                    'screen'
                );
                if (discardCancelledScreenSharingStart(attempt)) return;
                if (!nativeStreamStarted) {
                    return;
                }
            } else if (captureStream) {
                // 用户手势成功获取了流，重置自动弹窗失败标记
                S.screenCaptureAutoPromptFailed = false;
                // 正常流模式
                if (S.screenCaptureStream === captureStream) {
                    S.screenCaptureStreamLastUsed = Date.now();
                    scheduleScreenCaptureIdleCheck();
                }

                var streamInputType = isMobile() ? 'camera' : 'screen';
                if (await stopLiveVisionStreamIfBlocked(streamInputType)) {
                    return;
                }
                if (discardCancelledScreenSharingStart(attempt)) return;
                if (S.screenCaptureStream !== captureStream) return;
                startScreenVideoStreaming(captureStream, streamInputType);

                // 当用户停止共享屏幕时
                captureStream.getVideoTracks()[0].onended = function () {
                    if (S.screenCaptureStream !== captureStream) {
                        if (typeof captureStream.getTracks === 'function') {
                            captureStream.getTracks().forEach(function (track) {
                                try { track.stop(); } catch (e) { }
                            });
                        }
                        return;
                    }

                    stopScreening();
                    screenButton().classList.remove('active');
                    syncFloatingScreenButtonState(false);

                    if (typeof captureStream.getTracks === 'function') {
                        captureStream.getTracks().forEach(function (track) {
                            try { track.stop(); } catch (e) { }
                        });
                    }

                    if (S.screenCaptureStream === captureStream) {
                        S.screenCaptureStream = null;
                        S.screenCaptureStreamLastUsed = null;

                        if (S.screenCaptureStreamIdleTimer) {
                            clearTimeout(S.screenCaptureStreamIdleTimer);
                            S.screenCaptureStreamIdleTimer = null;
                        }
                    }
                };
            } else {
                // 连续分享必须保持系统/窗口选择器授予的来源。后端 pyautogui 只能
                // 截取整个桌面，还可能调用带闪光和声音的系统截图工具；在这里静默
                // 降级既会打扰用户，也可能发送用户没有选择的其它窗口。
                var streamError = new Error(safeT(
                    'app.screenSource.captureFailed',
                    '屏幕捕获已停止，请检查系统权限或重新选择来源'
                ));
                streamError.name = 'NotReadableError';
                throw streamError;
            }

            if (discardCancelledScreenSharingStart(attempt)) return;

            micButton().disabled = true;
            muteButton().disabled = false;
            screenButton().disabled = true;
            stopButton().disabled = false;
            resetSessionButton().disabled = false;

            screenButton().classList.add('active');
            syncFloatingScreenButtonState(true);

            if (window.unlockAchievement) {
                window.unlockAchievement('ACH_SEND_IMAGE').catch(function (err) {
                    console.error('解锁发送图片成就失败:', err);
                });
            }

            try {
                if (window.stopProactiveVisionDuringSpeech) {
                    window.stopProactiveVisionDuringSpeech();
                }
            } catch (e) {
                console.warn(window.t('console.stopVoiceActiveVisionFailed'), e);
            }

            if (!S.isRecording) window.showStatusToast(window.t ? window.t('app.micNotOpen') : '没开麦啊喂！', 3000);
        } catch (err) {
            if (discardCancelledScreenSharingStart(attempt)) return;
            console.error(isMobile() ? window.t('console.cameraAccessFailed') : window.t('console.screenShareFailed'), err);
            console.error(window.t('console.startupFailed'), err);
            var hint = '';
            var isDesktop = !isMobile();
            switch (err.name) {
                case 'NotAllowedError':
                    hint = isDesktop
                        ? '用户取消了屏幕共享，或系统未授予屏幕录制权限'
                        : '请检查 iOS 设置 → Safari → 摄像头 权限是否为"允许"';
                    break;
                case 'NotFoundError':
                    hint = isDesktop ? '未检测到可用的屏幕源' : '未检测到摄像头设备';
                    break;
                case 'NotReadableError':
                case 'AbortError':
                    hint = isDesktop
                        ? '屏幕捕获启动失败，可能与显卡驱动或系统权限有关，请尝试重启应用'
                        : '摄像头被其它应用占用？关闭扫码/拍照应用后重试';
                    break;
            }
            if (!hint && isDesktop && isNativeFrameProvider(resolveDesktopCaptureProvider())) {
                hint = safeT(
                    'app.screenSource.captureFailed',
                    '屏幕捕获已停止，请检查系统权限或重新选择来源'
                );
            }
            if (!windowsGraphicsCapturePrompted) {
                window.showStatusToast(err.name + ': ' + err.message + (hint ? '\n' + hint : ''), 5000);
            }
        }
    }
    mod.startScreenSharing = startScreenSharing;

    // ======================== stopScreenSharing ========================
    /**
     * 停止屏幕分享。
     * @param {boolean} forceRelease - 是否强制释放流。false时若主动视觉仍活跃则保留缓存流。
     */
    async function stopScreenSharing(forceRelease) {
        cancelPendingScreenSharingStart();
        stopScreening();

        // 判断主动视觉是否活跃
        var proactiveVisionActive = S.proactiveVisionEnabled && (
            S.isRecording || (S.proactiveVisionChatEnabled && S.proactiveChatEnabled)
        );

        // 条件释放流
        if (forceRelease || !proactiveVisionActive) {
            // 完全释放流
            try {
                if (S.screenCaptureStream && typeof S.screenCaptureStream.getTracks === 'function') {
                    var vt = S.screenCaptureStream.getVideoTracks && S.screenCaptureStream.getVideoTracks()[0];
                    if (vt) {
                        vt.onended = null;
                    }
                    S.screenCaptureStream.getTracks().forEach(function (track) {
                        try { track.stop(); } catch (e) { }
                    });
                }
            } catch (e) {
                console.warn(window.t('console.screenShareStopTracksFailed'), e);
            } finally {
                S.screenCaptureStream = null;
                S.screenCaptureStreamLastUsed = null;
                if (S.screenCaptureStreamIdleTimer) {
                    clearTimeout(S.screenCaptureStreamIdleTimer);
                    S.screenCaptureStreamIdleTimer = null;
                }
            }
        } else {
            // 主动视觉仍活跃，保留缓存流，仅停止发送和 UI
            console.log('[屏幕分享] 主动视觉仍活跃，保留缓存流');
        }

        // 仅在主动录像/语音连接分享时更新禁用状态；任何情况下都移除分享样式。
        resetScreenSharingControls();

        // 停止手动屏幕共享后，如果满足条件则恢复语音期间主动视觉定时
        try {
            if (S.proactiveVisionEnabled && S.isRecording) {
                if (window.startProactiveVisionDuringSpeech) {
                    window.startProactiveVisionDuringSpeech();
                }
            }
        } catch (e) {
            console.warn(window.t('console.resumeVoiceActiveVisionFailed'), e);
        }
    }
    mod.stopScreenSharing = stopScreenSharing;

    // ======================== switchMicCapture ========================
    window.switchMicCapture = async function () {
        if (muteButton().disabled) {
            if (window.startMicCapture) await window.startMicCapture();
        } else {
            if (window.stopMicCapture) await window.stopMicCapture();
        }
    };

    // ======================== switchScreenSharing ========================
    window.switchScreenSharing = async function () {
        if (isScreenSharingStartPending()) {
            await stopScreenSharing();
        } else if (stopButton().disabled) {
            // 检查是否在录音状态
            if (!S.isRecording) {
                window.showStatusToast(window.t ? window.t('app.micRequired') : '请先开启麦克风录音！', 3000);
                return;
            }
            await startScreenSharing();
        } else {
            await stopScreenSharing();
        }
    };

    function getScreenSourceDisplayName(source, screenIndex) {
        if (!source) return '';

        var rawName = source.name ? String(source.name) : '';
        var sourceId = source.id ? String(source.id) : '';
        if (!sourceId.startsWith('screen:')) {
            return rawName;
        }

        var index = null;
        if (typeof screenIndex === 'number' && isFinite(screenIndex)) {
            index = screenIndex + 1;
        }

        if (!index || index < 1) {
            var displayId = source.display_id != null ? String(source.display_id) : '';
            var displayIdMatch = displayId.match(/\d+/);
            if (displayIdMatch) {
                index = Number(displayIdMatch[0]);
            }
        }

        if (!index || index < 1) {
            index = 1;
        }

        if (window.t) {
            return window.t('app.screenSource.screenLabel', { index: index });
        }

        return '屏幕 ' + index;
    }
    mod.getScreenSourceDisplayName = getScreenSourceDisplayName;

    // ======================== selectScreenSource ========================
    async function selectScreenSource(sourceId, sourceName, displayName) {
        var previousSourceId = S.selectedScreenSourceId;
        S.selectedScreenSourceId = sourceId;
        if (previousSourceId !== sourceId) {
            markScreenSourceSelectionChanged();
        }
        markCurrentScreenSourceSelectionExplicit(sourceName || '');

        var resolvedSourceName = displayName || sourceName || sourceId;

        // 持久化到 localStorage
        try {
            if (sourceId) {
                localStorage.setItem('selectedScreenSourceId', sourceId);
            } else {
                localStorage.removeItem('selectedScreenSourceId');
            }
        } catch (e) {
            console.warn('[屏幕源] 无法保存到 localStorage:', e);
        }

        if (isScreenSourceTitleMatchEnabled()) {
            if (sourceId && sourceId.startsWith('window:')) {
                storeRememberedWindowTitle(sourceName || '');
            } else {
                clearRememberedWindowTitle();
            }
        }

        // 同步到主进程，确保 setDisplayMediaRequestHandler 兜底也认这个选择
        pushSelectedSourceToMain(sourceId);

        // 更新UI选中状态
        updateScreenSourceListSelection();

        // 显示选择提示
        window.showStatusToast(window.t ? window.t('app.screenSource.selected', { source: resolvedSourceName }) : '已选择 ' + resolvedSourceName, 3000);

        console.log('[屏幕源] 已选择:', sourceName || resolvedSourceName, '(ID:', sourceId, ')');

        // 切换窗口源时，强制释放旧的缓存流（无论是否在屏幕分享中）
        // 这确保下次获取流时使用新选择的源
        if (S.screenCaptureStream) {
            console.log('[屏幕源] 窗口选择已切换，强制释放旧缓存流');
            try {
                if (typeof S.screenCaptureStream.getTracks === 'function') {
                    S.screenCaptureStream.getTracks().forEach(function (track) {
                        try { track.stop(); } catch (e) { }
                    });
                }
            } catch (e) { }
            S.screenCaptureStream = null;
            S.screenCaptureStreamLastUsed = null;
            if (S.screenCaptureStreamIdleTimer) {
                clearTimeout(S.screenCaptureStreamIdleTimer);
                S.screenCaptureStreamIdleTimer = null;
            }
        }

        // 智能刷新：如果当前正在屏幕分享中，自动重启以应用新的屏幕源
        var stopBtn = document.getElementById('stopButton');
        // Native first-frame startup has already claimed a source, but the Stop
        // button is enabled only after that awaited frame returns. Treat this
        // pending interval as active so switching sources invalidates the old
        // generation before its late frame can be accepted.
        var isNativeCaptureActive = activeNativeCaptureSourceId !== null;
        var isScreenSharingActive = isNativeCaptureActive || !!(stopBtn && !stopBtn.disabled);

        if (isScreenSharingActive && window.switchScreenSharing) {
            console.log('[屏幕源] 检测到正在屏幕分享中，将自动重启以应用新源');
            // 先停止当前分享（流已释放，forceRelease 无所谓）
            await stopScreenSharing(true);
            // 等待一小段时间
            await new Promise(function (resolve) { setTimeout(resolve, 300); });
            // 重新开始分享（使用新选择的源）
            await startScreenSharing();
        }
    }
    mod.selectScreenSource = selectScreenSource;

    // ======================== updateScreenSourceListSelection ========================
    function updateScreenSourceListSelection() {
        var popupIds = ['live2d-popup-screen', 'vrm-popup-screen', 'mmd-popup-screen'];
        var screenPopups = [];
        popupIds.forEach(function (popupId) {
            var screenPopup = document.getElementById(popupId);
            if (screenPopup) screenPopups.push(screenPopup);
        });
        document.querySelectorAll('.neko-mic-popup-screen-sources').forEach(function (screenPopup) {
            screenPopups.push(screenPopup);
        });

        screenPopups.forEach(function (screenPopup) {
            if (!screenPopup) return;

            var options = screenPopup.querySelectorAll('.screen-source-option');
            options.forEach(function (option) {
                var sourceId = option.dataset.sourceId;
                var isSelected = sourceId === S.selectedScreenSourceId;

                if (isSelected) {
                    option.classList.add('selected');
                    option.style.background = 'var(--neko-popup-selected-bg)';
                    option.style.borderColor = '#4f8cff';
                } else {
                    option.classList.remove('selected');
                    option.style.background = 'transparent';
                    option.style.borderColor = 'transparent';
                }
            });
        });
    }
    mod.updateScreenSourceListSelection = updateScreenSourceListSelection;

    // ======================== renderFloatingScreenSourceList ========================
    window.renderFloatingScreenSourceList = async function (popupArg, renderOptions) {
        var screenPopup = popupArg || document.getElementById('live2d-popup-screen');
        renderOptions = renderOptions || {};
        if (!screenPopup) {
            console.warn('[屏幕源] 弹出框不存在');
            return false;
        }

        var popupId = screenPopup.id;
        var renderToken = (Number(screenPopup._screenSourceRenderToken) || 0) + 1;
        screenPopup._screenSourceRenderToken = renderToken;
        var requireVisible = renderOptions.requireVisible !== false;
        var isPopupAvailable = function () {
            if (!screenPopup || !screenPopup.isConnected) return false;
            if (popupId && document.getElementById(popupId) !== screenPopup) return false;
            if (screenPopup._screenSourceRenderToken !== renderToken) return false;
            if (!requireVisible) return true;
            return screenPopup.style.display === 'flex' && screenPopup.style.opacity !== '0';
        };
        if (!isPopupAvailable()) return false;

        var desktopProvider = resolveDesktopCaptureProvider();
        if (!desktopProvider || typeof desktopProvider.getSources !== 'function') {
            screenPopup.innerHTML = '';
            var notAvailableItem = document.createElement('div');
            notAvailableItem.textContent = window.t ? window.t('app.screenSource.notAvailable') : '仅在桌面版可用';
            notAvailableItem.style.padding = '12px';
            notAvailableItem.style.color = 'var(--neko-popup-text-sub)';
            notAvailableItem.style.fontSize = '13px';
            notAvailableItem.style.textAlign = 'center';
            screenPopup.appendChild(notAvailableItem);
            return false;
        }

        try {
            // 显示加载中
            screenPopup.innerHTML = '';
            var loadingItem = document.createElement('div');
            loadingItem.textContent = window.t ? window.t('app.screenSource.loading') : '加载中...';
            loadingItem.style.padding = '12px';
            loadingItem.style.color = 'var(--neko-popup-text-sub)';
            loadingItem.style.fontSize = '13px';
            loadingItem.style.textAlign = 'center';
            screenPopup.appendChild(loadingItem);

            // 第一阶段只枚举来源元数据。Electron 明确允许用 0x0 跳过每个窗口的
            // 缩略图捕获，名称返回后立即绘制，完整图片在第二阶段后台补齐。
            var sources = await desktopProvider.getSources({
                types: ['window', 'screen'],
                thumbnailSize: { width: 0, height: 0 }
            });

            if (!isPopupAvailable()) return false;

            screenPopup.innerHTML = '';

            if (!sources || sources.length === 0) {
                var noSourcesItem = document.createElement('div');
                noSourcesItem.textContent = window.t ? window.t('app.screenSource.noSources') : '没有可用的屏幕源';
                noSourcesItem.style.padding = '12px';
                noSourcesItem.style.color = 'var(--neko-popup-text-sub)';
                noSourcesItem.style.fontSize = '13px';
                noSourcesItem.style.textAlign = 'center';
                screenPopup.appendChild(noSourcesItem);
                return false;
            }

            // 分组：屏幕和窗口
            var screens = sources.filter(function (s) { return s.id.startsWith('screen:'); });
            var windows = sources.filter(function (s) { return s.id.startsWith('window:'); });
            var previewHosts = new Map();

            // Electron 的 source ID 只适合当前枚举结果；显式开启“记住窗口”后，
            // 用规范化标题重新解析当前 ID。只有唯一精确匹配才恢复，避免同名窗口误选。
            reconcileRememberedWindowSource(sources);

            function previewFrameStyles() {
                return {
                    width: '100%',
                    maxWidth: '90px',
                    height: '56px',
                    borderRadius: '4px',
                    border: '1px solid var(--neko-popup-separator)',
                    marginBottom: '4px',
                    boxSizing: 'border-box',
                    overflow: 'hidden'
                };
            }

            function renderPreviewLoading(host) {
                host.innerHTML = '';
                host.className = 'screen-source-thumbnail screen-source-thumbnail-loading';
                host.textContent = window.t ? window.t('app.screenSource.loading') : 'Loading...';
                Object.assign(host.style, previewFrameStyles(), {
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    padding: '4px',
                    background: 'var(--neko-screen-placeholder-bg, #f5f5f5)',
                    color: 'var(--neko-popup-text-sub)',
                    fontSize: '10px',
                    textAlign: 'center'
                });
            }

            function renderPreviewFallback(host, source) {
                host.innerHTML = '';
                host.className = 'screen-source-thumbnail screen-source-thumbnail-fallback';
                host.textContent = source.id.startsWith('screen:') ? '\uD83D\uDDA5\uFE0F' : '\uD83E\uDE9F';
                Object.assign(host.style, previewFrameStyles(), {
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    background: 'var(--neko-screen-placeholder-bg, #f5f5f5)',
                    fontSize: '24px'
                });
            }

            function sourceThumbnailDataUrl(source) {
                if (!source || !source.thumbnail) return '';
                if (typeof source.thumbnail === 'string') return source.thumbnail;
                if (typeof source.thumbnail.toDataURL === 'function') {
                    return source.thumbnail.toDataURL();
                }
                return '';
            }

            function renderPreviewImage(host, source) {
                var thumbnailDataUrl = '';
                try {
                    thumbnailDataUrl = sourceThumbnailDataUrl(source);
                    if (!thumbnailDataUrl || thumbnailDataUrl.trim() === '') {
                        throw new Error('thumbnail data URL is empty');
                    }
                } catch (error) {
                    console.warn('[屏幕源] 缩略图转换失败，使用占位图:', error);
                    renderPreviewFallback(host, source);
                    return false;
                }

                host.innerHTML = '';
                host.className = 'screen-source-thumbnail screen-source-thumbnail-ready';
                Object.assign(host.style, previewFrameStyles(), {
                    display: 'block',
                    padding: '0',
                    background: 'var(--neko-screen-placeholder-bg, #f5f5f5)'
                });
                var thumb = document.createElement('img');
                thumb.alt = '';
                thumb.src = thumbnailDataUrl;
                thumb.onerror = function () { renderPreviewFallback(host, source); };
                Object.assign(thumb.style, {
                    display: 'block',
                    width: '100%',
                    height: '100%',
                    objectFit: 'cover'
                });
                host.appendChild(thumb);
                return true;
            }

            // 创建网格容器的辅助函数
            function createGridContainer() {
                var grid = document.createElement('div');
                Object.assign(grid.style, {
                    display: 'grid',
                    gridTemplateColumns: 'repeat(3, 1fr)',
                    gap: '8px',
                    padding: '6px',
                    width: '100%',
                    boxSizing: 'border-box'
                });
                return grid;
            }

            // 创建屏幕源选项元素（网格样式：垂直布局，名字在下）
            function createSourceOption(source, screenIndex) {
                var displayName = getScreenSourceDisplayName(source, screenIndex);
                var option = document.createElement('div');
                option.className = 'screen-source-option';
                option.dataset.sourceId = source.id;
                option.dataset.sourceName = source.name || '';
                option.dataset.sourceSearchText = normalizeScreenSourceTitle(source.name || displayName);
                Object.assign(option.style, {
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    padding: '4px',
                    cursor: 'pointer',
                    borderRadius: '6px',
                    border: '2px solid transparent',
                    transition: 'all 0.2s ease',
                    background: 'transparent',
                    boxSizing: 'border-box',
                    minWidth: '0'  // 允许收缩
                });

                if (S.selectedScreenSourceId === source.id) {
                    option.classList.add('selected');
                    option.style.background = 'var(--neko-popup-selected-bg)';
                    option.style.borderColor = '#4f8cff';
                }

                // 名称阶段先保留与最终图片一致的 90x56 预览区域。
                var previewHost = document.createElement('div');
                // 元数据请求明确使用 0x0 跳过缩略图；Electron 仍可能返回
                // truthy 但为空的 NativeImage，因此这里始终等待第二阶段结果。
                renderPreviewLoading(previewHost);
                previewHosts.set(source.id, { host: previewHost, source: source });
                option.appendChild(previewHost);

                // 名称（在缩略图下方，允许多行）
                var label = document.createElement('span');
                label.textContent = displayName || source.name || '';
                if (source.name) {
                    label.title = source.name;
                    option.title = source.name;
                }
                Object.assign(label.style, {
                    fontSize: '10px',
                    color: 'var(--neko-popup-text)',
                    width: '100%',
                    textAlign: 'center',
                    lineHeight: '1.3',
                    wordBreak: 'break-word',
                    display: '-webkit-box',
                    WebkitLineClamp: '2',
                    WebkitBoxOrient: 'vertical',
                    overflow: 'hidden',
                    height: '26px'
                });
                option.appendChild(label);

                option.addEventListener('click', async function (e) {
                    e.stopPropagation();
                    await selectScreenSource(source.id, source.name, displayName);
                });

                option.addEventListener('mouseenter', function () {
                    if (!option.classList.contains('selected')) {
                        option.style.background = 'var(--neko-popup-hover)';
                    }
                });
                option.addEventListener('mouseleave', function () {
                    if (!option.classList.contains('selected')) {
                        option.style.background = 'transparent';
                    }
                });

                return option;
            }

            var windowGrid = null;
            var noWindowMatchesItem = null;
            if (windows.length > 0) {
                var filterWrap = document.createElement('div');
                Object.assign(filterWrap.style, {
                    padding: '2px 6px 6px',
                    flexShrink: '0'
                });
                var titleFilterInput = document.createElement('input');
                titleFilterInput.type = 'search';
                titleFilterInput.className = 'screen-source-title-filter';
                titleFilterInput.placeholder = window.t
                    ? window.t('app.screenSource.titleFilterPlaceholder')
                    : '筛选窗口标题';
                titleFilterInput.setAttribute(
                    'aria-label',
                    window.t ? window.t('app.screenSource.titleFilterAriaLabel') : '按标题筛选窗口'
                );
                Object.assign(titleFilterInput.style, {
                    width: '100%',
                    height: '28px',
                    padding: '4px 9px',
                    boxSizing: 'border-box',
                    border: '1px solid var(--neko-popup-separator)',
                    borderRadius: '6px',
                    outline: 'none',
                    background: 'var(--neko-popup-bg)',
                    color: 'var(--neko-popup-text)',
                    fontSize: '12px'
                });
                titleFilterInput.addEventListener('focus', function () {
                    titleFilterInput.style.borderColor = '#4f8cff';
                });
                titleFilterInput.addEventListener('blur', function () {
                    titleFilterInput.style.borderColor = 'var(--neko-popup-separator)';
                });
                titleFilterInput.addEventListener('input', function () {
                    if (!windowGrid) return;
                    var query = normalizeScreenSourceTitle(titleFilterInput.value);
                    var visibleCount = 0;
                    windowGrid.querySelectorAll('.screen-source-option').forEach(function (option) {
                        var visible = !query || option.dataset.sourceSearchText.indexOf(query) !== -1;
                        option.hidden = !visible;
                        option.style.display = visible ? 'flex' : 'none';
                        if (visible) visibleCount += 1;
                    });
                    if (noWindowMatchesItem) {
                        noWindowMatchesItem.hidden = visibleCount !== 0;
                    }
                });
                filterWrap.appendChild(titleFilterInput);
                screenPopup.appendChild(filterWrap);
            }

            // 添加屏幕列表（网格布局）
            if (screens.length > 0) {
                var screenLabel = document.createElement('div');
                screenLabel.className = 'screen-source-group-label screen-source-screen-label';
                screenLabel.textContent = window.t ? window.t('app.screenSource.screens') : '屏幕';
                Object.assign(screenLabel.style, {
                    padding: '4px 8px',
                    fontSize: '11px',
                    color: 'var(--neko-popup-text-sub)',
                    fontWeight: '600',
                    textTransform: 'uppercase'
                });
                screenPopup.appendChild(screenLabel);

                var screenGrid = createGridContainer();
                screens.forEach(function (source, index) {
                    screenGrid.appendChild(createSourceOption(source, index));
                });
                screenPopup.appendChild(screenGrid);
            }

            // 添加窗口列表（网格布局）
            if (windows.length > 0) {
                var windowLabel = document.createElement('div');
                windowLabel.className = 'screen-source-group-label screen-source-window-label';
                windowLabel.textContent = window.t ? window.t('app.screenSource.windows') : '窗口';
                Object.assign(windowLabel.style, {
                    padding: '4px 8px',
                    fontSize: '11px',
                    color: 'var(--neko-popup-text-sub)',
                    fontWeight: '600',
                    textTransform: 'uppercase',
                    marginTop: '8px'
                });
                screenPopup.appendChild(windowLabel);

                windowGrid = createGridContainer();
                windows.forEach(function (source) {
                    windowGrid.appendChild(createSourceOption(source, null));
                });
                screenPopup.appendChild(windowGrid);

                noWindowMatchesItem = document.createElement('div');
                noWindowMatchesItem.className = 'screen-source-no-window-matches';
                noWindowMatchesItem.textContent = window.t
                    ? window.t('app.screenSource.noWindowMatches')
                    : '没有匹配的窗口';
                noWindowMatchesItem.hidden = true;
                Object.assign(noWindowMatchesItem.style, {
                    padding: '8px 12px',
                    color: 'var(--neko-popup-text-sub)',
                    fontSize: '12px',
                    textAlign: 'center'
                });
                screenPopup.appendChild(noWindowMatchesItem);
            }

            // Linux portal 的来源枚举可能再次弹出系统选择器。名称阶段已经完成
            // 一次必要枚举，此类 provider 不再为缩略图重复请求。
            if (desktopSourceEnumerationMayPrompt(desktopProvider)) {
                previewHosts.forEach(function (entry) {
                    renderPreviewFallback(entry.host, entry.source);
                });
                return true;
            }

            // 第二阶段在后台获取整批缩略图。N.E.K.O.-PC 对这个显式缓存请求
            // 做 60 秒单快照缓存和 in-flight 去重；旧弹窗的迟到结果不会越过 token。
            Promise.resolve().then(function () {
                var thumbnailOptions = {
                    types: ['window', 'screen'],
                    thumbnailSize: { width: 160, height: 100 },
                    thumbnailCache: true
                };
                var configuredTimeoutMs = Number(C.SCREEN_SOURCE_THUMBNAIL_TIMEOUT);
                var thumbnailTimeoutMs = Number.isFinite(configuredTimeoutMs) && configuredTimeoutMs > 0
                    ? configuredTimeoutMs
                    : 15000;
                return window.invokeDesktopCaptureWithTimeout(
                    desktopProvider,
                    'getSources',
                    [thumbnailOptions],
                    thumbnailTimeoutMs
                );
            }).then(function (thumbnailSources) {
                if (!isPopupAvailable()) return;
                var thumbnailsById = new Map();
                (thumbnailSources || []).forEach(function (source) {
                    thumbnailsById.set(source.id, source);
                });
                previewHosts.forEach(function (entry, sourceId) {
                    var thumbnailSource = thumbnailsById.get(sourceId);
                    if (thumbnailSource && thumbnailSource.thumbnail) {
                        renderPreviewImage(entry.host, thumbnailSource);
                    } else {
                        renderPreviewFallback(entry.host, entry.source);
                    }
                });
            }).catch(function (error) {
                console.error('[屏幕源] 获取缩略图失败:', error);
                if (!isPopupAvailable()) return;
                previewHosts.forEach(function (entry) {
                    renderPreviewFallback(entry.host, entry.source);
                });
            });

            return true;
        } catch (error) {
            if (!isPopupAvailable()) return false;
            console.error('[屏幕源] 获取屏幕源失败:', error);
            screenPopup.innerHTML = '';
            var errorItem = document.createElement('div');
            errorItem.textContent = window.t ? window.t('app.screenSource.loadFailed') : '获取屏幕源失败';
            errorItem.style.padding = '12px';
            errorItem.style.color = '#dc3545';
            errorItem.style.fontSize = '13px';
            errorItem.style.textAlign = 'center';
            screenPopup.appendChild(errorItem);
            return false;
        }
    };

    // ======================== getSelectedScreenSourceId ========================
    window.getSelectedScreenSourceId = function () { return S.selectedScreenSourceId; };

    // ======================== detectScreenshotCaptureType ========================
    /**
     * 判断截图的捕获类型，用于正确映射 Avatar 坐标。
     *
     * @param {MediaStream|null} stream - 捕获流（如有）
     * @param {string|null} sourceId - Electron desktopCapturer 源 ID（如有）
     * @returns {'screen'|'viewport'|null}
     *   'screen'   — 全屏/整个桌面截图，坐标需从视口映射到屏幕
     *   'viewport' — 浏览器窗口/标签页截图，坐标直接映射
     *   null       — 无法确定或不应叠加（如其他应用窗口、手机相机等）
     */
    function detectScreenshotCaptureType(stream, sourceId) {
        // Electron source ID: 'screen:0:0' → 全屏, 'window:12345' → 窗口
        if (sourceId) {
            if (sourceId.startsWith('screen:')) return 'screen';
            // Electron 窗口源 — 可能是浏览器自身或其他 app
            // 如果是其他 app 窗口，Avatar 不在截图中，应返回 null。
            // 暂时保守返回 null（窗口截图不叠加）。
            return null;
        }

        // getDisplayMedia / 缓存流: 检查 displaySurface
        if (stream) {
            try {
                var tracks = stream.getVideoTracks();
                for (var i = 0; i < tracks.length; i++) {
                    var settings = tracks[i].getSettings();
                    if (settings.displaySurface === 'monitor') return 'screen';
                    if (settings.displaySurface === 'window') return null; // 窗口截图不叠加
                    if (settings.displaySurface === 'browser') return 'viewport'; // 标签页
                }
            } catch (e) { /* ignore */ }
            // displaySurface 可能不可用（部分浏览器），无法确定
            return null;
        }

        return null;
    }
    mod.detectScreenshotCaptureType = detectScreenshotCaptureType;

    // ======================== 多显示器闸门 ========================
    // getAvatarScreenPosition 的 'screen' 分支用 window.screenX（虚拟桌面全局坐标）
    // 当原点、用 window.screen.width（当前所在显示器尺寸）当归一化分母，两个参考系
    // 差一个「当前显示器 bounds 原点」。单屏下该原点恒为 0 所以结果正确；多屏下
    // 「捕获副屏 / Pet 窗口在主屏」会算出一个落在 [0,1] 内的错误坐标，把注解叠到
    // 另一块屏的截图上。
    //
    // 要把参考系修对，必须知道「被捕获的是哪块屏」，而整条链路都不携带这个信息：
    // sourceId 只被 startsWith('screen:') 判一下就丢弃，getSettings() 里没有任何
    // 显示器标识，运行时又不能重新 getSources（Linux Portal 会再弹系统窗口）。
    // 所以这里只做闸门：确认是多屏就不叠。宁可不叠，也不叠到错误的屏上。
    var multiDisplayCache = null;   // true / false / null(未知)
    var multiDisplayCacheAt = 0;
    // 桥上没有显示器拓扑变更事件（electronScreen 只有 getAllDisplays /
    // getCurrentDisplay / getCursorPoint / getDesktopCoordinateSnapshot /
    // getPrimaryDisplayInfo / moveWindowToDisplay），只能按 TTL 重查：一次查完就
    // 永久缓存的话，笔记本插上外接屏之后闸门会一直按单屏放行。
    var MULTI_DISPLAY_CACHE_TTL_MS = 5000;

    function refreshMultiDisplayCache() {
        // 无条件先打时间戳，失败/没有桥时也算「这一轮问过了」：否则查询失败会让
        // multiDisplayCache 一直是 null，下面的节流判据就永远放行，持续分享时
        // 变成每帧一次 IPC（还会叠出多个在途请求）。
        multiDisplayCacheAt = Date.now();
        var bridge = window.electronScreen;
        if (!bridge || typeof bridge.getAllDisplays !== 'function') return;
        try {
            Promise.resolve(bridge.getAllDisplays()).then(function (list) {
                if (list && typeof list.length === 'number') {
                    multiDisplayCache = list.length > 1;
                }
            }).catch(function () { /* 拿不到就保持上一次的判断 */ });
        } catch (e) { /* 拿不到就保持上一次的判断 */ }
    }

    function isKnownMultiDisplay() {
        // Chromium 的 Screen.isExtended 不需要权限，且随拓扑实时变化，是最直接的信号。
        // 拿不到时退回 electronScreen 缓存；两者都拿不到就返回 false，
        // 即逐字沿用改动前的行为（单屏用户与纯浏览器场景不受本闸门影响）。
        if (window.screen && typeof window.screen.isExtended === 'boolean') {
            return window.screen.isExtended;
        }
        // 刷新是 fire-and-forget：本次仍用上一轮的值，拓扑变化最多晚 TTL + 一次 IPC
        // 生效。不 await 是因为这条在截图路径上，持续分享时每秒都会问一次。
        // 节流只看时间戳，不看缓存是否还是未知——查询失败时 multiDisplayCache 会
        // 一直是 null，若把它放进判据就等于不节流。
        if (multiDisplayCacheAt === 0
            || (Date.now() - multiDisplayCacheAt) > MULTI_DISPLAY_CACHE_TTL_MS) {
            refreshMultiDisplayCache();
        }
        return multiDisplayCache === true;
    }
    mod.isKnownMultiDisplay = isKnownMultiDisplay;

    // ======================== getAvatarScreenPosition ========================
    /**
     * 获取 Avatar 模型在截图图片坐标系中的归一化位置（0-1）。
     *
     * 根据 captureType 做不同的坐标映射：
     *  - 'viewport': 截图内容 = 浏览器视口，直接用 viewport 坐标归一化
     *  - 'screen':   截图内容 = 整个屏幕，需要加上浏览器窗口在屏幕上的偏移
     *  - null:       不叠加，返回 null
     *
     * @param {'screen'|'viewport'|null} captureType
     * @returns {{ centerX: number, centerY: number, width: number, height: number } | null}
     */
    function getAvatarScreenPosition(captureType) {
        if (!captureType) return null;

        // 如果 live2d-container 被最小化/隐藏，截图中无 Avatar
        var container = document.getElementById('live2d-container');
        if (container && (container.classList.contains('minimized') ||
            getComputedStyle(container).visibility === 'hidden')) {
            return null;
        }

        // --- 第一步：获取 Avatar 在视口 CSS 像素中的绝对坐标 ---
        var avatarCx = NaN, avatarCy = NaN, avatarW = 0, avatarH = 0;

        // Live2D (PIXI) — getBounds 返回的就是视口像素坐标
        if (window.live2dManager && typeof window.live2dManager.getModelScreenBounds === 'function') {
            try {
                var bounds = window.live2dManager.getModelScreenBounds();
                if (bounds && bounds.width > 0 && bounds.height > 0) {
                    avatarCx = bounds.centerX;
                    avatarCy = bounds.centerY;
                    avatarW = bounds.width;
                    avatarH = bounds.height;
                }
            } catch (e) { /* ignore */ }
        }

        // VRM (Three.js)
        var THREE = window.THREE;
        if (isNaN(avatarCx) && THREE && window.vrmManager) {
            try {
                var vrm = window.vrmManager;
                var model = (typeof vrm.getCurrentModel === 'function' ? vrm.getCurrentModel() : vrm.currentModel);
                if (model && model.vrm && model.vrm.scene && vrm.camera) {
                    var canvas = vrm.renderer && vrm.renderer.domElement || document.getElementById('vrm-canvas');
                    if (canvas) {
                        var box = new THREE.Box3().setFromObject(model.vrm.scene);
                        var size3 = box.getSize(new THREE.Vector3());
                        var boxCenter = box.getCenter(new THREE.Vector3());
                        var cProj = boxCenter.clone().project(vrm.camera);
                        var cw = canvas.clientWidth || 1;
                        var ch = canvas.clientHeight || 1;
                        avatarCx = (cProj.x * 0.5 + 0.5) * cw;
                        avatarCy = (-cProj.y * 0.5 + 0.5) * ch;
                        var topPt = new THREE.Vector3(boxCenter.x, box.max.y, boxCenter.z).project(vrm.camera);
                        var botPt = new THREE.Vector3(boxCenter.x, box.min.y, boxCenter.z).project(vrm.camera);
                        avatarH = Math.abs((-topPt.y * 0.5 + 0.5) - (-botPt.y * 0.5 + 0.5)) * ch;
                        avatarW = avatarH * (size3.x / Math.max(size3.y, 0.01));
                        avatarW = Math.max(avatarW, 1);
                        avatarH = Math.max(avatarH, 1);
                    }
                }
            } catch (e) { /* ignore */ }
        }

        // MMD (Three.js)
        if (isNaN(avatarCx) && THREE && window.mmdManager) {
            try {
                var mmd = window.mmdManager;
                var mmdModel = (typeof mmd.getCurrentModel === 'function' ? mmd.getCurrentModel() : mmd.currentModel);
                if (mmdModel && mmdModel.mesh && mmd.camera) {
                    var mmdCanvas = mmd.renderer && mmd.renderer.domElement || mmd.canvas || document.getElementById('mmd-canvas');
                    if (mmdCanvas) {
                        var mbox = new THREE.Box3().setFromObject(mmdModel.mesh);
                        var msize = mbox.getSize(new THREE.Vector3());
                        var mc = mbox.getCenter(new THREE.Vector3());
                        var mcP = mc.clone().project(mmd.camera);
                        var mcw = mmdCanvas.clientWidth || 1;
                        var mch = mmdCanvas.clientHeight || 1;
                        avatarCx = (mcP.x * 0.5 + 0.5) * mcw;
                        avatarCy = (-mcP.y * 0.5 + 0.5) * mch;
                        var mtop = new THREE.Vector3(mc.x, mbox.max.y, mc.z).project(mmd.camera);
                        var mbot = new THREE.Vector3(mc.x, mbox.min.y, mc.z).project(mmd.camera);
                        avatarH = Math.abs((-mtop.y * 0.5 + 0.5) - (-mbot.y * 0.5 + 0.5)) * mch;
                        avatarW = avatarH * (msize.x / Math.max(msize.y, 0.01));
                        avatarW = Math.max(avatarW, 1);
                        avatarH = Math.max(avatarH, 1);
                    }
                }
            } catch (e) { /* ignore */ }
        }

        if (isNaN(avatarCx)) return null;

        // --- 第二步：根据 captureType 将视口像素坐标映射到截图坐标系 ---
        var refW, refH; // 截图所覆盖区域的 CSS 尺寸（用于归一化分母）

        if (captureType === 'screen') {
            // 多屏下无法判断截的是哪块屏，下面的坐标换算会算错屏，直接不叠。
            if (isKnownMultiDisplay()) return null;

            // 截图覆盖整个屏幕 → 坐标需加上浏览器窗口在屏幕上的偏移
            // viewportOrigin = windowOuter 的左上角 + chrome 偏移
            var chromeLeft = Math.round((window.outerWidth - window.innerWidth) / 2);
            var chromeTop = window.outerHeight - window.innerHeight - chromeLeft; // 减去等量底部边框
            var vpOriginX = (window.screenX || 0) + chromeLeft;
            var vpOriginY = (window.screenY || 0) + chromeTop;

            avatarCx += vpOriginX;
            avatarCy += vpOriginY;
            refW = window.screen.width || 1;
            refH = window.screen.height || 1;

            // 如果 Avatar 中心超出屏幕范围，说明不在截图内
            if (avatarCx < 0 || avatarCx > refW || avatarCy < 0 || avatarCy > refH) {
                return null;
            }
        } else {
            // 'viewport' — 截图覆盖浏览器视口
            refW = window.innerWidth || 1;
            refH = window.innerHeight || 1;
        }

        return {
            centerX: avatarCx / refW,
            centerY: avatarCy / refH,
            width:   avatarW / refW,
            height:  avatarH / refH
        };
    }
    mod.getAvatarScreenPosition = getAvatarScreenPosition;

    // ======================== Backward-compat window exports ========================
    window.startScreenSharing = startScreenSharing;
    window.stopScreenSharing = stopScreenSharing;
    window.isScreenSharingStartPending = isScreenSharingStartPending;
    window.selectScreenSource = selectScreenSource;
    window.getScreenSourceDisplayName = getScreenSourceDisplayName;
    window.captureCanvasFrame = captureCanvasFrame;
    window.captureFrameFromStream = captureFrameFromStream;
    window.prepareRememberedWindowCapture = prepareRememberedWindowCapture;
    window.acquireOrReuseCachedStream = acquireOrReuseCachedStream;
    window.fetchBackendScreenshot = fetchBackendScreenshot;
    window.fetchBackendInteractiveScreenshot = fetchBackendInteractiveScreenshot;
    window.getMobileCameraStream = getMobileCameraStream;
    window.startScreenVideoStreaming = startScreenVideoStreaming;
    window.stopScreening = stopScreening;
    window.scheduleScreenCaptureIdleCheck = scheduleScreenCaptureIdleCheck;
    window.syncFloatingScreenButtonState = syncFloatingScreenButtonState;
    window.getAvatarScreenPosition = getAvatarScreenPosition;
    window.detectScreenshotCaptureType = detectScreenshotCaptureType;
    window.clearSelectedScreenSource = clearSelectedScreenSource;
    window.maybeClearSourceOnNotFound = maybeClearSourceOnNotFound;

    // 预热多显示器缓存：electronScreen 桥是异步的，等到第一次截图再问就来不及，
    // 那一帧会按「未知 → 单屏」处理。screen.isExtended 可用时这一步是多余的。
    refreshMultiDisplayCache();

    // ======================== Export module ========================
    window.appScreen = mod;
})();
