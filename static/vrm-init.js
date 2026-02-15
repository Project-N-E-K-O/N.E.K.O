/**
 * VRM Init - 全局导出和自动初始化
 */

// --- VRM 模块加载逻辑 ---
(async function initVRMModules() {
    // 如果已经加载过模块，或者正由 model_manager.js 加载中，则不再重复加载
    if (window.vrmModuleLoaded || window._vrmModulesLoading) return;

    const VRM_VERSION = '1.0.0';

    const loadModules = async () => {
        console.log('[VRM] 开始加载依赖模块');

        // 可以并行加载的核心模块（无相互依赖）
        const parallelModules = [
            '/static/vrm-orientation.js',
            '/static/vrm-core.js',
            '/static/vrm-expression.js',
            '/static/vrm-animation.js',
            '/static/vrm-interaction.js',
            '/static/vrm-cursor-follow.js',
            '/static/vrm-manager.js'
        ];

        // 必须顺序加载的 UI 模块（vrm-ui-buttons.js 依赖 vrm-ui-popup.js 中定义的 createPopup）
        const sequentialModules = [
            '/static/vrm-ui-popup.js',
            '/static/vrm-ui-buttons.js'
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
            // 检查脚本是否已存在
            if (document.querySelector(`script[src^="${moduleSrc}"]`)) {
                return Promise.resolve();
            }

            return new Promise((resolve) => {
                const script = document.createElement('script');
                script.src = `${moduleSrc}?v=${VRM_VERSION}`;
                script.onload = () => {
                    console.log(`[VRM] 模块加载成功: ${moduleSrc}`);
                    resolve();
                };
                script.onerror = () => {
                    console.error(`[VRM] 模块加载失败: ${moduleSrc}`);
                    failedModules.push(moduleSrc);
                    resolve(); // 即使失败也继续
                };
                appendScriptSafely(script);
            });
        };

        // 1. 并行加载核心模块
        await Promise.all(parallelModules.map(loadScript));

        // 2. 顺序加载 UI 模块（确保 popup 在 buttons 之前完成）
        for (const moduleSrc of sequentialModules) {
            await loadScript(moduleSrc);
        }

        if (failedModules.length === 0) {
            window.vrmModuleLoaded = true;
            window.dispatchEvent(new CustomEvent('vrm-modules-ready'));
        } else {
            window.vrmModuleLoaded = false;
            window.dispatchEvent(new CustomEvent('vrm-modules-failed', {
                detail: { failedModules }
            }));
        }
    };

    // 如果 THREE 还没好，就等事件；好了就直接加载
    if (typeof window.THREE === 'undefined') {
        window.addEventListener('three-ready', loadModules, { once: true });
    } else {
        loadModules();
    }
})();

// 全局路径配置对象 (带默认值作为保底)
window.VRM_PATHS = {
    user_vrm: '/user_vrm',
    static_vrm: '/static/vrm'
};

// 全局：判断是否为移动端宽度（如果不存在则定义，避免重复定义）
window.isMobileWidth = window.isMobileWidth || (() => window.innerWidth <= 768);

const isModelManagerPage = () => window.location.pathname.includes('model_manager') || document.querySelector('#vrm-model-select') !== null;
window.vrmManager = null;

/**
 * 从后端同步路径配置
 */
async function fetchVRMConfig() {
    try {
        const response = await fetch('/api/model/vrm/config');
        if (response.ok) {
            const data = await response.json();
            if (data.success && data.paths) {
                const defaultPaths = {
                    user_vrm: '/user_vrm',
                    static_vrm: '/static/vrm'
                };

                // 合并后端返回的路径配置，保留默认值作为后备
                window.VRM_PATHS = {
                    ...defaultPaths,
                    ...data.paths,         // 后端返回的配置（覆盖默认值）
                    isLoaded: true         // 标记已加载
                };

                // 派发配置加载完成事件
                window.dispatchEvent(new CustomEvent('vrm-paths-loaded', {
                    detail: { paths: window.VRM_PATHS }
                }));

                return true;
            }
        } else {
            console.warn('[VRM Init] 获取路径配置失败，HTTP 状态:', response.status, response.statusText);
        }
        return false;
    } catch (error) {
        console.warn('[VRM Init] 无法获取路径配置，使用默认值:', error);
        return false;
    }
}

