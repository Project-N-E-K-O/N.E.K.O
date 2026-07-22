// Part responsibility: VRM, MMD, Live2D preview loading, disposal, resizing, and playback controls.

let workshopVrmManager = null;
let workshopMmdManager = null;
let _workshopVrmModulesLoaded = false;
let _workshopMmdModulesLoaded = false;
let _workshopVrmModulesLoading = false;
let _workshopMmdModulesLoading = false;
let _workshopPreviewGeneration = 0;

function cancelWorkshopPreviewLoads() {
    _workshopPreviewGeneration += 1;
    cancelPendingLive2DPreviewLoads();
}
function isWorkshopPreviewLoadCurrent(generation) {
    return generation === _workshopPreviewGeneration && !!document.getElementById('live2d-preview-content');
}

async function disposeStaleWorkshopPreviewManager(manager, type) {
    if (!manager) return;
    try {
        if (type === 'mmd' && typeof manager.stopAnimation === 'function') {
            manager.stopAnimation();
        }
        if (typeof manager.dispose === 'function') {
            await manager.dispose();
        }
    } catch (e) {
        console.warn(`[Workshop ${String(type || '').toUpperCase()}] 清理过期预览实例失败:`, e);
    } finally {
        if (type === 'vrm' && workshopVrmManager === manager) {
            workshopVrmManager = null;
        }
        if (type === 'mmd' && workshopMmdManager === manager) {
            workshopMmdManager = null;
        }
    }
}

// 按需加载 VRM 模块
async function ensureVrmModulesLoaded() {
    if (_workshopVrmModulesLoaded) return true;
    if (_workshopVrmModulesLoading) {
        // 等待加载完成，带超时和失败检测
        return new Promise((resolve) => {
            let elapsed = 0;
            const check = () => {
                if (_workshopVrmModulesLoaded) resolve(true);
                else if (!_workshopVrmModulesLoading || elapsed >= 30000) resolve(false);
                else { elapsed += 100; setTimeout(check, 100); }
            };
            check();
        });
    }
    _workshopVrmModulesLoading = true;

    // 等待 THREE 就绪
    if (typeof window.THREE === 'undefined') {
        await new Promise(resolve => {
            window.addEventListener('three-ready', resolve, { once: true });
        });
    }

    const vrmModules = [
        '/static/vrm/vrm-orientation.js',
        '/static/vrm/vrm-core.js',
        '/static/vrm/vrm-expression.js',
        '/static/vrm/vrm-animation.js',
        '/static/vrm/vrm-interaction.js',
        '/static/vrm/vrm-cursor-follow.js',
        '/static/vrm/vrm-manager.js'
    ];

    for (const moduleSrc of vrmModules) {
        // 检查是否已通过其他途径加载
        if (moduleSrc.includes('vrm-manager') && typeof window.VRMManager !== 'undefined') continue;
        if (moduleSrc.includes('vrm-core') && typeof window.VRMCore !== 'undefined') continue;

        const script = document.createElement('script');
        script.src = `${moduleSrc}?v=${Date.now()}`;
        await new Promise((resolve) => {
            script.onload = resolve;
            script.onerror = () => {
                console.error(`[Workshop VRM] 模块加载失败: ${moduleSrc}`);
                resolve();
            };
            document.body.appendChild(script);
        });
    }

    _workshopVrmModulesLoaded = typeof window.VRMManager !== 'undefined';
    _workshopVrmModulesLoading = false;
    return _workshopVrmModulesLoaded;
}

// 按需加载 MMD 模块
async function ensureMmdModulesLoaded() {
    if (_workshopMmdModulesLoaded) return true;
    if (_workshopMmdModulesLoading) {
        return new Promise((resolve) => {
            let elapsed = 0;
            const check = () => {
                if (_workshopMmdModulesLoaded) resolve(true);
                else if (!_workshopMmdModulesLoading || elapsed >= 30000) resolve(false);
                else { elapsed += 100; setTimeout(check, 100); }
            };
            check();
        });
    }
    _workshopMmdModulesLoading = true;

    if (typeof window.THREE === 'undefined') {
        await new Promise(resolve => {
            window.addEventListener('three-ready', resolve, { once: true });
        });
    }

    const mmdModules = [
        '/static/mmd/mmd-core.js',
        '/static/mmd/mmd-animation.js',
        '/static/mmd/mmd-expression.js',
        '/static/mmd/mmd-interaction.js',
        '/static/mmd/mmd-cursor-follow.js',
        '/static/mmd/mmd-manager.js'
    ];

    for (const moduleSrc of mmdModules) {
        if (moduleSrc.includes('mmd-manager') && typeof window.MMDManager !== 'undefined') continue;
        if (moduleSrc.includes('mmd-core') && typeof window.MMDCore !== 'undefined') continue;

        const script = document.createElement('script');
        script.src = `${moduleSrc}?v=${Date.now()}`;
        await new Promise((resolve) => {
            script.onload = resolve;
            script.onerror = () => {
                console.error(`[Workshop MMD] 模块加载失败: ${moduleSrc}`);
                resolve();
            };
            document.body.appendChild(script);
        });
    }

    _workshopMmdModulesLoaded = typeof window.MMDManager !== 'undefined';
    _workshopMmdModulesLoading = false;
    return _workshopMmdModulesLoaded;
}

// 隐藏所有 3D 预览容器
function hideAll3DPreviews() {
    const vrmContainer = document.getElementById('vrm-preview-container');
    const mmdContainer = document.getElementById('mmd-preview-container');
    if (vrmContainer) vrmContainer.style.display = 'none';
    if (mmdContainer) mmdContainer.style.display = 'none';
}

// 清理工坊 VRM 预览实例
async function disposeWorkshopVrm() {
    if (workshopVrmManager) {
        try {
            if (typeof workshopVrmManager.dispose === 'function') {
                await workshopVrmManager.dispose();
            }
        } catch (e) {
            console.warn('[Workshop VRM] dispose 失败:', e);
        }
        workshopVrmManager = null;
    }
    hideAll3DPreviews();
}

// 清理工坊 MMD 预览实例
async function disposeWorkshopMmd() {
    if (workshopMmdManager) {
        try {
            if (typeof workshopMmdManager.stopAnimation === 'function') {
                workshopMmdManager.stopAnimation();
            }
            if (typeof workshopMmdManager.dispose === 'function') {
                await workshopMmdManager.dispose();
            }
        } catch (e) {
            console.warn('[Workshop MMD] dispose 失败:', e);
        }
        workshopMmdManager = null;
    }
    hideAll3DPreviews();
}

function syncWorkshop3DPreviewSize(manager, canvasId) {
    if (!manager || !manager.renderer) return false;

    const previewContent = document.getElementById('live2d-preview-content');
    const canvas = canvasId ? document.getElementById(canvasId) : (manager.renderer.domElement || null);
    const rect = previewContent ? previewContent.getBoundingClientRect() : null;
    const w = Math.max(1, Math.round(rect?.width || previewContent?.clientWidth || canvas?.clientWidth || 0));
    const h = Math.max(1, Math.round(rect?.height || previewContent?.clientHeight || canvas?.clientHeight || 0));
    if (w <= 1 || h <= 1) return false;

    if (canvas) {
        canvas.style.width = '100%';
        canvas.style.height = '100%';
        canvas.width = Math.round(w * (window.devicePixelRatio || 1));
        canvas.height = Math.round(h * (window.devicePixelRatio || 1));
    }

    manager.renderer.setSize(w, h, false);
    if (manager.camera) {
        manager.camera.aspect = w / h;
        manager.camera.updateProjectionMatrix();
    }
    if (manager.effect && typeof manager.effect.setSize === 'function') {
        manager.effect.setSize(w, h);
    }
    return true;
}

