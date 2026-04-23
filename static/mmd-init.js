/**
 * MMD Init - 模块加载器和自动初始化
 * 参考 vrm-init.js 结构
 */

(function initMMDLoadingHelpers() {
    if (window.MMDLoadingOverlay) return;

    const STAGE_KEYS = {
        engine: 'mmd.loadingOverlay.engine',
        settings: 'mmd.loadingOverlay.settings',
        model: 'mmd.loadingOverlay.model',
        physics: 'mmd.loadingOverlay.physics',
        idle: 'mmd.loadingOverlay.idle',
        done: 'mmd.loadingOverlay.done',
        failed: 'mmd.loadingOverlay.failed'
    };

    const STAGE_FALLBACKS = {
        engine: 'Preparing MMD engine...',
        settings: 'Loading MMD settings...',
        model: 'Loading model resources...',
        physics: 'Initializing physics...',
        idle: 'Loading idle animation...',
        done: 'MMD ready',
        failed: 'Failed to load MMD'
    };

    const OVERLAY_STYLE_ID = 'neko-mmd-loading-overlay-style';
    const OVERLAY_ID = 'neko-mmd-loading-overlay';

    function translateStage(stage) {
        const key = STAGE_KEYS[stage] || STAGE_KEYS.engine;
        const fallback = STAGE_FALLBACKS[stage] || STAGE_FALLBACKS.engine;
        if (typeof window.t === 'function') {
            return window.t(key, fallback);
        }
        return fallback;
    }

    function ensureStyle() {
        if (document.getElementById(OVERLAY_STYLE_ID)) return;
        const style = document.createElement('style');
        style.id = OVERLAY_STYLE_ID;
        style.textContent = `
            #${OVERLAY_ID} {
                position: absolute;
                inset: 0;
                display: flex;
                align-items: center;
                justify-content: center;
                box-sizing: border-box;
                padding: 24px;
                background: rgba(10, 14, 18, 0.08);
                backdrop-filter: blur(1px);
                -webkit-backdrop-filter: blur(1px);
                opacity: 0;
                visibility: hidden;
                transition: opacity 180ms ease, visibility 180ms ease;
                z-index: 30;
            }

            #${OVERLAY_ID}.is-visible {
                opacity: 1;
                visibility: visible;
            }

            #${OVERLAY_ID} .neko-mmd-loading-card {
                min-width: 240px;
                max-width: min(70vw, 420px);
                padding: 20px 22px;
                border-radius: 18px;
                border: 1px solid rgba(255, 255, 255, 0.18);
                background: rgba(10, 15, 20, 0.46);
                box-shadow: 0 12px 28px rgba(0, 0, 0, 0.14);
                color: #f4f7fb;
                text-align: center;
                font-family: inherit;
            }

            #${OVERLAY_ID} .neko-mmd-loading-spinner {
                width: 38px;
                height: 38px;
                margin: 0 auto 14px;
                border-radius: 999px;
                border: 3px solid rgba(255, 255, 255, 0.16);
                border-top-color: rgba(255, 255, 255, 0.88);
                animation: neko-mmd-loading-spin 0.9s linear infinite;
            }

            #${OVERLAY_ID} .neko-mmd-loading-stage {
                font-size: 15px;
                line-height: 1.45;
                font-weight: 700;
                letter-spacing: 0.01em;
            }

            #${OVERLAY_ID} .neko-mmd-loading-detail {
                min-height: 18px;
                margin-top: 7px;
                font-size: 12px;
                line-height: 1.5;
                color: rgba(244, 247, 251, 0.74);
                word-break: break-word;
            }

            @media (max-width: 960px) {
                #${OVERLAY_ID} {
                    padding: 16px;
                }

                #${OVERLAY_ID} .neko-mmd-loading-card {
                    max-width: min(84vw, 360px);
                }
            }

            @keyframes neko-mmd-loading-spin {
                from { transform: rotate(0deg); }
                to { transform: rotate(360deg); }
            }
        `;
        (document.head || document.documentElement).appendChild(style);
    }

    function ensureContainerVisible(container) {
        if (!container) return null;
        const computed = window.getComputedStyle(container);
        if (computed.position === 'static') {
            container.style.position = 'fixed';
            container.style.top = '0';
            container.style.left = '0';
            container.style.width = '100%';
            container.style.height = '100%';
        }
        if (!container.style.zIndex) {
            container.style.zIndex = '10';
        }
        container.style.display = 'block';
        container.style.visibility = 'visible';
        container.classList.remove('hidden');
        return container;
    }

    function getSidebarSafeOffset() {
        const sidebar = document.getElementById('sidebar');
        if (!sidebar) return 0;

        const computed = window.getComputedStyle(sidebar);
        if (computed.display === 'none' || computed.visibility === 'hidden') {
            return 0;
        }

        const rect = sidebar.getBoundingClientRect();
        const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
        if (rect.width < 1 || rect.height < 1 || viewportWidth <= 960) {
            return 0;
        }

        // 模型管理页的预览区在 sidebar 右侧，overlay 卡片应按预览区居中，
        // 而不是按整个窗口居中，避免卡片贴近左侧控制面板。
        const safeOffset = Math.max(0, Math.ceil(rect.right + 28));
        const remainingWidth = viewportWidth - safeOffset;
        if (remainingWidth < 320) {
            return 0;
        }

        return Math.min(safeOffset, Math.round(viewportWidth * 0.42));
    }

    function syncOverlayLayout(overlay) {
        if (!overlay) return;

        const sidebarSafeOffset = getSidebarSafeOffset();
        overlay.style.paddingTop = '';
        overlay.style.paddingRight = '';
        overlay.style.paddingBottom = '';
        overlay.style.paddingLeft = '';

        if (sidebarSafeOffset > 0) {
            overlay.style.paddingTop = '24px';
            overlay.style.paddingRight = '24px';
            overlay.style.paddingBottom = '24px';
            overlay.style.paddingLeft = `${sidebarSafeOffset}px`;
            overlay.dataset.layout = 'sidebar-safe';
        } else {
            overlay.dataset.layout = 'centered';
        }
    }

    function ensureOverlay(container) {
        let overlay = container.querySelector(`#${OVERLAY_ID}`);
        if (overlay) {
            syncOverlayLayout(overlay);
            return overlay;
        }

        overlay = document.createElement('div');
        overlay.id = OVERLAY_ID;
        overlay.setAttribute('aria-live', 'polite');
        overlay.setAttribute('aria-hidden', 'true');
        overlay.hidden = true;
        overlay.innerHTML = `
            <div class="neko-mmd-loading-card">
                <div class="neko-mmd-loading-spinner"></div>
                <div class="neko-mmd-loading-stage"></div>
                <div class="neko-mmd-loading-detail"></div>
            </div>
        `;
        container.appendChild(overlay);
        syncOverlayLayout(overlay);
        return overlay;
    }

    function getOverlayState({ ensureVisible = false } = {}) {
        const container = document.getElementById('mmd-container');
        if (!container) return { container: null, overlay: null };
        if (ensureVisible) {
            ensureContainerVisible(container);
        }
        ensureStyle();
        return {
            container,
            overlay: ensureVisible ? ensureOverlay(container) : container.querySelector(`#${OVERLAY_ID}`)
        };
    }

    function isActiveSession(container, sessionId) {
        return !!container && container.dataset.mmdLoadingSessionId === String(sessionId);
    }

    function renderOverlay(overlay, stage, detail) {
        const safeStage = STAGE_KEYS[stage] ? stage : 'engine';
        overlay.dataset.stage = safeStage;
        overlay.querySelector('.neko-mmd-loading-stage').textContent = translateStage(safeStage);
        overlay.querySelector('.neko-mmd-loading-detail').textContent = detail ? String(detail) : '';
    }

    window._createMMDLoadingSessionId = function createMMDLoadingSessionId(prefix = 'mmd') {
        const rand = Math.random().toString(36).slice(2, 8);
        return `${prefix}-${Date.now()}-${rand}`;
    };

    window._waitForMMDModules = function waitForMMDModules(timeoutMs = 10000) {
        if (window.mmdModuleLoaded) {
            return Promise.resolve();
        }
        if (window._mmdModulesFailed) {
            return Promise.reject(new Error(`MMD modules failed: ${window._mmdModulesFailed.join(', ')}`));
        }
        return new Promise((resolve, reject) => {
            let settled = false;
            const cleanup = () => {
                window.removeEventListener('mmd-modules-ready', onReady);
                window.removeEventListener('mmd-modules-failed', onFailed);
                clearTimeout(timer);
            };
            const onReady = () => {
                if (settled) return;
                settled = true;
                cleanup();
                resolve();
            };
            const onFailed = (event) => {
                if (settled) return;
                settled = true;
                cleanup();
                const failedModules = event?.detail?.failedModules || window._mmdModulesFailed || [];
                reject(new Error(`MMD modules failed: ${failedModules.join(', ')}`));
            };
            const timer = setTimeout(() => {
                if (settled) return;
                settled = true;
                cleanup();
                reject(new Error('MMD Module Load Timeout'));
            }, timeoutMs);

            window.addEventListener('mmd-modules-ready', onReady, { once: true });
            window.addEventListener('mmd-modules-failed', onFailed, { once: true });
        });
    };

    window._waitForMMDRenderFrame = async function waitForMMDRenderFrame(manager, timeoutMs = 2000) {
        if (!manager) return;
        if (typeof manager.waitForRenderFrame === 'function') {
            await manager.waitForRenderFrame(timeoutMs);
            return;
        }
        if (manager.core && typeof manager.core.waitForRenderFrame === 'function') {
            await manager.core.waitForRenderFrame(timeoutMs);
            return;
        }
        await new Promise((resolve) => {
            let settled = false;
            let timeoutId = null;

            const finish = () => {
                if (settled) return;
                settled = true;
                if (timeoutId) {
                    clearTimeout(timeoutId);
                    timeoutId = null;
                }
                resolve();
            };

            requestAnimationFrame(() => {
                if (settled) return;
                requestAnimationFrame(finish);
            });

            timeoutId = window.setTimeout(finish, Math.max(0, Number(timeoutMs) || 0));
        });
    };

    window.MMDLoadingOverlay = {
        begin(sessionId, { stage = 'engine', detail = '' } = {}) {
            const { container, overlay } = getOverlayState({ ensureVisible: true });
            if (!container || !overlay) return false;
            container.dataset.mmdLoadingSessionId = String(sessionId);
            renderOverlay(overlay, stage, detail);
            overlay.hidden = false;
            overlay.setAttribute('aria-hidden', 'false');
            overlay.classList.add('is-visible');
            return true;
        },

        update(sessionId, { stage = 'engine', detail = '' } = {}) {
            const { container, overlay } = getOverlayState();
            if (!container || !overlay || !isActiveSession(container, sessionId)) return false;
            renderOverlay(overlay, stage, detail);
            return true;
        },

        end(sessionId) {
            const { container, overlay } = getOverlayState();
            if (!container || !overlay || !isActiveSession(container, sessionId)) return false;
            delete container.dataset.mmdLoadingSessionId;
            overlay.classList.remove('is-visible');
            overlay.setAttribute('aria-hidden', 'true');
            overlay.hidden = true;
            overlay.dataset.stage = '';
            overlay.querySelector('.neko-mmd-loading-detail').textContent = '';
            return true;
        },

        fail(sessionId, { detail = '', autoHideMs = 2200 } = {}) {
            if (!this.update(sessionId, { stage: 'failed', detail })) {
                return false;
            }
            window.setTimeout(() => {
                this.end(sessionId);
            }, autoHideMs);
            return true;
        }
    };
})();