window._vrmConvertPath = function (modelPath, options = {}) {
    const defaultPath = options.defaultPath || '/static/vrm/sister1.0.vrm';

    // 验证输入路径的有效性
    if (!modelPath ||
        modelPath === 'undefined' ||
        modelPath === 'null' ||
        (typeof modelPath === 'string' && (modelPath.trim() === '' || modelPath.includes('undefined')))) {
        console.warn('[VRM Path] 路径无效，使用默认路径:', modelPath);
        return defaultPath;
    }

    // 确保 modelPath 是字符串
    if (typeof modelPath !== 'string') {
        console.warn('[VRM Path] 路径不是字符串，使用默认路径:', modelPath);
        return defaultPath;
    }

    // 如果路径已经是有效的站内相对路径，直接返回，避免不必要的回退到默认路径；使用 window.VRM_PATHS 动态获取前缀，而不是硬编码
    const getConfiguredPrefixes = () => {
        if (!window.VRM_PATHS) {
            // 如果配置未加载，使用默认前缀
            return ['/static/vrm/', '/user_vrm/'];
        }

        // 处理数组或对象形状的配置
        let prefixes = [];
        if (Array.isArray(window.VRM_PATHS)) {
            prefixes = window.VRM_PATHS.map(p => (typeof p === 'string' ? p : p.path || '')).filter(Boolean);
        } else if (typeof window.VRM_PATHS === 'object') {
            // 从对象中提取路径前缀
            const userVrm = window.VRM_PATHS.user_vrm || '/user_vrm';
            const staticVrm = window.VRM_PATHS.static_vrm || '/static/vrm';
            prefixes = [
                staticVrm.endsWith('/') ? staticVrm : staticVrm + '/',
                userVrm.endsWith('/') ? userVrm : userVrm + '/'
            ];
        }

        // 如果没有有效的前缀，使用默认值
        if (prefixes.length === 0) {
            return ['/static/vrm/', '/user_vrm/'];
        }

        return prefixes;
    };

    const configuredPrefixes = getConfiguredPrefixes();
    if (configuredPrefixes.some(prefix => modelPath.startsWith(prefix))) {
        return modelPath;
    }

    let modelUrl = modelPath;

    // 确保 VRM_PATHS 已初始化
    if (!window.VRM_PATHS) {
        window.VRM_PATHS = {
            user_vrm: '/user_vrm',
            static_vrm: '/static/vrm'
        };
    }

    const userVrmPath = window.VRM_PATHS.user_vrm || '/user_vrm';
    const staticVrmPath = window.VRM_PATHS.static_vrm || '/static/vrm';

    if (/^https?:\/\//.test(modelUrl)) {
        return modelUrl;
    }

    // 处理 Windows 绝对路径（驱动器字母模式，如 C:\ 或 C:/）
    const windowsPathPattern = /^[A-Za-z]:[\\/]/;
    if (windowsPathPattern.test(modelUrl) || (modelUrl.includes('\\') && modelUrl.includes(':'))) {
        // 提取文件名并使用 user_vrm 路径
        const filename = modelUrl.split(/[\\/]/).pop();
        if (filename) {
            modelUrl = `${userVrmPath}/${filename}`;
        } else {
            return defaultPath;
        }
    } else if (modelUrl.includes('\\')) {
        // 如果包含反斜杠但不是 Windows 驱动器路径，统一转换为正斜杠
        modelUrl = modelUrl.replace(/\\/g, '/');
        if (!modelUrl.startsWith('/')) {
            modelUrl = `${userVrmPath}/${modelUrl}`;
        }
    } else if (!modelUrl.startsWith('http') && !modelUrl.startsWith('/')) {
        // 如果是相对路径（不以 http 或 / 开头），添加 user_vrm 路径前缀
        if (userVrmPath !== 'undefined' &&
            userVrmPath !== 'null' &&
            modelUrl !== 'undefined' &&
            modelUrl !== 'null') {
            modelUrl = `${userVrmPath}/${modelUrl}`;
        } else {
            console.error('[VRM Path] 路径拼接参数无效，使用默认路径:', { userVrmPath, modelUrl });
            return defaultPath;
        }
    } else {
        // 如果已经是完整路径（以 / 开头），确保格式正确；只重映射单段路径，保留多段路径
        modelUrl = modelUrl.replace(/\\/g, '/');
        if (!modelUrl.startsWith(userVrmPath + '/') && !modelUrl.startsWith(staticVrmPath + '/')) {
            const pathSegments = modelUrl.split('/').filter(Boolean);
            if (pathSegments.length === 1) {
                const filename = pathSegments[0];
                if (filename) {
                    modelUrl = `${userVrmPath}/${filename}`;
                }
            }
        }
    }

    // 最终验证：确保 modelUrl 不包含 "undefined" 或 "null"
    if (typeof modelUrl !== 'string' ||
        modelUrl.includes('undefined') ||
        modelUrl.includes('null') ||
        modelUrl.trim() === '') {
        console.error('[VRM Path] 路径转换后仍包含无效值，使用默认路径:', modelUrl);
        return defaultPath;
    }

    return modelUrl;
};