function scheduleWorkshop3DPreviewResize(manager, canvasId) {
    requestAnimationFrame(() => {
        syncWorkshop3DPreviewSize(manager, canvasId);
        requestAnimationFrame(() => syncWorkshop3DPreviewSize(manager, canvasId));
    });
}

// 加载 VRM 模型预览
async function loadVrmPreview(modelPath, rawData) {
    const previewGeneration = ++_workshopPreviewGeneration;
    let localVrmManager = null;
    try {
        cancelPendingLive2DPreviewLoads();
        selectedModelInfo = null;
        setLive2DPreviewRefreshButtonState(false, false);

        // 先清理之前的 3D 预览
        await disposeWorkshopVrm();
        await disposeWorkshopMmd();
        if (!isWorkshopPreviewLoadCurrent(previewGeneration)) return;

        // 清理 Live2D 预览（如果有）
        if (live2dPreviewManager && live2dPreviewManager.currentModel) {
            await live2dPreviewManager.removeModel({ skipCloseWindows: true });
            currentPreviewModel = null;
        }

        // 隐藏 Live2D canvas 和占位符
        const live2dCanvas = document.getElementById('live2d-preview-canvas');
        const placeholder = document.querySelector('#live2d-preview-content .preview-placeholder');
        if (live2dCanvas) live2dCanvas.style.display = 'none';
        if (placeholder) placeholder.style.display = 'none';

        // 更新标题
        const title = document.getElementById('model-preview-title');
        if (title) title.textContent = 'VRM';

        // 隐藏 Live2D 控件
        const live2dControls = document.getElementById('live2d-preview-controls');
        if (live2dControls) live2dControls.style.display = 'none';

        // 确保 VRM 模块已加载
        const loaded = await ensureVrmModulesLoaded();
        if (!isWorkshopPreviewLoadCurrent(previewGeneration)) return;
        if (!loaded) {
            console.error('[Workshop VRM] 模块加载失败');
            showMessage(window.t ? window.t('steam.vrmModuleLoadFailed') || 'VRM 模块加载失败' : 'VRM 模块加载失败', 'error');
            return;
        }

        // 显示 VRM 容器
        const vrmContainer = document.getElementById('vrm-preview-container');
        if (vrmContainer) vrmContainer.style.display = 'block';

        // 创建 VRM 管理器实例
        localVrmManager = new window.VRMManager();
        workshopVrmManager = localVrmManager;

        // 获取光照配置
        const lighting = rawData?.['lighting'] || null;

        // 初始化 Three.js 场景
        await localVrmManager.initThreeJS('vrm-preview-canvas', 'vrm-preview-container', lighting);
        if (!isWorkshopPreviewLoadCurrent(previewGeneration) || workshopVrmManager !== localVrmManager) {
            await disposeStaleWorkshopPreviewManager(localVrmManager, 'vrm');
            return;
        }

        // 修正容器样式：VRMCore.init 会设置 position:fixed 覆盖全屏，
        // 这里覆盖为 absolute 使其嵌入预览区域内
        const vrmContainerEl = document.getElementById('vrm-preview-container');
        if (vrmContainerEl) {
            vrmContainerEl.style.position = 'absolute';
            vrmContainerEl.style.top = '0';
            vrmContainerEl.style.left = '0';
            vrmContainerEl.style.width = '100%';
            vrmContainerEl.style.height = '100%';
            vrmContainerEl.style.zIndex = '10';
        }

        // 按预览区域实际尺寸同步 renderer / camera / effect，避免 CSS 尺寸和 WebGL 后备尺寸不一致。
        const previewContent = document.getElementById('live2d-preview-content');
        syncWorkshop3DPreviewSize(localVrmManager, 'vrm-preview-canvas');

        // 允许 3D 交互：临时启用预览区域的 pointer-events
        if (previewContent) previewContent.style.pointerEvents = 'auto';
        const overlay = document.getElementById('live2d-preview-overlay');
        if (overlay) overlay.style.display = 'none';

        // 获取 idle 动画路径
        const idleAnimation = rawData?.['idleAnimation'] || '/static/vrm/animation/wait03.vrma';

        // 加载模型
        const result = await localVrmManager.loadModel(modelPath, {
            canvasId: 'vrm-preview-canvas',
            containerId: 'vrm-preview-container',
            addShadow: true,
            idleAnimation: idleAnimation
        });
        if (!isWorkshopPreviewLoadCurrent(previewGeneration) || workshopVrmManager !== localVrmManager) {
            await disposeStaleWorkshopPreviewManager(localVrmManager, 'vrm');
            return;
        }

        if (result) {
            scheduleWorkshop3DPreviewResize(localVrmManager, 'vrm-preview-canvas');
            console.log('[Workshop VRM] 模型预览加载成功');
        }
    } catch (error) {
        console.error('[Workshop VRM] 加载预览失败:', error);
        await disposeStaleWorkshopPreviewManager(localVrmManager, 'vrm');
        currentPreviewModel = null;
        showMessage(window.t ? window.t('steam.vrmPreviewFailed') || 'VRM 模型预览加载失败' : 'VRM 模型预览加载失败', 'error');
    }
}