// --- MMD 模块加载逻辑 ---
(async function initMMDModules() {
    if (window.mmdModuleLoaded || window._mmdModulesLoading) return;

    const MMD_VERSION = '1.0.0';

    const loadModules = async () => {
        window._mmdModulesLoading = true;
        window._mmdModulesFailed = null;
        console.log('[MMD] 开始加载依赖模块');

        // 核心模块（无相互依赖，可并行）
        const parallelModules = [
            '/static/mmd-core.js',
            '/static/mmd-expression.js',
            '/static/mmd-animation.js',
            '/static/mmd-interaction.js',
            '/static/mmd-cursor-follow.js',
            '/static/mmd-manager.js'
        ];

        // UI 模块（公共定位 → 公共 mixin → 统一配置 → buttons → debug）
        // avatar-popup-common, avatar-ui-popup, avatar-ui-popup-config, avatar-ui-buttons
        // 已由 HTML 静态 <script> 加载，此处不再重复加载
        const sequentialModules = [
            '/static/mmd-ui-buttons.js',
            '/static/mmd-ui-debug.js'
        ];

        const failedModules = [];
        const appendScriptSafely = (script) => {
            const attachScript = () => {
                const parent = document.head || document.body || document.documentElement;
                parent.appendChild(script);
            };
            if (!document.head && !document.body) {
                document.addEventListener('DOMContentLoaded', attachScript, { once: true });
            } else {
                attachScript();
            }
        };

        const loadScript = (moduleSrc) => {
            const baseSrc = moduleSrc.split('?')[0];
            if (document.querySelector(`script[src^="${baseSrc}"]`)) {
                return Promise.resolve();
            }

            return new Promise((resolve) => {
                const script = document.createElement('script');
                script.src = `${baseSrc}?v=${MMD_VERSION}`;
                script.onload = () => {
                    console.log(`[MMD] 模块加载成功: ${moduleSrc}`);
                    resolve();
                };
                script.onerror = () => {
                    console.error(`[MMD] 模块加载失败: ${moduleSrc}`);
                    failedModules.push(moduleSrc);
                    resolve();
                };
                appendScriptSafely(script);
            });
        };

        // 1. 并行加载核心模块
        await Promise.all(parallelModules.map(loadScript));

        // 2. 顺序加载 UI 模块
        for (const moduleSrc of sequentialModules) {
            await loadScript(moduleSrc);
        }

        if (failedModules.length === 0) {
            window.mmdModuleLoaded = true;
            window._mmdModulesLoading = false;
            window.dispatchEvent(new CustomEvent('mmd-modules-ready'));
            console.log('[MMD] 所有模块加载完成');
        } else {
            window.mmdModuleLoaded = false;
            window._mmdModulesLoading = false;
            window._mmdModulesFailed = failedModules.slice();
            window.dispatchEvent(new CustomEvent('mmd-modules-failed', {
                detail: { failedModules }
            }));
            console.error('[MMD] 部分模块加载失败:', failedModules);
        }
    };

    // Three.js 就绪后加载
    if (typeof window.THREE === 'undefined') {
        window.addEventListener('three-ready', loadModules, { once: true });
    } else {
        loadModules();
    }
})();

