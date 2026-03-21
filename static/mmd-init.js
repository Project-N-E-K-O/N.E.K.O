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
            if (document.querySelector(`script[src^="${moduleSrc}"]`)) {
                return Promise.resolve();
            }

            return new Promise((resolve) => {
                const script = document.createElement('script');
                script.src = `${moduleSrc}?v=${MMD_VERSION}`;
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

    const mmdPath = window.mmdModel;
    if (!mmdPath || mmdPath === 'undefined' || mmdPath === 'null' || mmdPath.trim() === '') {
        console.warn('[MMD Init] MMD 模型路径为空，跳过自动加载');
        return;
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
                            // 预置物理开关，避免 loadModel 时不必要的 Ammo 初始化
                            if (savedSettings.physics?.enabled != null) {
                                window.mmdManager.enablePhysics = !!savedSettings.physics.enabled;
                            }
                        }
                    }
                } catch (settingsErr) {
                    console.warn('[MMD Init] 获取MMD设置失败:', settingsErr);
                }
            }

            const resolvedPath = window._mmdConvertPath ? window._mmdConvertPath(mmdPath) : mmdPath;
            await window.mmdManager.loadModel(resolvedPath);

            // 加载完成后应用完整设置
            if (savedSettings) {
                window.mmdManager.applySettings(savedSettings);
            }

            console.log('[MMD Init] MMD 模型自动加载完成');
        }
    } catch (e) {
        console.error('[MMD Init] MMD 自动加载失败:', e);
    }
});

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
    const defaultPath = options.defaultPath || null;

    if (!modelPath || typeof modelPath !== 'string' || modelPath.trim() === '') {
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