// 加载 MMD 模型预览
async function loadMmdPreview(modelPath, rawData) {
    const previewGeneration = ++_workshopPreviewGeneration;
    let localMmdManager = null;
    try {
        cancelPendingLive2DPreviewLoads();
        selectedModelInfo = null;
        setLive2DPreviewRefreshButtonState(false, false);

        // 先清理之前的 3D 预览
        await disposeWorkshopVrm();
        await disposeWorkshopMmd();
        if (!isWorkshopPreviewLoadCurrent(previewGeneration)) return;

        // 清理 Live2D 预览（如果有）
        if (live2dPreviewManager && live2dPreviewManager.currentModel) {
            await live2dPreviewManager.removeModel({ skipCloseWindows: true });
            currentPreviewModel = null;
        }

        // 隐藏 Live2D canvas 和占位符
        const live2dCanvas = document.getElementById('live2d-preview-canvas');
        const placeholder = document.querySelector('#live2d-preview-content .preview-placeholder');
        if (live2dCanvas) live2dCanvas.style.display = 'none';
        if (placeholder) placeholder.style.display = 'none';

        // 更新标题
        const title = document.getElementById('model-preview-title');
        if (title) title.textContent = 'MMD';

        // 隐藏 Live2D 控件
        const live2dControls = document.getElementById('live2d-preview-controls');
        if (live2dControls) live2dControls.style.display = 'none';

        // 确保 MMD 模块已加载
        const loaded = await ensureMmdModulesLoaded();
        if (!isWorkshopPreviewLoadCurrent(previewGeneration)) return;
        if (!loaded) {
            console.error('[Workshop MMD] 模块加载失败');
            showMessage(window.t ? window.t('steam.mmdModuleLoadFailed') || 'MMD 模块加载失败' : 'MMD 模块加载失败', 'error');
            return;
        }

        // 显示 MMD 容器
        const mmdContainer = document.getElementById('mmd-preview-container');
        if (mmdContainer) mmdContainer.style.display = 'block';

        // 创建 MMD 管理器实例
        localMmdManager = new window.MMDManager();
        workshopMmdManager = localMmdManager;

        // 初始化
        await localMmdManager.init('mmd-preview-canvas', 'mmd-preview-container');
        if (!isWorkshopPreviewLoadCurrent(previewGeneration) || workshopMmdManager !== localMmdManager) {
            await disposeStaleWorkshopPreviewManager(localMmdManager, 'mmd');
            return;
        }

        // 修正容器样式：MMDCore.init 会设置 position:fixed 覆盖全屏，
        // 这里覆盖为 absolute 使其嵌入预览区域内
        const mmdContainerEl = document.getElementById('mmd-preview-container');
        if (mmdContainerEl) {
            mmdContainerEl.style.position = 'absolute';
            mmdContainerEl.style.top = '0';
            mmdContainerEl.style.left = '0';
            mmdContainerEl.style.width = '100%';
            mmdContainerEl.style.height = '100%';
            mmdContainerEl.style.zIndex = '10';
        }

        // 按预览区域实际尺寸同步 renderer / camera / effect，避免 CSS 尺寸和 WebGL 后备尺寸不一致。
        const previewContent = document.getElementById('live2d-preview-content');
        syncWorkshop3DPreviewSize(localMmdManager, 'mmd-preview-canvas');

        // 允许 3D 交互：临时启用预览区域的 pointer-events
        if (previewContent) previewContent.style.pointerEvents = 'auto';
        const overlay = document.getElementById('live2d-preview-overlay');
        if (overlay) overlay.style.display = 'none';

        // 加载模型
        const modelInfo = await localMmdManager.loadModel(modelPath);
        if (!isWorkshopPreviewLoadCurrent(previewGeneration) || workshopMmdManager !== localMmdManager) {
            await disposeStaleWorkshopPreviewManager(localMmdManager, 'mmd');
            return;
        }

        if (modelInfo) {
            scheduleWorkshop3DPreviewResize(localMmdManager, 'mmd-preview-canvas');
            // 如果有 idle 动画，尝试加载
            const idleAnimation = rawData?.['mmd_idle_animation'] || '';
            if (idleAnimation && typeof localMmdManager.loadAnimation === 'function') {
                try {
                    await localMmdManager.loadAnimation(idleAnimation);
                    if (!isWorkshopPreviewLoadCurrent(previewGeneration) || workshopMmdManager !== localMmdManager) {
                        await disposeStaleWorkshopPreviewManager(localMmdManager, 'mmd');
                        return;
                    }
                    localMmdManager.playAnimation();
                } catch (e) {
                    console.warn('[Workshop MMD] idle 动画加载失败:', e);
                }
            }
            console.log('[Workshop MMD] 模型预览加载成功');
        }
    } catch (error) {
        console.error('[Workshop MMD] 加载预览失败:', error);
        await disposeStaleWorkshopPreviewManager(localMmdManager, 'mmd');
        currentPreviewModel = null;
        showMessage(window.t ? window.t('steam.mmdPreviewFailed') || 'MMD 模型预览加载失败' : 'MMD 模型预览加载失败', 'error');
    }
}

// 清除所有模型预览（Live2D + VRM + MMD）
async function clearAllModelPreviews(showModelNotSetMessage = false) {
    cancelWorkshopPreviewLoads();
    selectedModelInfo = null;
    setLive2DPreviewRefreshButtonState(false, false);
    await disposeWorkshopVrm();
    await disposeWorkshopMmd();
    hideAll3DPreviews();

    // 恢复 Live2D 预览区域的 pointer-events 和 overlay
    const previewContent = document.getElementById('live2d-preview-content');
    if (previewContent) previewContent.style.pointerEvents = 'none';
    const overlay = document.getElementById('live2d-preview-overlay');
    if (overlay) overlay.style.display = '';

    // 恢复 Live2D 标题和控件
    const title = document.getElementById('model-preview-title');
    if (title) {
        title.textContent = 'Live2D';
        title.setAttribute('data-i18n', 'steam.live2dPreview');
    }
    const live2dControls = document.getElementById('live2d-preview-controls');
    if (live2dControls) live2dControls.style.display = '';

    await clearLive2DPreview(showModelNotSetMessage);
}

// 清除Live2D预览并显示占位符
async function clearLive2DPreview(showModelNotSetMessage = false) {
    try {
        cancelPendingLive2DPreviewLoads();
        selectedModelInfo = null;
        window._previewMotionFiles = [];
        setLive2DPreviewRefreshButtonState(false, false);

        // 如果有模型加载，先移除它
        if (live2dPreviewManager && typeof live2dPreviewManager.removeModel === 'function') {
            await live2dPreviewManager.removeModel({ skipCloseWindows: true });
        }
        currentPreviewModel = null;

        // 隐藏canvas，显示占位符
        const canvas = document.getElementById('live2d-preview-canvas');
        const placeholder = document.querySelector('#live2d-preview-content .preview-placeholder');

        if (canvas) {
            canvas.style.display = 'none';
        }

        if (placeholder) {
            placeholder.style.display = 'flex';
            // 根据参数显示不同的提示文本
            const span = placeholder.querySelector('span');
            const getText = (key, fallback) => {
                if (!window.t) return fallback;
                const raw = window.t(key);
                return (raw && typeof raw === 'string' && raw !== key) ? raw : fallback;
            };
            const modelNotSetText = getText('steam.characterModelNotSet', '当前角色未设置模型');
            const selectCharText = getText('steam.selectCharaToPreview', '请选择角色进行预览');
            const isModelNotSet = showModelNotSetMessage === true;
            if (span) {
                if (isModelNotSet) {
                    span.textContent = modelNotSetText;
                    span.setAttribute('data-i18n', 'steam.characterModelNotSet');
                } else {
                    span.textContent = selectCharText;
                    span.setAttribute('data-i18n', 'steam.selectCharaToPreview');
                }
            }
            // 同步更新环形文字
            if (typeof buildPreviewRing === 'function') {
                buildPreviewRing(isModelNotSet ? modelNotSetText : selectCharText);
            }
        }

    } catch (error) {
        console.error('清除Live2D预览失败:', error);
    }
}