// 模块加载完成后，若当前是 MMD 模式则自动初始化并加载模型
(async function autoInitMMDOnMainPage() {
    // 模型管理页面和角色卡导出页不自动加载
    if (window._cardExportPage) return;
    if (window.location.pathname.includes('model_manager') || document.querySelector('#vrm-model-select') !== null) return;

    // 等待页面配置加载完成
    if (window.pageConfigReady && typeof window.pageConfigReady.then === 'function') {
        await window.pageConfigReady;
    }

    const modelType = (window.lanlan_config?.model_type || '').toLowerCase();
    const subType = (window.lanlan_config?.live3d_sub_type || '').toLowerCase();
    if (modelType !== 'live3d' || subType !== 'mmd') return;

    const loadingSessionId = window._createMMDLoadingSessionId('mmd-main');
    window.MMDLoadingOverlay.begin(loadingSessionId, { stage: 'engine' });

    let mmdPath = window.mmdModel;
    if (!mmdPath || mmdPath === 'undefined' || mmdPath === 'null' || mmdPath.trim() === '') {
        console.warn('[MMD Init] MMD 模型路径为空，使用默认模型');
        mmdPath = '/static/mmd/Miku/Miku.pmx';
    }

    console.log('[MMD Init] 检测到 MMD 模式，自动初始化并加载:', mmdPath);

    // 隐藏 VRM 容器，显示 MMD 容器
    const vrmContainer = document.getElementById('vrm-container');
    if (vrmContainer) { vrmContainer.style.display = 'none'; vrmContainer.classList.add('hidden'); }
    const live2dContainer = document.getElementById('live2d-container');
    if (live2dContainer) { live2dContainer.style.display = 'none'; live2dContainer.classList.add('hidden'); }
    const mmdContainer = document.getElementById('mmd-container');
    if (mmdContainer) { mmdContainer.classList.remove('hidden'); mmdContainer.style.display = 'block'; mmdContainer.style.visibility = 'visible'; }
    const mmdCanvas = document.getElementById('mmd-canvas');
    if (mmdCanvas) {
        // 保持 canvas 隐藏直到模型真正 ready，避免旧帧或首帧透过 loading overlay 露出。
        mmdCanvas.style.visibility = 'hidden';
        mmdCanvas.style.pointerEvents = 'none';
    }

    try {
        await window._waitForMMDModules(10000);
        const initializedManager = await initMMDModel();
        if (!initializedManager || !window.mmdManager || window.mmdManager._isDisposed) {
            const detail = (window.t && window.t('mmd.managerInitFailed')) || 'MMD 管理器初始化失败';
            window.MMDLoadingOverlay.fail(loadingSessionId, { detail });
            return;
        }
        if (window.mmdManager) {
            // 先获取保存的设置，预置影响加载路径的字段（如物理开关）
            const catgirlName = window.lanlan_config?.lanlan_name;
            let savedSettings = null;
            if (catgirlName) {
                try {
                    window.MMDLoadingOverlay.update(loadingSessionId, { stage: 'settings' });
                    const settingsRes = await fetch('/api/characters/catgirl/' + encodeURIComponent(catgirlName) + '/mmd_settings');
                    if (settingsRes.ok) {
                        const settingsData = await settingsRes.json();
                        if (settingsData.success && settingsData.settings) {
                            savedSettings = settingsData.settings;
                            // 预置物理开关和强度，避免 loadModel 时不必要的 Ammo 初始化，
                            // 且确保 warmup 使用正确的重力（防止 warmup 后变更重力导致拉丝）
                            if (savedSettings.physics?.enabled != null) {
                                window.mmdManager.enablePhysics = !!savedSettings.physics.enabled;
                            }
                            if (savedSettings.physics?.strength != null) {
                                window.mmdManager.physicsStrength = Math.max(0.1, Math.min(2.0, savedSettings.physics.strength));
                            }
                        }
                    }
                } catch (settingsErr) {
                    console.warn('[MMD Init] 获取MMD设置失败:', settingsErr);
                }
            }

            const resolvedPath = window._mmdConvertPath ? window._mmdConvertPath(mmdPath) : mmdPath;
            window.MMDLoadingOverlay.update(loadingSessionId, { stage: 'model' });
            await window.mmdManager.loadModel(resolvedPath, { loadingSessionId });

            // 加载完成后应用外观设置（光照/渲染/鼠标跟踪）
            // physics 已在 loadModel 前预置，不在此重复应用
            // （warmup 后变更重力或切换物理开关会导致拉丝/爆炸）
            if (savedSettings) {
                const { physics, ...nonPhysicsSettings } = savedSettings;
                window.mmdManager.applySettings(nonPhysicsSettings);
            }

            // 播放待机动作 & 启动轮换
            if (catgirlName) {
                try {
                    const charRes = await fetch('/api/characters/');
                    if (charRes.ok) {
                        const charData = await charRes.json();
                        const catData = charData?.['猫娘']?.[catgirlName];
                        // 优先取列表，向前兼容单字符串
                        let idleList = catData?.mmd_idle_animations;
                        if (!Array.isArray(idleList)) {
                            const single = catData?.mmd_idle_animation;
                            idleList = single ? [single] : [];
                        }
                        if (idleList.length > 0 && window.mmdManager) {
                            try {
                                window.MMDLoadingOverlay.update(loadingSessionId, { stage: 'idle' });
                                await window.mmdManager.loadAnimation(idleList[0]);
                                window.mmdManager.playAnimation();
                                console.log('[MMD Init] 已播放待机动作:', idleList[0]);
                                // 多于 1 个时启动轮换
                                _startMmdIdleRotation(idleList);
                            } catch (idleErr) {
                                console.warn('[MMD Init] 播放待机动作失败:', idleErr);
                            }
                        }
                    }
                } catch (idleErr) {
                    console.warn('[MMD Init] 获取角色待机动作失败:', idleErr);
                }
            }

            window.MMDLoadingOverlay.update(loadingSessionId, { stage: 'done' });
            await window._waitForMMDRenderFrame(window.mmdManager);
            window.MMDLoadingOverlay.end(loadingSessionId);
            if (mmdCanvas) {
                mmdCanvas.style.visibility = 'visible';
                mmdCanvas.style.pointerEvents = 'auto';
            }
            console.log('[MMD Init] MMD 模型自动加载完成');
        }
    } catch (e) {
        console.error('[MMD Init] MMD 自动加载失败:', e);
        window.MMDLoadingOverlay.fail(loadingSessionId, { detail: e?.message || String(e) });
    }
})();

