/**
 * VRM Init - 全局导出和自动初始化
 */

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
                // 合并默认值，提升容错：如果后端忘了带 user_vrm/static_vrm 任一字段，使用默认值
                const defaultPaths = {
                    user_vrm: '/user_vrm',
                    static_vrm: '/static/vrm'
                };
                
                // 确保 VRM_PATHS 已初始化（如果不存在则使用默认值）
                if (!window.VRM_PATHS) {
                    window.VRM_PATHS = { ...defaultPaths };
                }
                
                // 合并后端返回的路径配置，保留默认值作为后备
                window.VRM_PATHS = {
                    ...defaultPaths,
                    ...window.VRM_PATHS,  // 保留已有的配置（如果有）
                    ...data.paths,         // 后端返回的配置（覆盖默认值）
                    isLoaded: true         // 标记已加载
                };
                
                return true;
            }
        }
        return false;
    } catch (error) {
        console.warn('[VRM Init] 无法获取路径配置，使用默认值:', error);
        return false;
    }
}

window._vrmConvertPath = function(modelPath, options = {}) {
    const defaultPath = options.defaultPath || '/static/vrm/sister1.0.vrm';
    
    // 1. 验证输入路径的有效性
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
    
    // 3. 处理 Windows 绝对路径（驱动器字母模式，如 C:\ 或 C:/）
    const windowsPathPattern = /^[A-Za-z]:[\\/]/;
    if (windowsPathPattern.test(modelUrl) || (modelUrl.includes('\\') && modelUrl.includes(':'))) {
        // 提取文件名并使用 user_vrm 路径（Windows 路径通常来自本地文件系统，应映射到用户目录）
        const filename = modelUrl.split(/[\\/]/).pop();
        if (filename) {
            modelUrl = `${userVrmPath}/${filename}`;
        } else {
            return defaultPath;
        }
    } else if (modelUrl.includes('\\')) {
        // 4. 如果包含反斜杠但不是 Windows 驱动器路径，统一转换为正斜杠
        modelUrl = modelUrl.replace(/\\/g, '/');
        // 如果不是以 / 开头，当作相对路径处理
        if (!modelUrl.startsWith('/')) {
            modelUrl = `${userVrmPath}/${modelUrl}`;
        }
    } else if (!modelUrl.startsWith('http') && !modelUrl.startsWith('/')) {
        // 5. 如果是相对路径（不以 http 或 / 开头），添加 user_vrm 路径前缀
        // 验证路径有效性
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
        // 6. 如果已经是完整路径（以 / 开头），确保格式正确
        modelUrl = modelUrl.replace(/\\/g, '/');
        // 只重映射单段路径（如 "/file.vrm"），保留多段路径（如 "/custom/models/my.vrm"）
        if (!modelUrl.startsWith(userVrmPath + '/') && !modelUrl.startsWith(staticVrmPath + '/')) {
            const pathSegments = modelUrl.split('/').filter(Boolean);
            // 如果是单段路径（只有文件名），重映射到 userVrmPath
            if (pathSegments.length === 1) {
                const filename = pathSegments[0];
                if (filename) {
                    modelUrl = `${userVrmPath}/${filename}`;
                }
            }
            // 否则保留原始绝对路径（多段路径不重映射）
        }
    }
    
    // 7. 最终验证：确保 modelUrl 不包含 "undefined" 或 "null"
    if (typeof modelUrl !== 'string' || 
        modelUrl.includes('undefined') || 
        modelUrl.includes('null') ||
        modelUrl.trim() === '') {
        console.error('[VRM Path] 路径转换后仍包含无效值，使用默认路径:', modelUrl);
        return defaultPath;
    }
    
    return modelUrl;
};

// 同时挂载到 window.convertVRMModelPath（保持向后兼容）
// 但优先使用 _vrmConvertPath 来避免与本地函数的递归问题
// 只有在 window.convertVRMModelPath 不存在或已经被覆盖时才设置
if (!window.convertVRMModelPath) {
    window.convertVRMModelPath = window._vrmConvertPath;
} else if (window.convertVRMModelPath === window._vrmConvertPath) {
    // 如果已经是 _vrmConvertPath，保持不变
    // 这样可以避免覆盖本地函数
}