async function destroyLive2DPreviewContext() {
    const manager = live2dPreviewManager;
    cancelPendingLive2DPreviewLoads();
    selectedModelInfo = null;
    currentPreviewModel = null;
    window._previewMotionFiles = [];
    setLive2DPreviewRefreshButtonState(false, false);

    if (!manager) {
        return;
    }

    if (typeof manager._activeLoadToken === 'number') {
        manager._activeLoadToken += 1;
    }

    try {
        await clearLive2DPreview();
    } finally {
        manager._isLoadingModel = false;
        manager._modelLoadState = 'idle';
        manager._isModelReadyForInteraction = false;

        if (manager._canvasRevealTimer) {
            clearTimeout(manager._canvasRevealTimer);
            manager._canvasRevealTimer = null;
        }

        try {
            if (manager.pixi_app && manager.pixi_app.view && manager.pixi_app.view.style) {
                manager.pixi_app.view.style.transition = '';
                manager.pixi_app.view.style.opacity = '';
            }
        } catch (_) {}

        if (manager._previewResizeHandlerBound && manager._previewResizeHandler) {
            window.removeEventListener('resize', manager._previewResizeHandler);
        }
        manager._previewResizeHandlerBound = false;
        manager._previewResizeHandler = null;

        if (manager._screenChangeHandler) {
            window.removeEventListener('resize', manager._screenChangeHandler);
            manager._screenChangeHandler = null;
        }
        if (manager._displayChangeHandler) {
            window.removeEventListener('electron-display-changed', manager._displayChangeHandler);
            manager._displayChangeHandler = null;
        }

        if (typeof manager._stopIdleFpsGovernor === 'function') {
            manager._stopIdleFpsGovernor();
        }

        if (manager.pixi_app && typeof manager.pixi_app.destroy === 'function') {
            try {
                manager.pixi_app.destroy(true);
            } catch (destroyError) {
                console.warn('[CharacterCard] 销毁 Live2D 预览 PIXI 实例失败:', destroyError);
            }
        }

        manager.pixi_app = null;
        manager.currentModel = null;
        manager.isInitialized = false;
        manager._lastPIXIContext = { canvasId: null, containerId: null };
        live2dPreviewManager = null;
    }
}

// 通过模型名称加载Live2D模型
async function loadLive2DModelByName(modelName, modelInfo = null) {
    const loadGeneration = beginLive2DPreviewLoadGeneration();
    let loadedModel = null;
    setLive2DPreviewRefreshButtonState(false, false);
    const ensureCurrentLoad = async () => {
        if (isCurrentLive2DPreviewLoad(loadGeneration)) {
            return;
        }

        if (loadedModel && live2dPreviewManager?.currentModel === loadedModel) {
            try {
                await live2dPreviewManager.removeModel({ skipCloseWindows: true });
            } catch (cleanupError) {
                console.warn('[CharacterCard] 清理过期 Live2D 预览失败:', cleanupError);
            }
        }

        const staleError = new Error('Stale Live2D preview load');
        staleError.code = 'STALE_LIVE2D_PREVIEW_LOAD';
        throw staleError;
    };

    try {
        // 每次加载前都重新校验预览上下文。
        // Steam 详情面板会动态销毁并重建 canvas，仅凭 manager 是否存在
        // 无法判断它是否还绑定在当前这次打开的预览节点上。
        await initLive2DPreview();
        await ensureCurrentLoad();
        if (!live2dPreviewManager || !live2dPreviewManager.pixi_app) {
            throw new Error('Live2D preview is not ready');
        }

        // 强制resize PIXI应用，确保canvas尺寸正确
        // 这是必要的，因为当容器最初是隐藏的(display:none)时，PIXI的尺寸会是0
        if (live2dPreviewManager && live2dPreviewManager.pixi_app) {
            const container = document.getElementById('live2d-preview-content');
            if (container && container.clientWidth > 0 && container.clientHeight > 0) {
                live2dPreviewManager.pixi_app.renderer.resize(container.clientWidth, container.clientHeight);
            }
        }

        // 如果已经有模型加载，先移除它
        if (live2dPreviewManager && live2dPreviewManager.currentModel) {
            await live2dPreviewManager.removeModel({ skipCloseWindows: true });
            // 重置当前预览模型引用
            currentPreviewModel = null;
        }
        await ensureCurrentLoad();

        // 如果没有传入modelInfo，则从API获取模型列表
        if (!modelInfo) {
            // 调用API获取模型列表，找到对应模型的信息
            const response = await fetch('/api/live2d/models');
            if (!response.ok) {
                throw new Error(`HTTP错误，状态码: ${response.status}`);
            }

            const models = await response.json();
            modelInfo = models.find(model => model.name === modelName);

            if (!modelInfo) {
                throw new Error(window.t('steam.modelNotFound', '模型未找到'));
            }
        }
        await ensureCurrentLoad();

        // 确保获取正确的steam_id，优先使用modelInfo中的item_id
        let finalSteamId = modelInfo.item_id;

        // 1. Fetch files list
        let filesRes;
        // 根据modelInfo的source字段和finalSteamId决定使用哪个API端点
        if (modelInfo.source === 'user_mods') {
            // 对于用户mod模型，使用modelName构建URL
            filesRes = await fetch(`/api/live2d/model_files/${encodeURIComponent(modelName)}`);
        } else if (finalSteamId && finalSteamId !== 'undefined') {
            // 如果提供了finalSteamId，调用专门的API端点
            filesRes = await fetch(`/api/live2d/model_files_by_id/${finalSteamId}`);
        } else {
            // 否则使用原来的API端点
            filesRes = await fetch(`/api/live2d/model_files/${encodeURIComponent(modelName)}`);
        }
        const filesData = await filesRes.json();
        if (!filesData.success) throw new Error(window.t('live2d.modelFilesFetchFailed', '无法获取模型文件列表'));
        await ensureCurrentLoad();
        window._previewMotionFiles = filesData.motion_files || [];

        // 2. Fetch model config
        let modelJsonUrl;
        // 优先使用后端返回的model_config_url（如果有）
        if (filesData.model_config_url) {
            modelJsonUrl = filesData.model_config_url;
        } else if (modelInfo.source === 'user_mods') {
            // 对于用户mod模型，直接使用modelInfo.path（已经包含/user_mods/路径）
            modelJsonUrl = modelInfo.path;
        } else if (finalSteamId && finalSteamId !== 'undefined') {
            // 如果提供了finalSteamId但没有model_config_url，使用兼容模式构建URL
            // 注意：上传后的目录结构是 workshop/{item_id}/{model_name}/{model_name}.model3.json
            modelJsonUrl = `/workshop/${finalSteamId}/${modelName}/${modelName}.model3.json`;
        } else {
            // 否则使用原来的路径
            modelJsonUrl = modelInfo.path;
        }
        const modelConfigRes = await fetch(modelJsonUrl);
        if (!modelConfigRes.ok) throw new Error((window.t && window.t('live2d.modelConfigFetchFailed', { status: modelConfigRes.statusText })) || `无法获取模型配置: ${modelConfigRes.statusText}`);
        const modelConfig = await modelConfigRes.json();
        await ensureCurrentLoad();

        // 3. Add URL context for the loader
        modelConfig.url = modelJsonUrl;

        // 4. Inject PreviewAll motion group AND ensure all expressions are referenced
        if (!modelConfig.FileReferences) modelConfig.FileReferences = {};

        // Motions
        if (!modelConfig.FileReferences.Motions) modelConfig.FileReferences.Motions = {};
        // 只有当模型有动作文件时才添加PreviewAll组
        if (filesData.motion_files.length > 0) {
            modelConfig.FileReferences.Motions.PreviewAll = filesData.motion_files.map(file => ({
                File: file  // 直接使用API返回的完整路径
            }));
        }

        // Expressions: Overwrite with all available expression files for preview purposes.
        modelConfig.FileReferences.Expressions = filesData.expression_files.map(file => ({
            Name: file.split('/').pop().replace('.exp3.json', ''),  // 从路径中提取文件名作为名称
            File: file  // 直接使用API返回的完整路径
        }));

        // 5. Load preferences (如果需要)
        // const preferences = await live2dPreviewManager.loadUserPreferences();
        // const modelPreferences = preferences.find(p => p && p.model_path === modelInfo.path) || null;

        // 6. Load model FROM THE MODIFIED OBJECT
        await live2dPreviewManager.loadModel(modelConfig, {
            loadEmotionMapping: true,
            dragEnabled: true,
            wheelEnabled: true,
            skipCloseWindows: true  // 创意工坊页面不需要关闭其他窗口
        });
        loadedModel = live2dPreviewManager.currentModel || null;
        await ensureCurrentLoad();

        // 设置当前预览模型引用，用于播放动作和表情
        currentPreviewModel = loadedModel;

        // 清除模型路径，防止拖动预览时自动保存到preference
        live2dPreviewManager._lastLoadedModelPath = null;

        // 更新预览控件
        await updatePreviewControlsAfterModelLoad(filesData);
        await ensureCurrentLoad();

        // 模型加载完成后，确保它在容器中正确显示
        setTimeout(() => {
            if (!isCurrentLive2DPreviewLoad(loadGeneration)) {
                return;
            }

            const canvas = document.getElementById('live2d-preview-canvas');
            if (live2dPreviewManager && live2dPreviewManager.currentModel && canvas) {
                fitLive2DPreviewModelToContainer(live2dPreviewManager.currentModel);
                // 确保canvas正确显示，占位符被隐藏
                canvas.style.display = '';
                const placeholder = document.querySelector('#live2d-preview-content .preview-placeholder');
                if (placeholder) placeholder.style.display = 'none';
                // 强制重绘canvas
                if (live2dPreviewManager.pixi_app && live2dPreviewManager.pixi_app.renderer) {
                    live2dPreviewManager.pixi_app.renderer.render(live2dPreviewManager.pixi_app.stage);
                }
            }
        }, 100);

        // 更新全局selectedModelInfo变量
        selectedModelInfo = modelInfo;
        setLive2DPreviewRefreshButtonState(true, true);
    } catch (error) {
        if (error && error.code === 'STALE_LIVE2D_PREVIEW_LOAD') {
            return;
        }

        setLive2DPreviewRefreshButtonState(false, false);
        console.error('Failed to load Live2D model by name:', error);
        showMessage((window.t && window.t('live2d.modelLoadFailed', { model: modelName })) || `加载模型 ${modelName} 失败`, 'error');

        // 在加载失败时隐藏预览控件
        hidePreviewControls();
    }
}