// 直接赋值确保 _vrmConvertPath 的权威性，不会被已存在的 convertVRMModelPath 覆盖
window.convertVRMModelPath = window._vrmConvertPath;

// 共享的路径处理工具函数（供 vrm-core.js 和 vrm-init.js 使用）
window._vrmPathUtils = window._vrmPathUtils || {
    getFilename: (path) => {
        if (!path || typeof path !== 'string') return '';
        const parts = path.split('/').filter(Boolean);
        return parts.length > 0 ? parts[parts.length - 1].toLowerCase() : '';
    },
    normalizePath: (path) => {
        if (!path || typeof path !== 'string') return '';
        let normalized = path.replace(/^https?:\/\/[^\/]+/, '');
        // 使用 window.VRM_PATHS 动态获取路径前缀，而不是硬编码
        const userPrefix = (window.VRM_PATHS?.user_vrm || '/user_vrm').replace(/\/+$/, '');
        const staticPrefix = (window.VRM_PATHS?.static_vrm || '/static/vrm').replace(/\/+$/, '');
        // 转义正则表达式特殊字符并构建匹配模式
        const escapeRegex = (str) => str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        normalized = normalized
            .replace(new RegExp(`^${escapeRegex(userPrefix)}/`), '/')
            .replace(new RegExp(`^${escapeRegex(staticPrefix)}/`), '/');
        return normalized.toLowerCase();
    }
};

/**
 * 应用 VRM 打光配置到 vrmManager
 * @param {Object} lighting - 打光配置对象，包含 ambient, main, fill, rim, top, bottom 等属性
 * @param {Object} vrmManager - VRM 管理器实例
 * @returns {void}
 */
function applyVRMLighting(lighting, vrmManager) {
    // 如果缺少参数，提前返回
    if (!lighting || !vrmManager) {
        return;
    }

    // 映射：vrmManager 的光源属性名 → lighting 配置的键名
    const lightMapping = {
        ambientLight: 'ambient',
        mainLight: 'main',
        fillLight: 'fill',
        rimLight: 'rim',
        topLight: 'top',
        bottomLight: 'bottom'
    };

    // 遍历映射，只有当 vrmManager 属性存在且 lighting[key] !== undefined 时才设置 intensity
    for (const [vrmManagerProp, lightingKey] of Object.entries(lightMapping)) {
        if (vrmManager[vrmManagerProp] && lighting[lightingKey] !== undefined) {
            vrmManager[vrmManagerProp].intensity = lighting[lightingKey];
        }
    }

    // 应用曝光设置
    if (lighting.exposure !== undefined && vrmManager.renderer) {
        vrmManager.renderer.toneMappingExposure = lighting.exposure;
    }

    // 应用色调映射
    if (lighting.toneMapping !== undefined && vrmManager.renderer) {
        vrmManager.renderer.toneMapping = lighting.toneMapping;
    }
}

function initializeVRMManager() {
    if (window.vrmManager) return;

    try {
        if (typeof window.VRMManager !== 'undefined') {
            window.vrmManager = new window.VRMManager();
        }
    } catch (error) {
        console.debug('[VRM Init] VRMManager 初始化失败，可能模块尚未加载:', error);
    }
}

/**
 * 清理 Live2D 的 UI 元素（浮动按钮、锁图标、返回按钮）
 * 提取为公共函数，避免代码重复
 */
function cleanupLive2DUIElements() {
    const elementsToRemove = [
        'live2d-floating-buttons',
        'live2d-lock-icon',
        'live2d-return-button-container'
    ];
    elementsToRemove.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.remove();
    });
}

