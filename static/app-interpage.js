/**
 * app-interpage.js — Inter-page / cross-tab communication
 *
 * Handles:
 *   - BroadcastChannel setup and message dispatch
 *   - postMessage listeners (memory_edited, model_saved/reload_model)
 *   - Model hot-reload (Live2D / VRM switching)
 *   - UI hide/show commands from other tabs
 *   - Overlay cleanup helpers
 *
 * Dependencies (loaded before this file):
 *   - app-state.js          -> window.appState, window.appConst
 *
 * Runtime dependencies (available by the time handlers fire):
 *   - window.showStatusToast
 *   - window.stopMicCapture   (will be exposed by app.js or future app-mic.js)
 *   - window.clearAudioQueue  (will be exposed by app.js or future app-audio.js)
 *   - window.live2dManager, window.vrmManager
 *   - initLive2DModel / initVRMModel  (global functions from live2d-init.js / vrm-init.js)
 */
(function () {
    'use strict';

    const mod = {};
    const S = window.appState;
    // const C = window.appConst;  // not used in this module currently

    // =====================================================================
    // Message deduplication (BC + postMessage deliver the same message twice)
    // =====================================================================
    var _processedMsgKeys = {};

    /**
     * Returns true if this action+timestamp was already processed (duplicate).
     * First call for a given key returns false and registers it.
     */
    function isDuplicateMessage(action, timestamp) {
        if (!timestamp) return false;  // no timestamp → cannot deduplicate
        var key = action + '_' + timestamp;
        if (_processedMsgKeys[key]) return true;
        _processedMsgKeys[key] = true;
        setTimeout(function () { delete _processedMsgKeys[key]; }, 5000);
        return false;
    }

    // =====================================================================
    // Overlay cleanup helpers
    // =====================================================================

    /**
     * Remove Live2D overlay UI elements (floating buttons, lock icon, etc.)
     */
    function cleanupLive2DOverlayUI() {
        const live2dManager = window.live2dManager;

        if (live2dManager) {
            if (live2dManager._lockIconTicker && live2dManager.pixi_app?.ticker) {
                try {
                    live2dManager.pixi_app.ticker.remove(live2dManager._lockIconTicker);
                } catch (_) {
                    // ignore
                }
                live2dManager._lockIconTicker = null;
            }
            if (live2dManager._floatingButtonsTicker && live2dManager.pixi_app?.ticker) {
                try {
                    live2dManager.pixi_app.ticker.remove(live2dManager._floatingButtonsTicker);
                } catch (_) {
                    // ignore
                }
                live2dManager._floatingButtonsTicker = null;
            }
            if (live2dManager._floatingButtonsResizeHandler) {
                window.removeEventListener('resize', live2dManager._floatingButtonsResizeHandler);
                live2dManager._floatingButtonsResizeHandler = null;
            }
            if (live2dManager.tutorialProtectionTimer) {
                clearInterval(live2dManager.tutorialProtectionTimer);
                live2dManager.tutorialProtectionTimer = null;
            }
            live2dManager._floatingButtonsContainer = null;
            live2dManager._returnButtonContainer = null;
            live2dManager._lockIconElement = null;
            live2dManager._lockIconImages = null;
        }

        document.querySelectorAll('#live2d-floating-buttons, #live2d-lock-icon, #live2d-return-button-container')
            .forEach(function (el) { el.remove(); });
    }

    /**
     * Remove VRM overlay UI elements.
     */
    function cleanupVRMOverlayUI() {
        if (window.vrmManager && typeof window.vrmManager.cleanupUI === 'function') {
            window.vrmManager.cleanupUI();
            return;
        }
        document.querySelectorAll('#vrm-floating-buttons, #vrm-lock-icon, #vrm-return-button-container')
            .forEach(function (el) { el.remove(); });
    }

    /**
     * Remove MMD overlay UI elements.
     */
    function cleanupMMDOverlayUI() {
        if (window.mmdManager && typeof window.mmdManager.cleanupFloatingButtons === 'function') {
            window.mmdManager.cleanupFloatingButtons();
            return;
        }
        document.querySelectorAll('#mmd-floating-buttons, #mmd-lock-icon, #mmd-return-button-container')
            .forEach(function (el) { el.remove(); });
    }

    function markMMDCanvasLoadingSession(canvas, loadingSessionId) {
        if (!canvas) return;
        canvas.dataset.mmdLoadingSessionId = String(loadingSessionId);
        canvas.style.visibility = 'hidden';
        canvas.style.pointerEvents = 'none';
    }

    function restoreMMDCanvasForLoadingSession(canvas, loadingSessionId) {
        if (!canvas) return false;
        if (canvas.dataset.mmdLoadingSessionId !== String(loadingSessionId)) {
            return false;
        }
        delete canvas.dataset.mmdLoadingSessionId;
        canvas.style.visibility = 'visible';
        canvas.style.pointerEvents = 'auto';
        return true;
    }

    function isMMDLoadingSessionActive(canvas, loadingSessionId) {
        return !!canvas && canvas.dataset.mmdLoadingSessionId === String(loadingSessionId);
    }

    function clearMMDCanvasLoadingSession(canvas) {
        if (!canvas) return;
        delete canvas.dataset.mmdLoadingSessionId;
        canvas.style.visibility = 'hidden';
        canvas.style.pointerEvents = 'none';
    }

    // =====================================================================
    // Shared: memory-edited session reset logic
    // =====================================================================

    /**
     * Common handler for memory_edited events (used by both BroadcastChannel
     * and postMessage code paths).
     *
     * @param {string} catgirlName  - name of the character whose memory was edited
     */
    async function handleMemoryEdited(catgirlName) {
        console.log(
            window.t('console.memoryEditedRefreshContext'),
            catgirlName
        );

        // Was the user in voice mode before the edit?
        var wasRecording = S.isRecording;

        // Stop current mic capture
        if (S.isRecording && typeof window.stopMicCapture === 'function') {
            window.stopMicCapture();
        }

        // Tell backend to drop old context
        if (S.socket && S.socket.readyState === WebSocket.OPEN) {
            S.socket.send(JSON.stringify({ action: 'end_session' }));
            console.log('[Memory] 已向后端发送 end_session');
        }

        // Reset text session so next message reloads context
        if (S.isTextSessionActive) {
            S.isTextSessionActive = false;
            console.log('[Memory] 文本会话已重置，下次发送将重新加载上下文');
        }

        // Stop any playing AI audio (wait for decoder reset to avoid races)
        if (typeof window.clearAudioQueue === 'function') {
            try {
                await window.clearAudioQueue();
            } catch (e) {
                console.error('[Memory] clearAudioQueue 失败:', e);
            }
        }

        // If was in voice mode, wait for session teardown then re-connect
        if (wasRecording) {
            window.showStatusToast(
                window.t ? window.t('memory.refreshingContext') : '正在刷新上下文...',
                3000
            );
            // Wait for backend session to fully end
            await new Promise(function (resolve) { setTimeout(resolve, 1500); });
            // Trigger full startup flow via micButton click
            try {
                var micButton = document.getElementById('micButton');
                if (micButton) micButton.click();
            } catch (e) {
                console.error('[Memory] 自动重连语音失败:', e);
            }
        } else {
            window.showStatusToast(
                window.t ? window.t('memory.refreshed') : '记忆已更新，下次对话将使用新记忆',
                4000
            );
        }
    }

    // =====================================================================
    // Model hot-reload
    // =====================================================================

    /**
     * Capability check: does the current page host the full model UI?
     * index.html (served at / and /{lanlan_name}) has live2d-container,
     * vrm-container AND mmd-container. Other pages (chat, subtitle, etc.)
     * lack the complete set and must not run model reload / hide / show.
     */
    function _isModelHostPage() {
        return !!(document.getElementById('live2d-container')
              && document.getElementById('vrm-container')
              && document.getElementById('mmd-container'));
    }

    /**
     * Handle model hot-swap triggered from another tab (model_manager).
     *
     * Concurrency-safe: if a reload is already in flight, the new request
     * is queued and executed once the current one finishes.
     *
     * @param {string} [targetLanlanName='']  - optional character name filter
     */
    async function handleModelReload(targetLanlanName) {
        targetLanlanName = targetLanlanName || '';

        // 只有承载完整模型 UI 的页面才处理重载；Chat 等子窗口缺少渲染容器，
        // 执行会导致异常并弹出误导性的"模型切换失败"toast。
        if (!_isModelHostPage()) {
            console.log('[Model] 当前页面无模型容器，跳过模型重载');
            return;
        }

        // If the message targets a different character, ignore it
        var currentLanlanName = window.lanlan_config?.lanlan_name || '';
        if (targetLanlanName && currentLanlanName && targetLanlanName !== currentLanlanName) {
            console.log('[Model] 忽略来自其它角色的模型重载请求:', { targetLanlanName: targetLanlanName, currentLanlanName: currentLanlanName });
            return;
        }

        // Concurrency: wait if another reload is in-flight
        if (window._modelReloadInFlight) {
            console.log('[Model] 模型重载已在进行中，等待完成后重试');
            window._pendingModelReload = true;
            await window._modelReloadPromise;
            return;
        }

        // Mark in-flight
        window._modelReloadInFlight = true;
        window._pendingModelReload = false;

        var resolveReload;
        window._modelReloadPromise = new Promise(function (resolve) {
            resolveReload = resolve;
        });

        console.log('[Model] 开始热切换模型');
        let mmdRequestSessionId = '';
        let activeMmdLoadingSessionId = '';

        try {
            // 1. Re-fetch page config
            var nameForConfig = targetLanlanName || currentLanlanName;
            var pageConfigUrl = nameForConfig
                ? '/api/config/page_config?lanlan_name=' + encodeURIComponent(nameForConfig)
                : '/api/config/page_config';
            var response = await fetch(pageConfigUrl);
            var data = await response.json();

            if (data.success) {
                var newModelPath = data.model_path || '';
                var newModelType = (data.model_type || 'live2d').toLowerCase();
                var live3dSubType = (data.live3d_sub_type || '').toLowerCase();
                var oldModelType = window.lanlan_config?.model_type || 'live2d';
                var nextLighting = (data.lighting && typeof data.lighting === 'object')
                    ? Object.assign({}, data.lighting)
                    : null;

                window.lanlan_config = window.lanlan_config || {};
                window.lanlan_config.lighting = nextLighting;

                console.log('[Model] 模型切换:', {
                    oldType: oldModelType,
                    newType: newModelType,
                    newPath: newModelPath
                });

                // Empty model path -> fall back to default for VRM/Live3D-VRM
                if (!newModelPath) {
                    if (newModelType === 'vrm' || (newModelType === 'live3d' && live3dSubType === 'vrm')) {
                        newModelPath = '/static/vrm/sister1.0.vrm';
                        console.info('[Model] VRM模型路径为空，使用默认模型:', newModelPath);
                    } else {
                        console.warn('[Model] 模型路径为空，仍然执行模型类型切换');
                    }
                }

                // Cross-type switch: clean up the old overlay
                var oldLive3dSubType = (window.lanlan_config?.live3d_sub_type || '').toLowerCase();
                var typeChanged = oldModelType !== newModelType ||
                    (newModelType === 'live3d' && oldLive3dSubType !== live3dSubType);

                // 提前更新 config，防止异步间隙中其他代码基于过时类型重建按钮
                if (typeChanged && window.lanlan_config) {
                    window.lanlan_config.model_type = newModelType;
                    window.lanlan_config.live3d_sub_type = live3dSubType;
                }

                if (typeChanged) {
                    if (oldModelType === 'live2d') cleanupLive2DOverlayUI();
                    if (oldModelType === 'vrm') cleanupVRMOverlayUI();
                    if (oldModelType === 'live3d') {
                        cleanupVRMOverlayUI();
                        cleanupMMDOverlayUI();
                    }
                }

                // 3. Switch based on model type
                if (newModelType === 'vrm' || (newModelType === 'live3d' && live3dSubType === 'vrm')) {
                    window.vrmModel = newModelPath;
                    window.cubism4Model = '';

                    // Hide Live2D
                    console.log('[Model] 隐藏 Live2D 模型');
                    var live2dContainer = document.getElementById('live2d-container');
                    if (live2dContainer) {
                        live2dContainer.style.display = 'none';
                        live2dContainer.classList.add('hidden');
                    }

                    // Hide MMD
                    var mmdContainer = document.getElementById('mmd-container');
                    if (mmdContainer) {
                        mmdContainer.style.display = 'none';
                        mmdContainer.classList.add('hidden');
                    }
                    var mmdCanvas = document.getElementById('mmd-canvas');
                    if (mmdCanvas) {
                        mmdCanvas.style.visibility = 'hidden';
                        mmdCanvas.style.pointerEvents = 'none';
                    }
                    if (window.mmdManager && typeof window.mmdManager.pauseRendering === 'function') {
                        window.mmdManager.pauseRendering();
                    }
                    if (window.live2dManager && typeof window.live2dManager.pauseRendering === 'function') {
                        window.live2dManager.pauseRendering();
                    }
                    // 清空 Live2D 画布残留像素，避免透明窗口穿透
                    if (window.live2dManager && window.live2dManager.pixi_app && window.live2dManager.pixi_app.renderer) {
                        window.live2dManager.pixi_app.renderer.clear();
                    }

                    // Show & reload VRM
                    console.log('[Model] 加载 VRM 模型:', newModelPath);
                    var vrmContainer = document.getElementById('vrm-container');
                    if (vrmContainer) {
                        vrmContainer.classList.remove('hidden');
                        vrmContainer.style.display = 'block';
                        vrmContainer.style.visibility = 'visible';
                        vrmContainer.style.removeProperty('pointer-events');
                    }

                    var vrmCanvas = document.getElementById('vrm-canvas');
                    if (vrmCanvas) {
                        vrmCanvas.style.visibility = 'visible';
                        vrmCanvas.style.pointerEvents = 'auto';
                    }

                    // Ensure VRM manager is initialised
                    if (!window.vrmManager) {
                        console.log('[Model] VRM 管理器未初始化，等待初始化完成');
                        if (typeof initVRMModel === 'function') {
                            await initVRMModel();
                        }
                    }

                    // Load the new model
                    if (window.vrmManager) {
                        // 【关键修复】确保容器和 canvas 存在，并恢复 Three.js 场景可见性。
                        // 角色切换的清理逻辑会将 renderer.domElement 设为 display:none，
                        // 而 loadModel 内部在 scene/camera/renderer 已存在时不会调用
                        // ensureThreeReady（也就不会恢复 canvas 可见性），导致从 Live2D
                        // 切换到 VRM 时模型加载成功但不可见。
                        // initThreeJS 在已初始化时是幂等的，但会无条件恢复容器/canvas 可见性。
                        {
                            var vrmContainerEl = document.getElementById('vrm-container');
                            if (vrmContainerEl && !vrmContainerEl.querySelector('canvas')) {
                                var newCanvas = document.createElement('canvas');
                                newCanvas.id = 'vrm-canvas';
                                vrmContainerEl.appendChild(newCanvas);
                            }
                        }
                        await window.vrmManager.initThreeJS('vrm-canvas', 'vrm-container', nextLighting);

                        // 停止旧的待机轮换
                        if (typeof window._stopVrmIdleRotation === 'function') window._stopVrmIdleRotation();
                        if (typeof window._stopMmdIdleRotation === 'function') window._stopMmdIdleRotation();

                        // 【修复】在 loadModel 之前获取角色待机动作列表，
                        // 更新 lanlan_config 使 loadModel 内部读取到正确的待机动作 URL，
                        // 避免使用初始页面加载时的过时值导致动画加载失败进入 T-pose。
                        // 先清空旧值，确保 fetch 失败时 loadModel 回退到安全的硬编码默认值
                        // 而非残留的上一个角色的待机动作 URL。
                        var vrmIdleList = [];
                        window.lanlan_config.vrmIdleAnimation = '';
                        window.lanlan_config.vrmIdleAnimations = [];
                        if (nameForConfig) {
                            try {
                                var charResVrm = await fetch('/api/characters/');
                                if (charResVrm.ok) {
                                    var charDataVrm = await charResVrm.json();
                                    var catDataVrm = charDataVrm?.['猫娘']?.[nameForConfig];
                                    // 【修复】兼容新旧版字段，穿透 _reserved 读取 VRM 待机动作
                                    var rawVrmIdle = catDataVrm?._reserved?.avatar?.vrm?.idle_animation
                                                  || catDataVrm?.idle_animation
                                                  || catDataVrm?.idleAnimations
                                                  || catDataVrm?.idleAnimation;
                                    vrmIdleList = Array.isArray(rawVrmIdle) ? rawVrmIdle : (rawVrmIdle ? [rawVrmIdle] : []);
                                    window.lanlan_config.vrmIdleAnimation = vrmIdleList[0] || '';
                                    window.lanlan_config.vrmIdleAnimations = vrmIdleList;
                                }
                            } catch (e) {
                                console.warn('[Model] 获取VRM待机动作列表失败:', e);
                            }
                        }

                        await window.vrmManager.loadModel(newModelPath);

                        // 启动待机动作轮换（多个动作时自动切换）
                        if (vrmIdleList.length > 0 && typeof window._startVrmIdleRotation === 'function') {
                            window._startVrmIdleRotation(vrmIdleList);
                        }

                        // 重新应用打光/曝光/描边；若角色未保存自定义光照，则回退到默认值，避免沿用上一个角色的灯光状态。
                        var effectiveLighting = window.lanlan_config?.lighting || window.VRM_DEFAULT_LIGHTING || null;
                        if (effectiveLighting && typeof window.applyVRMLighting === 'function') {
                            window.applyVRMLighting(effectiveLighting, window.vrmManager);
                            if (typeof window.applyVRMOutlineWidth === 'function') {
                                var currentModelRef = window.vrmManager?.currentModel;
                                var outlineScale = effectiveLighting.outlineWidthScale;
                                requestAnimationFrame(function () {
                                    if (window.vrmManager?.currentModel !== currentModelRef) {
                                        return;
                                    }
                                    if (outlineScale !== undefined) {
                                        window.applyVRMOutlineWidth(outlineScale, window.vrmManager);
                                    }
                                });
                            }
                        }

                        // 重启 UI 更新循环（被 handleHideMainUI 停止）。
                        // handleShowMainUI 在 _modelReloadInFlight 为 true 时会跳过，
                        // 因此必须在模型加载完成后手动重启，否则悬浮按钮不会重新出现。
                        if (window.vrmManager && window.vrmManager._uiUpdateLoopId == null
                            && typeof window.vrmManager._startUIUpdateLoop === 'function') {
                            window.vrmManager._snapUIPosition = true;
                            window.vrmManager._startUIUpdateLoop();
                        }
                    } else {
                        console.error('[Model] VRM 管理器初始化失败');
                    }
                } else if (newModelType === 'live3d' && live3dSubType === 'mmd') {
                    // MMD mode (Live3D sub-type)
                    window.cubism4Model = '';
                    window.vrmModel = '';

                    // Hide Live2D
                    console.log('[Model] 隐藏 Live2D 模型');
                    var live2dContainerMmd = document.getElementById('live2d-container');
                    if (live2dContainerMmd) {
                        live2dContainerMmd.style.display = 'none';
                        live2dContainerMmd.classList.add('hidden');
                    }

                    // Hide VRM
                    var vrmContainerMmd = document.getElementById('vrm-container');
                    if (vrmContainerMmd) {
                        vrmContainerMmd.style.display = 'none';
                        vrmContainerMmd.classList.add('hidden');
                    }
                    var vrmCanvasMmd = document.getElementById('vrm-canvas');
                    if (vrmCanvasMmd) {
                        vrmCanvasMmd.style.visibility = 'hidden';
                        vrmCanvasMmd.style.pointerEvents = 'none';
                    }
                    if (window.vrmManager && typeof window.vrmManager.pauseRendering === 'function') {
                        window.vrmManager.pauseRendering();
                    }
                    if (window.vrmManager && window.vrmManager.renderer) {
                        window.vrmManager.renderer.clear();
                    }
                    if (window.live2dManager && typeof window.live2dManager.pauseRendering === 'function') {
                        window.live2dManager.pauseRendering();
                    }
                    if (window.live2dManager && window.live2dManager.pixi_app && window.live2dManager.pixi_app.renderer) {
                        window.live2dManager.pixi_app.renderer.clear();
                    }

                    // Show MMD container
                    console.log('[Model] 加载 MMD 模型:', newModelPath);
                    var mmdContainerShow = document.getElementById('mmd-container');
                    if (mmdContainerShow) {
                        mmdContainerShow.classList.remove('hidden');
                        mmdContainerShow.style.display = 'block';
                        mmdContainerShow.style.visibility = 'visible';
                        mmdContainerShow.style.removeProperty('pointer-events');
                    }
                    var mmdCanvasShow = document.getElementById('mmd-canvas');
                    const loadingSessionId = window._createMMDLoadingSessionId
                        ? window._createMMDLoadingSessionId('mmd-interpage')
                        : `mmd-interpage-${Date.now()}`;
                    if (mmdCanvasShow) {
                        // 先隐藏 canvas，避免旧帧或加载中的模型透过半透明 overlay 露出。
                        markMMDCanvasLoadingSession(mmdCanvasShow, loadingSessionId);
                    }
                    mmdRequestSessionId = loadingSessionId;
                    activeMmdLoadingSessionId = loadingSessionId;
                    window.MMDLoadingOverlay?.begin(loadingSessionId, { stage: 'engine' });

                    // Ensure MMD manager is initialised
                    if (!window.mmdManager) {
                        console.log('[Model] MMD 管理器未初始化，等待初始化完成');
                        if (typeof initMMDModel === 'function') {
                            const initializedManager = await initMMDModel();
                            if (!initializedManager || !window.mmdManager || window.mmdManager._isDisposed) {
                                throw new Error('MMD 管理器初始化失败');
                            }
                        }
                    }

                    // Load MMD model
                    if (window.mmdManager) {
                        // 提前获取设置并预置物理开关
                        let savedSettings = null;
                        try {
                            window.MMDLoadingOverlay?.update(loadingSessionId, { stage: 'settings' });
                            var settingsRes = await fetch('/api/characters/catgirl/' + encodeURIComponent(nameForConfig) + '/mmd_settings');
                            var settingsData = await settingsRes.json();
                            if (settingsData.success && settingsData.settings) {
                                savedSettings = settingsData.settings;
                                if (savedSettings.physics?.enabled != null) {
                                    window.mmdManager.enablePhysics = !!savedSettings.physics.enabled;
                                }
                            }
                        } catch (settingsErr) {
                            console.warn('[Model] 获取MMD设置失败:', settingsErr);
                        }
                        // 停止旧的待机轮换
                        if (typeof window._stopVrmIdleRotation === 'function') window._stopVrmIdleRotation();
                        if (typeof window._stopMmdIdleRotation === 'function') window._stopMmdIdleRotation();

                        window.MMDLoadingOverlay?.update(loadingSessionId, { stage: 'model' });
                        await window.mmdManager.loadModel(newModelPath, { loadingSessionId });

                        // 应用完整设置（光照、渲染、物理、鼠标跟踪）
                        if (savedSettings) {
                            window.mmdManager.applySettings(savedSettings);
                        }

                        // 播放待机动作 & 启动轮换
                        if (nameForConfig) {
                            try {
                                const charRes = await fetch('/api/characters/');
                                if (charRes.ok) {
                                    const charData = await charRes.json();
                                    const catData = charData?.['猫娘']?.[nameForConfig];
                                    // 【修复】兼容新旧版字段，穿透 _reserved 读取 MMD 待机动作
                                    let rawMmdIdle = catData?._reserved?.avatar?.mmd?.idle_animation
                                                  || catData?.mmd_idle_animations
                                                  || catData?.mmd_idle_animation;
                                    let idleList = Array.isArray(rawMmdIdle) ? rawMmdIdle : (rawMmdIdle ? [rawMmdIdle] : []);
                                    if (idleList.length > 0) {
                                        try {
                                            window.MMDLoadingOverlay?.update(loadingSessionId, { stage: 'idle' });
                                            await window.mmdManager.loadAnimation(idleList[0]);
                                            window.mmdManager.playAnimation();
                                            console.log('[Model] 已播放待机动作:', idleList[0]);
                                            if (typeof window._startMmdIdleRotation === 'function') {
                                                window._startMmdIdleRotation(idleList);
                                            }
                                        } catch (idleErr) {
                                            console.warn('[Model] 播放待机动作失败:', idleErr);
                                        }
                                    }
                                }
                            } catch (idleErr) {
                                console.warn('[Model] 获取角色待机动作失败:', idleErr);
                            }
                        }
                        window.MMDLoadingOverlay?.update(loadingSessionId, { stage: 'done' });
                        if (window._waitForMMDRenderFrame) {
                            await window._waitForMMDRenderFrame(window.mmdManager);
                        }
                        var mmdCanvasReady = document.getElementById('mmd-canvas');
                        if (mmdRequestSessionId === loadingSessionId && isMMDLoadingSessionActive(mmdCanvasReady, loadingSessionId)) {
                            window.MMDLoadingOverlay?.end(loadingSessionId);
                            restoreMMDCanvasForLoadingSession(mmdCanvasReady, loadingSessionId);
                            mmdRequestSessionId = '';
                            activeMmdLoadingSessionId = '';
                        }

                        // 重启 UI 更新循环（被 handleHideMainUI 停止）。
                        // handleShowMainUI 在 _modelReloadInFlight 为 true 时会跳过，
                        // 因此必须在模型加载完成后手动重启，否则悬浮按钮不会重新出现。
                        if (window.mmdManager && window.mmdManager._uiUpdateLoopId == null
                            && typeof window.mmdManager._startUIUpdateLoop === 'function') {
                            window.mmdManager._snapUIPosition = true;
                            window.mmdManager._startUIUpdateLoop();
                        }
                    } else {
                        console.error('[Model] MMD 管理器初始化失败');
                        throw new Error('MMD 管理器初始化失败');
                    }
                } else {
                    // Live2D mode
                    window.cubism4Model = newModelPath;
                    window.vrmModel = '';

                    // Hide VRM
                    console.log('[Model] 隐藏 VRM 模型');
                    var vrmContainer2 = document.getElementById('vrm-container');
                    if (vrmContainer2) {
                        vrmContainer2.style.display = 'none';
                        vrmContainer2.classList.add('hidden');
                    }
                    var vrmCanvas2 = document.getElementById('vrm-canvas');
                    if (vrmCanvas2) {
                        vrmCanvas2.style.visibility = 'hidden';
                        vrmCanvas2.style.pointerEvents = 'none';
                    }

                    // Hide MMD
                    var mmdContainer2 = document.getElementById('mmd-container');
                    if (mmdContainer2) {
                        mmdContainer2.style.display = 'none';
                        mmdContainer2.classList.add('hidden');
                    }
                    var mmdCanvas2 = document.getElementById('mmd-canvas');
                    if (mmdCanvas2) {
                        clearMMDCanvasLoadingSession(mmdCanvas2);
                    }
                    if (window.vrmManager && typeof window.vrmManager.pauseRendering === 'function') {
                        window.vrmManager.pauseRendering();
                    }
                    // 清空VRM画布残留像素，避免透明窗口穿透
                    if (window.vrmManager && window.vrmManager.renderer) {
                        window.vrmManager.renderer.clear();
                    }
                    if (window.mmdManager && typeof window.mmdManager.pauseRendering === 'function') {
                        window.mmdManager.pauseRendering();
                    }

                    // Show & reload Live2D
                    var live2dContainer2 = document.getElementById('live2d-container');
                    if (live2dContainer2) {
                        live2dContainer2.classList.remove('hidden');
                        live2dContainer2.style.display = 'block';
                        live2dContainer2.style.visibility = 'visible';
                        live2dContainer2.style.removeProperty('pointer-events');
                    }
                    var live2dCanvas2 = document.getElementById('live2d-canvas');
                    if (live2dCanvas2) {
                        live2dCanvas2.style.visibility = 'visible';
                        live2dCanvas2.style.pointerEvents = 'auto';
                    }

                    if (newModelPath) {
                        console.log('[Model] 加载 Live2D 模型:', newModelPath);

                        // Ensure Live2D manager is initialised
                        if (!window.live2dManager) {
                            console.log('[Model] Live2D 管理器未初始化，等待初始化完成');
                            if (typeof initLive2DModel === 'function') {
                                await initLive2DModel();
                            }
                        }

                        // Load the new model
                        if (window.live2dManager) {
                            // Ensure PIXI app is initialised
                            if (!window.live2dManager.pixi_app) {
                                // 安全网：如果 canvas 被 PIXI.destroy(true) 从 DOM 移除，重新创建
                                var live2dCanvasEl = document.getElementById('live2d-canvas');
                                if (!live2dCanvasEl) {
                                    console.log('[Model] live2d-canvas 不存在，重新创建');
                                    live2dCanvasEl = document.createElement('canvas');
                                    live2dCanvasEl.id = 'live2d-canvas';
                                    var live2dContainerEl = document.getElementById('live2d-container');
                                    if (live2dContainerEl) {
                                        live2dContainerEl.appendChild(live2dCanvasEl);
                                    }
                                }
                                console.log('[Model] PIXI 应用未初始化，正在初始化...');
                                await window.live2dManager.initPIXI('live2d-canvas', 'live2d-container');
                            }

                            // Apply saved user preferences to avoid "reset" on return from model manager
                            var modelPreferences = null;
                            try {
                                var preferences = await window.live2dManager.loadUserPreferences();
                                modelPreferences = preferences ? preferences.find(function (p) { return p && p.model_path === newModelPath; }) : null;
                            } catch (prefError) {
                                console.warn('[Model] 读取 Live2D 用户偏好失败，将继续加载模型:', prefError);
                            }

                            await window.live2dManager.loadModel(newModelPath, {
                                preferences: modelPreferences,
                                isMobile: window.innerWidth <= 768
                            });

                            // Sync legacy global references
                            if (window.LanLan1) {
                                window.LanLan1.live2dModel = window.live2dManager.getCurrentModel();
                                window.LanLan1.currentModel = window.live2dManager.getCurrentModel();
                            }

                            // 恢复 Live2D 待机动作
                            restoreLive2DIdleAnimationOnMainPage();
                        } else {
                            console.error('[Model] Live2D 管理器初始化失败');
                        }
                    } else {
                        console.warn('[Model] Live2D 模型路径为空，已切换容器但跳过模型加载');
                        window.showStatusToast(
                            window.t ? window.t('app.modelPathEmpty') : '模型路径为空',
                            2000
                        );
                    }
                }

                // 4. Commit config only after successful switch
                if (window.lanlan_config) {
                    window.lanlan_config.model_type = newModelType;
                    window.lanlan_config.live3d_sub_type = live3dSubType;
                }

                // 5. Success toast
                window.showStatusToast(
                    window.t ? window.t('app.modelSwitched') : '模型已切换',
                    2000
                );
            } else {
                console.error('[Model] 获取页面配置失败:', data.error);
                window.showStatusToast(
                    window.t ? window.t('app.modelSwitchFailed') : '模型切换失败',
                    3000
                );
            }
        } catch (error) {
            console.error('[Model] 模型热切换失败:', error);
            if (activeMmdLoadingSessionId) {
                window.MMDLoadingOverlay?.fail(activeMmdLoadingSessionId, { detail: error?.message || String(error) });
                if (mmdRequestSessionId === activeMmdLoadingSessionId) {
                    mmdRequestSessionId = '';
                }
                activeMmdLoadingSessionId = '';
            }
            // 回滚提前写入的 config，防止残留错误的模型类型
            if (typeChanged && window.lanlan_config) {
                window.lanlan_config.model_type = oldModelType;
                window.lanlan_config.live3d_sub_type = oldLive3dSubType || '';
                console.warn('[Model] 已回滚 config:', { model_type: oldModelType, live3d_sub_type: oldLive3dSubType });
            }
            window.showStatusToast(
                window.t ? window.t('app.modelSwitchFailed') : '模型切换失败',
                3000
            );
        } finally {
            // Clear in-flight flag
            window._modelReloadInFlight = false;
            resolveReload();

            // Process any queued reload request
            if (window._pendingModelReload) {
                console.log('[Model] 执行待处理的模型重载请求');
                window._pendingModelReload = false;
                setTimeout(function () { handleModelReload(); }, 100);
            }
        }
    }

    /**
     * [HACK/WORKAROUND] 动态向已加载的 Live2D 模型实例注入动作组。
     * 注意：这里直接修改了 pixi-live2d-display SDK 的内部私有/只读数据结构。
     * @deprecated-if-sdk-upgraded 如果未来升级了 live2d SDK，此函数极易崩溃，请优先寻找官方 API 替代。
     */
    function _injectMotionGroupSafely(live2dModel, groupName, motionFiles) {
        if (!live2dModel || !live2dModel.internalModel || !live2dModel.internalModel.motionManager) {
            console.warn('[_injectMotionGroup] 模型结构不完整，注入失败');
            return false;
        }

        const internalModel = live2dModel.internalModel;
        const motionManager = internalModel.motionManager;
        const motionsList = motionFiles.map(file => ({ File: file }));

        try {
            console.debug(`[_injectMotionGroup] 正在向内部结构注入动作组: ${groupName}`);

            // 1. 注入 MotionManager 配置
            if (!motionManager.definitions) motionManager.definitions = {};
            motionManager.definitions[groupName] = motionsList;

            // 2. 初始化实例缓存数组（关键：必须为空数组，避免跳过实际加载）
            if (!motionManager.motionGroups) motionManager.motionGroups = {};
            if (!motionManager.motionGroups[groupName]) motionManager.motionGroups[groupName] = [];

            // 3. 同步 fallback 的 Settings 树
            if (!internalModel.settings) internalModel.settings = {};
            if (!internalModel.settings.motions) internalModel.settings.motions = {};
            internalModel.settings.motions[groupName] = motionsList;

            // 4. 同步最外层的文件引用树
            if (!live2dModel.fileReferences) live2dModel.fileReferences = {};
            if (!live2dModel.fileReferences.Motions) live2dModel.fileReferences.Motions = {};
            live2dModel.fileReferences.Motions[groupName] = motionsList;

            console.debug('[_injectMotionGroup] 注入完成');
            return true;
        } catch (err) {
            console.error('[_injectMotionGroup] 篡改 SDK 内部结构时崩溃，可能是 SDK 已升级:', err);
            return false;
        }
    }

    // =====================================================================
    // Live2D 待机动作恢复功能
    //
    // 功能说明：
    // - 主页加载时自动读取 characters.json 中保存的 live2d_idle_animation
    // - 从 API 获取当前模型的动作文件列表
    // - 手动构建 motionManager.definitions 和 motionGroups（主页没有 PreviewAll 组）
    // - 加载并循环播放保存的待机动作
    //
    // 注意：motionGroups 必须初始化为空数组 []，不能放入配置对象！
    // 原因：SDK 会检查 motionGroups 是否已有内容来判断动作是否已加载。
    // 如果放入 JSON 配置对象，SDK 会误认为动作已加载，跳过网络请求和解析。
    // =====================================================================
    async function restoreLive2DIdleAnimationOnMainPage() {
        try {
            // 1. 获取当前角色名称，并作为当前任务的标识（防竞态）
            const initialLanlanName = window.lanlan_config?.lanlan_name;
            if (!initialLanlanName) {
                console.log('[Live2D Main] 没有 lanlan_name，跳过恢复待机动作');
                return;
            }

            // 2. 从 characters.json 获取保存的待机动作路径
            const response = await fetch('/api/characters/');
            const data = await response.json();

            // 【竞态防护】如果中途角色被切换了，立刻中止
            if (window.lanlan_config?.lanlan_name !== initialLanlanName) return;

            const charData = data['猫娘']?.[initialLanlanName];
            // 【修复】兼容新旧版字段，穿透 _reserved 读取 Live2D 待机动作
            const live2dIdleAnimation = charData?._reserved?.avatar?.live2d?.idle_animation
                                     || charData?.live2d_idle_animation
                                     || charData?.avatar?.live2d?.idle_animation;
            const live2dModelName = charData?.live2d;

            if (!live2dIdleAnimation) {
                console.log('[Live2D Main] 没有保存的待机动作');
                return;
            }

            if (!live2dModelName) {
                console.log('[Live2D Main] 没有模型名称');
                return;
            }

            console.log('[Live2D Main] 开始恢复待机动作:', live2dIdleAnimation);

            // 3. 从 API 获取模型的动作文件列表（主页没有初始化 PreviewAll 组）
            let modelFilesData;
            try {
                const filesResponse = await fetch('/api/live2d/model_files/' + encodeURIComponent(live2dModelName));
                modelFilesData = await filesResponse.json();
            } catch (e) {
                console.warn('[Live2D Main] 获取模型文件列表失败:', e);
                return;
            }

            // 【竞态防护】如果中途角色被切换了，立刻中止
            if (window.lanlan_config?.lanlan_name !== initialLanlanName) return;

            const motionFiles = modelFilesData?.motion_files || [];
            const motionIndex = motionFiles.indexOf(live2dIdleAnimation);
            if (motionIndex < 0) {
                console.log('[Live2D Main] 待机动作不在当前模型的动作列表中:', live2dIdleAnimation);
                return;
            }

            // 4. 获取 Live2D 模型和 motionManager
            const live2dManager = window.live2dManager;
            const live2dModel = live2dManager?.getCurrentModel();
            if (!live2dModel) {
                console.log('[Live2D Main] Live2D 模型未加载，跳过恢复');
                return;
            }

            const internalModel = live2dModel.internalModel;
            if (!internalModel?.motionManager) {
                console.log('[Live2D Main] motionManager 不存在');
                return;
            }

            const motionManager = internalModel.motionManager;
            const groupName = 'PreviewAll';

            // 5. 使用隔离的 Helper 函数注入动作组配置
            const injectSuccess = _injectMotionGroupSafely(live2dModel, groupName, motionFiles);
            if (!injectSuccess) {
                console.log('[Live2D Main] 注入动作组失败，跳过动作恢复');
                return;
            }

            // 6. 加载动作（耗时操作）
            await motionManager.loadMotion(groupName, motionIndex);

            // 【最终竞态防护】加载完成后，确保角色没切走，且当前的 Live2D 模型实例还是我之前拿到的那个
            if (window.lanlan_config?.lanlan_name !== initialLanlanName || live2dManager?.getCurrentModel() !== live2dModel) {
                console.log('[Live2D Main] 模型或角色已切换，中止待机动作播放');
                return;
            }

            // 7. 设置循环播放
            const motionInstance = motionManager.motionGroups?.[groupName]?.[motionIndex];
            if (motionInstance) {
                if (typeof motionInstance.setIsLoop === 'function') {
                    motionInstance.setIsLoop(true);
                } else if (motionInstance._loop !== undefined) {
                    motionInstance._loop = true;
                }
            }

            // 8. 停止当前动作并播放保存的待机动作
            motionManager.stopAllMotions();
            live2dModel.motion(groupName, motionIndex, 3);
            console.log('[Live2D Main] 已恢复待机动作并循环播放:', live2dIdleAnimation);

        } catch (error) {
            console.error('[Live2D Main] 恢复待机动作失败:', error);
        }
    }

    // 暴露给全局作用域，供 live2d-init.js 调用
    window.restoreLive2DIdleAnimationOnMainPage = restoreLive2DIdleAnimationOnMainPage;

    // =====================================================================
    // Hide / Show main UI (called when entering/leaving model manager)
    // =====================================================================

    /**
     * Hide main-page model rendering (entering model manager).
     */
    function handleHideMainUI() {
        if (!_isModelHostPage()) return;
        console.log('[UI] 隐藏主界面并暂停渲染');

        try {
            // Hide Live2D
            var live2dContainer = document.getElementById('live2d-container');
            if (live2dContainer) {
                live2dContainer.style.display = 'none';
                live2dContainer.classList.add('hidden');
            }

            var live2dCanvas = document.getElementById('live2d-canvas');
            if (live2dCanvas) {
                live2dCanvas.style.visibility = 'hidden';
                live2dCanvas.style.pointerEvents = 'none';
            }

            // Hide VRM
            var vrmContainer = document.getElementById('vrm-container');
            if (vrmContainer) {
                vrmContainer.style.display = 'none';
                vrmContainer.classList.add('hidden');
            }

            var vrmCanvas = document.getElementById('vrm-canvas');
            if (vrmCanvas) {
                vrmCanvas.style.visibility = 'hidden';
                vrmCanvas.style.pointerEvents = 'none';
            }

            // Hide MMD
            var mmdContainer = document.getElementById('mmd-container');
            if (mmdContainer) {
                mmdContainer.style.display = 'none';
                mmdContainer.classList.add('hidden');
            }

            var mmdCanvas = document.getElementById('mmd-canvas');
            if (mmdCanvas) {
                clearMMDCanvasLoadingSession(mmdCanvas);
            }

            // Pause render loops to save resources
            if (window.vrmManager && typeof window.vrmManager.pauseRendering === 'function') {
                window.vrmManager.pauseRendering();
            }

            if (window.live2dManager && typeof window.live2dManager.pauseRendering === 'function') {
                window.live2dManager.pauseRendering();
            }

            if (window.mmdManager && typeof window.mmdManager.pauseRendering === 'function') {
                window.mmdManager.pauseRendering();
            }

            // 停止 UI 更新循环（独立于渲染循环，pauseRendering 不会停止它们）
            // 如果不停止，UI 循环每帧会覆盖下面设置的 display: none，导致按钮重新出现
            if (window.vrmManager && window.vrmManager._uiUpdateLoopId != null) {
                cancelAnimationFrame(window.vrmManager._uiUpdateLoopId);
                window.vrmManager._uiUpdateLoopId = null;
            }
            if (window.mmdManager && window.mmdManager._uiUpdateLoopId != null) {
                cancelAnimationFrame(window.mmdManager._uiUpdateLoopId);
                window.mmdManager._uiUpdateLoopId = null;
            }

            // 隐藏所有悬浮按钮、锁图标和返回按钮（它们挂载在 document.body 上，不随容器隐藏）。
            // 记录隐藏前的 display，避免恢复时清空 display 导致容器短暂按默认 block 布局显示，
            // 出现“语音控制/屏幕分享/猫爪/设置/请她离开”先挤在一起再分开的闪烁。
            document.querySelectorAll(
                '#live2d-floating-buttons, #vrm-floating-buttons, #mmd-floating-buttons, ' +
                '#live2d-lock-icon, #vrm-lock-icon, #mmd-lock-icon, ' +
                '#live2d-return-button-container, #vrm-return-button-container, #mmd-return-button-container'
            ).forEach(function (el) {
                var computedDisplay = '';
                try {
                    computedDisplay = window.getComputedStyle(el).display || '';
                } catch (_) {}
                el.dataset.nekoPreHideDisplay = computedDisplay && computedDisplay !== 'none'
                    ? computedDisplay
                    : (el.style.display || 'none');
                el.style.display = 'none';
            });
        } catch (error) {
            console.error('[UI] 隐藏主界面失败:', error);
        }
    }

    /**
     * Show main-page model rendering (returning to main page).
     */
    function handleShowMainUI() {
        if (!_isModelHostPage()) return;
        // 模型重载进行中时跳过：handleModelReload 自己会正确切换容器，
        // 此时 lanlan_config.model_type 尚未更新，handleShowMainUI 会
        // 错误地恢复旧模型类型的容器，导致需要切换两次才能成功。
        if (window._modelReloadInFlight) {
            console.log('[UI] 模型重载进行中，跳过显示主界面（避免覆盖正在切换的容器）');
            return;
        }
        console.log('[UI] 显示主界面并恢复渲染');

        try {
            var currentModelType = window.lanlan_config?.model_type || 'live2d';
            console.log('[UI] 当前模型类型:', currentModelType);

            if (currentModelType === 'vrm') {
                // Show VRM
                var vrmContainer = document.getElementById('vrm-container');
                if (vrmContainer) {
                    vrmContainer.style.display = 'block';
                    vrmContainer.classList.remove('hidden');
                    console.log('[UI] VRM 容器已显示，display:', vrmContainer.style.display);
                }

                var vrmCanvas = document.getElementById('vrm-canvas');
                if (vrmCanvas) {
                    vrmCanvas.style.visibility = 'visible';
                    vrmCanvas.style.pointerEvents = 'auto';
                    console.log('[UI] VRM canvas 已显示，visibility:', vrmCanvas.style.visibility);
                }

                // Resume VRM rendering
                if (window.vrmManager && typeof window.vrmManager.resumeRendering === 'function') {
                    window.vrmManager.resumeRendering();
                }
                // 重启 VRM UI 更新循环（被 handleHideMainUI 停止）
                if (window.vrmManager && window.vrmManager._uiUpdateLoopId == null
                    && typeof window.vrmManager._startUIUpdateLoop === 'function') {
                    window.vrmManager._snapUIPosition = true;
                    window.vrmManager._startUIUpdateLoop();
                }
            } else if (currentModelType === 'live3d') {
                // Live3D: determine sub-type from config
                var live3dSubType = (window.lanlan_config && window.lanlan_config.live3d_sub_type || '').toLowerCase();
                
                if (live3dSubType === 'mmd') {
                    var mmdContainerR = document.getElementById('mmd-container');
                    if (mmdContainerR) {
                        mmdContainerR.style.display = 'block';
                        mmdContainerR.classList.remove('hidden');
                    }
                    var mmdCanvasR = document.getElementById('mmd-canvas');
                    var hasActiveLoadingSession = mmdCanvasR && !!mmdCanvasR.dataset.mmdLoadingSessionId;
                    if (mmdCanvasR && !hasActiveLoadingSession) {
                        mmdCanvasR.style.visibility = 'visible';
                        mmdCanvasR.style.pointerEvents = 'auto';
                    }
                    if (window.mmdManager && typeof window.mmdManager.resumeRendering === 'function') {
                        window.mmdManager.resumeRendering();
                    }
                    // 重启 MMD UI 更新循环（被 handleHideMainUI 停止）
                    // UI 循环会自动管理浮动按钮和锁图标的显示/定位
                    if (window.mmdManager && window.mmdManager._uiUpdateLoopId == null
                        && typeof window.mmdManager._startUIUpdateLoop === 'function') {
                        window.mmdManager._snapUIPosition = true;
                        window.mmdManager._startUIUpdateLoop();
                    }
                } else {
                    var vrmContainerR = document.getElementById('vrm-container');
                    if (vrmContainerR) {
                        vrmContainerR.style.display = 'block';
                        vrmContainerR.classList.remove('hidden');
                    }
                    var vrmCanvasR = document.getElementById('vrm-canvas');
                    if (vrmCanvasR) {
                        vrmCanvasR.style.visibility = 'visible';
                        vrmCanvasR.style.pointerEvents = 'auto';
                    }
                    if (window.vrmManager && typeof window.vrmManager.resumeRendering === 'function') {
                        window.vrmManager.resumeRendering();
                    }
                    if (window.vrmManager && window.vrmManager._uiUpdateLoopId == null
                        && typeof window.vrmManager._startUIUpdateLoop === 'function') {
                        window.vrmManager._snapUIPosition = true;
                        window.vrmManager._startUIUpdateLoop();
                    }
                }
            } else {
                // Show Live2D
                var live2dContainer = document.getElementById('live2d-container');
                if (live2dContainer) {
                    live2dContainer.style.display = 'block';
                    live2dContainer.classList.remove('hidden');
                    console.log('[UI] Live2D 容器已显示，display:', live2dContainer.style.display);
                }

                var live2dCanvas = document.getElementById('live2d-canvas');
                if (live2dCanvas) {
                    live2dCanvas.style.visibility = 'visible';
                    live2dCanvas.style.pointerEvents = 'auto';
                    console.log('[UI] Live2D canvas 已显示，visibility:', live2dCanvas.style.visibility);
                }

                // Resume Live2D rendering
                if (window.live2dManager && typeof window.live2dManager.resumeRendering === 'function') {
                    window.live2dManager.resumeRendering();
                }
            }

            // 只恢复常规悬浮按钮与锁图标。
            // “请她回来”按钮默认由“请她离开”流程显示；从角色外形/模型管理窗口返回时
            // 如果在这里把 return-button-container 的 display 从 none 清空，会凭空多出一个返回按钮。
            // 恢复为隐藏前的 display（如 flex/block），并让浮动按钮首两帧保持不可见，
            // 等 UI 更新循环完成定位后再显露，避免按钮先挤在一起再分开。
            var restoringFloatingEls = Array.from(document.querySelectorAll(
                '#live2d-floating-buttons, #vrm-floating-buttons, #mmd-floating-buttons, ' +
                '#live2d-lock-icon, #vrm-lock-icon, #mmd-lock-icon'
            ));
            var hiddenFloatingButtonEls = [];
            restoringFloatingEls.forEach(function (el) {
                var restoreDisplay = el.dataset.nekoPreHideDisplay || '';
                var isFloatingButtons = !!(el.id && /-floating-buttons$/.test(el.id));
                if (restoreDisplay && restoreDisplay !== 'none') {
                    if (isFloatingButtons) {
                        el.style.visibility = 'hidden';
                        hiddenFloatingButtonEls.push(el);
                    }
                    el.style.display = restoreDisplay;
                }
                delete el.dataset.nekoPreHideDisplay;
            });
            if (hiddenFloatingButtonEls.length > 0) {
                requestAnimationFrame(function () {
                    requestAnimationFrame(function () {
                        hiddenFloatingButtonEls.forEach(function (el) {
                            if (!el || !el.isConnected || el.style.display === 'none') return;
                            el.style.removeProperty('visibility');
                        });
                    });
                });
            }
            document.querySelectorAll(
                '#live2d-return-button-container, #vrm-return-button-container, #mmd-return-button-container'
            ).forEach(function (el) {
                if (!el.getAttribute('data-neko-return-visible')) {
                    el.style.display = 'none';
                }
            });
        } catch (error) {
            console.error('[UI] 显示主界面失败:', error);
        }
    }

    // =====================================================================
    // Voice chat composer sync (cross-window)
    // =====================================================================

    /**
     * Sync voice chat state to local React composer AND broadcast to other windows.
     * Called from app-buttons.js / app-audio-capture.js whenever voice chat starts/stops.
     *
     * @param {boolean} hidden - true = voice chat active, hide composer; false = show composer
     */
    function syncVoiceChatComposerHidden(hidden) {
        hidden = !!hidden;
        // Update local React composer
        if (window.reactChatWindowHost && typeof window.reactChatWindowHost.setComposerHidden === 'function') {
            window.reactChatWindowHost.setComposerHidden(hidden);
        }
        // Broadcast to other windows (chat.html ↔ index.html)
        if (nekoBroadcastChannel) {
            nekoBroadcastChannel.postMessage({
                action: 'voice_chat_active',
                active: hidden,
                lanlan_name: (window.lanlan_config && window.lanlan_config.lanlan_name) || '',
                timestamp: Date.now()
            });
        }
    }

    // =====================================================================
    // BroadcastChannel initialisation
    // =====================================================================

    var nekoBroadcastChannel = null;
    var _isRelayingYuiGuideHandoffSent = false;
    try {
        if (typeof BroadcastChannel !== 'undefined') {
            nekoBroadcastChannel = new BroadcastChannel('neko_page_channel');
            console.log('[BroadcastChannel] 主页面 BroadcastChannel 已初始化');

            nekoBroadcastChannel.onmessage = async function (event) {
                if (!event.data || !event.data.action) {
                    return;
                }

                // Deduplicate: same message arrives via both BC and postMessage
                if (isDuplicateMessage(event.data.action, event.data.timestamp)) {
                    console.log('[BroadcastChannel] 跳过重复消息:', event.data.action);
                    return;
                }

                console.log('[BroadcastChannel] 收到消息:', event.data.action);

                switch (event.data.action) {
                    case 'reload_model':
                        await handleModelReload(event.data?.lanlan_name);
                        break;
                    case 'hide_main_ui':
                        handleHideMainUI();
                        break;
                    case 'show_main_ui':
                        handleShowMainUI();
                        break;
                    case 'memory_edited':
                        await handleMemoryEdited(event.data.catgirl_name);
                        break;
                    case 'voice_chat_active': {
                        // 来自另一个窗口的语音对话状态变更，同步本地 React composer 隐藏状态
                        // 校验 lanlan_name：多角色场景下避免串状态
                        var vcCurrentName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                        if (event.data.lanlan_name && (!vcCurrentName || event.data.lanlan_name !== vcCurrentName)) break;
                        var vcHidden = !!event.data.active;
                        if (window.reactChatWindowHost && typeof window.reactChatWindowHost.setComposerHidden === 'function') {
                            window.reactChatWindowHost.setComposerHidden(vcHidden);
                        }
                        // 同步旧版 text-input-area（兜底）
                        var vcTextInput = document.getElementById('text-input-area');
                        if (vcTextInput) {
                            if (vcHidden) {
                                vcTextInput.classList.add('hidden');
                            } else {
                                vcTextInput.classList.remove('hidden');
                            }
                        }
                        break;
                    }
                    case 'avatar_updated': {
                        // 从 Pet 窗口接收头像数据，注入到 Chat 窗口
                        // 校验 lanlan_name：多角色场景下避免串头像
                        // 本地角色名未就绪时也跳过，等 config 注入后由 request_avatar 回填
                        const currentName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                        if (event.data.lanlan_name && (!currentName || event.data.lanlan_name !== currentName)) break;
                        const incomingDataUrl = event.data.dataUrl || '';
                        const incomingModelType = event.data.modelType || '';
                        if (window.appChatAvatar && typeof window.appChatAvatar.setExternalAvatar === 'function') {
                            window.appChatAvatar.setExternalAvatar(incomingDataUrl, incomingModelType);
                        } else if (incomingDataUrl) {
                            window.__nekoPendingAvatar = { dataUrl: incomingDataUrl, modelType: incomingModelType };
                        }
                        break;
                    }
                    case 'request_avatar': {
                        // 仅 Pet 主窗口（/index）应答，Chat 窗口不回传
                        if (window.location.pathname === '/chat') break;
                        // 校验 lanlan_name：与 avatar_updated 对称，本地名未就绪或不匹配时不回包
                        const reqCurrentName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                        if (event.data.lanlan_name && (!reqCurrentName || event.data.lanlan_name !== reqCurrentName)) break;
                        if (window.appChatAvatar && typeof window.appChatAvatar.getCachedPreview === 'function') {
                            const cached = window.appChatAvatar.getCachedPreview();
                            if (cached && cached.dataUrl && nekoBroadcastChannel) {
                                nekoBroadcastChannel.postMessage({
                                    action: 'avatar_updated',
                                    lanlan_name: (window.lanlan_config && window.lanlan_config.lanlan_name) || '',
                                    dataUrl: cached.dataUrl,
                                    modelType: cached.modelType || '',
                                    timestamp: Date.now()
                                });
                            }
                        }
                        break;
                    }
                    case 'handoff_consumed': {
                        // 目标页面消费了 handoff token，转发为 DOM 事件
                        window.dispatchEvent(new CustomEvent('neko:yui-guide:handoff-consumed', {
                            detail: event.data.detail || {}
                        }));
                        break;
                    }
                    case 'handoff_sent': {
                        // 其他标签页发出了 handoff-sent，转发为本地 DOM 事件
                        _isRelayingYuiGuideHandoffSent = true;
                        try {
                            window.dispatchEvent(new CustomEvent('neko:yui-guide:handoff-sent', {
                                detail: event.data.detail || {}
                            }));
                        } finally {
                            _isRelayingYuiGuideHandoffSent = false;
                        }
                        break;
                    }
                    case 'request_avatar_capture': {
                        if (window.location.pathname === '/chat') break;
                        var captureLanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                        if (event.data.lanlan_name && (!captureLanlanName || event.data.lanlan_name !== captureLanlanName)) break;
                        var captureRequestId = event.data.requestId || '';
                        var includeSource = !!event.data.includeSourceDataUrl;
                        if (window.avatarPortrait && typeof window.avatarPortrait.capture === 'function') {
                            window.avatarPortrait.capture({
                                width: 320, height: 320, padding: 0.035,
                                shape: 'rounded', radius: 40,
                                background: 'rgba(255, 255, 255, 0.96)',
                                includeDataUrl: true,
                                includeSourceDataUrl: includeSource
                            }).then(function (result) {
                                if (!nekoBroadcastChannel) return;
                                nekoBroadcastChannel.postMessage({
                                    action: 'avatar_capture_result',
                                    requestId: captureRequestId,
                                    dataUrl: result.dataUrl || '',
                                    modelType: result.modelType || '',
                                    sourceDataUrl: includeSource ? (result.sourceDataUrl || '') : '',
                                    cropRectPixels: result.cropRectPixels || null,
                                    timestamp: Date.now()
                                });
                            }).catch(function (err) {
                                console.error('[BroadcastChannel] avatar capture failed:', err);
                                if (!nekoBroadcastChannel) return;
                                nekoBroadcastChannel.postMessage({
                                    action: 'avatar_capture_result',
                                    requestId: captureRequestId,
                                    error: true,
                                    timestamp: Date.now()
                                });
                            });
                        } else if (nekoBroadcastChannel) {
                            nekoBroadcastChannel.postMessage({
                                action: 'avatar_capture_result',
                                requestId: captureRequestId,
                                error: true,
                                timestamp: Date.now()
                            });
                        }
                        break;
                    }
                }
            };
        }
    } catch (e) {
        console.log('[BroadcastChannel] 初始化失败，将使用 postMessage 后备方案:', e);
    }

    // =====================================================================
    // Cross-window handoff event forwarding via BroadcastChannel
    // =====================================================================

    // 首页发出 handoff-sent DOM 事件时，转发到 BC 让其他标签页感知
    window.addEventListener('neko:yui-guide:handoff-sent', function (evt) {
        if (_isRelayingYuiGuideHandoffSent) return;
        if (!nekoBroadcastChannel) return;
        nekoBroadcastChannel.postMessage({
            action: 'handoff_sent',
            detail: evt.detail || {},
            timestamp: Date.now()
        });
    });

    // =====================================================================
    // Cross-window avatar forwarding via BroadcastChannel
    // =====================================================================

    // Pet 窗口（/index）捕获头像后，通过 BC 广播给 Chat 窗口
    window.addEventListener('chat-avatar-preview-updated', function (evt) {
        // source === 'ipc' 表示此事件来自 BC 注入（setExternalAvatar），不回传避免循环
        if (evt.detail && evt.detail.source === 'ipc') return;
        if (!nekoBroadcastChannel) return;
        var dataUrl = evt.detail && evt.detail.dataUrl;
        if (!dataUrl) return;
        nekoBroadcastChannel.postMessage({
            action: 'avatar_updated',
            lanlan_name: (window.lanlan_config && window.lanlan_config.lanlan_name) || '',
            dataUrl: dataUrl,
            modelType: (evt.detail && evt.detail.modelType) || '',
            timestamp: Date.now()
        });
    });

    // Chat 窗口初始化时，向 Pet 窗口请求当前已缓存的头像
    if (window.location.pathname === '/chat' && nekoBroadcastChannel) {
        var initialLanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
        var postAvatarRequest = function () {
            nekoBroadcastChannel.postMessage({
                action: 'request_avatar',
                lanlan_name: (window.lanlan_config && window.lanlan_config.lanlan_name) || '',
                timestamp: Date.now()
            });
        };
        postAvatarRequest();
        // 配置可能尚未注入（lanlan_name 为空），等 IPC 注入后补发一次
        if (!initialLanlanName) {
            window.addEventListener('neko:config-injected', postAvatarRequest, { once: true });
        }
    }

    // =====================================================================
    // postMessage listeners (fallback for memory_edited & model_saved)
    // =====================================================================

    // Memory-edited from iframe (postMessage fallback)
    window.addEventListener('message', async function (event) {
        // Security: same-origin check
        if (event.origin !== window.location.origin) {
            console.warn('[Security] 拒绝来自不同源的 memory_edited 消息:', event.origin);
            return;
        }

        if (event.data && event.data.type === 'memory_edited') {
            await handleMemoryEdited(event.data.catgirl_name);
        }
    });

    // Model-saved / reload_model from model_manager window (postMessage fallback)
    window.addEventListener('message', async function (event) {
        // Security: same-origin check
        if (event.origin !== window.location.origin) {
            console.warn('[Security] 拒绝来自不同源的消息:', event.origin);
            return;
        }

        // Verify source is a known window (opener or child)
        if (event.source && event.source !== window.opener && !event.source.parent) {
            console.warn('[Security] 拒绝来自未知窗口的消息');
            return;
        }

        if (event.data && (event.data.action === 'model_saved' || event.data.action === 'reload_model')) {
            // Deduplicate: same message arrives via both BC and postMessage
            if (isDuplicateMessage(event.data.action, event.data.timestamp)) {
                console.log('[Model] 跳过重复 postMessage:', event.data.action);
                return;
            }
            console.log('[Model] 通过 postMessage 收到模型重载通知');
            await handleModelReload(event.data?.lanlan_name);
        }
    });

    // =====================================================================
    // Public API
    // =====================================================================

    mod.nekoBroadcastChannel = nekoBroadcastChannel;
    mod.handleModelReload = handleModelReload;
    mod.handleHideMainUI = handleHideMainUI;
    mod.handleShowMainUI = handleShowMainUI;
    mod.handleMemoryEdited = handleMemoryEdited;
    mod.cleanupLive2DOverlayUI = cleanupLive2DOverlayUI;
    mod.cleanupVRMOverlayUI = cleanupVRMOverlayUI;
    mod.cleanupMMDOverlayUI = cleanupMMDOverlayUI;
    mod.syncVoiceChatComposerHidden = syncVoiceChatComposerHidden;

    // Backward-compatible window globals
    window.handleModelReload = handleModelReload;
    window.handleHideMainUI = handleHideMainUI;
    window.handleShowMainUI = handleShowMainUI;
    window.cleanupLive2DOverlayUI = cleanupLive2DOverlayUI;
    window.cleanupVRMOverlayUI = cleanupVRMOverlayUI;
    window.cleanupMMDOverlayUI = cleanupMMDOverlayUI;
    window.syncVoiceChatComposerHidden = syncVoiceChatComposerHidden;

    window.appInterpage = mod;
})();