// 刷新Live2D预览
async function refreshLive2DPreview() {
    // 检查当前角色是否有设置模型
    if (!selectedModelInfo || !selectedModelInfo.name) {
        showMessage(window.t('characterModelNotSet', '当前角色未设置模型'), 'warning');
        return;
    }

    // 重新加载当前模型
    await loadLive2DModelByName(selectedModelInfo.name, selectedModelInfo);
}

// 模型加载后更新预览控件
async function updatePreviewControlsAfterModelLoad(filesData) {
    if (!live2dPreviewManager) {
        return;
    }

    // 检查filesData是否存在
    if (!filesData || !filesData.motion_files || !filesData.expression_files) {
        console.error('Invalid filesData object:', filesData);
        return;
    }

    // 显示Canvas，隐藏占位符
    const canvas = document.getElementById('live2d-preview-canvas');
    const placeholder = document.querySelector('.preview-placeholder');
    if (canvas) canvas.style.display = '';
    if (placeholder) placeholder.style.display = 'none';

    // 启用预览控件
    const motionSelect = document.getElementById('preview-motion-select');
    const expressionSelect = document.getElementById('preview-expression-select');
    const playMotionBtn = document.getElementById('preview-play-motion-btn');
    const playExpressionBtn = document.getElementById('preview-play-expression-btn');

    if (motionSelect) motionSelect.disabled = false;
    if (expressionSelect) expressionSelect.disabled = false;
    if (playMotionBtn) playMotionBtn.disabled = false;
    if (playExpressionBtn) playExpressionBtn.disabled = false;

    // 显示预览控件区域
    const previewControls = document.getElementById('live2d-preview-controls');
    if (previewControls) {
        previewControls.style.display = 'block';
    }

    // 更新动作和表情列表
    try {
        updatePreviewControls(filesData.motion_files, filesData.expression_files);
    } catch (error) {
        console.error('Failed to update preview controls:', error);
    }

    // 恢复已保存的待机动作（如果存在）。显式保留空值，避免“无动作”被浏览器默认选中第一个 option。
    const rawData = window._currentCardRawData || {};
    const savedIdleAnimation = rawData._reserved?.avatar?.live2d?.idle_animation
        || rawData.avatar?.live2d?.idle_animation
        || rawData.live2d_idle_animation
        || '';
    const savedIdleAnimationBaseName = savedIdleAnimation
        ? String(savedIdleAnimation).split('/').pop()
        : '';
    const availableMotionFiles = window._previewMotionFiles || [];
    let initialMotionToPlay = '';
    if (motionSelect) {
        motionSelect.value = '';
    }
    if (savedIdleAnimationBaseName && motionSelect) {
        const matchingSavedMotion = availableMotionFiles.find(file => {
            const normalizedFile = String(file || '');
            return normalizedFile === savedIdleAnimation
                || normalizedFile.split('/').pop() === savedIdleAnimationBaseName;
        });
        if (matchingSavedMotion) {
            motionSelect.value = matchingSavedMotion;
            initialMotionToPlay = matchingSavedMotion;
        }
    }

    const previewModelToAutoplay = currentPreviewModel;

    if (live2dPreviewManager) {
        live2dPreviewManager._userIdleAnimations = initialMotionToPlay
            ? [String(initialMotionToPlay).split('/').pop()]
            : [];
    }

    const scheduledMotionSelection = motionSelect ? motionSelect.value : '';

    if (initialMotionToPlay && previewModelToAutoplay) {
        requestAnimationFrame(() => {
            if (
                currentPreviewModel === previewModelToAutoplay
                && live2dPreviewManager?.currentModel === previewModelToAutoplay
                && motionSelect
                && motionSelect.value === scheduledMotionSelection
            ) {
                handlePreviewMotionPlay();
            }
        });
    }
}

// 更新角色卡信息预览（动态渲染所有属性）
function updateCardPreview() {
    const container = document.getElementById('card-info-dynamic-content');
    if (!container) return;

    // 从已加载的角色卡列表中获取当前角色卡数据
    if (!currentCharacterCardId || !window.characterCards) {
        container.innerHTML = `<p style="color: #999; text-align: center;">` +
            (window.t ? window.t('steam.selectCharacterCard') : '请选择一个角色卡') + '</p>';
        return;
    }

    const currentCard = window.characterCards.find(card => card.id === currentCharacterCardId);
    if (!currentCard) {
        container.innerHTML = `<p style="color: #999; text-align: center;">` +
            (window.t ? window.t('steam.characterCardNotFound') : '找不到角色卡数据') + '</p>';
        return;
    }

    // 获取角色卡原始数据
    const rawData = currentCard.rawData || currentCard || {};

    // 保留字段（不显示）
    // 系统保留字段 + 工坊保留字段
    const hiddenFields = getWorkshopHiddenFields();

    // 清空容器
    container.innerHTML = '';

    // 遍历所有属性并动态生成显示
    for (const key of getOrderedCharacterFieldKeys(rawData, hiddenFields)) {
        const value = rawData[key];
        // 跳过保留字段
        if (hiddenFields.includes(key)) continue;

        // 跳过空值
        if (value === null || value === undefined || value === '') continue;

        // 创建属性行
        const row = document.createElement('div');
        row.style.cssText = `color: #000; margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1.5px solid #d5efff; word-wrap: break-word; overflow-wrap: break-word; max-width: 100%;`;

        // 格式化值
        let displayValue = '';
        if (Array.isArray(value)) {
            // 数组：用逗号分隔显示
            displayValue = value.join('、');
        } else if (typeof value === 'object') {
            // 对象：显示为 JSON（但跳过复杂嵌套对象）
            try {
                displayValue = JSON.stringify(value, null, 0);
            } catch (e) {
                displayValue = '[复杂对象]';
            }
        } else {
            displayValue = String(value);
        }

        // 构建HTML - 使用黑色文字，添加自动换行
        row.innerHTML = '<strong style="color: #000;">' + escapeHtml(key) + ':</strong> <span style="font-weight: normal; color: #000; word-wrap: break-word; overflow-wrap: break-word; display: inline-block; max-width: 100%;">' + escapeHtml(displayValue) + '</span>';
        container.appendChild(row);
    }

    // 如果没有任何属性显示，显示提示
    if (container.children.length === 0) {
        container.innerHTML = `<p style="color: #999; text-align: center;">` +
            (window.t ? window.t('steam.noCardProperties') : '暂无属性信息') + '</p>';
    }
}