// ── 主页面 MMD 待机动作轮换 ──────────────────────────────
// 策略：优先在动画一轮播完（loop 事件）时切换，避免动作中途跳变；
//       20 秒回退定时器仅在动画过长时强制切换。
let _mmdIdleTimer = null;
let _mmdIdleLastUrl = null;
let _mmdIdleLoopCleanup = null;

function _clearMmdIdleSchedule() {
    if (_mmdIdleTimer) {
        clearTimeout(_mmdIdleTimer);
        _mmdIdleTimer = null;
    }
    if (_mmdIdleLoopCleanup) {
        _mmdIdleLoopCleanup();
        _mmdIdleLoopCleanup = null;
    }
}

function _startMmdIdleRotation(urls) {
    _stopMmdIdleRotation();
    if (!Array.isArray(urls) || urls.length < 2) return;

    function pickRandom() {
        const candidates = urls.filter(u => u !== _mmdIdleLastUrl);
        return candidates[Math.floor(Math.random() * candidates.length)] || urls[0];
    }

    async function switchToNext() {
        _clearMmdIdleSchedule();

        // Jukebox 舞蹈播放中：不打断，续期定时器等舞蹈结束后再轮换
        if (window.Jukebox?.State?.isVMDPlaying) {
            scheduleFallback();
            return;
        }

        const mgr = window.mmdManager;
        if (!mgr || !mgr.currentModel) return;

        try {
            const url = pickRandom();
            if (url) {
                // 不在此处 stopAnimation — stopAnimation() 会调用 skeleton.pose() 重置到 T-pose，
                // 而 await loadAnimation 期间渲染循环会显露这个 T-pose，造成闪帧。
                // loadAnimation 内部通过 _cleanupAnimation 清理旧动画，并以同步方式应用新动画第 0 帧
                // （pose() → mixer.update(0) → updateMatrixWorld 同步完成，不跨渲染帧），
                // 所以旧动画会一直播放到新动画加载完成那一刻，无 T-pose 闪烁。
                // 与 model_manager.js 的 _playIdleAnimation 保持一致的切换策略。
                await mgr.loadAnimation(url);
                mgr.playAnimation();
                _mmdIdleLastUrl = url;
                console.debug('[MMD IdleRotation] 切换待机动作:', url.split('/').pop());

                // 注册 loop 事件监听：动画一轮播完时自动切换
                const mixer = mgr.animationModule?.mixer;
                if (mixer) {
                    const handler = () => {
                        console.debug('[MMD IdleRotation] 动画循环完成，切换下一个');
                        switchToNext();
                    };
                    mixer.addEventListener('loop', handler);
                    _mmdIdleLoopCleanup = () => mixer.removeEventListener('loop', handler);
                }
            }
        } catch (e) {
            console.warn('[MMD IdleRotation] 切换失败:', e);
        }
        scheduleFallback();
    }

    /** 设置回退定时器 */
    function scheduleFallback() {
        if (_mmdIdleTimer) clearTimeout(_mmdIdleTimer);
        _mmdIdleTimer = setTimeout(() => {
            console.debug('[MMD IdleRotation] 回退定时器触发，强制切换');
            switchToNext();
        }, 20000);
    }

    scheduleFallback();

    // 如果动画已经在播放（如 app-interpage.js 预先播放的第一个），
    // 立即注册 loop 监听器，不必等 20 秒回退定时器
    const mixer = window.mmdManager?.animationModule?.mixer;
    if (mixer) {
        const handler = () => {
            console.debug('[MMD IdleRotation] 初始动画循环完成，切换下一个');
            switchToNext();
        };
        mixer.addEventListener('loop', handler);
        _mmdIdleLoopCleanup = () => mixer.removeEventListener('loop', handler);
    }
}

