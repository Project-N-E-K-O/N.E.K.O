/**
 * VRM Init - 全局导出和自动初始化
 */

// VRM 模型路径常量（与服务器端路由保持一致）
const VRM_STATIC_PATH = '/static/vrm';  // 项目目录下的 VRM 模型路径
const VRM_USER_PATH = '/user_vrm';  // 用户文档目录下的 VRM 模型路径

// 检查是否在模型管理页面（通过路径或特定元素判断）
const isModelManagerPage = window.location.pathname.includes('model_manager') || document.querySelector('#vrm-model-select') !== null;
// 创建全局 VRM 管理器实例（延迟创建，确保所有模块都已加载）
window.vrmManager = null;


function initializeVRMManager() {
    if (window.vrmManager) return;

    try {
        // 检查核心类是否存在
        if (typeof window.VRMManager !== 'undefined') {
            window.vrmManager = new VRMManager();
            console.log('[VRM Init] VRMManager 实例已通过核心类创建');
        }
    } catch (error) {
        console.error('[VRM Init] 初始化失败:', error);
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
    // 1. 等待配置加载完成
    if (window.pageConfigReady && typeof window.pageConfigReady.then === 'function') {
        await window.pageConfigReady;
    }

    // 2. 获取并确定模型路径
    let targetModelPath = window.vrmModel || (typeof vrmModel !== 'undefined' ? vrmModel : '');

    // 如果未指定路径，使用默认模型保底
    if (!targetModelPath) {
        if (window.cubism4Model) {
            return; // Live2D 模式，跳过 VRM 默认加载
        }

        // 使用默认模型路径
        targetModelPath = '/static/vrm/sister1.0.vrm';
    }
    if (!window.vrmManager) {
        console.warn('[VRM Init] VRM管理器未初始化，跳过加载');
        return;
    }

    try {
        // UI 切换逻辑 - 智能视觉切换
        const vrmContainer = document.getElementById('vrm-container');
        if (vrmContainer) vrmContainer.style.display = 'block';

        // 只隐藏Live2D的浮动按钮，保持功能面板可用
        const live2dFloatingButtons = document.getElementById('live2d-floating-buttons');
        if (live2dFloatingButtons) {
            live2dFloatingButtons.style.display = 'none';
        }

        // 隐藏Live2D锁图标（如果存在）
        const live2dLockIcon = document.getElementById('live2d-lock-icon');
        if (live2dLockIcon) {
            live2dLockIcon.style.display = 'none';
        }

        // 初始化 Three.js 场景
        await window.vrmManager.initThreeJS('vrm-canvas', 'vrm-container');

        // 路径转换逻辑
        let modelUrl = targetModelPath;
        if (!modelUrl.startsWith('http') && !modelUrl.startsWith('/')) {
            modelUrl = `${VRM_USER_PATH}/${modelUrl}`;
        }
        modelUrl = modelUrl.replace(/\\/g, '/'); // 修正 Windows 风格路径

        // 执行加载
        await window.vrmManager.loadModel(modelUrl);
        console.log(`[VRM] 模型加载成功: ${targetModelPath}`);
        

    } catch (error) {
        console.error('[VRM Init] 自动加载流程异常:', error);
        console.error('[VRM Init] 错误详情:', error.stack);
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

        // 只隐藏Live2D的浮动按钮，保持功能面板可用
        const live2dFloatingButtons = document.getElementById('live2d-floating-buttons');
        if (live2dFloatingButtons) {
            live2dFloatingButtons.style.display = 'none';
        }

        // 隐藏Live2D锁图标（如果存在）
        const live2dLockIcon = document.getElementById('live2d-lock-icon');
        if (live2dLockIcon) {
            live2dLockIcon.style.display = 'none';
        }

        // 5. 检查VRM管理器
        if (!window.vrmManager) {
            console.error('[VRM] VRM管理器不存在');
            return;
        }

        // 6. 路径转换
        let modelUrl = newModelPath;

        // 处理Windows绝对路径，转换为Web路径
        if (modelUrl.includes('\\') || modelUrl.includes(':')) {
            const filename = modelUrl.split(/[\\/]/).pop();
            if (filename) {
                modelUrl = `/static/vrm/${filename}`;
                console.log('[主页VRM检查] 转换为Web路径:', modelUrl);
            }
        } else if (!modelUrl.startsWith('http') && !modelUrl.startsWith('/')) {
            const VRM_USER_PATH = '/user_vrm';
            modelUrl = `${VRM_USER_PATH}/${modelUrl}`;
        }
        modelUrl = modelUrl.replace(/\\/g, '/');

        // 7. 初始化Three.js场景
        if (!window.vrmManager._isInitialized || !window.vrmManager.scene || !window.vrmManager.camera || !window.vrmManager.renderer) {
            await window.vrmManager.initThreeJS('vrm-canvas', 'vrm-container');
        }

        // 8. 加载VRM模型
        await window.vrmManager.loadModel(modelUrl);
        console.log(`[VRM] 模型加载成功: ${newModelPath}`);
        
    } catch (error) {
        console.error('[主页VRM检查] VRM检查和加载失败:', error);
        console.error('[主页VRM检查] 错误详情:', error.stack);
    }
};


// 调试函数，方便排查交互失效问题
window.checkVRMStatus = function() {
    console.log('[VRM] === 状态检查 ===');
    console.log('管理器:', !!window.vrmManager);
    if (window.vrmManager) {
        console.log('当前模型:', !!window.vrmManager.currentModel);
        console.log('锁定状态:', window.vrmManager.isLocked);
        if (window.vrmManager.interaction) {
            console.log('交互模块:', window.vrmManager.interaction.mouseTrackingEnabled ? '已启用' : '已禁用');
        }
    }
    console.log('[VRM] === 检查完成 ===');
};

// VRM 系统初始化完成