// 为输入字段添加事件监听器，自动更新预览
document.addEventListener('DOMContentLoaded', function () {
    // 只有 description 输入框仍然存在，为其添加事件监听器
    const descriptionInput = document.getElementById('character-card-description');

    // 页面加载完成后自动加载音色列表
    loadVoices();

    if (descriptionInput) {
        descriptionInput.addEventListener('input', updateCardPreview);
    }

    window.addEventListener('resize', updateCharacterCardTagScrollControls);
    ensureCharacterCardTagScrollControls();
    window.setTimeout(updateCharacterCardTagScrollControls, 0);
});

// 添加标签（角色卡用）
function addCharacterCardTag(type, tagValue) {
    const tagText = String(tagValue || '').trim();
    if (!tagText) return;
    addTag(tagText, type);
}

// 清除所有标签
function clearTags(type) {
    const tagsContainer = document.getElementById(`${type}-tags-container`);
    tagsContainer.innerHTML = '';
    if (type === 'character-card') {
        updateCharacterCardTagScrollControls();
    }
}

// Live2D预览相关功能
let live2dPreviewManager = null;
let currentPreviewModel = null;
let live2dPreviewLoadGeneration = 0;

function beginLive2DPreviewLoadGeneration() {
    live2dPreviewLoadGeneration += 1;
    return live2dPreviewLoadGeneration;
}

function cancelPendingLive2DPreviewLoads() {
    live2dPreviewLoadGeneration += 1;
}

function isCurrentLive2DPreviewLoad(loadGeneration) {
    return loadGeneration === live2dPreviewLoadGeneration;
}

// 初始化Live2D预览环境
async function initLive2DPreview() {
    try {
        // 检查Live2DManager是否已定义
        if (typeof Live2DManager === 'undefined') {
            throw new Error('Live2DManager class not found');
        }

        const canvasId = 'live2d-preview-canvas';
        const containerId = 'live2d-preview-content';
        const canvas = document.getElementById(canvasId);
        const container = document.getElementById(containerId);

        // Steam 预览区域是动态创建的；在 DOM 尚未生成时静默跳过，
        // 避免页面初始加载阶段提前报错并污染后续初始化状态。
        if (!canvas || !container) {
            return;
        }

        if (!live2dPreviewManager) {
            live2dPreviewManager = new Live2DManager();
        }

        const existingView = live2dPreviewManager.pixi_app?.view || null;
        const needsPixiRebuild = !!(
            existingView && (
                existingView !== canvas ||
                !existingView.isConnected
            )
        );

        if (needsPixiRebuild && typeof live2dPreviewManager.rebuildPIXI === 'function') {
            await live2dPreviewManager.rebuildPIXI(canvasId, containerId);
        } else if (typeof live2dPreviewManager.ensurePIXIReady === 'function') {
            await live2dPreviewManager.ensurePIXIReady(canvasId, containerId);
        } else if (!live2dPreviewManager.pixi_app) {
            await live2dPreviewManager.initPIXI(canvasId, containerId);
        }

        // 覆盖applyModelSettings方法，为预览模式实现专门的显示逻辑
        if (!live2dPreviewManager._previewApplyModelSettingsPatched) {
            const originalApplyModelSettings = live2dPreviewManager.applyModelSettings;
            live2dPreviewManager.applyModelSettings = function (model, options) {
                // 获取预览容器的尺寸
                const previewContainer = document.getElementById(containerId);
                if (!previewContainer || !this.pixi_app || !this.pixi_app.renderer) {
                    return originalApplyModelSettings.call(this, model, options);
                }
                fitLive2DPreviewModelToContainer(model);
            };
            live2dPreviewManager._previewApplyModelSettingsPatched = true;
        }

        // 添加窗口大小变化的监听，当预览区域大小变化时重新计算模型缩放和位置
        if (!live2dPreviewManager._previewResizeHandlerBound) {
            function resizePreviewModel() {
                const previewContainer = document.getElementById(containerId);
                if (live2dPreviewManager && live2dPreviewManager.pixi_app && previewContainer &&
                    previewContainer.clientWidth > 0 && previewContainer.clientHeight > 0) {
                    live2dPreviewManager.pixi_app.renderer.resize(previewContainer.clientWidth, previewContainer.clientHeight);
                }
                if (live2dPreviewManager && live2dPreviewManager.currentModel) {
                    // 调用我们覆盖的applyModelSettings方法，重新计算模型缩放和位置
                    live2dPreviewManager.applyModelSettings(live2dPreviewManager.currentModel, {});
                    if (live2dPreviewManager.pixi_app && live2dPreviewManager.pixi_app.renderer) {
                        live2dPreviewManager.pixi_app.renderer.render(live2dPreviewManager.pixi_app.stage);
                    }
                }
            }
            live2dPreviewManager._previewResizeHandler = resizePreviewModel;
            live2dPreviewManager._previewResizeHandlerBound = true;
            window.addEventListener('resize', resizePreviewModel);
        }

        // 添加removeModel方法的fallback，防止调用时出错
        if (!live2dPreviewManager.removeModel) {
            live2dPreviewManager.removeModel = async function (force) {
                try {
                    if (this.currentModel && this.pixi_app && this.pixi_app.stage) {
                        // 移除当前模型
                        this.pixi_app.stage.removeChild(this.currentModel);
                        this.currentModel = null;

                        // 如果有清理资源的方法，调用它
                        if (this.disposeCurrentModel) {
                            await this.disposeCurrentModel();
                        }
                    }
                } catch (error) {
                    console.error('Error removing model:', error);
                }
            };
        }

    } catch (error) {
        console.error('Failed to initialize Live2D preview:', error);
        live2dPreviewManager = null;
        showMessage(window.t('steam.live2dInitFailed'), 'error');
    }
}