// 替换掉原有的轮询，改用标准的事件监听
window.addEventListener('vrm-modules-ready', () => {
    initializeVRMManager();

    // 如果不是管理页面，尝试自动加载模型
    if (!isModelManagerPage()) {
        initVRMModel();
    }
});

// 自动初始化函数
async function initVRMModel() {
    // 防止重复进入：使用共享锁，避免与 checkAndLoadVRM 并发
    if (window._isVRMLoading) {
        return;
    }

    try {
        // 标记开始（共享锁）- 放在 try 块内确保 finally 能正确释放
        window._isVRMLoading = true;
        // 1. 等待配置加载完成
        if (window.pageConfigReady && typeof window.pageConfigReady.then === 'function') {
            await window.pageConfigReady;
        }
        // 在此处同步后端路径配置 
        await fetchVRMConfig();

        // 主动去服务器拉取最新的角色详情（包含光照）
        try {
            const currentName = window.lanlan_config?.lanlan_name;
            if (currentName) {
                // 请求完整的角色列表
                const res = await fetch('/api/characters');
                if (res.ok) {
                    let data;
                    try {
                        data = await res.json();
                    } catch (parseError) {
                        console.error('[VRM Init] JSON 解析失败:', parseError);
                        throw new Error('角色数据格式错误');
                    }
                    // 提取当前角色的数据
                    const charData = data['猫娘']?.[currentName];
                    if (charData) {
                        // 把 lighting 补全到全局配置里
                        window.lanlan_config.lighting = charData.lighting;
                        // 顺便把 VRM 路径也更新一下，防止主页存的是旧路径
                        if (charData.vrm) window.lanlan_config.vrm = charData.vrm;
                        // 待机动作配置传播到全局，供 vrm-manager.js loadModel 使用
                        if (charData.idleAnimation) window.lanlan_config.vrmIdleAnimation = charData.idleAnimation;
                    }
                } else {
                    if (res.status === 404) {
                        console.warn('[VRM Init] 角色数据接口不存在 (404)');
                    } else if (res.status >= 500) {
                        console.error('[VRM Init] 服务器错误:', res.status, res.statusText);
                    } else {
                        console.warn('[VRM Init] 获取角色数据失败，HTTP 状态:', res.status, res.statusText);
                    }
                }
            }
        } catch (e) {
            if (e.message === '角色数据格式错误') {
                console.error('[VRM Init] 角色数据解析失败，将使用默认设置:', e);
            } else {
                console.warn('[VRM Init] 网络请求失败，将使用默认设置:', e);
            }
        }
        // 2. 获取并确定模型路径
        // 如果是模型管理页面，不自动加载模型，直接返回
        if (isModelManagerPage()) {
            console.log('[VRM Init] 模型管理页面，跳过自动模型加载');
            return;
        }

        // 安全获取 window.vrmModel，处理各种边界情况（包括字符串 "undefined" 和 "null"）
        let targetModelPath = null;
        if (window.vrmModel !== undefined && window.vrmModel !== null) {
            const rawValue = window.vrmModel;
            if (typeof rawValue === 'string') {
                const trimmed = rawValue.trim();
                // 检查是否是无效的字符串值（包括 "undefined"、"null"、空字符串、包含 "undefined" 的字符串）
                if (trimmed !== '' &&
                    trimmed !== 'undefined' &&
                    trimmed !== 'null' &&
                    !trimmed.includes('undefined') &&
                    !trimmed.includes('null')) {
                    targetModelPath = trimmed;
                }
            } else {
                // 非字符串类型，转换为字符串后也要验证
                const strValue = String(rawValue);
                if (strValue !== 'undefined' && strValue !== 'null' && !strValue.includes('undefined')) {
                    targetModelPath = strValue;
                }
            }
        }

        // 如果未指定路径或路径无效，使用默认模型保底
        // 额外检查：确保 targetModelPath 不是字符串 "undefined" 或包含 "undefined"
        if (!targetModelPath ||
            (typeof targetModelPath === 'string' && (
                targetModelPath === 'undefined' ||
                targetModelPath === 'null' ||
                targetModelPath.includes('undefined') ||
                targetModelPath.includes('null') ||
                targetModelPath.trim() === ''
            ))) {
            // 获取当前是否应该处于 VRM 模式
            const isVRMMode = window.lanlan_config && window.lanlan_config.model_type === 'vrm';

            // 只有在 "存在 Live2D 对象" 且 "当前配置不是 VRM 模式" 时，才真的退出
            if (window.cubism4Model && !isVRMMode) {
                return; // Live2D 模式且未强制切换，跳过 VRM 默认加载
            }

            // 如果上面的 if 没拦截住（说明我们要加载 VRM），就会执行这一行，赋予默认模型
            targetModelPath = '/static/vrm/sister1.0.vrm';
        }

        if (!window.vrmManager) {
            console.warn('[VRM Init] VRM管理器未初始化，跳过加载');
            return;
        }

        // UI 切换逻辑
        const vrmContainer = document.getElementById('vrm-container');
        if (vrmContainer) vrmContainer.style.display = 'block';

        // 隐藏Live2D容器
        const live2dContainer = document.getElementById('live2d-container');
        if (live2dContainer) live2dContainer.style.display = 'none';

        // 清理Live2D的浮动按钮和锁图标
        cleanupLive2DUIElements();

        // 清理Live2D管理器和PIXI应用
        if (window.live2dManager) {
            try {
                // 清理当前模型
                if (window.live2dManager.currentModel) {
                    if (typeof window.live2dManager.currentModel.destroy === 'function') {
                        window.live2dManager.currentModel.destroy();
                    }
                    window.live2dManager.currentModel = null;
                }
                // 清理PIXI应用
                if (window.live2dManager.pixi_app) {
                    // 停止渲染循环
                    window.live2dManager.pixi_app.ticker.stop();
                    // 清理舞台
                    if (window.live2dManager.pixi_app.stage) {
                        window.live2dManager.pixi_app.stage.removeChildren();
                    }
                    // 完全销毁PIXI应用释放WebGL上下文
                    try {
                        window.live2dManager.pixi_app.destroy(true, {
                            children: true,
                            texture: true,
                            baseTexture: true
                        });
                    } catch (destroyError) {
                        console.warn('[VRM Init] PIXI应用销毁时出现警告:', destroyError);
                    }
                    window.live2dManager.pixi_app = null;
                }
            } catch (cleanupError) {
                console.warn('[VRM Init] Live2D清理时出现警告:', cleanupError);
            }
        }

        // 初始化 Three.js 场景，传入光照配置（如果存在）
        const lightingConfig = window.lanlan_config?.lighting || null;
        await window.vrmManager.initThreeJS('vrm-canvas', 'vrm-container', lightingConfig);

        // 使用统一的路径转换工具函数
        const modelUrl = window.convertVRMModelPath(targetModelPath);


        // 朝向会自动检测并保存（在vrm-core.js的loadModel中处理）
        // 如果模型背对屏幕，会自动翻转180度并保存，下次加载时直接应用
        await window.vrmManager.loadModel(modelUrl);

        // 页面加载时立即应用打光配置（如果初始化时没有传入，这里会应用）
        applyVRMLighting(window.lanlan_config?.lighting, window.vrmManager);

    } catch (error) {
        console.error('[VRM Init] 错误详情:', error.stack);
    } finally {
        // 无论成功还是失败，包括所有早期返回，最后都释放锁（共享锁）
        window._isVRMLoading = false;
    }
}