function _stopMmdIdleRotation() {
    _clearMmdIdleSchedule();
    _mmdIdleLastUrl = null;
}

window._stopMmdIdleRotation = _stopMmdIdleRotation;
window._startMmdIdleRotation = _startMmdIdleRotation;

// 全局路径配置
window.MMD_PATHS = {
    user_mmd: '/user_mmd',
    static_mmd: '/static/mmd'
};

window.mmdManager = null;

/**
 * 从后端同步 MMD 路径配置
 */
async function fetchMMDConfig() {
    try {
        const response = await fetch('/api/model/mmd/config');
        if (response.ok) {
            const data = await response.json();
            if (data.success && data.paths) {
                window.MMD_PATHS = {
                    ...window.MMD_PATHS,
                    ...data.paths,
                    isLoaded: true
                };
                window.dispatchEvent(new CustomEvent('mmd-paths-loaded', {
                    detail: { paths: window.MMD_PATHS }
                }));
                return true;
            }
        }
        return false;
    } catch (error) {
        console.warn('[MMD Init] 无法获取路径配置，使用默认值:', error);
        return false;
    }
}

/**
 * 路径转换：将模型路径转换为可访问的 URL
 */
window._mmdConvertPath = function (modelPath, options = {}) {
    const defaultPath = options.defaultPath || '/static/mmd/Miku/Miku.pmx';

    if (!modelPath || typeof modelPath !== 'string' || modelPath.trim() === '' ||
        modelPath === 'undefined' || modelPath === 'null' || modelPath.includes('undefined')) {
        console.warn('[MMD Path] 路径无效，使用默认路径:', modelPath);
        return defaultPath;
    }

    // 如果已经是有效的站内路径，直接返回
    const userPrefix = (window.MMD_PATHS?.user_mmd || '/user_mmd');
    const staticPrefix = (window.MMD_PATHS?.static_mmd || '/static/mmd');
    if (modelPath.startsWith(userPrefix) || modelPath.startsWith(staticPrefix)) {
        return modelPath;
    }

    // 如果是完整 URL，直接返回
    if (modelPath.startsWith('http://') || modelPath.startsWith('https://') || modelPath.startsWith('/')) {
        return modelPath;
    }

    // 否则视为相对路径，加上用户目录前缀
    return `${userPrefix}/${modelPath}`;
};

/**
 * 全局初始化函数：初始化 MMD 模型
 */
async function initMMDModel() {
    // 如果模块还没加载完，等待
    if (!window.mmdModuleLoaded) {
        await window._waitForMMDModules(10000);
    }

    if (typeof MMDManager === 'undefined') {
        console.error('[MMD Init] MMDManager 类未定义');
        return null;
    }

    // 如果已经有实例，先销毁
    if (window.mmdManager) {
        window.mmdManager.dispose();
    }

    window.mmdManager = new MMDManager();
    await window.mmdManager.init('mmd-canvas', 'mmd-container');

    // 获取后端路径配置
    await fetchMMDConfig();

    console.log('[MMD Init] MMD 管理器已初始化');
    return window.mmdManager;
}

// 导出到全局
window.initMMDModel = initMMDModel;
window.fetchMMDConfig = fetchMMDConfig;