// 从文件夹加载Live2D模型
async function loadLive2DModelFromFolder(files) {
    try {
        await initLive2DPreview();
        if (!live2dPreviewManager || !live2dPreviewManager.pixi_app) {
            throw new Error('Live2D preview is not ready');
        }

        // 获取第一个文件夹的名称
        const firstFolder = files[0].webkitRelativePath.split('/')[0];

        // 查找模型配置文件
        const modelConfigFile = files.find(file =>
            file.name.toLowerCase().endsWith('.model3.json') &&
            file.webkitRelativePath.startsWith(firstFolder + '/')
        );

        if (!modelConfigFile) {
            throw new Error(window.t('steam.modelConfigNotFound', '模型配置文件未找到'));
        }

        // 读取模型配置文件内容
        const modelConfigContent = await modelConfigFile.text();
        const modelConfig = JSON.parse(modelConfigContent);

        // 创建一个临时的模型加载环境
        const modelFiles = {};

        // 收集所有模型相关文件
        const motionFiles = [];
        const expressionFiles = [];

        for (const file of files) {
            if (file.webkitRelativePath.startsWith(firstFolder + '/')) {
                const relativePath = file.webkitRelativePath.substring(firstFolder.length + 1);
                modelFiles[relativePath] = file;

                // 收集动作文件
                if (file.name.toLowerCase().endsWith('.motion3.json')) {
                    motionFiles.push(relativePath);
                }
                // 收集表情文件
                if (file.name.toLowerCase().endsWith('.exp3.json')) {
                    expressionFiles.push(relativePath);
                }
            }
        }

        // 添加PreviewAll动作组到模型配置
        if (!modelConfig.FileReferences) modelConfig.FileReferences = {};
        if (!modelConfig.FileReferences.Motions) modelConfig.FileReferences.Motions = {};

        if (motionFiles.length > 0) {
            modelConfig.FileReferences.Motions.PreviewAll = motionFiles.map(file => ({
                File: file
            }));
        }

        // 更新表情引用
        if (expressionFiles.length > 0) {
            modelConfig.FileReferences.Expressions = expressionFiles.map(file => ({
                Name: file.split('/').pop().replace('.exp3.json', ''),
                File: file
            }));
        }

        // 加载模型 - 禁用所有交互功能
        currentPreviewModel = await live2dPreviewManager.loadModelFromFiles(modelConfig, modelFiles, {
            onProgress: (progress) => {
            },
            dragEnabled: false,
            wheelEnabled: false,
            touchZoomEnabled: false,
            mouseTracking: false
        });

        // 显示Canvas，隐藏占位符
        document.getElementById('live2d-preview-canvas').style.display = '';
        document.querySelector('.preview-placeholder').style.display = 'none';

        // 更新预览控件
        updatePreviewControls(motionFiles, expressionFiles);

        // 禁用所有交互功能
        live2dPreviewManager.setLocked(true, { updateFloatingButtons: false });
        // 直接禁用canvas的pointerEvents，确保点击拖动无效
        const previewCanvas = document.getElementById('live2d-preview-canvas');
        if (previewCanvas) {
            previewCanvas.style.pointerEvents = 'none';
        }

        // 确保覆盖层处于激活状态，阻挡所有鼠标事件
        const previewOverlay = document.getElementById('live2d-preview-overlay');
        if (previewOverlay) {
            previewOverlay.style.pointerEvents = 'auto';
        }

    } catch (error) {
        console.error('Failed to load Live2D model:', error);
        showMessage(window.t('steam.live2dPreviewLoadFailed', { error: error.message }), 'error');

        // 在加载失败时隐藏预览控件
        hidePreviewControls();
    }
}

// 隐藏预览控件
function hidePreviewControls() {
    // 隐藏预览控件
    const previewControls = document.getElementById('live2d-preview-controls');
    if (previewControls) {
        previewControls.style.display = 'none';
    }

    // 显示占位符
    document.querySelector('.preview-placeholder').style.display = '';

    // 清空并禁用动作和表情选择器
    const motionSelect = document.getElementById('preview-motion-select');
    const expressionSelect = document.getElementById('preview-expression-select');
    const playMotionBtn = document.getElementById('preview-play-motion-btn');
    const playExpressionBtn = document.getElementById('preview-play-expression-btn');

    if (motionSelect) {
        motionSelect.innerHTML = '<option value="">' + window.t('live2d.pleaseLoadModel', '请先加载模型') + '</option>';
        motionSelect.disabled = true;
    }

    if (expressionSelect) {
        expressionSelect.innerHTML = '<option value="">' + window.t('live2d.pleaseLoadModel', '请先加载模型') + '</option>';
        expressionSelect.disabled = true;
    }

    if (playMotionBtn) {
        playMotionBtn.disabled = true;
    }

    if (playExpressionBtn) {
        playExpressionBtn.disabled = true;
    }
}

// 更新预览控件
function updatePreviewControls(motionFiles, expressionFiles) {
    const motionSelect = document.getElementById('preview-motion-select');
    const expressionSelect = document.getElementById('preview-expression-select');
    const playMotionBtn = document.getElementById('preview-play-motion-btn');
    const playExpressionBtn = document.getElementById('preview-play-expression-btn');
    const previewControls = document.getElementById('live2d-preview-controls');

    // 检查必要的DOM元素是否存在
    if (!motionSelect || !expressionSelect || !playMotionBtn || !playExpressionBtn) {
        console.error('Missing required DOM elements for preview controls');
        return;
    }

    // 清空现有选项
    motionSelect.innerHTML = '';
    expressionSelect.innerHTML = '';

    // 更新动作选择框：始终提供空选项，允许保存“无待机动作”。
    const emptyMotionOption = document.createElement('option');
    emptyMotionOption.value = '';
    emptyMotionOption.textContent = (window.t && window.t('character.noIdleMotion', '无动作')) || '无动作';
    motionSelect.appendChild(emptyMotionOption);

    if (motionFiles.length > 0) {
        motionSelect.disabled = false;
        playMotionBtn.disabled = false;
        motionSelect.value = '';

        // 添加动作选项（value 使用文件名，便于直接作为 live2d_idle_animation）
        motionFiles.forEach((motionFile) => {
            const option = document.createElement('option');
            option.value = motionFile;
            option.textContent = motionFile;
            motionSelect.appendChild(option);
        });
    } else {
        motionSelect.disabled = true;
        playMotionBtn.disabled = true;
        emptyMotionOption.textContent = (window.t && window.t('live2d.noMotionFiles', '没有动作文件')) || '没有动作文件';
    }

    // 更新表情选择框：始终提供空选项，避免默认选中第一个表情。
    const emptyExpressionOption = document.createElement('option');
    emptyExpressionOption.value = '';
    emptyExpressionOption.textContent = (window.t && window.t('character.noExpression', '无表情')) || '无表情';
    expressionSelect.appendChild(emptyExpressionOption);

    if (expressionFiles.length > 0) {
        expressionSelect.disabled = false;
        playExpressionBtn.disabled = false;
        expressionSelect.value = '';

        // 添加表情选项
        expressionFiles.forEach(expressionFile => {
            const expressionName = expressionFile.split('/').pop().replace('.exp3.json', '');
            const option = document.createElement('option');
            option.value = expressionName;
            option.textContent = expressionName;
            expressionSelect.appendChild(option);
        });
    } else {
        expressionSelect.disabled = true;
        playExpressionBtn.disabled = true;
        emptyExpressionOption.textContent = (window.t && window.t('live2d.noExpressionFiles', '没有表情文件')) || '没有表情文件';
    }

    // 显示预览控件
    previewControls.style.display = '';

    ensurePreviewPlaybackBindings();
}