// 添加强制解锁函数
window.forceUnlockVRM = function () {
    if (window.vrmManager && window.vrmManager.interaction) {
        window.vrmManager.interaction.setLocked(false);

        // 清理可能残留的 CSS 样式
        if (window.vrmManager.canvas) {
            window.vrmManager.canvas.style.pointerEvents = 'auto';
        }
    }
};

// 手动触发主页VRM模型检查的函数
window.checkAndLoadVRM = async function () {
    // 使用共享锁，避免与 initVRMModel 并发
    if (window._isVRMLoading) return;
    window._isVRMLoading = true;
    try {
        // 确保配置已同步 (防止直接调用此函数时配置还没加载) 
        if (!window.VRM_PATHS.isLoaded) {
            await fetchVRMConfig();
        }

        // 1. 获取当前角色名称
        let currentLanlanName = window.lanlan_config?.lanlan_name;
        if (!currentLanlanName) {
            console.debug('[VRM Check] 未找到当前角色名称，跳过检查');
            return;
        }

        // 2. 获取角色配置
        const charResponse = await fetch('/api/characters');
        if (!charResponse.ok) {
            console.error('[VRM] 获取角色配置失败');
            return;
        }

        const charactersData = await charResponse.json();
        const catgirlConfig = charactersData['猫娘']?.[currentLanlanName];

        if (!catgirlConfig) {
            console.debug('[VRM Check] 未找到当前角色配置，跳过检查');
            return;
        }

        const modelType = catgirlConfig.model_type || 'live2d';
        if (modelType !== 'vrm') {
            console.debug('[VRM Check] 当前角色不是 VRM 模式，跳过检查');
            return;
        }

        // 3. 获取VRM路径
        const newModelPath = catgirlConfig.vrm || '';
        if (!newModelPath) {
            console.debug('[VRM Check] VRM 路径为空，跳过检查');
            return;
        }

        // 4. 显示VRM容器，智能视觉切换
        const vrmContainer = document.getElementById('vrm-container');
        if (vrmContainer) {
            vrmContainer.style.display = 'block';
        }

        // 隐藏Live2D容器，避免UI重叠
        const live2dContainer = document.getElementById('live2d-container');
        if (live2dContainer) {
            live2dContainer.style.display = 'none';
        }

        // 删除Live2D的浮动按钮和锁图标，而不是只隐藏
        cleanupLive2DUIElements();

        // 5. 检查VRM管理器
        if (!window.vrmManager) {
            return;
        }

        // 6. 使用统一的路径转换工具函数
        const modelUrl = window.convertVRMModelPath(newModelPath);

        // 7. 初始化Three.js场景，传入光照配置（如果存在）
        if (!window.vrmManager._isInitialized || !window.vrmManager.scene || !window.vrmManager.camera || !window.vrmManager.renderer) {
            const lightingConfig = catgirlConfig.lighting || null;
            await window.vrmManager.initThreeJS('vrm-canvas', 'vrm-container', lightingConfig);
        }

        // 8. 检查是否需要重新加载模型（使用规范化比较，避免路径前缀差异导致不必要的重载）
        const currentModelUrl = window.vrmManager.currentModel?.url;
        let needReload = true;

        if (currentModelUrl) {
            // 使用共享的路径处理工具函数（避免与 vrm-core.js 重复）
            const getFilename = window._vrmPathUtils?.getFilename;
            const normalizePath = window._vrmPathUtils?.normalizePath;

            if (!getFilename || !normalizePath) {
                console.warn('[VRM Init] 路径处理工具函数未初始化，跳过路径比较');
                needReload = true;
            } else {
                const currentFilename = getFilename(currentModelUrl);
                const newFilename = getFilename(modelUrl);

                // 首先尝试文件名匹配（最宽松，处理路径前缀差异）
                if (currentFilename && newFilename && currentFilename === newFilename) {
                    needReload = false;
                } else {
                    // 如果文件名不同，尝试规范化路径匹配
                    const normalizedCurrent = normalizePath(currentModelUrl);
                    const normalizedNew = normalizePath(modelUrl);
                    if (normalizedCurrent && normalizedNew && normalizedCurrent === normalizedNew) {
                        needReload = false;
                    } else if (currentModelUrl === modelUrl) {
                        // 最后尝试完整路径精确匹配
                        needReload = false;
                    }
                }
            }
        }

        if (needReload) {
            await window.vrmManager.loadModel(modelUrl);
        }

        // 直接使用刚刚拉取的 catgirlConfig 中的 lighting
        const lighting = catgirlConfig.lighting;

        // 应用打光配置
        applyVRMLighting(lighting, window.vrmManager);

        // 顺便更新一下全局变量，以防万一
        if (lighting && window.lanlan_config) {
            window.lanlan_config.lighting = lighting;
        }

    } catch (error) {
        console.error('[VRM Check] 检查失败:', error);
    } finally {
        // 释放共享锁
        window._isVRMLoading = false;
    }
};

// 监听器必须放在函数外面！
const handleVisibilityChange = () => {
    if (document.visibilityState === 'visible') {
        if (!isModelManagerPage() && window.checkAndLoadVRM) {
            window.checkAndLoadVRM();
        }
    }
};

document.addEventListener('visibilitychange', handleVisibilityChange);

window.cleanupVRMInit = function () {
    document.removeEventListener('visibilitychange', handleVisibilityChange);
};
// VRM 系统初始化完成