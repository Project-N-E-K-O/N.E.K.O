/**
 * MMD Init - 模块加载器和自动初始化
 * 参考 vrm-init.js 结构
 */

// --- MMD 模块加载逻辑 ---
(async function initMMDModules() {
    if (window.mmdModuleLoaded || window._mmdModulesLoading) return;

    const MMD_VERSION = '1.0.0';

    const loadModules = async () => {
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
            window.dispatchEvent(new CustomEvent('mmd-modules-ready'));
            console.log('[MMD] 所有模块加载完成');
        } else {
            window.mmdModuleLoaded = false;
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
window.addEventListener('mmd-modules-ready', async () => {
    // 模型管理页面不自动加载
    if (window.location.pathname.includes('model_manager') || document.querySelector('#vrm-model-select') !== null) return;

    // 等待页面配置加载完成
    if (window.pageConfigReady && typeof window.pageConfigReady.then === 'function') {
        await window.pageConfigReady;
    }

    const modelType = (window.lanlan_config?.model_type || '').toLowerCase();
    const subType = (window.lanlan_config?.live3d_sub_type || '').toLowerCase();
    if (modelType !== 'live3d' || subType !== 'mmd') return;

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
    if (mmdCanvas) { mmdCanvas.style.visibility = 'visible'; mmdCanvas.style.pointerEvents = 'auto'; }

    try {
        await initMMDModel();
        if (window.mmdManager) {
            // 先获取保存的设置，预置影响加载路径的字段（如物理开关）
            const catgirlName = window.lanlan_config?.lanlan_name;
            let savedSettings = null;
            if (catgirlName) {
                try {
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
            await window.mmdManager.loadModel(resolvedPath);

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

            console.log('[MMD Init] MMD 模型自动加载完成');
        }
    } catch (e) {
        console.error('[MMD Init] MMD 自动加载失败:', e);
    }
});

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
        await new Promise((resolve) => {
            window.addEventListener('mmd-modules-ready', resolve, { once: true });
            // 超时保护
            setTimeout(resolve, 10000);
        });
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
