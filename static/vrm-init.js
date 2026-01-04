/**
 * VRM Init - 全局导出和自动初始化
 */

// 全局路径配置对象 (带默认值作为保底)
window.VRM_PATHS = {
    user_vrm: '/user_vrm',
    static_vrm: '/static/vrm'
};

// 检查是否在模型管理页面（通过路径或特定元素判断）
const isModelManagerPage = window.location.pathname.includes('model_manager') || document.querySelector('#vrm-model-select') !== null;
// 创建全局 VRM 管理器实例（延迟创建，确保所有模块都已加载）
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
                // 更新全局配置
                window.VRM_PATHS = data.paths;
                console.log('[VRM Init] 路径配置已同步:', window.VRM_PATHS);
            }
        }
    } catch (error) {
        console.warn('[VRM Init] 无法获取路径配置，使用默认值:', error);
    }
}

function initializeVRMManager() {
    if (window.vrmManager) return;

    try {
        // 检查核心类是否存在
        if (typeof window.VRMManager !== 'undefined') {
            window.vrmManager = new VRMManager();
        }
    } catch (error) {
    }
}

// 替换掉原有的轮询，改用标准的事件监听
window.addEventListener('vrm-modules-ready', () => {
    initializeVRMManager();

    // 如果不是管理页面，尝试自动加载模型
    if (!isModelManagerPage) {
        initVRMModel();
    }
});

