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
        if (window.mmdManager && typeof window.mmdManager.cleanupUI === 'function') {
            window.mmdManager.cleanupUI();
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

                // Empty model path -> fall back to default for VRM, keep current for Live2D
                if (!newModelPath) {
                    if (newModelType === 'vrm') {
                        newModelPath = '/static/vrm/sister1.0.vrm';
                        console.info('[Model] VRM模型路径为空，使用默认模型:', newModelPath);
                    } else {
                        console.warn('[Model] 模型路径为空，保持当前模型不变');
                        window.showStatusToast(
                            window.t ? window.t('app.modelPathEmpty') : '模型路径为空',
                            2000
                        );
                        return;
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

                // 2. Update global config
                if (window.lanlan_config) {
                    window.lanlan_config.model_type = newModelType;
                    window.lanlan_config.live3d_sub_type = live3dSubType;
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
                        await window.vrmManager.loadModel(newModelPath);

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
                        await window.mmdManager.loadModel(newModelPath);

                        // 从后端获取并应用 MMD 设置（光照、渲染、物理、鼠标跟踪）
                        try {
                            var settingsRes = await fetch('/api/characters/catgirl/' + encodeURIComponent(nameForConfig) + '/mmd_settings');
                            var settingsData = await settingsRes.json();
                            if (settingsData.success && settingsData.settings) {
                                window.mmdManager.applySettings(settingsData.settings);
                            }
                        } catch (settingsErr) {
                            console.warn('[Model] 获取MMD设置失败:', settingsErr);
                        }
                    } else {
                        console.error('[Model] MMD 管理器初始化失败');
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

                    // Show & reload Live2D
                    if (newModelPath) {
                        console.log('[Model] 加载 Live2D 模型:', newModelPath);

                        var live2dContainer2 = document.getElementById('live2d-container');
                        if (live2dContainer2) {
                            live2dContainer2.classList.remove('hidden');
                            live2dContainer2.style.display = 'block';
                        }

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
                    }
                }

                // 4. Success toast
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
                // Live3D: determine sub-type from active manager
                var hasMMD = window.mmdManager && window.mmdManager.currentModel;
                var hasVRM = window.vrmManager && window.vrmManager.currentModel;
                
                if (hasMMD) {
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
                    if (typeof window.mmdManager.resumeRendering === 'function') {
                        window.mmdManager.resumeRendering();
                    }
                } else if (hasVRM) {
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
                    if (typeof window.vrmManager.resumeRendering === 'function') {
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
                }
            };
        }
    } catch (e) {
        console.log('[BroadcastChannel] 初始化失败，将使用 postMessage 后备方案:', e);
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