function handlePreviewMotionPlay() {
    if (!currentPreviewModel) return;

    const motionSelect = document.getElementById('preview-motion-select');
    const motionFile = motionSelect ? motionSelect.value : '';
    if (!motionFile) return;

    const motionIndex = (window._previewMotionFiles || []).indexOf(motionFile);
    if (motionIndex < 0) return;

    try {
        currentPreviewModel.motion('PreviewAll', motionIndex, 3);
    } catch (error) {
        console.error('Failed to play motion:', error);
        showMessage(window.t('live2d.playMotionFailed', { motion: motionFile }), 'error');
    }
}

function handlePreviewExpressionPlay() {
    if (!currentPreviewModel) return;

    const expressionSelect = document.getElementById('preview-expression-select');
    const expressionName = expressionSelect ? expressionSelect.value : '';
    if (!expressionName) return;

    try {
        currentPreviewModel.expression(expressionName);
    } catch (error) {
        console.error('Failed to play expression:', error);
        showMessage(window.t('live2d.playExpressionFailed', { expression: expressionName }), 'error');
    }
}

function ensurePreviewPlaybackBindings() {
    const playMotionBtn = document.getElementById('preview-play-motion-btn');
    if (playMotionBtn && playMotionBtn.dataset.previewMotionBound !== 'true') {
        playMotionBtn.addEventListener('click', handlePreviewMotionPlay);
        playMotionBtn.dataset.previewMotionBound = 'true';
    }

    const playExpressionBtn = document.getElementById('preview-play-expression-btn');
    if (playExpressionBtn && playExpressionBtn.dataset.previewExpressionBound !== 'true') {
        playExpressionBtn.addEventListener('click', handlePreviewExpressionPlay);
        playExpressionBtn.dataset.previewExpressionBound = 'true';
    }
}

// 注意事项标签功能
(function () {
    const tagsContainer = document.getElementById('notes-tags-container');
    const notesInput = document.getElementById('workshop-notes-input');
    let notesTags = [];

    // 渲染标签
    function renderTags() {
        tagsContainer.innerHTML = '';
        const removeTagTitle = window.t ? window.t('steam.removeTag') : '删除标签';
        notesTags.forEach((tag, index) => {
            const tagElement = document.createElement('span');
            tagElement.className = 'tag';

            const tagText = document.createElement('span');
            tagText.textContent = tag;

            const removeButton = document.createElement('button');
            removeButton.type = 'button';
            removeButton.className = 'tag-remove';
            removeButton.title = removeTagTitle;
            removeButton.setAttribute('aria-label', removeTagTitle);
            removeButton.setAttribute('data-i18n-title', 'steam.removeTag');
            removeButton.setAttribute('data-i18n-aria', 'steam.removeTag');
            removeButton.addEventListener('click', () => removeNotesTag(index));

            const removeIcon = document.createElement('span');
            removeIcon.textContent = '×';
            removeButton.appendChild(removeIcon);

            tagElement.appendChild(tagText);
            tagElement.appendChild(removeButton);
            tagsContainer.appendChild(tagElement);
        });
        if (window.updatePageTexts) {
            window.updatePageTexts();
        }
        updateNotesPreview(); // 更新预览，移到循环外部确保无论是否有标签都会执行
    }

    // 添加标签
    function addNotesTag(tagValue) {
        if (tagValue && tagValue.trim()) {
            const tag = tagValue.trim();

            // 检查标签数量是否超过限制（最多4个）
            if (notesTags.length >= 4) {
                alert(window.t ? window.t('steam.tagLimitReached') : '标签数量不能超过4个！');
                return;
            }

            // 检查标签字数是否超过限制（最多30字）
            if (tag.length > 30) {
                alert(window.t ? window.t('steam.tagTooLong') : '标签字数不能超过30字！');
                return;
            }

            // 去重
            if (!notesTags.includes(tag)) {
                notesTags.push(tag);
                renderTags();
            }
        }
    }

    // 删除标签
    function removeNotesTag(index) {
        notesTags.splice(index, 1);
        renderTags();
    }

    window.removeNotesTag = removeNotesTag;

    // 处理输入框变化
    function handleInput() {
        const inputValue = notesInput.value;

        // 当输入空格时添加标签
        if (inputValue.endsWith(' ')) {
            const tagValue = inputValue.trim();
            addNotesTag(tagValue);
            notesInput.value = '';
        }
    }

    // 监听输入变化，按空格添加标签
    if (notesInput) {
        notesInput.addEventListener('input', handleInput);
    }

    // 导出addNotesTag函数供外部使用
    window.addNotesTag = addNotesTag;
})();

// 预览图片选择功能
function selectPreviewImage() {
    // 创建文件选择事件监听
    const fileInput = document.getElementById('preview-image-file');

    // 清除之前的事件监听
    fileInput.onchange = null;

    // 添加新的事件监听
    fileInput.onchange = function (e) {
        if (e.target.files && e.target.files[0]) {
            const file = e.target.files[0];
            const hintElement = document.getElementById('preview-image-size-hint');

            // 校验文件大小（1MB = 1024 * 1024 字节）
            const maxSize = 1024 * 1024; // 1MB
            if (file.size > maxSize) {
                // 文件超过1MB，将提示文字变为红色
                if (hintElement) {
                    hintElement.style.color = 'red';
                }
                showMessage(window.t ? window.t('steam.previewImageSizeExceeded') : '预览图片大小超过1MB，请选择较小的图片', 'error');
                // 清空文件选择
                e.target.value = '';
                return;
            } else {
                // 文件大小符合要求，将提示文字恢复为默认色
                if (hintElement) {
                    hintElement.style.color = '#333';
                }
            }

            // 创建FormData对象，用于上传文件
            const formData = new FormData();
            // 获取原始文件扩展名
            const fileExtension = file.name.split('.').pop().toLowerCase();
            // 创建新的File对象，使用统一的文件名"preview.扩展名"
            const renamedFile = new File([file], `preview.${fileExtension}`, {
                type: file.type,
                lastModified: file.lastModified
            });
            formData.append('file', renamedFile);

            // 获取内容文件夹路径（如果已选择）
            const contentFolder = document.getElementById('content-folder').value.trim();
            if (contentFolder) {
                formData.append('content_folder', contentFolder);
            }

            // 显示上传进度
            showMessage(window.t ? window.t('steam.uploadingPreviewImage') : '正在上传预览图片...', 'info');

            // 上传文件到服务器
            fetch('/api/steam/workshop/upload-preview-image', {
                method: 'POST',
                body: formData
            })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        // 设置服务器返回的临时文件路径
                        document.getElementById('preview-image').value = data.file_path;
                        showMessage(window.t ? window.t('steam.previewImageUploaded') : '预览图片上传成功', 'success');
                    } else {
                        console.error("上传预览图片失败:", data.message);
                        showMessage(window.t ? window.t('steam.previewImageUploadFailed', { error: data.message }) : `预览图片上传失败: ${data.message}`, 'error');
                    }
                })
                .catch(error => {
                    console.error("上传预览图片出错:", error);
                    showMessage(window.t ? window.t('steam.previewImageUploadError', { error: error.message }) : `预览图片上传出错: ${error.message}`, 'error');
                });
        }
    };

    // 触发文件选择对话框
    fileInput.click();
}


// ===================== 我的档案管理 =====================