function initializeVRMManager() {
    if (window.vrmManager) return;

    try {
        // 检查核心类是否存在
        if (typeof window.VRMManager !== 'undefined') {
            // 使用显式的全局引用实例化，避免在非全局作用域中的 ReferenceError
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
    // 防止重复进入：如果正在初始化或模型已加载，直接退出
    if (window._isVRMInitializing) {
        return;
    }
    // 标记开始
    window._isVRMInitializing = true;
    
    try {
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
                    const data = await res.json();
                    // 提取当前角色的数据
                    const charData = data['猫娘']?.[currentName];
                    if (charData) {
                        // 把 lighting 补全到全局配置里
                        window.lanlan_config.lighting = charData.lighting;
                        // 顺便把 VRM 路径也更新一下，防止主页存的是旧路径
                        if (charData.vrm) window.lanlan_config.vrm = charData.vrm;
                    }
                }
            }
        } catch (e) {
            console.warn('[VRM Init] 同步角色数据失败，将使用默认设置:', e);
        }
        // 2. 获取并确定模型路径
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
            // (检查全局配置是否指定了 model_type: 'vrm')
            const isVRMMode = window.lanlan_config && window.lanlan_config.model_type === 'vrm';

            // 只有在 "存在 Live2D 对象" 且 "当前配置不是 VRM 模式" 时，才真的退出
            // 这样即使 window.cubism4Model 没销毁，只要配置切到了 vrm，就会继续往下走
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

        // 初始化 Three.js 场景
        await window.vrmManager.initThreeJS('vrm-canvas', 'vrm-container');

        // 使用统一的路径转换工具函数
        const modelUrl = window.convertVRMModelPath(targetModelPath);

        // 执行加载
        // 【简化】朝向会自动检测并保存（在vrm-core.js的loadModel中处理）
        // 如果模型背对屏幕，会自动翻转180度并保存，下次加载时直接应用
        await window.vrmManager.loadModel(modelUrl);
        
        // 页面加载时立即应用打光配置
        if (window.lanlan_config && window.lanlan_config.lighting && window.vrmManager) {
            const lighting = window.lanlan_config.lighting;
            if (window.vrmManager.ambientLight && lighting.ambient !== undefined) {
                window.vrmManager.ambientLight.intensity = lighting.ambient;
            }
            if (window.vrmManager.mainLight && lighting.main !== undefined) {
                window.vrmManager.mainLight.intensity = lighting.main;
            }
            if (window.vrmManager.fillLight && lighting.fill !== undefined) {
                window.vrmManager.fillLight.intensity = lighting.fill;
            }
            if (window.vrmManager.rimLight && lighting.rim !== undefined) {
                window.vrmManager.rimLight.intensity = lighting.rim;
            }
            if (window.vrmManager.topLight && lighting.top !== undefined) {
                window.vrmManager.topLight.intensity = lighting.top;
            }
            if (window.vrmManager.bottomLight && lighting.bottom !== undefined) {
                window.vrmManager.bottomLight.intensity = lighting.bottom;
            }
        }

    } catch (error) {
        console.error('[VRM Init] 错误详情:', error.stack);
    } finally {
        // 无论成功还是失败，包括所有早期返回，最后都释放锁
        window._isVRMInitializing = false;
    }
}

// 添加强制解锁函数
window.forceUnlockVRM = function() {
    if (window.vrmManager && window.vrmManager.interaction) {
        window.vrmManager.interaction.setLocked(false);

        // 清理可能残留的 CSS 样式
        if (window.vrmManager.canvas) {
            window.vrmManager.canvas.style.pointerEvents = 'auto';
        }
    }
};

// 手动触发主页VRM模型检查的函数
window.checkAndLoadVRM = async function() {
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
            return;
        }

        const modelType = catgirlConfig.model_type || 'live2d';
        if (modelType !== 'vrm') {
            return;
        }

        // 3. 获取VRM路径
        const newModelPath = catgirlConfig.vrm || '';
        if (!newModelPath) {
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

        // 7. 初始化Three.js场景
        if (!window.vrmManager._isInitialized || !window.vrmManager.scene || !window.vrmManager.camera || !window.vrmManager.renderer) {
            await window.vrmManager.initThreeJS('vrm-canvas', 'vrm-container');
        }

        // 8. 检查是否需要重新加载模型（使用规范化比较，避免路径前缀差异导致不必要的重载）
        const currentModelUrl = window.vrmManager.currentModel?.url;
        let needReload = true;
        
        if (currentModelUrl) {
            // 辅助函数：提取文件名（路径的最后一部分，不区分大小写）
            const getFilename = (path) => {
                if (!path || typeof path !== 'string') return '';
                const parts = path.split('/').filter(Boolean);
                return parts.length > 0 ? parts[parts.length - 1].toLowerCase() : '';
            };
            
            // 辅助函数：规范化路径（移除协议/主机，移除 /user_vrm/ 或 /static/vrm/ 前缀）
            const normalizePath = (path) => {
                if (!path || typeof path !== 'string') return '';
                // 移除协议和主机
                let normalized = path.replace(/^https?:\/\/[^\/]+/, '');
                // 移除 /user_vrm/ 或 /static/vrm/ 前缀
                normalized = normalized.replace(/^\/(user_vrm|static\/vrm)\//, '/');
                return normalized.toLowerCase();
            };
            
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

        if (needReload) {
            await window.vrmManager.loadModel(modelUrl);
        }
        
        // 直接使用刚刚拉取的 catgirlConfig 中的 lighting
        const lighting = catgirlConfig.lighting;
        
        if (lighting && window.vrmManager) {
            if (window.vrmManager.ambientLight && lighting.ambient !== undefined) {
                window.vrmManager.ambientLight.intensity = lighting.ambient;
            }
            if (window.vrmManager.mainLight && lighting.main !== undefined) {
                window.vrmManager.mainLight.intensity = lighting.main;
            }
            if (window.vrmManager.fillLight && lighting.fill !== undefined) {
                window.vrmManager.fillLight.intensity = lighting.fill;
            }
            if (window.vrmManager.rimLight && lighting.rim !== undefined) {
                window.vrmManager.rimLight.intensity = lighting.rim;
            }
            if (window.vrmManager.topLight && lighting.top !== undefined) {
                window.vrmManager.topLight.intensity = lighting.top;
            }
            if (window.vrmManager.bottomLight && lighting.bottom !== undefined) {
                window.vrmManager.bottomLight.intensity = lighting.bottom;
            }
            
            // 顺便更新一下全局变量，以防万一
            if (window.lanlan_config) window.lanlan_config.lighting = lighting;
        }

    } catch (error) {
        console.error('[VRM Check] 检查失败:', error);
    }
};

// 监听器必须放在函数外面！
document.addEventListener('visibilitychange', () => {
    // 当页面从后台（或子页面）切回来变可见时
    if (document.visibilityState === 'visible') {
        // 如果是在主页，且 VRM 检查函数存在
        if (!isModelManagerPage() && window.checkAndLoadVRM) {
            window.checkAndLoadVRM();
        }
    }
});
// VRM 系统初始化完成