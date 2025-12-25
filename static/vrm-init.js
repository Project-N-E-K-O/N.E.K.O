/**
 * VRM Init - 全局导出和自动初始化
 */

// VRM 模型路径常量（与服务器端路由保持一致）
const VRM_STATIC_PATH = '/static/vrm';  // 项目目录下的 VRM 模型路径
const VRM_USER_PATH = '/user_vrm';  // 用户文档目录下的 VRM 模型路径

// 创建全局 VRM 管理器实例（如果VRMManager类存在）
try {
    if (typeof VRMManager !== 'undefined') {
        window.vrmManager = new VRMManager();
    } else {
        window.vrmManager = null;
    }
} catch (error) {
    window.vrmManager = null;
}

// 自动初始化函数（延迟执行，等待 vrmModel 设置）
async function initVRMModel() {
    // 等待配置加载完成（如果存在）
    if (window.pageConfigReady && typeof window.pageConfigReady.then === 'function') {
        await window.pageConfigReady;
    }

    // 获取模型路径
    let targetModelPath = (typeof vrmModel !== 'undefined' ? vrmModel : (window.vrmModel || ''));

    // 如果没有设置模型路径，使用默认模型
    if (!targetModelPath) {
        targetModelPath = '/static/vrm/sister1.0.vrm';
        window.vrmModel = targetModelPath;
    }
    
    // 检查 vrmManager 是否已初始化
    if (!window.vrmManager) {
        return;
    }
    
    try {
        
        // 初始化 Three.js 场景
        await window.vrmManager.initThreeJS('vrm-canvas', 'vrm-container');
        
        // 显示VRM容器，隐藏Live2D容器
        const vrmContainer = document.getElementById('vrm-container');
        const live2dContainer = document.getElementById('live2d-container');
        if (vrmContainer) {
            vrmContainer.style.display = 'block';
        }
        if (live2dContainer) {
            live2dContainer.style.display = 'none';
        }
        
        // 确保模型URL正确（如果是相对路径，转换为绝对路径）
        let modelUrl = targetModelPath;
        
        if (!modelUrl.startsWith('http://') && !modelUrl.startsWith('https://') && !modelUrl.startsWith('/')) {
            if (modelUrl.endsWith('.vrm')) {
                const filename = modelUrl.split('/').pop();
                modelUrl = `${VRM_USER_PATH}/${filename}`;
            } else {
                modelUrl = `${VRM_USER_PATH}/${modelUrl}`;
            }
        }
        
        // 如果路径是本地文件路径（包含盘符或反斜杠），尝试转换为HTTP路径
        if (modelUrl.includes(':\\') || modelUrl.includes('\\')) {
            const filename = modelUrl.split(/[\\/]/).pop();
            modelUrl = `${VRM_STATIC_PATH}/${filename}`;
        }
        
        // 在加载前验证文件是否存在
        let fileExists = false;
        try {
            const response = await fetch(modelUrl, { method: 'HEAD' });
            if (response.ok) {
                fileExists = true;
            } else {
                if (modelUrl.startsWith(VRM_USER_PATH + '/')) {
                    const filename = modelUrl.replace(VRM_USER_PATH + '/', '');
                    const staticPath = `${VRM_STATIC_PATH}/${filename}`;
                    try {
                        const staticResponse = await fetch(staticPath, { method: 'HEAD' });
                        if (staticResponse.ok) {
                            modelUrl = staticPath;
                            fileExists = true;
                        }
                    } catch (e) {
                    }
                }
            }
        } catch (fetchError) {
        }
        
        // 加载模型
        await window.vrmManager.loadModel(modelUrl, {
            scale: { x: 1, y: 1, z: 1 }
        });
        
        // 确保wait03动画播放（备用方案，如果loadModel中的自动播放失败）
        setTimeout(async () => {
            if (window.vrmManager && window.vrmManager.currentModel && window.vrmManager.animation) {
                if (!window.vrmManager.animation.vrmaIsPlaying) {
                    try {
                        await window.vrmManager.animation.playVRMAAnimation('/static/vrm/animation/wait03.vrma', {
                            loop: true
                        });
                    } catch (error) {
                    }
                }
            }
        }, 2000);

    } catch (error) {
    }
}