// 启动延迟初始化
// 自动初始化函数
async function initVRMModel() {
    // 防止重复进入：如果正在初始化或模型已加载，直接退出
    if (window._isVRMInitializing) {
        console.log('[VRM Init] 正在初始化中，跳过重复调用');
        return;
    }
    // 标记开始
    window._isVRMInitializing = true;
    
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
            console.log(`[VRM Init] 正在同步角色 ${currentName} 的详细数据...`);
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
                    
                    console.log('[VRM Init] 数据同步成功，当前光照:', charData.lighting);
                }
            }
        }
    } catch (e) {
        console.warn('[VRM Init] 同步角色数据失败，将使用默认设置:', e);
    }
    // 2. 获取并确定模型路径
    let targetModelPath = window.vrmModel || (typeof vrmModel !== 'undefined' ? vrmModel : '');

    // 如果未指定路径，使用默认模型保底
    if (!targetModelPath) {
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

    try {
        // 【关键修复】UI 切换逻辑 - 智能视觉切换
        const vrmContainer = document.getElementById('vrm-container');
        if (vrmContainer) vrmContainer.style.display = 'block';

        // 隐藏Live2D容器
        const live2dContainer = document.getElementById('live2d-container');
        if (live2dContainer) live2dContainer.style.display = 'none';

        // 清理Live2D的浮动按钮和锁图标
        const live2dFloatingButtons = document.getElementById('live2d-floating-buttons');
        if (live2dFloatingButtons) {
            live2dFloatingButtons.remove();
            console.log('[VRM Init] 已清理Live2D浮动按钮');
        }

        const live2dLockIcon = document.getElementById('live2d-lock-icon');
        if (live2dLockIcon) {
            live2dLockIcon.remove();
            console.log('[VRM Init] 已清理Live2D锁图标');
        }

        const live2dReturnBtn = document.getElementById('live2d-return-button-container');
        if (live2dReturnBtn) {
            live2dReturnBtn.remove();
            console.log('[VRM Init] 已清理Live2D回来按钮');
        }

        // 清理Live2D管理器和PIXI应用
        if (window.live2dManager) {
            try {
                // 清理当前模型
                if (window.live2dManager.currentModel) {
                    if (typeof window.live2dManager.currentModel.destroy === 'function') {
                        window.live2dManager.currentModel.destroy();
                    }
                    window.live2dManager.currentModel = null;
                    console.log('[VRM Init] 已清理Live2D模型');
                }
                // 清理PIXI应用
                if (window.live2dManager.pixi_app) {
                    // 停止渲染循环
                    window.live2dManager.pixi_app.ticker.stop();
                    // 清理舞台
                    if (window.live2dManager.pixi_app.stage) {
                        window.live2dManager.pixi_app.stage.removeChildren();
                    }
                    console.log('[VRM Init] 已清理PIXI应用');
                }
            } catch (cleanupError) {
                console.warn('[VRM Init] Live2D清理时出现警告:', cleanupError);
            }
        }

        // 初始化 Three.js 场景
        await window.vrmManager.initThreeJS('vrm-canvas', 'vrm-container');

        // 路径转换逻辑
        let modelUrl = targetModelPath;
        if (!modelUrl.startsWith('http') && !modelUrl.startsWith('/')) {
            // 使用动态获取的 user_vrm 路径
            modelUrl = `${window.VRM_PATHS.user_vrm}/${modelUrl}`;
        }
        modelUrl = modelUrl.replace(/\\/g, '/'); // 修正 Windows 风格路径

        // 执行加载
        await window.vrmManager.loadModel(modelUrl);
        // 页面加载时立即应用打光配置
        if (window.lanlan_config && window.lanlan_config.lighting && window.vrmManager) {
            const lighting = window.lanlan_config.lighting;
            console.log('[VRM Init] 初始加载应用打光:', lighting);
            if (window.vrmManager.ambientLight) window.vrmManager.ambientLight.intensity = lighting.ambient;
            if (window.vrmManager.mainLight) window.vrmManager.mainLight.intensity = lighting.main;
            if (window.vrmManager.fillLight) window.vrmManager.fillLight.intensity = lighting.fill;
            if (window.vrmManager.rimLight) window.vrmManager.rimLight.intensity = lighting.rim;
        }

    } catch (error) {
        console.error('[VRM Init] 错误详情:', error.stack);
    }finally {
        // 无论成功还是失败，最后都释放锁
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

        // 【关键修复】删除Live2D的浮动按钮和锁图标，而不是只隐藏
        const live2dFloatingButtons = document.getElementById('live2d-floating-buttons');
        if (live2dFloatingButtons) {
            live2dFloatingButtons.remove();
        }

        const live2dLockIcon = document.getElementById('live2d-lock-icon');
        if (live2dLockIcon) {
            live2dLockIcon.remove();
        }

        const live2dReturnBtn = document.getElementById('live2d-return-button-container');
        if (live2dReturnBtn) {
            live2dReturnBtn.remove();
        }

        // 5. 检查VRM管理器
        if (!window.vrmManager) {
            return;
        }

        // 6. 路径转换
        let modelUrl = newModelPath;

        // 处理Windows绝对路径，转换为Web路径
        if (modelUrl.includes('\\') || modelUrl.includes(':')) {
            const filename = modelUrl.split(/[\\/]/).pop();
            if (filename) {
                // 使用动态获取的 static_vrm 路径
                modelUrl = `${window.VRM_PATHS.static_vrm}/${filename}`;
            }
        } else if (!modelUrl.startsWith('http') && !modelUrl.startsWith('/')) {
            // 使用动态获取的 user_vrm 路径
            modelUrl = `${window.VRM_PATHS.user_vrm}/${modelUrl}`;
        }

        // 7. 初始化Three.js场景
        if (!window.vrmManager._isInitialized || !window.vrmManager.scene || !window.vrmManager.camera || !window.vrmManager.renderer) {
            await window.vrmManager.initThreeJS('vrm-canvas', 'vrm-container');
        }

        // 8. 加载VRM模型
        await window.vrmManager.loadModel(modelUrl);

        // ============================================================
        // 🔥【核心修复点】：直接使用刚刚拉取的 catgirlConfig 中的 lighting
        // ============================================================
        const lighting = catgirlConfig.lighting;
        
        if (lighting && window.vrmManager) {
            console.log('[VRM Check] 同步最新光照:', lighting);
            if (window.vrmManager.ambientLight) window.vrmManager.ambientLight.intensity = lighting.ambient;
            if (window.vrmManager.mainLight) window.vrmManager.mainLight.intensity = lighting.main;
            if (window.vrmManager.fillLight) window.vrmManager.fillLight.intensity = lighting.fill;
            if (window.vrmManager.rimLight) window.vrmManager.rimLight.intensity = lighting.rim;
            
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
        if (!window.location.pathname.includes('model_manager') && window.checkAndLoadVRM) {
            console.log('[VRM] 页面重新可见，触发数据同步...');
            window.checkAndLoadVRM();
        }
    }
});
// VRM 系统初始化完成