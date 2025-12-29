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
    console.log('[VRM Init] 检测到模块就绪事件，开始初始化...');
    initializeVRMManager();

    // 如果不是管理页面，尝试自动加载模型
    if (!isModelManagerPage) {
        console.log('[VRM Init] 非管理页面，准备自动加载VRM模型...');
        console.log('[VRM Init] 当前window.vrmModel:', window.vrmModel);
        console.log('[VRM Init] 当前window.lanlan_config:', window.lanlan_config);
        initVRMModel();
    } else {
        console.log('[VRM Init] 管理页面，跳过自动加载');
    }
});

// 启动延迟初始化
// 自动初始化函数
async function initVRMModel() {
    console.log('[VRM Init] 开始自动初始化VRM模型...');

    // 1. 等待配置加载完成
    console.log('[VRM Init] 等待页面配置加载...');
    if (window.pageConfigReady && typeof window.pageConfigReady.then === 'function') {
        await window.pageConfigReady;
        console.log('[VRM Init] 页面配置加载完成');
    }

    // 2. 获取并确定模型路径
    let targetModelPath = window.vrmModel || (typeof vrmModel !== 'undefined' ? vrmModel : '');
    console.log('[VRM Init] 检测到的VRM模型路径:', targetModelPath);

    // 临时调试：强制使用默认VRM模型来测试加载功能
    if (!targetModelPath) {
        console.log('[VRM Init] 未找到VRM模型路径，使用默认模型进行测试');
        targetModelPath = '/static/vrm/avatarc.vrm'; // 默认模型
    }

    if (!window.vrmManager) {
        console.warn('[VRM Init] VRM管理器未初始化，跳过加载');
        return;
    }

    try {
        console.log(`[VRM Init] 开始加载 VRM 模型: ${targetModelPath}`);
        console.log('[VRM Init] 切换UI显示...');
        // 3. UI 切换逻辑 
        const vrmContainer = document.getElementById('vrm-container');
        const live2dContainer = document.getElementById('live2d-container');
        
        if (vrmContainer) vrmContainer.style.display = 'block';
        if (live2dContainer) live2dContainer.style.display = 'none';

        console.log('[VRM Init] 开始初始化Three.js场景...');
        // 4. 初始化 Three.js 场景 
        await window.vrmManager.initThreeJS('vrm-canvas', 'vrm-container');

        // 5. 路径转换逻辑（直接处理，不再进行 HEAD 请求检测以提升速度）
        let modelUrl = targetModelPath;
        if (!modelUrl.startsWith('http') && !modelUrl.startsWith('/')) {
            modelUrl = `${VRM_USER_PATH}/${modelUrl}`;
        }
        modelUrl = modelUrl.replace(/\\/g, '/'); // 修正 Windows 风格路径

        // 6. 执行加载
        console.log('[VRM Init] 开始加载VRM模型:', modelUrl);
        await window.vrmManager.loadModel(modelUrl);
        console.log('[VRM Init] VRM模型加载完成');
        

    } catch (error) {
        console.error('[VRM Init] 自动加载流程异常:', error);
        console.error('[VRM Init] 错误详情:', error.stack);
    }
}

// 添加强制解锁函数
window.forceUnlockVRM = function() {
    if (window.vrmManager && window.vrmManager.interaction) {
        console.log('[VRM Force Unlock] 执行逻辑解锁');
        // 统一调用我们重构后的接口
        window.vrmManager.interaction.setLocked(false);

        // 清理可能残留的 CSS 样式（如果之前误操作过）
        if (window.vrmManager.canvas) {
            window.vrmManager.canvas.style.pointerEvents = 'auto';
        }
    }
};

// 手动触发主页VRM模型检查的函数
window.checkAndLoadVRM = async function() {
    console.log('[主页VRM检查] 开始手动检查VRM模型...');

    try {
        // 1. 获取当前角色名称
        let currentLanlanName = window.lanlan_config?.lanlan_name;
        console.log('[主页VRM检查] 当前角色:', currentLanlanName);

        if (!currentLanlanName) {
            console.log('[主页VRM检查] 未找到当前角色，跳过检查');
            return;
        }

        // 2. 获取角色配置
        const charResponse = await fetch('/api/characters');
        if (!charResponse.ok) {
            console.error('[主页VRM检查] 获取角色配置失败');
            return;
        }

        const charactersData = await charResponse.json();
        const catgirlConfig = charactersData['猫娘']?.[currentLanlanName];

        if (!catgirlConfig) {
            console.log('[主页VRM检查] 未找到角色配置');
            return;
        }

        console.log('[主页VRM检查] 角色配置:', catgirlConfig);

        const modelType = catgirlConfig.model_type || 'live2d';
        console.log('[主页VRM检查] 模型类型:', modelType);

        if (modelType !== 'vrm') {
            console.log('[主页VRM检查] 模型类型不是VRM，跳过加载');
            return;
        }

        // 3. 获取VRM路径
        const newModelPath = catgirlConfig.vrm || '';
        console.log('[主页VRM检查] VRM路径:', newModelPath);

        if (!newModelPath) {
            console.log('[主页VRM检查] VRM路径为空，跳过加载');
            return;
        }

        // 4. 显示VRM容器
        const live2dContainer = document.getElementById('live2d-container');
        const vrmContainer = document.getElementById('vrm-container');
        if (live2dContainer) live2dContainer.style.display = 'none';
        if (vrmContainer) {
            vrmContainer.style.display = 'block';
            console.log('[主页VRM检查] VRM容器已显示');
        }

        // 5. 检查VRM管理器
        if (!window.vrmManager) {
            console.error('[主页VRM检查] VRM管理器不存在');
            return;
        }

        // 6. 路径转换
        let modelUrl = newModelPath;
        console.log('[主页VRM检查] 原始VRM路径:', modelUrl);

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
            console.log('[主页VRM检查] 初始化Three.js场景...');
            await window.vrmManager.initThreeJS('vrm-canvas', 'vrm-container');
        }

        // 8. 加载VRM模型
        console.log('[主页VRM检查] 开始加载VRM模型:', modelUrl);
        await window.vrmManager.loadModel(modelUrl);
        console.log('[主页VRM检查] VRM模型加载成功');
        
    } catch (error) {
        console.error('[主页VRM检查] VRM检查和加载失败:', error);
        console.error('[主页VRM检查] 错误详情:', error.stack);
    }
};


// 调试函数，方便排查交互失效问题
window.checkVRMStatus = function() {
    console.log('[VRM Status Check] === VRM 状态检查 ===');
    console.log('window.vrmManager:', !!window.vrmManager);
    if (window.vrmManager) {
        console.log('当前模型:', !!window.vrmManager.currentModel);
        console.log('锁定状态:', window.vrmManager.isLocked);
        if (window.vrmManager.interaction) {
            console.log('交互模块状态:', window.vrmManager.interaction.mouseTrackingEnabled ? '已启用' : '已禁用');
        }
    }
    console.log('[VRM Status Check] === 检查完成 ===');
};


// 自动显示帮助
console.log('[VRM Debug] 🎮 VRM 测试命令已加载:');
console.log('  testVRMModels.listModels() - 查看可用命令');