// 自动初始化（如果存在 vrmModel 变量且 vrmManager 已初始化）
// 如果 pageConfigReady 存在，等待它完成；否则立即执行
if (window.pageConfigReady && typeof window.pageConfigReady.then === 'function') {
    window.pageConfigReady.then(() => {
        // 检查是否有VRM模型路径和vrmManager
        if (window.vrmManager) {
            const targetModelPath = (typeof vrmModel !== 'undefined' ? vrmModel : (window.vrmModel || ''));
            if (targetModelPath) {
                initVRMModel();
            }
        }
    }).catch(() => {
        // 即使配置加载失败，也尝试初始化（可能使用默认模型）
        if (window.vrmManager) {
            const targetModelPath = (typeof vrmModel !== 'undefined' ? vrmModel : (window.vrmModel || ''));
            if (targetModelPath) {
                initVRMModel();
            }
        }
    });
} else {
    if (window.vrmManager) {
        const targetModelPath = (typeof vrmModel !== 'undefined' ? vrmModel : (window.vrmModel || ''));
        console.log('[VRM Init] 当前vrmModel状态:', targetModelPath);
        if (targetModelPath) {
            initVRMModel();
        } else {
            console.log('[VRM Init] vrmModel未设置，使用默认VRM模型');
            // 使用项目中的默认VRM模型
            window.vrmModel = '/static/vrm/sister1.0.vrm';
            setTimeout(() => {
                if (window.vrmManager) {
                    initVRMModel();
                }
            }, 1000);
        }
    } else {
        console.log('[VRM Init] vrmManager不存在，VRM模块可能未加载');
    }
}

// 添加调试函数
window.checkVRMStatus = function() {
    console.log('[VRM Status Check] === VRM 状态检查 ===');
    console.log('window.vrmManager:', !!window.vrmManager);
    if (window.vrmManager) {
        console.log('currentModel:', !!window.vrmManager.currentModel);
        console.log('scene:', !!window.vrmManager.scene);
        console.log('camera:', !!window.vrmManager.camera);
        console.log('renderer:', !!window.vrmManager.renderer);
        console.log('isLocked:', window.vrmManager.isLocked);
        console.log('interaction:', !!window.vrmManager.interaction);
        console.log('canvas:', !!window.vrmManager.canvas);
        if (window.vrmManager.canvas) {
            const computedStyle = window.getComputedStyle(window.vrmManager.canvas);
            console.log('canvas.pointerEvents:', window.vrmManager.canvas.style.pointerEvents);
            console.log('canvas.computedPointerEvents:', computedStyle.pointerEvents);
            console.log('canvas.display:', window.vrmManager.canvas.style.display);
            console.log('canvas.visibility:', window.vrmManager.canvas.style.visibility);
            console.log('canvas.zIndex:', window.vrmManager.canvas.style.zIndex);
            console.log('canvas.position:', window.vrmManager.canvas.style.position);
            const rect = window.vrmManager.canvas.getBoundingClientRect();
            console.log('canvas.rect:', { left: rect.left, top: rect.top, width: rect.width, height: rect.height });
        }
        if (window.vrmManager.interaction) {
            console.log('interaction.isLocked:', window.vrmManager.interaction.isLocked);
        }
    }
    console.log('[VRM Status Check] === 检查完成 ===');
};

// 添加强制解锁函数
window.forceUnlockVRM = function() {
    if (window.vrmManager) {
        console.log('[VRM Force Unlock] 强制解锁VRM');
        window.vrmManager.isLocked = false;
        if (window.vrmManager.canvas) {
            window.vrmManager.canvas.style.setProperty('pointer-events', 'auto', 'important');
            console.log('[VRM Force Unlock] Canvas pointer-events设置为auto');
        }
        if (window.vrmManager.container) {
            window.vrmManager.container.style.setProperty('pointer-events', 'auto', 'important');
            console.log('[VRM Force Unlock] Container pointer-events设置为auto');
        }
        if (window.vrmManager.interaction) {
            window.vrmManager.interaction.setLocked(false);
        }
        console.log('[VRM Force Unlock] 解锁完成');
    } else {
        console.log('[VRM Force Unlock] vrmManager不存在');
    }
};

