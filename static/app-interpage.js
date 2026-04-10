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
     * Handle model hot-swap triggered from another tab (model_manager).
     *
     * Concurrency-safe: if a reload is already in flight, the new request
     * is queued and executed once the current one finishes.
     *
     * @param {string} [targetLanlanName='']  - optional character name filter
     */
    async function handleModelReload(targetLanlanName) {
        targetLanlanName = targetLanlanName || '';

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
                        // 停止旧的待机轮换
                        if (typeof window._stopVrmIdleRotation === 'function') window._stopVrmIdleRotation();
                        if (typeof window._stopMmdIdleRotation === 'function') window._stopMmdIdleRotation();

                        await window.vrmManager.loadModel(newModelPath);

                        // 获取角色待机动作列表并启动轮换
                        if (nameForConfig && typeof window._startVrmIdleRotation === 'function') {
                            try {
                                const charRes = await fetch('/api/characters/');
                                if (charRes.ok) {
                                    const charData = await charRes.json();
                                    const catData = charData?.['猫娘']?.[nameForConfig];
                                    let idleList = catData?.idleAnimations;
                                    if (!Array.isArray(idleList)) {
                                        const single = catData?.idleAnimation;
                                        idleList = single ? [single] : [];
                                    }
                                    window.lanlan_config.vrmIdleAnimations = idleList;
                                    if (idleList.length > 0) {
                                        window._startVrmIdleRotation(idleList);
                                    }
                                }
                            } catch (e) {
                                console.warn('[Model] 获取VRM待机动作列表失败:', e);
                            }
                        }

                        // Apply lighting config if available
                        if (window.lanlan_config?.lighting && typeof window.applyVRMLighting === 'function') {
                            window.applyVRMLighting(window.lanlan_config.lighting, window.vrmManager);
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
                    if (mmdCanvasShow) {
                        mmdCanvasShow.style.visibility = 'visible';
                        mmdCanvasShow.style.pointerEvents = 'auto';
                    }

                    // Ensure MMD manager is initialised
                    if (!window.mmdManager) {
                        console.log('[Model] MMD 管理器未初始化，等待初始化完成');
                        if (typeof initMMDModel === 'function') {
                            await initMMDModel();
                        }
                    }

                    // Load MMD model
                    if (window.mmdManager) {
                        // 提前获取设置并预置物理开关
                        let savedSettings = null;
                        try {
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

                        await window.mmdManager.loadModel(newModelPath);

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
                                    let idleList = catData?.mmd_idle_animations;
                                    if (!Array.isArray(idleList)) {
                                        const single = catData?.mmd_idle_animation;
                                        idleList = single ? [single] : [];
                                    }
                                    if (idleList.length > 0) {
                                        try {
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
                        mmdCanvas2.style.visibility = 'hidden';
                        mmdCanvas2.style.pointerEvents = 'none';
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

    // =====================================================================
    // Hide / Show main UI (called when entering/leaving model manager)
    // =====================================================================

    /**
     * Hide main-page model rendering (entering model manager).
     */
    function handleHideMainUI() {
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
                mmdCanvas.style.visibility = 'hidden';
                mmdCanvas.style.pointerEvents = 'none';
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
        } catch (error) {
            console.error('[UI] 隐藏主界面失败:', error);
        }
    }

    /**
     * Show main-page model rendering (returning to main page).
     */
    function handleShowMainUI() {
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
                    if (mmdCanvasR) {
                        mmdCanvasR.style.visibility = 'visible';
                        mmdCanvasR.style.pointerEvents = 'auto';
                    }
                    if (window.mmdManager && typeof window.mmdManager.resumeRendering === 'function') {
                        window.mmdManager.resumeRendering();
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
        } catch (error) {
            console.error('[UI] 显示主界面失败:', error);
        }
    }

    // =====================================================================
    // BroadcastChannel initialisation
    // =====================================================================

    var nekoBroadcastChannel = null;
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
                }
            };
        }
    } catch (e) {
        console.log('[BroadcastChannel] 初始化失败，将使用 postMessage 后备方案:', e);
    }

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

    // Backward-compatible window globals
    window.handleModelReload = handleModelReload;
    window.handleHideMainUI = handleHideMainUI;
    window.handleShowMainUI = handleShowMainUI;
    window.cleanupLive2DOverlayUI = cleanupLive2DOverlayUI;
    window.cleanupVRMOverlayUI = cleanupVRMOverlayUI;
    window.cleanupMMDOverlayUI = cleanupMMDOverlayUI;

    window.appInterpage = mod;
})();
